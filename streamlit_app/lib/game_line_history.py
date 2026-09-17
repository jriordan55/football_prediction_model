"""Opening/closing lines for finished games — The Odds API snapshots first."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import pandas as pd

from .config import DATA_DIR
from .team_registry import match_selection_to_side, resolve_canonical, team_key, teams_match

_PREFERRED_BOOKS = ("draftkings", "fanduel", "pinnacle")


def _round_line(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val) * 2) / 2
    except (TypeError, ValueError):
        return None


def _matchup_keys(home: str, away: str) -> tuple[str, str]:
    return team_key(home), team_key(away)


def _parse_ts(val: Any) -> pd.Timestamp | None:
    if val is None or str(val).strip() in ("", "nan", "None"):
        return None
    try:
        return pd.to_datetime(val, utc=True)
    except (TypeError, ValueError):
        return None


def _kickoff_ts(kickoff: Any) -> pd.Timestamp | None:
    if kickoff is None:
        return None
    try:
        dt = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return pd.Timestamp(dt)
    except (TypeError, ValueError):
        return _parse_ts(kickoff)


@lru_cache(maxsize=8)
def _archive_line_index(year: int, week: int | None = None) -> dict[tuple[str, str], dict[str, float | None]]:
    """Onyx slate archive — fallback only when The Odds API history is missing."""
    cache = DATA_DIR / "onyx_slate_cache"
    if not cache.exists():
        return {}

    if week is not None:
        wk = 1 if int(week) == 0 else int(week)
        paths = sorted(cache.glob(f"onyx_slate_{year}_w{wk}*.json"), key=lambda p: p.name)
    else:
        paths = sorted(cache.glob(f"onyx_slate_{year}_w*.json"), key=lambda p: p.name)
    spread_samples: dict[tuple[str, str], list[float]] = {}
    total_samples: dict[tuple[str, str], list[float]] = {}

    for path in paths:
        try:
            slate = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for r in slate.get("rows") or []:
            hk = team_key(str(r.get("home") or ""))
            ak = team_key(str(r.get("away") or ""))
            if not hk or not ak:
                continue
            key = (hk, ak)
            grp = str(r.get("group") or "")
            side = str(r.get("side") or "").lower()
            if grp == "spread" and side == "home_cover":
                ln = _round_line(r.get("line"))
                if ln is not None:
                    spread_samples.setdefault(key, []).append(ln)
            elif grp == "total" and "over" in side:
                ln = _round_line(r.get("line"))
                if ln is not None:
                    total_samples.setdefault(key, []).append(ln)

    out: dict[tuple[str, str], dict[str, float | None]] = {}
    keys = set(spread_samples) | set(total_samples)
    for key in keys:
        spreads = spread_samples.get(key) or []
        totals = total_samples.get(key) or []
        entry: dict[str, float | None] = {}
        if spreads:
            entry["openSpread"] = spreads[0]
            entry["closeSpread"] = spreads[-1]
        if totals:
            entry["openTotal"] = totals[0]
            entry["closeTotal"] = totals[-1]
        if entry:
            out[key] = entry
    return out


@lru_cache(maxsize=2)
def _snapshot_history() -> pd.DataFrame:
    from .snapshots import load_snapshots

    return load_snapshots(days_back=21)


@lru_cache(maxsize=1)
def snapshots_available() -> bool:
    return not _snapshot_history().empty


@lru_cache(maxsize=1)
def _snapshot_matchup_index() -> dict[tuple[str, str], pd.DataFrame]:
    """Index snapshot rows by (away_key, home_key) — built once per process."""
    history = _snapshot_history()
    if history.empty or "home" not in history.columns or "away" not in history.columns:
        return {}

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in history.to_dict(orient="records"):
        home = resolve_canonical(str(row.get("home") or "")) or str(row.get("home") or "")
        away = resolve_canonical(str(row.get("away") or "")) or str(row.get("away") or "")
        if not home or not away:
            continue
        key = (team_key(away), team_key(home))
        buckets.setdefault(key, []).append(row)

    out: dict[tuple[str, str], pd.DataFrame] = {}
    for key, rows in buckets.items():
        out[key] = pd.DataFrame(rows)
    return out


def _snapshot_rows_for_matchup(home: str, away: str) -> pd.DataFrame:
    key = (team_key(away), team_key(home))
    hit = _snapshot_matchup_index().get(key)
    if hit is not None:
        return hit
    for (ak, hk), frame in _snapshot_matchup_index().items():
        if teams_match(ak, away) and teams_match(hk, home):
            return frame
    return pd.DataFrame()


def _pick_book(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "book_id" not in df.columns:
        return df
    books = df["book_id"].astype(str).str.lower()
    for bid in _PREFERRED_BOOKS:
        hit = df[books.eq(bid)]
        if not hit.empty:
            return hit
    return df


def _price_from_side_rows(
    rows: pd.DataFrame,
    home: str,
    away: str,
    *,
    which: str,
    side: str,
) -> Any:
    """American price for home spread or over total at open/close."""
    if rows.empty:
        return None
    for selection, grp in rows.groupby(rows["selection"].astype(str), dropna=False):
        grp = grp.sort_values("snapshot_at")
        if grp.empty:
            continue
        sel = str(selection).lower()
        if side == "over" and "over" not in sel:
            continue
        if side == "home":
            if match_selection_to_side(str(selection), home, away) != "home":
                continue
        row = grp.iloc[0] if which == "open" else grp.iloc[-1]
        price = row.get("price")
        if price is not None and str(price).strip() not in ("", "nan"):
            return price
    return None


def _market_slice(df: pd.DataFrame, market: str) -> pd.DataFrame:
    mk = df["market_key"].astype(str).str.lower()
    if market == "spread":
        return df[mk.isin({"spreads", "spread"})]
    return df[mk.isin({"totals", "total"})]


def _price_from_preferred(
    df: pd.DataFrame,
    home: str,
    away: str,
    *,
    which: str,
    side: str,
) -> Any:
    """Try DraftKings → FanDuel → Pinnacle → any book for open/close juice."""
    if df.empty:
        return None
    books = df["book_id"].astype(str).str.lower() if "book_id" in df.columns else pd.Series(dtype=str)
    for bid in _PREFERRED_BOOKS:
        hit = df[books.eq(bid)] if not books.empty else df.iloc[0:0]
        price = _price_from_side_rows(hit, home, away, which=which, side=side)
        if price is not None:
            return price
    return _price_from_side_rows(df, home, away, which=which, side=side)


def open_close_prices_from_snapshots(
    home: str,
    away: str,
    *,
    kickoff: Any = None,
) -> dict[str, Any]:
    """Preferred-book open/close American prices for home spread and over."""
    sub = _snapshot_rows_for_matchup(home, away)
    if sub.empty:
        return {}

    sub = sub.copy()
    sub["snapshot_at"] = pd.to_datetime(sub["snapshot_at"], utc=True, errors="coerce")
    sub = sub.dropna(subset=["snapshot_at"])
    if sub.empty:
        return {}

    kick_ts = _kickoff_ts(kickoff)
    close_sub = sub
    if kick_ts is not None:
        pre = sub[sub["snapshot_at"] < kick_ts]
        if not pre.empty:
            close_sub = pre

    spreads_open = _market_slice(sub, "spread")
    spreads_close = _market_slice(close_sub, "spread")
    totals_open = _market_slice(sub, "total")
    totals_close = _market_slice(close_sub, "total")

    return {
        "openSpreadPrice": _price_from_preferred(spreads_open, home, away, which="open", side="home"),
        "closeSpreadPrice": _price_from_preferred(spreads_close, home, away, which="close", side="home"),
        "openTotalPrice": _price_from_preferred(totals_open, home, away, which="open", side="over"),
        "closeTotalPrice": _price_from_preferred(totals_close, home, away, which="close", side="over"),
    }


def _home_spread_from_spread_rows(
    spreads: pd.DataFrame,
    home: str,
    away: str,
    *,
    which: str,
) -> float | None:
    """Extract home-perspective spread from first or last row per side."""
    if spreads.empty:
        return None
    home_side = away_side = None
    for selection, grp in spreads.groupby(spreads["selection"].astype(str), dropna=False):
        grp = grp.sort_values("snapshot_at")
        if grp.empty:
            continue
        row = grp.iloc[0] if which == "open" else grp.iloc[-1]
        try:
            ln = float(row["line"])
        except (TypeError, ValueError):
            continue
        side = match_selection_to_side(str(selection), home, away)
        if side == "home":
            home_side = _round_line(ln)
        elif side == "away":
            away_side = _round_line(ln)

    if home_side is not None:
        return home_side
    if away_side is not None:
        return _round_line(-away_side)
    return None


def _round_ml(val: Any) -> int | None:
    if val is None:
        return None
    try:
        n = int(round(float(val)))
    except (TypeError, ValueError):
        return None
    return n if n != 0 else None


def _ml_from_rows(h2h: pd.DataFrame, home: str, away: str, *, which: str) -> tuple[int | None, int | None]:
    if h2h.empty:
        return None, None
    home_ml = away_ml = None
    for selection, grp in h2h.groupby(h2h["selection"].astype(str), dropna=False):
        grp = grp.sort_values("snapshot_at")
        if grp.empty:
            continue
        row = grp.iloc[0] if which == "open" else grp.iloc[-1]
        side = match_selection_to_side(str(selection), home, away)
        price = _round_ml(row.get("price"))
        if side == "home":
            home_ml = price
        elif side == "away":
            away_ml = price
    return home_ml, away_ml


def _total_from_rows(totals: pd.DataFrame, *, which: str) -> float | None:
    if totals.empty:
        return None
    over = totals[totals["selection"].astype(str).str.lower().str.contains("over", na=False)]
    if over.empty:
        over = totals
    over = over.sort_values("snapshot_at")
    if over.empty:
        return None
    row = over.iloc[0] if which == "open" else over.iloc[-1]
    try:
        return _round_line(float(row["line"]))
    except (TypeError, ValueError):
        return None


def _lines_from_snapshots(
    home: str,
    away: str,
    *,
    kickoff: Any = None,
) -> dict[str, float | None]:
    """First + last pre-kickoff The Odds API snapshot (DraftKings preferred)."""
    sub = _snapshot_rows_for_matchup(home, away)
    if sub.empty:
        return {}

    sub = sub.copy()
    sub["snapshot_at"] = pd.to_datetime(sub["snapshot_at"], utc=True, errors="coerce")
    sub = sub.dropna(subset=["snapshot_at"])
    if sub.empty:
        return {}

    kick_ts = _kickoff_ts(kickoff)
    close_sub = sub
    if kick_ts is not None:
        pre = sub[sub["snapshot_at"] < kick_ts]
        if not pre.empty:
            close_sub = pre

    src_open = _pick_book(sub)
    src_close = _pick_book(close_sub)

    spreads_open = src_open[src_open["market_key"].astype(str).str.lower().isin({"spreads", "spread"})]
    spreads_close = src_close[src_close["market_key"].astype(str).str.lower().isin({"spreads", "spread"})]
    totals_open = src_open[src_open["market_key"].astype(str).str.lower().isin({"totals", "total"})]
    totals_close = src_close[src_close["market_key"].astype(str).str.lower().isin({"totals", "total"})]
    h2h_open = src_open[src_open["market_key"].astype(str).str.lower().isin({"h2h", "moneyline"})]
    h2h_close = src_close[src_close["market_key"].astype(str).str.lower().isin({"h2h", "moneyline"})]

    out: dict[str, float | int | None] = {}
    out["openSpread"] = _home_spread_from_spread_rows(spreads_open, home, away, which="open")
    out["closeSpread"] = _home_spread_from_spread_rows(spreads_close, home, away, which="close")
    out["openTotal"] = _total_from_rows(totals_open, which="open")
    out["closeTotal"] = _total_from_rows(totals_close, which="close")
    open_home_ml, open_away_ml = _ml_from_rows(h2h_open, home, away, which="open")
    close_home_ml, close_away_ml = _ml_from_rows(h2h_close, home, away, which="close")
    out["openHomeMoneyline"] = open_home_ml
    out["openAwayMoneyline"] = open_away_ml
    out["closeHomeMoneyline"] = close_home_ml
    out["closeAwayMoneyline"] = close_away_ml
    return {k: v for k, v in out.items() if v is not None}


def moneyline_from_snapshots(
    home: str,
    away: str,
    *,
    kickoff: Any = None,
) -> dict[str, int | None]:
    """Earliest + latest pre-kickoff moneyline from The Odds API snapshots."""
    snap = _lines_from_snapshots(home, away, kickoff=kickoff)
    return {
        "openHomeMoneyline": snap.get("openHomeMoneyline"),
        "openAwayMoneyline": snap.get("openAwayMoneyline"),
        "closeHomeMoneyline": snap.get("closeHomeMoneyline"),
        "closeAwayMoneyline": snap.get("closeAwayMoneyline"),
    }


def _lines_from_snapshots_for_book(
    home: str,
    away: str,
    book_id: str,
    *,
    kickoff: Any = None,
    which: str = "close",
) -> dict[str, dict[str, Any] | None]:
    """Per-book spread/total sides at open or close (pre-kickoff for close)."""
    from .sharp_edges import _book_side_rows

    sub = _snapshot_rows_for_matchup(home, away)
    if sub.empty:
        return {"spread_sides": None, "total_sides": None}

    sub = sub.copy()
    sub["snapshot_at"] = pd.to_datetime(sub["snapshot_at"], utc=True, errors="coerce")
    sub = sub.dropna(subset=["snapshot_at"])
    if "book_id" not in sub.columns:
        return {"spread_sides": None, "total_sides": None}
    bid = str(book_id).lower()
    sub = sub[sub["book_id"].astype(str).str.lower().eq(bid)]
    if sub.empty:
        return {"spread_sides": None, "total_sides": None}

    kick_ts = _kickoff_ts(kickoff)
    close_sub = sub
    if which == "close" and kick_ts is not None:
        pre = sub[sub["snapshot_at"] < kick_ts]
        if not pre.empty:
            close_sub = pre

    src = sub if which == "open" else close_sub
    spreads = src[src["market_key"].astype(str).str.lower().isin({"spreads", "spread"})]
    totals = src[src["market_key"].astype(str).str.lower().isin({"totals", "total"})]

    spread_sides: dict[str, dict[str, Any]] = {}
    total_sides: dict[str, dict[str, Any]] = {}

    if not spreads.empty:
        spreads = spreads.sort_values("snapshot_at")
        pick = spreads.groupby("selection").first().reset_index() if which == "open" else spreads.groupby("selection").last().reset_index()
        spread_sides = _book_side_rows(pick, home, away, "spreads")

    if not totals.empty:
        totals = totals.sort_values("snapshot_at")
        pick = totals.groupby("selection").first().reset_index() if which == "open" else totals.groupby("selection").last().reset_index()
        total_sides = _book_side_rows(pick, home, away, "totals")

    return {"spread_sides": spread_sides or None, "total_sides": total_sides or None}


def _home_spread_from_slate_row(row: Any) -> float | None:
    from .clv_report import _home_spread_from_slate

    if row is None:
        return None
    try:
        return _round_line(_home_spread_from_slate(row))
    except (TypeError, ValueError, AttributeError):
        return None


def _lines_from_slate_rows(rows: pd.DataFrame, *, home: str | None = None, away: str | None = None) -> dict[str, float | None]:
    if rows is None or rows.empty:
        return {}
    spread_rows = rows[(rows.get("group") == "spread") | (rows.get("market") == "Spread")]
    total_row = rows[(rows.get("group") == "total") | (rows.get("market") == "Total")].head(1)
    out: dict[str, float | None] = {}
    if not spread_rows.empty:
        home_rows = spread_rows[spread_rows["side"].astype(str) == "home_cover"]
        pick = home_rows.iloc[0] if not home_rows.empty else spread_rows.iloc[0]
        out["closeSpread"] = _home_spread_from_slate_row(pick)
    if not total_row.empty:
        out["closeTotal"] = _round_line(total_row.iloc[0].get("line"))
    return out


@lru_cache(maxsize=256)
def opening_closing_lines_cached(
    home: str,
    away: str,
    year: int,
    skip_espn: bool,
    week: int | None = None,
    kickoff: str | None = None,
) -> dict[str, Any]:
    """Cached open/close — The Odds API snapshots first, then opener file, archive last."""
    from .clv_report import _find_opener_quotes, _home_spread_from_snapshot, _total_from_snapshot

    open_spread = open_total = close_spread = close_total = None

    snap = _lines_from_snapshots(home, away, kickoff=kickoff)
    open_spread = snap.get("openSpread")
    open_total = snap.get("openTotal")
    close_spread = snap.get("closeSpread")
    close_total = snap.get("closeTotal")

    if open_spread is None or open_total is None:
        opener_q = _find_opener_quotes(home, away)
        if opener_q:
            if open_spread is None:
                open_spread = _home_spread_from_snapshot(opener_q, home, away)
            if open_total is None:
                open_total = _total_from_snapshot(opener_q)

    archive = _archive_line_index(year, week).get(_matchup_keys(home, away), {})
    if open_spread is None:
        open_spread = archive.get("openSpread")
    if open_total is None:
        open_total = archive.get("openTotal")
    if close_spread is None:
        close_spread = archive.get("closeSpread")
    if close_total is None:
        close_total = archive.get("closeTotal")

    open_spread = _round_line(open_spread)
    close_spread = _round_line(close_spread)
    open_total = _round_line(open_total)
    close_total = _round_line(close_total)

    return {
        "spread": close_spread,
        "total": close_total,
        "openSpread": open_spread,
        "closeSpread": close_spread,
        "openTotal": open_total,
        "closeTotal": close_total,
    }


def opening_closing_lines(
    home: str,
    away: str,
    *,
    event_id: str | None = None,
    slate_rows: pd.DataFrame | None = None,
    year: int | None = None,
    skip_espn: bool = False,
    kickoff: Any = None,
) -> dict[str, Any]:
    """Opening/closing lines — snapshots first; ESPN pickcenter optional."""
    yr = int(year or 2026)
    kick = str(kickoff) if kickoff is not None else None
    out = dict(opening_closing_lines_cached(home, away, yr, skip_espn, kickoff=kick))

    if not skip_espn and event_id:
        try:
            from .espn_client import fetch_game_summary, open_close_lines_from_summary

            summary = fetch_game_summary(str(event_id))
            espn = open_close_lines_from_summary(summary, home)
            if out.get("openSpread") is None:
                out["openSpread"] = _round_line(espn.get("openSpread"))
            if out.get("openTotal") is None:
                out["openTotal"] = _round_line(espn.get("openTotal"))
            if out.get("closeSpread") is None:
                out["closeSpread"] = _round_line(espn.get("closeSpread"))
            if out.get("closeTotal") is None:
                out["closeTotal"] = _round_line(espn.get("closeTotal"))
        except Exception:
            pass

    slate_m = _lines_from_slate_rows(slate_rows, home=home, away=away) if slate_rows is not None else {}
    if out.get("closeSpread") is None and slate_m.get("closeSpread") is not None:
        out["closeSpread"] = slate_m["closeSpread"]
    if out.get("closeTotal") is None and slate_m.get("closeTotal") is not None:
        out["closeTotal"] = slate_m["closeTotal"]
    out["spread"] = out.get("closeSpread")
    out["total"] = out.get("closeTotal")
    return out
