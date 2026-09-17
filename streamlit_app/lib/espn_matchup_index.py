"""Fast ESPN row lookup by canonical matchup — week board + season fallback."""
from __future__ import annotations

from typing import Any

import pandas as pd

from lib.team_registry import resolve_canonical, team_key, teams_match


def _row_score(row: pd.Series | dict) -> tuple[int, int, int]:
    """Sort key: prefer completed games with real scores."""
    if isinstance(row, pd.Series):
        row = row.to_dict()
    completed = 1 if row.get("completed") else 0
    try:
        hs = int(row.get("home_score")) if row.get("home_score") not in (None, "") else -1
        aws = int(row.get("away_score")) if row.get("away_score") not in (None, "") else -1
    except (TypeError, ValueError):
        hs, aws = -1, -1
    has_scores = 1 if hs >= 0 and aws >= 0 else 0
    return (completed, has_scores, hs + aws)


def build_espn_matchup_index(espn: pd.DataFrame) -> dict[str, pd.Series]:
    """Map away|home team keys to best ESPN row for that pairing."""
    if espn.empty:
        return {}

    buckets: dict[str, list[pd.Series]] = {}
    for _, row in espn.iterrows():
        home = resolve_canonical(str(row.get("home") or "")) or str(row.get("home") or "")
        away = resolve_canonical(str(row.get("away") or "")) or str(row.get("away") or "")
        if not home or not away:
            continue
        key = f"{team_key(away)}|{team_key(home)}"
        buckets.setdefault(key, []).append(row)

    out: dict[str, pd.Series] = {}
    for key, rows in buckets.items():
        best = max(rows, key=_row_score)
        out[key] = best
    return out


def lookup_espn_row(
    season_index: dict[str, pd.Series],
    week_espn: pd.DataFrame,
    home: str,
    away: str,
) -> pd.Series | None:
    """Week scoreboard first, then cached season index."""
    if not week_espn.empty:
        matches = week_espn[
            week_espn.apply(
                lambda r: teams_match(r.get("home"), home) and teams_match(r.get("away"), away),
                axis=1,
            )
        ]
        if not matches.empty:
            if len(matches) == 1:
                return matches.iloc[0]
            return max((matches.iloc[i] for i in range(len(matches))), key=_row_score)

    key = f"{team_key(away)}|{team_key(home)}"
    hit = season_index.get(key)
    if hit is not None:
        return hit

    for k, row in season_index.items():
        a_key, h_key = k.split("|", 1)
        if teams_match(a_key, away) and teams_match(h_key, home):
            return row
    return None
