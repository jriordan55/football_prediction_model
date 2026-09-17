"""Live in-game projections — pregame lines + score/clock/pace + ESPN win prob."""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from .sp_projections import scores_from_spread_total

CFBFASTR = {
    "avg_total_pts": 55.2,
    "pass_share": 0.58,
    "yards_per_point": 13.55,
}


def _normalize_wp_pct(raw: float | None) -> float | None:
    if raw is None:
        return None
    v = float(raw)
    if v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))


def _elapsed_fraction(period: int, clock_seconds: int | None, *, overtime: bool = False) -> float:
    if overtime or int(period or 1) > 4:
        return 0.92
    p = max(1, min(4, int(period or 1)))
    sec_left_q = float(clock_seconds if clock_seconds is not None else 450)
    sec_left_game = (4 - p) * 900.0 + sec_left_q
    return max(0.04, min(0.97, 1.0 - sec_left_game / 3600.0))


def _logistic_wp(home_margin: float, seconds_left: float, pre_spread: float | None) -> float:
    if seconds_left <= 0:
        return 0.999 if home_margin > 0 else (0.001 if home_margin < 0 else 0.5)
    scale = max(4.0, math.sqrt(seconds_left / 60.0) * 1.15)
    prior_shift = -(float(pre_spread or 0)) * 0.35
    z = (home_margin + prior_shift) / scale
    return 1.0 / (1.0 + math.exp(-z))


def _pregame_team_scores(spread: float | None, total: float | None) -> tuple[float, float]:
    if spread is None or total is None:
        t = CFBFASTR["avg_total_pts"]
        return t / 2, t / 2
    away_i, home_i = scores_from_spread_total(float(spread), float(total))
    return float(away_i), float(home_i)


def _pace_blend_weight(elapsed: float) -> float:
    return min(0.92, max(0.08, elapsed * 1.35))


def _garbage_multipliers(
    home_score: int,
    away_score: int,
    period: int,
    clock_seconds: int | None = None,
) -> tuple[float, float]:
    """Per-team remaining-scoring multipliers in lopsided late games."""
    margin = home_score - away_score
    if abs(margin) < 14 or period < 3:
        return 1.0, 1.0

    sec_left = float(clock_seconds if clock_seconds is not None else 450)
    big = abs(margin) >= 21
    # Q4 with time on clock: leader still runs offense — don't over-dampen
    if period >= 4 and sec_left > 180:
        if margin > 0:
            return (0.90 if big else 0.94, 0.68 if big else 0.78)
        return (0.68 if big else 0.78, 0.90 if big else 0.94)

    if margin > 0:
        return (0.78 if big else 0.88, 0.62 if big else 0.75)
    return (0.62 if big else 0.75, 0.78 if big else 0.88)


def _proj_team_points(
    cur_pts: float,
    pre_pts: float,
    elapsed: float,
    remain: float,
    *,
    mult: float = 1.0,
) -> float:
    """Project final team score from current + blended scoring pace."""
    w = _pace_blend_weight(elapsed)
    if elapsed >= 0.07 and cur_pts > 0:
        obs_rate = cur_pts / elapsed
        rate = (1.0 - w) * pre_pts + w * obs_rate
    else:
        rate = pre_pts
    rate = max(0.0, min(90.0, rate))
    return cur_pts + rate * remain * mult


@lru_cache(maxsize=1)
def _valid_point_increments(max_inc: int = 70) -> frozenset[int]:
    """Points addable in one game stretch — sums of FG (3), TD (6), TD+XP (7)."""
    ok = {0}
    for s in range(1, max_inc + 1):
        if (s >= 3 and s - 3 in ok) or (s >= 6 and s - 6 in ok) or (s >= 7 and s - 7 in ok):
            ok.add(s)
    return frozenset(ok)


def reachable_team_score(cur: int, target: float, *, max_score: int = 80) -> int:
    """
    Snap to a football score reachable from cur by adding only 3/6/7-point chunks.
    (49 is valid in isolation but not reachable from 44 or 45.)
    """
    from .sp_projections import _valid_football_scores

    valid = _valid_football_scores(max_score)
    inc = _valid_point_increments(max_score)
    cur_i = max(0, int(cur))
    tgt = max(float(cur_i), float(target))
    candidates = sorted(s for s in valid if s >= cur_i and (s - cur_i) in inc)
    if not candidates:
        return cur_i
    at_or_above = [s for s in candidates if s + 1e-6 >= tgt]
    pool = at_or_above if at_or_above else candidates
    return int(min(pool, key=lambda s: (abs(s - tgt), s)))


def _reachable_pairs(
    proj_home: float,
    proj_away: float,
    home_score: int,
    away_score: int,
) -> tuple[int, int, int, int]:
    """Joint snap — both teams must reach finals via legal scoring increments."""
    from .sp_projections import _valid_football_scores

    valid = _valid_football_scores()
    inc = _valid_point_increments()
    hs, aw = int(home_score), int(away_score)
    ph = max(float(hs), float(proj_home))
    pa = max(float(aw), float(proj_away))

    home_c = [s for s in valid if s >= hs and (s - hs) in inc]
    away_c = [s for s in valid if s >= aw and (s - aw) in inc]
    if not home_c:
        home_c = [hs]
    if not away_c:
        away_c = [aw]

    best_h = best_a = None
    best_cost = float("inf")
    tgt_margin = ph - pa
    tgt_total = ph + pa
    for h in home_c:
        for a in away_c:
            if h == a:
                continue
            cost = (
                abs(h - ph)
                + abs(a - pa)
                + 0.25 * abs((h + a) - tgt_total)
                + 0.15 * abs((h - a) - tgt_margin)
            )
            if cost < best_cost:
                best_cost = cost
                best_h, best_a = h, a

    if best_h is None or best_a is None:
        best_h = reachable_team_score(hs, ph)
        best_a = reachable_team_score(aw, pa)
        if best_h == best_a:
            best_h = reachable_team_score(hs, ph + 3)

    return int(best_a), int(best_h), int(best_h) + int(best_a), int(best_h) - int(best_a)


def _snap_live_scores(
    proj_home: float,
    proj_away: float,
    home_score: int,
    away_score: int,
) -> tuple[int, int, int, int]:
    """Snap to realistic reachable football integers; never below current score."""
    return _reachable_pairs(proj_home, proj_away, home_score, away_score)


def _floor_yards(cur: float, proj: float) -> int:
    return int(round(max(cur, proj)))


def _pregame_yards_split(pre_pts: float) -> tuple[float, float]:
    total_yds = max(120.0, pre_pts * CFBFASTR["yards_per_point"])
    pass_yds = total_yds * CFBFASTR["pass_share"]
    return pass_yds, total_yds - pass_yds


def _proj_category_yards(
    cur: float,
    pre_rate: float,
    elapsed: float,
    remain: float,
    *,
    mult: float = 1.0,
) -> float:
    """Project one yard category (pass or rush) on its own pace curve."""
    cur_f = float(cur)
    w = _pace_blend_weight(elapsed)
    if elapsed >= 0.07 and cur_f > 0:
        obs_rate = cur_f / elapsed
        rate = (1.0 - w) * pre_rate + w * obs_rate
    else:
        rate = pre_rate
    rate = max(0.0, min(650.0, rate))
    return cur_f + rate * remain * 0.94 * mult


def _proj_yards(
    cur_yards: float,
    cur_pass: float,
    cur_rush: float,
    pre_pts: float,
    elapsed: float,
    remain: float,
    *,
    mult: float = 1.0,
) -> tuple[float, float, float]:
    pre_pass, pre_rush = _pregame_yards_split(pre_pts)
    proj_pass = _proj_category_yards(cur_pass, pre_pass, elapsed, remain, mult=mult)
    rush_anchor = max(0.0, cur_rush)
    proj_rush = _proj_category_yards(rush_anchor, pre_rush, elapsed, remain, mult=mult)
    if cur_rush < 0:
        proj_rush = max(cur_rush, proj_rush)
    pace_total = cur_yards
    if cur_yards > 0 and elapsed >= 0.07:
        pace_total = cur_yards + (cur_yards / elapsed) * remain * 0.90 * mult
    proj_total = max(cur_yards, pace_total, proj_pass + max(0.0, proj_rush))
    return proj_total, max(cur_pass, proj_pass), max(cur_rush, proj_rush)


def project_live_game(
    *,
    home_score: int,
    away_score: int,
    period: int,
    clock_seconds: int | None,
    pregame_spread: float | None,
    pregame_total: float | None,
    home_yards: dict[str, float] | None = None,
    away_yards: dict[str, float] | None = None,
    espn_home_wp: float | None = None,
    status_state: str = "in",
) -> dict[str, Any]:
    home_yards = home_yards or {}
    away_yards = away_yards or {}
    pre_away, pre_home = _pregame_team_scores(pregame_spread, pregame_total)

    if status_state == "post":
        hs, aw = int(home_score), int(away_score)
        home_pass = float(home_yards.get("pass") or 0)
        away_pass = float(away_yards.get("pass") or 0)
        home_rush = float(home_yards.get("rush") or 0)
        away_rush = float(away_yards.get("rush") or 0)
        return {
            "projHome": hs,
            "projAway": aw,
            "projTotal": hs + aw,
            "projSpread": hs - aw,
            "homeWinPct": 100.0 if hs > aw else (0.0 if aw > hs else 50.0),
            "awayWinPct": 100.0 if aw > hs else (0.0 if hs > aw else 50.0),
            "curHomeYards": round(home_yards.get("total") or 0),
            "curAwayYards": round(away_yards.get("total") or 0),
            "curHomePassYards": round(home_pass),
            "curAwayPassYards": round(away_pass),
            "curHomeRushYards": round(home_rush),
            "curAwayRushYards": round(away_rush),
            "projHomeYards": round(home_yards.get("total") or 0),
            "projAwayYards": round(away_yards.get("total") or 0),
            "projHomePassYards": round(home_pass),
            "projAwayPassYards": round(away_pass),
            "projHomeRushYards": round(home_rush),
            "projAwayRushYards": round(away_rush),
            "luckyTag": None,
        }

    elapsed = _elapsed_fraction(period, clock_seconds, overtime=period > 4)
    remain = 1.0 - elapsed
    sec_left = max(0.0, (4 - min(4, period)) * 900.0 + float(clock_seconds or 450))
    cur_margin = float(home_score - away_score)

    home_mult, away_mult = _garbage_multipliers(
        home_score, away_score, period, clock_seconds
    )
    proj_home = _proj_team_points(float(home_score), pre_home, elapsed, remain, mult=home_mult)
    proj_away = _proj_team_points(float(away_score), pre_away, elapsed, remain, mult=away_mult)

    espn_home = _normalize_wp_pct(espn_home_wp)
    model_wp = _logistic_wp(cur_margin, sec_left, pregame_spread) * 100.0
    if espn_home is not None:
        home_wp_pct = 0.72 * espn_home + 0.28 * model_wp
    else:
        home_wp_pct = model_wp
    home_wp_pct = max(1.0, min(99.0, home_wp_pct))
    away_wp_pct = 100.0 - home_wp_pct

    home_ty = float(home_yards.get("total") or 0)
    away_ty = float(away_yards.get("total") or 0)
    home_pass = float(home_yards.get("pass") or 0)
    away_pass = float(away_yards.get("pass") or 0)
    home_rush = float(home_yards.get("rush") or 0)
    away_rush = float(away_yards.get("rush") or 0)

    proj_home_ty, proj_home_pass, proj_home_rush = _proj_yards(
        home_ty, home_pass, home_rush, pre_home, elapsed, remain, mult=home_mult
    )
    proj_away_ty, proj_away_pass, proj_away_rush = _proj_yards(
        away_ty, away_pass, away_rush, pre_away, elapsed, remain, mult=away_mult
    )

    away_i, home_i, proj_total_i, proj_spread_i = _snap_live_scores(
        proj_home, proj_away, home_score, away_score
    )

    exp_home_pts = pre_home * elapsed + cur_margin * _pace_blend_weight(elapsed) * 0.5
    lucky = round(home_score - exp_home_pts, 1)
    lucky_tag = f"{lucky:+.1f} lucky" if abs(lucky) >= 1.0 and status_state == "in" else None

    return {
        "projHome": home_i,
        "projAway": away_i,
        "projTotal": proj_total_i,
        "projSpread": proj_spread_i,
        "homeWinPct": round(home_wp_pct, 1),
        "awayWinPct": round(away_wp_pct, 1),
        "curHomeYards": round(home_ty),
        "curAwayYards": round(away_ty),
        "curHomePassYards": round(home_pass),
        "curAwayPassYards": round(away_pass),
        "curHomeRushYards": round(home_rush),
        "curAwayRushYards": round(away_rush),
        "projHomeYards": _floor_yards(home_ty, proj_home_ty),
        "projAwayYards": _floor_yards(away_ty, proj_away_ty),
        "projHomePassYards": _floor_yards(home_pass, proj_home_pass),
        "projAwayPassYards": _floor_yards(away_pass, proj_away_pass),
        "projHomeRushYards": _floor_yards(home_rush, proj_home_rush),
        "projAwayRushYards": _floor_yards(away_rush, proj_away_rush),
        "luckyTag": lucky_tag,
        "elapsedPct": round(elapsed * 100, 1),
    }
