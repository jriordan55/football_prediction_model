"""Sharp Lines game-line board — Pinnacle fair vs soft books."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .devig_methods import DEVIG_BOOK_PRESETS, devig_side_prob, fair_american_from_prob, quarter_kelly_units
from .odds_client import market_label
from .odds_math import adjust_fair_prob_for_line, american_to_implied, ev_pct, line_points_bettor_delta
from .sharp_board import DISPLAY_BOOKS, _collect_book_quotes, build_sharp_board_groups, lines_match_across_books
from .sharp_edges import PINNACLE, _is_spread_key, _is_team_total_key, _is_total_key
from .team_registry import resolve_canonical

# Pinnacle is the sharp reference — listed first, no Circa.
DISPLAY_SHARP_BOOKS: list[tuple[str, str]] = [
    ("pinnacle", "PIN"),
    *DISPLAY_BOOKS,
]

SOFT_BOOK_IDS = {bid for bid, _ in DISPLAY_BOOKS}


def _fmt_american(price: Any) -> str | None:
    if price is None:
        return None
    try:
        n = int(float(str(price).replace("+", "").replace("−", "-")))
        return f"+{n}" if n > 0 else str(n)
    except (TypeError, ValueError):
        return str(price)


def _opp_side_key(side_key: str, mk: str) -> str | None:
    sk = side_key.lower()
    if _is_spread_key(mk):
        return "away" if sk == "home" else "home"
    if _is_total_key(mk) and not _is_team_total_key(mk):
        return "under" if sk == "over" else "over"
    if _is_team_total_key(mk) and "_" in side_key:
        team, ou = side_key.rsplit("_", 1)
        return f"{team}_{'under' if ou == 'over' else 'over'}"
    return None


def _fair_prob_for_side(
    side_key: str,
    mk: str,
    book_quotes: dict[str, dict[str, dict[str, Any]]],
    ref_source: str,
    *,
    devig_method: str,
) -> float | None:
    """Fair probability — Pinnacle devig when available, else median-book devig."""
    opp_key = _opp_side_key(side_key, mk)
    pin = book_quotes.get(PINNACLE, {})

    if ref_source == "pinnacle" and pin:
        side_q = pin.get(side_key) or {}
        opp_q = pin.get(opp_key or "", {}) if opp_key else {}
        fair = devig_side_prob(side_q.get("price"), opp_q.get("price"), method=devig_method)
        if fair is not None:
            return fair

    # Market-average fallback (derivatives when Pinnacle absent).
    side_prices: list[Any] = []
    opp_prices: list[Any] = []
    for _bid, sides in book_quotes.items():
        sq = sides.get(side_key)
        if sq and sq.get("price") is not None:
            side_prices.append(sq["price"])
        if opp_key:
            oq = sides.get(opp_key)
            if oq and oq.get("price") is not None:
                opp_prices.append(oq["price"])

    if not side_prices:
        return None

    def _median_price(prices: list[Any]) -> Any:
        nums = []
        for p in prices:
            imp = american_to_implied(p)
            if imp is not None:
                nums.append((imp, p))
        if not nums:
            return None
        nums.sort(key=lambda x: x[0])
        return nums[len(nums) // 2][1]

    med_side = _median_price(side_prices)
    med_opp = _median_price(opp_prices) if opp_prices else None
    return devig_side_prob(med_side, med_opp, method=devig_method)


def _pick_label(side: dict[str, Any], quote: dict[str, Any], mk: str) -> str:
    side_key = str(side.get("side_key") or "")
    selection = quote.get("selection")
    line = quote.get("line")
    try:
        lf = float(line)
        if _is_spread_key(mk):
            line_txt = f"{lf:+.1f}"
        else:
            line_txt = f"{lf:g}"
    except (TypeError, ValueError):
        line_txt = str(line or "")

    if selection and _is_spread_key(mk):
        canon = resolve_canonical(str(selection)) or str(selection)
        return f"{canon} {line_txt}".strip()

    label = str(side.get("side_label") or side_key)
    if line is None:
        return label
    if _is_spread_key(mk):
        return f"{label} {line_txt}"
    if _is_total_key(mk) and not _is_team_total_key(mk):
        return f"{label} {line_txt}"
    if _is_team_total_key(mk) and "_" in side_key:
        team, ou = side_key.rsplit("_", 1)
        return f"{team} {ou.title()} {line_txt}"
    return f"{label} {line_txt}"


def _best_soft_offer(
    side_key: str,
    mk: str,
    book_quotes: dict[str, dict[str, dict[str, Any]]],
    fair_p: float,
    ref_line: Any,
) -> tuple[dict[str, Any] | None, float | None, float]:
    """Pick the soft-book offer with highest line-adjusted EV."""
    best: dict[str, Any] | None = None
    best_ev: float | None = None
    best_line_edge = 0.0

    try:
        ref_f = float(ref_line) if ref_line is not None else None
    except (TypeError, ValueError):
        ref_f = None

    for bid in SOFT_BOOK_IDS:
        q = book_quotes.get(bid, {}).get(side_key)
        if not q or q.get("price") is None:
            continue
        try:
            bl = float(q["line"]) if q.get("line") is not None else None
        except (TypeError, ValueError):
            bl = None

        adj_fair = fair_p
        if ref_f is not None and bl is not None:
            adj_fair = adjust_fair_prob_for_line(
                fair_p,
                side_key=side_key,
                market_key=mk,
                ref_line=ref_f,
                target_line=bl,
            )

        offer_ev = ev_pct(adj_fair, q.get("price"))
        if offer_ev is None:
            continue

        if best_ev is None or offer_ev > best_ev + 1e-9:
            best_ev = offer_ev
            best = {"book_id": bid, **q, "adj_fair": adj_fair}
            if ref_f is not None and bl is not None:
                best_line_edge = abs(line_points_bettor_delta(side_key, mk, ref_f, bl))

    return best, best_ev, best_line_edge


def build_game_sharp_rows(
    lines_df: pd.DataFrame,
    *,
    devig_method: str = "mpto",
    devig_preset: str = "mkt_avg",
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
) -> list[dict[str, Any]]:
    if lines_df.empty:
        return []

    _ = DEVIG_BOOK_PRESETS.get(devig_preset)  # reserved for weighted presets
    groups = build_sharp_board_groups(lines_df, min_edge=0.0, edges_only=False)
    rows_out: list[dict[str, Any]] = []

    grouped = lines_df.groupby(["event_id", "event", "home", "away", "market_key"], dropna=False)

    for g in groups:
        mk = str(g.get("market_key") or "").lower()
        event_id = g.get("event_id")
        home, away = str(g.get("home") or ""), str(g.get("away") or "")

        try:
            evdf = grouped.get_group((event_id, g.get("event"), home, away, g.get("market_key")))
        except KeyError:
            continue

        book_quotes = _collect_book_quotes(evdf, home, away, mk)
        ref_source = str(g.get("ref_source") or "pinnacle")

        for side in g.get("sides") or []:
            side_key = str(side.get("side_key") or "")
            if not lines_match_across_books(book_quotes, side_key):
                continue

            ref = side.get("ref") or {}
            ref_line = ref.get("line")
            ref_price = ref.get("price")

            fair_p = _fair_prob_for_side(side_key, mk, book_quotes, ref_source, devig_method=devig_method)
            if fair_p is None:
                continue

            best, best_ev, line_edge = _best_soft_offer(side_key, mk, book_quotes, fair_p, ref_line)
            if not best:
                best = side.get("best") or {}
                best_ev = 0.0
                line_edge = float(side.get("edge_points") or 0)

            best_bid = best.get("book_id")
            best_price = best.get("price")
            best_line = best.get("line")

            ev = float(best_ev or 0.0)
            sort_ev = ev if ev > 0 else line_edge

            qk = quarter_kelly_units(best.get("adj_fair") or fair_p, best_price or ref_price)

            books: dict[str, dict[str, Any]] = {}
            if ref_line is not None and ref_price is not None:
                books[PINNACLE] = {
                    "line": ref_line,
                    "price": _fmt_american(ref_price),
                }
            for bid, q in (side.get("books") or {}).items():
                books[bid] = {
                    "line": q.get("line"),
                    "price": _fmt_american(q.get("price")),
                }

            cell_flags: dict[str, bool] = {}
            if best_bid:
                cell_flags[best_bid] = True
            # Also flag books with positive line-adjusted EV within 0.3% of best.
            if ref_line is not None:
                try:
                    ref_f = float(ref_line)
                except (TypeError, ValueError):
                    ref_f = None
                if ref_f is not None:
                    for bid in SOFT_BOOK_IDS:
                        q = book_quotes.get(bid, {}).get(side_key)
                        if not q or q.get("price") is None:
                            continue
                        try:
                            bl = float(q["line"]) if q.get("line") is not None else None
                        except (TypeError, ValueError):
                            bl = None
                        adj = fair_p
                        if bl is not None:
                            adj = adjust_fair_prob_for_line(
                                fair_p,
                                side_key=side_key,
                                market_key=mk,
                                ref_line=ref_f,
                                target_line=bl,
                            )
                        offer_ev = ev_pct(adj, q.get("price")) or 0.0
                        if offer_ev > 0 and best_ev is not None and offer_ev >= float(best_ev) - 0.3:
                            cell_flags[bid] = True

            try:
                ref_f = float(ref_line) if ref_line is not None else None
                best_f = float(best_line) if best_line is not None else None
            except (TypeError, ValueError):
                ref_f = best_f = None
            edge_pts = 0.0
            if ref_f is not None and best_f is not None:
                edge_pts = round(abs(line_points_bettor_delta(side_key, mk, ref_f, best_f)) * 10) / 10

            rows_out.append(
                {
                    "event": g.get("event"),
                    "home": home,
                    "away": away,
                    "market_key": mk,
                    "market": g.get("market") or market_label(mk),
                    "pick": _pick_label(side, best, mk),
                    "side": side_key,
                    "line": best_line if best_line is not None else ref_line,
                    "ref_line": ref_line,
                    "ev_pct": ev,
                    "edge_points": edge_pts,
                    "sort_ev": sort_ev,
                    "fair_prob": fair_p,
                    "fair_price": fair_american_from_prob(fair_p),
                    "implied_pct": round(float(fair_p) * 100, 1),
                    "quarter_kelly": qk,
                    "ref_source": ref_source,
                    "best": {
                        "book_id": best_bid,
                        "price": _fmt_american(best_price),
                        "line": best_line,
                    },
                    "books": books,
                    "cell_flags": cell_flags,
                }
            )

    rows_out.sort(key=lambda r: float(r.get("sort_ev") or r.get("ev_pct") or 0), reverse=True)
    from .csv_log import log_sharp_lines

    log_sharp_lines(rows_out, tab=tab, year=year, week=week, dedupe=True)
    return rows_out


def filter_game_rows(
    rows: list[dict[str, Any]],
    *,
    matchup: str = "ALL",
    market: str = "ALL",
    book: str = "ALL",
    min_ev: float = 0.0,
    edges_only: bool = False,
) -> list[dict[str, Any]]:
    out = rows
    if matchup and matchup != "ALL":
        ml = matchup.lower()
        out = [r for r in out if ml in str(r.get("home") or "").lower() or ml in str(r.get("away") or "").lower()]
    if market and market not in ("ALL", "All Markets"):
        mk = market.lower()
        out = [
            r
            for r in out
            if mk in str(r.get("market_key") or "").lower() or mk in str(r.get("market") or "").lower()
        ]
    if book and book not in ("ALL", "All"):
        bid = book.lower()
        label_map = {lbl.lower(): b for b, lbl in DISPLAY_SHARP_BOOKS}
        bid = label_map.get(bid, bid)
        out = [r for r in out if bid in (r.get("books") or {}) or r.get("best", {}).get("book_id") == bid]
    if edges_only or min_ev > 0:
        floor = min_ev if min_ev > 0 else 0.0
        out = [
            r
            for r in out
            if float(r.get("ev_pct") or 0) >= floor or float(r.get("sort_ev") or 0) >= floor
        ]
    return out
