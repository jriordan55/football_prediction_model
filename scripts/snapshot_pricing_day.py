#!/usr/bin/env python3
"""Daily pricing capture — open and close snapshots with timestamps."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STREAMLIT = ROOT / "streamlit_app"
if str(STREAMLIT) not in sys.path:
    sys.path.insert(0, str(STREAMLIT))

from lib.config import load_env
from lib.pricing_history import (
    build_game_record,
    build_games_from_props,
    load_game_cards_file,
    load_props_board,
    merge_quotes,
    quotes_from_game_card,
    save_capture,
    SCHEMA_VERSION,
    METHODOLOGY,
    _utc_now_iso,
)
from lib.sport_context import SPORT_CFB, SPORT_NFL, get_sport_config

load_env()


def _fetch_live_quotes(sport: str) -> list[dict]:
    try:
        from lib.fourc_odds_client import fetch_board, fourc_odds_enabled, parse_board_game_quotes

        if not fourc_odds_enabled():
            return []
        board = fetch_board(sport)
        df = parse_board_game_quotes(board)
        if df.empty:
            return []
        return df.to_dict("records")
    except Exception as exc:
        print(f"  warn: live 4C fetch failed for {sport}: {exc}")
        return []


def _quotes_for_game(all_quotes: list[dict], home: str, away: str) -> list[dict]:
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    hits: list[dict] = []
    for q in all_quotes:
        rh = str(q.get("home") or "")
        ra = str(q.get("away") or "")
        if (cfb_match(rh, home) and cfb_match(ra, away)) or (nfl_match(rh, home) and nfl_match(ra, away)):
            hits.append(q)
    return hits


def capture_sport_week(
    sport: str,
    year: int,
    week: int,
    *,
    capture_type: str,
    captured_at: str,
) -> Path | None:
    cards = load_game_cards_file(sport, year, week)
    props_rows = load_props_board(sport, year, week)
    if not cards and props_rows:
        cards = build_games_from_props(props_rows)
    if not cards:
        print(f"  skip {sport} w{week}: empty slate")
        return None

    live_quotes = _fetch_live_quotes(sport)
    games = []
    for card in cards:
        home = str(card.get("home") or "")
        away = str(card.get("away") or "")
        live = _quotes_for_game(live_quotes, home, away) if live_quotes else []
        card_quotes = merge_quotes(quotes_from_game_card(card), live)
        if live:
            card = dict(card)
        games.append(
            build_game_record(
                sport,
                card,
                year=year,
                week=week,
                props_rows=props_rows,
                extra_quotes=live,
                run_props=True,
            )
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "year": int(year),
        "week": int(week),
        "captured_at": captured_at,
        "capture_type": capture_type,
        "methodology": METHODOLOGY,
        "source": "fourc_live+game_board_cache",
        "games": games,
    }
    path = save_capture(payload)
    print(f"  saved {capture_type} {sport} w{week}: {len(games)} games -> {path.name}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Save daily pricing open/close snapshots.")
    parser.add_argument(
        "--capture-type",
        choices=("daily_open", "daily_close", "live"),
        default=None,
        help="Capture label (default: both daily_open and daily_close)",
    )
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--cfb-week", type=int, default=None)
    parser.add_argument("--nfl-week", type=int, default=None)
    args = parser.parse_args()

    types = [args.capture_type] if args.capture_type else ("daily_open", "daily_close")
    ts = _utc_now_iso()

    for capture_type in types:
        print(f"Running {capture_type} at {ts}")

        for sport in (SPORT_CFB, SPORT_NFL):
            cfg = get_sport_config(sport)
            year = int(args.year or cfg.get("default_year", 2026))
            week = int(
                args.cfb_week if sport == SPORT_CFB and args.cfb_week is not None
                else args.nfl_week if sport == SPORT_NFL and args.nfl_week is not None
                else cfg.get("default_week", 1)
            )
            capture_sport_week(sport, year, week, capture_type=capture_type, captured_at=ts)

    print("Done.")


if __name__ == "__main__":
    main()
