"""FEI-based CFB game pricing (BCF Toys) — replaces SP+ for pricing engine."""
from __future__ import annotations

import json
import math
from typing import Any

from .config import DATA_DIR
from .fei_ratings import FEI_CALIB_PATH, ensure_fei_ratings, lookup_fei_team

LEAGUE_AVG_PTS = 28.5
DEFAULT_HFA = 2.81
DEFAULT_MARGIN_SIGMA = 16.09
DEFAULT_FEI_TO_MARGIN = 13.5
DEFAULT_TOTAL_FEI_SLOPE = -4.5


def _round_half(n: float) -> float:
    return round(n * 2) / 2


def _round_prob(n: float | None) -> float | None:
    if n is None:
        return None
    return round(float(n), 4)


def _load_calib() -> dict[str, Any]:
    if FEI_CALIB_PATH.exists():
        return json.loads(FEI_CALIB_PATH.read_text(encoding="utf-8"))
    legacy = DATA_DIR / "historical" / "cfb_sp_margin_calibration.json"
    if legacy.exists():
        raw = json.loads(legacy.read_text(encoding="utf-8"))
        return {
            "fei_to_margin": DEFAULT_FEI_TO_MARGIN,
            "total_base": raw.get("total_base", LEAGUE_AVG_PTS * 2),
            "total_fei_sum_slope": DEFAULT_TOTAL_FEI_SLOPE,
            "margin_sigma": raw.get("margin_sigma", DEFAULT_MARGIN_SIGMA),
            "hfa": DEFAULT_HFA,
        }
    return {
        "fei_to_margin": DEFAULT_FEI_TO_MARGIN,
        "total_base": LEAGUE_AVG_PTS * 2,
        "total_fei_sum_slope": DEFAULT_TOTAL_FEI_SLOPE,
        "margin_sigma": DEFAULT_MARGIN_SIGMA,
        "hfa": DEFAULT_HFA,
        "live_remainder_shrink": 0.75,
        "live_sim_blend_max": 0.45,
    }


def _home_win_from_margin(margin: float, sigma: float) -> float:
    s = sigma if sigma > 0 else DEFAULT_MARGIN_SIGMA
    z = margin / s
    return min(1 - 1e-6, max(1e-6, 0.5 * (1.0 + math.erf(z / math.sqrt(2)))))


def price_game_from_fei(
    home_name: str,
    away_name: str,
    *,
    neutral: bool = False,
    season: int | None = None,
    display_week: int | None = None,
    refresh: bool = True,
) -> dict[str, Any] | None:
    from .team_registry import resolve_canonical

    if refresh and season is not None:
        ensure_fei_ratings(season=int(season), week=display_week)

    home_name = resolve_canonical(home_name) or home_name
    away_name = resolve_canonical(away_name) or away_name
    home = lookup_fei_team(home_name, season=season, week=display_week)
    away = lookup_fei_team(away_name, season=season, week=display_week)
    if not home or not away:
        return None

    calib = _load_calib()
    fei_to_margin = float(calib.get("fei_to_margin", DEFAULT_FEI_TO_MARGIN))
    total_base = float(calib.get("total_base", LEAGUE_AVG_PTS * 2))
    total_slope = float(calib.get("total_fei_sum_slope", DEFAULT_TOTAL_FEI_SLOPE))
    margin_sigma = float(calib.get("margin_sigma", DEFAULT_MARGIN_SIGMA))
    hfa = 0.0 if neutral else float(calib.get("hfa", DEFAULT_HFA))

    home_fei = float(home["fei"])
    away_fei = float(away["fei"])
    fei_diff = home_fei - away_fei
    margin = fei_diff * fei_to_margin + hfa
    spread = _round_half(-margin)

    fei_sum = home_fei + away_fei
    total = total_base + fei_sum * total_slope
    total_line = _round_half(total)

    home_pts = max(3.0, (total + margin) / 2)
    away_pts = max(3.0, (total - margin) / 2)
    home_win = _home_win_from_margin(margin, margin_sigma)

    return {
        "spread": spread,
        "total": total_line,
        "home_score": int(round(home_pts)),
        "away_score": int(round(away_pts)),
        "margin": _round_half(margin),
        "home_lambda": home_pts,
        "away_lambda": away_pts,
        "model": "fei",
        "spread_market": {
            "line": spread,
            "home_cover_prob": _round_prob(0.5),
            "away_cover_prob": _round_prob(0.5),
        },
        "total_market": {"line": total_line},
        "moneyline": {
            "home_win_prob": _round_prob(home_win),
            "away_win_prob": _round_prob(1 - home_win),
        },
        "fei_home": home_fei,
        "fei_away": away_fei,
    }
