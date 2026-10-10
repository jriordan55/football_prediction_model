"""Current DraftKings and Pinnacle props from the public 4C board.

No API key. The phone site cannot call 4C itself (the board does not allow
browser reads), so this module runs where a normal HTTP client is allowed:
on your machine, and in the scheduled refresh that republishes the site.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
FOURC = "https://4codds.com/api/v2"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SPECS = {
    "nfl": {
        "board": "NFL",
        "referer": "https://4codds.com/football/nfl",
        "scoreboard": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "cache": ROOT / "data" / "espn_cache" / "nfl_fourc",
    },
    "cfb": {
        "board": "NCAAF",
        "referer": "https://4codds.com/football/ncaaf",
        "scoreboard": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        "cache": ROOT / "data" / "espn_cache" / "cfb" / "fourc",
    },
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
_LOCK = threading.Lock()
_NEXT = 0.0


def _session() -> requests.Session:
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _LOCAL.session = session
    return session


def _get(url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None, pause: float = 0.0) -> Any:
    global _NEXT
    last: Exception | None = None
    delay = 8.0
    for _attempt in range(6):
        try:
            if pause:
                with _LOCK:
                    wait = _NEXT - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    _NEXT = time.time() + pause
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


def _read(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


def _main_number(main: dict[str, Any], key: str) -> float | None:
    raw = main.get(key)
    if raw is None or raw == "":
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number


def game_lines(sport: str) -> dict[tuple[str, str], tuple[float | None, float | None]]:
    """Home spread and game total from the board. Spread is the home number."""
    spec = SPECS[sport]
    headers = {"Accept": "application/json", "User-Agent": UA, "Referer": spec["referer"]}
    board = _get(f"{FOURC}/board/football/{spec['board']}", headers=headers)
    found: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for game in board.get("games") or []:
        home = str((game.get("home") or {}).get("name") or "")
        away = str((game.get("away") or {}).get("name") or "")
        if not home or not away:
            continue
        main = game.get("main") if isinstance(game.get("main"), dict) else {}
        found[(home, away)] = (_main_number(main, "sp"), _main_number(main, "tot"))
    return found


def _quote(price: int | None) -> dict[str, int] | None:
    if price is None:
        return None
    return {"price": price}


def _scored_line(dk_line, dk_over, dk_under, pin_line, pin_over, pin_under, main: float) -> float:
    if dk_over is not None and dk_under is not None and dk_line is not None:
        return dk_line
    if pin_over is not None and pin_under is not None and pin_line is not None:
        return pin_line
    return main


def week_of(sport: str) -> tuple[int, int]:
    spec = SPECS[sport]
    data = _get(spec["scoreboard"], headers={"User-Agent": UA, "Accept": "application/json"})
    year = int((data.get("season") or {}).get("year") or 2026)
    week = int((data.get("week") or {}).get("number") or 1)
    return year, week


def fetch_lines(sport: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Pregame props with DraftKings and Pinnacle filled in when the book posts them."""
    spec = SPECS[sport]
    headers = {"Accept": "application/json", "User-Agent": UA, "Referer": spec["referer"]}
    year, week = week_of(sport)
    print(f"{sport} slate {year} week {week}", flush=True)
    board = _get(f"{FOURC}/board/football/{spec['board']}", headers=headers)
    games = _upcoming(board)
    print(f"{len(games)} games not started", flush=True)
    cache: Path = spec["cache"]
    unknown: Counter[str] = Counter()

    def props_for(game: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        path = cache / f"props_{game['id']}.json"
        payload = _read(path) if use_cache else None
        if not isinstance(payload, dict):
            payload = _get(f"{FOURC}/game/{game['id']}/props", headers=headers)
            _write(path, payload)
        return game, list(payload.get("props") or [])

    listed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(props_for, game) for game in games]
        for future in as_completed(futures):
            game, props = future.result()
            for prop in props:
                stat = str(prop.get("stat") or "").strip().upper()
                key = STAT_MAP.get(stat)
                if key is None:
                    if stat and "TEAM" not in stat and "POINTS" not in stat:
                        unknown[stat] += 1
                    continue
                if not str(prop.get("player") or "").strip():
                    continue
                listed.append((game, prop))
    print(f"{len(listed)} player props", flush=True)
    if unknown:
        print("unmapped", unknown.most_common(8), flush=True)

    markets: dict[str, dict[str, Any]] = {}
    for index, (_, prop) in enumerate(listed, start=1):
        prop_id = str(prop.get("id") or "")
        path = cache / f"market_{prop_id}.json"
        if use_cache:
            cached = _read(path)
            if isinstance(cached, dict):
                markets[prop_id] = cached
                continue
        try:
            payload = _get(f"{FOURC}/game/{prop_id}/market/tot", headers=headers, pause=1.35)
        except RuntimeError as exc:
            print(f"rate limit, keeping the {len(markets)} books already saved ({exc})", flush=True)
            break
        if payload.get("lines"):
            _write(path, payload)
        markets[prop_id] = payload
        if index % 25 == 0 or index == len(listed):
            print(f"books {index}/{len(listed)}", flush=True)

    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for game, prop in listed:
        home = str((game.get("home") or {}).get("name") or "")
        away = str((game.get("away") or {}).get("name") or "")
        main_game = game.get("main") if isinstance(game.get("main"), dict) else {}
        player = str(prop.get("player") or "").strip().title()
        try:
            main = float((prop.get("main") or {}).get("tot"))
        except (TypeError, ValueError):
            continue
        detail = markets.get(str(prop.get("id"))) or {}
        lines = detail.get("lines") if isinstance(detail.get("lines"), dict) else {}
        dk_line, dk_over, dk_under = _book_market(lines, "DRAFTKINGS", main)
        pin_line, pin_over, pin_under = _book_market(lines, "PINNACLE", main)
        if dk_over is None and dk_under is None:
            dk_line, dk_over, dk_under = _cell_book(prop, "DRAFTKINGS")
        if pin_over is None and pin_under is None:
            pin_line, pin_over, pin_under = _cell_book(prop, "PINNACLE")
        row = {
            "player": player,
            "prop_key": STAT_MAP[str(prop.get("stat") or "").strip().upper()],
            "line": _scored_line(dk_line, dk_over, dk_under, pin_line, pin_over, pin_under, main),
            "team": "",
            "home": home,
            "away": away,
            "home_abbr": str((game.get("home") or {}).get("short") or ""),
            "away_abbr": str((game.get("away") or {}).get("short") or ""),
            "event": f"{away} @ {home}",
            "startDate": str(game.get("start") or ""),
            "spread": _main_number(main_game, "sp"),
            "total": _main_number(main_game, "tot"),
            "dk_line": dk_line,
            "over": _quote(dk_over),
            "under": _quote(dk_under),
            "pin_line": pin_line,
            "pin_over": _quote(pin_over),
            "pin_under": _quote(pin_under),
            "espn_id": "",
        }
        key = (player.lower(), row["prop_key"], home, away)
        previous = best.get(key)
        if previous is None or _richer(row, previous):
            best[key] = row

    rows = list(best.values())
    matchups = []
    seen: set[str] = set()
    for game in games:
        gid = str(game.get("id") or "")
        if gid in seen:
            continue
        seen.add(gid)
        home = game.get("home") or {}
        away = game.get("away") or {}
        matchups.append({"id": gid, "home": home.get("name"), "away": away.get("name"), "start": game.get("start")})
    print(f"{sport} quotes {len(rows)} props, {len(matchups)} games", flush=True)
    return {
        "sport": sport,
        "season": year,
        "week": week,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "matchups": matchups,
    }


def _richer(row: dict[str, Any], previous: dict[str, Any]) -> bool:
    def score(item: dict[str, Any]) -> tuple[int, int]:
        pin = int(item.get("pin_over") is not None and item.get("pin_under") is not None)
        dk = int(item.get("over") is not None and item.get("under") is not None)
        return pin, dk

    return score(row) > score(previous)
