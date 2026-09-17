"""Global season/week filters shared across tabs."""
from __future__ import annotations

import streamlit as st

from lib.sport_context import default_season_week, get_sport, week_options


def init_app_filters() -> None:
    from lib.sport_context import _load_filters, get_sport, init_sport

    init_sport()
    _load_filters(get_sport())


def get_season_week() -> tuple[int, int]:
    init_app_filters()
    return int(st.session_state.app_year), int(st.session_state.app_week)


def render_global_filters(*, prefix: str = "global") -> tuple[int, int]:
    """Compact season/week picker — dark theme, no white flash."""
    init_app_filters()
    seasons = [2026]
    year = 2026
    week = int(st.session_state.app_week)
    opts = week_options()
    if year not in seasons:
        year = default_season_week()[0]
    week_idx = opts.index(week) if week in opts else 0

    st.markdown('<div class="bo-filter-bar">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1, 4])
    with c1:
        st.markdown('<span class="filter-label">Season</span>', unsafe_allow_html=True)
        y = st.selectbox(
            "Season",
            seasons,
            index=0,
            key=f"{prefix}_year_{get_sport()}",
            label_visibility="collapsed",
        )
    with c2:
        st.markdown('<span class="filter-label">Week</span>', unsafe_allow_html=True)
        w = st.selectbox(
            "Week",
            opts,
            index=week_idx,
            format_func=lambda x: f"Week {x}",
            key=f"{prefix}_week_{get_sport()}",
            label_visibility="collapsed",
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.session_state.app_year = int(y)
    st.session_state.app_week = int(w)
    from lib.sport_context import _save_filters

    _save_filters(get_sport())
    return int(y), int(w)
