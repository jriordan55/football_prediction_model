"""SP+ prop matchup with pace, tendencies, and game-script caps."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .nfl_projections import price_game
from .sp_projections import _ensure_sp_loaded, _load_sp_index, lookup_sp_team

MATCHUP_SP_SCALE_PASS = 12.0
MATCHUP_SP_SCALE_RUSH = 22.0
MATCHUP_SP_SCALE_TD = 16.0
FACTOR_MIN = 0.58
FACTOR_MAX = 1.42

# Prop-specific caps — full SP+ game model is too hot for player yards.
PROP_FACTOR_MIN = 0.88
PROP_FACTOR_MAX = 1.26
PROP_DEF_ADJ_CAP = 0.24
PROP_DEF_DISPLAY_CAP = round(PROP_DEF_ADJ_CAP * 100.0, 0)  # match applied prop cap in UI
PROP_SCRIPT_CAP = 0.10  # max ±10% from projected game script


def _clamp_factor(f: float) -> float:
    return min(FACTOR_MAX, max(FACTOR_MIN, f))


def _sp_rating(team: dict[str, Any] | None, side: str) -> float:
    if not team:
        return 0.0
    if side == "off":
        return float(team.get("spOff") if team.get("spOff") is not None else (team.get("sp") or 0) * 0.4)
    return float(team.get("spDef") if team.get("spDef") is not None else -(team.get("sp") or 0) * 0.4)


def _resolve_sp_context(
    season: int | None,
    display_week: int | None,
) -> tuple[int | None, int | None]:
    from .config import DEFAULT_WEEK, DEFAULT_YEAR

    yr = int(season) if season is not None else DEFAULT_YEAR
    wk = int(display_week) if display_week is not None else DEFAULT_WEEK
    return yr, wk


@lru_cache(maxsize=4)
def _sp_def_rank_map(season: int, display_week: int, mtime_ns: int) -> dict[str, int]:
    """Rank teams by current SP+ spDef — lower spDef = better defense = rank 1."""
    from .team_registry import normalize_team_key, resolve_canonical

    teams = list(_load_sp_index(season, display_week).values())
    ordered = sorted(teams, key=lambda t: float(t.get("spDef") or 99.0))
    ranks: dict[str, int] = {}
    for i, row in enumerate(ordered):
        rank = i + 1
        for label in (row.get("team"), row.get("key")):
            if not label:
                continue
            nk = normalize_team_key(str(label))
            if nk and nk not in ranks:
                ranks[nk] = rank
            canon = resolve_canonical(str(label))
            if canon:
                cn = normalize_team_key(canon)
                if cn and cn not in ranks:
                    ranks[cn] = rank
    return ranks


def _lookup_sp_def_rank(
    team_name: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
) -> int | None:
    from .sp_projections import _resolve_sp_index_path
    from .team_registry import normalize_team_key, resolve_canonical

    yr, wk = _resolve_sp_context(season, display_week)
    _ensure_sp_loaded(yr, wk)
    path = _resolve_sp_index_path(yr, wk)
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    ranks = _sp_def_rank_map(yr, wk, mtime_ns)
    for cand in (team_name, resolve_canonical(team_name)):
        if not cand:
            continue
        hit = ranks.get(normalize_team_key(str(cand)))
        if hit:
            return hit
    return None


@lru_cache(maxsize=4)
def _league_median_sp_def(season: int, display_week: int, mtime_ns: int) -> float:
    teams = list(_load_sp_index(season, display_week).values())
    vals = sorted(float(t.get("spDef") or 99.0) for t in teams)
    if not vals:
        return 20.0
    return vals[len(vals) // 2]


def defense_display_adjustment_pct(
    opp_name: str,
    prop_key: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
) -> float | None:
    """Defense-only effect vs league median — lower spDef (better D) => negative %."""
    yr, wk = _resolve_sp_context(season, display_week)
    _ensure_sp_loaded(yr, wk)
    opp = lookup_sp_team(opp_name, season=yr, display_week=wk)
    if not opp or opp.get("spDef") is None:
        return None

    from .sp_projections import _resolve_sp_index_path

    path = _resolve_sp_index_path(yr, wk)
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    median_def = _league_median_sp_def(yr, wk, mtime_ns)
    opp_def = float(opp["spDef"])

    pk = (prop_key or "").lower()
    sensitivity = {"pass_yds": 0.55, "rush_yds": 0.65, "rec_yds": 0.55, "pass_tds": 0.45}.get(pk, 0.55)
    # Higher opp spDef = worse defense = easier stat environment.
    delta = opp_def - median_def
    raw = (delta / max(median_def, 1.0)) * 100.0 * sensitivity
    return round(max(-PROP_DEF_DISPLAY_CAP, min(PROP_DEF_DISPLAY_CAP, raw)), 0)


def _pass_rate_from_sp(team_off: float, team_sp: float) -> float:
    """Pass tendency proxy from SP+ offense split when SDVS unavailable."""
    if team_sp == 0:
        return 0.5
    ratio = team_off / max(abs(team_sp), 1.0)
    return max(0.35, min(0.68, 0.42 + ratio * 0.08))


def _projected_margin(
    home: str,
    away: str,
    team: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
) -> float | None:
    yr, wk = _resolve_sp_context(season, display_week)
    priced = price_game(home, away, season=yr, display_week=wk)
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


def sp_matchup_factors(
    team_name: str,
    opp_name: str,
    *,
    home: str | None = None,
    away: str | None = None,
    season: int | None = None,
    display_week: int | None = None,
) -> dict[str, float]:
    yr, wk = _resolve_sp_context(season, display_week)
    _ensure_sp_loaded(yr, wk)

    team = lookup_sp_team(team_name, season=yr, display_week=wk)
    opp = lookup_sp_team(opp_name, season=yr, display_week=wk)
    if not team or not opp:
        return {"pass": 1.0, "rush": 1.0, "rec": 1.0, "td": 1.0, "matchupScore": 0.0}

    team_off = _sp_rating(team, "off")
    opp_def = _sp_rating(opp, "def")
    # Lower opp spDef = better defense; net edge is offense minus opponent defense.
    matchup_score = team_off - opp_def

    team_sp = float(team.get("sp") or 0)
    team_pass = _pass_rate_from_sp(team_off, team_sp)

    pass_base = _clamp_factor(1.0 + matchup_score / MATCHUP_SP_SCALE_PASS)
    rush_base = _clamp_factor(1.0 + matchup_score / MATCHUP_SP_SCALE_RUSH)
    td_base = _clamp_factor(1.0 + matchup_score / MATCHUP_SP_SCALE_TD)

    tendency_pass = 1.0 + max(-0.08, min(0.12, (team_pass - 0.5) * 0.35))
    tendency_rush = 1.0 - max(-0.06, min(0.10, (team_pass - 0.5) * 0.25))

    opp_def_rank = _lookup_sp_def_rank(opp_name, season=yr, display_week=wk)

    margin = None
    if home and away:
        margin = _projected_margin(home, away, team_name, season=yr, display_week=wk)

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
        "matchupScore": round(matchup_score, 2),
        "oppSpDef": round(opp_def, 1),
        "oppSpDefRank": opp_def_rank,
        "teamSpOff": round(team_off, 1),
    }


def prop_factor_key(prop: str) -> str:
    pk = (prop or "").lower()
    if pk in ("rush_yds", "rush_attempts"):
        return "rush"
    if pk in ("pass_yds", "pass_yds_q1", "pass_tds", "pass_attempts", "pass_completions"):
        return "pass"
    if pk == "tds":
        return "td"
    return "rec"


def _prop_script_factor(prop_key: str, margin: float | None) -> float:
    """Small game-script nudge for props — capped separately from team SP+ model."""
    if margin is None:
        return 1.0
    pk = (prop_key or "").lower()
    if pk in ("pass_yds", "pass_yds_q1", "pass_attempts", "pass_completions"):
        if margin > 7:
            lead = min(21.0, float(margin))
            return max(1.0 - PROP_SCRIPT_CAP, 1.0 - (lead - 7.0) * 0.004)
        if margin < -7:
            trail = min(14.0, abs(float(margin)))
            return min(1.0 + PROP_SCRIPT_CAP, 1.0 + trail * 0.003)
    if pk == "pass_tds" and margin > 10:
        return max(1.0 - PROP_SCRIPT_CAP, 1.0 - (min(21.0, margin) - 10.0) * 0.004)
    if pk in ("rush_yds", "rec_yds", "rush_attempts", "receptions"):
        if margin > 10:
            lead = min(21.0, float(margin))
            return min(1.0 + PROP_SCRIPT_CAP, 1.0 + (lead - 10.0) * 0.004)
        if margin < -10:
            trail = min(21.0, abs(float(margin)))
            return max(1.0 - PROP_SCRIPT_CAP, 1.0 - (trail - 10.0) * 0.004)
    return 1.0


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
    """Defense-relative + script + partial offense edge — bounded for props."""
    pk = (prop or "").lower()
    def_pct = defense_display_adjustment_pct(
        opp_name,
        pk,
        season=season,
        display_week=display_week,
    )
    def_delta = 0.0
    if def_pct is not None:
        def_delta = max(-PROP_DEF_ADJ_CAP, min(PROP_DEF_ADJ_CAP, float(def_pct) / 100.0))
    def_factor = 1.0 + def_delta

    margin = None
    if home and away:
        margin = _projected_margin(home, away, team_name, season=season, display_week=display_week)
    script_factor = _prop_script_factor(pk, margin)

    # Partial offense-vs-defense edge (damped — full SP+ model runs too hot).
    factors = sp_matchup_factors(
        team_name,
        opp_name,
        home=home,
        away=away,
        season=season,
        display_week=display_week,
    )
    fkey = prop_factor_key(pk)
    raw = float(factors.get("td" if pk == "pass_tds" else fkey, 1.0))
    if pk == "pass_tds":
        raw = float(factors.get("td", raw))
    offense_nudge = 1.0 + (raw - 1.0) * 0.32

    combined = def_factor * script_factor * offense_nudge
    return max(PROP_FACTOR_MIN, min(PROP_FACTOR_MAX, combined))


def apply_sp_matchup_to_baseline(
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


def clear_sp_matchup_cache() -> None:
    _sp_def_rank_map.cache_clear()
    _league_median_sp_def.cache_clear()
