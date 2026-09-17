"""Weekly Brief data — slate lean, weather, thread bullets from existing feeds."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from lib.config import DEFAULT_WEEK, DEFAULT_YEAR
from lib.display import _build_game_rows, team_abbr
from lib.espn_client import fetch_scoreboard
from lib.sport_context import cache_sport, get_sport_config
from lib.slate_loader import load_slate_df
from lib.weather_client import fetch_hourly_forecast, forecast_summary, weather_total_adjustment

# Reuse outdoor venue coords from weather view
VENUE_COORDS = {
    "hawaii": (21.3722, -157.9298),
    "oklahoma": (35.2058, -97.4424),
    "texas": (30.2836, -97.7325),
    "autzen": (44.0583, -123.0685),
    "beaver": (44.5646, -123.2620),
}


def _coords(row: pd.Series) -> tuple[float, float] | None:
    lat, lon = row.get("lat"), row.get("lon")
    if lat and lon:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    venue = str(row.get("venue") or "").lower()
    for key, coords in VENUE_COORDS.items():
        if key in venue:
            return coords
    city = str(row.get("venue_city") or "").lower()
    if "austin" in city:
        return 30.2836, -97.7325
    if "honolulu" in city:
        return 21.3722, -157.9298
    return None


def _abbr_lookup(year: int, week: int) -> dict[str, str]:
    sb = fetch_scoreboard(week=week, year=year)
    lookup: dict[str, str] = {}
    for _, row in sb.iterrows():
        for side in ("home", "away"):
            name = str(row.get(side) or "")
            abbr = str(row.get(f"{side}_abbr") or "")
            if name and abbr:
                lookup[name] = abbr
    return lookup


def _kickoff_label(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = pd.to_datetime(iso)
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        dt = dt.tz_convert("America/Chicago")
        return dt.strftime("%I:%M %p CT").lstrip("0")
    except Exception:
        return str(iso)[:16]


def brief_meta() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_thread_bullets(year: int, week: int) -> list[dict[str, str]]:
    df = load_slate_df(cache_sport(), year, week)
    if df.empty:
        return [
            {"title": "Slate loading", "body": "Projections and lines populate when the Week slate is live."},
        ]

    games = df[df["category"].eq("game")].drop_duplicates(subset=["matchup"], keep="first")
    n_games = int(games["matchup"].nunique()) if "matchup" in games.columns else len(games)
    plays = df[df.get("play", False) == True] if "play" in df.columns else pd.DataFrame()  # noqa: E712
    n_plays = len(plays)

    totals = df[df["group"].eq("total") & df["market"].eq("Total")].copy()
    lean_n = 0
    if not totals.empty and "modelProj" in totals.columns and "line" in totals.columns:
        for _, r in totals.iterrows():
            try:
                if abs(float(r["modelProj"]) - float(r["line"])) >= 1.5:
                    lean_n += 1
            except (TypeError, ValueError):
                pass

    props = df[df["category"].eq("prop")] if "category" in df.columns else pd.DataFrame()
    n_props = len(props)

    return [
        {
            "title": f"Week {week} Slate Lean",
            "body": f"{lean_n} games where our total disagrees with the posted number by 1.5+ points.",
        },
        {
            "title": "Steam Watch",
            "body": "Pinnacle spread open→close travel is on the Market Moves board — sorted by kickoff.",
        },
        {
            "title": "Injury Room",
            "body": "Latest player availability reports are updated from ESPN across the slate.",
        },
        {
            "title": "+EV Board",
            "body": f"{n_plays} flagged plays on {n_games} games · {n_props} player props scanned.",
        },
    ]


def build_weather_rows(year: int, week: int, *, limit: int = 6) -> list[dict[str, Any]]:
    board = fetch_scoreboard(week=week, year=year)
    if board.empty:
        return []

    outdoor = board[board["indoor"] != True].copy()  # noqa: E712
    if outdoor.empty:
        outdoor = board

    rows: list[dict[str, Any]] = []
    for _, game in outdoor.iterrows():
        coords = _coords(game)
        if not coords:
            continue
        lat, lon = coords
        hourly = fetch_hourly_forecast(
            lat,
            lon,
            str(game.get("date")),
            matchup=f"{game['away']} @ {game['home']}",
        )
        summary = forecast_summary(hourly)
        wind = summary.get("wind_mph") or 0
        temp = summary.get("temp_f") or 70
        precip = summary.get("precip_pct") or 0
        adj = weather_total_adjustment(wind, temp, precip)
        away = str(game.get("away") or "")
        home = str(game.get("home") or "")
        abbrs = _abbr_lookup(year, week)
        rows.append(
            {
                "away": away,
                "home": home,
                "away_abbr": team_abbr(away, abbrs),
                "home_abbr": team_abbr(home, abbrs),
                "away_logo": game.get("away_logo"),
                "home_logo": game.get("home_logo"),
                "venue": str(game.get("venue") or ""),
                "kickoff": _kickoff_label(str(game.get("date") or "")),
                "wind_mph": round(float(wind), 0),
                "temp_f": round(float(temp), 0),
                "precip_pct": round(float(precip), 0),
                "total_adj": adj,
            }
        )

    if not rows:
        return []
    out = sorted(rows, key=lambda r: abs(float(r["total_adj"])), reverse=True)
    return out[:limit]


def build_slate_lean_rows(year: int, week: int, *, limit: int = 10) -> list[dict[str, Any]]:
    df = load_slate_df(cache_sport(), year, week)
    if df.empty:
        return []

    totals = df[df["group"].eq("total") & df["market"].eq("Total")].copy()
    if totals.empty:
        return []

    abbrs = _abbr_lookup(year, week)
    games = _build_game_rows(totals, kind="total", week=week, abbr_lookup=abbrs)
    scored: list[dict[str, Any]] = []
    for g in games:
        diff = g.get("diff")
        if diff is None:
            continue
        try:
            diff_f = float(diff)
        except (TypeError, ValueError):
            continue
        if abs(diff_f) < 0.5:
            continue
        scored.append({**g, "diff": diff_f})

    scored.sort(key=lambda x: abs(float(x["diff"])), reverse=True)
    return scored[:limit]


def build_games_to_watch(year: int, week: int, *, limit: int = 12) -> list[dict[str, Any]]:
    df = load_slate_df(cache_sport(), year, week)
    if df.empty:
        return []

    plays = df[df["play"] == True].copy() if "play" in df.columns else pd.DataFrame()  # noqa: E712
    if plays.empty:
        plays = df[df["category"].eq("game")].copy()

    if "roi" in plays.columns:
        plays = plays.sort_values("roi", ascending=False, na_position="last")
    elif "edge" in plays.columns:
        plays = plays.sort_values("edge", ascending=False, na_position="last")

    abbrs = _abbr_lookup(year, week)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, r in plays.iterrows():
        matchup = str(r.get("matchup") or "")
        if not matchup or matchup in seen:
            continue
        seen.add(matchup)
        away = str(r.get("away") or "")
        home = str(r.get("home") or "")
        rows.append(
            {
                "matchup": matchup,
                "away_abbr": team_abbr(away, abbrs),
                "home_abbr": team_abbr(home, abbrs),
                "away_logo": r.get("awayLogo"),
                "home_logo": r.get("homeLogo"),
                "market": str(r.get("market") or r.get("selection") or "Game"),
                "edge": r.get("edgeDisplay") or r.get("edge"),
                "play": bool(r.get("play")),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def build_fantasy_chart_rows(year: int, week: int, *, limit: int = 15) -> list[dict[str, Any]]:
    df = load_slate_df(cache_sport(), year, week)
    if df.empty:
        return []

    props = df[df["category"].eq("prop")].copy() if "category" in df.columns else pd.DataFrame()
    if props.empty:
        return []

    sort_col = "edgeDisplay" if "edgeDisplay" in props.columns else "edge"
    if sort_col in props.columns:
        props = props.sort_values(sort_col, ascending=False, na_position="last")

    rows: list[dict[str, Any]] = []
    for _, r in props.head(limit).iterrows():
        rows.append(
            {
                "player": str(r.get("player") or r.get("selection") or ""),
                "team": str(r.get("team") or ""),
                "prop": str(r.get("market") or r.get("selection") or ""),
                "edge": r.get("edgeDisplay") or r.get("edge"),
                "price": r.get("price"),
                "play": bool(r.get("play")),
            }
        )
    return rows


def brief_headline(year: int, week: int) -> tuple[str, str]:
    df = load_slate_df(cache_sport(), year, week)
    n = int(df["matchup"].nunique()) if not df.empty and "matchup" in df.columns else 0
    sport_label = get_sport_config()["label"]
    return (
        f"{sport_label} Week {week}: {n} games, every number checked",
        "Model totals, weather, steam, and +EV props — pulled from your live slate.",
    )
