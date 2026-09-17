"""Historical sharp-line rows and post-game grading."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .games import list_games_for_display_week, lookup_completed_game
from .config import CSV_LOG_DIR
from .projection_results import grade_side
from .sharp_edges import _is_spread_key, _is_team_total_key, _is_total_key
from .team_registry import teams_match


def filter_lines_to_display_week(df: pd.DataFrame, year: int, week: int) -> pd.DataFrame:
    """Keep odds rows whose event matches the selected display week."""
    if df.empty:
        return df
    games = list_games_for_display_week(int(year), int(week), fbs_only=False)
    if not games:
        return df

    def _row_in_week(row: pd.Series) -> bool:
        for g in games:
            if teams_match(row.get("home"), g.get("home")) and teams_match(row.get("away"), g.get("away")):
                return True
        return False

    return df.loc[df.apply(_row_in_week, axis=1)].copy()


def load_logged_sharp_rows(year: int, week: int) -> list[dict[str, Any]]:
    """Load archived sharp-line snapshots for a display week."""
    if not CSV_LOG_DIR.exists():
        return []
    games = list_games_for_display_week(int(year), int(week), fbs_only=False)
    game_pairs = {(str(g.get("away") or ""), str(g.get("home") or "")) for g in games}
    frames: list[pd.DataFrame] = []
    usecols = [
        "logged_at_utc",
        "record_type",
        "year",
        "week",
        "event",
        "home",
        "away",
        "market_key",
        "market",
        "pick",
        "side",
        "line",
        "ref_line",
        "fair_price",
        "implied_pct",
        "ev_pct",
        "edge_points",
        "quarter_kelly",
        "best_book_id",
        "best_price",
        "best_line",
        "books_json",
    ]
    for fp in sorted(CSV_LOG_DIR.glob("streamlit_feed_*.csv")):
        try:
            df = pd.read_csv(fp, usecols=lambda c: c in usecols, low_memory=False)
        except (OSError, ValueError):
            try:
                df = pd.read_csv(fp, low_memory=False)
            except (OSError, ValueError):
                continue
            df = df[[c for c in usecols if c in df.columns]]
        part = df[
            (df["record_type"].astype(str) == "sharp_line")
            & (pd.to_numeric(df.get("year"), errors="coerce") == int(year))
            & (pd.to_numeric(df.get("week"), errors="coerce") == int(week))
        ]
        if not part.empty:
            frames.append(part)
    if not frames:
        return []
    all_rows = pd.concat(frames, ignore_index=True).sort_values("logged_at_utc")
    dedupe_cols = [c for c in ["home", "away", "market_key", "side", "line", "pick"] if c in all_rows.columns]
    if dedupe_cols:
        all_rows = all_rows.drop_duplicates(subset=dedupe_cols, keep="last")
    out: list[dict[str, Any]] = []
    for _, r in all_rows.iterrows():
        d = r.to_dict()
        home = str(d.get("home") or "")
        away = str(d.get("away") or "")
        if game_pairs and not any(teams_match(away, a) and teams_match(home, h) for a, h in game_pairs):
            continue
        try:
            books = json.loads(d.get("books_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            books = {}
        best_bid = d.get("best_book_id")
        d["best"] = {
            "book_id": best_bid,
            "price": d.get("best_price"),
            "line": d.get("best_line"),
        }
        d["books"] = books if isinstance(books, dict) else {}
        d["fair_price"] = d.get("fair_price")
        d["cell_flags"] = {str(best_bid): True} if best_bid else {}
        out.append(d)
    return out


def grade_sharp_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Grade a sharp pick against final score when the game is complete."""
    home = str(row.get("home") or "")
    away = str(row.get("away") or "")
    game = lookup_completed_game(int(row.get("year") or 2026), home, away)
    if not game:
        return None
    home_pts = int(game["homePoints"])
    away_pts = int(game["awayPoints"])
    mk = str(row.get("market_key") or "").lower()
    side = str(row.get("side") or "").lower()
    line_raw = row.get("ref_line") if row.get("ref_line") is not None else row.get("line")
    try:
        line_f = float(line_raw)
    except (TypeError, ValueError):
        return None

    if _is_spread_key(mk):
        grade = grade_side(
            side="home_cover" if side == "home" else "away_cover",
            line=line_f,
            home_points=home_pts,
            away_points=away_pts,
            market="Spread",
        )
    elif _is_team_total_key(mk):
        pick = str(row.get("pick") or "").lower()
        ou = "under" if "under" in pick or side.endswith("_under") else "over"
        team = home if teams_match(home, pick) or home.lower() in pick else away
        if not teams_match(team, away) and not teams_match(team, home):
            if teams_match(home, pick.split()[0] if pick else ""):
                team = home
            else:
                team = away
        actual = home_pts if teams_match(team, home) else away_pts
        if abs(actual - line_f) < 0.001:
            return {"result": "push", "actual": actual}
        if ou == "over":
            result = "hit" if actual > line_f else "miss"
        else:
            result = "hit" if actual < line_f else "miss"
        grade = {"result": result, "actual": actual}
    elif _is_total_key(mk):
        ou = side if side in ("over", "under") else ("under" if "under" in str(row.get("pick") or "").lower() else "over")
        grade = grade_side(
            side=ou,
            line=line_f,
            home_points=home_pts,
            away_points=away_pts,
            market="Total",
        )
    else:
        return None

    if grade.get("result") is None:
        return None
    return {"result": grade.get("result"), "actual": grade.get("actual")}


def enrich_sharp_rows_with_grades(rows: list[dict[str, Any]], *, year: int, week: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["year"] = year
        d["week"] = week
        graded = grade_sharp_row(d)
        if graded:
            d["pick_result"] = graded.get("result")
            d["actual"] = graded.get("actual")
        out.append(d)
    return out
