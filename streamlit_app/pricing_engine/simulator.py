"""Vectorized Monte Carlo — all single-game markets priced in sync."""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from lib.odds_math import implied_to_american

from .constants import CFB_R_DISPERSION, DEFAULT_SIMS, NFL_R_DISPERSION, PERIOD_H1, PERIOD_Q1
from .live_adjust import adjust_lambdas_for_live, game_state_from_live
from .ratings import default_team_rates, price_base_game


def _round_half(n: float) -> float:
    return round(n * 2) / 2


def _sim_seed(*parts: object) -> int:
    blob = "|".join(str(p) for p in parts).encode()
    return int(hashlib.md5(blob).hexdigest()[:8], 16)


def _american(p: float) -> str | None:
    return implied_to_american(p)


def _sample_scores(
    n: int,
    home_mean: float,
    away_mean: float,
    *,
    r_disp: float,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Negative-binomial score draws (vectorized)."""
    hm = max(0.5, float(home_mean))
    am = max(0.5, float(away_mean))
    r = max(0.5, float(r_disp))
    p_home = r / (r + hm)
    p_away = r / (r + am)
    return rng.negative_binomial(r, p_home, size=n), rng.negative_binomial(r, p_away, size=n)


def _allocate_periods(home: np.ndarray, away: np.ndarray, rng: np.random.Generator) -> dict[str, np.ndarray]:
    n = len(home)
    q1_noise = 0.85 + rng.random(n) * 0.3
    h1_noise = 0.92 + rng.random(n) * 0.16
    q1f = np.clip(PERIOD_Q1 * q1_noise, 0.15, 0.35)
    h1f = np.clip(PERIOD_H1 * h1_noise, 0.45, 0.62)
    home_q1 = np.round(home * q1f).astype(int)
    away_q1 = np.round(away * q1f).astype(int)
    home_h1 = np.round(home * h1f).astype(int)
    away_h1 = np.round(away * h1f).astype(int)
    return {
        "home_q1": home_q1,
        "away_q1": away_q1,
        "home_h1": home_h1,
        "away_h1": away_h1,
        "home_h2": np.maximum(0, home - home_h1),
        "away_h2": np.maximum(0, away - away_h1),
    }


def _cover_prob(home: np.ndarray, away: np.ndarray, spread: float) -> float:
    need = int(np.ceil(abs(float(spread))))
    return float(np.mean(home - away >= need))


def _over_prob(vals: np.ndarray, line: float) -> float:
    return float(np.mean(vals > line))


def run_matchup_simulation(
    sport: str,
    home: str,
    away: str,
    *,
    season: int | None = None,
    week: int | None = None,
    market_spread: float | None = None,
    market_total: float | None = None,
    live_game: dict[str, Any] | None = None,
    n_sims: int = DEFAULT_SIMS,
) -> dict[str, Any]:
    # Ratings/lambdas from FEI (CFB) or ELO — never blend posted lines into score draws.
    base = price_base_game(
        sport,
        home,
        away,
        season=season,
        week=week,
    )
    if not base:
        return {"error": "no_ratings", "home": home, "away": away}

    home_l = float(base.get("home_lambda") or base.get("home_score") or 22)
    away_l = float(base.get("away_lambda") or base.get("away_score") or 22)
    model_spread = float((base.get("spread_market") or {}).get("line") or base.get("spread") or 0)
    model_total = float((base.get("total_market") or {}).get("line") or base.get("total") or home_l + away_l)
    spread_line = float(market_spread) if market_spread is not None else model_spread
    total_line = float(market_total) if market_total is not None else model_total

    live = game_state_from_live(live_game)
    h_sim, a_sim, fixed = adjust_lambdas_for_live(home_l, away_l, live)
    r_disp = NFL_R_DISPERSION if str(sport).lower() == "nfl" else CFB_R_DISPERSION

    seed = _sim_seed(
        sport, home, away, season, week, market_spread, market_total,
        live.get("state"), live.get("clock"), live.get("home_score"), live.get("away_score"),
    )
    rng = np.random.default_rng(seed)
    n = int(n_sims)

    if fixed and live.get("state") == "completed":
        home_pts = np.full(n, int(fixed["home"]))
        away_pts = np.full(n, int(fixed["away"]))
    elif fixed and live.get("state") == "live":
        rh, ra = _sample_scores(n, h_sim, a_sim, r_disp=r_disp, rng=rng)
        home_pts = int(fixed["home"]) + rh
        away_pts = int(fixed["away"]) + ra
    else:
        home_pts, away_pts = _sample_scores(n, h_sim, a_sim, r_disp=r_disp, rng=rng)

    periods = _allocate_periods(home_pts, away_pts, rng)
    totals = home_pts + away_pts
    home_ml = float(np.mean(home_pts > away_pts))

    home_rates, away_rates = default_team_rates(home_l, away_l)
    quality = 0.85 + rng.random(n)
    home_pass = np.round(home_rates["pass_yds"] * quality).astype(int)
    away_pass = np.round(away_rates["pass_yds"] * (2 - quality)).astype(int)
    home_rush = np.round(home_rates["rush_yds"] * quality).astype(int)
    away_rush = np.round(away_rates["rush_yds"] * (2 - quality)).astype(int)
    home_tds = np.maximum(0, np.round(home_rates["tds"] * quality).astype(int))
    away_tds = np.maximum(0, np.round(away_rates["tds"] * (2 - quality)).astype(int))

    q1_spread = _round_half(-(float(np.mean(periods["home_q1"])) - float(np.mean(periods["away_q1"]))))
    q1_total = _round_half(float(np.mean(periods["home_q1"])) + float(np.mean(periods["away_q1"])))
    h1_spread = _round_half(-(float(np.mean(periods["home_h1"])) - float(np.mean(periods["away_h1"]))))
    h1_total = _round_half(float(np.mean(periods["home_h1"])) + float(np.mean(periods["away_h1"])))

    home_pass_line = _round_half(float(np.mean(home_pass)))
    away_pass_line = _round_half(float(np.mean(away_pass)))
    home_rush_line = _round_half(float(np.mean(home_rush)))
    away_rush_line = _round_half(float(np.mean(away_rush)))
    home_td_line = _round_half(float(np.mean(home_tds)))
    away_td_line = _round_half(float(np.mean(away_tds)))

    markets: list[dict[str, Any]] = []

    def add(
        market: str,
        selection: str,
        line: float | None,
        prob: float,
        *,
        period: str = "FG",
        projection: float | str | None = None,
    ):
        markets.append(
            {
                "period": period,
                "market": market,
                "selection": selection,
                "line": line,
                "projection": projection,
                "prob": round(prob, 4),
                "price": _american(prob),
            }
        )

    margin_mean = round(float(np.mean(home_pts - away_pts)), 1)
    total_mean = round(float(np.mean(totals)), 1)
    home_mean = round(float(np.mean(home_pts)), 1)
    away_mean = round(float(np.mean(away_pts)), 1)

    hc = _cover_prob(home_pts, away_pts, spread_line)
    add("Spread", home, spread_line, hc, projection=margin_mean)
    add("Spread", away, -spread_line, 1 - hc, projection=margin_mean)
    ot = _over_prob(totals.astype(float), total_line)
    add("Total", "Over", total_line, ot, projection=total_mean)
    add("Total", "Under", total_line, 1 - ot, projection=total_mean)
    add("ML", home, None, home_ml, projection=f"{home_ml * 100:.1f}%")
    add("ML", away, None, 1 - home_ml, projection=f"{(1 - home_ml) * 100:.1f}%")
    htt = _round_half(home_l)
    att = _round_half(away_l)
    add("Team Total", home, htt, _over_prob(home_pts.astype(float), htt), projection=home_mean)
    add("Team Total", away, att, _over_prob(away_pts.astype(float), att), projection=away_mean)

    add("Spread", home, q1_spread, _cover_prob(periods["home_q1"], periods["away_q1"], q1_spread), period="1Q")
    add("Total", "Over", q1_total, _over_prob((periods["home_q1"] + periods["away_q1"]).astype(float), q1_total), period="1Q")
    add("Spread", home, h1_spread, _cover_prob(periods["home_h1"], periods["away_h1"], h1_spread), period="1H")
    add("Total", "Over", h1_total, _over_prob((periods["home_h1"] + periods["away_h1"]).astype(float), h1_total), period="1H")

    home_pass_mean = round(float(np.mean(home_pass)), 1)
    away_pass_mean = round(float(np.mean(away_pass)), 1)
    home_rush_mean = round(float(np.mean(home_rush)), 1)
    away_rush_mean = round(float(np.mean(away_rush)), 1)
    home_td_mean = round(float(np.mean(home_tds)), 1)
    away_td_mean = round(float(np.mean(away_tds)), 1)
    home_comp = np.round(home_pass / 11.0).astype(int)
    away_comp = np.round(away_pass / 11.0).astype(int)
    home_comp_line = _round_half(float(np.mean(home_comp)))
    away_comp_line = _round_half(float(np.mean(away_comp)))
    home_comp_mean = round(float(np.mean(home_comp)), 1)
    away_comp_mean = round(float(np.mean(away_comp)), 1)
    add("Pass Yds", home, home_pass_line, _over_prob(home_pass.astype(float), home_pass_line), projection=home_pass_mean)
    add("Pass Yds", away, away_pass_line, _over_prob(away_pass.astype(float), away_pass_line), projection=away_pass_mean)
    add("Comp", home, home_comp_line, _over_prob(home_comp.astype(float), home_comp_line), projection=home_comp_mean)
    add("Comp", away, away_comp_line, _over_prob(away_comp.astype(float), away_comp_line), projection=away_comp_mean)
    add("Rush Yds", home, home_rush_line, _over_prob(home_rush.astype(float), home_rush_line), projection=home_rush_mean)
    add("Rush Yds", away, away_rush_line, _over_prob(away_rush.astype(float), away_rush_line), projection=away_rush_mean)
    add("TDs", home, home_td_line, _over_prob(home_tds.astype(float), home_td_line), projection=home_td_mean)
    add("TDs", away, away_td_line, _over_prob(away_tds.astype(float), away_td_line), projection=away_td_mean)
    game_pass = _round_half(home_pass_line + away_pass_line)
    game_pass_mean = round(home_pass_mean + away_pass_mean, 1)
    add("Pass Yds", "Game", game_pass, _over_prob((home_pass + away_pass).astype(float), game_pass), projection=game_pass_mean)

    out = {
        "home": home,
        "away": away,
        "sport": sport,
        "model": base.get("model") or ("fei" if sport == "cfb" else "elo"),
        "live_state": live.get("state"),
        "spread": spread_line,
        "total": total_line,
        "model_spread": model_spread,
        "model_total": model_total,
        "home_lambda": home_l,
        "away_lambda": away_l,
        "n_sims": n,
        "markets": markets,
        "scoreboard": {
            "home_mean": round(float(np.mean(home_pts)), 1),
            "away_mean": round(float(np.mean(away_pts)), 1),
            "total_mean": round(float(np.mean(totals)), 1),
        },
    }

    if live.get("state") == "live":
        from pricing_engine.live_calibrate import calibrate_live_sim

        out = calibrate_live_sim(
            out,
            live=live,
            model_spread=model_spread,
            market_spread=spread_line,
            market_ml_home=live.get("market_ml_home"),
        )

    return out
