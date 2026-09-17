"""Autohedge, Kelly, and TKO portfolio math (Abrams — *But How Much Did You Lose?*)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .odds_math import american_to_implied, devig_two_way, implied_to_american


def american_to_net_odds(american: float) -> float:
    """Net profit per $1 staked (Kelly b). +400 → 4.0, -350 → 100/350."""
    if american == 0:
        raise ValueError("American odds cannot be zero")
    if american > 0:
        return american / 100.0
    return 100.0 / abs(american)


def net_odds_to_decimal(net: float) -> float:
    return 1.0 + net


def kelly_fraction(p_win: float, american: float) -> float:
    """Kelly fraction; negative when the bet is -EV."""
    b = american_to_net_odds(american)
    q = 1.0 - p_win
    return (b * p_win - q) / b


def vig_free_prob(american_a: float, american_b: float) -> tuple[float, float]:
    """Devig a two-way market; return (p_a, p_b)."""
    p_a = devig_two_way(american_a, american_b)
    if p_a is None:
        raise ValueError("Could not devig market")
    return p_a, 1.0 - p_a


def balanced_hedge_fraction(f_value: float, odds_value: float, odds_hedge: float) -> float:
    """Hedge fraction h so payout is equal regardless of winner (balanced arb)."""
    b_v = american_to_net_odds(odds_value)
    b_h = american_to_net_odds(odds_hedge)
    return f_value * (1.0 + b_v) / (1.0 + b_h)


def growth_factors(
    f_value: float,
    h_hedge: float,
    *,
    b_value: float,
    b_hedge: float,
) -> tuple[float, float]:
    """Bankroll multipliers if value side wins vs hedge side wins."""
    g_value_win = 1.0 - h_hedge + f_value * b_value
    g_hedge_win = 1.0 - f_value + h_hedge * b_hedge
    return g_value_win, g_hedge_win


def expected_log_growth(
    f_value: float,
    h_hedge: float,
    *,
    p_value_win: float,
    b_value: float,
    b_hedge: float,
) -> float:
    g_v, g_h = growth_factors(f_value, h_hedge, b_value=b_value, b_hedge=b_hedge)
    if g_v <= 0 or g_h <= 0:
        return float("-inf")
    p_h = 1.0 - p_value_win
    return p_value_win * math.log(g_v) + p_h * math.log(g_h)


def optimal_hedge_fraction(
    f_value: float,
    *,
    p_value_win: float,
    odds_value: float,
    odds_hedge: float,
    h_max: float | None = None,
) -> float:
    """Maximize expected log growth over hedge size (autohedge)."""
    b_v = american_to_net_odds(odds_value)
    b_h = american_to_net_odds(odds_hedge)
    if h_max is None:
        h_max = max(balanced_hedge_fraction(f_value, odds_value, odds_hedge) * 1.25, f_value + 0.05)
    h_max = min(h_max, 1.0 - f_value - 1e-9)

    grid = np.linspace(0.0, h_max, 400)
    best_h, best_eg = 0.0, float("-inf")
    for h in grid:
        eg = expected_log_growth(
            f_value, float(h), p_value_win=p_value_win, b_value=b_v, b_hedge=b_h
        )
        if eg > best_eg:
            best_eg, best_h = eg, float(h)
    return best_h


def autohedge_fraction(
    f_value: float,
    *,
    p_value_win: float,
    odds_value: float,
    odds_hedge: float,
) -> float:
    """
    Book shortcut when value side is maxed: balanced arb hedge + Kelly on hedge side.
    h_auto = h_balanced + kelly_hedge (Kelly on hedge is often negative).
    """
    h_bal = balanced_hedge_fraction(f_value, odds_value, odds_hedge)
    p_hedge_win = 1.0 - p_value_win
    k_h = kelly_fraction(p_hedge_win, odds_hedge)
    return max(0.0, h_bal + k_h)


def guaranteed_arb_profit(
    bankroll: float,
    stake_value: float,
    *,
    odds_value: float,
    odds_hedge: float,
) -> tuple[float, float]:
    """Return (hedge_stake, guaranteed_profit) for balanced arb."""
    f_v = stake_value / bankroll
    h = balanced_hedge_fraction(f_v, odds_value, odds_hedge)
    b_v = american_to_net_odds(odds_value)
    b_h = american_to_net_odds(odds_hedge)
    g_v, _ = growth_factors(f_v, h, b_value=b_v, b_hedge=b_h)
    profit = bankroll * (g_v - 1.0)
    return h * bankroll, profit


def amount_at_risk(f_value: float, h_hedge: float, *, b_value: float, b_hedge: float) -> float:
    """Worst-case loss as fraction of bankroll."""
    g_v, g_h = growth_factors(f_value, h_hedge, b_value=b_value, b_hedge=b_hedge)
    return 1.0 - min(g_v, g_h)


def tko_plus_ev_fraction(p_plus_ev: float) -> float:
    """Rufus' demon / TKO: fraction on +EV side equals true win probability."""
    return min(1.0, max(0.0, p_plus_ev))


def tko_expected_log_growth(
    f: float,
    *,
    p: float,
    b_plus: float,
    b_minus: float,
) -> float:
    """EG when entire bankroll split: f on +EV side, (1-f) on opposite."""
    if f <= 0 or f >= 1:
        return float("-inf")
    g_plus = f * (1.0 + b_plus)
    g_minus = (1.0 - f) * (1.0 + b_minus)
    if g_plus <= 0 or g_minus <= 0:
        return float("-inf")
    return p * math.log(g_plus) + (1.0 - p) * math.log(g_minus)


def eg_hedge_curve(
    f_value: float,
    *,
    p_value_win: float,
    odds_value: float,
    odds_hedge: float,
    n_points: int = 80,
    h_max_frac: float = 1.25,
) -> tuple[np.ndarray, np.ndarray]:
    b_v = american_to_net_odds(odds_value)
    b_h = american_to_net_odds(odds_hedge)
    h_bal = balanced_hedge_fraction(f_value, odds_value, odds_hedge)
    h_max = min(h_bal * h_max_frac, 1.0 - f_value - 1e-9)
    hs = np.linspace(0.0, max(h_max, 1e-6), n_points)
    egs = [
        expected_log_growth(f_value, float(h), p_value_win=p_value_win, b_value=b_v, b_hedge=b_h)
        for h in hs
    ]
    return hs, np.array(egs)


@dataclass(frozen=True)
class StakingPlan:
    label: str
    stake_value: float
    stake_hedge: float
    expected_log_growth: float
    amount_at_risk: float
    guaranteed_profit: float | None = None


def compare_strategies(
    bankroll: float,
    *,
    p_value_win: float,
    odds_value: float,
    odds_hedge: float,
    value_stake_cap: float | None = None,
    kelly_fractions: Iterable[float] = (1.0, 0.5),
) -> list[StakingPlan]:
    """Compare value-only, arb, autohedge, and fractional Kelly plans."""
    b_v = american_to_net_odds(odds_value)
    b_h = american_to_net_odds(odds_hedge)
    plans: list[StakingPlan] = []

    k_full = max(0.0, kelly_fraction(p_value_win, odds_value))
    for frac in kelly_fractions:
        f = k_full * frac
        if value_stake_cap is not None:
            f = min(f, value_stake_cap / bankroll)
        stake_v = f * bankroll
        eg = expected_log_growth(f, 0.0, p_value_win=p_value_win, b_value=b_v, b_hedge=b_h)
        risk = amount_at_risk(f, 0.0, b_value=b_v, b_hedge=b_h)
        label = "Full Kelly value" if frac >= 0.99 else f"{frac:.0%} Kelly value"
        plans.append(
            StakingPlan(label, stake_v, 0.0, eg, risk * bankroll, None)
        )

    f_cap = (value_stake_cap / bankroll) if value_stake_cap is not None else k_full
    f_cap = max(f_cap, 1e-9)
    h_bal = balanced_hedge_fraction(f_cap, odds_value, odds_hedge)
    g_v, _ = growth_factors(f_cap, h_bal, b_value=b_v, b_hedge=b_h)
    plans.append(
        StakingPlan(
            "Balanced arb",
            f_cap * bankroll,
            h_bal * bankroll,
            expected_log_growth(f_cap, h_bal, p_value_win=p_value_win, b_value=b_v, b_hedge=b_h),
            0.0,
            (g_v - 1.0) * bankroll,
        )
    )

    h_auto = autohedge_fraction(f_cap, p_value_win=p_value_win, odds_value=odds_value, odds_hedge=odds_hedge)
    plans.append(
        StakingPlan(
            "Autohedge",
            f_cap * bankroll,
            h_auto * bankroll,
            expected_log_growth(f_cap, h_auto, p_value_win=p_value_win, b_value=b_v, b_hedge=b_h),
            amount_at_risk(f_cap, h_auto, b_value=b_v, b_hedge=b_h) * bankroll,
            None,
        )
    )

    h_opt = optimal_hedge_fraction(
        f_cap, p_value_win=p_value_win, odds_value=odds_value, odds_hedge=odds_hedge
    )
    if abs(h_opt - h_auto) > 0.001:
        plans.append(
            StakingPlan(
                "EG-optimal hedge",
                f_cap * bankroll,
                h_opt * bankroll,
                expected_log_growth(f_cap, h_opt, p_value_win=p_value_win, b_value=b_v, b_hedge=b_h),
                amount_at_risk(f_cap, h_opt, b_value=b_v, b_hedge=b_h) * bankroll,
                None,
            )
        )

    return plans


BOOK_BASKETBALL_PRESET = {
    "bankroll": 2500.0,
    "odds_value": 400,
    "odds_hedge": -350,
    "value_stake": 100.0,
    "sharp_a": 315,
    "sharp_b": -350,
}

RUfus_DEMON_PRESET = {
    "odds_plus": 300,
    "odds_minus": -105,
    "p_plus": 0.5,
}
