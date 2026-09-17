"""nfelo prop matchup — same bounded methodology as CFB SP+ props, nfelo EPA source."""
from __future__ import annotations

from typing import Any

from .nfelo_projections import price_game_from_nfelo
from .nfelo_ratings import (
    _def_epa_field,
    _league_median_def_epa,
    lookup_nfelo_def_rank,
    lookup_nfelo_team,
)
from .sp_prop_matchup import (
    PROP_DEF_ADJ_CAP,
    PROP_DEF_DISPLAY_CAP,
    PROP_FACTOR_MAX,
    PROP_FACTOR_MIN,
    PROP_SCRIPT_CAP,
    _prop_script_factor,
    prop_factor_key,
)

# Typical spread of nflelo defensive EPA/play — avoids dividing by ~0.02 league median.
EPA_DEF_STD = 0.08

MATCHUP_EPA_SCALE_PASS = 0.22
MATCHUP_EPA_SCALE_RUSH = 0.32
MATCHUP_EPA_SCALE_TD = 0.26
FACTOR_MIN = 0.58
FACTOR_MAX = 1.42


def _clamp_factor(f: float) -> float:
    return min(FACTOR_MAX, max(FACTOR_MIN, f))


def _off_epa(team: dict[str, Any] | None, prop_key: str) -> float:
    if not team:
        return 0.0
    pk = (prop_key or "").lower()
    if pk == "rush_yds":
        return float(team.get("off_epa_rush") or 0.0)
    if pk in ("pass_yds", "pass_yds_q1", "pass_tds", "pass_attempts", "pass_completions", "receptions", "rec_yds"):
        return float(team.get("off_epa_pass") or 0.0)
    return float(team.get("off_epa_play") or 0.0)


def _def_epa(team: dict[str, Any] | None, prop_key: str) -> float:
    if not team:
        return 0.0
    field = _def_epa_field(prop_key)
    return float(team.get(field) or team.get("def_epa_play") or 0.0)


def _pass_rate_from_epa(team: dict[str, Any] | None) -> float:
    if not team:
        return 0.5
    op = float(team.get("off_epa_pass") or 0.0)
    oru = float(team.get("off_epa_rush") or 0.0)
    total = abs(op) + abs(oru)
    if total <= 0.01:
        return 0.55
    return max(0.35, min(0.68, 0.42 + (op - oru) * 0.35))


def _projected_margin(
    home: str,
    away: str,
    team: str,
) -> float | None:
    priced = price_game_from_nfelo(home, away)
    if not priced:
        return None
    margin = priced.get("margin")
    if margin is None:
        return None
    from .team_registry import teams_match

    if teams_match(team, home):
        return float(margin)
    if teams_match(team, away):
        return -float(margin)
    return None


def defense_display_adjustment_pct(opp_name: str, prop_key: str, **_kwargs: Any) -> float | None:
    """Defense EPA vs league median — higher opp EPA allowed = softer defense = positive %."""
    opp = lookup_nfelo_team(opp_name)
    if not opp:
        return None
    pk = (prop_key or "").lower()
    opp_def = _def_epa(opp, pk)
    median = _league_median_def_epa(pk)
    sensitivity = {
        "pass_yds": 0.55,
        "pass_yds_q1": 0.5,
        "pass_attempts": 0.45,
        "pass_completions": 0.45,
        "rush_yds": 0.65,
        "rush_attempts": 0.6,
        "rec_yds": 0.55,
        "receptions": 0.55,
        "pass_tds": 0.45,
    }.get(pk, 0.55)
    delta = opp_def - median
    raw = (delta / EPA_DEF_STD) * 100.0 * sensitivity * 0.35
    return round(max(-PROP_DEF_DISPLAY_CAP, min(PROP_DEF_DISPLAY_CAP, raw)), 0)


def nfelo_matchup_factors(
    team_name: str,
    opp_name: str,
    *,
    home: str | None = None,
    away: str | None = None,
    **_kwargs: Any,
) -> dict[str, float]:
    team = lookup_nfelo_team(team_name)
    opp = lookup_nfelo_team(opp_name)
    if not team or not opp:
        return {"pass": 1.0, "rush": 1.0, "rec": 1.0, "td": 1.0, "matchupScore": 0.0}

    pass_score = float(team.get("off_epa_pass") or 0) - float(opp.get("def_epa_pass") or 0)
    rush_score = float(team.get("off_epa_rush") or 0) - float(opp.get("def_epa_rush") or 0)
    td_score = pass_score

    team_pass = _pass_rate_from_epa(team)
    pass_base = _clamp_factor(1.0 + pass_score / MATCHUP_EPA_SCALE_PASS)
    rush_base = _clamp_factor(1.0 + rush_score / MATCHUP_EPA_SCALE_RUSH)
    td_base = _clamp_factor(1.0 + td_score / MATCHUP_EPA_SCALE_TD)

    tendency_pass = 1.0 + max(-0.08, min(0.12, (team_pass - 0.5) * 0.35))
    tendency_rush = 1.0 - max(-0.06, min(0.10, (team_pass - 0.5) * 0.25))

    opp_def_rank = lookup_nfelo_def_rank(opp_name)
    margin = _projected_margin(home, away, team_name) if home and away else None

    script_pass = script_rush = 1.0
    if margin is not None:
        if margin < -3:
            trail = min(14.0, abs(margin))
            script_pass = 1.0 + trail * 0.015
            script_rush = 1.0 - trail * 0.012
        elif margin > 7:
            lead = min(21.0, margin)
            script_pass = 1.0 - lead * 0.012
            script_rush = 1.0 + lead * 0.006

    elite_cut = 1.0
    if opp_def_rank is not None and opp_def_rank <= 25:
        elite_cut = max(0.84, 1.0 - (26 - opp_def_rank) * 0.011)

    pass_factor = _clamp_factor(pass_base * tendency_pass * script_pass)
    rush_factor = _clamp_factor(rush_base * tendency_rush * script_rush * elite_cut)
    rec_factor = _clamp_factor(pass_base * tendency_pass * script_pass * 0.95)
    td_factor = _clamp_factor(td_base * (0.7 * script_pass + 0.3 * tendency_pass))

    return {
        "pass": pass_factor,
        "rush": rush_factor,
        "rec": rec_factor,
        "td": td_factor,
        "matchupScore": round(pass_score, 3),
        "oppSpDef": round(_def_epa(opp, "pass_yds"), 3),
        "oppSpDefRank": opp_def_rank,
        "teamSpOff": round(float(team.get("off_epa_pass") or 0), 3),
    }


def prop_matchup_factor(
    prop: str,
    team_name: str,
    opp_name: str,
    *,
    home: str | None = None,
    away: str | None = None,
    season: int | None = None,
    display_week: int | None = None,
) -> float:
    _ = season, display_week
    pk = (prop or "").lower()
    def_pct = defense_display_adjustment_pct(opp_name, pk)
    def_delta = 0.0
    if def_pct is not None:
        def_delta = max(-PROP_DEF_ADJ_CAP, min(PROP_DEF_ADJ_CAP, float(def_pct) / 100.0))
    def_factor = 1.0 + def_delta

    margin = _projected_margin(home, away, team_name) if home and away else None
    script_factor = _prop_script_factor(pk, margin)

    factors = nfelo_matchup_factors(team_name, opp_name, home=home, away=away)
    fkey = prop_factor_key(pk)
    raw = float(factors.get("td" if pk == "pass_tds" else fkey, 1.0))
    if pk == "pass_tds":
        raw = float(factors.get("td", raw))
    offense_nudge = 1.0 + (raw - 1.0) * 0.32

    combined = def_factor * script_factor * offense_nudge
    return max(PROP_FACTOR_MIN, min(PROP_FACTOR_MAX, combined))


def apply_nfelo_matchup_to_baseline(
    baseline: float | None,
    prop: str,
    team_name: str,
    opp_name: str,
    *,
    home: str | None = None,
    away: str | None = None,
    season: int | None = None,
    display_week: int | None = None,
) -> float | None:
    if baseline is None or baseline <= 0:
        return None
    factor = prop_matchup_factor(
        prop,
        team_name,
        opp_name,
        home=home,
        away=away,
        season=season,
        display_week=display_week,
    )
    return round(float(baseline) * float(factor), 1)
