"""Fast local slate loading — no Node API, no Excel audit on read."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from lib.games import list_games_for_display_week
from lib.config import DATA_DIR
from lib.team_registry import teams_match

from .odds_client import slate_to_df
from .sp_projections import PROJECTION_VERSION, ensure_fresh_projections, read_local_slate


def _read_archive_slate_raw(year: int, *, week0: bool = False) -> dict[str, Any] | None:
    """Load richest archived Onyx slate — Week 0 lives in the w1_5000 snapshot."""
    cache = DATA_DIR / "onyx_slate_cache"
    if week0:
        candidates = [
            cache / f"onyx_slate_{year}_w0.json",
            cache / f"onyx_slate_{year}_w1_5000.json",
            cache / f"onyx_slate_{year}_w1_500.json",
        ]
    else:
        candidates = [
            cache / f"onyx_slate_{year}_w1_5000.json",
            cache / f"onyx_slate_{year}_w1_500.json",
            cache / f"onyx_slate_{year}_w1.json",
        ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            slate = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if slate.get("rows"):
            return slate
    return None


def _filter_slate_to_games(df: pd.DataFrame, games: list[dict[str, Any]]) -> pd.DataFrame:
    if df.empty or not games:
        return df.iloc[0:0]

    def _row_in_week(r: pd.Series) -> bool:
        for g in games:
            if teams_match(r.get("home"), g.get("home")) and teams_match(r.get("away"), g.get("away")):
                return True
        return False

    return df.loc[df.apply(_row_in_week, axis=1)].copy()


def _load_week0_slate_df(
    year: int,
    *,
    sport: str,
    historical: bool,
    reprice: bool = False,
) -> pd.DataFrame:
    """Week 0 opening weekend — archived props from the Aug slate snapshot."""
    games = list_games_for_display_week(year, 0, sport=sport)
    slate = _read_archive_slate_raw(year, week0=True)
    if not slate:
        slate = read_local_slate(year=year, week=1)
    if not slate:
        return pd.DataFrame()

    if not historical and reprice:
        slate = ensure_fresh_projections(slate) or slate

    df = slate_to_df(slate)
    if df.empty:
        return df

    df = _filter_slate_to_games(df, games)
    if "projectionVersion" not in df.columns:
        df = df.copy()
        df["projectionVersion"] = slate.get("projectionVersion", PROJECTION_VERSION)
    from .csv_log import log_slate_rows

    log_slate_rows(df, tab=None, year=int(year), week=int(week if not week0 else 0), dedupe=True)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_slate_df(
    sport: str,
    year: int,
    week: int,
    *,
    historical: bool = False,
    reprice: bool = False,
    log: bool = True,
) -> pd.DataFrame:
    """Load slate from local JSON. Repricing is opt-in (Player Projections reprices props separately)."""
    from .sport_context import get_sport_config

    if int(week) == 0 and not get_sport_config()["has_week0"]:
        week = 1
    if int(week) == 0:
        return _load_week0_slate_df(int(year), sport=sport, historical=historical, reprice=reprice)

    slate = read_local_slate(year=year, week=week)
    if not historical and reprice:
        slate = ensure_fresh_projections(slate)
    df = slate_to_df(slate)
    if not df.empty and "projectionVersion" not in df.columns and slate:
        df = df.copy()
        df["projectionVersion"] = slate.get("projectionVersion", PROJECTION_VERSION)
    if log:
        from .csv_log import log_slate_rows

        log_slate_rows(df, tab=None, year=int(year), week=int(week), dedupe=True)
    return df
