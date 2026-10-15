"""Last-10 NFL prop stats from the player's own ESPN game log.

Each prop uses the stat that market actually grades. Anytime touchdowns use the
count of touchdowns scored. Other yes/no markets (first TD, last TD, and other
0.5 yes/no lines) are 0/1 so the mean is a rate.
Games in the slate week and later are left out. A game counts only when the
player started. Kickers, punters, and long snappers are not on the 22-man
starter list, so a game counts for them when they played.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

from lib.config import DATA_DIR
from lib.prop_pricing import is_yes_no_half_point_prop, prop_key_from_row
from lib.prop_reprice import _normalize_name

CACHE_DIR = DATA_DIR / "espn_cache"
_CURRENT_SEASON_TTL_SEC = 12 * 3600
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Counting stats copied off the player's own gamelog. Passer-only and
# defender-only fields are gated so a QB's "sacks" (times sacked) is never
# used as a defensive sack prop, and a defender's interceptions are never
# used as passes intercepted.
_PASSER_STATS = {
    "pass_yds": "passingYards",
    "pass_attempts": "passingAttempts",
    "pass_completions": "completions",
    "pass_tds": "passingTouchdowns",
    "pass_ints": "interceptions",
    "pass_long": "longPassing",
}
_SKILL_STATS = {
    "rush_yds": "rushingYards",
    "rush_attempts": "rushingAttempts",
    "rush_long": "longRushing",
    "rush_tds": "rushingTouchdowns",
    "rec_yds": "receivingYards",
    "receptions": "receptions",
    "rec_long": "longReception",
    "rec_tds": "receivingTouchdowns",
}
_DEFENDER_STATS = {
    "sacks": "sacks",
    "solo_tackles": "soloTackles",
    "tackles_assists": "totalTackles",
}
_KICK_STATS = {
    "field_goals": "fieldGoalsMade-fieldGoalAttempts",
    "pats": "extraPointsMade-extraPointAttempts",
    "kicking_pts": "totalKickingPoints",
}
_ANYTIME_TD_FIELDS = (
    "rushingTouchdowns",
    "receivingTouchdowns",
    "interceptionTouchdowns",
    "kickReturnTouchdowns",
    "puntReturnTouchdowns",
    "fumbleReturnTouchdowns",
    "defensiveTouchdowns",
)
_TD_SCORER_RE = re.compile(r"^(.+?)\s+\d+(?:\.\d+)?\s+Yd\b", re.I)
_DST_RE = re.compile(r"\b(d/st|dst|defense)\b", re.I)
_DST_TD_RE = re.compile(
    r"interception|fumble|kickoff return|punt return|blocked",
    re.I,
)
# ESPN's starter flag is the offensive and defensive 22. These positions
# never appear on it, so a game they played is their start.
_SPECIALIST_POSITIONS = frozenset({"22", "23", "78"})
_MADE_ATTEMPT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*[-/]\s*(-?\d+(?:\.\d+)?)$")


def _cache_path(kind: str, *parts: str) -> Path:
    safe = "_".join(re.sub(r"[^A-Za-z0-9._-]+", "", str(p)) for p in parts)
    return CACHE_DIR / f"{kind}_{safe}.json"


def _read_cache(path: Path, *, ttl: float | None) -> Any | None:
    if not path.exists():
        return None
    if ttl is not None:
        try:
            if time.time() - path.stat().st_mtime > ttl:
                return None
        except OSError:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def _get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        response = requests.get(url, params=params, headers=_HEADERS, timeout=25)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_stat(name: str, raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text in {"", "-", "—", "--"}:
        return 0.0
    try:
        value = float(text)
    except ValueError:
        matched = _MADE_ATTEMPT_RE.match(text)
        if not matched:
            return None
        if "Made" not in name and "made" not in name and not name.startswith(("fieldGoals", "extraPoints")):
            return None
        value = float(matched.group(1))
    if not math.isfinite(value):
        return None
    return value


def _gamelog_url(athlete_id: str) -> str:
    return (
        "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/"
        f"{athlete_id}/gamelog"
    )


def _search_nfl_athlete(player: str, team: str | None, *also_teams: str) -> str | None:
    data = _get_json(
        "https://site.web.api.espn.com/apis/common/v3/search",
        {
            "query": player,
            "limit": 12,
            "type": "player",
            "sport": "football",
            "league": "nfl",
        },
    )
    if not data:
        return None
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        if not _names_match(str(item.get("displayName") or ""), player):
            continue
        label = str(item.get("label") or "")
        upper = label.upper()
        if any(tok in upper for tok in ("NCAAF", "COLLEGE", "FBS", "FCS")):
            continue
        team_label = label.split("·")[-1].strip() if "·" in label else label
        if team_label.lower().startswith("nfl - "):
            team_label = team_label[6:].strip()
        if _team_ok(team_label, team or "", *also_teams):
            return str(item.get("id") or "") or None
    return None


def fetch_player_games(athlete_id: str, season: int) -> list[dict[str, Any]]:
    """One row per game with every numeric stat ESPN lists for that player."""
    aid = str(athlete_id or "").strip()
    if not aid:
        return []
    yr = int(season)
    path = _cache_path("gamelog", aid, str(yr))
    ttl = _CURRENT_SEASON_TTL_SEC if yr >= 2026 else None
    cached = _read_cache(path, ttl=ttl)
    if isinstance(cached, list):
        return cached

    data = _get_json(_gamelog_url(aid), {"season": yr})
    if not data:
        return []
    names = [str(n) for n in (data.get("names") or [])]
    events_meta = data.get("events") or {}
    games: list[dict[str, Any]] = []
    seen: set[str] = set()
    for season_type in data.get("seasonTypes") or []:
        label = str(season_type.get("displayName") or season_type.get("name") or "")
        if "preseason" in label.lower():
            continue
        categories = []
        for cat in season_type.get("categories") or []:
            events = cat.get("events") or []
            if events:
                categories.append(cat)
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
                    "season": yr,
                    "week": week_n,
                    "game_date": str(meta.get("gameDate") or ""),
                    "opponent": str(opponent.get("displayName") or meta.get("shortName") or ""),
                    "opponent_abbr": str(opponent.get("abbreviation") or ""),
                    "opponent_logo": str(opponent.get("logo") or ""),
                    "stats": parsed,
                }
            )
    games.sort(key=lambda g: (str(g.get("game_date") or ""), int(g.get("season") or 0), int(g.get("week") or 0)))
    _write_cache(path, games)
    return games


def _athlete_identity(athlete_id: str) -> tuple[str, str] | None:
    aid = str(athlete_id or "").strip()
    if not aid:
        return None
    path = _cache_path("athlete", aid)
    cached = _read_cache(path, ttl=7 * 24 * 3600)
    if isinstance(cached, dict) and cached.get("name"):
        return str(cached.get("name") or ""), str(cached.get("team") or "")
    data = _get_json(
        f"https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{aid}"
    )
    if not data:
        return None
    athlete = data.get("athlete") if isinstance(data.get("athlete"), dict) else data
    name = str(athlete.get("displayName") or athlete.get("fullName") or "")
    team = athlete.get("team") if isinstance(athlete.get("team"), dict) else {}
    team_name = str(team.get("displayName") or "")
    if not name:
        return None
    _write_cache(path, {"name": name, "team": team_name})
    return name, team_name


def _names_match(a: str, b: str) -> bool:
    return bool(_normalize_name(a)) and _normalize_name(a) == _normalize_name(b)


def _team_ok(athlete_team: str, *candidates: str) -> bool:
    from lib.nfl_team_registry import teams_match

    labels = [c for c in candidates if str(c or "").strip()]
    if not labels:
        return True
    if not athlete_team:
        return False
    return any(teams_match(athlete_team, label) for label in labels)


def resolve_espn_id(
    player: str,
    team: str | None,
    espn_id: str | None = None,
    *also_teams: str,
) -> str | None:
    """Return an ESPN id only when the name and current team match this prop."""

    def _accept(aid: str | None) -> str | None:
        if not aid:
            return None
        identity = _athlete_identity(str(aid))
        if not identity:
            return None
        name, athlete_team = identity
        if not _names_match(name, player):
            return None
        if _team_ok(athlete_team, team or "", *also_teams):
            return str(aid)
        return None

    accepted = _accept(str(espn_id or "").strip() or None)
    if accepted:
        return accepted
    return _accept(_search_nfl_athlete(player, team, *also_teams))


def _stat_value(stats: dict[str, float], key: str) -> float | None:
    if key not in stats:
        return None
    return float(stats[key])


def _sum_present(stats: dict[str, float], keys: tuple[str, ...]) -> float | None:
    present = [float(stats[k]) for k in keys if k in stats]
    if not present:
        return None
    return float(sum(present))


def game_stat_value(game: dict[str, Any], prop_key: str) -> float | None:
    """Stat for one game, or None when this player's log does not carry that market."""
    stats = game.get("stats") if isinstance(game.get("stats"), dict) else {}
    pk = str(prop_key or "").lower().strip()
    passer = "passingYards" in stats
    defender = "soloTackles" in stats or "totalTackles" in stats

    if pk in _PASSER_STATS:
        if not passer:
            return None
        return _stat_value(stats, _PASSER_STATS[pk])
    if pk in _DEFENDER_STATS:
        if not defender:
            return None
        return _stat_value(stats, _DEFENDER_STATS[pk])
    if pk in _SKILL_STATS:
        return _stat_value(stats, _SKILL_STATS[pk])
    if pk in _KICK_STATS:
        return _stat_value(stats, _KICK_STATS[pk])
    if pk == "pass_rush_yds":
        return _sum_present(stats, ("passingYards", "rushingYards"))
    if pk == "rr_yds":
        return _sum_present(stats, ("rushingYards", "receivingYards"))
    if pk == "rr_tds":
        return _sum_present(stats, ("rushingTouchdowns", "receivingTouchdowns"))
    if pk == "prd_yds":
        return _sum_present(stats, ("passingYards", "rushingYards", "receivingYards"))
    if pk == "prd_tds":
        return _sum_present(stats, ("passingTouchdowns", "rushingTouchdowns", "receivingTouchdowns"))
    if pk == "tds":
        # Scoring plays name the player who actually scored. Passing TDs do not count.
        if game.get("td_count") is not None:
            return float(game["td_count"])
        return _sum_present(stats, _ANYTIME_TD_FIELDS)
    if pk in {"first_td", "last_td"}:
        flag = game.get(pk)
        if flag is None:
            return None
        return float(flag)
    return None


def _before_slate(game: dict[str, Any], season: int, before_week: int | None) -> bool:
    if before_week is None:
        return True
    try:
        game_season = int(game.get("season") or 0)
    except (TypeError, ValueError):
        return False
    if game_season < int(season):
        return True
    if game_season > int(season):
        return False
    week = game.get("week")
    if week is None:
        return False
    try:
        return int(week) < int(before_week)
    except (TypeError, ValueError):
        return False


def _td_plays(event_id: str) -> list[dict[str, Any]] | None:
    """Touchdowns in chronological order: scorer, team, and whether it is a D/ST score."""
    eid = str(event_id or "").strip()
    if not eid:
        return None
    path = _cache_path("tdplays", eid)
    cached = _read_cache(path, ttl=None)
    if isinstance(cached, dict) and isinstance(cached.get("plays"), list):
        return list(cached["plays"])
    data = _get_json(
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
        {"event": eid},
    )
    if not data:
        return None
    plays = data.get("scoringPlays") or []

    def order(play: dict[str, Any]) -> tuple[int, float]:
        period = play.get("period") if isinstance(play.get("period"), dict) else {}
        clock = play.get("clock") if isinstance(play.get("clock"), dict) else {}
        try:
            period_n = int(period.get("number") or 0)
        except (TypeError, ValueError):
            period_n = 0
        try:
            remaining = float(clock.get("value"))
        except (TypeError, ValueError):
            remaining = -1.0
        return period_n, -remaining

    parsed: list[dict[str, Any]] = []
    for play in sorted(plays, key=order):
        if not isinstance(play, dict):
            continue
        kind = play.get("type") if isinstance(play.get("type"), dict) else {}
        kind_text = str(kind.get("text") or "")
        if "touchdown" not in kind_text.lower():
            continue
        matched = _TD_SCORER_RE.match(str(play.get("text") or "").strip())
        if not matched:
            continue
        team = play.get("team") if isinstance(play.get("team"), dict) else {}
        parsed.append(
            {
                "scorer": matched.group(1).strip(),
                "team": str(team.get("displayName") or ""),
                "dst": bool(_DST_TD_RE.search(kind_text)),
            }
        )
    _write_cache(path, {"plays": parsed})
    return parsed


def _player_scored(play: dict[str, Any], player: str) -> bool:
    scorer = str(play.get("scorer") or "")
    return bool(scorer) and _names_match(scorer, player)


def _attach_td_flags(games: list[dict[str, Any]], player: str) -> None:
    for game in games:
        if game.get("td_count") is not None and game.get("first_td") is not None:
            continue
        plays = _td_plays(str(game.get("event_id") or ""))
        if plays is None:
            continue
        mine = [p for p in plays if _player_scored(p, player)]
        game["td_count"] = float(len(mine))
        game["first_td"] = 1.0 if plays and _player_scored(plays[0], player) else 0.0
        game["last_td"] = 1.0 if plays and _player_scored(plays[-1], player) else 0.0


def _position_id(position: Any) -> str:
    if not isinstance(position, dict):
        return ""
    raw_id = position.get("id")
    if raw_id not in (None, ""):
        return str(raw_id)
    ref = str(position.get("$ref") or "")
    if "/positions/" not in ref:
        return ""
    return ref.split("/positions/")[-1].split("?")[0].strip("/")


@lru_cache(maxsize=2048)
def _event_roles(event_id: str) -> dict[str, tuple[bool, bool, str]] | None:
    """player id -> (started, did_not_play, position id). None when the roster cannot be loaded."""
    eid = str(event_id or "").strip()
    if not eid:
        return None
    path = _cache_path("roles", eid)
    cached = _read_cache(path, ttl=None)
    if isinstance(cached, dict) and isinstance(cached.get("players"), dict):
        out: dict[str, tuple[bool, bool, str]] = {}
        for pid, role in cached["players"].items():
            if not isinstance(role, dict):
                continue
            out[str(pid)] = (
                bool(role.get("starter")),
                bool(role.get("didNotPlay")),
                str(role.get("position") or ""),
            )
        return out

    data = _get_json(
        "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
        f"events/{eid}/competitions/{eid}/competitors"
    )
    items = (data or {}).get("items") or []
    if len(items) < 2:
        return None
    players: dict[str, dict[str, Any]] = {}
    loaded = 0
    for item in items:
        roster_ref = (item.get("roster") or {}).get("$ref") if isinstance(item, dict) else None
        if not roster_ref:
            continue
        roster = _get_json(str(roster_ref))
        if not roster or not isinstance(roster.get("entries"), list):
            continue
        loaded += 1
        for entry in roster["entries"]:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("playerId") or "")
            if not pid:
                continue
            players[pid] = {
                "starter": bool(entry.get("starter")),
                "didNotPlay": bool(entry.get("didNotPlay")),
                "position": _position_id(entry.get("position")),
            }
    if loaded < 2 or not players:
        return None
    _write_cache(path, {"players": players})
    return {
        pid: (bool(role["starter"]), bool(role["didNotPlay"]), str(role["position"]))
        for pid, role in players.items()
    }


def player_started(game: dict[str, Any], athlete_id: str) -> bool:
    """True when this player started. Specialists count when they played."""
    eid = str(game.get("event_id") or "").strip()
    aid = str(athlete_id or "").strip()
    if not eid or not aid:
        return False
    roles = _event_roles(eid)
    if roles is None:
        return True
    role = roles.get(aid)
    if role is None:
        return False
    started, did_not_play, position = role
    if did_not_play:
        return False
    if started:
        return True
    return position in _SPECIALIST_POSITIONS


def player_games(athlete_id: str, season: int) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for yr in (int(season) - 1, int(season)):
        if yr < 1999:
            continue
        frames.extend(fetch_player_games(athlete_id, yr))
    frames.sort(key=lambda g: (str(g.get("game_date") or ""), int(g.get("season") or 0), int(g.get("week") or 0)))
    return frames


_VOLUME_FIELDS = {
    "pass_yds": "passingAttempts",
    "pass_tds": "passingAttempts",
    "pass_long": "passingAttempts",
    "pass_completions": "passingAttempts",
    "rush_yds": "rushingAttempts",
    "rush_tds": "rushingAttempts",
    "rush_long": "rushingAttempts",
    "rec_yds": "receivingTargets",
    "rec_long": "receivingTargets",
    "rec_tds": "receivingTargets",
    "receptions": "receivingTargets",
}


@lru_cache(maxsize=4096)
def volume_trend(
    athlete_id: str,
    prop_key: str,
    season: int,
    before_week: int,
) -> tuple[str, float, float] | None:
    """Last-3 versus last-10 starts on the volume stat behind this prop.

    Returns ``(up|down|flat, last3_mean, last10_mean)``.
    """
    field = _VOLUME_FIELDS.get(str(prop_key or "").lower().strip())
    aid = str(athlete_id or "").strip()
    if not field or not aid:
        return None
    games = [
        game
        for game in player_games(aid, int(season))
        if _before_slate(game, int(season), int(before_week)) and player_started(game, aid)
    ]
    values: list[float] = []
    for game in games[-10:]:
        stats = game.get("stats") if isinstance(game.get("stats"), dict) else {}
        if field not in stats:
            continue
        try:
            values.append(float(stats[field]))
        except (TypeError, ValueError):
            continue
    if len(values) < 5:
        return None
    last10 = sum(values) / len(values)
    recent = values[-3:]
    last3 = sum(recent) / len(recent)
    if last10 <= 0:
        direction = "flat" if last3 <= 0 else "up"
    else:
        ratio = last3 / last10
        gap = last3 - last10
        if ratio <= 0.85 and gap <= -1:
            direction = "down"
        elif ratio >= 1.15 and gap >= 1:
            direction = "up"
        else:
            direction = "flat"
    return direction, round(last3, 2), round(last10, 2)


def recent_market_games(
    athlete_id: str,
    prop_key: str,
    *,
    season: int,
    before_week: int | None = None,
    limit: int = 10,
    player: str = "",
    binary: bool = False,
) -> list[dict[str, Any]]:
    """Last `limit` starts before `before_week`, oldest first."""
    pk = str(prop_key or "").lower().strip()
    games = [
        g
        for g in player_games(athlete_id, int(season))
        if _before_slate(g, int(season), before_week) and player_started(g, athlete_id)
    ]
    window = games[-int(limit) :]
    if pk in {"tds", "first_td", "last_td"} and player:
        _attach_td_flags(window, player)
    out: list[dict[str, Any]] = []
    for game in window:
        value = game_stat_value(game, pk)
        if value is None or not math.isfinite(value):
            continue
        if binary:
            value = 1.0 if value >= 1.0 else 0.0
        opp = str(game.get("opponent") or "Opp")
        abbr = str(game.get("opponent_abbr") or "")
        week = game.get("week")
        log_season = int(game.get("season") or season)
        short = abbr or (opp.split()[-1] if opp else "Opp")
        label = f"{short} {str(log_season)[-2:]}w{week}" if week is not None else short
        out.append(
            {
                "label": label,
                "value": float(value),
                "week": week,
                "season": log_season,
                "opponent": opp,
                "opponent_logo": str(game.get("opponent_logo") or ""),
                "opponent_abbr": abbr or short[:4].upper(),
                "event_id": str(game.get("event_id") or ""),
            }
        )
    return out[-int(limit) :]


def _sample_sd(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = float(statistics.stdev(values))
    if not math.isfinite(sd) or sd <= 1e-9:
        return 0.0
    return sd


def summarize_games(
    games: list[dict[str, Any]],
    line: float | None,
    *,
    binary: bool,
) -> dict[str, Any]:
    values = [float(g["value"]) for g in games]
    if not values:
        return {}
    mean = sum(values) / len(values)
    sd = _sample_sd(values)
    hits = None
    hit_pct = None
    hit_lbl = None
    if line is not None:
        if binary:
            hits = sum(1 for v in values if v >= 1.0)
        else:
            hits = sum(1 for v in values if v > float(line))
        hit_pct = round(100.0 * hits / len(values), 1)
        hit_lbl = f"{hits}/{len(values)}"
    return {
        "stat_avg": round(mean, 4),
        "stat_sd": round(sd, 4) if sd is not None else None,
        "stat_n": len(values),
        "stat_binary": bool(binary),
        "hit_rate": hit_lbl,
        "hit_pct": hit_pct,
    }


def _row_binary(row: dict[str, Any], prop_key: str) -> bool:
    # Anytime TD is the number of touchdowns scored, not the rate of games with one.
    if str(prop_key or "").lower().strip() == "tds":
        return False
    return is_yes_no_half_point_prop(prop_key, row.get("line"), row=row)


def defense_team_name(player: str) -> str | None:
    """'Detroit Lions D/ST' → 'Detroit Lions'. None for real players."""
    raw = str(player or "").strip()
    if not raw or not _DST_RE.search(raw):
        return None
    cleaned = _DST_RE.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -/")
    from lib.nfl_team_registry import is_nfl_franchise, resolve_canonical

    canon = resolve_canonical(cleaned)
    if canon and is_nfl_franchise(canon):
        return canon
    return None


def _team_abbreviation(team: str) -> str | None:
    from lib.nfl_team_registry import NFL_TEAMS, resolve_canonical

    canon = resolve_canonical(team)
    if not canon:
        return None
    for canonical, abbr, _aliases in NFL_TEAMS:
        if canonical == canon:
            return "wsh" if abbr == "WAS" else abbr.lower()
    return None


def _team_schedule_games(team: str, season: int) -> list[dict[str, Any]]:
    abbr = _team_abbreviation(team)
    if not abbr:
        return []
    path = _cache_path("sched", abbr, str(int(season)))
    ttl = _CURRENT_SEASON_TTL_SEC if int(season) >= 2026 else None
    cached = _read_cache(path, ttl=ttl)
    if isinstance(cached, list):
        return cached
    data = _get_json(
        f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{abbr}/schedule",
        {"season": int(season)},
    )
    if not data:
        return []
    games: list[dict[str, Any]] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        season_type = event.get("seasonType") if isinstance(event.get("seasonType"), dict) else {}
        if str(season_type.get("abbreviation") or "").lower() != "reg":
            continue
        comp = (event.get("competitions") or [{}])[0]
        status = ((comp.get("status") or {}).get("type") or {}).get("name")
        if status != "STATUS_FINAL":
            continue
        week = event.get("week") if isinstance(event.get("week"), dict) else {}
        try:
            week_n = int(week.get("number"))
        except (TypeError, ValueError):
            continue
        games.append(
            {
                "event_id": str(event.get("id") or ""),
                "season": int(season),
                "week": week_n,
                "game_date": str(event.get("date") or ""),
                "opponent": str(event.get("shortName") or ""),
            }
        )
    games.sort(key=lambda g: (int(g["season"]), int(g["week"])))
    _write_cache(path, games)
    return games


def defense_recent_games(
    team: str,
    prop_key: str,
    *,
    season: int,
    before_week: int | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Last completed games for a team defense/special-teams TD market."""
    from lib.nfl_team_registry import teams_match

    pk = str(prop_key or "").lower().strip()
    played: list[dict[str, Any]] = []
    for yr in (int(season) - 1, int(season)):
        for game in _team_schedule_games(team, yr):
            if _before_slate(game, int(season), before_week):
                played.append(game)
    played.sort(key=lambda g: (str(g.get("game_date") or ""), int(g.get("season") or 0), int(g.get("week") or 0)))
    window = played[-int(limit) :]
    out: list[dict[str, Any]] = []
    for game in window:
        plays = _td_plays(str(game.get("event_id") or ""))
        if plays is None:
            continue
        if pk == "first_td":
            value = 1.0 if plays and plays[0].get("dst") and teams_match(str(plays[0].get("team") or ""), team) else 0.0
        elif pk == "last_td":
            value = 1.0 if plays and plays[-1].get("dst") and teams_match(str(plays[-1].get("team") or ""), team) else 0.0
        else:
            value = float(
                sum(1 for p in plays if p.get("dst") and teams_match(str(p.get("team") or ""), team))
            )
        week = game.get("week")
        log_season = int(game.get("season") or season)
        out.append(
            {
                "label": f"{log_season}w{week}",
                "value": value,
                "week": week,
                "season": log_season,
                "opponent": str(game.get("opponent") or ""),
                "opponent_logo": "",
                "opponent_abbr": "",
                "event_id": str(game.get("event_id") or ""),
            }
        )
    return out


def _no_touchdown_games(
    home: str,
    away: str,
    *,
    season: int,
    before_week: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """1 when a recent game of either team had no touchdown."""
    seen: dict[str, dict[str, Any]] = {}
    for team in (home, away):
        if not str(team or "").strip():
            continue
        for yr in (int(season) - 1, int(season)):
            for game in _team_schedule_games(team, yr):
                if not _before_slate(game, int(season), int(before_week)):
                    continue
                eid = str(game.get("event_id") or "")
                if eid:
                    seen[eid] = game
    chosen = sorted(
        seen.values(),
        key=lambda g: (str(g.get("game_date") or ""), int(g.get("season") or 0), int(g.get("week") or 0)),
    )[-int(limit) :]
    out: list[dict[str, Any]] = []
    for game in chosen:
        plays = _td_plays(str(game.get("event_id") or ""))
        if plays is None:
            continue
        out.append({"value": 0.0 if plays else 1.0})
    return out


def apply_l10_to_props(
    props: list[dict[str, Any]],
    *,
    year: int,
    week: int,
    max_workers: int = 6,
) -> list[dict[str, Any]]:
    """Overwrite last-10 mean, sd, and hit rate for every row. Wrong ids are dropped."""
    if not props:
        return []

    slots: dict[tuple[str, str], dict[str, Any]] = {}
    for row in props:
        player = str(row.get("player") or "").strip()
        team = str(row.get("team") or "").strip()
        if not player:
            continue
        if defense_team_name(player):
            continue
        slots.setdefault((player, team), row)

    resolved: dict[tuple[str, str], str | None] = {}
    workers = max(1, min(int(max_workers), max(1, len(slots))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                resolve_espn_id,
                player,
                team or None,
                str(sample.get("espn_id") or ""),
                str(sample.get("home") or ""),
                str(sample.get("away") or ""),
            ): (player, team)
            for (player, team), sample in slots.items()
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                resolved[key] = fut.result()
            except Exception:
                resolved[key] = None

    ids = sorted({aid for aid in resolved.values() if aid})
    logs: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(player_games, aid, int(year)): aid for aid in ids}
        for fut in as_completed(futures):
            aid = futures[fut]
            try:
                logs[aid] = fut.result()
            except Exception:
                logs[aid] = []

    # Anytime / first / last TD use the scoring play, not the passing-TD column.
    td_ids: set[str] = set()
    for row in props:
        pk = str(row.get("prop_key") or row.get("propKey") or prop_key_from_row(row) or "").lower()
        if pk not in {"tds", "first_td", "last_td"}:
            continue
        player = str(row.get("player") or "").strip()
        team = str(row.get("team") or "").strip()
        dst = defense_team_name(player)
        if dst:
            played = []
            for yr in (int(year) - 1, int(year)):
                played.extend(
                    g for g in _team_schedule_games(dst, yr) if _before_slate(g, int(year), int(week))
                )
            played.sort(key=lambda g: (str(g.get("game_date") or ""), int(g.get("week") or 0)))
            for game in played[-10:]:
                eid = str(game.get("event_id") or "")
                if eid:
                    td_ids.add(eid)
            continue
        aid = resolved.get((player, team))
        if not aid:
            continue
        prior = [g for g in logs.get(aid, []) if _before_slate(g, int(year), int(week))]
        for game in prior[-10:]:
            eid = str(game.get("event_id") or "")
            if eid:
                td_ids.add(eid)
    if td_ids:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_td_plays, eid): eid for eid in td_ids}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception:
                    continue

    out: list[dict[str, Any]] = []
    for row in props:
        merged = dict(row)
        player = str(merged.get("player") or "").strip()
        team = str(merged.get("team") or "").strip()
        pk = str(merged.get("prop_key") or merged.get("propKey") or prop_key_from_row(merged) or "").lower()
        aid = resolved.get((player, team)) if player else None
        if aid:
            merged["espn_id"] = aid
        elif merged.get("espn_id"):
            # Stored id failed the name/team check.
            merged["espn_id"] = None
        binary = _row_binary(merged, pk)
        try:
            line = float(merged.get("line"))
        except (TypeError, ValueError):
            line = None
        summary: dict[str, Any] = {}
        dst_team = defense_team_name(player)
        if _names_match(player, "No Touchdown") and pk in {"tds", "first_td", "last_td"}:
            games = _no_touchdown_games(
                str(merged.get("home") or ""),
                str(merged.get("away") or ""),
                season=int(year),
                before_week=int(week),
            )
            summary = summarize_games(games, line if line is not None else 0.5, binary=True)
        elif dst_team and pk:
            games = defense_recent_games(
                dst_team,
                pk,
                season=int(year),
                before_week=int(week),
                limit=10,
            )
            if binary:
                for game in games:
                    game["value"] = 1.0 if float(game.get("value") or 0) >= 1.0 else 0.0
            summary = summarize_games(games, line, binary=binary)
        elif aid and pk:
            games = recent_market_games(
                aid,
                pk,
                season=int(year),
                before_week=int(week),
                limit=10,
                player=player,
                binary=binary,
            )
            summary = summarize_games(games, line, binary=binary)
        merged["stat_avg"] = summary.get("stat_avg")
        merged["stat_sd"] = summary.get("stat_sd")
        merged["stat_n"] = summary.get("stat_n")
        merged["stat_binary"] = bool(summary.get("stat_binary"))
        merged["hit_rate"] = summary.get("hit_rate")
        merged["hit_pct"] = summary.get("hit_pct")
        out.append(merged)
    return out
