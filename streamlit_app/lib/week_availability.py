"""When a display week has started, finished, or is still in the future."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from lib.game_status import game_past_kickoff
from lib.games import display_week_complete, list_games_for_display_week
from lib.matchup_board import load_matchup_board
from lib.sport_context import SPORT_NFL, cache_sport

WeekState = Literal["future", "in_progress", "complete"]


def _parse_start(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def week_calendar_started(year: int, week: int, *, sport: str | None = None) -> bool:
    """Conservative calendar check when schedule data is missing or stale."""
    now = datetime.now(timezone.utc)
    sid = sport or cache_sport()
    wk = int(week)
    yr = int(year)

    if sid == SPORT_NFL:
        anchor = datetime(yr, 9, 4, 17, 0, tzinfo=timezone.utc) + timedelta(weeks=max(0, wk - 1))
        return now >= anchor

    if wk == 0:
        anchor = datetime(yr, 8, 23, 16, 0, tzinfo=timezone.utc)
    else:
        anchor = datetime(yr, 8, 30, 16, 0, tzinfo=timezone.utc) + timedelta(weeks=max(0, wk - 1))
    return now >= anchor


def _state_from_kickoffs(kickoffs: list[datetime], *, calendar_started: bool) -> WeekState:
    now = datetime.now(timezone.utc)
    if not kickoffs:
        return "in_progress" if calendar_started else "future"
    if all(k > now for k in kickoffs):
        return "in_progress" if calendar_started else "future"
    return "in_progress"


def week_schedule_state(year: int, week: int) -> WeekState:
    """future = no kickoffs yet; in_progress = started; complete = all finals."""
    yr, wk = int(year), int(week)
    sport = cache_sport()
    if display_week_complete(yr, wk, sport=sport):
        return "complete"

    calendar_started = week_calendar_started(yr, wk, sport=sport)

    board = load_matchup_board(sport, yr, wk)
    if board.empty:
        games = list_games_for_display_week(yr, wk, fbs_only=False, sport=sport)
        if not games:
            return "in_progress" if calendar_started else "future"
        kickoffs = [_parse_start(str(g.get("startDate") or "")) for g in games]
        kickoffs = [k for k in kickoffs if k is not None]
        if any(game_past_kickoff(str(g.get("startDate") or "")) for g in games):
            return "in_progress"
        return _state_from_kickoffs(kickoffs, calendar_started=calendar_started)

    now = datetime.now(timezone.utc)
    kickoffs = [_parse_start(str(r.get("date") or "")) for _, r in board.iterrows()]
    kickoffs = [k for k in kickoffs if k is not None]
    if any(game_past_kickoff(str(r.get("date") or "")) for _, r in board.iterrows()):
        return "in_progress"
    if not kickoffs:
        return "in_progress" if calendar_started else "future"
    if all(k > now for k in kickoffs):
        return "in_progress" if calendar_started else "future"
    return "in_progress"


def week_has_started(year: int, week: int) -> bool:
    if week_calendar_started(int(year), int(week)):
        return True
    return week_schedule_state(year, week) != "future"


def current_display_week(year: int, sport: str | None = None) -> int:
    """Default week: latest slate with kickoffs, or next upcoming week if prior is complete."""
    from lib.sport_context import get_sport_config, week_options

    sid = sport or cache_sport()
    opts = week_options(sid)
    if not opts:
        return int(get_sport_config(sid)["default_week"])

    latest_started = int(opts[0])
    for w in opts:
        if week_schedule_state(int(year), int(w)) != "future":
            latest_started = int(w)
        elif week_calendar_started(int(year), int(w), sport=sid):
            latest_started = int(w)

    week = latest_started
    while week < int(opts[-1]) and display_week_complete(int(year), int(week), sport=sid):
        nxt = week + 1
        board = load_matchup_board(sid, int(year), nxt, tab=None)
        if not board.empty or week_calendar_started(int(year), nxt, sport=sid):
            return nxt
        week = nxt
    return int(latest_started)
