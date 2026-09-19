"""Calibrate live win % and expected margin using pregame ratings + market + score/time."""
from __future__ import annotations

import math
from typing import Any


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def _margin_from_win_prob(p: float, sigma: float) -> float:
    p = min(0.985, max(0.015, float(p)))
    s = sigma if sigma > 0 else 16.09
    lo, hi = -s * 3.5, s * 3.5
    for _ in range(48):
        mid = (lo + hi) / 2.0
        if _norm_cdf(mid / s) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _remain_frac(live: dict[str, Any]) -> float:
    rf = live.get("remain_frac")
    if rf is not None:
        try:
            return min(0.98, max(0.02, float(rf)))
        except (TypeError, ValueError):
            pass
    period = int(live.get("period") or 1)
    return min(0.98, max(0.02, 1.0 - ((period - 1) * 900 + 450) / 3600))


def _pregame_margin(
    *,
    model_spread: float | None,
    market_spread: float | None,
    model_home_wp: float | None,
    market_ml_home: float | None,
    margin_sigma: float,
) -> float:
    """Home expected final margin from FEI spread and/or market ML."""
    from lib.odds_math import american_to_implied

    margins: list[tuple[float, float]] = []

    for sp, w in ((model_spread, 0.55), (market_spread, 0.45)):
        if sp is None:
            continue
        try:
            margins.append((-float(sp), w))
        except (TypeError, ValueError):
            continue

    for prob, w in ((model_home_wp, 0.5),):
        if prob is None:
            continue
        try:
            p = float(prob)
            if p > 1.0:
                p /= 100.0
            margins.append((_margin_from_win_prob(p, margin_sigma * 0.55), w))
        except (TypeError, ValueError):
            continue

    if market_ml_home is not None:
        imp = american_to_implied(market_ml_home)
        if imp is not None:
            margins.append((_margin_from_win_prob(float(imp), margin_sigma * 0.45), 0.35))

    if not margins:
        return 0.0
    wt = sum(w for _, w in margins)
    return sum(m * w for m, w in margins) / wt if wt else 0.0


def _load_live_calib() -> dict[str, float]:
    from lib.fei_projections import _load_calib

    c = _load_calib()
    return {
        "remainder_shrink": float(c.get("live_remainder_shrink", 0.75)),
        "sim_blend_max": float(c.get("live_sim_blend_max", 0.45)),
        "margin_sigma": float(c.get("margin_sigma", 16.09)),
    }


def calibrate_live_sim(
    sim: dict[str, Any],
    *,
    live: dict[str, Any],
    model_spread: float | None = None,
    market_spread: float | None = None,
    market_ml_home: float | None = None,
    margin_sigma: float | None = None,
) -> dict[str, Any]:
    """Shrink live MC output toward historically calibrated score/time/market anchor."""
    if live.get("state") != "live":
        return sim

    calib = _load_live_calib()
    margin_sigma = float(margin_sigma or calib["margin_sigma"])
    remainder_shrink = calib["remainder_shrink"]
    sim_blend_max = calib["sim_blend_max"]

    sb = sim.get("scoreboard") or {}
    hm = float(sb.get("home_mean") or 0)
    am = float(sb.get("away_mean") or 0)
    sim_margin = hm - am

    home_score = int(live.get("home_score") or 0)
    away_score = int(live.get("away_score") or 0)
    score_margin = home_score - away_score
    remain = _remain_frac(live)

    model_wp = None
    for m in sim.get("markets") or []:
        if m.get("market") != "ML":
            continue
        if m.get("selection") == sim.get("home"):
            model_wp = m.get("prob")
            break

    pre_margin = _pregame_margin(
        model_spread=model_spread if model_spread is not None else sim.get("model_spread"),
        market_spread=market_spread if market_spread is not None else sim.get("spread"),
        model_home_wp=model_wp,
        market_ml_home=market_ml_home,
        margin_sigma=margin_sigma,
    )

    # Score on board + rating-anchored remainder (regresses toward pregame talent)
    remainder = pre_margin * remain * remainder_shrink
    cal_margin = score_margin + remainder

    # Trust score more as game progresses, but never fully ignore pregame anchor early
    elapsed = 1.0 - remain
    sim_weight = min(sim_blend_max, elapsed * (sim_blend_max + 0.1))
    final_margin = (1.0 - sim_weight) * cal_margin + sim_weight * sim_margin

    sigma_eff = margin_sigma * math.sqrt(remain + 0.12)
    home_wp = _norm_cdf(final_margin / sigma_eff)
    home_wp = min(0.99, max(0.01, home_wp))

    mid_total = float(sb.get("total_mean") or (hm + am))
    sb["home_mean"] = round((mid_total + final_margin) / 2, 1)
    sb["away_mean"] = round((mid_total - final_margin) / 2, 1)
    sb["total_mean"] = round(sb["home_mean"] + sb["away_mean"], 1)

    for m in sim.get("markets") or []:
        if m.get("market") != "ML":
            continue
        sel = str(m.get("selection") or "")
        if sel == sim.get("home"):
            m["prob"] = round(home_wp, 4)
        elif sel == sim.get("away"):
            m["prob"] = round(1 - home_wp, 4)

    sim["scoreboard"] = sb
    sim["calibrated_margin"] = round(final_margin, 1)
    sim["calibrated_home_wp"] = round(home_wp * 100, 1)
    return sim
