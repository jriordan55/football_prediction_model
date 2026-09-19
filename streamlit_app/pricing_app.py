"""Single-game pricing — model projections on live sportsbook lines."""
from __future__ import annotations

import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.config import load_env
from lib.espn_client import fetch_scoreboard_cached
from lib.espn_live import (
    fetch_game_plays_cached,
    fetch_game_summary_cached,
    fetch_live_scoreboard,
    latest_win_probability,
    live_poll_bucket,
)
from lib.prop_results import parse_espn_boxscore
from lib.fourc_odds_client import fourc_odds_enabled
from lib.games import list_games_for_display_week
from pricing_engine.pit import game_is_final
from lib.sport_context import SPORT_CFB, SPORT_NFL, get_sport, get_sport_config, init_sport, set_sport
from lib.styling import section_label
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
from pricing_engine.pbp_log import load_matchup_pbp_log
from pricing_engine.ui.inplay_table import render_pbp_log_html
from pricing_engine.ui.render import render_html
from pricing_engine.ui.theme import apply_pricing_theme, pricing_board_css

load_env()

st.set_page_config(page_title="Pricing", layout="wide", initial_sidebar_state="collapsed")

init_sport()
_cfg = get_sport_config()

if "pe_week" not in st.session_state:
    st.session_state.pe_week = _cfg.get("default_week", 1)
if "pe_year" not in st.session_state:
    st.session_state.pe_year = _cfg.get("default_year", 2026)

# Bump when projection methodology changes to invalidate cached session sims.
_PROJ_METHODOLOGY_VER = 11
_PBP_CAPTURE_MIN_SEC = 20
_PBP_RENDER_MIN_SEC = 12

_PROP_TABS = [
    ("rush_yds", "Rush Yards"),
    ("rec_yds", "Rec Yards"),
    ("receptions", "Receptions"),
    ("pass_yds", "Pass Yards"),
    ("pass_tds", "Pass TDs"),
    ("tds", "Anytime TD"),
]


def _team(val: object) -> str:
    if isinstance(val, dict):
        return str(val.get("name") or val.get("displayName") or "")
    return str(val or "")


def _is_live(status: object) -> bool:
    if isinstance(status, dict):
        state = str(status.get("state") or "").lower()
        if state in ("in", "live", "halftime"):
            return True
        status = status.get("description") or status.get("detail") or status.get("name") or state
    s = str(status or "").lower().strip()
    if s in ("in", "live", "in progress", "halftime"):
        return True
    if "final" in s or "scheduled" in s or "pregame" in s or s == "pre":
        return False
    if any(tok in s for tok in ("progress", "quarter", "halftime", "half time", "end of 1", "end of 2", "end of 3", "end of 4")):
        return True
    return False


def _game_is_live(
    game: dict,
    *,
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> bool:
    if game_is_final(game):
        return False
    if _is_live(game.get("status")) or _is_live(game.get("status_state")):
        return True
    live = _live_state(sport, year, week, home, away)
    if live and _is_live(live.get("status")):
        return True
    try:
        hp = int(game.get("homePoints") if game.get("homePoints") is not None else game.get("home_score"))
        ap = int(game.get("awayPoints") if game.get("awayPoints") is not None else game.get("away_score"))
        if (hp > 0 or ap > 0) and not game_is_final({**game, "homePoints": hp, "awayPoints": ap}):
            return True
    except (TypeError, ValueError):
        pass
    return False


def _merge_live_scoreboard(
    game: dict,
    *,
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict:
    row = dict(game)
    live = _live_state(sport, year, week, home, away)
    if not live:
        return row
    if live.get("status"):
        row["status"] = live["status"]
    if live.get("period") is not None:
        row["period"] = live["period"]
    if live.get("clock"):
        row["clock"] = live["clock"]
    if live.get("home_score") is not None:
        row["homePoints"] = live["home_score"]
    if live.get("away_score") is not None:
        row["awayPoints"] = live["away_score"]
    return row


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


def _pregame_snapshot_sig(
    quotes: dict[str, dict[str, Any]],
    props: list,
    spread: float | None,
    total: float | None,
) -> tuple:
    quote_bits = tuple(
        sorted(
            (k, (q or {}).get("line"), (q or {}).get("price"))
            for k, q in (quotes or {}).items()
        )
    )
    return (spread, total, len(props), quote_bits)


def _pbp_play_sig(plays: list[dict]) -> tuple[int, str]:
    if not plays:
        return (0, "")
    last = plays[-1] or {}
    return (len(plays), str(last.get("id") or ""))


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
            from lib.espn_live import derive_situation, _team_yards
            from pricing_engine.situation_model import parse_play_situation

            tick = live_poll_bucket()
            summary = fetch_game_summary_cached(str(eid), sport, tick=tick)
            wp = latest_win_probability(summary)
            if wp.get("homeWinPct") is not None:
                live["win_prob_home"] = wp["homeWinPct"]
            plays = fetch_game_plays_cached(str(eid), sport, tick=tick)
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
    from lib.games import lookup_completed_game, merge_completed

    sb = fetch_scoreboard_cached(week=max(week, 1), year=year, sport=sport)
    sb_rows = sb.to_dict("records") if not sb.empty else []
    games = list_games_for_display_week(year, week, sport=sport)
    if games:
        out = [{"home": _team(g.get("home")), "away": _team(g.get("away")), **g} for g in games]
    else:
        if not sb_rows:
            return []
        out = [{"home": _team(r.get("home")), "away": _team(r.get("away")), **r} for r in sb_rows]
    logo_by_team: dict[str, dict] = {}
    score_by_matchup: dict[str, dict] = {}
    for r in sb_rows:
        home_n = _team(r.get("home"))
        away_n = _team(r.get("away"))
        if home_n and away_n:
            key = f"{away_n.lower()}|{home_n.lower()}"
            score_by_matchup[key] = r
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
        home_n = str(row.get("home") or "")
        away_n = str(row.get("away") or "")
        sb_hit = score_by_matchup.get(f"{away_n.lower()}|{home_n.lower()}")
        if sb_hit:
            row["event_id"] = row.get("event_id") or row.get("id") or sb_hit.get("event_id")
            if row.get("homePoints") is None and sb_hit.get("home_score") is not None:
                row["homePoints"] = sb_hit.get("home_score")
            if row.get("awayPoints") is None and sb_hit.get("away_score") is not None:
                row["awayPoints"] = sb_hit.get("away_score")
            if not row.get("completed") and sb_hit.get("completed"):
                row["completed"] = bool(sb_hit.get("completed"))
            if sb_hit.get("status"):
                row["status"] = sb_hit.get("status")
            if sb_hit.get("status_state"):
                row["status_state"] = sb_hit.get("status_state")

        cfbd = lookup_completed_game(year, home_n, away_n, sport=sport)
        merged = merge_completed(
            cfbd,
            home_n,
            away_n,
            home_pts=row.get("homePoints"),
            away_pts=row.get("awayPoints"),
            status_completed=bool(row.get("completed")),
            sport=sport,
        )
        if merged:
            row["homePoints"] = merged.get("homePoints", row.get("homePoints"))
            row["awayPoints"] = merged.get("awayPoints", row.get("awayPoints"))
            row["completed"] = bool(merged.get("completed", row.get("completed")))
            if merged.get("homeLineScores"):
                row["homeLineScores"] = merged.get("homeLineScores")
            if merged.get("awayLineScores"):
                row["awayLineScores"] = merged.get("awayLineScores")

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
def _cached_matchup_sim(
    sport: str,
    home: str,
    away: str,
    year: int,
    week: int,
    spread: float | None,
    total: float | None,
) -> dict:
    """Monte Carlo game sim — cached per matchup so prop tabs don't re-sim."""
    return run_matchup_simulation(
        sport, home, away,
        season=year, week=week,
        market_spread=spread, market_total=total,
        live_game=None, n_sims=DEFAULT_SIMS,
    )


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
    final = game_is_final(game)
    key = _projection_key(sport, home, away, year, week)
    if st.session_state.get("pe_proj_key") == key and not final:
        return

    payload = load_matchup_odds(
        sport, home, away, year=year, week=week, game=game, completed=final,
    )
    spread, total = _resolve_lines(payload["quotes"], game)
    props = payload["props"]
    archived = payload.get("archived_game") or {}

    sim = archived.get("sim") if final and archived.get("sim") else None
    prop_map = None
    if final and archived:
        from lib.pricing_history import prop_projection_map_from_archived

        prop_map = prop_projection_map_from_archived(archived.get("props") or props)

    if sim is None:
        if final:
            sim = run_matchup_simulation(
                sport, home, away,
                season=year, week=week,
                market_spread=spread, market_total=total,
                live_game=None, n_sims=DEFAULT_SIMS,
            )
            prop_map = build_prop_projection_map(
                props, year=year, week=week, sim=sim,
                home=home, away=away, backtest=True,
            )
        else:
            sim = _cached_matchup_sim(sport, home, away, year, week, spread, total)
            prop_map = build_prop_projection_map(
                props,
                year=year,
                week=week,
                sim=sim,
                home=home,
                away=away,
            )

    st.session_state.pe_sim = sim
    st.session_state.pe_pregame_sim = sim
    st.session_state.pe_prop_projs = prop_map or {}
    st.session_state.pe_proj_key = key
    st.session_state.pe_sim_spread = spread
    st.session_state.pe_sim_total = total
    st.session_state.pop("pe_live_fp", None)


def _render_player_props(
    props: list,
    *,
    sport: str,
    year: int,
    week: int,
    projections: dict[tuple[str, str, str], float] | None = None,
    game: dict | None = None,
    sim: dict | None = None,
    home: str | None = None,
    away: str | None = None,
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
    active_tab = st.session_state.pe_prop_tab
    for i, (key, label) in enumerate(tabs[:6]):
        with cols[i]:
            if st.button(
                label,
                key=f"pe_tab_{key}",
                use_container_width=True,
                type="primary" if active_tab == key else "secondary",
            ):
                st.session_state.pe_prop_tab = key
    st.markdown("</div>", unsafe_allow_html=True)

    stat = st.session_state.pe_prop_tab
    html_out = render_player_props_table(
        props,
        year=year,
        week=week,
        stat_filter=stat,
        projections=projections,
        game=game or {},
        sim=sim,
        home=home,
        away=away,
        completed=completed,
        sport=sport,
    )
    render_html(html_out)


def main() -> None:
    apply_pricing_theme()
    st.markdown(pricing_board_css(), unsafe_allow_html=True)

    sport = get_sport()
    cfg = get_sport_config()
    ensure_live_feed(sport)

    st.markdown('<div class="bo-rz-header-row">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([2.5, 0.55, 0.55, 0.45])
    with c1:
        st.markdown('<div class="bo-hero-eyebrow">PRICING</div>', unsafe_allow_html=True)
    with c2:
        if st.button(
            "CFB",
            key="pe_cfb",
            use_container_width=True,
            type="primary" if sport == SPORT_CFB else "secondary",
        ):
            set_sport(SPORT_CFB)
            cfg_cfb = get_sport_config(SPORT_CFB)
            st.session_state.pe_year = int(cfg_cfb["default_year"])
            st.session_state.pe_week = int(cfg_cfb["default_week"])
            for key in ("pe_proj_key", "pe_prop_projs", "pe_sim", "pe_pregame_sim", "pe_live_prop_projs", "pe_live_fp"):
                st.session_state.pop(key, None)
            _slate.clear()
            _cached_matchup_sim.clear()
            fetch_live_scoreboard.clear()
            fetch_game_summary_cached.clear()
            fetch_game_plays_cached.clear()
            st.rerun()
    with c3:
        if st.button("NFL", key="pe_nfl", use_container_width=True, type="primary" if sport == SPORT_NFL else "secondary"):
            set_sport(SPORT_NFL)
            cfg_nfl = get_sport_config(SPORT_NFL)
            st.session_state.pe_year = int(cfg_nfl["default_year"])
            st.session_state.pe_week = int(cfg_nfl["default_week"])
            for key in ("pe_proj_key", "pe_prop_projs", "pe_sim", "pe_pregame_sim", "pe_live_prop_projs", "pe_live_fp"):
                st.session_state.pop(key, None)
            _slate.clear()
            _cached_matchup_sim.clear()
            fetch_live_scoreboard.clear()
            fetch_game_summary_cached.clear()
            fetch_game_plays_cached.clear()
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

    _bootstrap_projections(sport, home, away, year, week, game)

    @st.fragment(run_every=ODDS_POLL_SEC)
    def _live_odds_panel():
        fresh_game = _merge_live_scoreboard(
            dict(game), sport=sport, year=year, week=week, home=home, away=away,
        )
        live_now = _game_is_live(
            fresh_game, sport=sport, year=year, week=week, home=home, away=away,
        )
        completed = game_is_final(fresh_game)

        if live_now:
            live = _live_enriched_state(sport, year, week, home, away, fresh_game)
            fp = _live_fingerprint(live)
            if st.session_state.get("pe_live_fp") != fp:
                spread = st.session_state.get("pe_sim_spread")
                total = st.session_state.get("pe_sim_total")
                st.session_state.pe_sim = run_matchup_simulation(
                    sport, home, away,
                    season=year, week=week,
                    market_spread=spread, market_total=total,
                    live_game=live, n_sims=DEFAULT_SIMS,
                )
                if sport != SPORT_CFB:
                    payload_live = load_matchup_odds(sport, home, away, year=year, week=week)
                    pre = st.session_state.get("pe_prop_projs") or {}
                    st.session_state.pe_live_prop_projs = _live_prop_projections(
                        payload_live["props"],
                        home=home,
                        away=away,
                        pregame_map=pre,
                        live=live,
                        live_sim=st.session_state.get("pe_sim"),
                        pregame_sim=st.session_state.get("pe_pregame_sim"),
                    )
                st.session_state.pe_live_fp = fp
        else:
            live = None

        payload = load_matchup_odds(
            sport, home, away, year=year, week=week, game=fresh_game, completed=completed,
        )
        q = payload["quotes"]
        props = payload["props"]
        sim = st.session_state.get("pe_sim")
        if live_now and sport != SPORT_CFB:
            prop_projs = st.session_state.get("pe_live_prop_projs") or st.session_state.get("pe_prop_projs") or {}
        else:
            prop_projs = st.session_state.get("pe_prop_projs") or {}

        grade_game = dict(fresh_game)
        from lib.pricing_history import load_pregame_snapshot

        snap = load_pregame_snapshot(sport, year, week, home, away)
        archived = dict(snap) if snap else {}
        payload_arch = payload.get("archived_game") or {}
        for key, val in payload_arch.items():
            if val is not None and (key not in archived or not archived.get(key)):
                archived[key] = val
        if archived.get("event_id") and not grade_game.get("event_id"):
            grade_game["event_id"] = archived.get("event_id")
        if archived.get("home_score") is not None and grade_game.get("homePoints") is None:
            grade_game["homePoints"] = archived.get("home_score")
        if archived.get("away_score") is not None and grade_game.get("awayPoints") is None:
            grade_game["awayPoints"] = archived.get("away_score")
        game_completed = game_is_final(grade_game)
        if game_completed:
            grade_game["completed"] = True
            if archived.get("sim"):
                sim = archived["sim"]
            if archived.get("props"):
                from lib.pricing_history import prop_projection_map_from_archived

                prop_projs = prop_projection_map_from_archived(archived["props"])
            if payload.get("source") == "theoddsapi":
                st.caption(
                    "Historical odds from The Odds API snapshots + props cache. "
                    "Projections are frozen at pre-kickoff form (no look-ahead)."
                )
            elif not q and not props:
                st.warning(
                    "No pregame snapshot for this game. Open the pricing page before kickoff "
                    "so lines, props, and projections are saved automatically."
                )
        elif not live_now and (q or props or sim):
            from lib.pricing_history import save_pregame_snapshot

            pbp_spread_snap, pbp_total_snap = _resolve_lines(q, grade_game)
            snap_sig = _pregame_snapshot_sig(q, props, pbp_spread_snap, pbp_total_snap)
            snap_key = f"pe_snap_sig_{sport}_{year}_{week}_{home}|{away}"
            if st.session_state.get(snap_key) != snap_sig:
                save_pregame_snapshot(
                    sport,
                    year,
                    week,
                    home,
                    away,
                    game=fresh_game,
                    quotes=q,
                    props=props,
                    sim=st.session_state.get("pe_pregame_sim") or sim,
                    prop_map=prop_projs,
                )
                st.session_state[snap_key] = snap_sig
            if st.session_state.get("pe_pregame_sim") is None and sim:
                st.session_state.pe_pregame_sim = sim

        section_label("Game Markets")
        render_html(render_market_table(q, sim, game=grade_game, live=live, completed=game_completed))

        section_label("Player Props")
        _render_player_props(
            props,
            sport=sport,
            year=year,
            week=week,
            projections=prop_projs,
            game=grade_game,
            sim=sim,
            home=home,
            away=away,
            completed=game_completed,
        )

        from pricing_engine.grading import _resolve_event_id

        eid = _resolve_event_id(
            grade_game,
            home=home,
            away=away,
            sport=sport,
            year=year,
            week=week,
        ) or grade_game.get("event_id") or archived.get("event_id")
        pregame_bundle = archived if archived else None
        if not pregame_bundle and (q or props):
            pregame_bundle = {
                "home": home,
                "away": away,
                "quotes": q,
                "props": props,
                "sim": sim,
                "event_id": eid,
            }
        pbp_spread, pbp_total = _resolve_lines(q, grade_game)
        pbp_cache_key = f"pe_pbp_{eid}" if eid else ""
        pbp_plays: list = st.session_state.get(pbp_cache_key, []) if pbp_cache_key else []
        pbp_err = ""
        logo_game = enrich_row_logos(dict(grade_game))
        if eid and (live_now or game_completed):
            try:
                tick = live_poll_bucket()
                espn_plays = fetch_game_plays_cached(str(eid), sport, tick=tick)
                play_sig = _pbp_play_sig(espn_plays)
                sig_key = f"pe_pbp_sig_{eid}"
                ts_key = f"pe_pbp_ts_{eid}"
                cap_ts_key = f"pe_pbp_cap_ts_{eid}"
                now = time.time()
                prev_sig = st.session_state.get(sig_key)
                prev_ts = float(st.session_state.get(ts_key) or 0.0)
                need_render = (
                    not pbp_plays
                    or play_sig != prev_sig
                    or now - prev_ts >= _PBP_RENDER_MIN_SEC
                )
                if need_render:
                    capture = (
                        live_now
                        and play_sig != prev_sig
                        and now - float(st.session_state.get(cap_ts_key) or 0.0) >= _PBP_CAPTURE_MIN_SEC
                    )
                    summary = fetch_game_summary_cached(str(eid), sport, tick=tick)
                    fresh_pbp = load_matchup_pbp_log(
                        sport=sport,
                        year=year,
                        week=week,
                        event_id=str(eid),
                        home=home,
                        away=away,
                        quotes=q,
                        props=props,
                        spread=pbp_spread,
                        total=pbp_total,
                        pregame=pregame_bundle or archived,
                        pregame_sim=st.session_state.get("pe_pregame_sim") or archived.get("sim") or sim,
                        live=live_now,
                        completed=game_completed,
                        home_logo=logo_game.get("homeLogo") or logo_game.get("home_logo"),
                        away_logo=logo_game.get("awayLogo") or logo_game.get("away_logo"),
                        capture_snapshots=capture,
                        summary=summary,
                        plays=espn_plays,
                    )
                    if fresh_pbp:
                        pbp_plays = fresh_pbp
                        st.session_state[pbp_cache_key] = fresh_pbp
                        st.session_state[sig_key] = play_sig
                        st.session_state[ts_key] = now
                        if capture:
                            st.session_state[cap_ts_key] = now
            except Exception as exc:
                pbp_err = str(exc)
        elif not eid:
            st.caption("No ESPN event id for this game — PBP log unavailable.")
        elif not live_now and not game_completed:
            st.caption("Play-by-play log appears when the game goes live and after final.")
        if pbp_err and not pbp_plays:
            st.caption(f"PBP load issue: {pbp_err}")
        if not pbp_plays and (live_now or game_completed) and not pbp_err:
            st.caption("No spread/total/ML quotes available for the play-by-play log.")

        if game_completed and eid and not st.session_state.get(f"pe_pbp_xlsx_{eid}"):
            try:
                from lib.pbp_export import export_pbp_game_xlsx

                xlsx_path = export_pbp_game_xlsx(
                    sport=sport,
                    event_id=str(eid),
                    home=home,
                    away=away,
                    year=year,
                    week=week,
                    quotes=q,
                    pregame=pregame_bundle or archived,
                    pregame_sim=st.session_state.get("pe_pregame_sim") or archived.get("sim") or sim,
                )
                if xlsx_path:
                    st.session_state[f"pe_pbp_xlsx_{eid}"] = str(xlsx_path)
            except Exception:
                pass

        render_html(render_pbp_log_html(pbp_plays, live=live_now))

    _live_odds_panel()


if __name__ == "__main__":
    main()
