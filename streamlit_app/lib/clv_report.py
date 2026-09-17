"""CLV report — model vs close, best price, line travel (opener → close)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import pandas as pd

from .config import DATA_DIR, book_allowed, book_label, normalize_book_id
from .display import team_abbr
from .odds_math import american_to_implied
from .sport_context import opener_snapshot_path


def _opener_path():
    return opener_snapshot_path()


def _team_token(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower())
    parts = [p for p in n.split() if p and p not in {"the", "of", "at", "state", "university"}]
    return parts[0] if parts else ""


def event_matches(event: str, home: str, away: str) -> bool:
    e = str(event or "").lower()
    al = str(away or "").lower()
    hl = str(home or "").lower()
    if not al or not hl or al not in e or hl not in e:
        return False
    # Avoid matching North Texas when looking for Texas State @ Texas
    if al != hl and al.split()[0] == hl.split()[0] and al not in e:
        return False
    if hl == "texas" and "north texas" in e:
        return False
    if hl == "miami" and "miami (oh)" in e or "miami ohio" in e:
        return False
    return True


def _home_spread_from_slate(row: pd.Series) -> float | None:
    side = str(row.get("side") or "")
    if side == "home_cover":
        try:
            return float(row.get("line"))
        except (TypeError, ValueError):
            return None
    if side == "away_cover":
        try:
            return -float(row.get("line"))
        except (TypeError, ValueError):
            return None
    try:
        return float(row.get("line"))
    except (TypeError, ValueError):
        return None


def _model_spread(row: pd.Series) -> float | None:
    side = str(row.get("side") or "")
    try:
        proj = float(row.get("modelProj"))
    except (TypeError, ValueError):
        return None
    if side == "away_cover":
        return -proj
    return proj


def _home_spread_from_snapshot(quotes: list[dict], home: str, away: str = "") -> float | None:
    from .team_registry import match_selection_to_side

    home_line = None
    away_line = None
    for q in quotes:
        market = str(q.get("market") or "")
        if market not in {"Spread", "Point Spread"}:
            continue
        sel = str(q.get("selection") or "")
        try:
            ln = float(q.get("line"))
        except (TypeError, ValueError):
            continue
        side = match_selection_to_side(sel, home, away)
        if side == "home":
            home_line = ln
        elif side == "away":
            away_line = ln
    if home_line is not None:
        return home_line
    if away_line is not None:
        return -away_line
    return None


def _total_from_snapshot(quotes: list[dict], *, over: bool = True) -> float | None:
    for q in quotes:
        market = str(q.get("market") or "").lower()
        if "total" not in market:
            continue
        sel = str(q.get("selection") or "").lower()
        if over and "over" in sel:
            try:
                return float(q.get("line"))
            except (TypeError, ValueError):
                continue
        if not over and "under" in sel:
            try:
                return float(q.get("line"))
            except (TypeError, ValueError):
                continue
    for q in quotes:
        market = str(q.get("market") or "").lower()
        if "total" in market and q.get("line") is not None:
            try:
                return float(q.get("line"))
            except (TypeError, ValueError):
                continue
    return None


@lru_cache(maxsize=1)
def load_opener_quotes() -> dict[str, list[dict]]:
    if not _opener_path().exists():
        return {}
    try:
        data = json.loads(_opener_path().read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    by_event: dict[str, list[dict]] = {}
    for q in data.get("quotes") or []:
        ev = str(q.get("event") or "")
        by_event.setdefault(ev, []).append(q)
    return by_event


def _find_opener_quotes(home: str, away: str) -> list[dict]:
    for event, quotes in load_opener_quotes().items():
        if event_matches(event, home, away):
            return quotes
    return []


def _market_line_moved(open_val: Any, close_val: Any, *, fmt: str = "line") -> bool:
    """True only when open and close differ — flat lines are excluded from grading."""
    if open_val is None or close_val is None:
        return False
    if fmt == "american":
        try:
            return int(round(float(open_val))) != int(round(float(close_val)))
        except (TypeError, ValueError):
            return False
    try:
        return round(float(open_val) * 2) / 2 != round(float(close_val) * 2) / 2
    except (TypeError, ValueError):
        return False


def _format_move_display(open_val: Any, close_val: Any, *, fmt: str = "line") -> str:
    if fmt == "american":
        return f"{_fmt_american(open_val) or '—'} → {_fmt_american(close_val) or '—'}"
    try:
        return f"{float(open_val):g} → {float(close_val):g}"
    except (TypeError, ValueError):
        return "—"


def _toward_model(open_val: float, close_val: float, model_val: float, *, epsilon: float = 0.05) -> bool:
    return abs(close_val - model_val) + epsilon < abs(open_val - model_val)


def _append_grid_item(
    grid: list[dict[str, Any]],
    *,
    toward: bool,
    matchup: str,
    market: str,
    open_line: float,
    close_line: float,
    model_line: float,
) -> None:
    grid.append(
        {
            "toward": toward,
            "matchup": matchup,
            "market": market,
            "line_display": _format_move_display(open_line, close_line, fmt="line"),
            "model_display": f"Model {model_line:g}",
            "open_line": open_line,
            "close_line": close_line,
            "model_line": model_line,
        }
    )


def _round_ml(price: Any) -> int | None:
    if price is None:
        return None
    try:
        n = int(round(float(price)))
    except (TypeError, ValueError):
        return None
    return n if n != 0 else None


def _ml_from_snapshot_quotes(quotes: list[dict], home: str, away: str) -> tuple[int | None, int | None]:
    from .team_registry import match_selection_to_side

    home_ml = away_ml = None
    for q in quotes:
        mk = str(q.get("market_key") or q.get("market") or "").lower()
        if mk not in {"h2h", "moneyline"} and "moneyline" not in str(q.get("market") or "").lower():
            continue
        sel = str(q.get("selection") or "")
        side = match_selection_to_side(sel, home, away)
        price = _round_ml(q.get("price"))
        if side == "home":
            home_ml = price
        elif side == "away":
            away_ml = price
    return home_ml, away_ml


def _resolve_ml_open_close(
    home: str,
    away: str,
    oc: dict[str, Any],
    *,
    kickoff: Any = None,
) -> tuple[int | None, int | None, int | None, int | None]:
    """Close from cfbfastR/nflfastR; open from fastR or earliest captured odds."""
    from .game_line_history import moneyline_from_snapshots

    open_home = oc.get("openHomeMoneyline")
    open_away = oc.get("openAwayMoneyline")
    close_home = oc.get("closeHomeMoneyline")
    close_away = oc.get("closeAwayMoneyline")

    snap = moneyline_from_snapshots(home, away, kickoff=kickoff)
    if open_home is None:
        open_home = snap.get("openHomeMoneyline")
    if open_away is None:
        open_away = snap.get("openAwayMoneyline")
    if close_home is None:
        close_home = snap.get("closeHomeMoneyline")
    if close_away is None:
        close_away = snap.get("closeAwayMoneyline")

    if open_home is None or open_away is None:
        opener_q = _find_opener_quotes(home, away)
        oh, oa = _ml_from_snapshot_quotes(opener_q, home, away)
        if open_home is None:
            open_home = oh
        if open_away is None:
            open_away = oa

    return open_home, open_away, close_home, close_away


def _home_win_prob(row: pd.Series) -> float | None:
    side = str(row.get("side") or "")
    try:
        prob = float(row.get("winProb"))
    except (TypeError, ValueError):
        try:
            prob = float(row.get("modelProj"))
        except (TypeError, ValueError):
            return None
    if side == "away_ml":
        prob = 1.0 - prob
    return prob if 0.01 < prob < 0.99 else None


def _fmt_american(price: Any) -> str | None:
    if price is None:
        return None
    try:
        n = int(round(float(price)))
    except (TypeError, ValueError):
        return None
    return f"+{n}" if n > 0 else str(n)


def _ml_toward_model(
    *,
    open_home: int | None,
    close_home: int | None,
    open_away: int | None,
    close_away: int | None,
    model_home_prob: float,
) -> tuple[bool | None, str, int | None, int | None]:
    """Grade ML move on the side our model favors."""
    if model_home_prob >= 0.5:
        open_ml, close_ml, model = open_home, close_home, model_home_prob
        pick = "home"
    else:
        open_ml, close_ml, model = open_away, close_away, 1.0 - model_home_prob
        pick = "away"
    open_imp = american_to_implied(open_ml)
    close_imp = american_to_implied(close_ml)
    if open_imp is None or close_imp is None or model is None:
        return None, pick, open_ml, close_ml
    return _toward_model(open_imp, close_imp, model), pick, open_ml, close_ml


def _matchup_short(away: str, home: str) -> str:
    return f"{team_abbr(away)} @ {team_abbr(home)}"


def build_clv_report(
    slate_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    *,
    week: int,
    year: int = 2026,
    history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Market Moves — did opener→close line travel toward our model?"""
    from .fastr_market_lines import fastr_open_close_lines_cached
    from .snapshots import load_snapshots
    from .sport_context import cache_sport

    if slate_df.empty:
        return {"empty": True, "ready": False}

    if history is None:
        history = load_snapshots(days_back=21)

    closing_odds = _closing_odds_from_history(history)
    if closing_odds.empty and not odds_df.empty:
        closing_odds = odds_df

    grid: list[dict[str, Any]] = []
    spread_toward = spread_total = 0
    total_toward = total_total = 0
    travelled_sides: list[dict[str, Any]] = []
    travelled_totals: list[dict[str, Any]] = []

    spread_rows: list[pd.Series] = []
    for _, sub in slate_df[slate_df["group"].astype(str) == "spread"].groupby("matchup", dropna=False):
        hc = sub[sub["side"].astype(str) == "home_cover"]
        spread_rows.append(hc.iloc[0] if not hc.empty else sub.iloc[0])
    spreads = pd.DataFrame(spread_rows) if spread_rows else slate_df.iloc[0:0]

    total_rows: list[pd.Series] = []
    for _, sub in slate_df[slate_df["group"].astype(str) == "total"].groupby("matchup", dropna=False):
        ov = sub[sub["side"].astype(str) == "over"]
        total_rows.append(ov.iloc[0] if not ov.empty else sub.iloc[0])
    totals = pd.DataFrame(total_rows) if total_rows else slate_df.iloc[0:0]

    sport = cache_sport()
    for _, row in spreads.iterrows():
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        kickoff = row.get("startDate")
        oc = fastr_open_close_lines_cached(home, away, int(year), week=week, sport=sport)
        open_line = oc.get("openSpread")
        close_line = oc.get("closeSpread")
        if close_line is None:
            close_line = _home_spread_from_slate(row)
        if close_line is None or open_line is None:
            continue

        if not _market_line_moved(open_line, close_line):
            continue

        model = _model_spread(row)
        if model is None:
            continue

        toward = _toward_model(float(open_line), float(close_line), float(model))
        _append_grid_item(
            grid,
            toward=toward,
            matchup=_matchup_short(away, home),
            market="Spread",
            open_line=float(open_line),
            close_line=float(close_line),
            model_line=float(model),
        )
        spread_total += 1
        if toward:
            spread_toward += 1

        delta = round(float(close_line) - float(open_line), 1)
        travelled_sides.append(
            {
                "matchup": _matchup_short(away, home),
                "away": away,
                "home": home,
                "awayLogo": row.get("awayLogo"),
                "homeLogo": row.get("homeLogo"),
                "open": float(open_line),
                "close": float(close_line),
                "delta": delta,
                "toward": toward,
            }
        )

    for _, row in totals.iterrows():
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        kickoff = row.get("startDate")
        oc = fastr_open_close_lines_cached(home, away, int(year), week=week, sport=sport)
        open_line = oc.get("openTotal")
        close_line = oc.get("closeTotal")
        if open_line is None or close_line is None:
            from .game_line_history import _lines_from_snapshots

            snap = _lines_from_snapshots(home, away, kickoff=kickoff)
            if open_line is None:
                open_line = snap.get("openTotal")
            if close_line is None:
                close_line = snap.get("closeTotal")
        if close_line is None or open_line is None:
            continue

        if not _market_line_moved(open_line, close_line):
            continue

        try:
            model = float(row.get("modelProj"))
        except (TypeError, ValueError):
            continue

        toward = _toward_model(float(open_line), float(close_line), float(model))
        _append_grid_item(
            grid,
            toward=toward,
            matchup=_matchup_short(away, home),
            market="Total",
            open_line=float(open_line),
            close_line=float(close_line),
            model_line=float(model),
        )
        total_total += 1
        if toward:
            total_toward += 1

        delta = round(float(close_line) - float(open_line), 1)
        travelled_totals.append(
            {
                "matchup": _matchup_short(away, home),
                "away": away,
                "home": home,
                "awayLogo": row.get("awayLogo"),
                "homeLogo": row.get("homeLogo"),
                "open": float(open_line),
                "close": float(close_line),
                "delta": delta,
                "toward": toward,
            }
        )

    with_us = sum(1 for g in grid if g.get("toward"))
    against = len(grid) - with_us
    graded = len(grid)
    pct = round(100 * with_us / graded) if graded else None
    spread_pct = round(100 * spread_toward / spread_total) if spread_total else None
    total_pct = round(100 * total_toward / total_total) if total_total else None

    best_price = _best_price_books(closing_odds)

    travelled = bool(travelled_sides or travelled_totals)
    ready = graded > 0 and travelled

    return {
        "empty": not ready,
        "ready": ready,
        "week": week,
        "year": year,
        "pct": pct,
        "with_us": with_us,
        "against": against,
        "graded": graded,
        "grid": grid,
        "spread_pct": spread_pct,
        "spread_hits": spread_toward,
        "spread_graded": spread_total,
        "total_pct": total_pct,
        "total_hits": total_toward,
        "total_graded": total_total,
        "best_price": best_price if ready else [],
        "travelled_sides": sorted(travelled_sides, key=lambda x: -abs(x["delta"])),
        "travelled_totals": sorted(travelled_totals, key=lambda x: -abs(x["delta"])),
        "opener_at": _opener_timestamp(),
    }


def _closing_odds_from_history(history: pd.DataFrame) -> pd.DataFrame:
    """Last captured Odds API quote per book/market — used as close for finished weeks."""
    if history.empty or "snapshot_at" not in history.columns:
        return pd.DataFrame()
    hist = history.copy()
    hist["snapshot_at"] = pd.to_datetime(hist["snapshot_at"], utc=True, errors="coerce")
    group_cols = [c for c in ["event", "market_key", "selection", "book_id"] if c in hist.columns]
    if not group_cols:
        return pd.DataFrame()
    return hist.sort_values("snapshot_at").groupby(group_cols, dropna=False).tail(1).reset_index(drop=True)


def _opener_timestamp() -> str | None:
    if not _opener_path().exists():
        return None
    try:
        data = json.loads(_opener_path().read_text(encoding="utf-8"))
        return data.get("updatedAt")
    except (json.JSONDecodeError, OSError):
        return None


def _best_price_books(odds_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Share of markets where each book posted the best American price."""
    if odds_df.empty:
        return []

    if "event" not in odds_df.columns or "market_key" not in odds_df.columns:
        return []

    work = odds_df.copy()
    if "book_id" not in work.columns and "book" in work.columns:
        work["book_id"] = work["book"]
    if "book_id" not in work.columns:
        return []

    work = work[work["book_id"].map(lambda b: book_allowed(str(b)))]
    mk = work["market_key"].astype(str).str.lower()
    work = work[mk.isin({"spreads", "spread", "totals", "total", "h2h", "moneyline"})]
    if work.empty:
        return []

    if "line" in work.columns:
        work["line_key"] = pd.to_numeric(work["line"], errors="coerce").round(1)
    else:
        work["line_key"] = pd.NA
    best_counts: dict[str, int] = {}
    markets_graded = 0

    group_cols = ["event", "market_key", "selection", "line_key"]
    for _, grp in work.groupby(group_cols, dropna=False):
        if grp.empty:
            continue
        prices = pd.to_numeric(grp["price"], errors="coerce")
        if prices.isna().all():
            continue
        winner = normalize_book_id(str(grp.loc[prices.idxmax(), "book_id"] or ""))
        if not winner:
            continue
        markets_graded += 1
        best_counts[winner] = best_counts.get(winner, 0) + 1

    if not best_counts or markets_graded == 0:
        return []

    totals_by_book = sorted(best_counts.items(), key=lambda x: -x[1])
    top = totals_by_book[:5]
    rest_best = sum(cnt for _, cnt in totals_by_book[5:])

    out: list[dict[str, Any]] = []
    for bid, cnt in top:
        out.append(
            {
                "book_id": bid,
                "book": book_label(bid),
                "share_pct": round(100 * cnt / markets_graded),
                "markets": cnt,
            }
        )
    if rest_best > 0:
        out.append(
            {
                "book_id": "",
                "book": "Everyone else",
                "share_pct": round(100 * rest_best / markets_graded),
                "markets": rest_best,
            }
        )
    return out
