#!/usr/bin/env python3
"""In-play pricing capture — all live CFB/NFL games, PBP-driven market rows."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STREAMLIT = ROOT / "streamlit_app"
if str(STREAMLIT) not in sys.path:
    sys.path.insert(0, str(STREAMLIT))

from lib.config import load_env
from pricing_engine.inplay_runner import run_all, run_sport_week

load_env()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture in-play pricing for live games")
    parser.add_argument("--sport", choices=["cfb", "nfl"], default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--week", type=int, default=None)
    args = parser.parse_args()

    if args.sport:
        from lib.sport_context import init_sport, set_sport, get_sport_config

        init_sport()
        set_sport(args.sport)
        cfg = get_sport_config(args.sport)
        year = args.year or int(cfg.get("default_year") or 2026)
        week = args.week or int(cfg.get("default_week") or 1)
        results = run_sport_week(args.sport, year, week)
        print(json.dumps({"sport": args.sport, "year": year, "week": week, "games": results}, indent=2))
    else:
        summary = run_all()
        print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
