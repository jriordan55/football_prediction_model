"""Sharp Lines — fair line vs best available price."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from lib.excel_audit import export_pull
from lib.config import odds_api_key
from lib.node_bridge import fetch_pinnacle_edges, node_available
from lib.odds_cache import load_cached_lines, pull_sport_odds_lines
from lib.odds_math import american_to_implied, devig_two_way
from lib.styling import callout, section_header, show_dataframe

TAB = "Sharp Lines"


US_SOFT = ["draftkings", "fanduel", "betmgm", "caesars", "betrivers", "thescore", "fanatics"]


def _compute_edges_local() -> pd.DataFrame:
    df, _ = pull_sport_odds_lines(force=False, tab=TAB)
    if df.empty:
        df, _ = pull_sport_odds_lines(force=True, tab=TAB)
    if df.empty:
        return pd.DataFrame()

    rows = []
    for event, evdf in df.groupby("event"):
        pin = evdf[evdf["book_id"] == "pinnacle"]
        if pin.empty:
            continue
        for mk in ["h2h", "spreads", "totals"]:
            mdf = evdf[evdf["market_key"] == mk]
            if mdf.empty:
                continue
            pin_m = mdf[mdf["book_id"] == "pinnacle"]
            if pin_m.empty:
                continue
            for _, prow in pin_m.iterrows():
                soft = mdf[(mdf["book_id"].isin(US_SOFT)) & (mdf["selection"] == prow["selection"])]
                if soft.empty:
                    continue
                best = soft.iloc[0]
                if mk == "h2h" and len(soft) > 1:
                    soft_prices = pd.to_numeric(soft["price"], errors="coerce")
                    if not soft_prices.isna().all():
                        best = soft.loc[soft_prices.idxmax()]
                try:
                    edge_pts = (
                        float(best["line"]) - float(prow["line"])
                        if mk != "h2h" and prow.get("line") is not None
                        else float(best["price"]) - float(prow["price"])
                    )
                except (TypeError, ValueError):
                    pin_fair = devig_two_way(prow["price"], prow["price"])
                    edge_pts = (american_to_implied(best["price"]) or 0) - (pin_fair or 0)
                rows.append(
                    {
                        "event": event,
                        "market": mk,
                        "outcome": prow["selection"],
                        "fair_line": prow.get("line") or prow["price"],
                        "book_line": best.get("line") or best["price"],
                        "book": best["book"],
                        "edge_pts": round(edge_pts, 1) if isinstance(edge_pts, (int, float)) else edge_pts,
                    }
                )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.reindex(out["edge_pts"].abs().sort_values(ascending=False).index)


def _display_edges(edges: pd.DataFrame) -> pd.DataFrame:
    col = "edge_points" if "edge_points" in edges.columns else "edge_pts"
    rename = {
        "event": "Game",
        "market": "Market",
        "outcome": "Side",
        "fair_line": "Fair line",
        "book_line": "Book line",
        "pinnacle_price": "Fair line",
        "best_soft_price": "Book line",
        "best_soft_book": "Book",
        "book": "Book",
        col: "Edge",
        "edge_pct": "Edge %",
    }
    cols = [c for c in rename if c in edges.columns]
    if not cols:
        cols = list(edges.columns)[:8]
    return edges[cols].rename(columns={k: v for k, v in rename.items() if k in edges.columns})


def render() -> None:
    section_header(
        "Sharp Lines",
        "Where the board disagrees with our fair number — sorted by edge size.",
        eyebrow="EDGES",
    )

    fc1, fc2, _ = st.columns([2, 2, 3])
    with fc1:
        min_edge = st.slider("Minimum edge (pts)", 0.0, 5.0, 1.0, 0.5)
    with fc2:
        refresh = st.checkbox("Refresh live lines", value=False)

    edges = None
    cached = load_cached_lines()
    if node_available():
        payload = fetch_pinnacle_edges(refresh=refresh, min_edge=min_edge, tab=TAB)
        if payload and payload.get("edges"):
            edges = pd.DataFrame(payload["edges"])

    if edges is None or edges.empty:
        if not odds_api_key():
            callout("Live lines are temporarily unavailable. Please try again shortly.", "warn")
            return
        if refresh:
            edges = _compute_edges_local()
        elif not cached.empty:
            edges = _compute_edges_local()
        else:
            callout("Enable <strong>Refresh live lines</strong> to load the edge board (uses API credits).", "info")
            return

    if edges.empty:
        callout("No edges above your threshold right now.", "info")
        return

    col = "edge_points" if "edge_points" in edges.columns else "edge_pts"
    if col in edges.columns:
        edges = edges[edges[col].abs() >= min_edge]

    show = _display_edges(edges.head(50))
    show_dataframe(show)
    export_pull("derived_sharp_lines", edges.head(50), tab=TAB, origin="computed")
    st.caption(f"{len(edges)} opportunities on the board")
