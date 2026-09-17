"""nfelo game pricing — spread, total, team scores from nfelo power ratings."""
from __future__ import annotations

import math
from typing import Any

from .nfelo_ratings import ensure_nfelo_ratings, lookup_nfelo_team
from .nfl_team_registry import resolve_canonical
from .sp_projections import (
    _home_win_from_margin,
    _poisson_over_prob,
    _round_half,
    _round_prob,
    _skellam_home_cover,
    realistic_team_scores,
    scores_from_spread_total,
)

NFL_HFA = 2.5
DEFAULT_MARGIN_SIGMA = 13.5
MARKET_BLEND = 0.12


def price_game_from_nfelo(
    home_name: str,
    away_name: str,
    *,
    neutral: bool = False,
    market_spread: float | None = None,
    market_total: float | None = None,
    refresh_ratings: bool = True,
) -> dict[str, Any] | None:
    """Project spread/total/scores from nfelo spread value + scoring rates."""
    if refresh_ratings:
        ensure_nfelo_ratings()
    home_name = resolve_canonical(home_name) or home_name
    away_name = resolve_canonical(away_name) or away_name
    home = lookup_nfelo_team(home_name, refresh=refresh_ratings)
    away = lookup_nfelo_team(away_name, refresh=refresh_ratings)
    if not home or not away:
        return None

    hfa = 0.0 if neutral else NFL_HFA
    margin = float(home.get("spread_value") or 0) - float(away.get("spread_value") or 0) + hfa
    spread = _round_half(-margin)

    home_exp = (float(home.get("pts_for") or 22) + float(away.get("pts_against") or 22)) / 2.0
    away_exp = (float(away.get("pts_for") or 22) + float(home.get("pts_against") or 22)) / 2.0
    total = _round_half(home_exp + away_exp)

    if market_spread is not None:
        spread = _round_half(float(market_spread) * (1 - MARKET_BLEND) + spread * MARKET_BLEND)
        margin = -spread
    if market_total is not None:
        total = _round_half(float(market_total) * (1 - MARKET_BLEND) + total * MARKET_BLEND)

    home_pts = max(3.0, (total + margin) / 2.0)
    away_pts = max(3.0, (total - margin) / 2.0)
    away_score, home_score = realistic_team_scores(home_pts, away_pts, spread=spread, total=total)

    fair_spread = abs(spread)
    p_home_cover = _skellam_home_cover(away_pts, home_pts, fair_spread)
    p_over = _poisson_over_prob(total, home_pts + away_pts)
    home_win = _home_win_from_margin(margin, DEFAULT_MARGIN_SIGMA)

    return {
        "home": home_name,
        "away": away_name,
        "spread": spread,
        "total": total,
        "home_score": home_score,
        "away_score": away_score,
        "margin": _round_half(margin),
        "home_lambda": home_pts,
        "away_lambda": away_pts,
        "spread_market": {
            "line": spread,
            "home_cover_prob": _round_prob(p_home_cover),
            "away_cover_prob": _round_prob(1 - p_home_cover if p_home_cover is not None else None),
        },
        "total_market": {
            "line": total,
            "over_prob": _round_prob(p_over),
            "under_prob": _round_prob(1 - p_over if p_over is not None else None),
        },
        "moneyline": {
            "home_win_prob": _round_prob(home_win),
            "away_win_prob": _round_prob(1 - home_win if home_win is not None else None),
        },
        "model": "nfelo",
    }
