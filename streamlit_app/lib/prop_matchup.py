"""Sport-aware prop matchup router — CFB SP+ or NFL nfelo."""
from __future__ import annotations

from typing import Any


def apply_prop_matchup_to_baseline(
    baseline: float | None,
    prop: str,
    team_name: str,
    opp_name: str,
    *,
    home: str | None = None,
    away: str | None = None,
    season: int | None = None,
    display_week: int | None = None,
) -> float | None:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfelo_prop_matchup import apply_nfelo_matchup_to_baseline

        return apply_nfelo_matchup_to_baseline(
            baseline,
            prop,
            team_name,
            opp_name,
            home=home,
            away=away,
            season=season,
            display_week=display_week,
        )
    from .sp_prop_matchup import apply_sp_matchup_to_baseline

    return apply_sp_matchup_to_baseline(
        baseline,
        prop,
        team_name,
        opp_name,
        home=home,
        away=away,
        season=season,
        display_week=display_week,
    )


def defense_display_adjustment_pct(
    opp_name: str,
    prop_key: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
) -> float | None:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfelo_prop_matchup import defense_display_adjustment_pct as nfl_def

        return nfl_def(opp_name, prop_key)
    from .sp_prop_matchup import defense_display_adjustment_pct as cfb_def

    return cfb_def(opp_name, prop_key, season=season, display_week=display_week)


def lookup_def_rank(
    team_name: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
) -> int | None:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfelo_ratings import lookup_nfelo_def_rank

        return lookup_nfelo_def_rank(team_name)
    from .sp_prop_matchup import _lookup_sp_def_rank

    return _lookup_sp_def_rank(team_name, season=season, display_week=display_week)


def matchup_factors(
    team_name: str,
    opp_name: str,
    *,
    home: str | None = None,
    away: str | None = None,
    season: int | None = None,
    display_week: int | None = None,
) -> dict[str, Any]:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfelo_prop_matchup import nfelo_matchup_factors

        return nfelo_matchup_factors(team_name, opp_name, home=home, away=away)
    from .sp_prop_matchup import sp_matchup_factors

    return sp_matchup_factors(
        team_name,
        opp_name,
        home=home,
        away=away,
        season=season,
        display_week=display_week,
    )
