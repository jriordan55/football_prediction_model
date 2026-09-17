"""Sharp Lines player prop board — +EV grid from The Odds API multi-book lines only."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .devig_methods import DEVIG_BOOK_PRESETS, devig_side_prob, fair_american_from_prob, quarter_kelly_units, weighted_fair_prob
from .odds_math import american_to_implied, ev_pct
from .prop_pricing import PROP_KEYS, prop_key_from_row
from .prop_reprice import reprice_prop_row

# Screenshot book order — no Onyx; only real sportsbooks.
DISPLAY_PROP_BOOKS: list[tuple[str, str]] = [
    ("pinnacle", "PIN"),
    ("fanduel", "FD"),
    ("draftkings", "DK"),
    ("bet365", "365"),
    ("betmgm", "MGM"),
    ("thescore", "theScore"),
    ("caesars", "CZ"),
    ("fanatics", "FN"),
    ("betrivers", "BR"),
]

PROP_ABBR = {
    "pass_yds_q1": "1q pass yds",
    "pass_yds": "pass yds",
    "rush_yds": "rush yds",
    "rec_yds": "rec yds",
    "pass_tds": "pass td",
    "receptions": "rec",
    "tds": "attd",
    "rush_tds": "rush td",
    "rec_tds": "rec td",
    "first_td": "1st td",
    "last_td": "last td",
    "pass_attempts": "pass att",
    "pass_completions": "comp",
    "pass_ints": "pass int",
    "pass_long": "long pass",
    "pass_rush_yds": "p+r yds",
    "prd_tds": "prd td",
    "prd_yds": "prd yds",
    "rush_attempts": "rush att",
    "rush_long": "long rush",
    "rr_tds": "rr td",
    "rr_yds": "rr yds",
    "rec_long": "long rec",
    "field_goals": "fg",
    "kicking_pts": "kick pts",
    "pats": "pat",
    "sacks": "sacks",
    "solo_tackles": "solo tkl",
    "tackles_assists": "tkl+ast",
    "tds_over": "td over",
}

_ONYX_BOOKS = frozenset({"onyx", "onyx_odds", "onyxodds"})


def _normalize_player(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _prop_group_key(row: dict[str, Any]) -> str | None:
    player = _normalize_player(row.get("player") or "")
    prop = prop_key_from_row(row) or row.get("propKey") or ""
    if not player or not prop:
        return None
    try:
        line = round(float(row.get("line")) * 2) / 2
    except (TypeError, ValueError):
        return None
    return f"{player}|{prop}|{line}"


def _line_better(side: str, line_a: float | None, line_b: float | None) -> bool:
    if line_a is None or line_b is None:
        return False
    if side == "over":
        return line_a < line_b - 1e-9
    return line_a > line_b + 1e-9


def _price_better(price_a: Any, price_b: Any) -> bool:
    ia, ib = american_to_implied(price_a), american_to_implied(price_b)
    if ia is None or ib is None:
        return False
    return ia > ib + 1e-9


def _best_quote(side: str, books: dict[str, dict[str, Any]], *, avg_line: float | None) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for bid, q in books.items():
        price = q.get(f"{side}_price") or q.get("price")
        if not price:
            continue
        candidate = {**q, "book_id": bid, "price": price}
        if best is None:
            best = candidate
            continue
        if avg_line is not None and _line_better(side, q.get("line"), avg_line):
            if not _line_better(side, best.get("line"), avg_line):
                best = candidate
                continue
        if _line_better(side, q.get("line"), best.get("line")):
            best = candidate
            continue
        if q.get("line") == best.get("line") and _price_better(price, best.get("price")):
            best = candidate
    return best


def _model_prob_for_side(row: dict[str, Any], side: str, price: Any) -> float | None:
    prop_key = prop_key_from_row(row) or row.get("propKey")
    if not prop_key or str(prop_key).lower() not in PROP_KEYS:
        return None
    meta = {
        **row,
        "side": side,
        "price": price,
        "propKey": prop_key,
        "source": "theoddsapi",
    }
    repriced = reprice_prop_row(meta, skip_gamelog=True)
    try:
        wp = float(repriced.get("winProb"))
        if 0 < wp < 1:
            return wp
    except (TypeError, ValueError):
        pass
    return None


def _aggregate_groups(multi_book_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    if multi_book_df.empty:
        return groups

    for _, row in multi_book_df.iterrows():
        r = row.to_dict()
        bid = str(r.get("book_id") or "").lower()
        if bid in _ONYX_BOOKS:
            continue

        gkey = _prop_group_key(r)
        if not gkey:
            continue

        g = groups.setdefault(
            gkey,
            {
                "player": r.get("player"),
                "propKey": prop_key_from_row(r) or r.get("propKey"),
                "market": r.get("market"),
                "line": r.get("line"),
                "home": r.get("home"),
                "away": r.get("away"),
                "books": {},
            },
        )

        try:
            line_f = float(r.get("line"))
        except (TypeError, ValueError):
            line_f = None

        entry = g["books"].setdefault(
            bid, {"line": line_f, "over_price": None, "under_price": None, "price": None}
        )
        if line_f is not None:
            entry["line"] = line_f
        for side in ("over", "under"):
            px = r.get(f"{side}_price")
            if px:
                entry[f"{side}_price"] = px
        primary = entry.get("over_price") or entry.get("under_price")
        if primary:
            entry["price"] = primary

    return groups


def build_prop_sharp_rows(
    multi_book_df: pd.DataFrame,
    *,
    devig_method: str = "mpto",
    devig_preset: str = "mkt_avg",
) -> list[dict[str, Any]]:
    """Build +EV rows from The Odds API multi-book props only."""
    if multi_book_df.empty:
        return []

    preset = DEVIG_BOOK_PRESETS.get(devig_preset) or DEVIG_BOOK_PRESETS["mkt_avg"]
    weights = preset.get("weights") or {}
    groups = _aggregate_groups(multi_book_df)

    rows_out: list[dict[str, Any]] = []

    for g in groups.values():
        prop_key = str(g.get("propKey") or "")
        home, away = str(g.get("home") or ""), str(g.get("away") or "")

        for side in ("over", "under"):
            quotes: list[dict[str, Any]] = []
            for bid, entry in g["books"].items():
                if bid in _ONYX_BOOKS:
                    continue
                op, up = entry.get("over_price"), entry.get("under_price")
                if not op and not up:
                    continue
                quotes.append(
                    {
                        "book_id": bid,
                        "over_price": op,
                        "under_price": up,
                        "line": entry.get("line"),
                    }
                )

            if not quotes:
                continue

            side_price = any(q.get(f"{side}_price") for q in quotes)
            if not side_price:
                continue

            fair_p = weighted_fair_prob(quotes, side, method=devig_method, weights=weights or None)
            if fair_p is None:
                for q in quotes:
                    opp = "under_price" if side == "over" else "over_price"
                    fair_p = devig_side_prob(q.get(f"{side}_price"), q.get(opp), method=devig_method)
                    if fair_p is not None:
                        break
            if fair_p is None:
                continue

            lines = [q.get("line") for q in g["books"].values() if q.get("line") is not None]
            avg_line = float(sum(lines) / len(lines)) if lines else None
            best = _best_quote(side, g["books"], avg_line=avg_line)
            if not best or not best.get("price"):
                continue

            meta_row = {
                "player": g.get("player"),
                "propKey": prop_key,
                "line": g.get("line"),
                "home": home,
                "away": away,
                "side": side,
                "price": best.get("price"),
                "market": g.get("market"),
            }
            model_p = _model_prob_for_side(meta_row, side, best.get("price"))
            if model_p is not None:
                ev = ev_pct(model_p, best.get("price"))
            else:
                imp = american_to_implied(best.get("price"))
                ev = round((fair_p - imp) * 100, 1) if imp is not None else None
            if ev is None:
                continue

            qk = quarter_kelly_units(model_p or fair_p, best.get("price"))

            from .team_registry import teams_match

            team = ""
            opp = ""
            opp_prefix = "@"
            opp_logo = ""
            try:
                from .prop_reprice import resolve_prop_team, team_logo_for

                team = resolve_prop_team(meta_row) or ""
                if team:
                    on_home = teams_match(team, home)
                    opp = away if on_home else home
                    opp_prefix = "v" if on_home else "@"
                    opp_logo = team_logo_for(meta_row, team) or ""
            except Exception:
                pass

            cell_flags: dict[str, bool] = {}
            best_price = best.get("price")
            for bid, q in g["books"].items():
                px = q.get(f"{side}_price") or q.get("price")
                if not px:
                    continue
                is_best = best.get("book_id") == bid and px == best_price
                line_edge = avg_line is not None and _line_better(side, q.get("line"), avg_line)
                price_edge = _price_better(px, best_price) if best_price else False
                cell_flags[bid] = bool(is_best or line_edge or price_edge)

            fair_price = fair_american_from_prob(fair_p)

            prop_label = g.get("market") or PROP_ABBR.get(prop_key, prop_key.replace("_", " "))
            try:
                line_txt = f"{float(g.get('line')):g}"
            except (TypeError, ValueError):
                line_txt = str(g.get("line") or "")

            rows_out.append(
                {
                    "player": g.get("player"),
                    "position": "",
                    "teamLogo": opp_logo,
                    "propKey": prop_key,
                    "propAbbr": PROP_ABBR.get(prop_key, prop_key.replace("_", " ")),
                    "market": prop_label,
                    "pick": f"{side.title()} {line_txt}".strip(),
                    "line": g.get("line"),
                    "side": side,
                    "ev_pct": ev,
                    "fair_prob": fair_p,
                    "fair_price": fair_price,
                    "implied_pct": round(float(fair_p) * 100, 1),
                    "quarter_kelly": qk,
                    "model_prob": model_p,
                    "best": best,
                    "books": g["books"],
                    "cell_flags": cell_flags,
                    "home": home,
                    "away": away,
                    "opp": opp,
                    "opp_logo": opp_logo,
                    "opp_prefix": opp_prefix,
                    "team": team,
                }
            )

    rows_out.sort(key=lambda r: float(r.get("ev_pct") or 0), reverse=True)
    return rows_out


def filter_prop_rows(
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
    if market and market not in ("ALL", "All Props"):
        mk = market.lower()
        out = [
            r
            for r in out
            if mk in str(r.get("propKey") or "").lower()
            or mk in str(r.get("propAbbr") or "").lower()
            or mk in str(r.get("market") or "").lower()
        ]
    if book and book not in ("ALL", "All"):
        bid = book.lower()
        label_map = {lbl.lower(): b for b, lbl in DISPLAY_PROP_BOOKS}
        bid = label_map.get(bid, bid)
        out = [r for r in out if bid in (r.get("books") or {}) or r.get("best", {}).get("book_id") == bid]
    if edges_only:
        out = [r for r in out if float(r.get("ev_pct") or 0) >= min_ev]
    elif min_ev > 0:
        out = [r for r in out if float(r.get("ev_pct") or 0) >= min_ev]
    return out
