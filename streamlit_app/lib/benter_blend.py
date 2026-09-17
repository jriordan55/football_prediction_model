"""Benter-style precision-weighted market/model blend (mirrors puntandrally/benter_blend.js)."""
from __future__ import annotations

import math
from typing import Any

from .odds_math import american_to_implied

MARKET_PRECISION_PER_BOOK = 120
MODEL_PRECISION_PER_SIM = 0.04
MIN_MODEL_PRECISION = 25
MIN_MARKET_PRECISION = 40
MIN_PLAY_ROI = 0.1

PLAY_METHODOLOGY = (
    "Bill Connelly SP+ with walk-forward calibrated ML · "
    "Benter prior = Pinnacle fair when available, else multi-book consensus · "
    "PLAY = ≥10% expected ROI (Benter blend vs posted book) AND "
    "(model agrees with Benter prior OR Pinnacle fair)."
)


def clamp_prob(p: float) -> float:
    return min(1 - 1e-6, max(1e-6, float(p)))


def round_prob(p: float | None) -> float | None:
    if p is None:
        return None
    return round(float(p), 3)


def binomial_precision(prob: float, n: float) -> float:
    p = clamp_prob(prob)
    v = p * (1 - p)
    if v <= 0 or not math.isfinite(n) or n <= 0:
        return MIN_MODEL_PRECISION
    return min(5000.0, max(MIN_MODEL_PRECISION, n / v))


def market_precision(book_count: int = 1, extra: int = 0) -> float:
    n = max(1, int(book_count)) + extra
    return max(MIN_MARKET_PRECISION, n * MARKET_PRECISION_PER_BOOK)


def model_precision_from_sims(sim_prob: float, sim_count: int) -> float:
    return binomial_precision(sim_prob, sim_count) * MODEL_PRECISION_PER_SIM + MIN_MODEL_PRECISION


def bayesian_precision_blend(
    prior_prob: float,
    model_prob: float,
    *,
    market_prec: float | None = None,
    model_prec: float | None = None,
) -> dict[str, Any]:
    prior = clamp_prob(prior_prob)
    obs = clamp_prob(model_prob)
    tm = market_prec if market_prec is not None else MIN_MARKET_PRECISION
    tl = model_prec if model_prec is not None else MIN_MODEL_PRECISION
    blended = (tm * prior + tl * obs) / (tm + tl)
    return {
        "prior": round_prob(prior),
        "posterior": round_prob(obs),
        "blended": round_prob(blended),
        "marketWeight": round_prob(tm / (tm + tl)),
        "modelWeight": round_prob(tl / (tm + tl)),
        "marketPrecision": round(tm),
        "modelPrecision": round(tl),
    }


def directions_agree(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return (a - 0.5) * (b - 0.5) > 0


def ev_per_unit(cover_prob: float, american_price: Any) -> dict[str, float] | None:
    implied = american_to_implied(american_price)
    if implied is None:
        return None
    try:
        n = float(str(american_price).replace("+", "").replace("−", "-"))
    except (TypeError, ValueError):
        return None
    decimal = 1 + n / 100 if n > 0 else 1 + 100 / abs(n)
    if not math.isfinite(decimal):
        return None
    return {
        "edge": cover_prob - implied,
        "ev": cover_prob * decimal - 1,
        "implied": implied,
        "decimal": decimal,
    }


def evaluate_play(
    *,
    prior: float | None,
    model_prob: float | None,
    blended: float | None = None,
    implied: float | None = None,
    price: Any = None,
    pin_fair: float | None = None,
    min_roi: float = MIN_PLAY_ROI,
) -> dict[str, Any]:
    effective = blended if blended is not None else model_prob if model_prob is not None else prior
    edge = (effective - implied) if effective is not None and implied is not None else None
    pricing = ev_per_unit(effective, price) if effective is not None and price is not None else None
    roi = pricing["ev"] if pricing else None
    benter_agrees = directions_agree(prior, model_prob)
    pinnacle_agrees = pin_fair is not None and directions_agree(pin_fair, model_prob)
    play = roi is not None and roi >= min_roi and (benter_agrees or pinnacle_agrees)
    return {
        "edge": edge,
        "roi": roi,
        "play": play,
        "benterAgrees": benter_agrees,
        "pinnacleAgrees": pinnacle_agrees,
        "effectiveBlend": effective,
    }


def analyze_prop_benter(
    prior_prob: float | None,
    model_prob: float | None,
    *,
    book_count: int = 1,
    sim_count: int = 500,
) -> dict[str, Any] | None:
    if prior_prob is None or model_prob is None:
        return None
    return bayesian_precision_blend(
        prior_prob,
        model_prob,
        market_prec=market_precision(book_count),
        model_prec=model_precision_from_sims(model_prob, sim_count),
    )


def ml_blend(prior: float | None, model_prob: float | None, sim_count: int = 500, book_count: int = 3) -> float | None:
    if prior is None or model_prob is None:
        return None
    blend = bayesian_precision_blend(
        prior,
        model_prob,
        market_prec=market_precision(book_count),
        model_prec=model_precision_from_sims(model_prob, sim_count),
    )
    return blend.get("blended")


def fill_market_metrics(
    *,
    prior: float | None,
    model_prob: float | None,
    blended: float | None,
    implied: float | None,
    price: Any,
    pin_fair: float | None = None,
) -> dict[str, Any]:
    play = evaluate_play(
        prior=prior,
        model_prob=model_prob,
        blended=blended,
        implied=implied,
        price=price,
        pin_fair=pin_fair,
    )
    return {
        "prior": round_prob(prior),
        "blended": round_prob(blended if blended is not None else prior),
        "implied": round_prob(implied),
        "edge": round_prob(play["edge"]),
        "roi": round_prob(play["roi"]),
        "play": play["play"],
        "benterAgrees": play["benterAgrees"],
        "pinnacleAgrees": play["pinnacleAgrees"],
    }
