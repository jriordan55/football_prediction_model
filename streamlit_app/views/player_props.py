"""+EV Player Props — reference board + player detail."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from lib.display import render_prop_detail_panel, render_props_board
from lib.config import DEFAULT_WEEK, DEFAULT_YEAR
from lib.excel_audit import export_pull
from lib.sport_context import cache_sport
from lib.slate_loader import load_slate_df
from lib.odds_client import props_df, fetch_onyx_df
from lib.matchup_prop_enrich import is_real_market_prop, resolve_prop_projection
from lib.prop_board import filter_main_prop_lines
from lib.prop_board_enrich import enrich_prop_board
from lib.prop_pricing import normalize_prop_market, normalize_onyx_player, prop_key_from_row
from lib.prop_reprice import reprice_props_df
from lib.prop_starters import enrich_starter_metadata
from lib.styling import callout, section_header, section_label, week_season_filters

TAB = "Player Props"
SEL_KEY = "pp_selected_player"
PROP_BOARD_VERSION = 6


def _prepare_prop_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "player" in out.columns:
        out["player"] = out["player"].map(normalize_onyx_player)
    if "market" in out.columns:
        out["market"] = out["market"].map(normalize_prop_market)
    out = out[out.apply(lambda r: is_real_market_prop(r.to_dict()), axis=1)].copy()
    return filter_main_prop_lines(out)


@st.cache_data(ttl=3600, show_spinner=False)
def load_player_props(
    sport: str,
    year: int,
    week: int,
    *,
    board_version: int = PROP_BOARD_VERSION,
) -> pd.DataFrame:
    _ = sport, board_version
    df = load_slate_df(cache_sport(), year, week)
    if df.empty:
        df = props_df(fetch_onyx_df(tab=TAB))
    else:
        mask = df["player"].notna() & (df["player"].astype(str).str.len() > 1)
        mask |= df["group"].astype(str).str.contains("prop", case=False, na=False)
        df = df[mask]
    df = _prepare_prop_frame(df)
    repriced = reprice_props_df(df)
    repriced = enrich_starter_metadata(repriced)
    repriced = repriced[
        repriced.apply(lambda r: resolve_prop_projection(r.to_dict()) is not None, axis=1)
    ].copy()
    if repriced.empty:
        return repriced
    repriced["year"] = int(year)
    repriced["week"] = int(week)
    return enrich_prop_board(repriced)


def render() -> None:
    year, week = week_season_filters("pp", year=DEFAULT_YEAR, week=DEFAULT_WEEK)

    section_header(
        "+EV Player Props",
        "Season baseline projections vs market lines — sorted by edge.",
        eyebrow="PROPS",
    )

    st.markdown('<div class="bo-filter-inline">', unsafe_allow_html=True)
    min_ev = st.slider("Minimum EV", 0.0, 15.0, 2.0, 0.5, format="%.0f%%")
    st.markdown("</div>", unsafe_allow_html=True)

    df = load_player_props(
        cache_sport(), int(year), int(week), board_version=PROP_BOARD_VERSION,
    )
    if df.empty:
        callout("Player props will appear here when the slate is live.", "info")
        return

    df_all = df.copy()
    if "ev_pct" not in df.columns or df["ev_pct"].isna().all():
        if "roi" in df.columns:
            df["ev_pct"] = (pd.to_numeric(df["roi"], errors="coerce") * 100).round(1)
        else:
            df["ev_pct"] = pd.to_numeric(df.get("edgeDisplay"), errors="coerce")
    df_all["ev_pct"] = df["ev_pct"]

    df = df[df["ev_pct"].fillna(0) >= min_ev].sort_values("ev_pct", ascending=False)
    export_pull("derived_ev_props", df, tab=TAB, origin="computed")

    if df.empty:
        callout(f"No props above {min_ev:.0f}% EV right now. Try lowering the minimum.", "info")
        return

    players = df["player"].drop_duplicates().tolist()
    if SEL_KEY not in st.session_state or st.session_state[SEL_KEY] not in players:
        st.session_state[SEL_KEY] = players[0]

    section_label("Today's +EV board", f"{len(df)} plays · sorted by edge")

    left, right = st.columns([1.35, 1], gap="large")
    with left:
        render_props_board(df, selected_player=st.session_state[SEL_KEY])
    with right:
        st.markdown('<div class="bo-pp-picker-label">Player detail</div>', unsafe_allow_html=True)
        st.markdown('<div class="bo-pp-select-wrap">', unsafe_allow_html=True)
        pick = st.selectbox(
            "Player",
            players,
            index=players.index(st.session_state[SEL_KEY]),
            key="pp_player_picker",
            label_visibility="collapsed",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        st.session_state[SEL_KEY] = pick
        player_rows = df[df["player"] == pick]
        best_prop = ""
        if not player_rows.empty:
            best_prop = str(
                player_rows.sort_values("ev_pct", ascending=False).iloc[0].get("propKey") or ""
            )
        render_prop_detail_panel(df, pick, chart_prop=best_prop, chart_df=df_all)
