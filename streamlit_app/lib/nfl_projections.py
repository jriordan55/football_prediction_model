"""NFL game pricing — nfelo power ratings for spread, total, and team scores."""
from __future__ import annotations

from typing import Any

from .nfelo_projections import price_game_from_nfelo
from .nfelo_ratings import ensure_nfelo_ratings


def price_game_nfl(
    home: str,
    away: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
    market_spread: float | None = None,
    market_total: float | None = None,
) -> dict[str, Any] | None:
    _ = season, display_week
    ensure_nfelo_ratings()
    return price_game_from_nfelo(
        home,
        away,
        market_spread=market_spread,
        market_total=market_total,
    )


def price_game(
    home: str,
    away: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
    market_spread: float | None = None,
    market_total: float | None = None,
    refresh: bool = True,
) -> dict[str, Any] | None:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        return price_game_nfl(
            home,
            away,
            season=season,
            display_week=display_week,
            market_spread=market_spread,
            market_total=market_total,
        )
    from .sp_projections import price_game_from_sp

    return price_game_from_sp(
        home,
        away,
        season=season,
        display_week=display_week,
        refresh=refresh,
    )
