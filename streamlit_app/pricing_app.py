"""Single-game pricing — model projections on live sportsbook lines."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.config import load_env
from lib.espn_client import fetch_game_summary, fetch_scoreboard_cached
from lib.espn_live import fetch_live_scoreboard, latest_win_probability
from lib.prop_results import parse_espn_boxscore
from lib.fourc_odds_client import fourc_odds_enabled
from lib.games import list_games_for_display_week
from pricing_engine.pit import game_is_final
from lib.sport_context import SPORT_CFB, SPORT_NFL, get_sport, get_sport_config, init_sport, set_sport
from lib.styling import apply_theme, section_label
from lib.team_logos import enrich_row_logos

from pricing_engine.constants import DEFAULT_SIMS, LIVE_POLL_SEC, ODDS_POLL_SEC, PROJECTION_CACHE_TTL
from pricing_engine.fourc_board import ensure_live_feed, load_matchup_odds
from pricing_engine.simulator import run_matchup_simulation
from pricing_engine.ui.board import render_market_table
from pricing_engine.ui.props_table import (
    build_prop_projection_map,
    render_player_props_table,
    row_prop_key,
)
from pricing_engine.inplay_props import enrich_live_prop_row
from pricing_engine.ui.inplay_table import inplay_rows_for_game, render_inplay_html
from pricing_engine.ui.render import render_html
from pricing_engine.ui.theme import pricing_board_css

load_env()

st.set_page_config(page_title="Pricing", layout="wide", initial_sidebar_state="collapsed")

init_sport()
_cfg = get_sport_config()

if "pe_week" not in st.session_state:
    st.session_state.pe_week = _cfg.get("default_week", 1)
if "pe_year" not in st.session_state:
    st.session_state.pe_year = _cfg.get("default_year", 2026)

# Bump when projection methodology changes to invalidate cached session sims.
_PROJ_METHODOLOGY_VER = 5

_PROP_TABS = [
    ("rush_yds", "Rush Yds"),
    ("rec_yds", "Rec Yds"),
    ("receptions", "Receptions"),
    ("pass_yds", "Pass Yds"),
    ("pass_tds", "Pass TDs"),
    ("tds", "TDs"),
]


def _team(val: object) -> str:
    if isinstance(val, dict):
        return str(val.get("name") or val.get("displayName") or "")
    return str(val or "")


def _is_live(status: object) -> bool:
    s = str(status or "").lower()
    if isinstance(status, dict):
        s = str(status.get("state") or "").lower()
    return s in ("in", "live", "in progress", "halftime")


def _live_fingerprint(live: dict) -> tuple:
    return (
        live.get("status"),
        live.get("period"),
        live.get("clock"),
        live.get("home_score"),
        live.get("away_score"),
        live.get("win_prob_home"),
        live.get("down"),
        live.get("distance"),
        live.get("yard_line"),
        live.get("possession"),
    )


def _live_state(sport: str, year: int, week: int, home: str, away: str) -> dict:
    for row in fetch_live_scoreboard(sport, week=week, year=year):
        h = _team((row.get("home") or {}).get("name") if isinstance(row.get("home"), dict) else row.get("home"))
        a = _team((row.get("away") or {}).get("name") if isinstance(row.get("away"), dict) else row.get("away"))
        from lib.team_registry import teams_match as cfb_match
        from lib.nfl_team_registry import teams_match as nfl_match

        match = nfl_match if sport == SPORT_NFL else cfb_match
        if match(h, home) and match(a, away):
            status = row.get("status") or {}
            state = status.get("state") if isinstance(status, dict) else str(status)
            hb = row.get("home") or {}
            ab = row.get("away") or {}
            return {
                "status": state,
                "period": status.get("period") if isinstance(status, dict) else row.get("period"),
                "clock": status.get("displayClock") if isinstance(status, dict) else row.get("clock"),
                "home_score": hb.get("score") if isinstance(hb, dict) else row.get("home_score"),
                "away_score": ab.get("score") if isinstance(ab, dict) else row.get("away_score"),
            }
    return {}


def _live_enriched_state(sport: str, year: int, week: int, home: str, away: str, game: dict) -> dict:
    live = _live_state(sport, year, week, home, away)
    if not live:
        return live
    eid = game.get("event_id") or game.get("id")
    if eid:
        try:
            from lib.espn_live import derive_situation, fetch_game_plays, _team_yards
            from pricing_engine.situation_model import parse_play_situation

            summary = fetch_game_summary(str(eid), sport=sport)
            wp = latest_win_probability(summary)
            if wp.get("homeWinPct") is not None:
                live["win_prob_home"] = wp["homeWinPct"]
            plays = fetch_game_plays(str(eid), sport=sport)
            situation = derive_situation(summary, live, plays)
            latest = plays[-1] if plays else {}
            play_sit = parse_play_situation(latest, situation, home=home, away=away)
            live["event_id"] = str(eid)
            box = parse_espn_boxscore(summary)
            live["_box_stats"] = box
            live["situation"] = play_sit
            live["down"] = play_sit.down
            live["distance"] = play_sit.distance
            live["yard_line"] = play_sit.yard_line
            live["possession"] = play_sit.possession_text
            live["home_score"] = play_sit.home_score
            live["away_score"] = play_sit.away_score
            live["period"] = play_sit.period
            live["clock"] = play_sit.clock
            live["_team_yards"] = _team_yards(summary)
        except Exception:
            pass
    return live


def _live_prop_projections(
    props: list,
    *,
    home: str,
    away: str,
    pregame_map: dict,
    live: dict,
    live_sim: dict | None,
    pregame_sim: dict | None,
) -> dict[tuple[str, str, str], float]:
    box: dict = {}
    play_sit = live.get("situation")
    period = int(live.get("period") or 1)
    clock_sec = None
    if play_sit is not None:
        period = int(play_sit.period or period)
        clock_sec = play_sit.clock_seconds
    home_yards, away_yards = ({}, {})
    if live.get("_team_yards"):
        away_yards, home_yards = live["_team_yards"]
    box = live.get("_box_stats") or box
    out: dict[tuple[str, str, str], float] = {}
    for prop in props:
        pk = row_prop_key(prop)
        key = (str(prop.get("player") or ""), str(prop.get("prop_key") or pk or ""), str(prop.get("line") or ""))
        pre = pregame_map.get(key)
        enriched = enrich_live_prop_row(
            prop,
            box_stats=box,
            pregame_proj=pre,
            period=period,
            clock_seconds=clock_sec,
            sit=play_sit,
            home=home,
            away=away,
            live_sim=live_sim,
            pregame_sim=pregame_sim,
            home_team_box=home_yards,
            away_team_box=away_yards,
        )
        lp = enriched.get("live_projection")
        if lp is not None:
            out[key] = float(lp)
        elif pre is not None:
            out[key] = float(pre)
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _slate(sport: str, year: int, week: int) -> list[dict]:
    games = list_games_for_display_week(year, week, sport=sport)
    if games:
        out = [{"home": _team(g.get("home")), "away": _team(g.get("away")), **g} for g in games]
    else:
        sb = fetch_scoreboard_cached(week=max(week, 1), year=year, sport=sport)
        if sb.empty:
            return []
        out = [{"home": _team(r.get("home")), "away": _team(r.get("away")), **r} for r in sb.to_dict("records")]

    sb = fetch_scoreboard_cached(week=max(week, 1), year=year, sport=sport)
    logo_by_team: dict[str, dict] = {}
    if not sb.empty:
        for r in sb.to_dict("records"):
            for side in ("home", "away"):
                name = _team(r.get(side))
                if name:
                    logo_by_team[name.lower()] = {
                        f"{side}_logo": r.get(f"{side}_logo"),
                        "kickoff": r.get("kickoff") or r.get("date_str"),
                    }

    enriched: list[dict] = []
    for g in out:
        row = enrich_row_logos(dict(g))
        for side in ("home", "away"):
            name = str(row.get(side) or "").lower()
            extra = logo_by_team.get(name) or {}
            if extra.get(f"{side}_logo"):
                row[f"{side}_logo"] = extra[f"{side}_logo"]
            if not row.get("kickoff") and extra.get("kickoff"):
                row["kickoff"] = extra["kickoff"]
        enriched.append(row)
    return enriched


def _resolve_lines(q: dict, game: dict) -> tuple[float | None, float | None]:
    spread = q.get("spread_home", {}).get("line")
    if spread is None and q.get("spread_away", {}).get("line") is not None:
        try:
            spread = -float(q["spread_away"]["line"])
        except (TypeError, ValueError):
            spread = None
    if spread is not None:
        try:
            spread = float(spread)
        except (TypeError, ValueError):
            spread = None
    if spread is None:
        spread = game.get("spread") or game.get("closeSpread")
        try:
            spread = float(spread) if spread is not None else None
        except (TypeError, ValueError):
            spread = None

    total = q.get("total_over", {}).get("line") or q.get("total_under", {}).get("line")
    if total is not None:
        try:
            total = float(total)
        except (TypeError, ValueError):
            total = None
    if total is None:
        total = game.get("total") or game.get("closeTotal")
        try:
            total = float(total) if total is not None else None
        except (TypeError, ValueError):
            total = None
    return spread, total


@st.cache_data(ttl=PROJECTION_CACHE_TTL, show_spinner=False)
def _stable_prematch_sim(
    sport: str,
    home: str,
    away: str,
    year: int,
    week: int,
    spread: float | None,
    total: float | None,
) -> dict:
    """Game projections — computed once per matchup, not on every odds tick."""
    return run_matchup_simulation(
        sport, home, away,
        season=year, week=week,
        market_spread=spread, market_total=total,
        live_game=None, n_sims=DEFAULT_SIMS,
    )


@st.cache_data(ttl=PROJECTION_CACHE_TTL, show_spinner=False)
def _stable_prop_projections(
    sport: str,
    home: str,
    away: str,
    year: int,
    week: int,
    props_sig: tuple[tuple[str, str, str], ...],
) -> dict[tuple[str, str, str], float]:
    _ = sport
    payload = load_matchup_odds(sport, home, away, year=year, week=week)
    return build_prop_projection_map(payload["props"], year=year, week=week)


def _projection_key(sport: str, home: str, away: str, year: int, week: int) -> tuple:
    return (sport, home, away, year, week, _PROJ_METHODOLOGY_VER)


def _bootstrap_projections(
    sport: str,
    home: str,
    away: str,
    year: int,
    week: int,
    game: dict,
) -> None:
    """Lock game + player projections when the selected matchup changes."""
    key = _projection_key(sport, home, away, year, week)
    if st.session_state.get("pe_proj_key") == key:
        return

    payload = load_matchup_odds(sport, home, away, year=year, week=week)
    spread, total = _resolve_lines(payload["quotes"], game)
    # Projections always from pricing engine — archive supplies odds/lines only.
    st.session_state.pe_sim = _stable_prematch_sim(sport, home, away, year, week, spread, total)
    st.session_state.pe_pregame_sim = st.session_state.pe_sim
    props = payload["props"]
    sig = tuple(
        (str(p.get("player") or ""), str(p.get("prop_key") or ""), str(p.get("line") or ""))
        for p in props
    )
    st.session_state.pe_prop_projs = _stable_prop_projections(sport, home, away, year, week, sig)
    st.session_state.pe_proj_key = key
    st.session_state.pe_sim_spread = spread
    st.session_state.pe_sim_total = total
    st.session_state.pop("pe_live_fp", None)


def _render_player_props(
    props: list,
    *,
    year: int,
    week: int,
    projections: dict[tuple[str, str, str], float] | None = None,
    game: dict | None = None,
    completed: bool = False,
) -> None:
    if not props:
        st.markdown(
            '<div class="bo-pe-empty-block">No player props with sportsbook lines for this game.</div>',
            unsafe_allow_html=True,
        )
        return

    available = {row_prop_key(p) for p in props if row_prop_key(p)}
    tabs = [(k, label) for k, label in _PROP_TABS if k in available]
    if not tabs:
        tabs = [(k, label) for k, label in _PROP_TABS]

    if "pe_prop_tab" not in st.session_state or st.session_state.pe_prop_tab not in {k for k, _ in tabs}:
        st.session_state.pe_prop_tab = tabs[0][0] if tabs else "rush_yds"

    st.markdown('<div class="bo-pe-prop-tabs">', unsafe_allow_html=True)
    cols = st.columns(min(len(tabs), 6))
    for i, (key, label) in enumerate(tabs[:6]):
        with cols[i]:
            count = sum(1 for p in props if row_prop_key(p) == key)
            if st.button(f"{label} {count}", key=f"pe_tab_{key}", use_container_width=True):
                st.session_state.pe_prop_tab = key
    st.markdown("</div>", unsafe_allow_html=True)

    stat = st.session_state.pe_prop_tab
    html_out = render_player_props_table(
        props,
        year=year,
        week=week,
        stat_filter=stat,
        projections=projections,
        game=game,
        completed=completed,
    )
    render_html(html_out)


def main() -> None:
    apply_theme()
    st.markdown(pricing_board_css(), unsafe_allow_html=True)

    sport = get_sport()
    cfg = get_sport_config()
    ensure_live_feed(sport)

    st.markdown('<div class="bo-rz-header-row">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([2.5, 0.55, 0.55, 0.45])
    with c1:
        st.markdown('<div class="bo-hero-eyebrow">PRICING</div>', unsafe_allow_html=True)
    with c2:
        if st.button("CFB", key="pe_cfb", use_container_width=True, type="primary" if sport == SPORT_CFB else "secondary"):
            set_sport(SPORT_CFB)
            cfg_cfb = get_sport_config(SPORT_CFB)
            st.session_state.pe_year = int(cfg_cfb["default_year"])
            st.session_state.pe_week = int(cfg_cfb["default_week"])
            st.session_state.pop("pe_proj_key", None)
            st.rerun()
    with c3:
        if st.button("NFL", key="pe_nfl", use_container_width=True, type="primary" if sport == SPORT_NFL else "secondary"):
            set_sport(SPORT_NFL)
            cfg_nfl = get_sport_config(SPORT_NFL)
            st.session_state.pe_year = int(cfg_nfl["default_year"])
            st.session_state.pe_week = int(cfg_nfl["default_week"])
            st.session_state.pop("pe_proj_key", None)
            st.rerun()
    with c4:
        st.session_state.pe_week = st.number_input(
            "W", min_value=0 if cfg.get("has_week0") else 1,
            max_value=int(cfg.get("max_week", 16)),
            value=int(st.session_state.pe_week), label_visibility="collapsed",
        )
    st.markdown("</div>", unsafe_allow_html=True)

    if not fourc_odds_enabled():
        st.warning("Live odds feed is off. Set FOURC_ODDS_ENABLED=1 in your environment.")
        return

    year = int(st.session_state.pe_year)
    week = int(st.session_state.pe_week)
    games = _slate(sport, year, week)
    if not games:
        st.info("No games on the slate for this week.")
        return

    labels = [f'{g["away"]} @ {g["home"]}' for g in games]
    st.markdown('<div class="bo-pe-game-select">', unsafe_allow_html=True)
    pick = st.selectbox("Game", labels, label_visibility="collapsed")
    st.markdown("</div>", unsafe_allow_html=True)

    game = games[labels.index(pick)]
    home, away = game["home"], game["away"]
    live_now = _is_live(game.get("status"))
    completed = game_is_final(game)

    _bootstrap_projections(sport, home, away, year, week, game)

    if live_now:
        @st.fragment(run_every=LIVE_POLL_SEC)
        def _live_sim_refresh():
            live = _live_enriched_state(sport, year, week, home, away, game)
            fp = _live_fingerprint(live)
            if st.session_state.get("pe_live_fp") == fp:
                return
            spread = st.session_state.get("pe_sim_spread")
            total = st.session_state.get("pe_sim_total")
            st.session_state.pe_sim = run_matchup_simulation(
                sport, home, away,
                season=year, week=week,
                market_spread=spread, market_total=total,
                live_game=live, n_sims=DEFAULT_SIMS,
            )
            payload = load_matchup_odds(sport, home, away, year=year, week=week)
            pre = st.session_state.get("pe_prop_projs") or {}
            st.session_state.pe_live_prop_projs = _live_prop_projections(
                payload["props"],
                home=home,
                away=away,
                pregame_map=pre,
                live=live,
                live_sim=st.session_state.get("pe_sim"),
                pregame_sim=st.session_state.get("pe_pregame_sim"),
            )
            st.session_state.pe_live_fp = fp

        _live_sim_refresh()

    @st.fragment(run_every=ODDS_POLL_SEC)
    def _live_odds_panel():
        payload = load_matchup_odds(sport, home, away, year=year, week=week)
        q = payload["quotes"]
        props = payload["props"]
        sim = st.session_state.get("pe_sim")
        if live_now:
            prop_projs = st.session_state.get("pe_live_prop_projs") or st.session_state.get("pe_prop_projs") or {}
        else:
            prop_projs = st.session_state.get("pe_prop_projs") or {}
        live = _live_enriched_state(sport, year, week, home, away, game) if live_now else None

        grade_game = dict(game)
        archived = payload.get("archived_game") or {}
        if archived.get("event_id") and not grade_game.get("event_id"):
            grade_game["event_id"] = archived.get("event_id")
        if archived.get("home_score") is not None and grade_game.get("homePoints") is None:
            grade_game["homePoints"] = archived.get("home_score")
        if archived.get("away_score") is not None and grade_game.get("awayPoints") is None:
            grade_game["awayPoints"] = archived.get("away_score")
        game_completed = game_is_final(grade_game)

        section_label("Game Markets")
        render_html(render_market_table(q, sim, game=grade_game, live=live, completed=game_completed))

        section_label("Player Props")
        _render_player_props(
            props,
            year=year,
            week=week,
            projections=prop_projs,
            game=grade_game,
            completed=game_completed,
        )

        if live_now:
            section_label("In-Play Log (PBP × Markets)")
            eid = grade_game.get("event_id") or archived.get("event_id")
            ip_df = inplay_rows_for_game(sport=sport, event_id=str(eid) if eid else None, home=home, away=away)
            render_html(render_inplay_html(ip_df))

    _live_odds_panel()


if __name__ == "__main__":
    main()
