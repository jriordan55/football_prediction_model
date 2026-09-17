"""NFL player props — DraftKings only via The Odds API."""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .config import book_label, normalize_book_id
from .multi_book_props import (
    NFL_PLAYER_PROP_MARKETS,
    fetch_event_player_props,
    match_odds_event_ids,
)


def _dk_rows_to_prop_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    for row in rows:
        book_id = normalize_book_id(str(row.get("book_id") or "draftkings"))
        if book_id != "draftkings":
            continue
        side = str(row.get("side") or "over").lower()
        price = row.get("over_price") if side == "over" else row.get("under_price")
        if price is None:
            price = row.get("price")
        out.append(
            {
                "player": row.get("player"),
                "propKey": row.get("propKey"),
                "market": row.get("market"),
                "line": row.get("line"),
                "side": side.title(),
                "price": price,
                "home": row.get("home"),
                "away": row.get("away"),
                "event": row.get("event"),
                "event_id": row.get("event_id"),
                "group": "prop",
                "book_id": book_id,
                "book": book_label(book_id),
                "source": "draftkings",
            }
        )
    return pd.DataFrame(out)


@st.cache_data(ttl=900, show_spinner="Loading DraftKings NFL props…")
def load_nfl_draftkings_props(sport: str, year: int, week: int, *, force: bool = False) -> pd.DataFrame:
    """Pull NFL player props from DraftKings (The Odds API, book=draftkings only)."""
    _ = sport
    from .games import list_games_for_display_week
    from .multi_book_props import load_multi_book_props

    matchups = list_games_for_display_week(int(year), int(week), fbs_only=False)
    if not matchups:
        return pd.DataFrame()

    if not force:
        cached = load_multi_book_props(tuple(matchups))
        if not cached.empty and "book_id" in cached.columns:
            dk_cached = cached[cached["book_id"].astype(str).str.lower() == "draftkings"]
            if not dk_cached.empty:
                df = _dk_rows_to_prop_frame(dk_cached.to_dict(orient="records"))
                if not df.empty:
                    df["year"] = int(year)
                    df["week"] = int(week)
                    return df

    event_ids = match_odds_event_ids(matchups, limit=32)
    if not event_ids:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for eid in event_ids:
        rows.extend(
            fetch_event_player_props(
                str(eid),
                bookmakers="draftkings",
                markets=NFL_PLAYER_PROP_MARKETS,
            )
        )
    df = _dk_rows_to_prop_frame(rows)
    if df.empty:
        return df
    df["year"] = int(year)
    df["week"] = int(week)
    return df
