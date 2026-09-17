"""Weather Report — kickoff hourly forecast with total impact."""
from __future__ import annotations

import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import streamlit as st

from lib.app_filters import render_global_filters
from lib.display import team_abbr
from lib.espn_client import fetch_scoreboard_cached
from lib.excel_audit import export_pull
from lib.sport_context import cache_sport
from lib.matchup_board import load_matchup_board
from lib.styling import callout, section_header
from lib.team_registry import teams_match
from lib.venue_coords import coords_for_game
from lib.weather_client import fetch_hourly_forecast_cached, kickoff_hourly_slots
from lib.weather_historical import project_total_adjustment, summarize_kickoff_weather

TAB = "Weather Report"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.strftime("%m/%d")
    except (TypeError, ValueError):
        return str(iso)[:10]


def _logo(url: str | None, *, cls: str = "bo-wx-logo") -> str:
    if url and str(url).lower() not in ("nan", "none", ""):
        return f'<img class="{cls}" src="{html.escape(str(url))}" alt="" loading="lazy" />'
    return f'<span class="{cls}-fallback"></span>'


def _hour_col(slot: dict) -> str:
    temp = slot.get("temp_f")
    precip = slot.get("precip_pct")
    try:
        temp_txt = f"{int(round(float(temp)))}°"
    except (TypeError, ValueError):
        temp_txt = "—"
    try:
        precip_txt = f"{int(round(float(precip)))}%"
    except (TypeError, ValueError):
        precip_txt = "—"
    return (
        f'<div class="bo-wx-hour">'
        f'<div class="bo-wx-hour-label">{html.escape(str(slot.get("label") or ""))}</div>'
        f'<div class="bo-wx-hour-icon">{html.escape(str(slot.get("icon") or "☁"))}</div>'
        f'<div class="bo-wx-hour-temp">{temp_txt}</div>'
        f'<div class="bo-wx-hour-precip">{precip_txt}</div>'
        f"</div>"
    )


def _weather_card(game: dict, hourly: pd.DataFrame, adj: float, wx: dict | None = None) -> str:
    away = str(game.get("away") or "")
    home = str(game.get("home") or "")
    away_abbr = team_abbr(away)
    home_abbr = team_abbr(home)
    slots = kickoff_hourly_slots(hourly)
    if wx is None:
        wx = summarize_kickoff_weather(slots)
    wind = float(wx.get("wind_mph") or 0)
    date_txt = _fmt_date(str(game.get("date") or ""))
    hours = "".join(_hour_col(s) for s in slots)
    impact_cls = "neg" if adj < 0 else "pos"
    adj_txt = f"{adj:+.1f}"

    return f"""
<div class="bo-wx-card">
  <div class="bo-wx-accent"></div>
  <div class="bo-wx-left">
    <div class="bo-wx-logos">{_logo(game.get("away_logo"))}{_logo(game.get("home_logo"))}</div>
    <div class="bo-wx-matchup">{html.escape(away_abbr)} at {html.escape(home_abbr)}</div>
    <div class="bo-wx-meta">WIND {int(round(wind))} MPH · {html.escape(date_txt)}</div>
  </div>
  <div class="bo-wx-hours">{hours}</div>
  <div class="bo-wx-impact">
    <div class="bo-wx-impact-val {impact_cls}">{adj_txt}</div>
    <div class="bo-wx-impact-label">ON THE TOTAL</div>
  </div>
</div>"""


def _merge_espn(board: pd.DataFrame, espn: pd.DataFrame) -> pd.DataFrame:
    """Enrich CFBD board rows with ESPN venue city/state/logos/indoor."""
    if board.empty:
        return espn
    if espn.empty:
        return board

    rows: list[dict] = []
    for _, br in board.iterrows():
        row = br.to_dict()
        match = espn[
            espn.apply(
                lambda r: teams_match(r.get("home"), row.get("home")) and teams_match(r.get("away"), row.get("away")),
                axis=1,
            )
        ]
        if not match.empty:
            er = match.iloc[0]
            for col in ("event_id", "venue_city", "venue_state", "lat", "lon", "indoor", "home_logo", "away_logo", "date"):
                if col in er.index:
                    val = er.get(col)
                    if val is not None and str(val).lower() not in ("nan", ""):
                        row[col] = val
        rows.append(row)
    return pd.DataFrame(rows)


def _forecast_one(game: dict, year: int) -> tuple[float, str, dict] | None:
    coords = coords_for_game(game)
    if not coords:
        return None
    lat, lon = coords
    kickoff = str(game.get("date") or "")
    matchup = f"{game['away']} @ {game['home']}"
    hourly = fetch_hourly_forecast_cached(lat, lon, kickoff, matchup, TAB)
    slots = kickoff_hourly_slots(hourly)
    wx = summarize_kickoff_weather(slots)
    adj = project_total_adjustment(
        wx["wind_mph"],
        wx["temp_f"],
        wx["precip_pct"],
        year=int(year) - 1,
    )
    row = {
        "matchup": matchup,
        "Total adj": adj,
        "wind_mph": wx["wind_mph"],
        "temp_f": wx["temp_f"],
        "precip_pct": wx["precip_pct"],
    }
    card = _weather_card(game, hourly, adj, wx)
    return adj, card, row


@st.cache_data(ttl=1800, show_spinner=False)
def _weather_week_bundle(sport: str, year: int, week: int) -> tuple[list[str], list[dict]]:
    """Build full week weather cards once — disk + memory cached forecasts."""
    _ = sport
    board = load_matchup_board(cache_sport(), int(year), int(week), tab=TAB)
    espn = fetch_scoreboard_cached(week=max(int(week), 1), year=int(year), tab=TAB)
    games = _merge_espn(board, espn)
    if games.empty:
        return [], []

    outdoor = games[games.get("indoor", False) != True].copy()  # noqa: E712
    if outdoor.empty:
        outdoor = games

    card_rows: list[tuple[float, str, dict]] = []
    game_dicts = [g.to_dict() for _, g in outdoor.iterrows()]
    workers = min(8, max(2, len(game_dicts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_forecast_one, g, int(year)): g for g in game_dicts}
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception:
                continue
            if result is not None:
                card_rows.append(result)

    if not card_rows:
        return [], []

    card_rows.sort(key=lambda x: x[0], reverse=True)
    cards_html = [html for _, html, _ in card_rows]
    table_rows = [row for _, _, row in card_rows]
    return cards_html, table_rows


def render() -> None:
    year, week = render_global_filters(prefix="wx")

    section_header(
        "Weather Report",
        "Kickoff through +3 hours — how wind, temperature, and rain move the total.",
        eyebrow=f"WEEK {week}",
    )

    with st.spinner("Loading weather…"):
        cards_html, rows = _weather_week_bundle(cache_sport(), int(year), int(week))

    if not cards_html:
        board = load_matchup_board(cache_sport(), int(year), int(week), tab=TAB)
        if board.empty:
            callout("Scoreboard unavailable for this week.", "info")
        else:
            callout("Could not resolve stadium coordinates for this week's outdoor games.", "info")
        return

    export_pull("derived_weather_totals", pd.DataFrame(rows).sort_values("Total adj", ascending=False), tab=TAB, origin="computed")
    st.markdown(f'<div class="bo-wx-stack">{"".join(cards_html)}</div>', unsafe_allow_html=True)
    st.caption(f"{len(cards_html)} outdoor games · sorted by total impact · Week {week}")
