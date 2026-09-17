"""Player prop projections — EWMA recent form + Bayesian shrink for sparse samples."""
from __future__ import annotations

import math
import statistics
from typing import Any

from .player_gamelog import athlete_id_for_player, fetch_gamelog_df_combined
from .prop_pricing import prop_key_from_row
from .prop_reprice import BASELINE_FIELDS, _baseline_on_team, find_baseline_by_name, resolve_prop_team
from .prop_matchup import apply_prop_matchup_to_baseline
from .team_registry import teams_match

# Max deviation from market line after matchup.
LINE_SHRINK_START = 0.14
LINE_SHRINK_MAX = 0.45
MARKET_BLEND = 0.08  # final anchor toward posted line

# Max swing from core rate after matchup adjustment.
CORE_SWING = 0.20

# EWMA — recent games weighted heavier (span ~4–5 games).
EWMA_ALPHA = 0.32
RECENT_LIMIT = 10

# Tukey IQR multiplier by prop — tighter for volume stats with spike risk.
OUTLIER_IQR_MULT: dict[str, float] = {
    "pass_yds": 1.75,
    "pass_yds_q1": 1.75,
    "pass_attempts": 1.6,
    "pass_completions": 1.6,
    "rush_yds": 1.5,
    "rush_attempts": 1.5,
    "rec_yds": 1.5,
    "receptions": 1.5,
    "pass_tds": 2.0,
}

# Equivalent games of prior belief when sample is thin.
PRIOR_STRENGTH: dict[str, float] = {
    "pass_yds": 5.5,
    "pass_yds_q1": 5.0,
    "pass_attempts": 5.0,
    "pass_completions": 5.0,
    "rush_yds": 4.5,
    "rush_attempts": 4.5,
    "rec_yds": 4.5,
    "receptions": 4.5,
    "pass_tds": 6.0,
}

# Meaningful production for sample counting (avoid counting mop-up snaps).
ACTIVE_THRESHOLDS: dict[str, float] = {
    "pass_yds": 80.0,
    "pass_yds_q1": 20.0,
    "pass_attempts": 5.0,
    "pass_completions": 3.0,
    "rush_yds": 12.0,
    "rush_attempts": 3.0,
    "rec_yds": 8.0,
    "receptions": 1.0,
    "pass_tds": 0.5,
}

# Role baselines when market line unavailable.
POSITION_PRIORS: dict[tuple[str, str], float] = {
    ("pass_yds", "QB"): 210.0,
    ("pass_yds", ""): 200.0,
    ("pass_yds_q1", "QB"): 58.0,
    ("pass_attempts", "QB"): 32.0,
    ("pass_completions", "QB"): 21.0,
    ("rush_yds", "RB"): 62.0,
    ("rush_yds", "QB"): 18.0,
    ("rush_yds", "FB"): 28.0,
    ("rush_yds", "HB"): 55.0,
    ("rush_attempts", "RB"): 14.0,
    ("rush_attempts", "QB"): 4.0,
    ("rec_yds", "WR"): 52.0,
    ("rec_yds", "TE"): 38.0,
    ("rec_yds", "RB"): 22.0,
    ("receptions", "WR"): 4.5,
    ("receptions", "TE"): 3.5,
    ("receptions", "RB"): 2.5,
    ("pass_tds", "QB"): 1.4,
}


def _ewma(values: list[float], *, alpha: float = EWMA_ALPHA) -> float | None:
    if not values:
        return None
    s = float(values[0])
    for x in values[1:]:
        s = alpha * float(x) + (1.0 - alpha) * s
    return s


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def _outlier_fences(values: list[float], prop_key: str) -> tuple[float, float] | None:
    """Tukey fences — returns (lo, hi) or None when sample too small."""
    if len(values) < 4:
        return None
    s = sorted(values)
    q1 = _percentile(s, 0.25)
    q3 = _percentile(s, 0.75)
    iqr = max(q3 - q1, 1.0)
    mult = OUTLIER_IQR_MULT.get((prop_key or "").lower(), 1.5)
    return q1 - mult * iqr, q3 + mult * iqr


def _robust_recent_values(values: list[float], prop_key: str) -> tuple[list[float], int]:
    """
    Drop extreme single-game spikes before rate estimation.
    Returns cleaned chronological series + outlier count.
    """
    if len(values) < 4:
        return values, 0

    fences = _outlier_fences(values, prop_key)
    if fences is None:
        return values, 0
    lo, hi = fences

    clean = [v for v in values if lo <= v <= hi]
    n_out = len(values) - len(clean)

    # Never discard more than 30% of games — winsorize instead.
    if n_out == 0:
        return values, 0
    if n_out / len(values) > 0.3:
        capped = [min(hi, max(lo, v)) for v in values]
        return capped, n_out

    if len(clean) < 3:
        capped = [min(hi, max(lo, v)) for v in values]
        return capped, n_out
    return clean, n_out


def _robust_recent_rate(values: list[float], prop_key: str) -> tuple[float | None, int]:
    """
    Outlier-safe recent rate: median anchor + EWMA on cleaned series.
    Single-game spikes (e.g. 255 rush yds) won't dominate the projection.
    """
    if not values:
        return None, 0
    clean, n_out = _robust_recent_values(values, prop_key)
    if not clean:
        return None, n_out

    med = float(statistics.median(clean))
    ewma = _ewma(clean)
    if ewma is None:
        return med, n_out

    if n_out > 0:
        # Spike games removed — trust median over EWMA.
        return 0.25 * ewma + 0.75 * med, n_out
    if len(clean) >= 6:
        return 0.45 * ewma + 0.55 * med, n_out
    return 0.55 * ewma + 0.45 * med, n_out


def _bayesian_shrink(estimate: float, n_eff: float, prior: float, strength: float) -> float:
    if not math.isfinite(estimate) or not math.isfinite(prior) or prior <= 0:
        return estimate
    n = max(0.0, float(n_eff))
    k = max(0.5, float(strength))
    w = n / (n + k)
    return w * estimate + (1.0 - w) * prior


def _position_prior(prop_key: str, position: str) -> float | None:
    pk = (prop_key or "").lower()
    pos = (position or "").upper()
    if (pk, pos) in POSITION_PRIORS:
        return POSITION_PRIORS[(pk, pos)]
    if (pk, "") in POSITION_PRIORS:
        return POSITION_PRIORS[(pk, "")]
    return None


def _market_prior(
    prop_key: str,
    position: str,
    line_f: float | None,
) -> float | None:
    if line_f is not None and math.isfinite(line_f) and line_f > 0:
        return float(line_f)
    return _position_prior(prop_key, position)


def _recent_form(
    player: str,
    team: str | None,
    prop_key: str,
    *,
    season: int | None = None,
    as_of_week: int | None = None,
    limit: int = RECENT_LIMIT,
) -> tuple[float | None, float, float, float | None]:
    """Robust recent rate, effective sample, active games, simple mean."""
    aid = athlete_id_for_player(player, team or None)
    if not aid:
        return None, 0.0, 0.0, None
    from .config import DEFAULT_YEAR

    yr = int(season) if season is not None else DEFAULT_YEAR
    df = fetch_gamelog_df_combined(aid, yr)
    if as_of_week is not None:
        from pricing_engine.pit import filter_gamelog_as_of

        df = filter_gamelog_as_of(df, yr, int(as_of_week))
    if df.empty:
        return None, 0.0, 0.0, None

    from .player_gamelog import gamelog_prop_series

    series = gamelog_prop_series(df, prop_key)
    if series is None:
        return None, 0.0, 0.0, None

    vals = [float(v) for v in series.tail(limit) if v is not None]
    vals = [v for v in vals if math.isfinite(v) and v >= 0]
    if not vals:
        return None, 0.0, 0.0, None

    thresh = ACTIVE_THRESHOLDS.get((prop_key or "").lower(), 1.0)
    n_active = float(sum(1 for v in vals if v >= thresh))
    recent_rate, n_outliers = _robust_recent_rate(vals, prop_key)
    mean_all = float(statistics.mean(vals))
    # Active games count full; inactive recent games add partial credit.
    n_eff = n_active + max(0.0, (len(vals) - n_active)) * 0.15
    if n_outliers > 0:
        n_eff = max(n_active, n_eff - n_outliers * 0.35)
    return recent_rate, n_eff, n_active, mean_all


def _season_rate(
    player: str,
    team: str | None,
    prop_key: str,
    *,
    as_of_week: int | None = None,
) -> tuple[float | None, float]:
    """Season per-game rate and games played."""
    field = BASELINE_FIELDS.get((prop_key or "").lower())
    if not field:
        return None, 0.0

    row = None
    if team:
        row = _baseline_on_team(player, team)
    if not row:
        row = find_baseline_by_name(player)

    if not row or row.get(field) is None:
        return None, 0.0
    try:
        val = float(row[field])
        gp = float(row.get("games") or 0)
    except (TypeError, ValueError):
        return None, 0.0
    if val < 0:
        return None, 0.0
    if as_of_week is not None:
        max_gp = max(0.0, float(as_of_week) - 1.0)
        if gp > max_gp + 0.5:
            return None, max_gp
        gp = min(gp, max_gp)
    return val, max(0.0, gp)


def _core_rate(
    player: str,
    team: str | None,
    prop_key: str,
    *,
    season: int | None = None,
    as_of_week: int | None = None,
    skip_gamelog: bool = False,
    line_f: float | None = None,
    position: str = "",
    line_as_prior: bool = True,
) -> float | None:
    """EWMA + season rate, shrunk toward market/position prior when sample is thin."""
    pk = (prop_key or "").lower()
    prior_line = line_f if line_as_prior else None
    prior = _market_prior(pk, position, prior_line)
    if (prior is None or prior <= 0) and not line_as_prior:
        prior = _position_prior(pk, position)

    season_rate, season_gp = _season_rate(player, team, pk, as_of_week=as_of_week)
    recent, n_eff, n_active, mean_all = (None, 0.0, 0.0, None) if skip_gamelog else _recent_form(
        player, team, pk, season=season, as_of_week=as_of_week,
    )

    # Blend recent robust rate with season rate when both exist.
    player_est: float | None = None
    if recent is not None and season_rate is not None and season_rate > 0:
        recent_w = min(0.75, 0.35 + n_active * 0.12)
        if season_gp >= 4:
            recent_w = min(recent_w, 0.5)
        player_est = recent_w * recent + (1.0 - recent_w) * season_rate
        n_eff = n_eff + min(3.0, season_gp * 0.35)
    elif recent is not None:
        player_est = recent
    elif season_rate is not None and season_rate > 0:
        player_est = season_rate
        n_eff = min(6.0, season_gp)

    if player_est is None:
        if prior is None or prior <= 0:
            return None
        return prior

    if prior is None or prior <= 0:
        prior = player_est

    # When season includes outlier-inflated per-game rate, cap toward robust recent.
    if recent is not None and season_rate is not None and season_rate > recent * 1.35:
        player_est = min(player_est, recent * 1.15 + season_rate * 0.15)

    # Zero-heavy game logs: don't let a single spike dominate with no supporting season.
    if n_active <= 1 and mean_all is not None and season_gp < 4:
        player_est = min(player_est, max(mean_all, prior * 0.45))

    strength = PRIOR_STRENGTH.get(pk, 4.0)
    if n_active <= 0:
        strength *= 2.0
    elif n_active <= 1:
        strength *= 1.55
    elif n_active <= 2:
        strength *= 1.2

    # Pull harder toward market when player rate sits above the posted line.
    if line_as_prior and line_f is not None and player_est > line_f * 1.06:
        overshoot = min(1.8, (player_est / max(line_f, 1.0) - 1.0) * 2.5)
        strength *= 1.0 + overshoot

    shrunk = _bayesian_shrink(player_est, n_eff, prior, strength)

    if line_as_prior and line_f is not None and shrunk > line_f * 1.28:
        shrunk = 0.78 * line_f + 0.22 * shrunk
    return shrunk


def _game_script_factor(prop_key: str, margin: float | None) -> float:
    """Favorite pass-volume haircut; trailing underdog slight bump."""
    if margin is None:
        return 1.0
    pk = (prop_key or "").lower()
    if pk in ("pass_yds", "pass_yds_q1", "pass_attempts", "pass_completions"):
        if margin > 7:
            lead = min(28.0, float(margin))
            return max(0.78, 1.0 - (lead - 7.0) * 0.016)
        if margin < -7:
            trail = min(21.0, abs(float(margin)))
            return min(1.08, 1.0 + trail * 0.006)
    if pk == "pass_tds" and margin > 10:
        return max(0.85, 1.0 - (min(28.0, margin) - 10.0) * 0.012)
    if pk in ("rush_yds", "rec_yds", "rush_attempts", "receptions"):
        if margin > 10:
            return min(1.12, 1.0 + (min(28.0, margin) - 10.0) * 0.008)
        if margin < -10:
            trail = min(28.0, abs(float(margin)))
            return max(0.82, 1.0 - (trail - 10.0) * 0.012)
    return 1.0


def _shrink_toward_line(adjusted: float, line: float, *, core: float | None) -> float:
    if line <= 0 or not math.isfinite(line):
        return adjusted
    rel = abs(adjusted - line) / max(line, 1.0)
    out = adjusted
    if rel > LINE_SHRINK_START:
        excess = rel - LINE_SHRINK_START
        weight = min(LINE_SHRINK_MAX, excess * 0.85)
        out = adjusted * (1.0 - weight) + line * weight
    if core is not None and core > 0:
        lo, hi = core * (1.0 - CORE_SWING), core * (1.0 + CORE_SWING)
        out = max(lo, min(hi, out))
    return out


def median_matchup_projection(
    row: dict[str, Any],
    *,
    skip_gamelog: bool = False,
    skip_starters: bool = True,
    line_as_prior: bool = True,
) -> float | None:
    """EWMA rate × opponent/pace/script, Bayesian-shrunk when sample is limited."""
    player = str(row.get("player") or "")
    prop_key = prop_key_from_row(row) or ""
    if not player or not prop_key:
        return None

    try:
        line_f = float(row.get("line"))
        if not math.isfinite(line_f) or line_f < 0:
            line_f = None
    except (TypeError, ValueError):
        line_f = None

    team = resolve_prop_team(row, skip_starters=skip_starters)
    home, away = row.get("home"), row.get("away")
    opp = None
    if team and home and away:
        if teams_match(team, home):
            opp = away
        elif teams_match(team, away):
            opp = home
    if not opp:
        opp = away or home

    try:
        season = int(row.get("year")) if row.get("year") is not None else None
    except (TypeError, ValueError):
        season = None
    try:
        week = int(row.get("week")) if row.get("week") is not None else None
    except (TypeError, ValueError):
        week = None

    position = str(row.get("position") or "")
    core = _core_rate(
        player,
        team,
        prop_key,
        season=season,
        as_of_week=week,
        skip_gamelog=skip_gamelog,
        line_f=line_f,
        position=position,
        line_as_prior=line_as_prior,
    )
    if core is None or core <= 0:
        return None

    adjusted = apply_prop_matchup_to_baseline(
        core,
        prop_key,
        str(team or ""),
        str(opp or ""),
        home=home,
        away=away,
        season=season,
        display_week=week,
    )
    if adjusted is None:
        adjusted = core

    if line_f is not None and line_as_prior:
        adjusted = _shrink_toward_line(adjusted, line_f, core=core)
        adjusted = (1.0 - MARKET_BLEND) * adjusted + MARKET_BLEND * line_f

    if prop_key == "pass_tds":
        return round(max(0.0, adjusted), 1)
    return round(max(0.0, adjusted), 1)
