"""Weather card HTML for a single game — used in game details."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any

import pandas as pd

from lib.display import team_abbr
from lib.venue_coords import coords_for_game
from lib.weather_client import fetch_hourly_forecast_cached, kickoff_hourly_slots
from lib.weather_historical import project_total_adjustment, summarize_kickoff_weather


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


def weather_card_html(game: dict[str, Any], hourly: pd.DataFrame, adj: float, wx: dict | None = None) -> str:
    away = str(game.get("away") or "")
    home = str(game.get("home") or "")
    away_abbr = team_abbr(away, year=int(game.get("year") or 2026))
    home_abbr = team_abbr(home, year=int(game.get("year") or 2026))
    slots = kickoff_hourly_slots(hourly)
    if wx is None:
        wx = summarize_kickoff_weather(slots)
    wind = float(wx.get("wind_mph") or 0)
    date_txt = _fmt_date(str(game.get("date") or ""))
    hours = "".join(_hour_col(s) for s in slots)
    impact_cls = "neg" if adj < 0 else "pos"
    adj_txt = f"{adj:+.1f}"

    return f"""
<div class="bo-wx-card bo-mu-weather-card">
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


def _game_dict_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    meta = detail.get("meta") or {}
    logos = detail.get("logos") or {}
    return {
        "home": meta.get("home"),
        "away": meta.get("away"),
        "date": meta.get("startDate"),
        "venue": meta.get("venue"),
        "venue_city": meta.get("venue_city"),
        "venue_state": meta.get("venue_state"),
        "indoor": meta.get("indoor"),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "home_logo": logos.get("home"),
        "away_logo": logos.get("away"),
        "year": meta.get("year"),
    }


def render_game_weather_html(detail: dict[str, Any], *, season_year: int, tab: str = "Game Detail") -> str:
    """Kickoff-window forecast + total adjustment for one game."""
    game = _game_dict_from_detail(detail)
    if game.get("indoor") is True:
        return (
            '<section class="bo-mu-weather">'
            '<h2 class="bo-section-title">Weather · kickoff window</h2>'
            '<p class="bo-mu-muted">Indoor venue — weather does not move the total.</p>'
            "</section>"
        )

    coords = coords_for_game(game)
    if not coords:
        return ""

    lat, lon = coords
    kickoff = str(game.get("date") or "")
    matchup = f"{game.get('away')} @ {game.get('home')}"
    try:
        hourly = fetch_hourly_forecast_cached(lat, lon, kickoff, matchup, tab)
    except Exception:
        return ""

    if hourly.empty:
        return ""

    slots = kickoff_hourly_slots(hourly)
    wx = summarize_kickoff_weather(slots)
    adj = project_total_adjustment(
        wx["wind_mph"],
        wx["temp_f"],
        wx["precip_pct"],
        year=int(season_year) - 1,
    )
    card = weather_card_html(game, hourly, adj, wx)
    return (
        '<section class="bo-mu-weather">'
        '<h2 class="bo-section-title">Weather · kickoff through +3 hours</h2>'
        f"{card}"
        "</section>"
    )
