"""Disk cache for the full player props board (shared by projections + matchup detail)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from lib.config import DATA_DIR
from lib.sport_context import get_sport_config, resolve_sport

BOARD_CACHE_DIR = DATA_DIR / "odds_api_cache"
BOARD_CACHE_VERSION = 14


def player_props_board_path(sport: str, year: int, week: int) -> Path:
    tag = get_sport_config(resolve_sport(sport))["odds_cache_tag"]
    return BOARD_CACHE_DIR / f"player_props_board_{tag}_{int(year)}_w{int(week)}_v{BOARD_CACHE_VERSION}.json"


def load_player_props_board(sport: str, year: int, week: int) -> pd.DataFrame:
    path = player_props_board_path(sport, year, week)
    if not path.exists():
        return pd.DataFrame()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return pd.DataFrame()
