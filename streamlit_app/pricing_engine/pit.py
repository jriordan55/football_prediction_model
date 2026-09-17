"""Point-in-time helpers — no look-ahead for backtested pricing projections."""
from __future__ import annotations

from typing import Any

import pandas as pd


def is_completed_week(sport: str, year: int, week: int) -> bool:
    from lib.games import display_week_complete

    return bool(display_week_complete(int(year), int(week), sport=sport))


def game_is_final(game: dict[str, Any]) -> bool:
    hp = game.get("homePoints")
    if hp is None:
        hp = game.get("home_score")
    ap = game.get("awayPoints")
    if ap is None:
        ap = game.get("away_score")
    if hp is None or ap is None:
        return False
    if game.get("completed") is True:
        return True
    status = str(game.get("status") or "").lower()
    if isinstance(game.get("status"), dict):
        status = str(game["status"].get("state") or game["status"].get("name") or "").lower()
    if any(tok in status for tok in ("final", "complete", "closed")):
        return True
    return game.get("completed") is not False


def ratings_should_refresh(sport: str, year: int, week: int | None) -> bool:
    """Freeze rating pulls for completed display weeks."""
    if week is None:
        return True
    return not is_completed_week(sport, int(year), int(week))


def filter_gamelog_as_of(
    df: pd.DataFrame,
    season: int,
    as_of_week: int | None,
) -> pd.DataFrame:
    """Keep only games strictly before the target display week."""
    if df.empty or as_of_week is None:
        return df
    out = df.copy()
    wk = int(as_of_week)
    if "_season" in out.columns:
        cur = out["_season"].fillna(season).astype(int)
        if "week" in out.columns:
            wcol = pd.to_numeric(out["week"], errors="coerce")
            mask = (cur < int(season)) | ((cur == int(season)) & (wcol < wk))
            return out.loc[mask.fillna(False)].reset_index(drop=True)
        return out.loc[cur < int(season)].reset_index(drop=True)
    if "week" in out.columns:
        wcol = pd.to_numeric(out["week"], errors="coerce")
        return out.loc[wcol < wk].reset_index(drop=True)
    return out


def realized_roi_from_result(expected_roi: float | None, result: str | None) -> float | None:
    """Realized unit ROI from pregame expected ROI when the bet settles."""
    if expected_roi is None or result is None:
        return None
    r = str(result).lower()
    if r == "push":
        return 0.0
    if r == "hit":
        return float(expected_roi)
    if r == "miss":
        return -1.0
    return None
