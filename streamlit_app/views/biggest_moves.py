"""Biggest Odds Moves — consensus open to close (NCAAF focus)."""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st
import pandas as pd

from lib.excel_audit import export_pull
from lib.odds_cache import cache_age_minutes, pull_sport_odds_lines
from lib.snapshots import (
    append_snapshot_if_changed,
    consensus_biggest_moves,
    latest_snapshot_preview,
    load_snapshots,
    snapshot_stats,
    spread_total_moves,
)
from lib.styling import callout, section_header, section_label, show_dataframe, stat_strip

TAB = "Biggest Moves"


def render() -> None:
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    section_header(
        "Biggest Odds Moves",
        f"Biggest line moves on the board · {today}",
        eyebrow="MOVERS",
    )

    age = cache_age_minutes()
    if age is not None:
        st.caption(f"Last line pull: {int(age)} min ago")

    if st.button("Pull lines & save snapshot"):
        df, meta = pull_sport_odds_lines(force=True, tab=TAB)
        if meta.get("skipped_pull"):
            callout(meta.get("reason", "Using cached lines."), "info")
            df, _ = pull_sport_odds_lines(force=False, tab=TAB)
        if df.empty:
            callout("Could not load lines. Try again later.", "warn")
        else:
            n, status = append_snapshot_if_changed(df, "theoddsapi", tab=TAB)
            if status == "unchanged":
                callout("Lines unchanged since last snapshot — no credits wasted on duplicate capture.", "info")
            else:
                callout(f"Market snapshot saved — <strong>{n:,}</strong> lines tracked.", "success")
            st.cache_data.clear()
            st.rerun()

    history = load_snapshots(days_back=7)
    stats = snapshot_stats(history)

    if stats["captures"] == 0:
        callout(
            "Tap <strong>Pull lines & save snapshot</strong> to start tracking. "
            "Nothing loads automatically — you control when credits are used.",
            "info",
        )
        _show_example_table()
        return

    stat_strip(
        [
            ("Snapshots today", str(stats["captures"]), "purple" if stats["captures"] >= 2 else ""),
            ("Lines tracked", f"{stats['rows']:,}", ""),
            ("Books", str(stats["books"]), ""),
            ("Games", str(stats["events"]), "green"),
        ]
    )

    if stats["captures"] < 2:
        latest = stats["latest"]
        ts = latest.strftime("%I:%M %p UTC") if latest is not None else "—"
        callout(
            f"First snapshot at <strong>{ts}</strong>. "
            "Pull again when lines move to unlock open-to-close reports.",
            "warn",
        )
        preview = latest_snapshot_preview(history)
        if not preview.empty:
            section_label("Current lines", "From your most recent snapshot")
            show_dataframe(preview)

    cfb = history[history["event"].astype(str).str.len() > 0]
    ml = consensus_biggest_moves(cfb[cfb["market_key"] == "h2h"])
    spreads = spread_total_moves(cfb)

    section_label(
        "Biggest moneyline moves",
        "Median move across books · minimum 5 books confirming",
    )
    if not ml.empty:
        cfb_ml = ml[ml["event"].astype(str).str.contains("@")]
        show = cfb_ml.head(15) if not cfb_ml.empty else ml.head(10)
        export_pull("derived_biggest_ml_moves", show, tab=TAB, origin="computed")
        show_dataframe(
            show.rename(columns={"move_pts": "Move (prob pts)", "books_confirming": "Books confirming"})
        )
    elif stats["captures"] >= 2:
        callout("No significant moneyline moves yet today.", "info")

    section_label("Spreads and totals that moved")
    if spreads.empty:
        if stats["captures"] >= 2:
            callout("No significant spread or total moves yet today.", "info")
    else:
        cfb_st = spreads[spreads["market"].astype(str).str.contains("spread|total", case=False)]
        out = cfb_st if not cfb_st.empty else spreads
        export_pull("derived_biggest_spread_total", out.head(20), tab=TAB, origin="computed")
        show_dataframe(out.head(20))


def _show_example_table() -> None:
    section_label("What you'll see", "Sample moves once two snapshots are captured")
    show_dataframe(
        pd.DataFrame(
            [
                {"Game": "LIU @ Kansas", "Market": "Spread (Kansas)", "Open → Close": "-39.5 → -41", "Books": 6},
                {"Game": "Baylor @ Auburn", "Market": "Total", "Open → Close": "59 → 58", "Books": 9},
                {"Game": "VMI @ Virginia Tech", "Market": "Spread (VMI)", "Open → Close": "55.5 → 56.5", "Books": 5},
            ]
        )
    )
