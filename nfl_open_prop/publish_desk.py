"""Put the full-game sim, the extra books, and last week's games on the prop desk.

The phone page only reads desk.json. This module fills that file. The page
itself never runs the sim.
"""
from __future__ import annotations

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

from nfl_open_prop.desk import CFB_CACHE, DATA, STAT_BY_ID, STATS, CACHE as NFL_CACHE
from nfl_open_prop.export_site import WEB
from nfl_open_prop.refresh_site import DESK, INDEX, bump_index

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "streamlit_app"
LINES = DATA / "fastr_player_cache" / "nfl"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _norm(value: str) -> str:
    text = value.lower().replace(".", " ").replace("'", "").replace("’", "")
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    return " ".join(text.split())


def _price(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace("+", "").replace("−", "-")))
    except (TypeError, ValueError):
        return None


def _blank_quote() -> dict:
    return {
        "line": None,
        "dkLine": None,
        "dkOver": None,
        "dkUnder": None,
        "pinLine": None,
        "pinOver": None,
        "pinUnder": None,
        "ppLine": None,
        "ppOver": None,
        "udLine": None,
        "udOver": None,
    }


def _slate_week(year: int) -> int:
    import sys

    app = str(APP)
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.nfl_props_week import slate_week_from_schedule

    return int(slate_week_from_schedule(year) or 6)


def merge_book_lines(sport: dict, rows: list[dict]) -> int:
    """Add DraftKings, PrizePicks, and Underdog lines. Existing Pinnacle prices stay."""
    players = sport.get("players") or []
    by_name = {_norm(str(player.get("name") or "")): player for player in players}
    added = 0
    for row in rows:
        stat = str(row.get("propKey") or "")
        if stat not in STAT_BY_ID:
            continue
        name = str(row.get("player") or "").strip()
        key = _norm(name)
        if not key:
            continue
        player = by_name.get(key)
        if player is None:
            slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
            home = str(row.get("home") or "")
            away = str(row.get("away") or "")
            player = {
                "id": f"nfl-{slug}",
                "name": name,
                "team": "",
                "home": home,
                "away": away,
                "opp": "",
                "start": "",
                "side": "",
                "spread": None,
                "total": None,
                "defense": {},
                "availability": 1,
                "injury": {},
                "absent": {},
                "takeover": {},
                "props": {},
                "logs": {},
                "sim": {},
            }
            players.append(player)
            by_name[key] = player
        quote = player["props"].get(stat) or _blank_quote()
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            line = 0.5 if STAT_BY_ID[stat]["kind"] == "binary" else None
        if line is None:
            continue
        book = str(row.get("book_id") or "")
        over = _price(row.get("over_price"))
        under = _price(row.get("under_price"))
        if book == "draftkings":
            quote["dkLine"] = line
            quote["dkOver"] = over
            quote["dkUnder"] = under
            quote["line"] = line
        elif book == "prizepicks":
            quote["ppLine"] = line
            quote["ppOver"] = over
            if quote.get("line") is None:
                quote["line"] = line
        elif book == "underdog":
            quote["udLine"] = line
            quote["udOver"] = over
            if quote.get("line") is None:
                quote["line"] = line
        else:
            continue
        player["props"][stat] = quote
        if not player.get("home"):
            player["home"] = str(row.get("home") or "")
            player["away"] = str(row.get("away") or "")
        added += 1
    sport["players"] = [player for player in players if player.get("props")]
    sport["players"].sort(key=lambda player: player.get("name") or "")
    return added


def _fetch_games(espn_id: str, year: int, cache: Path, league: str, need_week: int) -> list[dict]:
    path = cache / f"gamelog_{espn_id}_{year}.json"
    games: list[dict] = []
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                games = cached
        except (OSError, json.JSONDecodeError):
            games = []
    have = [int(game.get("week") or 0) for game in games if isinstance(game, dict)]
    if have and max(have) >= need_week:
        return games
    url = (
        "https://site.web.api.espn.com/apis/common/v3/sports/football/"
        f"{league}/athletes/{espn_id}/gamelog"
    )
    try:
        response = requests.get(url, headers=_HEADERS, params={"season": year}, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return games
    from nfl_open_prop.build_cfb import _parse_games

    parsed = _parse_games(payload, year) if isinstance(payload, dict) else []
    if parsed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parsed), encoding="utf-8")
        return parsed
    return games


def _played(game: dict, season: int, slate_week: int) -> bool:
    """A game counts once it has been played, including one already over in the current week."""
    try:
        game_season = int(game.get("season") or 0)
        game_week = int(game.get("week") or 0)
    except (TypeError, ValueError):
        return False
    if game_season < season or (game_season == season and game_week < slate_week):
        return True
    if game_season != season or game_week != slate_week:
        return False
    raw = str(game.get("game_date") or "")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when < datetime.now(timezone.utc)


def _log_rows(player: dict, games: list[dict], sport_id: str, season: int, week_before: int, roles: dict, snaps: dict) -> int:
    from lib.nfl_l10_stats import game_stat_value
    from nfl_open_prop.played import offense_snaps, team_abbr

    added = 0
    bucket = player.setdefault("logs", {})
    snap_weeks = {(key[0], key[1]) for key in snaps}
    for game in games:
        try:
            game_season = int(game.get("season") or 0)
            game_week = int(game.get("week") or 0)
        except (TypeError, ValueError):
            continue
        if not _played(game, season, week_before):
            continue
        event_id = str(game.get("event_id") or "")
        role = (roles.get(event_id) or {}).get(str(player["id"]))
        if sport_id == "nfl":
            week_has_snaps = (game_season, game_week) in snap_weeks
            count = offense_snaps(
                snaps,
                str(player.get("name") or ""),
                game_season,
                game_week,
                team_abbr(str(player.get("team") or player.get("home") or "")),
                str(game.get("opponent_abbr") or ""),
            )
            if week_has_snaps:
                if count is None or count < 10:
                    continue
            elif not role or role[1] or not role[0]:
                continue
        elif not role or role[1] or not role[0]:
            continue
        opp = str(game.get("opponent_abbr") or game.get("opponent") or "")
        for stat in STAT_BY_ID:
            value = game_stat_value(game, stat)
            if value is None:
                continue
            rows = bucket.setdefault(stat, [])
            if any(int(row[0]) == game_season and int(row[1]) == game_week for row in rows):
                continue
            rows.append([game_season, game_week, opp, round(float(value), 2)])
            added += 1
    for rows in bucket.values():
        rows.sort(key=lambda row: (row[0], row[1]))
    return added


def fill_empty_logs(sport: dict, sport_id: str) -> int:
    """Find ESPN ids for new names and load the games that count."""
    import sys

    app = str(APP)
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.nfl_l10_stats import resolve_espn_id
    from nfl_open_prop.build_cfb import _search_athlete
    from nfl_open_prop.played import fetch_role, load_snaps

    season = int(sport.get("season") or 2026)
    week = int(sport.get("week") or 1)
    cache = NFL_CACHE if sport_id == "nfl" else CFB_CACHE
    league = "nfl" if sport_id == "nfl" else "college-football"
    missing = [player for player in sport.get("players") or [] if not player.get("logs")]
    if not missing:
        return 0

    def identify(player: dict) -> None:
        if str(player.get("id") or "").isdigit():
            return
        if sport_id == "nfl":
            found = resolve_espn_id(
                str(player.get("name") or ""),
                str(player.get("team") or "") or None,
                None,
                str(player.get("home") or ""),
                str(player.get("away") or ""),
            )
        else:
            hit = _search_athlete(
                str(player.get("name") or ""),
                str(player.get("home") or ""),
                str(player.get("away") or ""),
            )
            found = hit[0] if hit else None
        if found:
            player["id"] = str(found)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(identify, missing))
    snaps = load_snaps(tuple(range(season - 2, season + 1))) if sport_id == "nfl" else {}
    histories: dict[str, list[dict]] = {}
    wanted = [player for player in missing if str(player.get("id") or "").isdigit()]

    def history(player: dict) -> tuple[str, list[dict]]:
        games = []
        for year in range(season - 2, season + 1):
            games.extend(_fetch_games(str(player["id"]), year, cache, league, 18 if year < season else week - 1))
        return str(player["id"]), games

    with ThreadPoolExecutor(max_workers=8) as pool:
        for espn_id, games in pool.map(history, wanted):
            histories[espn_id] = games
    event_ids: list[str] = []
    seen: set[str] = set()
    for games in histories.values():
        for game in games:
            event_id = str(game.get("event_id") or "")
            if event_id and event_id not in seen:
                seen.add(event_id)
                event_ids.append(event_id)
    roles: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = pool.map(lambda event_id: fetch_role(event_id, league) or {}, event_ids)
        for event_id, role in zip(event_ids, fetched):
            roles[event_id] = role
    added = 0
    for player in wanted:
        added += _log_rows(player, histories.get(str(player["id"])) or [], sport_id, season, week, roles, snaps)
    return added


def extend_short_logs(sport: dict, sport_id: str) -> int:
    """Fill a short log with 2024 and 2025, the two seasons before this one."""
    import sys

    app = str(APP)
    if app not in sys.path:
        sys.path.insert(0, app)
    from nfl_open_prop.played import fetch_role, load_snaps

    season = int(sport.get("season") or 2026)
    week = int(sport.get("week") or 1)
    cache = NFL_CACHE if sport_id == "nfl" else CFB_CACHE
    league = "nfl" if sport_id == "nfl" else "college-football"
    prior = (season - 2, season - 1)
    snaps = load_snaps(prior + (season,)) if sport_id == "nfl" else {}
    wanted = []
    for player in sport.get("players") or []:
        if not str(player.get("id") or "").isdigit():
            continue
        logs = player.get("logs") or {}
        longest = max((len(rows) for rows in logs.values()), default=0)
        seasons = {int(row[0]) for rows in logs.values() for row in rows}
        if longest >= 10 and prior[0] in seasons:
            continue
        if any(year not in seasons for year in prior) or longest < 10:
            wanted.append(player)
    if not wanted:
        return 0
    print(f"{sport_id} filling {len(wanted)} short logs from {prior[0]} and {prior[1]}", flush=True)

    def history(player: dict) -> tuple[str, list[dict]]:
        games = []
        for year in prior:
            games.extend(_fetch_games(str(player["id"]), year, cache, league, 18))
        return str(player["id"]), games

    histories: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for espn_id, games in pool.map(history, wanted):
            histories[espn_id] = games
    event_ids: list[str] = []
    seen: set[str] = set()
    for games in histories.values():
        for game in games:
            event_id = str(game.get("event_id") or "")
            if event_id and event_id not in seen:
                seen.add(event_id)
                event_ids.append(event_id)
    roles: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = pool.map(lambda event_id: fetch_role(event_id, league) or {}, event_ids)
        for event_id, role in zip(event_ids, fetched):
            roles[event_id] = role
    added = 0
    for player in wanted:
        added += _log_rows(player, histories.get(str(player["id"])) or [], sport_id, season, week, roles, snaps)
    return added


def refresh_completed_week(sport: dict, sport_id: str) -> int:
    """Add the last completed week when a player's log stops short of it."""
    from lib.nfl_l10_stats import game_stat_value
    from nfl_open_prop.played import fetch_role, load_snaps, offense_snaps, team_abbr

    season = int(sport.get("season") or 2026)
    week = int(sport.get("week") or 1)
    need = week - 1
    if need < 1:
        return 0
    cache = NFL_CACHE if sport_id == "nfl" else CFB_CACHE
    league = "nfl" if sport_id == "nfl" else "college-football"
    snaps = load_snaps((season,)) if sport_id == "nfl" else {}
    through = week - 1
    for player in sport.get("players") or []:
        if _played({"season": season, "week": week, "game_date": player.get("start")}, season, week):
            through = week
            break
    players = [player for player in sport.get("players") or [] if str(player.get("id") or "").isdigit()]
    logs: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_fetch_games, str(player["id"]), season, cache, league, through): player
            for player in players
        }
        for future in as_completed(futures):
            player = futures[future]
            try:
                logs[str(player["id"])] = future.result()
            except Exception:
                logs[str(player["id"])] = []
    roles: dict[str, dict] = {}
    added = 0
    snap_weeks = {(key[0], key[1]) for key in snaps}
    for player in players:
        games = [game for game in logs.get(str(player["id"])) or [] if _played(game, season, week)]
        for game in games:
            try:
                game_season = int(game.get("season") or 0)
                game_week = int(game.get("week") or 0)
            except (TypeError, ValueError):
                continue
            event_id = str(game.get("event_id") or "")
            if event_id and event_id not in roles:
                roles[event_id] = fetch_role(event_id, league) or {}
            role = (roles.get(event_id) or {}).get(str(player["id"]))
            if sport_id == "nfl":
                week_has_snaps = (game_season, game_week) in snap_weeks
                count = offense_snaps(
                    snaps,
                    str(player.get("name") or ""),
                    game_season,
                    game_week,
                    team_abbr(str(player.get("team") or "")),
                    str(game.get("opponent_abbr") or ""),
                )
                if week_has_snaps:
                    if count is None or count < 10:
                        continue
                elif not role or role[1] or not role[0]:
                    continue
            elif not role or role[1] or not role[0]:
                continue
            opp = str(game.get("opponent_abbr") or game.get("opponent") or "")
            bucket = player.setdefault("logs", {})
            for stat in STAT_BY_ID:
                value = game_stat_value(game, stat)
                if value is None:
                    continue
                rows = bucket.setdefault(stat, [])
                if any(int(row[0]) == game_season and int(row[1]) == game_week for row in rows):
                    continue
                rows.append([game_season, game_week, opp, round(float(value), 2)])
                rows.sort(key=lambda row: (row[0], row[1]))
                added += 1
    return added


def _pack_draws(draws: np.ndarray, play: float) -> dict:
    arr = np.asarray(draws, dtype=float)
    return {
        "mean": round(float(np.mean(arr)), 2),
        "median": round(float(np.median(arr)), 2),
        "p10": round(float(np.quantile(arr, 0.1)), 2),
        "p90": round(float(np.quantile(arr, 0.9)), 2),
        "sd": round(float(np.std(arr)), 2),
        "play": round(float(play), 3),
    }


def attach_nfl_sim(sport: dict) -> int:
    import sys

    app = str(APP)
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.nfl_game_sim import norm_name, simulate_week

    year = int(sport.get("season") or 2026)
    week = int(sport.get("week") or 1)
    bundle = simulate_week(year, week)
    packed: dict[str, dict[str, dict]] = {}
    rows = bundle["meta"]["rows"]
    draws = bundle["draws"]
    for index, row in enumerate(rows):
        stat = row["stat"]
        if stat not in STAT_BY_ID:
            continue
        center = row.get("center")
        if center is not None:
            packed.setdefault(row["name"], {})[stat] = {
                "mean": round(float(center), 2),
                "median": round(float(center), 2),
                "p10": round(float(row.get("p10") or 0), 2),
                "p90": round(float(row.get("p90") or 0), 2),
                "sd": round(float(row.get("sd") or 0), 2),
                "play": round(float(row.get("play") or 0), 3),
            }
        else:
            packed.setdefault(row["name"], {})[stat] = _pack_draws(draws[index], row.get("play") or 0)
    matched = 0
    for player in sport.get("players") or []:
        sim = packed.get(norm_name(str(player.get("name") or "")))
        if not sim:
            player["sim"] = {}
            continue
        player["sim"] = sim
        matched += 1
    return matched


def _recent(rows: list, season: int) -> list[float]:
    this = [float(row[3]) for row in rows if int(row[0]) == season]
    use = this if len(this) >= 2 else [float(row[3]) for row in rows[-8:]]
    return use


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _cfb_center(values: list[float], stat: str) -> tuple[float, float] | None:
    from lib.nfl_game_sim import _spread

    if not values:
        return None
    sample = np.asarray(values[-10:], dtype=float)
    center = float(np.median(sample))
    return center, _spread(stat, center, sample)


def attach_cfb_sim(sport: dict) -> int:
    """Median of a play-by-play simulation of each college game."""
    import sys

    app = str(APP)
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.cfb_game_sim import norm_name, simulate_week

    year = int(sport.get("season") or 2026)
    week = int(sport.get("week") or 1)
    bundle = simulate_week(year, week)
    packed: dict[str, dict[str, dict]] = {}
    rows = bundle["meta"]["rows"]
    draws = bundle["draws"]
    for index, row in enumerate(rows):
        stat = row["stat"]
        if stat not in STAT_BY_ID:
            continue
        center = row.get("center")
        if center is not None:
            packed.setdefault(row["name"], {})[stat] = {
                "mean": round(float(center), 2),
                "median": round(float(center), 2),
                "p10": round(float(row.get("p10") or 0), 2),
                "p90": round(float(row.get("p90") or 0), 2),
                "sd": round(float(row.get("sd") or 0), 2),
                "play": round(float(row.get("play") or 0), 3),
            }
        else:
            packed.setdefault(row["name"], {})[stat] = _pack_draws(draws[index], row.get("play") or 0)
    matched = 0
    for player in sport.get("players") or []:
        sim = packed.get(norm_name(str(player.get("name") or "")))
        if not sim:
            player["sim"] = {}
            continue
        availability = 1.0 if player.get("availability") is None else float(player.get("availability") or 0)
        if availability <= 0.25:
            sim = {
                stat: {**row, "mean": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0, "sd": 0.0, "play": 0.0}
                for stat, row in sim.items()
            }
        player["sim"] = sim
        matched += 1
    return matched


def _stats_payload() -> list[dict]:
    return [
        {"id": spec["id"], "label": spec["label"], "unit": spec["unit"], "default": spec["default"], "kind": spec["kind"]}
        for spec in STATS
    ]


def publish() -> None:
    desk = json.loads(DESK.read_text(encoding="utf-8"))
    nfl = desk["sports"]["nfl"]
    cfb = desk["sports"]["cfb"]
    year = int(nfl.get("season") or 2026)
    week = _slate_week(year)
    nfl["season"] = year
    nfl["week"] = week
    lines_path = LINES / f"sim_lines_{year}_w{week}.json"
    if lines_path.exists():
        rows = json.loads(lines_path.read_text(encoding="utf-8"))
        count = merge_book_lines(nfl, rows)
        print(f"nfl lines {count}", flush=True)
    print("refreshing completed games…", flush=True)
    print(f"nfl backfill {fill_empty_logs(nfl, 'nfl')}", flush=True)
    print(f"cfb backfill {fill_empty_logs(cfb, 'cfb')}", flush=True)
    print(f"nfl prior seasons {extend_short_logs(nfl, 'nfl')}", flush=True)
    print(f"cfb prior seasons {extend_short_logs(cfb, 'cfb')}", flush=True)
    print(f"nfl log rows added {refresh_completed_week(nfl, 'nfl')}", flush=True)
    print(f"cfb log rows added {refresh_completed_week(cfb, 'cfb')}", flush=True)
    print("attaching the sim…", flush=True)
    print(f"nfl sim players {attach_nfl_sim(nfl)}", flush=True)
    print(f"cfb sim players {attach_cfb_sim(cfb)}", flush=True)
    from nfl_open_prop.close_signal import attach_signals

    print(f"nfl close signals {attach_signals(nfl)}", flush=True)
    from nfl_open_prop.prizepicks_lines import attach_prizepicks

    print(f"nfl prizepicks lines {attach_prizepicks(nfl)}", flush=True)
    nfl["stats"] = _stats_payload()
    cfb["stats"] = _stats_payload()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    nfl["updatedAt"] = datetime.now(timezone.utc).isoformat()
    desk["generatedAt"] = nfl["updatedAt"]
    DESK.write_text(json.dumps(desk, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    shutil.copyfile(WEB, DESK.parent / "app.js")
    bump_index(stamp)
    print(f"wrote {DESK}", flush=True)


if __name__ == "__main__":
    publish()
