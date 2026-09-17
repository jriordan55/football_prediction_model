"""Local odds snapshot store for line movement and biggest-moves reports."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import SNAPSHOT_DIR
from .csv_log import log_odds_quotes
from .excel_audit import export_pull
from .odds_math import american_to_implied, implied_prob_points


def _median(vals: list[float]) -> float:
    return float(pd.Series(vals).median())

def _today_file() -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return SNAPSHOT_DIR / f"{day}.jsonl"


def append_snapshot(df: pd.DataFrame, source: str = "mixed", tab: str | None = None) -> int:
    if df.empty:
        return 0
    path = _today_file()
    ts = datetime.now(timezone.utc).isoformat()
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for _, row in df.iterrows():
            rec = {**row.to_dict(), "snapshot_at": ts, "snapshot_source": source}
            f.write(json.dumps(rec, default=str) + "\n")
            n += 1
    export_pull("odds_snapshot", df.assign(snapshot_at=ts), tab=tab, origin=source)
    log_odds_quotes(df, source=source, tab=tab, snapshot_at=ts)
    return n


def latest_snapshot_lines() -> pd.DataFrame:
    """Most recent capture — used instead of live API for move charts."""
    history = load_snapshots(days_back=3)
    if history.empty or "snapshot_at" not in history.columns:
        return pd.DataFrame()
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    latest = history["snapshot_at"].max()
    return history[history["snapshot_at"] == latest].reset_index(drop=True)


def snapshot_fingerprint_from_history(history: pd.DataFrame) -> str | None:
    if history.empty:
        return None
    from .odds_cache import lines_fingerprint

    latest = latest_snapshot_lines()
    if latest.empty:
        return None
    return lines_fingerprint(latest)


def append_snapshot_if_changed(
    df: pd.DataFrame, source: str = "mixed", tab: str | None = None
) -> tuple[int, str]:
    """Append only when lines differ from the latest snapshot."""
    from .odds_cache import lines_fingerprint

    if df.empty:
        return 0, "empty"
    new_fp = lines_fingerprint(df)
    old_fp = snapshot_fingerprint_from_history(load_snapshots(days_back=3))
    if old_fp and new_fp == old_fp:
        return 0, "unchanged"
    n = append_snapshot(df, source=source, tab=tab)
    return n, "saved"


def snapshot_stats(history: pd.DataFrame | None = None) -> dict[str, Any]:
    """Counts unique capture times and rows for UI status."""
    if history is None:
        history = load_snapshots(days_back=7)
    if history.empty:
        return {"captures": 0, "rows": 0, "books": 0, "events": 0, "latest": None, "first": None}
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    captures = history["snapshot_at"].dt.floor("s").nunique()
    return {
        "captures": int(captures),
        "rows": len(history),
        "books": int(history["book_id"].nunique()) if "book_id" in history.columns else 0,
        "events": int(history["event"].nunique()) if "event" in history.columns else 0,
        "latest": history["snapshot_at"].max(),
        "first": history["snapshot_at"].min(),
    }


def latest_snapshot_preview(history: pd.DataFrame, limit: int = 12) -> pd.DataFrame:
    """Sample of most recent capture for first-snapshot UX."""
    if history.empty:
        return pd.DataFrame()
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    latest = history["snapshot_at"].max()
    cur = history[history["snapshot_at"] == latest]
    cols = [c for c in ["event", "market_key", "selection", "book_id", "price", "line"] if c in cur.columns]
    return cur[cols].head(limit)


def load_snapshots(days_back: int = 3) -> pd.DataFrame:
    if not SNAPSHOT_DIR.exists():
        return pd.DataFrame()
    files = sorted(SNAPSHOT_DIR.glob("*.jsonl"), reverse=True)[:days_back]
    rows: list[dict] = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return pd.DataFrame(rows)


def compute_line_moves(current: pd.DataFrame, history: pd.DataFrame, hours: float = 22) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    # If no explicit current lines, use latest snapshot only (no API).
    if current.empty:
        current = latest_snapshot_lines()
    if current.empty:
        return pd.DataFrame()
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    history = history[history["snapshot_at"] >= cutoff]
    if history.empty:
        return pd.DataFrame()

    moves: list[dict] = []
    for (event, market_key, selection, book_id), grp in history.groupby(
        ["event", "market_key", "selection", "book_id"], dropna=False
    ):
        grp = grp.sort_values("snapshot_at")
        open_row = grp.iloc[0]
        cur = current[
            (current["event"] == event)
            & (current["market_key"] == market_key)
            & (current["selection"] == selection)
            & (current["book_id"] == book_id)
        ]
        if cur.empty:
            continue
        close_price = cur.iloc[0].get("price")
        open_price = open_row.get("price")
        open_line = open_row.get("line")
        close_line = cur.iloc[0].get("line")
        mk = str(market_key).lower()
        if mk in {"h2h", "moneyline"}:
            move = implied_prob_points(open_price, close_price)
            move_label = move
        elif "spread" in mk:
            try:
                move_label = round(float(close_line) - float(open_line), 1) if open_line is not None and close_line is not None else None
            except (TypeError, ValueError):
                move_label = None
            move = move_label
        else:
            try:
                move_label = round(float(close_line) - float(open_line), 1) if open_line is not None and close_line is not None else None
            except (TypeError, ValueError):
                move_label = None
            move = move_label
        if move is None:
            continue
        moves.append(
            {
                "event": event,
                "market_key": market_key,
                "selection": selection,
                "book_id": book_id,
                "open_price": open_price,
                "close_price": close_price,
                "open_line": open_line,
                "close_line": close_line,
                "move": move,
                "commence_time": cur.iloc[0].get("commence_time"),
            }
        )
    out = pd.DataFrame(moves)
    if out.empty:
        return out
    return out.reindex(out["move"].abs().sort_values(ascending=False).index)


def consensus_biggest_moves(history: pd.DataFrame, min_books: int = 5, min_prob_pts: float = 2.5) -> pd.DataFrame:
    """Median implied-probability move across books (h2h only)."""
    if history.empty:
        return pd.DataFrame()
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    rows: list[dict] = []
    for (event, selection), grp in history.groupby(["event", "selection"], dropna=False):
        grp = grp.sort_values("snapshot_at")
        by_book = []
        for book_id, bgrp in grp.groupby("book_id"):
            if len(bgrp) < 2:
                continue
            open_p = bgrp.iloc[0].get("price")
            close_p = bgrp.iloc[-1].get("price")
            pts = implied_prob_points(open_p, close_p)
            if pts is not None:
                by_book.append({"book_id": book_id, "move_pts": pts, "open": open_p, "close": close_p})
        if len(by_book) < min_books:
            continue
        med = float(pd.Series([b["move_pts"] for b in by_book]).median())
        if abs(med) < min_prob_pts:
            continue
        confirming = sum(1 for b in by_book if (b["move_pts"] > 0) == (med > 0))
        rows.append(
            {
                "event": event,
                "side": selection,
                "open_median": pd.Series([b["open"] for b in by_book]).median(),
                "close_median": pd.Series([b["close"] for b in by_book]).median(),
                "move_pts": round(med, 1),
                "books_confirming": f"{confirming} of {len(by_book)}",
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.reindex(out["move_pts"].abs().sort_values(ascending=False).index)


def spread_total_moves(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    rows: list[dict] = []
    for (event, market_key, selection), grp in history.groupby(["event", "market_key", "selection"], dropna=False):
        mk = str(market_key).lower()
        if mk in {"h2h", "moneyline"}:
            continue
        grp = grp.sort_values("snapshot_at")
        by_book = []
        for book_id, bgrp in grp.groupby("book_id"):
            if len(bgrp) < 2:
                continue
            o, c = bgrp.iloc[0].get("line"), bgrp.iloc[-1].get("line")
            try:
                by_book.append(float(c) - float(o))
            except (TypeError, ValueError):
                pass
        if len(by_book) < 3:
            continue
        med = _median(by_book)
        if abs(med) < 0.5:
            continue
        rows.append(
            {
                "event": event,
                "market": market_key,
                "selection": selection,
                "open_close": f"{grp.iloc[0].get('line')} → {grp.iloc[-1].get('line')}",
                "median_move": round(med, 1),
                "books": len(by_book),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.reindex(out["median_move"].abs().sort_values(ascending=False).index)


def _history_window(history: pd.DataFrame, hours: float) -> pd.DataFrame:
    """Rows inside the lookback window; falls back to full history if window is empty."""
    if history.empty:
        return history
    history = history.copy()
    history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)
    windowed = history[history["snapshot_at"] >= cutoff]
    return windowed if not windowed.empty else history


def _parse_event_teams(event: str, row: pd.Series | None = None) -> tuple[str, str]:
    if row is not None:
        away = str(row.get("away") or "").strip()
        home = str(row.get("home") or "").strip()
        if away and home:
            return away, home
    text = str(event or "")
    if " @ " in text:
        away, home = text.split(" @ ", 1)
        return away.strip(), home.strip()
    return "", ""


def pinnacle_kickoff_spread_moves(history: pd.DataFrame, hours: float = 22) -> pd.DataFrame:
    """
    Pinnacle spread open→close move for every NCAAF game on the latest snapshot.
    Positive move = favorite line eased toward the dog (steamed up).
    Sorted by kickoff (commence_time).
    """
    if history.empty:
        return pd.DataFrame()

    window = _history_window(history, hours)
    window = window.copy()
    window["snapshot_at"] = pd.to_datetime(window["snapshot_at"], utc=True)

    latest = latest_snapshot_lines()
    if latest.empty:
        return pd.DataFrame()

    pin_latest = latest[
        (latest["book_id"].astype(str).str.lower() == "pinnacle")
        & (latest["market_key"].astype(str).str.lower().isin({"spreads", "spread"}))
    ]
    if pin_latest.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for event, cur_grp in pin_latest.groupby("event", dropna=False):
        hist = window[
            (window["event"] == event)
            & (window["book_id"].astype(str).str.lower() == "pinnacle")
            & (window["market_key"].astype(str).str.lower().isin({"spreads", "spread"}))
        ]
        if hist.empty:
            continue
        hist = hist.sort_values("snapshot_at")
        meta = cur_grp.iloc[0]

        fav_open = fav_close = dog_open = dog_close = None
        fav_team = dog_team = None
        for selection, sel_grp in hist.groupby("selection", dropna=False):
            sel_grp = sel_grp.sort_values("snapshot_at")
            try:
                open_line = float(sel_grp.iloc[0]["line"])
                close_line = float(sel_grp.iloc[-1]["line"])
            except (TypeError, ValueError):
                continue
            sel_name = str(selection or "")
            if open_line < 0:
                fav_team = fav_team or sel_name
                fav_open = open_line if fav_open is None else fav_open
                fav_close = close_line
            elif open_line > 0:
                dog_team = dog_team or sel_name
                dog_open = open_line if dog_open is None else dog_open
                dog_close = close_line

        if fav_open is None or fav_close is None:
            continue

        move = round(float(fav_close) - float(fav_open), 1)
        away, home = _parse_event_teams(str(event), meta)
        short = f"{_team_short(away)} @ {_team_short(home)}" if away and home else str(event)

        rows.append(
            {
                "event": event,
                "event_short": short,
                "away": away,
                "home": home,
                "move": move,
                "open_line": fav_open,
                "close_line": fav_close,
                "dog_team": dog_team or "",
                "fav_team": fav_team or "",
                "logo_team": dog_team or away,
                "commence_time": meta.get("commence_time"),
                "captures": int(hist["snapshot_at"].dt.floor("s").nunique()),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["commence_time"] = pd.to_datetime(out["commence_time"], utc=True, errors="coerce")
    return out.sort_values(["commence_time", "event"], na_position="last").reset_index(drop=True)


def _team_short(name: str) -> str:
    """Abbreviate 'Virginia Tech Hokies' → 'VT'."""
    text = str(name or "").strip()
    if not text:
        return ""
    parts = text.split()
    if len(parts) >= 2 and parts[-1] in {
        "Tigers",
        "Bulldogs",
        "Wildcats",
        "Bears",
        "Eagles",
        "Panthers",
        "Cardinals",
        "Hokies",
        "Cavaliers",
        "Demon",
        "Deacons",
        "Wolves",
        "Aggies",
        "Cowboys",
        "Knights",
        "Owls",
        "Rams",
        "Lobos",
        "Broncos",
        "Ducks",
        "Buckeyes",
        "Rebels",
        "Gamecocks",
        "Flames",
        "Bobcats",
    }:
        if parts[-2] == "Demon" and parts[-1] == "Deacons":
            return "Wake"
        if len(parts) >= 3 and parts[-2] == "Fighting":
            return parts[0][:4].upper()
        return parts[0][:4].upper() if len(parts[0]) <= 5 else parts[0][:3].upper()
    return parts[0][:4].upper()


def consensus_spread_dog_moves(moves: pd.DataFrame, min_books: int = 2) -> pd.DataFrame:
    """Per-game median favorite-side spread move (+ = money on dog)."""
    if moves.empty:
        return pd.DataFrame()
    spread = moves[moves["market_key"].astype(str).str.lower().isin({"spreads", "spread"})].copy()
    if spread.empty:
        return spread

    rows: list[dict] = []
    for event, grp in spread.groupby("event", dropna=False):
        per_book: dict[str, float] = {}
        fav_team = dog_team = None
        for book_id, bgrp in grp.groupby("book_id", dropna=False):
            fav_moves: list[float] = []
            for _, r in bgrp.iterrows():
                try:
                    ol = float(r["open_line"])
                    mv = float(r["move"])
                except (TypeError, ValueError):
                    continue
                sel = str(r.get("selection") or "")
                if ol < 0:
                    fav_team = fav_team or sel
                    fav_moves.append(mv)
                elif ol > 0:
                    dog_team = dog_team or sel
            if fav_moves:
                per_book[str(book_id)] = _median(fav_moves)

        if len(per_book) < min_books:
            continue
        vals = list(per_book.values())
        med = round(_median(vals), 1)
        if abs(med) < 0.1:
            continue
        rows.append(
            {
                "event": event,
                "move": med,
                "team": (dog_team if med > 0 else fav_team) or "",
                "books": len(per_book),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("move", ascending=False).reset_index(drop=True)
