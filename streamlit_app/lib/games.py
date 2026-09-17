"""Sport-aware games API — delegates to CFBD or NFL backends."""
from __future__ import annotations

from typing import Any

from .sport_context import SPORT_NFL, resolve_sport


def list_games_for_display_week(
    year: int,
    display_week: int,
    *,
    fbs_only: bool = True,
    sport: str | None = None,
) -> list[dict[str, Any]]:
    sid = resolve_sport(sport)
    if sid == SPORT_NFL:
        from .nfl_games import list_games_for_display_week as _nfl

        return _nfl(year, display_week, fbs_only=fbs_only)
    from .cfbd_games import list_games_for_display_week as _cfb

    return _cfb(year, display_week, fbs_only=fbs_only)


def lookup_completed_game(
    year: int,
    home: str,
    away: str,
    *,
    sport: str | None = None,
) -> dict[str, Any] | None:
    sid = resolve_sport(sport)
    if sid == SPORT_NFL:
        from .nfl_games import lookup_completed_game as _nfl

        return _nfl(year, home, away)
    from .cfbd_games import lookup_completed_game as _cfb

    return _cfb(year, home, away)


def merge_completed(
    result: dict[str, Any] | None,
    home: str,
    away: str,
    *,
    home_pts: Any,
    away_pts: Any,
    status_completed: bool = False,
    sport: str | None = None,
) -> dict[str, Any] | None:
    sid = resolve_sport(sport)
    if sid == SPORT_NFL:
        from .nfl_games import merge_completed as _nfl

        return _nfl(
            result,
            home,
            away,
            home_pts=home_pts,
            away_pts=away_pts,
            status_completed=status_completed,
        )
    from .cfbd_games import merge_completed as _cfb

    return _cfb(
        result,
        home,
        away,
        home_pts=home_pts,
        away_pts=away_pts,
        status_completed=status_completed,
    )


def display_week_complete(
    year: int,
    display_week: int,
    *,
    sport: str | None = None,
) -> bool:
    sid = resolve_sport(sport)
    if sid == SPORT_NFL:
        from .nfl_games import display_week_complete as _nfl

        return _nfl(year, display_week)
    from .cfbd_games import display_week_complete as _cfb

    if _cfb(year, display_week):
        return True
    return _board_week_complete(year, display_week, sport=sid)


def _board_week_complete(year: int, display_week: int, *, sport: str) -> bool:
    """ESPN/matchup board fallback when CFBD schedule file is missing or stale."""
    from .matchup_board import load_matchup_board

    board = load_matchup_board(sport, int(year), int(display_week), tab=None)
    if board.empty:
        return False
    if "completed" not in board.columns:
        return False
    completed = board["completed"].fillna(False).astype(bool)
    if not completed.all():
        return False
    if "home_score" in board.columns and "away_score" in board.columns:
        scores_ok = board["home_score"].notna() & board["away_score"].notna()
        return bool(scores_ok.all())
    return True
