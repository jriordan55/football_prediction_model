"""Market Moves — opener to close from cfbfastR / nflfastR betting lines."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from lib.app_filters import render_global_filters
from lib.clv_report import build_clv_report
from lib.config import DATA_DIR
from lib.display import render_clv_dashboard
from lib.games import display_week_complete
from lib.market_moves_slate import load_market_moves_slate
from lib.odds_cache import load_cached_lines
from lib.snapshots import load_snapshots
from lib.sport_context import cache_sport, get_sport_config
from lib.styling import callout, section_header
from lib.week_availability import week_calendar_started, week_has_started, week_schedule_state

TAB = "Market Moves"
CACHE_DIR = DATA_DIR / "market_moves_cache"
CACHE_TTL_SEC = 3600
CACHE_VERSION = "fastr_v10"


def _cache_path(sport: str, year: int, week: int) -> Path:
    return CACHE_DIR / f"market_moves_{CACHE_VERSION}_{sport}_{int(year)}_w{int(week)}.json"


def _load_disk_report(sport: str, year: int, week: int) -> dict | None:
    path = _cache_path(sport, year, week)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(str(payload.get("saved_at")).replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - saved_at).total_seconds() > CACHE_TTL_SEC:
            return None
        report = payload.get("report")
        return report if isinstance(report, dict) else None
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _save_disk_report(sport: str, year: int, week: int, report: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"saved_at": datetime.now(timezone.utc).isoformat(), "report": report}
        _cache_path(sport, year, week).write_text(json.dumps(payload, default=str), encoding="utf-8")
    except OSError:
        pass


@st.cache_data(ttl=1800, show_spinner=False)
def _load_report(sport: str, year: int, week: int) -> dict:
    cached = _load_disk_report(sport, int(year), int(week))
    if cached is not None:
        return cached

    historical = display_week_complete(int(year), int(week))
    slate = load_market_moves_slate(sport, int(year), int(week), historical=historical)
    odds = load_cached_lines()
    history = load_snapshots(days_back=7)
    report = build_clv_report(slate, odds, week=int(week), year=int(year), history=history)
    if report.get("ready"):
        _save_disk_report(sport, int(year), int(week), report)
    return report


def render() -> None:
    year, week = render_global_filters(prefix="mm")
    state = week_schedule_state(int(year), int(week))

    section_header("Market Moves", eyebrow=f"WEEK {week}")

    cached = _load_disk_report(cache_sport(), int(year), int(week))
    calendar_started = week_calendar_started(int(year), int(week))

    if state == "future" and not calendar_started and cached is None:
        callout(
            "This week's market movement report will be available once games kick off. "
            "Open-to-close grading uses cfbfastR / nflfastR opening and closing lines.",
            "info",
        )
        return

    report = cached if cached is not None else _load_report(cache_sport(), int(year), int(week))

    if not report.get("ready"):
        if week_has_started(int(year), int(week)):
            callout(
                "Line movement data is still loading for this week. "
                "The report populates once cfbfastR / nflfastR lines are available for every game.",
                "info",
            )
        return

    render_clv_dashboard(report)

    sport_label = get_sport_config()["label"]
    source = "cfbfastR" if cache_sport() == "cfb" else "nflfastR"
    st.caption(f"{sport_label} · Week {week} · spread/total opener → close from {source}")
