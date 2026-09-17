"""Player prop board — correct team tags and pricing metrics."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .odds_math import american_to_implied, ev_pct
from .prop_median_projection import median_matchup_projection
from .prop_pricing import (
    analyze_prop_line,
    normalize_prop_market,
    prop_key_from_row,
    side_win_prob,
    sync_market_fields,
)
from .prop_results import model_pick_side
from .prop_reprice import resolve_prop_team, team_logo_for
from .prop_matchup import (
    defense_display_adjustment_pct,
    lookup_def_rank,
    matchup_factors,
)
from .sp_prop_matchup import prop_factor_key
from .team_registry import teams_match


def ensure_team_column(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee a team column — resolve from ESPN/home/away when missing."""
    if df.empty:
        return df
    out = df.copy()
    if "team" not in out.columns:
        out["team"] = ""
    teams: list[str] = []
    for _, row in out.iterrows():
        r = row.to_dict()
        team = str(r.get("team") or r.get("teamLabel") or "").strip()
        if not team:
            resolved = resolve_prop_team(r, skip_starters=True)
            team = str(resolved or "").strip()
        teams.append(team)
    out["team"] = teams
    return out


PROP_LABELS: dict[str, str] = {
    "passing yards": "Passing Yards",
    "1st quarter pass yards": "1Q Pass Yards",
    "passing tds": "Passing TDs",
    "passing touchdowns": "Passing TDs",
    "pass attempts": "Pass Attempts",
    "pass completions": "Pass Completions",
    "rushing yards": "Rushing Yards",
    "rush attempts": "Rush Attempts",
    "receiving yards": "Receiving Yards",
    "receptions": "Receptions",
    "anytime touchdown": "Anytime TD",
    "anytime td": "Anytime TD",
}


def prop_display_name(market: Any) -> str:
    m = normalize_prop_market(market)
    if m in PROP_LABELS:
        return PROP_LABELS[m]
    return str(market or "Prop").strip().title()


def _side_prob(row: dict[str, Any]) -> float | None:
    side = str(row.get("side") or "").lower()
    try:
        proj = float(row.get("modelProj") or row.get("projection"))
        line = float(row.get("line"))
    except (TypeError, ValueError):
        proj = line = None

    if proj is not None and line is not None:
        pk = prop_key_from_row(row) or ""
        over_price = row.get("price") if "over" in side else None
        priced = analyze_prop_line(
            projection=proj,
            line=line,
            prop_key=pk,
            position=str(row.get("position") or ""),
            over_price=over_price,
        )
        if priced:
            wp = side_win_prob(priced.get("over_pct"), side)
            try:
                if wp is not None:
                    return float(wp)
            except (TypeError, ValueError):
                pass

    try:
        wp = float(row.get("winProb"))
        if 0.01 < wp < 0.99:
            return wp
    except (TypeError, ValueError):
        pass
    try:
        over = float(row.get("overProb"))
        if 0.01 < over < 0.99:
            return side_win_prob(over, side)
    except (TypeError, ValueError):
        pass
    return None


def _expected_roi(row: dict[str, Any], win_prob: float | None) -> float | None:
    if win_prob is not None:
        try:
            ev = ev_pct(win_prob, row.get("price"))
            if ev is not None:
                return ev / 100.0
        except (TypeError, ValueError):
            pass
    try:
        roi = float(row.get("roi"))
        if -1.0 < roi < 5.0:
            return roi
    except (TypeError, ValueError):
        pass
    return None


def _resolve_opponent(row: dict[str, Any], team: str | None) -> str | None:
    home = str(row.get("home") or "")
    away = str(row.get("away") or "")
    if not team:
        opp = str(row.get("opponent") or "").strip()
        return opp or None
    if home and away:
        if teams_match(team, home):
            return away
        if teams_match(team, away):
            return home
    opp = str(row.get("opponent") or "").strip()
    return opp or away or home or None


def _attach_opponent_sp(row: dict[str, Any], team: str | None, opponent: str | None) -> None:
    if not opponent:
        return
    row["opponent"] = opponent
    opp_logo = team_logo_for(row, opponent)
    if opp_logo:
        row["opponentLogo"] = opp_logo

    season = row.get("year")
    week = row.get("week")
    try:
        yr = int(season) if season is not None else None
    except (TypeError, ValueError):
        yr = None
    try:
        wk = int(week) if week is not None else None
    except (TypeError, ValueError):
        wk = None

    rank = lookup_def_rank(opponent, season=yr, display_week=wk)
    if rank is not None:
        row["opp_sp_def_rank"] = rank

    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfelo_ratings import lookup_nfelo_team

        rating_row = lookup_nfelo_team(opponent)
        if rating_row is not None and rating_row.get("def_epa_play") is not None:
            row["opp_sp_def"] = round(float(rating_row["def_epa_play"]), 3)
    else:
        from .sp_projections import lookup_sp_team

        sp_row = lookup_sp_team(opponent, season=yr, display_week=wk)
        if sp_row and sp_row.get("spDef") is not None:
            row["opp_sp_def"] = round(float(sp_row["spDef"]), 1)

    pk = prop_key_from_row(row) or ""
    if pk:
        adj = defense_display_adjustment_pct(
            opponent,
            pk,
            season=yr,
            display_week=wk,
        )
        if adj is not None:
            row["opp_def_adj_pct"] = adj
    if team and opponent and pk:
        factors = matchup_factors(
            str(team),
            str(opponent),
            home=row.get("home"),
            away=row.get("away"),
            season=yr,
            display_week=wk,
        )
        if factors.get("oppSpDefRank") is not None:
            row["opp_sp_def_rank"] = factors["oppSpDefRank"]
        if factors.get("oppSpDef") is not None:
            row["opp_sp_def"] = factors["oppSpDef"]
        fkey = prop_factor_key(pk)
        factor = float(factors.get("td" if pk == "pass_tds" else fkey, 1.0))
        if pk == "pass_tds":
            factor = float(factors.get("td", factor))
        row["opp_matchup_factor"] = round(factor, 3)


def enrich_prop_row(row: dict[str, Any]) -> dict[str, Any]:
    from .prop_pricing import is_combo_prop_market

    out = sync_market_fields(dict(row))
    if is_combo_prop_market(out):
        return {}
    if (
        out.get("prop_label")
        and out.get("team")
        and out.get("opponent")
        and out.get("opponentLogo")
        and out.get("opp_sp_def_rank") is not None
        and out.get("modelProj") is not None
    ):
        out.setdefault("team", "")
        return out

    team = str(out.get("team") or out.get("teamLabel") or "").strip()
    if not team:
        resolved = resolve_prop_team(out, skip_starters=True)
        team = str(resolved or "").strip()
    out["team"] = team
    if team:
        logo = team_logo_for(out, team)
        if logo:
            out["teamLogo"] = logo

    opponent = _resolve_opponent(out, team or None)
    _attach_opponent_sp(out, team, opponent)

    proj = median_matchup_projection(out, skip_gamelog=False, skip_starters=True)
    if proj is not None:
        out["modelProj"] = proj
        out["projection"] = proj
    else:
        for key in ("modelProj", "projection"):
            try:
                val = float(out.get(key))
                if val >= 0:
                    proj = val
                    break
            except (TypeError, ValueError):
                continue

    try:
        line_f = float(out.get("line"))
    except (TypeError, ValueError):
        line_f = None
    model_side = model_pick_side(proj, line_f) if proj is not None and line_f is not None else None
    if model_side:
        out["side"] = model_side
        out["modelSide"] = model_side

    wp = _side_prob(out)
    if wp is not None:
        out["winProb"] = wp
        side = str(out.get("side") or "").lower()
        if "under" in side:
            out["overProb"] = 1.0 - wp
        elif "over" in side:
            out["overProb"] = wp

    be = american_to_implied(out.get("price"))
    if be is not None:
        out["break_even"] = be

    roi = _expected_roi(out, wp)
    if roi is not None:
        out["expected_roi"] = roi
        out["ev_pct"] = round(roi * 100, 1)

    out["prop_label"] = prop_display_name(out.get("market"))
    return out


def enrich_prop_board(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rows = [enrich_prop_row(r.to_dict()) for _, r in df.iterrows()]
    rows = [r for r in rows if r]
    if not rows:
        return pd.DataFrame()
    return ensure_team_column(pd.DataFrame(rows).reset_index(drop=True))
