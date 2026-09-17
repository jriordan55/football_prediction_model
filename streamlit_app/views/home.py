"""Home — Weekly Brief board (REDZONE-style layout, Football Labs data)."""
from __future__ import annotations

import streamlit as st

from lib.config import DEFAULT_YEAR
from lib.display import render_fantasy_chart, render_games_to_watch, render_weekly_brief_cards
from lib.styling import callout
from lib.sport_context import SPORT_CFB, cache_sport
from lib.week_availability import current_display_week
from lib.weekly_brief import (
    brief_headline,
    brief_meta,
    build_fantasy_chart_rows,
    build_games_to_watch,
    build_slate_lean_rows,
    build_thread_bullets,
    build_weather_rows,
)

TAB = "Weekly Brief"
SUB_TABS = ["The Thread", "Games to Watch", "Fantasy Chart"]


@st.cache_data(ttl=600, show_spinner=False)
def _brief_payload(sport: str, year: int, week: int) -> dict:
    _ = sport
    headline, subtitle = brief_headline(year, week)
    return {
        "headline": headline,
        "subtitle": subtitle,
        "meta": brief_meta(),
        "bullets": build_thread_bullets(year, week),
        "weather": build_weather_rows(year, week, limit=2),
        "lean": build_slate_lean_rows(year, week, limit=10),
        "watch": build_games_to_watch(year, week),
        "props": build_fantasy_chart_rows(year, week),
    }


def _render_subnav() -> str:
    if "brief_subnav" not in st.session_state:
        st.session_state.brief_subnav = SUB_TABS[0]
    st.markdown('<div class="bo-rz-subnav">', unsafe_allow_html=True)
    cols = st.columns(len(SUB_TABS))
    for col, label in zip(cols, SUB_TABS):
        with col:
            if st.button(
                label,
                key=f"brief_sub_{label}",
                use_container_width=True,
                type="primary" if st.session_state.brief_subnav == label else "secondary",
            ):
                st.session_state.brief_subnav = label
    st.markdown("</div>", unsafe_allow_html=True)
    return st.session_state.brief_subnav


def render() -> None:
    year, week = DEFAULT_YEAR, current_display_week(DEFAULT_YEAR, SPORT_CFB)
    sub = _render_subnav()

    try:
        data = _brief_payload(cache_sport(), year, week)
    except Exception:
        callout("Weekly Brief couldn't load right now. Try refreshing.", "warn")
        return

    if sub == "The Thread":
        render_weekly_brief_cards(
            headline=data["headline"],
            subtitle=data["subtitle"],
            bullets=data["bullets"],
            weather_rows=data["weather"],
            lean_rows=data["lean"],
            meta=data["meta"],
            week=week,
        )
    elif sub == "Games to Watch":
        st.markdown(
            f"""
<div class="bo-rz-card" style="margin-bottom:14px">
  <div class="bo-rz-card-top"><span class="bo-rz-eyebrow">GAMES TO WATCH</span>
  <span class="bo-rz-meta">{data["meta"]}</span></div>
  <h2 class="bo-rz-title">Flagged plays and biggest edges on the slate</h2>
  <p class="bo-rz-sub">Sorted by model ROI · Week {week}</p>
  <div class="bo-rz-card-accent"></div>
</div>""",
            unsafe_allow_html=True,
        )
        render_games_to_watch(data["watch"])
    else:
        st.markdown(
            f"""
<div class="bo-rz-card" style="margin-bottom:14px">
  <div class="bo-rz-card-top"><span class="bo-rz-eyebrow">FANTASY CHART</span>
  <span class="bo-rz-meta">{data["meta"]}</span></div>
  <h2 class="bo-rz-title">Top player props by edge</h2>
  <p class="bo-rz-sub">Same +EV prop board · Week {week}</p>
  <div class="bo-rz-card-accent"></div>
</div>""",
            unsafe_allow_html=True,
        )
        render_fantasy_chart(data["props"])
