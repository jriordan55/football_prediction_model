"""Export play-by-play pricing log to a clean Excel workbook when a game finishes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from lib.config import DATA_DIR

EXPORT_DIR = DATA_DIR / "inplay_pricing" / "exports"

PBP_XLSX_COLUMNS = [
    "captured_at",
    "quarter",
    "clock",
    "down",
    "distance",
    "yard_line",
    "play_text",
    "team",
    "score",
    "exp_spread",
    "spread",
    "exp_total",
    "total_line",
    "total_over_odds",
    "total_under_odds",
    "ml_odds",
    "win_pct",
    "exp_price",
]


def _flatten_plays(plays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for play in plays:
        base = {
            "captured_at": play.get("captured_at"),
            "quarter": play.get("quarter"),
            "clock": play.get("clock"),
            "down": play.get("down"),
            "distance": play.get("distance"),
            "yard_line": play.get("yard_line"),
            "play_text": play.get("play_text"),
        }
        for side in ("away", "home"):
            team = play.get(side) or {}
            rows.append(
                {
                    **base,
                    "team": team.get("name"),
                    "score": team.get("score"),
                    "exp_spread": team.get("exp_spread"),
                    "spread": team.get("spread"),
                    "exp_total": team.get("exp_total"),
                    "total_line": team.get("total"),
                    "total_over_odds": team.get("total_over_odds"),
                    "total_under_odds": team.get("total_under_odds"),
                    "ml_odds": team.get("ml_odds"),
                    "win_pct": team.get("win_pct"),
                    "exp_price": team.get("exp_price"),
                }
            )
    return rows


def export_pbp_game_xlsx(
    *,
    sport: str,
    event_id: str,
    home: str,
    away: str,
    year: int,
    week: int,
    quotes: dict[str, dict[str, Any]] | None = None,
    pregame: dict[str, Any] | None = None,
    pregame_sim: dict[str, Any] | None = None,
) -> Path | None:
    """Write a clean per-play Excel log. Safe to call multiple times (overwrites)."""
    from pricing_engine.pbp_log import build_team_pbp_plays

    plays = build_team_pbp_plays(
        sport=sport,
        year=year,
        week=week,
        event_id=str(event_id),
        home=home,
        away=away,
        quotes=quotes or {},
        pregame=pregame,
        pregame_sim=pregame_sim,
        completed=True,
        live=False,
    )
    if not plays:
        return None

    rows = _flatten_plays(plays)
    df = pd.DataFrame(rows)
    for col in PBP_XLSX_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[PBP_XLSX_COLUMNS]

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{sport}_{event_id}_pbp.xlsx"
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="PBP Log", index=False)
            meta = pd.DataFrame(
                [
                    {"field": "sport", "value": sport},
                    {"field": "event_id", "value": event_id},
                    {"field": "home", "value": home},
                    {"field": "away", "value": away},
                    {"field": "year", "value": year},
                    {"field": "week", "value": week},
                    {"field": "exported_at", "value": datetime.now(timezone.utc).isoformat()},
                    {"field": "play_count", "value": len(plays)},
                ]
            )
            meta.to_excel(writer, sheet_name="Meta", index=False)
    except ImportError:
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        return csv_path

    return path
