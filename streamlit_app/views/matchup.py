"""Matchup Detail — reference-style game card with historical results."""
from __future__ import annotations

import streamlit as st

from lib.config import DEFAULT_WEEK, DEFAULT_YEAR
from lib.espn_client import fetch_league_injuries
from lib.game_board import clear_game_cards_cache
from lib.matchup_board import load_matchup_board
from lib.matchup_detail_loader import (
    clear_matchup_details_cache,
    load_matchup_detail,
    matchup_detail_cache_updated_at,
)
from lib.matchup_display import render_matchup_detail
from lib.sport_context import cache_sport
from lib.styling import callout, section_header, week_season_filters
from lib.team_registry import resolve_canonical
from lib.view_disk_cache import format_cache_age

TAB = "Matchup Detail"


@st.cache_data(ttl=3600, show_spinner=False)
def _load_injuries(sport: str):
    _ = sport
    return fetch_league_injuries(tab=TAB)


def _clear_matchup_view_cache(sport: str, year: int, week: int) -> None:
    clear_game_cards_cache(sport, int(year), int(week))
    clear_matchup_details_cache(sport, int(year), int(week))


def render() -> None:
    sport = cache_sport()
    year, week = week_season_filters("match", year=DEFAULT_YEAR, week=DEFAULT_WEEK)

    is_past_week = int(week) < int(DEFAULT_WEEK)
    subtitle = (
        "Final scores, pre-game projections, and graded props for completed games."
        if is_past_week
        else "Projections, team profiles, props, and injuries — one game at a time."
    )

    section_header("Matchup Detail", subtitle, eyebrow="MATCHUP")

    cache_ts = matchup_detail_cache_updated_at(sport, int(year), int(week))
    hdr_l, hdr_r = st.columns([3, 1])
    with hdr_r:
        refresh = st.button("Refresh odds & lines", key="match_refresh", use_container_width=True)
    if refresh:
        _clear_matchup_view_cache(sport, int(year), int(week))
        st.session_state["match_force_build"] = True
        st.rerun()

    with hdr_l:
        st.caption(f"Details cached · updated {format_cache_age(cache_ts)}")

    board = load_matchup_board(cache_sport(), int(year), int(week), tab=TAB)
    if board.empty:
        callout(
            "No games on the board for this week yet. Week 0 covers opening-weekend games (late August).",
            "info" if int(week) == 0 else "warn",
        )
        return

    options = board["label"].tolist()
    pick = st.selectbox("Select game", options, label_visibility="collapsed")
    row = board[board["label"] == pick]
    if row.empty:
        row = board.head(1)
    board_row = row.iloc[0]

    home_canon = board_row.get("home_canon") or resolve_canonical(board_row.get("home")) or board_row.get("home")
    away_canon = board_row.get("away_canon") or resolve_canonical(board_row.get("away")) or board_row.get("away")
    historical = bool(is_past_week or board_row.get("completed"))
    force = bool(st.session_state.pop("match_force_build", False))

    home_s, away_s = str(home_canon), str(away_canon)
    br = board_row.to_dict()
    label = "Refreshing game details…" if force else "Loading game details…"
    with st.spinner(label):
        detail = load_matchup_detail(
            sport, home_s, away_s,
            year=int(year), week=int(week), historical=historical,
            board_row=br, tab=TAB, force=force,
        )

    if historical and not detail.get("actualScore") and board_row.get("completed"):
        callout(
            "This game is marked complete but final scores haven't synced yet. Props and results will fill in once CFBD/ESPN updates.",
            "info",
        )

    injuries = _load_injuries(cache_sport())
    render_matchup_detail(detail, injuries)
