"""Live Games — in-progress board with projected finals + play-by-play detail."""
from __future__ import annotations

import json
import time

import streamlit as st

from lib.app_filters import render_global_filters
from lib.espn_live import (
    fetch_game_plays_cached,
    fetch_game_summary_cached,
    fetch_live_game_detail,
    fetch_live_scoreboard,
    latest_win_probability,
    derive_situation,
    _team_yards,
    _plays_from_drives,
    live_poll_bucket,
    slate_rows_for_matchup,
)
from lib.live_display import render_live_card, render_live_detail
from lib.live_projections import project_live_game
from lib.sport_context import cache_sport
from lib.slate_loader import load_slate_df
from lib.styling import section_header

TAB = "Live"
SEL_KEY = "live_selected_event"
WIN_DISPLAY_KEY = "live_win_display"
POLL_SEC = 15


def _win_display_mode() -> str:
    v = st.session_state.get(WIN_DISPLAY_KEY, "Win %")
    return "american" if v in ("American odds", "Price to win") else "pct"


def _clear_live_cache() -> None:
    fetch_live_scoreboard.clear()
    fetch_live_game_detail.clear()
    fetch_game_summary_cached.clear()
    fetch_game_plays_cached.clear()
    _board_with_projections.clear()


def _pregame_for_game(g: dict, slate) -> dict:
    """Pregame lines from archived slate + cached odds — no ESPN summary on board load."""
    pre = {"spread": None, "total": None}

    if slate is not None and not slate.empty:
        rows = slate_rows_for_matchup(slate, g["home"]["name"], g["away"]["name"])
        for row in rows:
            if str(row.get("market")) == "Spread" and row.get("line") is not None:
                pre["spread"] = float(row["line"])
            if str(row.get("market")) == "Total" and row.get("line") is not None:
                pre["total"] = float(row["line"])

    if pre.get("spread") is None or pre.get("total") is None:
        try:
            from lib.game_odds_quotes import draftkings_game_quotes
            from lib.odds_cache import load_cached_lines

            odds = load_cached_lines()
            if not odds.empty:
                quotes = draftkings_game_quotes(g["home"]["name"], g["away"]["name"], odds)
                home_sp = quotes.get("home_spread") or {}
                away_sp = quotes.get("away_spread") or {}
                over_q = quotes.get("over") or {}
                if pre.get("spread") is None:
                    if home_sp.get("line") is not None:
                        pre["spread"] = float(home_sp["line"])
                    elif away_sp.get("line") is not None:
                        pre["spread"] = -float(away_sp["line"])
                if pre.get("total") is None and over_q.get("line") is not None:
                    pre["total"] = float(over_q["line"])
        except Exception:
            pass

    return pre


def _enrich_live_context(g: dict, summary: dict | None, *, sport: str, tick: int) -> None:
    if not summary:
        return
    plays = fetch_game_plays_cached(g["event_id"], sport, tick=tick)
    if not plays:
        plays = _plays_from_drives(summary)
    g["situation"] = derive_situation(summary, g, plays)
    g["awayYards"], g["homeYards"] = _team_yards(summary)
    g["winProbability"] = latest_win_probability(summary)


def _project_game(g: dict, pre: dict) -> dict:
    wp = g.get("winProbability") or {}
    proj = project_live_game(
        home_score=int(g["home"]["score"]),
        away_score=int(g["away"]["score"]),
        period=int((g.get("situation") or {}).get("period") or 1),
        clock_seconds=(g.get("situation") or {}).get("clockSeconds"),
        pregame_spread=pre.get("spread"),
        pregame_total=pre.get("total"),
        home_yards=g.get("homeYards"),
        away_yards=g.get("awayYards"),
        espn_home_wp=wp.get("homeWinPct"),
        status_state=g["status"]["state"],
    )
    return {**g, "projection": proj, "pregame": pre}


@st.cache_data(ttl=12, show_spinner=False)
def _board_with_projections(sport: str, year: int, week: int, slate_sig: str, tick: int) -> list[dict]:
    _ = slate_sig
    games = fetch_live_scoreboard(sport, week=week, year=year, tab=TAB)
    slate = load_slate_df(sport, year, week if week != 0 else 1)
    out = []
    for g in games:
        pre = _pregame_for_game(g, slate)
        if g["status"]["state"] == "in":
            try:
                summary = fetch_game_summary_cached(g["event_id"], sport, tab=TAB, tick=tick)
                _enrich_live_context(g, summary, sport=sport, tick=tick)
            except Exception:
                pass
        out.append(_project_game(g, pre))
    return out


def _poll_tick() -> int:
    return int(time.time()) // POLL_SEC


def _render_win_display_toggle() -> None:
    st.radio(
        "Win display",
        options=["Win %", "Price to win"],
        horizontal=True,
        key=WIN_DISPLAY_KEY,
        label_visibility="collapsed",
    )


def _ordered_games(games: list[dict]) -> list[dict]:
    live = [g for g in games if (g.get("status") or {}).get("state") == "in"]
    upcoming = [g for g in games if (g.get("status") or {}).get("state") == "pre"]
    final = [
        g
        for g in games
        if (g.get("status") or {}).get("state") == "post" or (g.get("status") or {}).get("completed")
    ]
    other = [g for g in games if g not in live and g not in upcoming and g not in final]
    return live + upcoming + other + final


def _render_board(year: int, week: int) -> None:
    games = _board_with_projections(cache_sport(), year, week, f"{year}_{week}", _poll_tick())
    live_n = sum(1 for g in games if (g.get("status") or {}).get("state") == "in")
    section_header(
        "Live Games",
        f"{live_n} in progress · auto-refresh {POLL_SEC}s · projected finals from pregame + live pace",
    )
    _render_win_display_toggle()
    selected = st.session_state.get(SEL_KEY)
    win_mode = _win_display_mode()

    if not games:
        st.markdown('<div class="bo-live-empty">No games on the board for this week.</div>', unsafe_allow_html=True)
        return

    ordered = _ordered_games(games)
    n_cols = 3
    for i in range(0, len(ordered), n_cols):
        cols = st.columns(n_cols)
        for j, g in enumerate(ordered[i : i + n_cols]):
            eid = str(g.get("event_id") or "")
            away = g.get("away") or {}
            home = g.get("home") or {}
            label = f"{away.get('abbr') or away.get('name', '')} @ {home.get('abbr') or home.get('name', '')}"
            with cols[j]:
                st.markdown(
                    render_live_card(g, selected=(eid == selected), win_display=win_mode),
                    unsafe_allow_html=True,
                )
                if st.button(label, key=f"live_pick_{eid}_{i}_{j}", use_container_width=True, type="secondary"):
                    st.session_state[SEL_KEY] = eid
                    st.rerun()


def _render_detail(event_id: str, year: int, week: int) -> None:
    sport = cache_sport()
    slate = load_slate_df(sport, year, week if week != 0 else 1)
    board = _board_with_projections(sport, year, week, f"{year}_{week}", _poll_tick())
    card = next((g for g in board if g["event_id"] == event_id), None)
    home_name = card["home"]["name"] if card else ""
    away_name = card["away"]["name"] if card else ""
    slate_rows = slate_rows_for_matchup(slate, home_name, away_name) if home_name else []

    detail_raw = fetch_live_game_detail(
        sport,
        event_id,
        home_name,
        away_name,
        slate_json=json.dumps(slate_rows, default=str),
        week=week,
        year=year,
        tab=TAB,
        tick=_poll_tick(),
    )
    wp = detail_raw.get("winProbability") or {}
    proj = project_live_game(
        home_score=int(detail_raw["game"]["home"]["score"]),
        away_score=int(detail_raw["game"]["away"]["score"]),
        period=int((detail_raw.get("situation") or {}).get("period") or 1),
        clock_seconds=(detail_raw.get("situation") or {}).get("clockSeconds"),
        pregame_spread=(detail_raw.get("pregame") or {}).get("spread"),
        pregame_total=(detail_raw.get("pregame") or {}).get("total"),
        home_yards=detail_raw.get("homeYards"),
        away_yards=detail_raw.get("awayYards"),
        espn_home_wp=wp.get("homeWinPct"),
        status_state=detail_raw["game"]["status"]["state"],
    )
    st.markdown(
        render_live_detail({**detail_raw, "projection": proj}, win_display=_win_display_mode()),
        unsafe_allow_html=True,
    )


def _render_body(year: int, week: int) -> None:
    """Board or detail — safe to rerun inside a fragment without flashing the shell."""
    selected = st.session_state.get(SEL_KEY)
    st.markdown('<div class="bo-live-page">', unsafe_allow_html=True)
    if selected:
        _render_win_display_toggle()
        _render_detail(str(selected), year, week)
    else:
        _render_board(year, week)
    st.markdown("</div>", unsafe_allow_html=True)


def render() -> None:
    year, week = render_global_filters(prefix="live")

    hdr_l, hdr_r = st.columns([4, 1])
    with hdr_r:
        if st.button("Refresh now", key="live_refresh"):
            _clear_live_cache()
            st.rerun()

    selected = st.session_state.get(SEL_KEY)
    if selected:
        with hdr_l:
            if st.button("← Back to live board", key="live_back"):
                st.session_state.pop(SEL_KEY, None)
                st.rerun()

    if hasattr(st, "fragment"):
        @st.fragment(run_every=POLL_SEC)
        def _live_poll() -> None:
            _render_body(year, week)

        _live_poll()
    else:
        _render_body(year, week)
