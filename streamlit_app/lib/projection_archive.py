"""Archive graded projections for finished games — remove from live Streamlit boards."""
from __future__ import annotations

import hashlib
import math
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from .config import CSV_LOG_DIR
from .games import lookup_completed_game, merge_completed
from .espn_client import fetch_game_summary
from .game_status import game_is_final, game_past_kickoff
from .matchup_board import load_matchup_board
from .matchup_prop_enrich import resolve_prop_projection
from .projection_results import grade_side
from .prop_results import grade_player_prop, model_pick_side, parse_espn_boxscore, stat_from_box
from .team_registry import resolve_canonical, teams_match

from .sport_context import cache_sport, projection_grades_filename

_LOCK = threading.Lock()
_ARCHIVED_KEYS: set[str] = set()


def _grades_path() -> Path:
    return CSV_LOG_DIR / projection_grades_filename()


def _safe_int_pts(val: Any) -> int | None:
    if val is None:
        return None
    try:
        f = float(val)
        if not math.isfinite(f):
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _matchup_key(home: str | None, away: str | None) -> tuple[str, str]:
    return (resolve_canonical(home) or str(home or ""), resolve_canonical(away) or str(away or ""))


def _row_archive_key(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("year") or ""),
        str(row.get("week") or ""),
        str(row.get("record_type") or ""),
        str(row.get("matchup") or ""),
        str(row.get("player") or ""),
        str(row.get("market") or ""),
        str(row.get("line") or ""),
        str(row.get("side") or ""),
        str(row.get("spread_line") or ""),
        str(row.get("total_line") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def _result_grade(result: str | None) -> str:
    if result == "hit":
        return "WIN"
    if result == "miss":
        return "LOSS"
    if result == "push":
        return "PUSH"
    return "—"


@lru_cache(maxsize=1)
def _load_existing_keys() -> set[str]:
    if not _grades_path().exists():
        return set()
    try:
        df = pd.read_csv(_grades_path(), usecols=["archive_key"], low_memory=False)
        return set(df["archive_key"].dropna().astype(str))
    except (OSError, ValueError, KeyError):
        return set()


def _append_grades(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    existing = _load_existing_keys() | _ARCHIVED_KEYS
    fresh: list[dict[str, Any]] = []
    for row in rows:
        key = _row_archive_key(row)
        if key in existing:
            continue
        row["archive_key"] = key
        fresh.append(row)
        _ARCHIVED_KEYS.add(key)
    if not fresh:
        return 0

    frame = pd.DataFrame(fresh)
    frame.insert(0, "graded_at_utc", _utc_now())

    _grades_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if _grades_path().exists() and _grades_path().stat().st_size > 0:
            try:
                prior = pd.read_csv(_grades_path(), low_memory=False)
                combined = pd.concat([prior, frame], ignore_index=True)
            except (OSError, pd.errors.EmptyDataError, ValueError):
                combined = frame
        else:
            combined = frame
        combined.to_csv(_grades_path(), index=False)
    _load_existing_keys.cache_clear()
    return len(fresh)


@lru_cache(maxsize=8)
def _box_stats_for_event(event_id: str) -> dict[str, dict[str, float]]:
    if not event_id:
        return {}
    try:
        summary = fetch_game_summary(str(event_id))
        return parse_espn_boxscore(summary)
    except Exception:
        return {}


@lru_cache(maxsize=16)
def load_final_games(year: int, week: int, tab: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (home_canon, away_canon) -> final game metadata."""
    board = load_matchup_board(cache_sport(), int(year), int(week), tab=tab)
    finals: dict[tuple[str, str], dict[str, Any]] = {}

    if board.empty:
        return finals

    for _, row in board.iterrows():
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        start = row.get("date")
        home_pts = row.get("home_score")
        away_pts = row.get("away_score")
        completed = bool(row.get("completed"))

        cfbd = lookup_completed_game(int(year), home, away)
        merged = merge_completed(
            cfbd,
            home,
            away,
            home_pts=home_pts,
            away_pts=away_pts,
            status_completed=completed or bool(cfbd and cfbd.get("completed")),
        )
        if merged:
            home_pts = merged.get("homePoints", home_pts)
            away_pts = merged.get("awayPoints", away_pts)
            completed = bool(merged.get("completed"))

        home_i = _safe_int_pts(home_pts)
        away_i = _safe_int_pts(away_pts)
        is_final = game_is_final(
            completed=completed,
            home_pts=home_i,
            away_pts=away_i,
            start_date=start,
        )
        if not is_final and game_past_kickoff(start) and home_i is not None and away_i is not None:
            is_final = True

        if not is_final or home_i is None or away_i is None:
            continue

        key = _matchup_key(home, away)
        finals[key] = {
            "home": home,
            "away": away,
            "home_canon": key[0],
            "away_canon": key[1],
            "home_score": home_i,
            "away_score": away_i,
            "margin": home_i - away_i,
            "total": home_i + away_i,
            "start_date": start,
            "event_id": row.get("event_id"),
            "matchup": f"{away} @ {home}",
        }
    return finals


def _matchup_is_final(home: str | None, away: str | None, finals: dict[tuple[str, str], dict[str, Any]]) -> bool:
    hk, ak = _matchup_key(home, away)
    if (hk, ak) in finals:
        return True
    for (fh, fa) in finals:
        if teams_match(hk, fh) and teams_match(ak, fa):
            return True
    return False


def _lookup_final(home: str | None, away: str | None, finals: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    hk, ak = _matchup_key(home, away)
    hit = finals.get((hk, ak))
    if hit:
        return hit
    for (fh, fa), game in finals.items():
        if teams_match(hk, fh) and teams_match(ak, fa):
            return game
    return None


def _grade_prop_row(row: dict[str, Any], game: dict[str, Any], *, year: int, week: int) -> dict[str, Any] | None:
    try:
        projection = float(row.get("modelProj"))
    except (TypeError, ValueError):
        projection = resolve_prop_projection(row, historical=True)
    if projection is None:
        return None

    model_side = model_pick_side(projection, row.get("line"))
    if not model_side:
        return None

    box_stats: dict[str, dict[str, float]] = {}
    event_id = str(game.get("event_id") or "")
    if event_id:
        box_stats = _box_stats_for_event(event_id)

    player = str(row.get("player") or "")
    market = str(row.get("market") or "")
    actual = stat_from_box(player, market, box_stats) if box_stats and player else None
    graded = grade_player_prop({**row, "side": model_side}, actual, grade_side=model_side)
    result = graded.get("result")
    if result is None and actual is None:
        return None

    ev_pct = row.get("ev_pct")
    if ev_pct is None and row.get("edgeDisplay") is not None:
        try:
            ev_pct = float(row.get("edgeDisplay"))
        except (TypeError, ValueError):
            ev_pct = None

    roi_f = _expected_roi_from_row(row)

    return {
        "year": year,
        "week": week,
        "record_type": "prop_projection",
        "matchup": game.get("matchup") or f"{game.get('away')} @ {game.get('home')}",
        "home": game.get("home"),
        "away": game.get("away"),
        "start_date": game.get("start_date") or row.get("startDate"),
        "event_id": event_id,
        "player": player,
        "position": row.get("position"),
        "market": market,
        "selection": row.get("selection"),
        "side": model_side,
        "line": row.get("line"),
        "price": row.get("price"),
        "book": row.get("book") or row.get("bookId"),
        "model_proj": projection,
        "ev_pct": ev_pct,
        "edge_display": row.get("edgeDisplay"),
        "roi": roi_f,
        "expected_roi": roi_f,
        "actual": graded.get("actual"),
        "result": result,
        "grade": _result_grade(result),
        "home_score": game.get("home_score"),
        "away_score": game.get("away_score"),
    }


def _expected_roi_from_row(row: dict[str, Any]) -> float | None:
    for key in ("expected_roi", "roi"):
        try:
            val = float(row.get(key))
            if math.isfinite(val):
                return val
        except (TypeError, ValueError):
            continue
    try:
        ev = row.get("ev_pct")
        if ev is not None:
            ev_f = float(ev)
            if math.isfinite(ev_f):
                return ev_f / 100.0
    except (TypeError, ValueError):
        pass
    return None


def _ev_pct_from_row(row: dict[str, Any]) -> float | None:
    try:
        ev = row.get("ev_pct")
        if ev is not None:
            ev_f = float(ev)
            if math.isfinite(ev_f):
                return ev_f
    except (TypeError, ValueError):
        pass
    roi = _expected_roi_from_row(row)
    if roi is not None:
        return round(roi * 100.0, 1)
    return None


def _prematch_prop_row(row: dict[str, Any], *, year: int, week: int) -> dict[str, Any]:
    home = row.get("home")
    away = row.get("away")
    matchup = row.get("matchup") or (f"{away} @ {home}" if home and away else "")
    roi_f = _expected_roi_from_row(row)
    try:
        projection = float(row.get("modelProj") or row.get("projection"))
    except (TypeError, ValueError):
        projection = None
    return {
        "year": year,
        "week": week,
        "record_type": "prop_prematch",
        "matchup": matchup,
        "home": home,
        "away": away,
        "start_date": row.get("startDate") or row.get("start"),
        "event_id": row.get("event_id"),
        "player": row.get("player"),
        "position": row.get("position"),
        "team": row.get("team") or row.get("teamLabel"),
        "market": row.get("market"),
        "selection": row.get("selection"),
        "side": row.get("side") or row.get("sideLabel"),
        "line": row.get("line"),
        "price": row.get("price"),
        "book": row.get("book") or row.get("bookId"),
        "model_proj": projection,
        "ev_pct": _ev_pct_from_row(row),
        "edge_display": row.get("edgeDisplay") or row.get("edgePct") or row.get("edge"),
        "roi": roi_f,
        "expected_roi": roi_f,
        "win_prob": row.get("winProb"),
        "prop_key": row.get("propKey"),
    }


def _prematch_game_rows(card: dict[str, Any], *, year: int, week: int) -> list[dict[str, Any]]:
    home = card.get("home")
    away = card.get("away")
    base = {
        "year": year,
        "week": week,
        "record_type": "game_prematch",
        "matchup": card.get("matchup") or card.get("label") or f"{away} @ {home}",
        "home": home,
        "away": away,
        "start_date": card.get("start"),
        "event_id": card.get("event_id"),
    }
    rows: list[dict[str, Any]] = []

    def _add(market: str, *, line: Any, model_proj: Any, side: str | None = None, price: Any = None) -> None:
        if line is None or model_proj is None:
            return
        rows.append(
            {
                **base,
                "market": market,
                "side": side,
                "line": line,
                "model_proj": model_proj,
                "price": price,
            }
        )

    _add("Spread", line=card.get("spread_line"), model_proj=card.get("spread_proj"))
    _add("Total", line=card.get("total_line"), model_proj=card.get("total_proj"))

    away_tt = card.get("away_team_over") or {}
    home_tt = card.get("home_team_over") or {}
    _add(
        "Away Team Total",
        line=away_tt.get("line"),
        model_proj=card.get("proj_away"),
        side="over",
        price=away_tt.get("price"),
    )
    _add(
        "Home Team Total",
        line=home_tt.get("line"),
        model_proj=card.get("proj_home"),
        side="over",
        price=home_tt.get("price"),
    )
    return rows


def archive_prematch_props(
    df: pd.DataFrame,
    *,
    year: int,
    week: int,
    tab: str | None = None,
) -> int:
    """Persist live player projections + pre-match expected ROI."""
    _ = tab
    if df.empty:
        return 0
    rows = [_prematch_prop_row(r.to_dict(), year=year, week=week) for _, r in df.iterrows()]
    return _append_grades(rows)


def archive_prematch_game_cards(
    cards: list[dict[str, Any]],
    *,
    year: int,
    week: int,
    tab: str | None = None,
) -> int:
    """Persist live game-line projections and collected market quotes."""
    _ = tab
    if not cards:
        return 0
    rows: list[dict[str, Any]] = []
    for card in cards:
        rows.extend(_prematch_game_rows(card, year=year, week=week))
    return _append_grades(rows)


def archive_matchup_props(
    props: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
    year: int,
    week: int,
    historical: bool = False,
    tab: str | None = None,
) -> int:
    """Persist matchup-detail Expected ROI props (live or graded)."""
    _ = tab
    if not props:
        return 0
    home = meta.get("home")
    away = meta.get("away")
    matchup = meta.get("label") or f"{away} @ {home}"
    rows: list[dict[str, Any]] = []
    for prop in props:
        row = {
            "player": prop.get("player"),
            "position": prop.get("position"),
            "team": prop.get("team"),
            "market": prop.get("prop") or prop.get("market"),
            "line": prop.get("line"),
            "side": prop.get("side"),
            "price": prop.get("price"),
            "book": prop.get("book"),
            "modelProj": prop.get("modelProj") or prop.get("projection"),
            "projection": prop.get("projection") or prop.get("modelProj"),
            "roi": prop.get("roi"),
            "expected_roi": prop.get("expected_roi") if prop.get("expected_roi") is not None else prop.get("roi"),
            "edgeDisplay": prop.get("edgePct"),
            "home": home,
            "away": away,
            "matchup": matchup,
            "startDate": meta.get("startDate"),
            "actual": prop.get("actual"),
            "result": prop.get("result"),
            "grade": prop.get("grade"),
        }
        if historical and prop.get("result"):
            game = {
                "home": home,
                "away": away,
                "matchup": matchup,
                "start_date": meta.get("startDate"),
                "event_id": meta.get("event_id"),
                "home_score": meta.get("home_score"),
                "away_score": meta.get("away_score"),
            }
            graded = _grade_prop_row(row, game, year=year, week=week)
            if graded:
                rows.append(graded)
                continue
        rows.append(_prematch_prop_row(row, year=year, week=week))
    return _append_grades(rows)


def persist_prop_board(
    df: pd.DataFrame,
    *,
    year: int,
    week: int,
    tab: str | None = None,
) -> pd.DataFrame:
    """Archive pre-match props, append daily CSV, merge post-game grades."""
    from .csv_log import log_prop_projections

    if df.empty:
        return df
    archive_prematch_props(df, year=year, week=week, tab=tab)
    log_prop_projections(df, tab=tab, year=year, week=week, dedupe=True)
    return archive_and_merge_props(df, year=year, week=week, tab=tab)


def persist_game_cards(
    cards: list[dict[str, Any]],
    *,
    year: int,
    week: int,
    tab: str | None = None,
) -> list[dict[str, Any]]:
    """Archive pre-match game lines and grade finished games."""
    from .csv_log import log_game_projections

    if not cards:
        return cards
    archive_prematch_game_cards(cards, year=year, week=week, tab=tab)
    log_game_projections(cards, tab=tab, year=year, week=week, dedupe=True)
    return archive_and_filter_game_cards(cards, year=year, week=week, tab=tab, grade=True)


def _grade_game_card(card: dict[str, Any], game: dict[str, Any], *, year: int, week: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = {
        "year": year,
        "week": week,
        "record_type": "game_projection",
        "matchup": card.get("matchup") or game.get("matchup"),
        "home": game.get("home"),
        "away": game.get("away"),
        "start_date": card.get("start") or game.get("start_date"),
        "event_id": game.get("event_id"),
        "home_score": game.get("home_score"),
        "away_score": game.get("away_score"),
    }

    spread_proj = card.get("spread_proj")
    spread_line = card.get("spread_line")
    if spread_proj is not None and spread_line is not None:
        try:
            sp = float(spread_proj)
            sl = float(spread_line)
            side = "away_cover" if sp < sl else "home_cover"
            grade = grade_side(
                side=side,
                line=sl,
                home_points=int(game["home_score"]),
                away_points=int(game["away_score"]),
                market="Spread",
            )
            rows.append(
                {
                    **base,
                    "market": "Spread",
                    "side": side,
                    "line": sl,
                    "model_proj": sp,
                    "spread_proj": sp,
                    "spread_line": sl,
                    "actual": grade.get("actual"),
                    "result": grade.get("result"),
                    "grade": _result_grade(grade.get("result")),
                }
            )
        except (TypeError, ValueError):
            pass

    total_proj = card.get("total_proj")
    total_line = card.get("total_line")
    if total_proj is not None and total_line is not None:
        try:
            tp = float(total_proj)
            tl = float(total_line)
            side = "over" if tp > tl else "under"
            grade = grade_side(
                side=side,
                line=tl,
                home_points=int(game["home_score"]),
                away_points=int(game["away_score"]),
                market="Total",
            )
            rows.append(
                {
                    **base,
                    "market": "Total",
                    "side": side,
                    "line": tl,
                    "model_proj": tp,
                    "total_proj": tp,
                    "total_line": tl,
                    "actual": grade.get("actual"),
                    "result": grade.get("result"),
                    "grade": _result_grade(grade.get("result")),
                }
            )
        except (TypeError, ValueError):
            pass

    away_tt = card.get("away_team_over") or {}
    home_tt = card.get("home_team_over") or {}
    proj_away = card.get("proj_away")
    proj_home = card.get("proj_home")
    if away_tt.get("line") is not None and proj_away is not None:
        try:
            line = float(away_tt["line"])
            proj = float(proj_away)
            side = "over" if proj > line else "under"
            actual = int(game["away_score"])
            if abs(actual - line) < 0.001:
                result = "push"
            else:
                over_wins = actual > line
                wins = over_wins if side == "over" else not over_wins
                result = "hit" if wins else "miss"
            rows.append(
                {
                    **base,
                    "market": "Away Team Total",
                    "side": side,
                    "line": line,
                    "model_proj": proj,
                    "actual": actual,
                    "result": result,
                    "grade": _result_grade(result),
                }
            )
        except (TypeError, ValueError):
            pass
    if home_tt.get("line") is not None and proj_home is not None:
        try:
            line = float(home_tt["line"])
            proj = float(proj_home)
            side = "over" if proj > line else "under"
            actual = int(game["home_score"])
            if abs(actual - line) < 0.001:
                result = "push"
            else:
                over_wins = actual > line
                wins = over_wins if side == "over" else not over_wins
                result = "hit" if wins else "miss"
            rows.append(
                {
                    **base,
                    "market": "Home Team Total",
                    "side": side,
                    "line": line,
                    "model_proj": proj,
                    "actual": actual,
                    "result": result,
                    "grade": _result_grade(result),
                }
            )
        except (TypeError, ValueError):
            pass

    return rows


def archive_and_filter_props(
    df: pd.DataFrame,
    *,
    year: int,
    week: int,
    tab: str | None = None,
    keep_graded: bool = False,
) -> pd.DataFrame:
    """Grade finished-game props; drop completed rows unless keep_graded or week is archived."""
    if df.empty:
        return df

    graded = load_graded_props_df(year, week)
    if not graded.empty:
        return graded

    if keep_graded:
        return archive_and_merge_props(df, year=year, week=week, tab=tab)

    finals = load_final_games(year, week, tab=tab)
    if not finals:
        return df

    archive_rows: list[dict[str, Any]] = []
    keep_mask: list[bool] = []

    for _, row in df.iterrows():
        rd = row.to_dict()
        home, away = rd.get("home"), rd.get("away")
        if not _matchup_is_final(home, away, finals):
            keep_mask.append(True)
            continue
        keep_mask.append(False)
        game = _lookup_final(home, away, finals)
        if not game:
            continue
        graded_row = _grade_prop_row(rd, game, year=year, week=week)
        if graded_row:
            archive_rows.append(graded_row)

    _append_grades(archive_rows)
    return df.loc[pd.Series(keep_mask, index=df.index)].reset_index(drop=True)


def archive_and_merge_props(
    df: pd.DataFrame,
    *,
    year: int,
    week: int,
    tab: str | None = None,
) -> pd.DataFrame:
    """Grade finished games, append to CSV, and attach actual/result to board rows."""
    if df.empty:
        return df

    finals = load_final_games(year, week, tab=tab)
    if not finals:
        return df

    archive_rows: list[dict[str, Any]] = []
    out_rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        rd = row.to_dict()
        home, away = rd.get("home"), rd.get("away")
        game = _lookup_final(home, away, finals)
        if not game:
            out_rows.append(rd)
            continue
        graded = _grade_prop_row(rd, game, year=year, week=week)
        if graded:
            archive_rows.append(graded)
            rd["actual_stat"] = graded.get("actual")
            rd["prop_result"] = graded.get("result")
            rd["grade"] = graded.get("grade")
            rd["completed"] = True
        out_rows.append(rd)

    _append_grades(archive_rows)
    return pd.DataFrame(out_rows).reset_index(drop=True)


def _mark_finished_card(card: dict[str, Any], game: dict[str, Any]) -> dict[str, Any]:
    """Attach final score metadata so finished cards render at the bottom."""
    out = dict(card)
    out["completed"] = True
    out["home_score"] = game.get("home_score", out.get("home_score"))
    out["away_score"] = game.get("away_score", out.get("away_score"))
    out["final_margin"] = game.get("margin")
    out["final_total"] = game.get("total")
    return out


def archive_and_filter_game_cards(
    cards: list[dict[str, Any]],
    *,
    year: int,
    week: int,
    tab: str | None = None,
    grade: bool = True,
) -> list[dict[str, Any]]:
    """Grade finished-game cards, archive, return all cards (finished marked for bottom sort)."""
    if not cards:
        return cards

    finals = load_final_games(year, week, tab=tab)
    if not finals:
        return cards

    archive_rows: list[dict[str, Any]] = []
    marked: list[dict[str, Any]] = []

    for card in cards:
        home, away = card.get("home"), card.get("away")
        is_done = bool(card.get("completed")) or _matchup_is_final(home, away, finals)
        if is_done:
            game = _lookup_final(home, away, finals)
            if game:
                card = _mark_finished_card(card, game)
                if grade:
                    archive_rows.extend(_grade_game_card(card, game, year=year, week=week))
        marked.append(card)

    if grade:
        _append_grades(archive_rows)
    return marked


def grades_path() -> Path:
    return _grades_path()


@lru_cache(maxsize=8)
def load_graded_props_df(year: int, week: int) -> pd.DataFrame:
    """Historical prop rows with actual results for a completed display week."""
    if not _grades_path().exists():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(_grades_path(), low_memory=False)
    except (OSError, ValueError):
        return pd.DataFrame()
    mask = (
        (raw["year"] == int(year))
        & (raw["week"] == int(week))
        & (raw["record_type"].astype(str) == "prop_projection")
    )
    g = raw.loc[mask].copy()
    if g.empty:
        return g

    from .prop_pricing import prop_key_from_row, normalize_prop_market
    from .prop_board_enrich import prop_display_name

    rows: list[dict[str, Any]] = []
    for _, r in g.iterrows():
        d = {
            "year": int(year),
            "week": int(week),
            "player": r.get("player"),
            "team": r.get("team") or r.get("position"),
            "position": r.get("position"),
            "market": normalize_prop_market(r.get("market")),
            "line": r.get("line"),
            "side": r.get("side"),
            "price": r.get("price"),
            "book": r.get("book"),
            "modelProj": r.get("model_proj"),
            "projection": r.get("model_proj"),
            "home": r.get("home"),
            "away": r.get("away"),
            "matchup": r.get("matchup"),
            "startDate": r.get("start_date"),
            "actual_stat": r.get("actual"),
            "prop_result": r.get("result"),
            "grade": r.get("grade"),
            "ev_pct": r.get("ev_pct"),
            "roi": r.get("roi"),
            "expected_roi": r.get("expected_roi") or r.get("roi"),
            "completed": True,
            "historical": True,
        }
        d["propKey"] = prop_key_from_row(d) or ""
        d["prop_label"] = prop_display_name(d.get("market"))
        rows.append(d)
    return pd.DataFrame(rows)


def props_have_results(year: int, week: int) -> bool:
    """True when archived grades exist or every slate game is final."""
    if not load_graded_props_df(year, week).empty:
        return True
    from .games import list_games_for_display_week

    games = list_games_for_display_week(year, week, fbs_only=False)
    if not games:
        return False
    return all(g.get("completed") and g.get("homePoints") is not None for g in games)
