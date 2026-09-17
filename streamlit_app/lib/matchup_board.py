"""Matchup game picker — Week 0 + historical CFBD schedule."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib.broadcast_logos import broadcast_logo_url
from lib.games import list_games_for_display_week, merge_completed
from lib.espn_client import (
    fetch_scoreboard_cached,
    fetch_scoreboard_season_cached,
)
from lib.espn_matchup_index import build_espn_matchup_index, lookup_espn_row
from lib.game_status import game_is_final
from lib.team_registry import official_team_abbr, resolve_canonical


@st.cache_data(ttl=300, show_spinner=False)
def load_matchup_board(
    sport: str,
    year: int,
    week: int,
    tab: str | None = None,
) -> pd.DataFrame:
    cfbd_games = list_games_for_display_week(year, week, sport=sport)
    if not cfbd_games:
        # Fallback to ESPN for upcoming slates
        return fetch_scoreboard_cached(week=max(week, 1), year=year, tab=tab, sport=sport)

    # Week scoreboard is fast; season index fills gaps (many Week 1 games aren't on ESPN week=1).
    espn_season = fetch_scoreboard_season_cached(year, tab=tab, sport=sport)
    season_index = build_espn_matchup_index(espn_season)
    if int(week) <= 0:
        espn_week = pd.DataFrame()
    else:
        espn_week = fetch_scoreboard_cached(week=max(week, 1), year=year, tab=tab, sport=sport)

    rows = []
    for g in cfbd_games:
        home, away = g["home"], g["away"]
        espn_row = lookup_espn_row(season_index, espn_week, home, away)

        cfbd_completed = bool(g.get("completed"))
        home_pts, away_pts = g.get("homePoints"), g.get("awayPoints")
        espn_completed = False
        if espn_row is not None:
            espn_completed = bool(espn_row.get("completed"))
            if espn_row.get("home_score") is not None:
                home_pts = espn_row.get("home_score")
            if espn_row.get("away_score") is not None:
                away_pts = espn_row.get("away_score")

        status_completed = cfbd_completed or espn_completed
        game = merge_completed(
            {"completed": status_completed, "homePoints": home_pts, "awayPoints": away_pts},
            home,
            away,
            home_pts=home_pts,
            away_pts=away_pts,
            status_completed=status_completed,
            sport=sport,
        )
        start = g.get("startDate") or (espn_row.get("date") if espn_row is not None else None)
        is_final = game_is_final(
            completed=bool(game and game.get("completed")),
            home_pts=game.get("homePoints") if game else home_pts,
            away_pts=game.get("awayPoints") if game else away_pts,
            start_date=start,
        )

        broadcast = espn_row.get("broadcast") if espn_row is not None else None
        raw_bc_logo = espn_row.get("broadcast_logo") if espn_row is not None else None
        bc_logo = (
            broadcast_logo_url(str(broadcast), str(raw_bc_logo or ""))
            if broadcast
            else (str(raw_bc_logo) if raw_bc_logo else None)
        )

        rows.append(
            {
                "event_id": espn_row.get("event_id") if espn_row is not None else g.get("id"),
                "home": home,
                "away": away,
                "home_canon": resolve_canonical(home) or home,
                "away_canon": resolve_canonical(away) or away,
                "home_abbr": (
                    espn_row.get("home_abbr")
                    if espn_row is not None and espn_row.get("home_abbr")
                    else official_team_abbr(home, year=int(year))
                ),
                "away_abbr": (
                    espn_row.get("away_abbr")
                    if espn_row is not None and espn_row.get("away_abbr")
                    else official_team_abbr(away, year=int(year))
                ),
                "home_logo": espn_row.get("home_logo") if espn_row is not None else None,
                "away_logo": espn_row.get("away_logo") if espn_row is not None else None,
                "broadcast": broadcast,
                "broadcast_logo": bc_logo,
                "date": g.get("startDate") or (espn_row.get("date") if espn_row is not None else None),
                "venue": g.get("venue") or (espn_row.get("venue") if espn_row is not None else None),
                "venue_city": espn_row.get("venue_city") if espn_row is not None else None,
                "venue_state": espn_row.get("venue_state") if espn_row is not None else None,
                "indoor": espn_row.get("indoor") if espn_row is not None else None,
                "lat": espn_row.get("lat") if espn_row is not None else None,
                "lon": espn_row.get("lon") if espn_row is not None else None,
                "week": week,
                "completed": is_final,
                "home_score": game.get("homePoints") if game and is_final else None,
                "away_score": game.get("awayPoints") if game and is_final else None,
                "label": f"{away} @ {home}",
            }
        )

    return pd.DataFrame(rows)
