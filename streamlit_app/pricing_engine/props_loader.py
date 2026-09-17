"""Load player props for a single matchup — cached board, no blocking rebuild on render."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib.sport_context import SPORT_NFL


def _teams_match_row(row: dict, home: str, away: str, *, sport: str) -> bool:
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    match = nfl_match if sport == SPORT_NFL else cfb_match
    rh = str(row.get("home") or "")
    ra = str(row.get("away") or "")
    if not rh or not ra:
        return False
    return match(rh, home) and match(ra, away)


@st.cache_data(ttl=120, show_spinner=False)
def load_matchup_player_props(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> pd.DataFrame:
    from views.player_projections import load_cached_player_props

    df = load_cached_player_props(sport, int(year), int(week))
    if df.empty:
        return df

    from lib.prop_board_enrich import ensure_team_column

    df = ensure_team_column(df)
    rows = [r.to_dict() for _, r in df.iterrows() if _teams_match_row(r.to_dict(), home, away, sport=sport)]
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).reset_index(drop=True)
    if "ev_pct" not in out.columns or out["ev_pct"].isna().all():
        if "roi" in out.columns:
            out["ev_pct"] = (pd.to_numeric(out["roi"], errors="coerce") * 100).round(1)
        else:
            out["ev_pct"] = pd.to_numeric(out.get("edgeDisplay"), errors="coerce")
    sort_col = "ev_pct" if "ev_pct" in out.columns else None
    if sort_col:
        out = out.sort_values(sort_col, ascending=False, na_position="last")
    return out.reset_index(drop=True)


def build_matchup_player_props(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> pd.DataFrame:
    """Full rebuild when cache is empty — call inside st.spinner."""
    from views.player_projections import build_player_props_board

    build_player_props_board.clear()
    build_player_props_board(sport, int(year), int(week))
    load_matchup_player_props.clear()
    return load_matchup_player_props(sport, int(year), int(week), home, away)
