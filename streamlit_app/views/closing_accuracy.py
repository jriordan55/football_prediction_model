"""Closing Accuracy — model vs close dashboard."""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from lib.config import DEFAULT_WEEK, DEFAULT_YEAR
from lib.excel_audit import export_pull
from lib.node_bridge import fetch_labs_results, node_available
from lib.odds_cache import cache_age_minutes, load_cached_lines, pull_sport_odds_lines
from lib.styling import callout, section_header, section_label, week_season_filters

TAB = "Closing Accuracy"


def render() -> None:
    year, week = week_season_filters("ca", year=DEFAULT_YEAR, week=DEFAULT_WEEK)

    section_header(
        "Closing Line Accuracy",
        "How our projections track against the closing number — and which books offered the best price.",
        eyebrow="ACCURACY",
    )

    results = None
    if node_available():
        results = fetch_labs_results(int(year), int(week), tab=TAB)

    c1, c2 = st.columns([1, 1])
    with c1:
        _render_model_vs_close(results, week)
    with c2:
        _render_best_price()

    st.markdown("---")
    _render_travelled(results)


def _render_model_vs_close(results: dict | None, week: int) -> None:
    section_label("Our number vs the close")
    hit_rate = None
    spread_hits = total_hits = graded = 0
    if results and results.get("lineResults"):
        lr = results["lineResults"]
        graded = lr.get("gradedCount", 0)
        spread_hits = lr.get("spreadHits", 0)
        total_hits = lr.get("totalHits", 0)
        if graded:
            hit_rate = round(100 * (spread_hits + total_hits) / max(graded * 2, 1))

    if hit_rate is None:
        st.metric("Line moved toward model", "—")
        callout(
            f"Week {week} grades will appear here once final scores are in.",
            "info",
        )
    else:
        st.metric("When the line moved, toward us", f"{hit_rate}%")
        st.caption(f"{spread_hits} spread hits · {total_hits} total hits · {graded} games graded")

        hits = spread_hits + total_hits
        misses = max(0, graded * 2 - hits)
        squares = hits + misses
        cols = st.columns(min(20, max(10, squares // 3)))
        for i in range(min(squares, 60)):
            good = i < hits
            cols[i % len(cols)].markdown(
                f"<div style='background:{'#4ade80' if good else '#fb923c'};height:18px;border-radius:4px;margin:2px'></div>",
                unsafe_allow_html=True,
            )


def _render_best_price() -> None:
    section_label("Who gave you the best price")
    age = cache_age_minutes()
    refresh = st.button("Refresh best-price chart", key="ca_refresh_prices")
    if age is not None and not refresh:
        st.caption(f"Cached lines · last pull {int(age)} min ago · no auto API calls")

    if refresh:
        df, meta = pull_sport_odds_lines(force=True, tab=TAB)
        if meta.get("skipped_pull"):
            callout(meta.get("reason", "Using cached lines."), "info")
            df = load_cached_lines()
    else:
        df = load_cached_lines()
        meta = {}

    if df.empty:
        callout("Tap <strong>Refresh best-price chart</strong> to load lines (uses API credits once).", "info")
        return

    best_counts: dict[str, int] = {}
    for (_, sel), grp in df[df["market_key"] == "h2h"].groupby(["event", "selection"]):
        prices = pd.to_numeric(grp["price"], errors="coerce")
        if prices.isna().all():
            continue
        idx = prices.idxmax()
        book = grp.loc[idx, "book"]
        best_counts[book] = best_counts.get(book, 0) + 1

    if not best_counts:
        callout("No moneyline prices available for this slate.", "info")
        return

    total = sum(best_counts.values())
    items = sorted(best_counts.items(), key=lambda x: -x[1])[:6]
    labels = [k for k, _ in items]
    values = [100 * v / total for _, v in items]

    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.55,
            marker_colors=["#a78bfa", "#4ade80", "#fb923c", "#71717a", "#f87171", "#52525b"],
        )
    )
    fig.update_layout(template="plotly_dark", paper_bgcolor="#07070b", height=280, margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)
    for book, cnt in items:
        st.caption(f"**{book}** — {100 * cnt / total:.0f}% of best prices")

    best_df = pd.DataFrame(
        [{"book": k, "best_price_count": v, "share_pct": round(100 * v / total, 1)} for k, v in items]
    )
    export_pull("derived_best_price", best_df, tab=TAB, origin="computed", extra=meta)


def _render_travelled(results: dict | None) -> None:
    section_label("How far the number travelled", "Projection error vs final score on completed games")
    rows = []
    if results and results.get("scoreRows"):
        for r in results["scoreRows"][:12]:
            rows.append(
                {
                    "Game": r.get("matchup"),
                    "Projection": r.get("projTotal"),
                    "Final": r.get("actualTotal"),
                    "Error": r.get("totalError"),
                }
            )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        callout("Total projection accuracy populates after games finish.", "info")
