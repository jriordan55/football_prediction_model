"""Rating inputs — SP+ (CFB) and ELO/nfelo (NFL)."""
from __future__ import annotations

from typing import Any

from lib.sport_context import SPORT_CFB, SPORT_NFL


def price_base_game(
    sport: str,
    home: str,
    away: str,
    *,
    season: int | None = None,
    week: int | None = None,
    market_spread: float | None = None,
    market_total: float | None = None,
    neutral: bool = False,
) -> dict[str, Any] | None:
    """Return spread/total/lambdas from SP+ or NFL ELO ratings."""
    from lib.config import DEFAULT_YEAR
    from pricing_engine.pit import ratings_should_refresh

    yr = int(season if season is not None else DEFAULT_YEAR)
    refresh = ratings_should_refresh(sport, yr, week)
    sid = str(sport).lower()
    if sid == SPORT_NFL:
        from lib.nfelo_projections import price_game_from_nfelo

        return price_game_from_nfelo(
            home,
            away,
            neutral=neutral,
            market_spread=market_spread,
            market_total=market_total,
            refresh_ratings=refresh,
        )
    from lib.sp_projections import price_game_from_sp

    base = price_game_from_sp(
        home,
        away,
        season=season,
        display_week=week,
        refresh=refresh,
    )
    if base and (market_spread is not None or market_total is not None):
        blend = 0.12
        if market_spread is not None and base.get("spread") is not None:
            base["spread"] = round(float(market_spread) * (1 - blend) + float(base["spread"]) * blend, 1)
            sm = base.get("spread_market") or {}
            sm["line"] = base["spread"]
            base["spread_market"] = sm
        if market_total is not None and base.get("total") is not None:
            base["total"] = round(float(market_total) * (1 - blend) + float(base["total"]) * blend, 1)
            tm = base.get("total_market") or {}
            tm["line"] = base["total"]
            base["total_market"] = tm
    return base


def rating_label(sport: str) -> str:
    return "SP+" if str(sport).lower() == SPORT_CFB else "ELO"


def default_team_rates(home_lambda: float, away_lambda: float) -> tuple[dict[str, float], dict[str, float]]:
    """Fallback yard/TD rates from score lambdas."""
    home = {
        "pass_yds": max(80, home_lambda * 9.5),
        "rush_yds": max(60, home_lambda * 4.2),
        "tds": max(0.5, home_lambda / 6.5),
    }
    away = {
        "pass_yds": max(80, away_lambda * 9.5),
        "rush_yds": max(60, away_lambda * 4.2),
        "tds": max(0.5, away_lambda / 6.5),
    }
    return home, away
