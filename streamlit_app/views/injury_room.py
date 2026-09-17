"""Injury Room — ESPN league injuries."""
from __future__ import annotations

import streamlit as st

from lib.display import render_injury_feed
from lib.espn_client import fetch_league_injuries, injury_summary
from lib.styling import callout, section_header, stat_strip

TAB = "Injury Room"


@st.cache_data(ttl=300, show_spinner=False)
def load_injuries():
    return fetch_league_injuries(tab=TAB)


def render() -> None:
    section_header(
        "Injury Reports",
        "Latest player availability across college football.",
        eyebrow="INJURIES",
    )

    df = load_injuries()
    if df.empty:
        callout("Injury reports aren't available right now.", "info")
        return

    summary = injury_summary(df)
    stat_strip(
        [
            ("Listed", str(summary["listed"]), ""),
            ("Unavailable", str(summary["unavailable"]), ""),
            ("Questionable", str(summary["questionable"]), ""),
            ("Teams", str(summary["teams"]), "purple"),
        ]
    )

    filt = st.radio(
        "Filter",
        ["Latest", "Unavailable", "Questionable", "All"],
        horizontal=True,
        label_visibility="collapsed",
    )

    show = df.copy()
    status = show["status"].astype(str).str.upper()
    if filt == "Unavailable":
        show = show[status.str.contains("OUT|IR|INJURED|DOUBTFUL|SUSPEND", na=False)]
    elif filt == "Questionable":
        show = show[status.str.contains("QUESTION|DAY|DOUBT", na=False)]
    elif filt == "Latest":
        show = show.sort_values("updated", ascending=False)

    search = st.text_input("Search player or team", placeholder="Filter team, player…", label_visibility="collapsed")
    if search:
        mask = show["player"].str.contains(search, case=False, na=False) | show["team"].str.contains(
            search, case=False, na=False
        )
        show = show[mask]

    show = show.sort_values("updated", ascending=False)
    render_injury_feed(show, limit=80)
