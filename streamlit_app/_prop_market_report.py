"""ROI and hit rate by player prop market for weeks 0 and 1."""
from __future__ import annotations

import json
import math
import os
import sys
import warnings
from pathlib import Path
from typing import Any

os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
warnings.filterwarnings("ignore")

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import DATA_DIR, DEFAULT_YEAR
from lib.games import list_games_for_display_week
from lib.odds_client import slate_to_df
from lib.odds_math import ev_pct
from lib.projection_archive import _grade_prop_row, _lookup_final, load_final_games
from lib.prop_board import filter_main_prop_lines
from lib.prop_board_enrich import enrich_prop_board, prop_display_name
from lib.matchup_prop_enrich import has_real_market_price
from lib.prop_pricing import (
    is_combo_prop_market,
    normalize_prop_market,
    prop_key_from_row,
    sync_market_fields,
)
from lib.prop_reprice import reprice_props_df
from lib.slate_loader import _filter_slate_to_games, _read_archive_slate_raw
from lib.sp_projections import read_local_slate

REPORT_PROP_KEYS = frozenset({"pass_yds", "rush_yds", "rec_yds", "pass_tds", "receptions"})


def _flat_roi(price: float | None, result: str | None) -> float | None:
    if result not in ("hit", "miss", "push"):
        return None
    if result == "push":
        return 0.0
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p) or p == 0:
        return None
    if result == "hit":
        return p / 100.0 if p > 0 else 100.0 / abs(p)
    return -1.0


def _finite_float(val: Any) -> float | None:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _is_report_prop(row: dict[str, Any]) -> bool:
    if is_combo_prop_market(row):
        return False
    if row.get("synthetic"):
        return False
    player = str(row.get("player") or "").strip()
    if not player:
        return False
    if not str(row.get("market") or row.get("prop") or "").strip():
        return False
    if _finite_float(row.get("line")) is None:
        return False
    if not has_real_market_price(row):
        return False
    pk = prop_key_from_row(row)
    if not pk or pk not in REPORT_PROP_KEYS:
        return False
    book = str(row.get("book") or row.get("book_id") or "").strip().lower()
    return book not in {"model", "synthetic", "baseline"}


def _load_slate_week(year: int, week: int) -> pd.DataFrame:
    if week == 0:
        games = list_games_for_display_week(year, 0)
        slate = _read_archive_slate_raw(year, week0=True)
        if not slate:
            slate = read_local_slate(year=year, week=1)
    else:
        games = list_games_for_display_week(year, week)
        slate = read_local_slate(year=year, week=week)
    if not slate:
        return pd.DataFrame()

    df = slate_to_df(slate)
    if df.empty:
        return df
    if week == 0 and games:
        df = _filter_slate_to_games(df, games)
    return df


def _grade_week_from_slate(year: int, week: int) -> pd.DataFrame:
    df = _load_slate_week(year, week)
    if df.empty:
        return pd.DataFrame()

    rows = [sync_market_fields(r.to_dict()) for _, r in df.iterrows()]
    rows = [r for r in rows if _is_report_prop(r)]
    if not rows:
        return pd.DataFrame()

    board = pd.DataFrame(rows)
    board = filter_main_prop_lines(board)
    if board.empty:
        return board

    board = reprice_props_df(board, sim_count=150, skip_gamelog=False, skip_starters=True)
    board = enrich_prop_board(board)
    board["year"] = year
    board["week"] = week

    finals = load_final_games(year, week, tab="prop_report")
    if not finals:
        return pd.DataFrame()

    graded_rows: list[dict] = []
    for _, row in board.iterrows():
        rd = row.to_dict()
        game = _lookup_final(rd.get("home"), rd.get("away"), finals)
        if not game:
            continue
        g = _grade_prop_row(rd, game, year=year, week=week)
        if not g or g.get("result") is None:
            continue
        graded_rows.append(g)
    return pd.DataFrame(graded_rows)


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rows = []
    for _, r in df.iterrows():
        market = normalize_prop_market(r.get("market"))
        result = str(r.get("prop_result") or r.get("result") or "").lower()
        price = r.get("price")
        exp = None
        for key in ("ev_pct", "expected_roi", "roi"):
            try:
                val = r.get(key)
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    continue
                exp = float(val)
                break
            except (TypeError, ValueError):
                continue
        if exp is not None and abs(exp) <= 1.5:
            exp = exp * 100.0

        rows.append(
            {
                "prop_label": prop_display_name(market),
                "result": result,
                "price": price,
                "expected_roi_pct": exp,
                "realized_roi_pct": (_flat_roi(price, result) or 0) * 100
                if result in ("hit", "miss", "push")
                else None,
            }
        )
    return pd.DataFrame(rows)


def _summarize(frame: pd.DataFrame) -> pd.DataFrame:
    graded = frame[frame["result"].isin(["hit", "miss", "push"])].copy()
    if graded.empty:
        return pd.DataFrame()

    def _agg(g: pd.DataFrame) -> pd.Series:
        hits = int((g["result"] == "hit").sum())
        misses = int((g["result"] == "miss").sum())
        pushes = int((g["result"] == "push").sum())
        n_dec = hits + misses
        exp = g["expected_roi_pct"].dropna()
        real = g["realized_roi_pct"].dropna()
        return pd.Series(
            {
                "n": len(g),
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "hit_rate_pct": round(100 * hits / n_dec, 1) if n_dec else float("nan"),
                "avg_exp_roi_pct": round(exp.mean(), 1) if len(exp) else float("nan"),
                "avg_real_roi_pct": round(real.mean(), 1) if len(real) else float("nan"),
                "total_real_roi_pct": round(real.sum(), 1) if len(real) else float("nan"),
            }
        )

    return (
        graded.groupby("prop_label", sort=True)
        .apply(_agg, include_groups=False)
        .reset_index()
        .sort_values("n", ascending=False)
    )


def main() -> None:
    year = DEFAULT_YEAR
    print(f"Player prop market report — {year} CFB\n")
    print("Markets: passing/rushing/receiving yards, passing TDs, receptions.")
    print("Side graded: model pick (MINE vs line). Realized ROI = flat 1u at posted price.\n")

    for week in (0, 1):
        frame = _normalize_frame(_grade_week_from_slate(year, week))
        print("=" * 70)
        print(f"WEEK {week}")
        print("=" * 70)
        if frame.empty:
            print("No graded props.\n")
            continue

        summary = _summarize(frame)
        dec = frame[frame["result"].isin(["hit", "miss"])]
        hits = int((dec["result"] == "hit").sum())
        misses = int((dec["result"] == "miss").sum())
        real = frame["realized_roi_pct"].dropna()
        exp = frame["expected_roi_pct"].dropna()

        print(
            f"Overall: {hits}-{misses} ({100 * hits / (hits + misses):.1f}% hit), "
            f"avg exp ROI {exp.mean():+.1f}%, avg realized ROI {real.mean():+.1f}% "
            f"({len(real)} bets)\n"
        )
        print(
            summary.to_string(
                index=False,
                columns=["prop_label", "n", "hits", "misses", "hit_rate_pct", "avg_exp_roi_pct", "avg_real_roi_pct"],
            )
        )
        print()


if __name__ == "__main__":
    main()
