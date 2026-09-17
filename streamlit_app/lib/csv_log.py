"""Append-only CSV log for Streamlit odds and projections (timestamped, daily files)."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import CSV_LOG_DIR, csv_log_enabled

_LOCK = threading.Lock()
_LAST_FP: dict[str, str] = {}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _today_feed_path() -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    CSV_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return CSV_LOG_DIR / f"streamlit_feed_{day}.csv"


def _fingerprint(df: pd.DataFrame, meta: dict[str, Any]) -> str:
    cols = [c for c in df.columns if c not in {"logged_at_utc", "snapshot_at"}]
    use = df[cols].head(400).copy().fillna("")
    for c in cols:
        use[c] = use[c].astype(str)
    payload = use.to_csv(index=False) + json.dumps(meta, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _append_dataframe(path: Path, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    if path.exists() and path.stat().st_size > 0:
        try:
            existing = pd.read_csv(path, low_memory=False)
            combined = pd.concat([existing, frame], ignore_index=True)
        except (OSError, pd.errors.EmptyDataError, ValueError):
            combined = frame
    else:
        combined = frame
    combined.to_csv(path, index=False)


def append_csv_log(
    data: pd.DataFrame | list[dict] | None,
    *,
    record_type: str,
    source: str,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
    dedupe: bool = False,
    extra: dict[str, Any] | None = None,
) -> int:
    """Append rows to today's feed CSV. Returns number of rows written."""
    if not csv_log_enabled() or data is None:
        return 0

    df = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    if df.empty:
        return 0

    meta: dict[str, Any] = {
        "logged_at_utc": _utc_iso(),
        "record_type": record_type,
        "source": source,
        "tab": tab or "",
        "year": year,
        "week": week,
    }
    if extra:
        meta.update(extra)

    if dedupe:
        fp_key = f"{record_type}|{source}|{tab}|{year}|{week}"
        fp = _fingerprint(df, meta)
        if _LAST_FP.get(fp_key) == fp:
            return 0
        _LAST_FP[fp_key] = fp

    for key, val in meta.items():
        if key not in df.columns:
            df[key] = val

    lead = ["logged_at_utc", "record_type", "source", "tab", "year", "week"]
    other = [c for c in df.columns if c not in lead]
    frame = df[lead + other]

    path = _today_feed_path()
    with _LOCK:
        _append_dataframe(path, frame)
    return len(frame)


def log_odds_quotes(
    df: pd.DataFrame,
    *,
    source: str,
    tab: str | None = None,
    dedupe: bool = False,
    snapshot_at: str | None = None,
) -> int:
    if df.empty:
        return 0
    out = df.copy()
    if snapshot_at and "snapshot_at" not in out.columns:
        out["snapshot_at"] = snapshot_at
    return append_csv_log(
        out,
        record_type="odds_quote",
        source=source,
        tab=tab,
        dedupe=dedupe,
    )


def log_slate_rows(
    df: pd.DataFrame,
    *,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
    source: str = "onyx_slate",
    dedupe: bool = True,
) -> int:
    if df.empty:
        return 0
    return append_csv_log(
        df,
        record_type="slate_row",
        source=source,
        tab=tab,
        year=year,
        week=week,
        dedupe=dedupe,
    )


def log_game_projections(
    cards: list[dict[str, Any]],
    *,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
    dedupe: bool = True,
) -> int:
    if not cards:
        return 0
    rows: list[dict[str, Any]] = []
    for c in cards:
        away_tt = c.get("away_team_over") or {}
        home_tt = c.get("home_team_over") or {}
        rows.append(
            {
                "matchup": c.get("matchup") or c.get("label"),
                "home": c.get("home"),
                "away": c.get("away"),
                "start_date": c.get("start"),
                "event_id": c.get("event_id"),
                "spread_proj": c.get("spread_proj"),
                "spread_line": c.get("spread_line"),
                "spread_diff": c.get("spread_diff"),
                "total_proj": c.get("total_proj"),
                "total_line": c.get("total_line"),
                "total_diff": c.get("total_diff"),
                "proj_home": c.get("proj_home"),
                "proj_away": c.get("proj_away"),
                "away_tt_line": away_tt.get("line"),
                "away_tt_price": away_tt.get("price"),
                "away_tt_book": away_tt.get("book"),
                "home_tt_line": home_tt.get("line"),
                "home_tt_price": home_tt.get("price"),
                "home_tt_book": home_tt.get("book"),
                "away_tt_diff": c.get("away_tt_diff"),
                "home_tt_diff": c.get("home_tt_diff"),
                "open_spread": c.get("open_spread"),
                "close_spread": c.get("close_spread"),
                "open_total": c.get("open_total"),
                "close_total": c.get("close_total"),
                "completed": c.get("completed"),
                "home_score": c.get("home_score"),
                "away_score": c.get("away_score"),
                "quotes_json": json.dumps(
                    {
                        k: c.get(k)
                        for k in (
                            "away_spread",
                            "home_spread",
                            "over",
                            "under",
                            "away_team_over",
                            "home_team_over",
                        )
                        if c.get(k) is not None
                    },
                    default=str,
                ),
            }
        )
    return append_csv_log(
        rows,
        record_type="game_projection",
        source="sp_plus",
        tab=tab,
        year=year,
        week=week,
        dedupe=dedupe,
    )


def log_prop_projections(
    df: pd.DataFrame,
    *,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
    dedupe: bool = True,
) -> int:
    if df.empty:
        return 0
    cols = [
        c
        for c in [
            "matchup",
            "home",
            "away",
            "startDate",
            "player",
            "team",
            "position",
            "market",
            "selection",
            "side",
            "line",
            "price",
            "book",
            "bookId",
            "modelProj",
            "projection",
            "winProb",
            "prior",
            "blended",
            "edge",
            "edgeDisplay",
            "ev_pct",
            "roi",
            "expected_roi",
            "play",
            "propKey",
            "category",
            "group",
            "projectionVersion",
        ]
        if c in df.columns
    ]
    use = df[cols].copy() if cols else df.copy()
    if "expected_roi" not in use.columns:
        if "roi" in use.columns:
            use["expected_roi"] = pd.to_numeric(use["roi"], errors="coerce")
        elif "ev_pct" in use.columns:
            use["expected_roi"] = pd.to_numeric(use["ev_pct"], errors="coerce") / 100.0
    return append_csv_log(
        use,
        record_type="prop_projection",
        source="onyx_model",
        tab=tab,
        year=year,
        week=week,
        dedupe=dedupe,
    )


def log_player_prop_quotes(
    df: pd.DataFrame,
    *,
    source: str = "theoddsapi",
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
    dedupe: bool = False,
) -> int:
    if df.empty:
        return 0
    cols = [
        c
        for c in [
            "event",
            "event_id",
            "home",
            "away",
            "player",
            "propKey",
            "market",
            "market_key",
            "line",
            "side",
            "price",
            "over_price",
            "under_price",
            "book_id",
            "book",
            "source",
        ]
        if c in df.columns
    ]
    use = df[cols].copy() if cols else df.copy()
    return append_csv_log(
        use,
        record_type="prop_odds_quote",
        source=source,
        tab=tab,
        year=year,
        week=week,
        dedupe=dedupe,
    )


def log_sharp_lines(
    rows: list[dict[str, Any]],
    *,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
    dedupe: bool = True,
) -> int:
    if not rows:
        return 0
    flat: list[dict[str, Any]] = []
    for r in rows:
        best = r.get("best") or {}
        flat.append(
            {
                "event": r.get("event"),
                "home": r.get("home"),
                "away": r.get("away"),
                "market_key": r.get("market_key"),
                "market": r.get("market"),
                "pick": r.get("pick"),
                "side": r.get("side"),
                "line": r.get("line"),
                "ref_line": r.get("ref_line"),
                "fair_price": r.get("fair_price"),
                "implied_pct": r.get("implied_pct"),
                "ev_pct": r.get("ev_pct"),
                "edge_points": r.get("edge_points"),
                "quarter_kelly": r.get("quarter_kelly"),
                "best_book_id": best.get("book_id"),
                "best_price": best.get("price"),
                "best_line": best.get("line"),
                "books_json": json.dumps(r.get("books") or {}, default=str),
            }
        )
    return append_csv_log(
        flat,
        record_type="sharp_line",
        source="computed",
        tab=tab,
        year=year,
        week=week,
        dedupe=dedupe,
    )


def log_feed_path() -> Path:
    """Today's append-only CSV path."""
    return _today_feed_path()


def _parse_quotes_json(raw: Any) -> dict[str, Any]:
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _csv_row_to_game_card(row: dict[str, Any]) -> dict[str, Any]:
    quotes = _parse_quotes_json(row.get("quotes_json"))
    away_tt = {
        "line": row.get("away_tt_line"),
        "price": row.get("away_tt_price"),
        "book": row.get("away_tt_book"),
    }
    home_tt = {
        "line": row.get("home_tt_line"),
        "price": row.get("home_tt_price"),
        "book": row.get("home_tt_book"),
    }
    card: dict[str, Any] = {
        "matchup": row.get("matchup"),
        "label": row.get("matchup"),
        "home": row.get("home"),
        "away": row.get("away"),
        "start": row.get("start_date"),
        "event_id": row.get("event_id"),
        "spread_proj": row.get("spread_proj"),
        "spread_line": row.get("spread_line"),
        "spread_diff": row.get("spread_diff"),
        "total_proj": row.get("total_proj"),
        "total_line": row.get("total_line"),
        "total_diff": row.get("total_diff"),
        "proj_home": row.get("proj_home"),
        "proj_away": row.get("proj_away"),
        "away_tt_diff": row.get("away_tt_diff"),
        "home_tt_diff": row.get("home_tt_diff"),
        "open_spread": row.get("open_spread"),
        "close_spread": row.get("close_spread"),
        "open_total": row.get("open_total"),
        "close_total": row.get("close_total"),
        "completed": bool(row.get("completed")),
        "home_score": row.get("home_score"),
        "away_score": row.get("away_score"),
        "away_team_over": away_tt if away_tt.get("line") is not None else quotes.get("away_team_over"),
        "home_team_over": home_tt if home_tt.get("line") is not None else quotes.get("home_team_over"),
    }
    for key in ("away_spread", "home_spread", "over", "under"):
        if quotes.get(key) is not None:
            card[key] = quotes[key]
    return card


def load_game_cards_from_csv_log(year: int, week: int) -> list[dict[str, Any]]:
    """Rebuild game cards from archived streamlit feed rows (completed weeks)."""
    if not CSV_LOG_DIR.exists():
        return []
    frames: list[pd.DataFrame] = []
    for fp in sorted(CSV_LOG_DIR.glob("streamlit_feed_*.csv")):
        try:
            chunk = pd.read_csv(fp, low_memory=False)
        except (OSError, ValueError, pd.errors.EmptyDataError):
            continue
        if chunk.empty or "record_type" not in chunk.columns:
            continue
        mask = (
            chunk["record_type"].astype(str).eq("game_projection")
            & pd.to_numeric(chunk.get("year"), errors="coerce").eq(int(year))
            & pd.to_numeric(chunk.get("week"), errors="coerce").eq(int(week))
        )
        hit = chunk.loc[mask]
        if not hit.empty:
            frames.append(hit)
    if not frames:
        return []
    raw = pd.concat(frames, ignore_index=True)
    if "logged_at_utc" in raw.columns:
        raw = raw.sort_values("logged_at_utc")
    matchup_col = raw.get("matchup")
    if matchup_col is None:
        return []
    latest = raw.drop_duplicates(subset=["matchup"], keep="last")
    cards = [_csv_row_to_game_card(r.to_dict()) for _, r in latest.iterrows()]
    return [c for c in cards if c.get("home") and c.get("away")]
