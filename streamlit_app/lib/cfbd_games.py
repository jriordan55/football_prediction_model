"""CFBD schedule + completed scores for matchup history."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import DATA_DIR
from .team_registry import resolve_canonical, team_key, teams_match


def _games_path(year: int):
    return DATA_DIR / f"cfbd_games_{year}.json"


@lru_cache(maxsize=8)
def load_cfbd_games(year: int) -> list[dict[str, Any]]:
    path = _games_path(year)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _is_fbs(g: dict[str, Any]) -> bool:
    return g.get("homeClassification") == "fbs" and g.get("awayClassification") == "fbs"


def is_week0_game(g: dict[str, Any]) -> bool:
    """Week 0 = CFBD week-1 FBS games kicking off in August (opening weekend)."""
    if g.get("seasonType") != "regular":
        return False
    if int(g.get("week") or -1) != 1:
        return False
    if not _is_fbs(g):
        return False
    start = str(g.get("startDate") or "")
    return start[:7] == f"{g.get('season', 2026)}-08" or (start and start < f"{g.get('season', 2026)}-09-01")


def cfbd_week_for_display(display_week: int) -> int | None:
    """Map UI week to CFBD week filter."""
    if display_week == 0:
        return 1
    return display_week


def list_games_for_display_week(year: int, display_week: int, *, fbs_only: bool = True) -> list[dict[str, Any]]:
    games = load_cfbd_games(year)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for g in games:
        if g.get("seasonType") != "regular":
            continue
        if fbs_only and not _is_fbs(g):
            continue
        if g.get("homeTeam") == g.get("awayTeam"):
            continue

        cfbd_wk = int(g.get("week") or -1)
        w0 = is_week0_game(g)

        if display_week == 0:
            if not w0:
                continue
        elif display_week == 1:
            if cfbd_wk != 1 or w0:
                continue
        else:
            if cfbd_wk != display_week:
                continue

        home = resolve_canonical(g.get("homeTeam")) or g.get("homeTeam")
        away = resolve_canonical(g.get("awayTeam")) or g.get("awayTeam")
        key = f"{team_key(away)}|{team_key(home)}"
        if key in seen:
            continue
        seen.add(key)

        home_pts = g.get("homePoints")
        away_pts = g.get("awayPoints")
        status = str(g.get("status") or "").lower()
        completed = bool(g.get("completed")) or status in ("completed", "final")

        out.append(
            {
                "id": g.get("id"),
                "home": home,
                "away": away,
                "week": display_week,
                "cfbd_week": cfbd_wk,
                "startDate": g.get("startDate"),
                "venue": g.get("venue"),
                "neutral": bool(g.get("neutralSite")),
                "homePoints": home_pts,
                "awayPoints": away_pts,
                "completed": completed,
                "homeLineScores": g.get("homeLineScores"),
                "awayLineScores": g.get("awayLineScores"),
            }
        )

    out.sort(key=lambda x: (x.get("startDate") or "", x.get("away") or ""))
    return out


def lookup_completed_game(year: int, home: str, away: str) -> dict[str, Any] | None:
    hk, ak = team_key(home), team_key(away)
    for g in load_cfbd_games(year):
        if team_key(g.get("homeTeam")) != hk or team_key(g.get("awayTeam")) != ak:
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
            "homeLineScores": g.get("homeLineScores"),
            "awayLineScores": g.get("awayLineScores"),
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
    """Fill completed game from ESPN scores only when the feed marks the game final."""
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
    """True when every game on this display week has a final score."""
    games = list_games_for_display_week(year, display_week, fbs_only=False)
    if not games:
        return False
    for g in games:
        if not g.get("completed"):
            return False
        if g.get("homePoints") is None or g.get("awayPoints") is None:
            return False
    return True
