"""Live ESPN college football — scoreboard, plays, situation, box stats."""
from __future__ import annotations

import re
import time
from typing import Any

import streamlit as st

from .espn_client import SESSION, fetch_game_summary, open_close_lines_from_summary
from .sport_context import espn_core_url, espn_site_url
from .team_logos import team_logo_url
from .team_registry import teams_match


def _get(url: str, timeout: int = 30) -> dict[str, Any]:
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _compact_play(play: dict[str, Any]) -> dict[str, Any]:
    period_num = (play.get("period") or {}).get("number") if isinstance(play.get("period"), dict) else play.get("period")
    return {
        "id": play.get("id"),
        "text": play.get("text") or play.get("alternativeText") or play.get("shortText") or "",
        "type": (play.get("type") or {}).get("text") or "",
        "period": period_num,
        "clock": (play.get("clock") or {}).get("displayValue") if isinstance(play.get("clock"), dict) else play.get("clock"),
        "scoringPlay": bool(play.get("scoringPlay")),
        "scoreValue": int(play.get("scoreValue") or 0),
        "awayScore": play.get("awayScore"),
        "homeScore": play.get("homeScore"),
        "down": (play.get("start") or {}).get("down") or play.get("down"),
        "distance": (play.get("start") or {}).get("distance") or play.get("distance"),
        "yardLine": (play.get("start") or {}).get("yardLine") or play.get("yardLine"),
        "possession": (play.get("start") or {}).get("possessionText") or play.get("possessionText") or "",
    }


def fetch_game_plays(event_id: str, *, sport: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
    url = f"{espn_core_url(sport)}/events/{event_id}/competitions/{event_id}/plays?limit={limit}"
    data = _get(url)
    items = list(data.get("items") or [])
    page_count = int(data.get("pageCount") or 1)
    for page in range(2, min(page_count, 10) + 1):
        page_data = _get(f"{url}&page={page}")
        items.extend(page_data.get("items") or [])
    return [_compact_play(p) for p in items if _compact_play(p).get("text")]


def live_poll_bucket(ttl: int = 8) -> int:
    """Time bucket for short-lived Streamlit caches during live polling."""
    return int(time.time()) // max(1, int(ttl))


@st.cache_data(ttl=8, show_spinner=False)
def fetch_game_summary_cached(
    event_id: str,
    sport: str,
    tab: str | None = None,
    tick: int = 0,
) -> dict[str, Any]:
    _ = tick
    return fetch_game_summary(event_id, sport=sport, tab=tab)


@st.cache_data(ttl=8, show_spinner=False)
def fetch_game_plays_cached(
    event_id: str,
    sport: str,
    limit: int = 400,
    tick: int = 0,
) -> list[dict[str, Any]]:
    _ = tick
    return fetch_game_plays(event_id, sport=sport, limit=limit)


def _plays_from_drives(summary: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    drives = summary.get("drives") or {}
    for key in ("previous", "current"):
        block = drives.get(key) or []
        if not isinstance(block, list):
            block = [block] if block else []
        for drive in block:
            for play in drive.get("plays") or []:
                row = _compact_play(play)
                if row.get("text"):
                    out.append(row)
    return out


def _parse_clock_seconds(clock: str | None) -> int | None:
    if not clock:
        return None
    m = re.match(r"(\d+):(\d+)", str(clock).strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def derive_situation(summary: dict[str, Any], game: dict[str, Any], plays: list[dict[str, Any]]) -> dict[str, Any]:
    comp = (summary.get("header") or {}).get("competitions") or [{}]
    comp = comp[0] if comp else {}
    sit = comp.get("situation") or summary.get("situation") or {}
    latest = plays[-1] if plays else {}
    status = game.get("status") or {}
    period = int(sit.get("period") or 0) or int(latest.get("period") or 0)
    if not period:
        m = re.search(r"\b([1-4])\b", str(status.get("detail") or ""))
        period = int(m.group(1)) if m else 1
    clock = (
        (sit.get("lastPlay") or {}).get("clock", {}).get("displayValue")
        or latest.get("clock")
        or status.get("displayClock")
        or ""
    )
    down_dist = sit.get("shortDownDistanceText") or ""
    if not down_dist and latest.get("down"):
        down_dist = f"{latest.get('down')} & {latest.get('distance')}"
    possession = sit.get("possessionText") or latest.get("possession") or ""
    yard_line = sit.get("yardLine") or latest.get("yardLine")
    return {
        "period": period,
        "clock": clock,
        "clockSeconds": _parse_clock_seconds(clock),
        "downDistance": down_dist,
        "possession": possession,
        "yardLine": yard_line,
        "isRedZone": bool(sit.get("isRedZone")),
    }


def _parse_stat_number(stat: dict[str, Any]) -> float | None:
    """Parse ESPN flat boxscore stat (displayValue or numeric value)."""
    val = stat.get("value")
    if isinstance(val, (int, float)) and val != "-":
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
    disp = str(stat.get("displayValue") or "").replace(",", "")
    if not disp or disp == "-":
        return None
    m = re.search(r"-?\d+\.?\d*", disp)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _team_yards(summary: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    """Return away/home yard buckets: total, pass, rush."""
    away: dict[str, float] = {"total": 0.0, "pass": 0.0, "rush": 0.0}
    home: dict[str, float] = {"total": 0.0, "pass": 0.0, "rush": 0.0}
    for entry in (summary.get("boxscore") or {}).get("teams") or []:
        bucket = away if entry.get("homeAway") == "away" else home
        for stat in entry.get("statistics") or []:
            name = str(stat.get("name") or "").lower()
            num = _parse_stat_number(stat)
            if num is None:
                continue
            if name == "totalyards":
                bucket["total"] = num
            elif name in ("netpassingyards", "passingyards"):
                bucket["pass"] = num
            elif name == "rushingyards":
                bucket["rush"] = num
        if bucket["total"] <= 0 and (bucket["pass"] or bucket["rush"]):
            bucket["total"] = bucket["pass"] + bucket["rush"]
    return away, home


def _normalize_win_pct(raw: Any) -> float | None:
    """ESPN returns 0–1 fraction; some feeds use 0–100."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))


def latest_win_probability(summary: dict[str, Any]) -> dict[str, float | None]:
    wp = summary.get("winprobability") or []
    if not wp:
        return {"homeWinPct": None, "awayWinPct": None}
    last = wp[-1]
    home_f = _normalize_win_pct(last.get("homeWinPercentage"))
    away_f = (100.0 - home_f) if home_f is not None else None
    return {"homeWinPct": home_f, "awayWinPct": away_f}


def _normalize_team_color(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "#1a1a22"
    if s.startswith("#"):
        return s if len(s) >= 4 else "#1a1a22"
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return f"#{s}"
    return "#1a1a22"


def _compact_game_from_event(ev: dict[str, Any]) -> dict[str, Any]:
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = away = {}
    for c in competitors:
        if c.get("homeAway") == "home":
            home = c
        else:
            away = c
    status_type = (ev.get("status") or comp.get("status") or {}).get("type") or {}
    ht = home.get("team") or {}
    at = away.get("team") or {}
    try:
        home_score = int(home.get("score")) if home.get("score") not in (None, "") else 0
    except (TypeError, ValueError):
        home_score = 0
    try:
        away_score = int(away.get("score")) if away.get("score") not in (None, "") else 0
    except (TypeError, ValueError):
        away_score = 0
    state = status_type.get("state") or "pre"
    period = status_type.get("period")
    if period is None:
        m = re.search(r"\b([1-4])\b", str(status_type.get("detail") or status_type.get("shortDetail") or ""))
        period = int(m.group(1)) if m else None
    season = ev.get("season") or {}
    return {
        "event_id": str(ev.get("id")),
        "name": ev.get("shortName") or ev.get("name") or "",
        "date": ev.get("date"),
        "week": (ev.get("week") or {}).get("number"),
        "season_year": season.get("year"),
        "status": {
            "state": state,
            "completed": bool(status_type.get("completed") or state == "post"),
            "detail": status_type.get("detail") or status_type.get("shortDetail") or "",
            "displayClock": status_type.get("displayClock") or "",
            "period": period,
        },
        "venue": (comp.get("venue") or {}).get("fullName"),
        "home": {
            "name": ht.get("displayName") or "",
            "abbr": ht.get("abbreviation") or "",
            "logo": team_logo_url(ht.get("displayName") or "", fallback=str(ht.get("logo") or "")),
            "color": _normalize_team_color(ht.get("color")),
            "score": home_score,
        },
        "away": {
            "name": at.get("displayName") or "",
            "abbr": at.get("abbreviation") or "",
            "logo": team_logo_url(at.get("displayName") or "", fallback=str(at.get("logo") or "")),
            "color": _normalize_team_color(at.get("color")),
            "score": away_score,
        },
    }


def fetch_scoreboard_events(
    sport: str,
    *,
    week: int | None = None,
    date: str | None = None,
) -> list[dict[str, Any]]:
    """Raw ESPN scoreboard events — week filter, calendar date (YYYYMMDD), or both."""
    params: list[str] = []
    if date:
        params.append(f"dates={date}")
    if week is not None:
        params.append(f"week={week}")
    qs = "&".join(params)
    url = f"{espn_site_url(sport)}/scoreboard" + (f"?{qs}" if qs else "")
    data = _get(url)
    return [_compact_game_from_event(ev) for ev in data.get("events") or []]


@st.cache_data(ttl=5, show_spinner=False)
def fetch_live_scoreboard(sport: str, week: int, year: int, tab: str | None = None) -> list[dict[str, Any]]:
    _ = tab, year
    return fetch_scoreboard_events(sport, week=week)


def discover_live_games(
    sport: str,
    *,
    year: int,
    default_week: int,
) -> list[dict[str, Any]]:
    """
    Find all in-progress games for CFB or NFL.
    Scans today's board plus default week ±1 so CI does not miss live games
    when CFB_WEEK / NFL_WEEK env vars are slightly off.
    """
    from datetime import datetime, timezone

    from .sport_context import get_sport_config

    cfg = get_sport_config(sport)
    min_w = int(cfg.get("min_week", 1))
    max_w = int(cfg.get("max_week", 18))
    weeks: set[int] = set()
    for w in (default_week - 1, default_week, default_week + 1):
        if min_w <= int(w) <= max_w:
            weeks.add(int(w))

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    by_id: dict[str, dict[str, Any]] = {}
    for w in sorted(weeks):
        for g in fetch_scoreboard_events(sport, week=w):
            eid = str(g.get("event_id") or "")
            if eid:
                by_id[eid] = g
    for g in fetch_scoreboard_events(sport, date=today):
        eid = str(g.get("event_id") or "")
        if eid:
            by_id[eid] = g

    live_states = {"in", "halftime"}
    out: list[dict[str, Any]] = []
    for g in by_id.values():
        status = g.get("status") or {}
        state = str(status.get("state") or "").lower() if isinstance(status, dict) else str(status).lower()
        if state not in live_states:
            continue
        home = (g.get("home") or {}).get("name") if isinstance(g.get("home"), dict) else g.get("home")
        away = (g.get("away") or {}).get("name") if isinstance(g.get("away"), dict) else g.get("away")
        game_week = g.get("week")
        try:
            game_week = int(game_week) if game_week is not None else int(default_week)
        except (TypeError, ValueError):
            game_week = int(default_week)
        game_year = g.get("season_year") or year
        try:
            game_year = int(game_year)
        except (TypeError, ValueError):
            game_year = int(year)
        out.append(
            {
                "event_id": str(g.get("event_id") or ""),
                "home": str(home or ""),
                "away": str(away or ""),
                "status": state,
                "week": game_week,
                "year": game_year,
            }
        )
    return out


def _pregame_lines(summary: dict[str, Any], home_name: str, slate_rows: list[dict] | None) -> dict[str, float | None]:
    lines = open_close_lines_from_summary(summary, home_name)
    spread = lines.get("spread") or lines.get("closeSpread")
    total = lines.get("total") or lines.get("closeTotal")
    if spread is not None and total is not None:
        return {"spread": float(spread), "total": float(total), "source": "espn"}
    if slate_rows:
        for row in slate_rows:
            if str(row.get("market")) == "Spread" and row.get("line") is not None:
                try:
                    spread = float(row.get("line"))
                except (TypeError, ValueError):
                    pass
            if str(row.get("market")) == "Total" and row.get("line") is not None:
                try:
                    total = float(row.get("line"))
                except (TypeError, ValueError):
                    pass
        if spread is not None and total is not None:
            return {"spread": float(spread), "total": float(total), "source": "slate"}
    return {"spread": spread, "total": total, "source": "partial"}


@st.cache_data(ttl=10, show_spinner=False)
def fetch_live_game_detail(
    sport: str,
    event_id: str,
    home_name: str,
    away_name: str,
    slate_json: str = "",
    week: int = 1,
    year: int = 2026,
    tab: str | None = None,
    tick: int = 0,
) -> dict[str, Any]:
    import json

    _ = tick
    summary = fetch_game_summary_cached(event_id, sport, tab=tab, tick=tick)
    plays = fetch_game_plays_cached(event_id, sport, limit=400, tick=tick)
    if not plays:
        plays = _plays_from_drives(summary)
    slate_rows = json.loads(slate_json) if slate_json else None
    board = fetch_live_scoreboard(sport, week=week, year=year, tab=tab)
    game = next((g for g in board if g["event_id"] == str(event_id)), None)
    if not game:
        comp = (summary.get("header") or {}).get("competitions") or [{}]
        comp = comp[0] if comp else {}
        competitors = comp.get("competitors") or []
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
        ht = home_c.get("team") or {}
        at = away_c.get("team") or {}
        game = {
            "event_id": str(event_id),
            "home": {
                "name": ht.get("displayName") or home_name,
                "abbr": ht.get("abbreviation") or "",
                "logo": team_logo_url(ht.get("displayName") or home_name, fallback=str(ht.get("logo") or "")),
                "color": _normalize_team_color(ht.get("color")),
                "score": int(home_c.get("score") or 0),
            },
            "away": {
                "name": at.get("displayName") or away_name,
                "abbr": at.get("abbreviation") or "",
                "logo": team_logo_url(at.get("displayName") or away_name, fallback=str(at.get("logo") or "")),
                "color": _normalize_team_color(at.get("color")),
                "score": int(away_c.get("score") or 0),
            },
            "status": {"state": "in", "detail": "", "completed": False},
        }
    situation = derive_situation(summary, game, plays)
    away_yards, home_yards = _team_yards(summary)
    pregame = _pregame_lines(summary, game["home"]["name"], slate_rows)
    return {
        "game": game,
        "plays": plays,
        "situation": situation,
        "awayYards": away_yards,
        "homeYards": home_yards,
        "winProbability": latest_win_probability(summary),
        "pregame": pregame,
    }


def slate_rows_for_matchup(slate_df, home: str, away: str) -> list[dict]:
    if slate_df is None or slate_df.empty:
        return []
    mask = slate_df.apply(
        lambda r: teams_match(str(r.get("home") or ""), home) and teams_match(str(r.get("away") or ""), away),
        axis=1,
    )
    return slate_df[mask].to_dict("records")
