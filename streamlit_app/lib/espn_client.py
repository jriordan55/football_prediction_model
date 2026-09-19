"""ESPN college football — injuries, scoreboard, venues, athlete logs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from .excel_audit import export_pull

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
    }
)


def _site_url() -> str:
    from .sport_context import espn_site_url

    return espn_site_url()


def _core_url() -> str:
    from .sport_context import espn_core_url

    return espn_core_url()


def _session_headers() -> dict[str, str]:
    from .sport_context import get_sport_config

    cfg = get_sport_config()
    return {"Referer": str(cfg["espn_referer"])}

NON_INJURY = {"active", "", "probable"}

_SKIP_BROADCAST = frozenset({"eradm", "espn+", "espn plus", "streaming"})


def _broadcast_from_comp(comp: dict[str, Any]) -> tuple[str, str]:
    """Return (network_short_name, logo_url) from ESPN competition broadcast data."""
    best_name = ""
    best_logo = ""
    best_rank = -1

    def consider(name: str, logo: str, *, national: bool, is_tv: bool) -> None:
        nonlocal best_name, best_logo, best_rank
        n = str(name or "").strip()
        if not n:
            return
        low = n.lower()
        if low in _SKIP_BROADCAST or any(x in low for x in _SKIP_BROADCAST):
            return
        rank = (2 if national else 0) + (1 if is_tv else 0)
        if rank > best_rank or (rank == best_rank and not best_logo and logo):
            best_rank = rank
            best_name = n
            best_logo = str(logo or "").strip()

    for geo in comp.get("geoBroadcasts") or []:
        media = geo.get("media") or {}
        name = str(media.get("shortName") or media.get("name") or "").strip()
        logo = str(media.get("logo") or media.get("darkLogo") or "").strip()
        market = geo.get("market") or {}
        geo_type = geo.get("type") or {}
        national = str(market.get("type") or "").lower() == "national"
        is_tv = str(geo_type.get("shortName") or "").upper() == "TV"
        consider(name, logo, national=national, is_tv=is_tv)

    if best_name:
        return best_name, best_logo

    for bc in comp.get("broadcasts") or []:
        names = bc.get("names") or []
        if names:
            return str(names[0]).strip(), ""
    return "", ""


def _parse_scoreboard_event(ev: dict[str, Any]) -> dict[str, Any]:
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = away = {}
    for c in competitors:
        if c.get("homeAway") == "home":
            home = c
        else:
            away = c
    venue = comp.get("venue") or {}
    status_type = (ev.get("status") or {}).get("type") or {}
    home_score = home.get("score")
    away_score = away.get("score")
    try:
        home_pts = int(home_score) if home_score not in (None, "") else None
    except (TypeError, ValueError):
        home_pts = None
    try:
        away_pts = int(away_score) if away_score not in (None, "") else None
    except (TypeError, ValueError):
        away_pts = None
    completed = status_type.get("completed") or (
        home_pts is not None and away_pts is not None and status_type.get("state") == "post"
    )
    broadcast, broadcast_logo = _broadcast_from_comp(comp)
    return {
        "event_id": ev.get("id"),
        "name": ev.get("name"),
        "date": ev.get("date"),
        "week": (ev.get("week") or {}).get("number"),
        "home": (home.get("team") or {}).get("displayName"),
        "away": (away.get("team") or {}).get("displayName"),
        "home_abbr": (home.get("team") or {}).get("abbreviation"),
        "away_abbr": (away.get("team") or {}).get("abbreviation"),
        "home_logo": (home.get("team") or {}).get("logo"),
        "away_logo": (away.get("team") or {}).get("logo"),
        "home_score": home_pts,
        "away_score": away_pts,
        "completed": bool(completed),
        "venue": venue.get("fullName"),
        "venue_city": (venue.get("address") or {}).get("city"),
        "venue_state": (venue.get("address") or {}).get("state"),
        "lat": venue.get("address", {}).get("latitude") or venue.get("latitude"),
        "lon": venue.get("address", {}).get("longitude") or venue.get("longitude"),
        "indoor": venue.get("indoor"),
        "status": status_type.get("description"),
        "status_state": status_type.get("state"),
        "broadcast": broadcast,
        "broadcast_logo": broadcast_logo,
    }


def _get(url: str, timeout: int = 30) -> dict:
    r = SESSION.get(url, timeout=timeout, headers=_session_headers())
    r.raise_for_status()
    return r.json()


def fetch_league_injuries(tab: str | None = None) -> pd.DataFrame:
    """League-wide injury report — Covers.com primary, OpticOdds/ESPN fallback."""
    from .injury_loader import fetch_league_injuries as _load

    return _load(tab=tab)


def fetch_scoreboard(
    week: int | None = None,
    year: int | None = None,
    tab: str | None = None,
    *,
    sport: str | None = None,
) -> pd.DataFrame:
    from .sport_context import espn_site_url

    params = []
    if year:
        params.append(f"dates={year}")
    if week is not None:
        params.append(f"week={week}")
    qs = "&".join(params)
    url = f"{espn_site_url(sport)}/scoreboard" + (f"?{qs}" if qs else "")
    data = _get(url)
    rows: list[dict] = []
    for ev in data.get("events") or []:
        rows.append(_parse_scoreboard_event(ev))
    df = pd.DataFrame(rows)
    export_pull("espn_scoreboard", df, tab=tab, origin="live", extra={"week": week, "year": year})
    return df


def fetch_game_summary(event_id: str, tab: str | None = None, *, sport: str | None = None) -> dict[str, Any]:
    from .sport_context import espn_site_url

    data = _get(f"{espn_site_url(sport)}/summary?event={event_id}")
    export_pull("espn_game_summary", pd.DataFrame([{"event_id": event_id}]), tab=tab, origin="live")
    return data


def _parse_espn_line(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).strip().lstrip("oOuU")
    try:
        return round(float(s) * 2) / 2
    except (TypeError, ValueError):
        return None


def open_close_lines_from_summary(summary: dict[str, Any], home: str) -> dict[str, Any]:
    """Opening and closing spread/total from ESPN pickcenter nested odds."""
    picks = summary.get("pickcenter") or []
    if not picks:
        return {"spread": None, "total": None}
    pc = picks[0]
    ps = pc.get("pointSpread") or {}
    home_ps = ps.get("home") or {}
    open_spread = _parse_espn_line((home_ps.get("open") or {}).get("line"))
    close_spread = _parse_espn_line((home_ps.get("close") or {}).get("line"))

    tot = pc.get("total") or {}
    over = tot.get("over") or {}
    open_total = _parse_espn_line((over.get("open") or {}).get("line"))
    close_total = _parse_espn_line((over.get("close") or {}).get("line"))

    return {
        "spread": close_spread,
        "total": close_total,
        "openSpread": open_spread,
        "closeSpread": close_spread,
        "openTotal": open_total,
        "closeTotal": close_total,
        "book": (pc.get("provider") or {}).get("name"),
    }


def closing_lines_from_summary(summary: dict[str, Any], home: str) -> dict[str, Any]:
    """Extract closing spread/total from ESPN pickcenter."""
    return open_close_lines_from_summary(summary, home)


def fetch_scoreboard_season(
    year: int,
    tab: str | None = None,
    *,
    sport: str | None = None,
) -> pd.DataFrame:
    """Full-season scoreboard — used to locate Week 0 games ESPN tags as week 1."""
    from .sport_context import espn_site_url

    url = f"{espn_site_url(sport)}/scoreboard?dates={year}&limit=400"
    data = _get(url)
    rows: list[dict] = []
    for ev in data.get("events") or []:
        rows.append(_parse_scoreboard_event(ev))
    df = pd.DataFrame(rows)
    export_pull("espn_scoreboard_season", df, tab=tab, origin="live", extra={"year": year})
    return df


def fetch_scoreboard_season_cached(
    year: int,
    tab: str | None = None,
    *,
    sport: str | None = None,
) -> pd.DataFrame:
    """Cached full-season scoreboard — Week 0 only."""
    from .sport_context import cache_sport, resolve_sport

    return _fetch_scoreboard_season_cached_impl(resolve_sport(sport or cache_sport()), year, tab)


try:
    import streamlit as st

    @st.cache_data(ttl=600, show_spinner=False)
    def _fetch_scoreboard_season_cached_impl(sport: str, year: int, tab: str | None) -> pd.DataFrame:
        return fetch_scoreboard_season(year, tab=tab, sport=sport)

except Exception:

    def _fetch_scoreboard_season_cached_impl(sport: str, year: int, tab: str | None) -> pd.DataFrame:
        return fetch_scoreboard_season(year, tab=tab, sport=sport)


def injury_summary(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {"listed": 0, "unavailable": 0, "questionable": 0, "teams": 0}
    status = df["status"].astype(str).str.upper()
    unavailable = status.str.contains("OUT|IR|INJURED|DOUBTFUL|SUSPEND", na=False).sum()
    questionable = status.str.contains("QUESTION|DAY|DOUBT", na=False).sum()
    return {
        "listed": len(df),
        "unavailable": int(unavailable),
        "questionable": int(questionable),
        "teams": int(df["team"].nunique()),
    }


def fetch_scoreboard_cached(
    week: int | None = None,
    year: int | None = None,
    tab: str | None = None,
    *,
    sport: str | None = None,
) -> pd.DataFrame:
    """Cached ESPN scoreboard — avoids repeated HTTP on every tab."""
    from .sport_context import cache_sport, resolve_sport

    return _fetch_scoreboard_cached_impl(resolve_sport(sport or cache_sport()), week, year, tab)


try:
    import streamlit as st

    @st.cache_data(ttl=600, show_spinner=False)
    def _fetch_scoreboard_cached_impl(
        sport: str,
        week: int | None,
        year: int | None,
        tab: str | None,
    ) -> pd.DataFrame:
        return fetch_scoreboard(week=week, year=year, tab=tab, sport=sport)

except Exception:

    def _fetch_scoreboard_cached_impl(
        sport: str,
        week: int | None,
        year: int | None,
        tab: str | None,
    ) -> pd.DataFrame:
        return fetch_scoreboard(week=week, year=year, tab=tab, sport=sport)
