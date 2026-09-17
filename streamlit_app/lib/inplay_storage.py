"""Persist in-play market rows to CSV and per-game state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR

INPLAY_DIR = DATA_DIR / "inplay_pricing"
INPLAY_CSV = INPLAY_DIR / "inplay_market_rows.csv"
STATE_DIR = INPLAY_DIR / "state"

CSV_COLUMNS = [
    "captured_at",
    "sport",
    "year",
    "week",
    "event_id",
    "play_id",
    "period",
    "clock",
    "play_text",
    "down",
    "distance",
    "possession",
    "yard_line",
    "score_margin",
    "home",
    "away",
    "home_score",
    "away_score",
    "home_win_prob",
    "away_win_prob",
    "home_pass_yds",
    "home_rush_yds",
    "home_total_yds",
    "away_pass_yds",
    "away_rush_yds",
    "away_total_yds",
    "market_type",
    "market",
    "selection",
    "player",
    "line",
    "book_id",
    "book_price",
    "model_projection",
    "model_price",
    "model_prob",
    "actual_result",
    "actual_stat",
    "bet_result",
    "expected_roi_pct",
    "pregame_line",
    "pregame_book_price",
    "pregame_projection",
    "pregame_capture_at",
    "pregame_capture_type",
]

BY_GAME_DIR = INPLAY_DIR / "by_game"


def _state_path(event_id: str) -> Path:
    return STATE_DIR / f"{event_id}.json"


def load_seen_plays(event_id: str) -> set[str]:
    path = _state_path(event_id)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(str(x) for x in (data.get("seen_plays") or []))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def save_seen_plays(event_id: str, seen: set[str], *, meta: dict[str, Any] | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"seen_plays": sorted(seen), **(meta or {})}
    _state_path(event_id).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _normalize_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[CSV_COLUMNS]


def append_rows(rows: list[dict[str, Any]]) -> Path:
    if not rows:
        return INPLAY_CSV
    INPLAY_DIR.mkdir(parents=True, exist_ok=True)
    BY_GAME_DIR.mkdir(parents=True, exist_ok=True)
    df = _normalize_df(rows)
    header = not INPLAY_CSV.exists()
    df.to_csv(INPLAY_CSV, mode="a", header=header, index=False)

    for (sport, event_id), chunk in df.groupby(["sport", "event_id"], dropna=False):
        if not event_id:
            continue
        game_path = BY_GAME_DIR / f"{sport}_{event_id}.csv"
        game_header = not game_path.exists()
        chunk.to_csv(game_path, mode="a", header=game_header, index=False)

    return INPLAY_CSV


def load_inplay_rows(
    *,
    sport: str | None = None,
    event_id: str | None = None,
    limit: int = 5000,
) -> pd.DataFrame:
    if not INPLAY_CSV.exists():
        return pd.DataFrame(columns=CSV_COLUMNS)
    df = pd.read_csv(INPLAY_CSV)
    if sport:
        df = df[df["sport"].astype(str) == str(sport)]
    if event_id:
        df = df[df["event_id"].astype(str) == str(event_id)]
    return df.tail(limit)
