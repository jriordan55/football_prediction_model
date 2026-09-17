"""Score sampling — negative binomial, period splits, yard/TD draws."""
from __future__ import annotations

import math
import random
from typing import Any

from .constants import NFL_R_DISPERSION, PERIOD_H1, PERIOD_Q1, TD_CV, YARD_CV


def _round_half(n: float) -> float:
    return round(n * 2) / 2


def _round_pct(n: float) -> float:
    return round(n, 4)


def sample_negbin(mean: float, r: float, *, cap: int = 80) -> int:
    """Negative binomial via gamma-Poisson mixture."""
    mean = max(0.0, float(mean))
    if mean <= 0:
        return 0
    p = r / (r + mean)
    # gamma shape r, scale mean/r
    g = random.gammavariate(r, mean / r)
    lam = max(0.0, g)
    # Poisson with rate lam
    k = 0
    exp_l = math.exp(-lam)
    u = random.random()
    s = exp_l
    while u > s and k < cap:
        k += 1
        s += exp_l * (lam**k) / math.factorial(k)
    return min(cap, k)


def sample_poisson(lam: float, *, cap: int = 80) -> int:
    lam = max(0.0, float(lam))
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = L
    u = random.random()
    while u > p and k < cap:
        k += 1
        p += L * (lam**k) / math.factorial(k)
    return min(cap, k)


def sample_lognormal(mean: float, cv: float) -> float:
    mean = max(1.0, float(mean))
    sigma2 = math.log(1 + cv * cv)
    mu = math.log(mean) - sigma2 / 2
    z = random.gauss(0, 1)
    return math.exp(mu + math.sqrt(sigma2) * z)


def sample_gamma(mean: float, cv: float) -> float:
    mean = max(0.1, float(mean))
    k = 1 / (cv * cv)
    theta = mean / k
    return random.gammavariate(k, theta)


def allocate_period(
    home_pts: int,
    away_pts: int,
    *,
    q1_share: float = PERIOD_Q1,
    h1_share: float = PERIOD_H1,
) -> dict[str, int]:
    q1_noise = 0.85 + random.random() * 0.3
    h1_noise = 0.92 + random.random() * 0.16
    q1f = min(0.35, max(0.15, q1_share * q1_noise))
    h1f = min(0.62, max(0.45, h1_share * h1_noise))
    home_q1 = round(home_pts * q1f)
    away_q1 = round(away_pts * q1f)
    home_h1 = round(home_pts * h1f)
    away_h1 = round(away_pts * h1f)
    return {
        "home_q1": home_q1,
        "away_q1": away_q1,
        "home_h1": home_h1,
        "away_h1": away_h1,
        "home_h2": max(0, home_pts - home_h1),
        "away_h2": max(0, away_pts - away_h1),
    }


def cover_prob(home_vals: list[int], away_vals: list[int], spread_line: float) -> float:
    need = math.ceil(abs(float(spread_line)))
    covers = sum(1 for h, a in zip(home_vals, away_vals) if h - a >= need)
    return _round_pct(covers / len(home_vals)) if home_vals else 0.0


def over_prob(values: list[float], line: float) -> float:
    hits = sum(1 for v in values if v > line)
    return _round_pct(hits / len(values)) if values else 0.0


def dist_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0, "p10": 0, "p50": 0, "p90": 0}
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    pct = lambda p: s[min(n - 1, max(0, int(p * n)))]
    return {
        "mean": round(mean, 1),
        "p10": round(pct(0.1), 1),
        "p50": round(pct(0.5), 1),
        "p90": round(pct(0.9), 1),
    }


def simulate_score_pair(
    home_lambda: float,
    away_lambda: float,
    *,
    r_disp: float = NFL_R_DISPERSION,
    use_negbin: bool = True,
) -> tuple[int, int]:
    quality = 0.85 + random.random() * 0.3
    h_mean = home_lambda * quality
    a_mean = away_lambda * (2 - quality)
    if use_negbin:
        return sample_negbin(h_mean, r_disp), sample_negbin(a_mean, r_disp)
    return sample_poisson(h_mean), sample_poisson(a_mean)


def simulate_yard_tds(
    home_rates: dict[str, float] | None,
    away_rates: dict[str, float] | None,
    quality: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not home_rates or not away_rates:
        return out
    q2 = 2 - quality
    out["home_pass"] = round(sample_lognormal(home_rates["pass_yds"] * quality, YARD_CV))
    out["away_pass"] = round(sample_lognormal(away_rates["pass_yds"] * q2, YARD_CV))
    out["home_rush"] = round(sample_lognormal(home_rates["rush_yds"] * quality, YARD_CV))
    out["away_rush"] = round(sample_lognormal(away_rates["rush_yds"] * q2, YARD_CV))
    out["home_tds"] = round(sample_gamma(home_rates["tds"] * quality, TD_CV))
    out["away_tds"] = round(sample_gamma(away_rates["tds"] * q2, TD_CV))
    out["total_tds"] = out["home_tds"] + out["away_tds"]
    return out
