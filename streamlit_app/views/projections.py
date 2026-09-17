"""Projections — sides & totals with model edge."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from lib.config import DEFAULT_WEEK, DEFAULT_YEAR
from lib.display import render_projection_board
from lib.espn_client import fetch_scoreboard
from lib.sport_context import cache_sport
from lib.slate_loader import load_slate_df
from lib.styling import callout, section_header, week_season_filters

TAB = "Projections"


@st.cache_data(ttl=600, show_spinner=False)
def _abbr_lookup(sport: str, year: int, week: int) -> dict[str, str]:
    _ = sport
    sb = fetch_scoreboard(week=week, year=year, tab=TAB)
    lookup: dict[str, str] = {}
    for _, row in sb.iterrows():
        for side in ("home", "away"):
            name = str(row.get(side) or "")
            abbr = str(row.get(f"{side}_abbr") or "")
            if name and abbr:
                lookup[name] = abbr
    return lookup


def render() -> None:
    year, week = week_season_filters("proj", year=DEFAULT_YEAR, week=DEFAULT_WEEK)

    section_header(
        "Projections",
        "Sides, totals, and model edge on every game.",
        eyebrow="LINES",
    )

    df = load_slate_df(cache_sport(), int(year), int(week))

    if df.empty:
        callout("Projections will appear here when the slate is live.", "info")
        return

    game = df[
        df["group"].isin(["spread", "total", "moneyline"])
        | (df["category"].eq("game") & df["market"].isin(["Spread", "Total", "Moneyline"]))
    ].copy()

    totals = game[game["group"].eq("total") & game["market"].eq("Total")]
    spreads = game[game["group"].eq("spread") & game["market"].eq("Spread")]
    abbrs = _abbr_lookup(cache_sport(), int(year), int(week))

    tab_t, tab_s = st.tabs(["Totals", "Spreads"])
    with tab_t:
        if totals.empty:
            callout("No total projections on the board right now.", "info")
        else:
            render_projection_board(totals, kind="total", week=int(week), abbr_lookup=abbrs)
    with tab_s:
        if spreads.empty:
            callout("No spread projections on the board right now.", "info")
        else:
            render_projection_board(spreads, kind="spread", week=int(week), abbr_lookup=abbrs)
