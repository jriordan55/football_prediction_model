"""Devigging methods + Kelly sizing for sharp prop boards."""
from __future__ import annotations

import math
from typing import Any

from .odds_math import american_to_implied, implied_to_american

DEVIG_METHODS: dict[str, str] = {
    "em": "Equal Margin",
    "mpto": "Proportional",
    "shin": "Shin",
    "or": "Odds Ratio",
    "log": "Logarithmic",
}

DEVIG_BOOK_PRESETS: dict[str, dict[str, Any]] = {
    "mkt_avg": {"label": "Market Avg", "weights": {}},
    "pinnacle": {"label": "Pinnacle", "weights": {"pinnacle": 1.0}},
    "draftkings": {"label": "DraftKings", "weights": {"draftkings": 1.0}},
    "fanduel": {"label": "FanDuel", "weights": {"fanduel": 1.0}},
    "fd_dk_50": {"label": "FD / DK 50-50", "weights": {"fanduel": 0.5, "draftkings": 0.5}},
}


def _clamp_prob(p: float) -> float:
    return min(1.0 - 1e-9, max(1e-9, p))


def devig_side_prob(
    price_a: Any,
    price_b: Any,
    *,
    method: str = "mpto",
) -> float | None:
    """Fair probability for side A from a two-way market."""
    imp_a = american_to_implied(price_a)
    imp_b = american_to_implied(price_b)
    if imp_a is None or imp_b is None:
        return None
    total = imp_a + imp_b
    if total <= 1.0:
        return _clamp_prob(imp_a)
    overround = total - 1.0
    m = (method or "mpto").lower()

    if m == "em":
        return _clamp_prob(imp_a - overround / 2)

    if m == "mpto":
        return _clamp_prob(imp_a / total)

    if m == "shin":
        # Shin z via bisection (standard two-way approximation).
        z = 0.0
        for _ in range(40):
            denom_a = 1.0 - z * (1.0 - imp_a)
            denom_b = 1.0 - z * (1.0 - imp_b)
            if denom_a <= 0 or denom_b <= 0:
                break
            fair_a = imp_a / denom_a
            fair_b = imp_b / denom_b
            s = fair_a + fair_b - 1.0
            if abs(s) < 1e-6:
                return _clamp_prob(fair_a)
            z = max(0.0, min(0.99, z + s * 0.35))
        return _clamp_prob(imp_a / total)

    if m == "or":
        # Odds-ratio (additive on odds scale).
        try:
            dec_a = 1.0 / imp_a
            dec_b = 1.0 / imp_b
            fair_dec_a = dec_a / (dec_a + dec_b) * (dec_a + dec_b - overround * dec_a * dec_b / (dec_a + dec_b))
            return _clamp_prob(1.0 / fair_dec_a)
        except (ZeroDivisionError, ValueError):
            return _clamp_prob(imp_a / total)

    if m == "log":
        # Logarithmic / power devig.
        try:
            la = math.log(imp_a)
            lb = math.log(imp_b)
            t = la + lb
            if t >= 0:
                return _clamp_prob(imp_a / total)
            k = math.log(1.0 + overround) / t if t else 1.0
            fa = math.exp(k * la)
            fb = math.exp(k * lb)
            return _clamp_prob(fa / (fa + fb))
        except (ValueError, ZeroDivisionError):
            return _clamp_prob(imp_a / total)

    return _clamp_prob(imp_a / total)


def weighted_fair_prob(
    quotes: list[dict[str, Any]],
    side: str,
    *,
    method: str = "mpto",
    weights: dict[str, float] | None = None,
) -> float | None:
    """Weighted median fair prob across books for over/under side."""
    side_l = (side or "").lower()
    probs: list[tuple[float, float]] = []
    for q in quotes:
        book = str(q.get("book_id") or q.get("book") or "").lower()
        w = 1.0
        if weights:
            w = float(weights.get(book, 0.0))
            if w <= 0:
                continue
        if side_l == "over":
            pa, pb = q.get("over_price"), q.get("under_price")
        else:
            pa, pb = q.get("under_price"), q.get("over_price")
        fair = devig_side_prob(pa, pb, method=method)
        if fair is not None:
            probs.append((fair, w))
    if not probs:
        return None
    if len(probs) == 1:
        return probs[0][0]
    total_w = sum(w for _, w in probs)
    if total_w <= 0:
        return None
    return sum(p * w for p, w in probs) / total_w


def fair_american_from_prob(p: float | None) -> str | None:
    return implied_to_american(p)


def kelly_fraction(model_prob: float | None, american_price: Any) -> float | None:
    if model_prob is None:
        return None
    imp = american_to_implied(american_price)
    if imp is None or imp <= 0:
        return None
    dec = 1.0 / imp
    b = dec - 1.0
    if b <= 0:
        return None
    q = 1.0 - model_prob
    k = (b * model_prob - q) / b
    return max(0.0, k)


def quarter_kelly_units(model_prob: float | None, american_price: Any, *, unit_bankroll: float = 100.0) -> float | None:
    k = kelly_fraction(model_prob, american_price)
    if k is None:
        return None
    return round(k * 0.25 * unit_bankroll, 2)
