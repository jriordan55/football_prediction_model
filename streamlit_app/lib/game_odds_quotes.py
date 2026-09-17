"""Game-line quotes — DraftKings spread/total + The Odds API team totals."""
from __future__ import annotations

import math
from typing import Any
import pandas as pd

from .config import book_label, normalize_book_id
from .odds_cache import load_cached_lines, pull_sport_odds_lines
from .odds_client import ODDS_API_BOOKMAKERS, fetch_odds_api_event_odds, flatten_odds_api_events
from .sharp_edges import _book_side_rows, _book_team_total_rows
from .team_registry import match_selection_to_side, resolve_canonical, teams_match

_DK = "draftkings"
_DK_LABEL = book_label(_DK)
_FD = "fanduel"
_FD_LABEL = book_label(_FD)
_CARD_BOOKS = (_DK, _FD)

# Prefer DraftKings; fall through other The Odds API books for team totals.
_TT_BOOK_PRIORITY = [
    "draftkings",
    "fanduel",
    "pinnacle",
    "betmgm",
    "caesars",
    "betrivers",
    "thescore",
    "fanatics",
]


def _format_american(price: Any) -> str:
    if price is None or str(price).strip() in ("", "nan", "None"):
        return "—"
    try:
        n = int(float(price))
    except (TypeError, ValueError):
        return str(price)
    return f"+{n}" if n > 0 else str(n)


def _pack(side: dict[str, Any] | None, *, book_id: str, book: str | None = None) -> dict[str, Any] | None:
    if not side:
        return None
    bid = normalize_book_id(book_id) or _DK
    return {
        "line": side.get("line"),
        "price": _format_american(side.get("price")),
        "book": book or book_label(bid),
        "book_id": bid,
    }


def _has_odds_cols(df: pd.DataFrame, *cols: str) -> bool:
    return not df.empty and all(c in df.columns for c in cols)


def _game_rows(odds_df: pd.DataFrame, home: str, away: str) -> pd.DataFrame:
    if odds_df.empty:
        return odds_df.iloc[0:0]
    home_canon = resolve_canonical(home) or home
    away_canon = resolve_canonical(away) or away
    home_col = odds_df["home"].astype(str) if "home" in odds_df.columns else pd.Series(dtype=str)
    away_col = odds_df["away"].astype(str) if "away" in odds_df.columns else pd.Series(dtype=str)
    mask = home_col.apply(lambda h: teams_match(h, home_canon)) & away_col.apply(
        lambda a: teams_match(a, away_canon)
    )
    return odds_df.loc[mask]


def _game_dk_rows(odds_df: pd.DataFrame, home: str, away: str) -> pd.DataFrame:
    game = _game_rows(odds_df, home, away)
    if not _has_odds_cols(game, "book_id"):
        return game.iloc[0:0]
    return game[game["book_id"].astype(str).str.lower().eq(_DK)]


def _team_total_sides_for_book(
    team_totals: pd.DataFrame, home: str, away: str, book_id: str
) -> tuple[str, dict[str, dict[str, Any]] | None]:
    bid = normalize_book_id(book_id)
    if not _has_odds_cols(team_totals, "book_id"):
        return bid, None
    bdf = team_totals[team_totals["book_id"].astype(str).str.lower().eq(bid)]
    if bdf.empty:
        return bid, None
    sides = _book_team_total_rows(bdf, home, away)
    away_tt = home_tt = None
    for team_key, team_sides in sides.items():
        if teams_match(team_key, away):
            away_tt = team_sides
        elif teams_match(team_key, home):
            home_tt = team_sides
    if not away_tt or not home_tt:
        return bid, None
    if not away_tt.get("over") or not home_tt.get("over"):
        return bid, None
    return bid, {"away": away_tt, "home": home_tt}


def _best_team_total_quotes(
    team_totals: pd.DataFrame, home: str, away: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not _has_odds_cols(team_totals, "book_id"):
        return None, None

    books_present = team_totals["book_id"].astype(str).str.lower().unique().tolist()
    ordered = [b for b in _TT_BOOK_PRIORITY if b in books_present]
    ordered.extend(b for b in books_present if b not in ordered)

    for bid in ordered:
        _, sides = _team_total_sides_for_book(team_totals, home, away, bid)
        if not sides:
            continue
        away_q = _pack(sides["away"].get("over"), book_id=bid)
        home_q = _pack(sides["home"].get("over"), book_id=bid)
        if away_q and home_q:
            return away_q, home_q

    return None, None


def slate_team_total_quotes(home: str, away: str, slate: pd.DataFrame | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Onyx derivative Team Total rows — fallback when The Odds API has no team totals."""
    if slate is None or slate.empty or "group" not in slate.columns:
        return None, None

    rows = slate[(slate["group"].astype(str).eq("derivative")) & (slate["market"].astype(str).eq("Team Total"))]
    if rows.empty:
        return None, None

    away_q = home_q = None
    for _, r in rows.iterrows():
        if str(r.get("side") or "").lower() != "over":
            continue
        team = str(r.get("team") or "")
        bid = normalize_book_id(str(r.get("book") or "onyx")) or "onyx"
        quote = _pack(
            {"line": r.get("line"), "price": r.get("price")},
            book_id=bid,
            book=book_label(bid),
        )
        if not quote:
            continue
        if teams_match(team, away):
            away_q = quote
        elif teams_match(team, home):
            home_q = quote

    return away_q, home_q


def _event_ids_missing_team_totals(odds_df: pd.DataFrame) -> list[str]:
    if odds_df.empty or "event_id" not in odds_df.columns:
        return []
    tt = odds_df[odds_df["market_key"].astype(str).str.lower().eq("team_totals")]
    have = set(tt["event_id"].astype(str).tolist()) if not tt.empty else set()
    spread_ids = odds_df.loc[odds_df["market_key"].astype(str).str.lower().eq("spreads"), "event_id"]
    out: list[str] = []
    for eid in spread_ids.astype(str).drop_duplicates().tolist():
        if eid and eid not in have:
            out.append(eid)
    return out


def supplement_team_totals(
    odds_df: pd.DataFrame,
    event_ids: list[str] | None = None,
    *,
    tab: str | None = None,
    limit: int = 40,
) -> pd.DataFrame:
    """Per-event The Odds API pull for team_totals on specific events still missing them."""
    if odds_df.empty:
        return odds_df

    if event_ids:
        tt = odds_df[odds_df["market_key"].astype(str).str.lower().eq("team_totals")]
        have = set(tt["event_id"].astype(str).tolist()) if not tt.empty else set()
        missing = [str(eid) for eid in event_ids if eid and str(eid) not in have][: max(0, int(limit))]
    else:
        missing = _event_ids_missing_team_totals(odds_df)[: max(0, int(limit))]

    if not missing:
        return odds_df

    frames = [odds_df]
    for eid in missing:
        event, _ = fetch_odds_api_event_odds(str(eid), "team_totals", bookmakers=ODDS_API_BOOKMAKERS, tab=tab)
        if not event:
            continue
        chunk = flatten_odds_api_events([event] if isinstance(event, dict) else [])
        if chunk.empty:
            continue
        tt = chunk[chunk["market_key"].astype(str).str.lower().eq("team_totals")]
        if not tt.empty:
            frames.append(tt)

    if len(frames) == 1:
        return odds_df

    merged = pd.concat(frames, ignore_index=True)
    dedupe_cols = [c for c in ["event_id", "market_key", "book_id", "selection", "line", "description"] if c in merged.columns]
    if dedupe_cols:
        merged = merged.drop_duplicates(subset=dedupe_cols, keep="last")
    return merged


def load_board_odds_df(*, tab: str | None = None, event_ids: list[str] | None = None) -> pd.DataFrame:
    """The Odds API lines — extended cache first; optional per-event team-total top-up."""
    try:
        df, _ = pull_sport_odds_lines(require_extended=True, tab=tab)
    except Exception:
        df = load_cached_lines()
    if df.empty:
        return df
    if event_ids:
        return supplement_team_totals(df, event_ids, tab=tab)
    return df


def _derived_team_total_quotes(
    spread_sides: dict[str, dict[str, Any]],
    total_sides: dict[str, dict[str, Any]],
    *,
    home_spread_line: float | None = None,
    total_line: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Implied team O/U lines from posted game spread + total when API team totals missing."""
    over = total_sides.get("over") or {}
    home_sp = spread_sides.get("home") or {}
    try:
        total = float(total_line if total_line is not None else over.get("line"))
        spread_raw = home_sp.get("line")
        if spread_raw is None:
            spread_raw = home_spread_line
        spread = float(spread_raw)
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(total) and math.isfinite(spread)):
        return None, None
    # Home spread convention — matches scores_from_spread_total / SP+ pricing.
    home_line = round((total - spread) / 2, 1)
    away_line = round((total + spread) / 2, 1)
    price = over.get("price")
    return (
        _pack({"line": away_line, "price": price}, book_id=_DK),
        _pack({"line": home_line, "price": price}, book_id=_DK),
    )


def _home_spread_from_team_total_lines(away_line: float, home_line: float) -> float:
    """Implied home spread from team O/U lines (away_line - home_line)."""
    return round(float(away_line) - float(home_line), 2)


def _align_team_total_quotes(
    away_tt: dict[str, Any] | None,
    home_tt: dict[str, Any] | None,
    *,
    home_spread: float | None,
    total: float | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Swap away/home team totals when they disagree with market spread + game total."""
    if not away_tt or not home_tt:
        return away_tt, home_tt
    try:
        away_line = float(away_tt.get("line"))
        home_line = float(home_tt.get("line"))
    except (TypeError, ValueError):
        return away_tt, home_tt

    if total is not None:
        try:
            tot = float(total)
            if math.isfinite(tot) and abs(away_line + home_line - tot) > 0.26:
                if abs(home_line + away_line - tot) <= 0.26:
                    away_tt, home_tt = home_tt, away_tt
                    away_line, home_line = home_line, away_line
        except (TypeError, ValueError):
            pass

    if home_spread is not None:
        try:
            hs = float(home_spread)
            if math.isfinite(hs):
                implied = _home_spread_from_team_total_lines(away_line, home_line)
                if abs(implied - hs) > 0.26 and abs(-implied - hs) < abs(implied - hs):
                    away_tt, home_tt = home_tt, away_tt
        except (TypeError, ValueError):
            pass

    return away_tt, home_tt


def _repack_team_total(
    quote: dict[str, Any] | None,
    *,
    book_id: str,
    price: Any = None,
) -> dict[str, Any] | None:
    if not quote or quote.get("line") is None:
        return quote
    bid = normalize_book_id(book_id) or _DK
    return _pack(
        {"line": quote.get("line"), "price": price if price is not None else quote.get("price")},
        book_id=bid,
    )


def _game_book_rows(odds_df: pd.DataFrame, home: str, away: str, book_id: str) -> pd.DataFrame:
    game = _game_rows(odds_df, home, away)
    if not _has_odds_cols(game, "book_id"):
        return game.iloc[0:0]
    return game[game["book_id"].astype(str).str.lower().eq(normalize_book_id(book_id))]


def _quotes_for_book(
    odds_df: pd.DataFrame,
    home: str,
    away: str,
    book_id: str,
    *,
    slate: pd.DataFrame | None = None,
    home_spread_line: float | None = None,
    total_line: float | None = None,
    game_odds: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Spread/total/team-total quotes for one book."""
    game = game_odds if game_odds is not None else _game_rows(odds_df, home, away)
    bid = normalize_book_id(book_id) or _DK
    book_rows = (
        game[game["book_id"].astype(str).str.lower().eq(bid)]
        if _has_odds_cols(game, "book_id", "market_key")
        else pd.DataFrame()
    )

    spread_sides: dict[str, dict[str, Any]] = {}
    total_sides: dict[str, dict[str, Any]] = {}
    if not book_rows.empty:
        spreads = book_rows[book_rows["market_key"].astype(str).str.lower().eq("spreads")]
        totals = book_rows[book_rows["market_key"].astype(str).str.lower().eq("totals")]
        spread_sides = _book_side_rows(spreads, home, away, "spreads")
        total_sides = _book_side_rows(totals, home, away, "totals")

    team_totals = (
        game[game["market_key"].astype(str).str.lower().eq("team_totals")]
        if _has_odds_cols(game, "market_key")
        else pd.DataFrame()
    )
    if team_totals.empty and game_odds is not None:
        full_game = _game_rows(odds_df, home, away)
        if _has_odds_cols(full_game, "market_key"):
            team_totals = full_game[
                full_game["market_key"].astype(str).str.lower().eq("team_totals")
            ]

    market_home_spread: float | None = None
    market_total: float | None = None
    try:
        if spread_sides.get("home", {}).get("line") is not None:
            market_home_spread = float(spread_sides["home"]["line"])
    except (TypeError, ValueError):
        market_home_spread = None
    try:
        over_line = (total_sides.get("over") or {}).get("line")
        if over_line is not None:
            market_total = float(over_line)
        elif total_line is not None:
            market_total = float(total_line)
    except (TypeError, ValueError):
        market_total = None

    tt_price = (total_sides.get("over") or {}).get("price")
    away_tt, home_tt = _best_team_total_quotes(team_totals, home, away)
    if away_tt and home_tt:
        away_tt = _repack_team_total(away_tt, book_id=bid, price=tt_price)
        home_tt = _repack_team_total(home_tt, book_id=bid, price=tt_price)
    if away_tt is None or home_tt is None:
        _, sides = _team_total_sides_for_book(team_totals, home, away, bid)
        if sides:
            away_tt = _pack(sides["away"].get("over"), book_id=bid)
            home_tt = _pack(sides["home"].get("over"), book_id=bid)

    if away_tt is None or home_tt is None:
        slate_away, slate_home = slate_team_total_quotes(home, away, slate)
        if bid == _DK or not away_tt:
            away_tt = away_tt or slate_away
            home_tt = home_tt or slate_home

    if away_tt is None or home_tt is None:
        derived_away, derived_home = _derived_team_total_quotes(
            spread_sides,
            total_sides,
            home_spread_line=market_home_spread if market_home_spread is not None else home_spread_line,
            total_line=market_total if market_total is not None else total_line,
        )
        if derived_away and derived_home:
            derived_away = _repack_team_total(derived_away, book_id=bid, price=tt_price)
            derived_home = _repack_team_total(derived_home, book_id=bid, price=tt_price)
            away_tt = away_tt or derived_away
            home_tt = home_tt or derived_home

    align_spread = market_home_spread
    if align_spread is None and home_spread_line is not None:
        try:
            align_spread = float(home_spread_line)
        except (TypeError, ValueError):
            align_spread = None
    away_tt, home_tt = _align_team_total_quotes(
        away_tt,
        home_tt,
        home_spread=align_spread,
        total=market_total,
    )

    return {
        "away_spread": _pack(spread_sides.get("away"), book_id=bid),
        "home_spread": _pack(spread_sides.get("home"), book_id=bid),
        "over": _pack(total_sides.get("over"), book_id=bid),
        "under": _pack(total_sides.get("under"), book_id=bid),
        "away_team_over": away_tt,
        "home_team_over": home_tt,
    }


def _snapshot_book_quotes(
    home: str,
    away: str,
    book_id: str,
    *,
    kickoff: Any = None,
) -> dict[str, Any]:
    """Closing quotes from archived The Odds API snapshots (pre-kickoff)."""
    from .game_line_history import _lines_from_snapshots_for_book

    bid = normalize_book_id(book_id) or _DK
    raw = _lines_from_snapshots_for_book(home, away, bid, kickoff=kickoff, which="close")
    spread_sides = raw.get("spread_sides") or {}
    total_sides = raw.get("total_sides") or {}
    away_tt = home_tt = None
    return {
        "away_spread": _pack(spread_sides.get("away"), book_id=bid),
        "home_spread": _pack(spread_sides.get("home"), book_id=bid),
        "over": _pack(total_sides.get("over"), book_id=bid),
        "under": _pack(total_sides.get("under"), book_id=bid),
        "away_team_over": away_tt,
        "home_team_over": home_tt,
    }


def _best_row_at_line(
    rows: pd.DataFrame,
    *,
    line: float | None = None,
    line_tol: float = 0.01,
) -> pd.Series | None:
    """Pick the best American price among rows, optionally at a target line."""
    from .sharp_edges import _better_american

    if rows.empty:
        return None
    subset = rows
    if line is not None and "line" in rows.columns:
        try:
            target = float(line)
            ln = pd.to_numeric(rows["line"], errors="coerce")
            at_line = rows.loc[ln.notna() & ((ln - target).abs() <= line_tol)]
            if not at_line.empty:
                subset = at_line
        except (TypeError, ValueError):
            pass
    best: pd.Series | None = None
    for _, row in subset.iterrows():
        if best is None:
            best = row
            continue
        if _better_american(row.get("price"), best.get("price")) == row.get("price"):
            best = row
    return best


def _best_spread_quotes(
    game: pd.DataFrame,
    home: str,
    away: str,
    *,
    home_spread_line: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if game.empty or not _has_odds_cols(game, "market_key"):
        return None, None
    spreads = game[game["market_key"].astype(str).str.lower().eq("spreads")]
    if spreads.empty:
        return None, None
    target = home_spread_line
    if target is None:
        for _, row in spreads.iterrows():
            slot = match_selection_to_side(str(row.get("selection") or ""), home, away)
            if slot == "home" and row.get("line") is not None:
                try:
                    target = float(row.get("line"))
                    break
                except (TypeError, ValueError):
                    pass
    away_best = home_best = None
    for side in ("away", "home"):
        side_rows = []
        for _, row in spreads.iterrows():
            slot = match_selection_to_side(str(row.get("selection") or ""), home, away)
            if slot == side:
                side_rows.append(row)
        if not side_rows:
            continue
        side_line = target if side == "home" else (-target if target is not None else None)
        best = _best_row_at_line(pd.DataFrame(side_rows), line=side_line)
        if best is not None:
            bid = normalize_book_id(str(best.get("book_id") or "")) or str(best.get("book_id") or "")
            packed = _pack({"line": best.get("line"), "price": best.get("price")}, book_id=bid)
            if side == "away":
                away_best = packed
            else:
                home_best = packed
    return away_best, home_best


def _best_total_quotes(
    game: pd.DataFrame,
    *,
    total_line: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if game.empty or not _has_odds_cols(game, "market_key"):
        return None, None
    totals = game[game["market_key"].astype(str).str.lower().eq("totals")]
    if totals.empty:
        return None, None
    target = total_line
    if target is None:
        for _, row in totals.iterrows():
            sel = str(row.get("selection") or "").lower()
            if "over" in sel and row.get("line") is not None:
                try:
                    target = float(row.get("line"))
                    break
                except (TypeError, ValueError):
                    pass
    over_best = under_best = None
    for side in ("over", "under"):
        side_rows = []
        for _, row in totals.iterrows():
            sel = str(row.get("selection") or "").lower()
            if side in sel:
                side_rows.append(row)
        if not side_rows:
            continue
        best = _best_row_at_line(pd.DataFrame(side_rows), line=target)
        if best is not None:
            bid = normalize_book_id(str(best.get("book_id") or "")) or str(best.get("book_id") or "")
            packed = _pack({"line": best.get("line"), "price": best.get("price")}, book_id=bid)
            if side == "over":
                over_best = packed
            else:
                under_best = packed
    return over_best, under_best


def best_book_game_quotes(
    home: str,
    away: str,
    odds_df: pd.DataFrame,
    *,
    slate: pd.DataFrame | None = None,
    home_spread_line: float | None = None,
    total_line: float | None = None,
    game_odds: pd.DataFrame | None = None,
    completed: bool = False,
    kickoff: Any = None,
) -> dict[str, Any]:
    """Best available price across all books for spread, total, and team totals."""
    from .game_line_history import snapshots_available

    game = game_odds if game_odds is not None else _game_rows(odds_df, home, away)
    away_sp, home_sp = _best_spread_quotes(
        game, home, away, home_spread_line=home_spread_line
    )
    over_q, under_q = _best_total_quotes(game, total_line=total_line)

    team_totals = (
        game[game["market_key"].astype(str).str.lower().eq("team_totals")]
        if _has_odds_cols(game, "market_key")
        else pd.DataFrame()
    )
    if team_totals.empty and game_odds is not None:
        full_game = _game_rows(odds_df, home, away)
        if _has_odds_cols(full_game, "market_key"):
            team_totals = full_game[full_game["market_key"].astype(str).str.lower().eq("team_totals")]

    away_tt = home_tt = None
    if not team_totals.empty:
        for team_side, team_name in (("away", away), ("home", home)):
            rows = []
            desc_col = team_totals["description"] if "description" in team_totals.columns else pd.Series([""] * len(team_totals))
            for _, row in team_totals.iterrows():
                sel = str(row.get("selection") or "").lower()
                if "over" not in sel:
                    continue
                desc = str(row.get("description") or row.get("selection") or "")
                if teams_match(desc, team_name) or teams_match(str(row.get("selection") or ""), team_name):
                    rows.append(row)
            best = _best_row_at_line(pd.DataFrame(rows) if rows else pd.DataFrame())
            if best is not None:
                bid = normalize_book_id(str(best.get("book_id") or "")) or str(best.get("book_id") or "")
                packed = _pack({"line": best.get("line"), "price": best.get("price")}, book_id=bid)
                if team_side == "away":
                    away_tt = packed
                else:
                    home_tt = packed

    market_home_spread = None
    try:
        if home_sp and home_sp.get("line") is not None:
            market_home_spread = float(home_sp["line"])
    except (TypeError, ValueError):
        pass
    market_total = None
    try:
        if over_q and over_q.get("line") is not None:
            market_total = float(over_q["line"])
        elif total_line is not None:
            market_total = float(total_line)
    except (TypeError, ValueError):
        pass

    if away_tt is None or home_tt is None:
        derived_away, derived_home = _derived_team_total_quotes(
            {"home": home_sp or {}, "away": away_sp or {}},
            {"over": over_q or {}, "under": under_q or {}},
            home_spread_line=market_home_spread if market_home_spread is not None else home_spread_line,
            total_line=market_total if market_total is not None else total_line,
        )
        away_tt = away_tt or derived_away
        home_tt = home_tt or derived_home

    if away_tt is None or home_tt is None:
        slate_away, slate_home = slate_team_total_quotes(home, away, slate)
        away_tt = away_tt or slate_away
        home_tt = home_tt or slate_home

    away_tt, home_tt = _align_team_total_quotes(
        away_tt,
        home_tt,
        home_spread=market_home_spread if market_home_spread is not None else home_spread_line,
        total=market_total,
    )

    out = {
        "away_spread": away_sp,
        "home_spread": home_sp,
        "over": over_q,
        "under": under_q,
        "away_team_over": away_tt,
        "home_team_over": home_tt,
    }

    if completed and snapshots_available():
        for bid in _CARD_BOOKS:
            hist = _snapshot_book_quotes(home, away, bid, kickoff=kickoff)
            for key, val in out.items():
                if not val or val.get("line") is None:
                    out[key] = hist.get(key.replace("_fd", "")) or val

    return out


def multi_book_game_quotes(
    home: str,
    away: str,
    odds_df: pd.DataFrame,
    *,
    slate: pd.DataFrame | None = None,
    home_spread_line: float | None = None,
    total_line: float | None = None,
    game_odds: pd.DataFrame | None = None,
    completed: bool = False,
    kickoff: Any = None,
) -> dict[str, Any]:
    """DraftKings + FanDuel spread/total/team totals; snapshot fallback for finals."""
    from .game_line_history import snapshots_available

    out: dict[str, Any] = {}
    game = game_odds if game_odds is not None else _game_rows(odds_df, home, away)
    keys = ("away_spread", "home_spread", "over", "under", "away_team_over", "home_team_over")
    use_hist = bool(completed and snapshots_available())

    for bid in _CARD_BOOKS:
        suffix = "" if bid == _DK else "_fd"
        live = _quotes_for_book(
            odds_df,
            home,
            away,
            bid,
            slate=slate,
            home_spread_line=home_spread_line,
            total_line=total_line,
            game_odds=game,
        )
        if use_hist:
            hist = _snapshot_book_quotes(home, away, bid, kickoff=kickoff)
            for key in keys:
                val = live.get(key)
                if not val or val.get("line") is None:
                    live[key] = hist.get(key) or val

        for key in keys:
            val = live.get(key)
            if val and val.get("line") is not None:
                out[f"{key}{suffix}"] = val

    return out


def draftkings_game_quotes(
    home: str,
    away: str,
    odds_df: pd.DataFrame,
    *,
    slate: pd.DataFrame | None = None,
    home_spread_line: float | None = None,
    total_line: float | None = None,
    game_odds: pd.DataFrame | None = None,
    completed: bool = False,
    kickoff: Any = None,
) -> dict[str, Any]:
    """Best price across all books; snapshot fallback for finals."""
    return best_book_game_quotes(
        home,
        away,
        odds_df,
        slate=slate,
        home_spread_line=home_spread_line,
        total_line=total_line,
        game_odds=game_odds,
        completed=completed,
        kickoff=kickoff,
    )

