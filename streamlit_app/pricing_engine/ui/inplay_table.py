"""Render in-play PBP market rows from CSV."""
from __future__ import annotations

from typing import Any

import pandas as pd

from lib.inplay_storage import CSV_COLUMNS, load_inplay_rows


def inplay_rows_for_game(
    *,
    sport: str,
    event_id: str | None = None,
    home: str | None = None,
    away: str | None = None,
) -> pd.DataFrame:
    df = load_inplay_rows(sport=sport, event_id=event_id)
    if df.empty:
        return df
    if event_id:
        return df
    if home and away:
        from lib.nfl_team_registry import teams_match as nfl_match
        from lib.team_registry import teams_match as cfb_match

        match = nfl_match if sport.lower() == "nfl" else cfb_match

        def _row_match(r: pd.Series) -> bool:
            return match(str(r.get("home") or ""), home) and match(str(r.get("away") or ""), away)

        return df[df.apply(_row_match, axis=1)]
    return df


def render_inplay_html(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return '<div class="bo-pe-empty-block">No in-play captures yet for this game.</div>'

    show_cols = [
        "captured_at",
        "period",
        "clock",
        "play_text",
        "market_type",
        "market",
        "selection",
        "player",
        "line",
        "book_id",
        "book_price",
        "model_projection",
        "model_price",
        "actual_result",
        "actual_stat",
        "pregame_projection",
        "pregame_line",
        "expected_roi_pct",
    ]
    cols = [c for c in show_cols if c in df.columns]
    view = df[cols].copy()
    view = view.fillna("—")

    parts = [
        '<table class="bo-pe-table bo-pe-inplay"><thead><tr>',
        *[f"<th>{c.replace('_', ' ').title()}</th>" for c in cols],
        "</tr></thead><tbody>",
    ]
    for _, row in view.iterrows():
        parts.append("<tr>")
        for c in cols:
            val = row[c]
            parts.append(f"<td>{val}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)
