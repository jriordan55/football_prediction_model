"""Multi-book sharp comparison board — Pinnacle or market-average reference."""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

import pandas as pd

from .odds_client import market_label
from .odds_math import line_diff_better, median_line
from .sharp_edges import (
    PINNACLE,
    SOFT_BOOKS,
    _american_num,
    _better_american,
    _book_side_rows,
    _book_team_total_rows,
    _is_h2h_key,
    _is_spread_key,
    _is_team_total_key,
    _is_total_key,
)

# Column order for the odds grid (soft books after reference/best).
DISPLAY_BOOKS: list[tuple[str, str]] = [
    ("draftkings", "DK"),
    ("fanduel", "FD"),
    ("betmgm", "MGM"),
    ("caesars", "CZR"),
    ("betrivers", "BR"),
    ("thescore", "theScore"),
    ("fanatics", "FAN"),
]

PRIMARY_MARKETS = {"spreads", "totals"}

_LINE_MATCH_TOL = 1e-6


def _side_line_values(book_quotes: dict[str, dict[str, dict[str, Any]]], side_key: str) -> list[float]:
    """Collect numeric lines for a side from every book that posted one."""
    vals: list[float] = []
    for sides in book_quotes.values():
        q = sides.get(side_key)
        if not q or q.get("line") is None:
            continue
        try:
            vals.append(float(q["line"]))
        except (TypeError, ValueError):
            continue
    return vals


def lines_match_across_books(book_quotes: dict[str, dict[str, dict[str, Any]]], side_key: str) -> bool:
    """True when every book with a line for this side posts the same number."""
    vals = _side_line_values(book_quotes, side_key)
    if not vals:
        return False
    ref = vals[0]
    return all(abs(v - ref) <= _LINE_MATCH_TOL for v in vals)


def _side_prefix(side_key: str) -> str:
    sk = side_key.lower()
    if sk == "over":
        return "o"
    if sk == "under":
        return "u"
    if sk == "home":
        return ""
    if sk == "away":
        return ""
    if sk.endswith("_over"):
        return "o"
    if sk.endswith("_under"):
        return "u"
    return ""


def _line_better(side_key: str, candidate: float, reference: float) -> bool:
    return line_diff_better(side_key, candidate, reference) > 1e-6


def _quote_better(side_key: str, book_line: float | None, book_price: Any, ref_line: float | None, ref_price: Any) -> bool:
    if book_line is None or ref_line is None:
        return False
    try:
        bl, rl = float(book_line), float(ref_line)
    except (TypeError, ValueError):
        return False
    if _line_better(side_key, bl, rl):
        return True
    if abs(bl - rl) > 1e-6:
        return False
    if book_price is None or ref_price is None:
        return False
    return _better_american(book_price, ref_price) == book_price and _american_num(book_price) != _american_num(ref_price)


def _median_price(prices: list[Any]) -> Any:
    nums = [_american_num(p) for p in prices]
    nums = [n for n in nums if n is not None]
    if not nums:
        return None
    med = float(sorted(nums)[len(nums) // 2])
    n = int(round(med))
    return f"+{n}" if n > 0 else str(n)


def _collect_book_quotes(evdf: pd.DataFrame, home: str, away: str, market_key: str) -> dict[str, dict[str, dict[str, Any]]]:
    """book_id -> side_key -> {line, price, selection}."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    mk = str(market_key or "").lower()
    for book_id, bdf in evdf.groupby("book_id"):
        bid = str(book_id).lower()
        if _is_team_total_key(mk):
            teams = _book_team_total_rows(bdf, home, away)
            sides: dict[str, dict[str, Any]] = {}
            for team, pair in teams.items():
                if pair.get("over"):
                    sides[f"{team}_over"] = {**pair["over"], "team": team}
                if pair.get("under"):
                    sides[f"{team}_under"] = {**pair["under"], "team": team}
        else:
            sides = _book_side_rows(bdf, home, away, mk)
        if sides:
            out[bid] = sides
    return out


def _reference_for_side(
    side_key: str,
    book_quotes: dict[str, dict[str, dict[str, Any]]],
    *,
    use_avg_fallback: bool,
) -> tuple[dict[str, Any] | None, str]:
    pin = book_quotes.get(PINNACLE, {}).get(side_key)
    if pin and pin.get("line") is not None:
        return pin, "pinnacle"

    if not use_avg_fallback:
        return None, "pinnacle"

    lines: list[float] = []
    prices: list[Any] = []
    for bid, sides in book_quotes.items():
        q = sides.get(side_key)
        if not q or q.get("line") is None:
            continue
        try:
            lines.append(float(q["line"]))
        except (TypeError, ValueError):
            continue
        if q.get("price") is not None:
            prices.append(q["price"])

    med = median_line(lines)
    if med is None:
        return None, "avg"
    return {"line": med, "price": _median_price(prices)}, "avg"


def _best_soft_quote(
    side_key: str,
    book_quotes: dict[str, dict[str, dict[str, Any]]],
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    ref_line = ref.get("line")
    ref_price = ref.get("price")
    for bid in SOFT_BOOKS:
        q = book_quotes.get(bid, {}).get(side_key)
        if not q or q.get("line") is None or q.get("price") is None:
            continue
        if best is None:
            best = {"book_id": bid, **q}
            continue
        try:
            bl = float(q["line"])
            bbl = float(best["line"])
        except (TypeError, ValueError):
            continue
        line_edge = line_diff_better(side_key, bl, bbl)
        price_better = _better_american(q["price"], best["price"]) == q["price"]
        if line_edge > 1e-6 or (abs(line_edge) <= 1e-6 and price_better):
            best = {"book_id": bid, **q}

    if best and ref_line is not None:
        try:
            edge = round(abs(float(best["line"]) - float(ref_line)) * 10) / 10
        except (TypeError, ValueError):
            edge = 0.0
        best["edge_points"] = edge
    return best


def _side_keys_for_market(market_key: str, home: str, away: str, book_quotes: dict) -> list[tuple[str, str]]:
    """Return (side_key, display_label) pairs."""
    mk = str(market_key or "").lower()
    if _is_spread_key(mk):
        return [("home", home), ("away", away)]
    if _is_total_key(mk):
        return [("over", "Over"), ("under", "Under")]
    return []


def _build_one_group(
    *,
    event_id: Any,
    event: str,
    home: str,
    away: str,
    market_key: str,
    commence: Any,
    book_quotes: dict[str, dict[str, dict[str, Any]]],
    side_defs: list[tuple[str, str]],
    use_avg_fallback: bool,
    min_edge: float,
    edges_only: bool,
    market_suffix: str | None = None,
) -> dict[str, Any] | None:
    mk = str(market_key or "").lower()
    ref_source = "pinnacle"
    sides_out: list[dict[str, Any]] = []

    for side_key, side_label in side_defs:
        if not lines_match_across_books(book_quotes, side_key):
            if mk in PRIMARY_MARKETS:
                return None
            continue

        ref, src = _reference_for_side(side_key, book_quotes, use_avg_fallback=use_avg_fallback)
        if ref is None or ref.get("line") is None:
            if mk in PRIMARY_MARKETS:
                return None
            continue
        if src == "avg":
            ref_source = "avg"

        books_out: dict[str, dict[str, Any]] = {}
        for bid, _ in DISPLAY_BOOKS:
            q = book_quotes.get(bid, {}).get(side_key)
            if q and q.get("line") is not None:
                books_out[bid] = q

        best = _best_soft_quote(side_key, book_quotes, ref)
        edge_pts = float(best.get("edge_points") or 0) if best else 0.0

        cell_flags: dict[str, bool] = {}
        for bid, q in books_out.items():
            cell_flags[bid] = _quote_better(side_key, q.get("line"), q.get("price"), ref.get("line"), ref.get("price"))
        if best:
            cell_flags["best"] = _quote_better(side_key, best.get("line"), best.get("price"), ref.get("line"), ref.get("price"))

        sides_out.append(
            {
                "side_key": side_key,
                "side_label": side_label,
                "prefix": _side_prefix(side_key),
                "ref": ref,
                "books": books_out,
                "best": best,
                "edge_points": edge_pts,
                "cell_flags": cell_flags,
            }
        )

    if len(sides_out) < 2:
        return None

    max_edge = max((s.get("edge_points") or 0) for s in sides_out)
    if edges_only and max_edge < min_edge:
        return None
    if min_edge > 0 and max_edge < min_edge:
        return None

    market = market_label(mk)
    if market_suffix:
        market = f"{market} · {market_suffix}"

    return {
        "event_id": event_id,
        "event": event,
        "home": home,
        "away": away,
        "market_key": mk,
        "market": market,
        "commence_time": commence,
        "ref_source": ref_source,
        "sides": sides_out,
        "max_edge": max_edge,
    }


def build_sharp_board_groups(df: pd.DataFrame, *, min_edge: float = 0.0, edges_only: bool = False) -> list[dict[str, Any]]:
    if df.empty:
        return []

    groups: list[dict[str, Any]] = []
    grouped = df.groupby(["event_id", "event", "home", "away", "market_key"], dropna=False)

    for (event_id, event, home, away, market_key), evdf in grouped:
        mk = str(market_key or "").lower()
        if _is_h2h_key(mk):
            continue
        if not (_is_spread_key(mk) or _is_total_key(mk) or _is_team_total_key(mk)):
            continue

        use_avg_fallback = mk not in PRIMARY_MARKETS
        book_quotes = _collect_book_quotes(evdf, str(home), str(away), mk)
        if not book_quotes:
            continue

        commence = evdf["commence_time"].iloc[0] if "commence_time" in evdf.columns else None

        if _is_team_total_key(mk):
            teams_seen: set[str] = set()
            for sides in book_quotes.values():
                for sk in sides:
                    if "_" in sk:
                        teams_seen.add(sk.rsplit("_", 1)[0])
            for team in sorted(teams_seen):
                tg = _build_one_group(
                    event_id=event_id,
                    event=str(event),
                    home=str(home),
                    away=str(away),
                    market_key=mk,
                    commence=commence,
                    book_quotes=book_quotes,
                    side_defs=[(f"{team}_over", f"{team} Over"), (f"{team}_under", f"{team} Under")],
                    use_avg_fallback=use_avg_fallback,
                    min_edge=min_edge,
                    edges_only=edges_only,
                    market_suffix=team,
                )
                if tg:
                    groups.append(tg)
            continue

        side_defs = _side_keys_for_market(mk, str(home), str(away), book_quotes)
        if not side_defs:
            continue

        tg = _build_one_group(
            event_id=event_id,
            event=str(event),
            home=str(home),
            away=str(away),
            market_key=mk,
            commence=commence,
            book_quotes=book_quotes,
            side_defs=side_defs,
            use_avg_fallback=use_avg_fallback,
            min_edge=min_edge,
            edges_only=edges_only,
        )
        if tg:
            groups.append(tg)

    groups.sort(key=lambda g: g.get("max_edge") or 0, reverse=True)
    return groups


def filter_board_groups(
    groups: list[dict[str, Any]],
    *,
    market_filter: str = "All",
    period_filter: str = "All",
    team_filter: str | None = None,
    books: list[str] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    mf = market_filter.lower()
    pf = period_filter.lower()

    for g in groups:
        mk = g["market_key"]

        if mf != "all":
            if mf == "spread" and not _is_spread_key(mk):
                continue
            if mf == "total" and not (_is_total_key(mk) and not _is_team_total_key(mk)):
                continue
            if mf == "team total" and not _is_team_total_key(mk):
                continue

        if pf != "all":
            if pf == "full game" and re.search(r"(_h1|_q1|_h2|_q2|_q3|_q4)", mk):
                continue
            if pf == "1st half" and "_h1" not in mk:
                continue
            if pf == "1st quarter" and "_q1" not in mk:
                continue

        if team_filter and team_filter.upper() != "ALL":
            home, away = g["home"], g["away"]
            tf = team_filter.lower()
            if tf not in home.lower() and tf not in away.lower():
                continue

        if books:
            g = {**g, "display_books": [(b, lbl) for b, lbl in DISPLAY_BOOKS if b in books]}
        else:
            g = {**g, "display_books": DISPLAY_BOOKS}
        out.append(g)
    return out


def format_commence(ts: Any) -> tuple[str, str]:
    if ts is None or (isinstance(ts, float) and math.isnan(ts)):
        return "—", ""
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        date = f"{dt.month}/{dt.day}/{dt.year % 100:02d}"
        hour = dt.strftime("%I").lstrip("0") or "12"
        time = f"{hour}:{dt.strftime('%M %p')}"
        return date, time
    except (TypeError, ValueError):
        return "—", ""
