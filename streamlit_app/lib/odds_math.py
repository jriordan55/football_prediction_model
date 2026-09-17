"""Odds math — devig, consensus, implied probability, edges."""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd


def american_to_implied(price: Any) -> float | None:
    if price is None or price == "":
        return None
    s = str(price).replace("−", "-").replace("+", "").strip()
    try:
        n = float(s)
    except ValueError:
        return None
    if n == 0:
        return None
    if n > 0:
        return 100.0 / (n + 100.0)
    return abs(n) / (abs(n) + 100.0)


def implied_to_american(p: float | None) -> str | None:
    if p is None or not (0 < p < 1):
        return None
    if p >= 0.5:
        n = -100 * p / (1 - p)
    else:
        n = 100 * (1 - p) / p
    n = round(n)
    return f"+{n}" if n > 0 else str(n)


def devig_two_way(price_a: Any, price_b: Any) -> float | None:
    ia = american_to_implied(price_a)
    ib = american_to_implied(price_b)
    if ia is None or ib is None:
        return None
    total = ia + ib
    if total <= 0:
        return None
    return ia / total


def implied_prob_points(open_price: Any, close_price: Any) -> float | None:
    o = american_to_implied(open_price)
    c = american_to_implied(close_price)
    if o is None or c is None:
        return None
    return round((c - o) * 100, 1)


def closing_line_value_pct(taken_price: Any, close_price: Any) -> float | None:
    """Total CLV % — (closing implied / taken implied - 1) × 100."""
    taken = american_to_implied(taken_price)
    close = american_to_implied(close_price)
    if taken is None or close is None or taken <= 0:
        return None
    return round((close / taken - 1.0) * 100.0, 2)


def format_clv_pct(val: float | None) -> str:
    if val is None:
        return "—"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def median_line(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.median(values))


def build_consensus(df: pd.DataFrame, line_col: str = "line", price_col: str = "price") -> dict:
    if df.empty:
        return {}
    lines = pd.to_numeric(df[line_col], errors="coerce").dropna()
    out: dict[str, Any] = {"books": len(df)}
    if len(lines):
        out["line"] = round(float(lines.median()) * 2) / 2
    fair_probs = []
    for _, row in df.iterrows():
        # single-side fair from paired rows handled upstream
        imp = american_to_implied(row.get(price_col))
        if imp is not None:
            fair_probs.append(imp)
    if fair_probs:
        out["implied_median"] = float(np.median(fair_probs))
    return out


def edge_points_spread(model_line: float, market_line: float) -> float:
    return round(model_line - market_line, 1)


def _prob_per_point(market_key: str) -> float:
    """Rough fair-probability shift per full point of line movement (CFB)."""
    mk = str(market_key or "").lower()
    if re.search(r"_q[1-4]|_h1|_h2", mk):
        return 0.032
    return 0.025


def line_points_bettor_delta(
    side_key: str,
    market_key: str,
    ref_line: float,
    target_line: float,
) -> float:
    """Signed point edge for the bettor vs the reference line (+ = easier)."""
    sk = str(side_key or "").lower()
    mk = str(market_key or "").lower()
    delta = float(target_line) - float(ref_line)

    if mk.startswith("spreads") or sk in {"home", "away"}:
        return delta

    if sk in {"over"} or sk.endswith("_over"):
        return float(ref_line) - float(target_line)

    if sk in {"under"} or sk.endswith("_under"):
        return float(target_line) - float(ref_line)

    return delta


def adjust_fair_prob_for_line(
    fair_p: float,
    *,
    side_key: str,
    market_key: str,
    ref_line: float | None,
    target_line: float | None,
) -> float:
    if ref_line is None or target_line is None:
        return fair_p
    try:
        pts = line_points_bettor_delta(side_key, market_key, float(ref_line), float(target_line))
    except (TypeError, ValueError):
        return fair_p
    if abs(pts) < 1e-9:
        return fair_p
    shifted = fair_p + pts * _prob_per_point(market_key)
    return min(1.0 - 1e-9, max(1e-9, shifted))


def line_diff_better(side_key: str, candidate: float, reference: float) -> float:
    """Positive when candidate line is better for the bettor than reference."""
    try:
        c, r = float(candidate), float(reference)
    except (TypeError, ValueError):
        return 0.0
    sk = str(side_key or "").lower()
    if sk in {"home", "away"}:
        return c - r
    if sk in {"over"} or sk.endswith("_over"):
        return r - c
    if sk in {"under"} or sk.endswith("_under"):
        return c - r
    return c - r


def ev_pct(model_prob: float, american_price: Any) -> float | None:
    imp = american_to_implied(american_price)
    if imp is None or model_prob is None:
        return None
    dec = 1 / imp if imp else None
    if not dec:
        return None
    return round((model_prob * dec - 1) * 100, 1)


def grade_line_result(side: str, line: float | None, actual_margin: float, actual_total: float) -> str:
    """Return HIT / MISS / PUSH for spread or total."""
    side_l = (side or "").lower()
    if "spread" in side_l or side_l in {"home", "away", "favorite", "dog"}:
        if line is None:
            return "—"
        diff = actual_margin - line  # home margin vs spread (home perspective)
        if abs(diff) < 0.01:
            return "PUSH"
        return "HIT" if diff > 0 else "MISS"
    if "over" in side_l:
        if line is None:
            return "—"
        if abs(actual_total - line) < 0.01:
            return "PUSH"
        return "HIT" if actual_total > line else "MISS"
    if "under" in side_l:
        if line is None:
            return "—"
        if abs(actual_total - line) < 0.01:
            return "PUSH"
        return "HIT" if actual_total < line else "MISS"
    return "—"
