"""Shared rules for whether a game is finished vs still upcoming."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_start(start: Any) -> datetime | None:
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def game_is_final(
    *,
    completed: bool = False,
    home_pts: Any = None,
    away_pts: Any = None,
    start_date: Any = None,
) -> bool:
    """
    True when the game is finished with real scores.
    Pre-kickoff 0-0 placeholders must not count as final.
    """
    try:
        hp = int(home_pts) if home_pts is not None and str(home_pts).strip() not in ("", "nan") else None
        ap = int(away_pts) if away_pts is not None and str(away_pts).strip() not in ("", "nan") else None
    except (TypeError, ValueError):
        return False
    if hp is None or ap is None:
        return False

    kick = _parse_start(start_date)
    if kick:
        # Don't treat as final until ~4 hours after scheduled kickoff
        if kick + timedelta(hours=4) > datetime.now(timezone.utc):
            return False

    return bool(completed)


def game_past_kickoff(start_date: Any, *, buffer_hours: float = 4.0) -> bool:
    """True when kickoff was long enough ago that the game should have finished."""
    kick = _parse_start(start_date)
    if not kick:
        return False
    return kick + timedelta(hours=buffer_hours) <= datetime.now(timezone.utc)
