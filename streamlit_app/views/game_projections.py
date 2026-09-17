"""Game Projections — matchup board + detail on click."""
from __future__ import annotations

import streamlit as st

from lib.app_filters import render_global_filters
from lib.display import render_game_projection_cards
from lib.espn_client import fetch_league_injuries
from lib.game_board import (
    build_game_card_rows,
    cards_missing_odds,
    game_cards_cache_updated_at,
    load_cached_game_cards,
    merge_odds_into_cards,
)
from lib.game_status import game_is_final, game_past_kickoff
from lib.games import display_week_complete
from lib.matchup_board import load_matchup_board
from lib.matchup_detail_loader import load_matchup_detail
from lib.matchup_display import render_matchup_detail
from lib.odds_cache import ensure_fourc_lines
from lib.odds_live_feed import ensure_odds_live_feed, live_feed_updated_at, live_odds_enabled
from lib.otter_odds_client import otter_odds_enabled
from lib.styling import callout, section_header
from lib.sp_refresh import ensure_fresh_sp_ratings
from lib.sport_context import SPORT_CFB, SPORT_NFL, cache_sport, get_sport
from lib.team_registry import resolve_canonical
from lib.view_disk_cache import format_cache_age

TAB = "Game Projections"
SEL_KEY = "gp_selected_matchup"


@st.cache_data(ttl=3600, show_spinner=False)
def _load_injuries(sport: str):
    _ = sport
    return fetch_league_injuries(tab=TAB)


@st.cache_data(ttl=60, show_spinner=False)
def build_game_cards_board(
    sport: str,
    year: int,
    week: int,
    *,
    include_line_history: bool = False,
    odds_sig: str = "",
) -> list[dict]:
    _ = odds_sig
    return build_game_card_rows(
        sport,
        int(year),
        int(week),
        tab=TAB,
        quick=not include_line_history and not live_odds_enabled(),
        save_cache=True,
    )


def _render_detail(
    year: int,
    week: int,
    board_row,
    *,
    historical: bool,
) -> None:
    home_canon = board_row.get("home_canon") or resolve_canonical(board_row.get("home")) or board_row.get("home")
    away_canon = board_row.get("away_canon") or resolve_canonical(board_row.get("away")) or board_row.get("away")
    br = board_row.to_dict() if hasattr(board_row, "to_dict") else dict(board_row)
    sport = cache_sport()
    home_s, away_s = str(home_canon), str(away_canon)

    with st.spinner("Loading game details…"):
        detail = load_matchup_detail(
            sport, home_s, away_s,
            year=int(year), week=int(week), historical=historical,
            board_row=br, tab=TAB,
        )

    render_matchup_detail(detail, _load_injuries(sport))


def render() -> None:
    year, week = render_global_filters(prefix="gp")
    sport = get_sport()

    section_header("Game Projections", eyebrow=f"WEEK {week}")

    use_live_odds = live_odds_enabled() and sport in (SPORT_CFB, SPORT_NFL)
    odds_sig = ""
    if use_live_odds:
        ensure_odds_live_feed(sport)
        odds_df = ensure_fourc_lines(tab=TAB)
        odds_sig = str(len(odds_df))
        try:
            from lib.odds_cache import _load_meta

            meta = _load_meta()
            odds_sig = str(meta.get("fetched_at") or odds_sig)
        except Exception:
            pass

    if use_live_odds:
        odds_ts = live_feed_updated_at(sport)
        sync_txt = format_cache_age(odds_ts) if odds_ts else "just now"
        src = "Otter Odds" if otter_odds_enabled() else "4C Odds"
        st.markdown(
            f'<p id="gp-odds-sync" class="gp-odds-sync">{src} · synced {sync_txt}</p>',
            unsafe_allow_html=True,
        )
    else:
        cache_ts = game_cards_cache_updated_at(sport, int(year), int(week))
        st.caption(f"Board · updated {format_cache_age(cache_ts)}")

    if SEL_KEY not in st.session_state:
        st.session_state[SEL_KEY] = None

    if st.session_state[SEL_KEY]:
        if st.button("← Back to all games", key="gp_back"):
            st.session_state[SEL_KEY] = None
            st.rerun()
        board = load_matchup_board(cache_sport(), int(year), int(week), tab=TAB)
        row = board[board["label"] == st.session_state[SEL_KEY]]
        if row.empty:
            cards = load_cached_game_cards(sport, int(year), int(week))
            for c in cards:
                if c.get("matchup") == st.session_state[SEL_KEY] or c.get("label") == st.session_state[SEL_KEY]:
                    row = board[
                        (board["home"] == c.get("home")) & (board["away"] == c.get("away"))
                    ]
                    break
        if row.empty:
            st.session_state[SEL_KEY] = None
            st.rerun()
        br = row.iloc[0]
        is_final = game_is_final(
            completed=bool(br.get("completed")),
            home_pts=br.get("home_score"),
            away_pts=br.get("away_score"),
            start_date=br.get("date"),
        )
        show_archived = is_final or (int(week) == 0 and game_past_kickoff(br.get("date")))
        _render_detail(int(year), int(week), br, historical=show_archived)
        return

    week_done = display_week_complete(int(year), int(week), sport=sport)
    cards = load_cached_game_cards(sport, int(year), int(week))
    if not cards or (use_live_odds and cards_missing_odds(cards)):
        label = "Loading archived projections…" if week_done else "Building game projections…"
        with st.spinner(label):
            if sport != SPORT_NFL and not week_done:
                ensure_fresh_sp_ratings(season=int(year), display_week=int(week))
            build_game_cards_board.clear()
            cards = build_game_cards_board(
                sport,
                int(year),
                int(week),
                odds_sig=odds_sig,
            )

    if not cards:
        if sport == SPORT_NFL:
            callout("No NFL games for this week yet.", "warn")
        else:
            callout(
                "No games for this week yet. Week 0 covers opening-weekend games (late August).",
                "info" if int(week) == 0 else "warn",
            )
        return

    if use_live_odds:
        cards = merge_odds_into_cards(cards, sport=sport, tab=TAB)

    render_game_projection_cards(cards, selected=st.session_state.get(SEL_KEY))
