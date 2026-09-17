#!/usr/bin/env python3
"""Backfill pricing-engine projections + stored odds for completed weeks."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from pathlib import Path

os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
logging.getLogger("streamlit").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")
warnings.filterwarnings("ignore", message=".*Session state.*")

ROOT = Path(__file__).resolve().parents[1]
STREAMLIT = ROOT / "streamlit_app"
if str(STREAMLIT) not in sys.path:
    sys.path.insert(0, str(STREAMLIT))

from lib.config import load_env
from lib.pricing_history import (
    build_week_capture,
    daily_snapshot_times,
    load_snapshot_df,
    save_capture,
)
from lib.sport_context import SPORT_CFB, SPORT_NFL

load_env()

DEFAULT_YEAR = 2026
CFB_WEEKS = (0, 1, 2)
NFL_WEEKS = (1,)
# Historical JSONL days that overlap early-season slates
HISTORICAL_DAYS = ("2026-09-01", "2026-09-02", "2026-09-07", "2026-09-08")


def _parse_weeks(raw: str | None, default: tuple[int, ...]) -> list[int]:
    if not raw:
        return list(default)
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def backfill_week(
    sport: str,
    year: int,
    week: int,
    *,
    include_daily: bool,
    run_props: bool,
) -> list[Path]:
    saved: list[Path] = []

    payload = build_week_capture(
        sport, year, week,
        capture_type="backfill",
        source="game_board_cache+props_board",
        run_props=run_props,
    )
    if not payload["games"]:
        print(f"  skip {sport} w{week}: no games found")
        return saved

    path = save_capture(payload)
    saved.append(path)
    print(
        f"  saved backfill {sport} w{week}: {len(payload['games'])} games, "
        f"{sum(len(g.get('props') or []) for g in payload['games'])} props -> {path.name}"
    )

    if not include_daily:
        return saved

    snap_df = load_snapshot_df(HISTORICAL_DAYS)
    if snap_df.empty:
        return saved

    proj_lookup = {
        (str(g.get("home") or ""), str(g.get("away") or "")): g
        for g in payload["games"]
    }
    day_times = daily_snapshot_times(snap_df)
    for day, times in day_times.items():
        for label, ts in (("daily_open", times.get("open")), ("daily_close", times.get("close"))):
            if ts is None:
                continue
            daily = build_week_capture(
                sport, year, week,
                capture_type=label,
                captured_at=ts.isoformat(),
                source=f"streamlit_odds_snapshots:{day}",
                snapshot_df=snap_df,
                snapshot_at=ts,
                run_props=False,
                run_sim=False,
                projection_lookup=proj_lookup,
            )
            daily_games = [g for g in daily["games"] if g.get("odds_quotes")]
            if not daily_games:
                continue
            daily["games"] = daily_games
            p = save_capture(daily)
            saved.append(p)
            print(f"  saved {label} {day} {sport} w{week}: {len(daily_games)} games -> {p.name}")

    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill pricing history from stored odds/lines.")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--cfb-weeks", default=",".join(str(w) for w in CFB_WEEKS))
    parser.add_argument("--nfl-weeks", default=",".join(str(w) for w in NFL_WEEKS))
    parser.add_argument("--no-daily", action="store_true", help="Skip historical daily open/close from JSONL")
    parser.add_argument("--no-props", action="store_true", help="Skip player prop projection recompute")
    args = parser.parse_args()

    cfb_weeks = _parse_weeks(args.cfb_weeks, CFB_WEEKS)
    nfl_weeks = _parse_weeks(args.nfl_weeks, NFL_WEEKS)
    run_props = not args.no_props
    include_daily = not args.no_daily

    print(f"Backfilling pricing history for {args.year} (props={'on' if run_props else 'off'})")
    all_paths: list[Path] = []

    for week in cfb_weeks:
        print(f"CFB week {week}")
        all_paths.extend(
            backfill_week(SPORT_CFB, args.year, week, include_daily=include_daily, run_props=run_props)
        )

    for week in nfl_weeks:
        print(f"NFL week {week}")
        all_paths.extend(
            backfill_week(SPORT_NFL, args.year, week, include_daily=include_daily, run_props=run_props)
        )

    print(f"Done — {len(all_paths)} capture file(s) written under data/pricing_history/")


if __name__ == "__main__":
    main()
