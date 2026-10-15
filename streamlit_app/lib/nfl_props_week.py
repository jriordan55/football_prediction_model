"""Which NFL week the props table should use."""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from lib.sport_context import SPORT_NFL, get_sport_config

# A Monday-night game is still the current slate until it has had time to finish.
_SLATE_WINDOW = timedelta(hours=5)


def _kickoff(game: dict[str, Any]) -> datetime | None:
    raw = game.get("startDate")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def slate_week_from_schedule(year: int) -> int | None:
    """Earliest week whose last kickoff has not finished. Later weeks stay off the table."""
    from lib.nfl_games import load_nfl_games

    by_week: dict[int, list[datetime]] = {}
    for game in load_nfl_games(int(year)):
        try:
            week = int(game.get("week"))
        except (TypeError, ValueError):
            continue
        if not 1 <= week <= 18:
            continue
        kick = _kickoff(game)
        if kick is not None:
            by_week.setdefault(week, []).append(kick)
    if not by_week:
        return None
    now = datetime.now(timezone.utc)
    for week in sorted(by_week):
        if max(by_week[week]) + _SLATE_WINDOW > now:
            return week
    return max(by_week)


def resolve_nfl_props_week(year: int | None = None) -> int:
    """Props table week: ``NFL_WEEK`` env, else the current schedule slate."""
    cfg = get_sport_config(SPORT_NFL)
    raw = os.getenv("NFL_WEEK")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    yr = int(year) if year is not None else int(cfg["default_year"])
    slate = slate_week_from_schedule(yr)
    if slate is not None:
        return int(slate)
    return int(cfg["default_week"])


def week_for_live_odds_events(year: int, events: list[dict[str, Any]]) -> int | None:
    """NFL week of the slate currently posted on The Odds API."""
    from lib.nfl_games import load_nfl_games
    from lib.nfl_team_registry import teams_match

    games = load_nfl_games(int(year))
    if not games or not events:
        return None
    weeks: list[int] = []
    for event in events:
        home = event.get("home_team")
        away = event.get("away_team")
        if not home or not away:
            continue
        for game in games:
            if not teams_match(home, game.get("homeTeam")) or not teams_match(away, game.get("awayTeam")):
                continue
            try:
                week = int(game.get("week"))
            except (TypeError, ValueError):
                break
            if 1 <= week <= 18:
                weeks.append(week)
            break
    if not weeks:
        return None
    return int(Counter(weeks).most_common(1)[0][0])
