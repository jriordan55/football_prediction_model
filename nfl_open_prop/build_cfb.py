"""Build the college slate the prop desk reads.

Pregame player props come from the public 4C Odds board. Hit rates come from
each player's ESPN college football game log. DraftKings and Pinnacle are
taken off the main total when that book has a price there.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "streamlit_app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from lib.nfl_l10_stats import _parse_stat  # noqa: E402
from lib.team_registry import normalize_team_key, resolve_canonical  # noqa: E402
from nfl_open_prop.desk import CFB_CACHE, CFB_SNAPSHOT, STAT_BY_ID  # noqa: E402

FOURC = "https://4codds.com/api/v2"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://4codds.com/football/ncaaf",
}
ESPN_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json",
}
STAT_MAP = {
    "TOTAL PASSING YARDS": "pass_yds",
    "PASSING YARDS": "pass_yds",
    "TOTAL PASSING TOUCHDOWNS": "pass_tds",
    "TOTAL TOUCHDOWN PASSES": "pass_tds",
    "PASSING TOUCHDOWNS": "pass_tds",
    "TOTAL PASS ATTEMPTS": "pass_attempts",
    "PASS ATTEMPTS": "pass_attempts",
    "TOTAL COMPLETIONS": "pass_completions",
    "TOTAL PASS COMPLETIONS": "pass_completions",
    "COMPLETIONS": "pass_completions",
    "TOTAL RUSHING YARDS": "rush_yds",
    "RUSHING YARDS": "rush_yds",
    "TOTAL RUSHING ATTEMPTS": "rush_attempts",
    "RUSH ATTEMPTS": "rush_attempts",
    "RUSHING ATTEMPTS": "rush_attempts",
    "TOTAL RECEIVING YARDS": "rec_yds",
    "RECEIVING YARDS": "rec_yds",
    "TOTAL RECEPTIONS": "receptions",
    "RECEPTIONS": "receptions",
    "TOTAL RUSHING + RECEIVING YARDS": "rr_yds",
    "RUSHING + RECEIVING YARDS": "rr_yds",
    "RUSH + REC YARDS": "rr_yds",
    "TOTAL PASSING + RUSHING YARDS": "pass_rush_yds",
    "PASSING + RUSHING YARDS": "pass_rush_yds",
    "PASS + RUSH YARDS": "pass_rush_yds",
    "ANYTIME TOUCHDOWN": "tds",
    "ANYTIME TOUCHDOWN SCORER": "tds",
    "TO SCORE A TOUCHDOWN": "tds",
    "TOTAL TOUCHDOWNS": "tds",
}
_LOCAL = threading.local()


def _session() -> requests.Session:
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _LOCAL.session = session
    return session


_FOURC_LOCK = threading.Lock()
_FOURC_NEXT = 0.0


def _get(url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None, pause: float = 0.0) -> Any:
    global _FOURC_NEXT
    last: Exception | None = None
    delay = 8.0
    for attempt in range(6):
        try:
            if pause:
                with _FOURC_LOCK:
                    wait = _FOURC_NEXT - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    _FOURC_NEXT = time.time() + pause
            response = _session().get(url, headers=headers, params=params, timeout=25)
            if response.status_code == 429:
                time.sleep(delay)
                delay = min(delay * 1.7, 75)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            time.sleep(min(delay, 20))
    raise RuntimeError(f"{url} failed: {last}")


def _cache_json(path: Path, loader) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    payload = loader()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _norm_name(value: str) -> str:
    text = value.lower().replace(".", " ").replace("'", "")
    text = re.sub(r"[^a-z\s]", " ", text)
    return " ".join(text.split())


def _school_key(name: str) -> str:
    canonical = resolve_canonical(name) or name
    return normalize_team_key(canonical)


def _same_school(label: str, school: str) -> bool:
    want = _school_key(school)
    if not want:
        return False
    for part in re.split(r"[·|/,]| - ", label):
        got = _school_key(part)
        if got and got == want:
            return True
    return False


def _cfb_week() -> tuple[int, int]:
    data = _get(
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        headers=ESPN_HEADERS,
    )
    year = int((data.get("season") or {}).get("year") or 2026)
    week = int((data.get("week") or {}).get("number") or 7)
    return year, week


def _book_price(entries: list[Any] | None, book: str) -> int | None:
    for entry in entries or []:
        if not entry or str(entry[0]).upper() != book:
            continue
        try:
            return int(float(entry[1]))
        except (TypeError, ValueError, IndexError):
            return None
    return None


def _cell_book(prop: dict[str, Any], book: str) -> tuple[float | None, int | None, int | None]:
    """Price already sitting on the prop card, when that book is the one shown."""
    over = under = None
    line = None
    for cell in prop.get("cells") or []:
        if not isinstance(cell, list) or len(cell) < 5 or not isinstance(cell[4], list):
            continue
        if str(cell[4][0]).upper() != book:
            continue
        try:
            price = int(float(cell[4][1]))
            got = float(cell[2])
        except (TypeError, ValueError, IndexError):
            continue
        side = str(cell[1])
        if side == "over":
            over = price
            line = got
        elif side == "under":
            under = price
            line = got if line is None else line
    return line, over, under


def _book_market(lines: dict[str, Any], book: str, main: float | None) -> tuple[float | None, int | None, int | None]:
    found: list[tuple[bool, float, float, int | None, int | None]] = []
    for key, side in lines.items():
        if not isinstance(side, dict):
            continue
        try:
            line = float(key)
        except (TypeError, ValueError):
            continue
        over = _book_price(side.get("over"), book)
        under = _book_price(side.get("under"), book)
        if over is None and under is None:
            continue
        distance = abs(line - main) if main is not None else 0.0
        found.append((over is not None and under is not None, -distance, line, over, under))
    if not found:
        return None, None, None
    found.sort(reverse=True)
    _, _, line, over, under = found[0]
    return line, over, under


def _quote(price: int | None) -> dict[str, int] | None:
    if price is None:
        return None
    return {"price": price}


def _upcoming(board: dict[str, Any]) -> list[dict[str, Any]]:
    games = []
    for game in board.get("games") or []:
        if game.get("live"):
            continue
        status = str((game.get("state") or {}).get("status") or "NONE").upper()
        if status in {"FINAL", "POST", "LIVE", "HALFTIME"}:
            continue
        home = game.get("home") or {}
        away = game.get("away") or {}
        if not home.get("name") or not away.get("name"):
            continue
        games.append(game)
    return games


def _parse_games(data: dict[str, Any], year: int) -> list[dict[str, Any]]:
    names = [str(name) for name in (data.get("names") or [])]
    events_meta = data.get("events") or {}
    games: list[dict[str, Any]] = []
    seen: set[str] = set()
    for season_type in data.get("seasonTypes") or []:
        label = str(season_type.get("displayName") or season_type.get("name") or "")
        if "preseason" in label.lower():
            continue
        categories = [cat for cat in (season_type.get("categories") or []) if cat.get("events")]
        if not categories:
            continue
        category = max(categories, key=lambda cat: len(cat.get("events") or []))
        for event in category.get("events") or []:
            stats = event.get("stats") or []
            if names and len(stats) != len(names):
                continue
            event_id = str(event.get("eventId") or "")
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            meta = events_meta.get(event_id) or {}
            opponent = meta.get("opponent") or {}
            parsed: dict[str, float] = {}
            for name, raw in zip(names, stats):
                value = _parse_stat(name, raw)
                if value is not None:
                    parsed[name] = value
            week = meta.get("week")
            try:
                week_n = int(week) if week is not None else None
            except (TypeError, ValueError):
                week_n = None
            games.append(
                {
                    "event_id": event_id,
                    "season": year,
                    "week": week_n,
                    "game_date": str(meta.get("gameDate") or ""),
                    "opponent": str(opponent.get("displayName") or ""),
                    "opponent_abbr": str(opponent.get("abbreviation") or ""),
                    "stats": parsed,
                }
            )
    games.sort(key=lambda game: (str(game.get("game_date") or ""), int(game.get("week") or 0)))
    return games


def _search_athlete(name: str, home: str, away: str, league: str = "college-football") -> tuple[str, str, str] | None:
    data = _get(
        "https://site.web.api.espn.com/apis/common/v3/search",
        headers=ESPN_HEADERS,
        params={
            "query": name,
            "limit": 8,
            "type": "player",
            "sport": "football",
            "league": league,
        },
    )
    want = _norm_name(name)
    matches = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        display = str(item.get("displayName") or "")
        if _norm_name(display) != want:
            continue
        label = str(item.get("label") or item.get("description") or "")
        matches.append((str(item.get("id") or ""), display, label))
    for espn_id, display, label in matches:
        if _same_school(label, home):
            return espn_id, display, home
        if _same_school(label, away):
            return espn_id, display, away
    if len(matches) == 1 and matches[0][0]:
        return matches[0][0], matches[0][1], ""
    return None


def _fetch_log(espn_id: str, year: int, cache: Path | None = None, league: str = "college-football") -> list[dict[str, Any]]:
    path = (cache or CFB_CACHE) / f"gamelog_{espn_id}_{year}.json"
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    url = (
        "https://site.web.api.espn.com/apis/common/v3/sports/football/"
        f"{league}/athletes/{espn_id}/gamelog"
    )
    try:
        data = _get(url, headers=ESPN_HEADERS, params={"season": year})
    except RuntimeError:
        return []
    games = _parse_games(data, year) if isinstance(data, dict) else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games), encoding="utf-8")
    return games


def build(sport: str = "cfb") -> dict[str, Any]:
    from nfl_open_prop.desk import CACHE, SNAPSHOT
    from nfl_open_prop.fourc_lines import SPECS, fetch_lines

    fresh = fetch_lines(sport, use_cache=True)
    year, week = int(fresh["season"]), int(fresh["week"])
    rows = list(fresh["rows"])
    matchups = list(fresh["matchups"])
    if sport == "nfl":
        log_cache = CACHE
        snapshot = SNAPSHOT
        league = "nfl"
        prefix = "nfl"
    else:
        log_cache = CFB_CACHE
        snapshot = CFB_SNAPSHOT
        league = "college-football"
        prefix = "cfb"
    cache = SPECS[sport]["cache"]
    identities: dict[tuple[str, str, str], str] = {}
    for row in rows:
        identities[(_norm_name(row["player"]), row["home"], row["away"])] = row["player"]

    resolved: dict[tuple[str, str, str], tuple[str, str, str]] = {}

    def resolve(key: tuple[str, str, str]) -> tuple[tuple[str, str, str], tuple[str, str, str] | None]:
        norm, home, away = key
        player = identities[key]
        path = cache / f"espn_{norm.replace(' ', '_')}_{_school_key(home)}_{_school_key(away)}.json"
        found = _cache_json(path, lambda: {"hit": _search_athlete(player, home, away, league)})
        hit = found.get("hit") if isinstance(found, dict) else None
        if not hit or not hit[0]:
            return key, None
        return key, (str(hit[0]), str(hit[1]), str(hit[2]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(resolve, key) for key in identities]
        done = 0
        for future in as_completed(futures):
            key, hit = future.result()
            if hit:
                resolved[key] = hit
            done += 1
            if done % 40 == 0:
                print(f"players {done}/{len(identities)}", flush=True)
    print(f"matched {len(resolved)} of {len(identities)} players", flush=True)

    for row in rows:
        hit = resolved.get((_norm_name(row["player"]), row["home"], row["away"]))
        if not hit:
            slug = re.sub(r"[^a-z0-9]+", "-", _norm_name(row["player"])).strip("-")
            row["espn_id"] = f"{prefix}-{slug}-{_school_key(row['home'])}"
            continue
        espn_id, display, team = hit
        row["espn_id"] = espn_id
        row["player"] = display or row["player"]
        row["team"] = team

    needed = sorted({row["espn_id"] for row in rows if row["espn_id"].isdigit()})
    print(f"logs for {len(needed)} players", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for espn_id in needed:
            for season in (year - 1, year):
                futures.append(pool.submit(_fetch_log, espn_id, season, log_cache, league))
        done = 0
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 80 == 0:
                print(f"logs {done}/{len(futures)}", flush=True)

    payload = {
        "version": 1,
        "sport": sport,
        "year": year,
        "week": week,
        "source": "4codds",
        "updatedAt": fresh["updatedAt"],
        "rowCount": len(rows),
        "gameCount": len(matchups),
        "props": rows,
        "matchups": matchups,
    }
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {snapshot} ({len(rows)} props, {len(matchups)} games)", flush=True)
    return payload


if __name__ == "__main__":
    build()
