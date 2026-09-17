"""CLV Report — closing line value dashboard."""
from __future__ import annotations

import streamlit as st

from lib.clv_report import build_clv_report
from lib.config import DEFAULT_WEEK, DEFAULT_YEAR
from lib.display import render_clv_dashboard
from lib.excel_audit import export_pull
from lib.odds_cache import cache_age_minutes, load_cached_lines, pull_sport_odds_lines
from lib.sport_context import cache_sport, get_sport_config
from lib.slate_loader import load_slate_df
from lib.snapshots import append_snapshot_if_changed, load_snapshots
from lib.styling import callout, section_header, week_season_filters

TAB = "CLV Leaderboard"


@st.cache_data(ttl=600, show_spinner=False)
def _load_report(sport: str, year: int, week: int) -> dict:
    _ = sport
    slate = load_slate_df(cache_sport(), year, week)
    odds = load_cached_lines()
    return build_clv_report(slate, odds, week=week, year=year)


def render() -> None:
    year, week = week_season_filters("clv", year=DEFAULT_YEAR, week=DEFAULT_WEEK)

    section_header("CLV Report", eyebrow=f"WEEK {week}")

    c1, c2 = st.columns([3, 1])
    with c2:
        refresh = st.button("Refresh lines", key="clv_refresh_lines", use_container_width=True)
        if refresh:
            df, meta = pull_sport_odds_lines(force=True, tab=TAB)
            if not df.empty:
                append_snapshot_if_changed(df, "theoddsapi", tab=TAB)
            st.cache_data.clear()
            st.rerun()

    age = cache_age_minutes()
    if age is not None:
        st.caption(f"Cached multi-book lines · last pull {int(age)} min ago · no auto API calls on page load")

    report = _load_report(cache_sport(), int(year), int(week))
    export_pull("derived_clv_report", report, tab=TAB, origin="computed", extra={"week": week, "year": year})

    if report.get("empty"):
        callout(
            "Pull lines once to populate best-price books. "
            "Line-travel grades use the saved opener snapshot vs current slate close.",
            "info",
        )

    render_clv_dashboard(report)

    opener = report.get("opener_at")
    if opener:
        st.caption(f"Opener baseline: {str(opener)[:10]} · close from current slate")

    sport_label = get_sport_config()["label"]
    st.markdown(
        f"""
<div class="bo-clv-footer">
  <span>{sport_label} · Week {week} · opener to close</span>
</div>""",
        unsafe_allow_html=True,
    )
