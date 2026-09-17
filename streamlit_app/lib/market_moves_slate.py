"""Build spread/total slate rows for Market Moves from the full week board."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .matchup_board import load_matchup_board
from .nfl_projections import price_game


def load_market_moves_slate(sport: str, year: int, week: int, *, historical: bool = False) -> pd.DataFrame:
    """Full schedule for the selected week + model projections (not Onyx +EV subset)."""
    _ = historical
    board = load_matchup_board(sport, int(year), int(week))
    if board.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, br in board.iterrows():
        home = str(br.get("home") or "")
        away = str(br.get("away") or "")
        if not home or not away:
            continue
        home_canon = str(br.get("home_canon") or home)
        away_canon = str(br.get("away_canon") or away)
        matchup = f"{away} @ {home}"

        priced = (
            price_game(
                home_canon,
                away_canon,
                season=int(year),
                display_week=int(week),
                refresh=False,
            )
            or {}
        )
        spread_model = priced.get("spread")
        total_model = priced.get("total")

        base = {
            "matchup": matchup,
            "home": home,
            "away": away,
            "startDate": br.get("date"),
            "homeLogo": br.get("home_logo"),
            "awayLogo": br.get("away_logo"),
        }

        if spread_model is not None:
            rows.append(
                {
                    **base,
                    "group": "spread",
                    "market": "Spread",
                    "side": "home_cover",
                    "modelProj": spread_model,
                }
            )

        if total_model is not None:
            rows.append(
                {
                    **base,
                    "group": "total",
                    "market": "Total",
                    "side": "over",
                    "modelProj": total_model,
                }
            )

    return pd.DataFrame(rows)
