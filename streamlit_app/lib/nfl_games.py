"""NFL schedule + scores — ESPN scoreboard with disk cache."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import DATA_DIR
from .nfl_team_registry import resolve_canonical, team_key, teams_match


def _games_path(year: int) -> Any:
    return DATA_DIR / f"nfl_games_{year}.json"


def _persist_games(year: int, games: list[dict[str, Any]]) -> None:
    path = _games_path(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games, indent=2), encoding="utf-8")
    load_nfl_games.cache_clear()


@lru_cache(maxsize=8)
def load_nfl_games(year: int) -> list[dict[str, Any]]:
    path = _games_path(year)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return _refresh_nfl_games_from_espn(year)


def _refresh_nfl_games_from_espn(year: int) -> list[dict[str, Any]]:
    from .espn_client import fetch_scoreboard_season

    df = fetch_scoreboard_season(year)
    if df.empty:
        return []

    games: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        home = resolve_canonical(row.get("home")) or row.get("home")
        away = resolve_canonical(row.get("away")) or row.get("away")
        if not home or not away:
            continue
        week = row.get("week")
        try:
            week_i = int(week) if week is not None else None
        except (TypeError, ValueError):
            week_i = None
        home_pts = row.get("home_score")
        away_pts = row.get("away_score")
        completed = bool(row.get("completed"))
        games.append(
            {
                "id": row.get("event_id"),
                "homeTeam": home,
                "awayTeam": away,
                "week": week_i,
                "season": year,
                "startDate": row.get("date"),
                "venue": row.get("venue"),
                "neutralSite": False,
                "homePoints": home_pts,
                "awayPoints": away_pts,
                "completed": completed,
                "status": "completed" if completed else "scheduled",
            }
        )
    if games:
        _persist_games(year, games)
    return games


def list_games_for_display_week(
    year: int,
    display_week: int,
    *,
    fbs_only: bool = True,
) -> list[dict[str, Any]]:
    _ = fbs_only
    if display_week < 1:
        return []

    games = load_nfl_games(year)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Prefer live ESPN week filter — cached season files can mis-tag weeks.
    from .espn_client import fetch_scoreboard

    sb = fetch_scoreboard(week=display_week, year=year)
    if not sb.empty:
        for _, row in sb.iterrows():
            home = resolve_canonical(row.get("home")) or row.get("home")
            away = resolve_canonical(row.get("away")) or row.get("away")
            if not home or not away:
                continue
            key = f"{team_key(away)}|{team_key(home)}"
            if key in seen:
                continue
            seen.add(key)
            home_pts = row.get("home_score")
            away_pts = row.get("away_score")
            completed = bool(row.get("completed"))
            out.append(
                {
                    "id": row.get("event_id"),
                    "home": home,
                    "away": away,
                    "week": display_week,
                    "startDate": row.get("date"),
                    "venue": row.get("venue"),
                    "neutral": False,
                    "homePoints": home_pts,
                    "awayPoints": away_pts,
                    "completed": completed,
                }
            )
        out.sort(key=lambda x: (x.get("startDate") or "", x.get("away") or ""))
        return out

    if not games:
        return []

    for g in games:
        wk = g.get("week")
        try:
            wk_i = int(wk) if wk is not None else -1
        except (TypeError, ValueError):
            wk_i = -1
        if wk_i != int(display_week):
            continue
        home = resolve_canonical(g.get("homeTeam")) or g.get("homeTeam")
        away = resolve_canonical(g.get("awayTeam")) or g.get("awayTeam")
        key = f"{team_key(away)}|{team_key(home)}"
        if key in seen:
            continue
        seen.add(key)
        home_pts = g.get("homePoints")
        away_pts = g.get("awayPoints")
        completed = bool(g.get("completed")) or (
            home_pts is not None and away_pts is not None
        )
        out.append(
            {
                "id": g.get("id"),
                "home": home,
                "away": away,
                "week": display_week,
                "startDate": g.get("startDate"),
                "venue": g.get("venue"),
                "neutral": bool(g.get("neutralSite")),
                "homePoints": home_pts,
                "awayPoints": away_pts,
                "completed": completed,
            }
        )
    out.sort(key=lambda x: (x.get("startDate") or "", x.get("away") or ""))
    return out


def lookup_completed_game(year: int, home: str, away: str) -> dict[str, Any] | None:
    hk, ak = team_key(home), team_key(away)
    for g in load_nfl_games(year):
        gh = team_key(g.get("homeTeam"))
        ga = team_key(g.get("awayTeam"))
        if gh != hk or ga != ak:
            continue
        home_pts, away_pts = g.get("homePoints"), g.get("awayPoints")
        if home_pts is None or away_pts is None:
            continue
        return {
            "home": resolve_canonical(g.get("homeTeam")) or g.get("homeTeam"),
            "away": resolve_canonical(g.get("awayTeam")) or g.get("awayTeam"),
            "week": g.get("week"),
            "homePoints": int(home_pts),
            "awayPoints": int(away_pts),
            "margin": int(home_pts) - int(away_pts),
            "total": int(home_pts) + int(away_pts),
            "completed": True,
            "startDate": g.get("startDate"),
            "venue": g.get("venue"),
        }
    return None


def merge_completed(
    result: dict[str, Any] | None,
    home: str,
    away: str,
    *,
    home_pts: Any,
    away_pts: Any,
    status_completed: bool = False,
) -> dict[str, Any] | None:
    base = dict(result or {})
    base.setdefault("home", resolve_canonical(home) or home)
    base.setdefault("away", resolve_canonical(away) or away)
    try:
        hp = int(home_pts) if home_pts is not None and str(home_pts).strip() != "" else None
        ap = int(away_pts) if away_pts is not None and str(away_pts).strip() != "" else None
    except (TypeError, ValueError):
        hp = ap = None
    if hp is not None:
        base["homePoints"] = hp
    if ap is not None:
        base["awayPoints"] = ap
    if hp is not None and ap is not None:
        base["margin"] = hp - ap
        base["total"] = hp + ap
    if base.get("completed"):
        return base
    if not status_completed or hp is None or ap is None:
        base["completed"] = False
        return base if base else None
    base["completed"] = True
    return base


def display_week_complete(year: int, display_week: int) -> bool:
    games = list_games_for_display_week(year, display_week, fbs_only=False)
    if not games:
        return False
    for g in games:
        if not g.get("completed"):
            return False
        if g.get("homePoints") is None or g.get("awayPoints") is None:
            return False
    return True
