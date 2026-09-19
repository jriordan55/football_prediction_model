"""4C Odds (4codds.com) — live CFB/NFL game lines for Streamlit.

Public board + market endpoints used by https://4codds.com/football/ncaaf and /nfl
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from .config import book_allowed, book_label, load_env, normalize_book_id
from .odds_client import market_label
from .sport_context import SPORT_CFB, SPORT_NFL
from .team_registry import teams_match

FOURC_SITE = "https://4codds.com"
FOURC_API = f"{FOURC_SITE}/api/v2"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
)


def fourc_league(sport: str | None = None) -> tuple[str, str]:
    """Return (board_sport, league_code) for 4C Odds API paths."""
    sid = str(sport or SPORT_CFB).lower()
    if sid == SPORT_NFL:
        return "football", "NFL"
    return "football", "NCAAF"


def fourc_referer(sport: str | None = None) -> str:
    _, league = fourc_league(sport)
    return f"{FOURC_SITE}/football/{league.lower()}"
# 4C Odds book codes → internal book_id (retail + Pinnacle).
_FOURC_BOOK_MAP: dict[str, str] = {
    "DRAFTKINGS": "draftkings",
    "FANDUEL": "fanduel",
    "PINNACLE": "pinnacle",
    "BETMGM": "betmgm",
    "CAESARS": "caesars",
    "WILLIAMHILL": "caesars",
    "BETRIVERS": "betrivers",
    "FANATICS": "fanatics",
    "THESCORE": "thescore",
    "ESPN": "thescore",
    "ESPNBET": "thescore",
    "NOVIG": "novig",
    "KALSHI": "kalshi",
    "POLYMARKET": "polymarket",
    "POLYMARKETUS": "polymarketus",
    "4C": "4c",
    "VERTEX": "vertex",
    "3ET": "3et",
    "BETFAIR": "betfair",
    "MATCHBOOK": "matchbook",
    "LOWVIG": "lowvig",
    "HARDROCK": "hardrock",
    "AMAPOLA": "amapola",
    "APEX": "apex",
    "PLAYERSFANTASY": "playersfantasy",
    "BETONLINE": "betonline",
    "BOVADA": "bovada",
    "PREDICTFUN": "predictfun",
    "PROPHETX": "prophetx",
}


def fourc_odds_enabled() -> bool:
    load_env()
    raw = os.getenv("FOURC_ODDS_ENABLED", "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def fourc_refresh_sec() -> int:
    load_env()
    try:
        return max(15, int(os.getenv("FOURC_ODDS_REFRESH_SEC", "30")))
    except (TypeError, ValueError):
        return 60


def fourc_market_limit() -> int:
    load_env()
    try:
        return max(5, int(os.getenv("FOURC_ODDS_MARKET_FETCH_LIMIT", "40")))
    except (TypeError, ValueError):
        return 40


def _get(path: str, *, sport: str | None = None, timeout: int = 45) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{FOURC_API}/{path.lstrip('/')}"
    headers = {"Referer": fourc_referer(sport)}
    r = SESSION.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


def fetch_meta(*, sport: str | None = None) -> dict[str, Any]:
    return _get("meta", sport=sport)


def fetch_board(*, sport: str | None = None, league: str | None = None) -> dict[str, Any]:
    board_sport, board_league = fourc_league(sport)
    if league:
        board_league = league
    return _get(f"board/{board_sport}/{board_league}", sport=sport or board_league)


def fetch_game_market(game_id: str, market: str, *, sport: str | None = None) -> dict[str, Any]:
    return _get(f"game/{game_id}/market/{market}", sport=sport)


def _fourc_book_id(code: str) -> str | None:
    raw = str(code or "").strip().upper()
    if not raw:
        return None
    bid = _FOURC_BOOK_MAP.get(raw) or normalize_book_id(raw)
    return bid or raw.lower()


def _ts_iso(ms: Any) -> str:
    try:
        ts = int(float(ms)) / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).isoformat()


def _line_key(line: float | None) -> str:
    if line is None:
        return "null"
    f = float(line)
    if f == int(f):
        return str(int(f))
    return str(f)


def _game_names(game: dict[str, Any]) -> tuple[str, str, str]:
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    return home, away, f"{away} @ {home}"


def _quote_row(
    game: dict[str, Any],
    *,
    market_key: str,
    selection: str,
    line: float | None,
    book_id: str,
    price: Any,
    updated_ms: Any,
) -> dict[str, Any]:
    home, away, event = _game_names(game)
    try:
        price_i = int(float(price)) if price is not None else None
    except (TypeError, ValueError):
        price_i = None
    return {
        "event": event,
        "event_id": str(game.get("id") or ""),
        "home": home,
        "away": away,
        "commence_time": game.get("start"),
        "market_key": market_key,
        "market": market_label(market_key),
        "selection": selection,
        "description": None,
        "side": selection,
        "line": line,
        "price": price_i,
        "book_id": book_id,
        "book": book_label(book_id),
        "source": "4codds",
        "updated_at": _ts_iso(updated_ms),
    }


def _book_quotes_from_side(
    game: dict[str, Any],
    *,
    market_key: str,
    side: str,
    selection: str,
    line: float | None,
    entries: list[list[Any]] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries or []:
        if not entry or len(entry) < 2:
            continue
        book_id = _fourc_book_id(str(entry[0]))
        if not book_id:
            continue
        price = entry[1]
        if price is None:
            continue
        rows.append(
            _quote_row(
                game,
                market_key=market_key,
                selection=selection,
                line=line,
                book_id=book_id,
                price=price,
                updated_ms=entry[4] if len(entry) > 4 else None,
            )
        )
    return rows


def _flatten_board_cells(game: dict[str, Any]) -> list[dict[str, Any]]:
    """Best book per cell from the board payload (single API call)."""
    home, away, _ = _game_names(game)
    main = game.get("main") or {}
    rows: list[dict[str, Any]] = []
    for cell in game.get("cells") or []:
        if not cell or len(cell) < 5:
            continue
        mk_raw = str(cell[0] or "")
        side = str(cell[1] or "")
        line = cell[2]
        book_entry = cell[4]
        if not isinstance(book_entry, list) or len(book_entry) < 2:
            continue

        if mk_raw == "sp":
            market_key = "spreads"
            try:
                ln = float(line) if line is not None else float(main.get("sp"))
            except (TypeError, ValueError):
                continue
            if side == "home":
                selection, spread_line = home, ln
            else:
                selection, spread_line = away, -ln
        elif mk_raw == "tot":
            market_key = "totals"
            try:
                ln = float(line) if line is not None else float(main.get("tot"))
            except (TypeError, ValueError):
                continue
            selection = "Over" if side == "over" else "Under"
            spread_line = ln
        elif mk_raw == "ml":
            market_key = "h2h"
            selection = home if side == "home" else away
            spread_line = None
        else:
            continue

        book_id = _fourc_book_id(str(book_entry[0]))
        if not book_id:
            continue
        rows.append(
            _quote_row(
                game,
                market_key=market_key,
                selection=selection,
                line=spread_line,
                book_id=book_id,
                price=book_entry[1],
                updated_ms=book_entry[4] if len(book_entry) > 4 else None,
            )
        )
    return rows


def _flatten_market_detail(
    game: dict[str, Any],
    market: str,
    payload: dict[str, Any],
    *,
    main_line: float | None,
) -> list[dict[str, Any]]:
    home, away, _ = _game_names(game)
    lines = payload.get("lines") or {}
    market_main = payload.get("main")
    if main_line is None and market_main is not None:
        try:
            main_line = float(market_main)
        except (TypeError, ValueError):
            main_line = None
    key = _line_key(main_line)
    side_map = lines.get(key)
    if side_map is None and main_line is not None:
        side_map = lines.get(str(float(main_line)))
    if side_map is None and main_line is not None:
        side_map = lines.get(str(int(main_line)) if float(main_line) == int(float(main_line)) else None)
    if not isinstance(side_map, dict):
        return []

    rows: list[dict[str, Any]] = []
    if market == "sp":
        try:
            ln = float(main_line)
        except (TypeError, ValueError):
            return rows
        rows.extend(
            _book_quotes_from_side(
                game,
                market_key="spreads",
                side="home",
                selection=home,
                line=ln,
                entries=side_map.get("home"),
            )
        )
        rows.extend(
            _book_quotes_from_side(
                game,
                market_key="spreads",
                side="away",
                selection=away,
                line=-ln,
                entries=side_map.get("away"),
            )
        )
    elif market == "tot":
        try:
            ln = float(main_line)
        except (TypeError, ValueError):
            return rows
        rows.extend(
            _book_quotes_from_side(
                game,
                market_key="totals",
                side="over",
                selection="Over",
                line=ln,
                entries=side_map.get("over"),
            )
        )
        rows.extend(
            _book_quotes_from_side(
                game,
                market_key="totals",
                side="under",
                selection="Under",
                line=ln,
                entries=side_map.get("under"),
            )
        )
    elif market == "ml":
        rows.extend(
            _book_quotes_from_side(
                game,
                market_key="h2h",
                side="home",
                selection=home,
                line=None,
                entries=side_map.get("home"),
            )
        )
        rows.extend(
            _book_quotes_from_side(
                game,
                market_key="h2h",
                side="away",
                selection=away,
                line=None,
                entries=side_map.get("away"),
            )
        )
    return rows


def _matches_week_game(game: dict[str, Any], matchups: list[dict[str, Any]]) -> bool:
    home, away, _ = _game_names(game)
    for m in matchups:
        mh = str(m.get("home") or "")
        ma = str(m.get("away") or "")
        if teams_match(home, mh) and teams_match(away, ma):
            return True
    return False


def fetch_fourc_game_lines(
    sport: str | None = None,
    matchups: list[dict[str, Any]] | None = None,
    *,
    deep: bool = True,
    tab: str | None = None,
) -> pd.DataFrame:
    """
    Pull spreads, totals, and moneylines from 4C Odds for CFB or NFL.

    - Board call always (all games).
    - When deep=True, fetches /market/sp|tot|ml for matched games (multi-book main lines).
    """
    _ = tab
    sid = str(sport or SPORT_CFB).lower()
    board = fetch_board(sport=sid)
    games: list[dict[str, Any]] = list(board.get("games") or [])
    if matchups:
        games = [g for g in games if _matches_week_game(g, matchups)]

    rows: list[dict[str, Any]] = []
    if not deep or not games:
        for g in board.get("games") or []:
            if matchups and not _matches_week_game(g, matchups):
                continue
            rows.extend(_flatten_board_cells(g))
        return _dedupe(pd.DataFrame(rows))

    limit = fourc_market_limit()
    for g in games[:limit]:
        main = g.get("main") or {}
        gid = str(g.get("id") or "")
        if not gid:
            continue
        try:
            if main.get("sp") is not None:
                sp_payload = fetch_game_market(gid, "sp", sport=sid)
                rows.extend(_flatten_market_detail(g, "sp", sp_payload, main_line=float(main["sp"])))
            if main.get("tot") is not None:
                tot_payload = fetch_game_market(gid, "tot", sport=sid)
                rows.extend(_flatten_market_detail(g, "tot", tot_payload, main_line=float(main["tot"])))
            ml_payload = fetch_game_market(gid, "ml", sport=sid)
            rows.extend(_flatten_market_detail(g, "ml", ml_payload, main_line=None))
        except (requests.RequestException, OSError, ValueError):
            rows.extend(_flatten_board_cells(g))

    if not rows:
        for g in games:
            rows.extend(_flatten_board_cells(g))

    return _dedupe(pd.DataFrame(rows))


def fetch_ncaaf_game_lines(
    matchups: list[dict[str, Any]] | None = None,
    *,
    deep: bool = True,
    tab: str | None = None,
) -> pd.DataFrame:
    return fetch_fourc_game_lines(SPORT_CFB, matchups, deep=deep, tab=tab)


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cols = [c for c in ["event_id", "market_key", "selection", "book_id", "line", "price"] if c in df.columns]
    if cols:
        df = df.drop_duplicates(subset=cols, keep="last")
    return df.reset_index(drop=True)


# Never surface the aggregator itself — retail / sharp books only in UI.
_UI_SKIP_BOOKS = frozenset({"4c", "4codds"})

_FOURC_STAT_TO_PROP: dict[str, str] = {
    "TOTAL RUSHING YARDS": "rush_yds",
    "TOTAL RECEIVING YARDS": "rec_yds",
    "TOTAL RECEPTIONS": "receptions",
    "TOTAL PASSING YARDS": "pass_yds",
    "TOTAL TOUCHDOWN PASSES": "pass_tds",
    "TOTAL TOUCHDOWNS": "tds",
}


def fourc_stat_to_prop_key(stat: str) -> str | None:
    return _FOURC_STAT_TO_PROP.get(str(stat or "").strip().upper())


def _price_int(price: Any) -> int | None:
    try:
        return int(float(price))
    except (TypeError, ValueError):
        return None


def _better_american(a: int, b: int) -> int:
    if a >= 0 and b >= 0:
        return a if a > b else b
    if a < 0 and b < 0:
        return a if a > b else b
    return a if a > b else b


def _liquidity_from_entry(entry: list[Any]) -> float | None:
    if not entry or len(entry) < 4:
        return None
    try:
        val = float(entry[3])
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def _quote_from_book_entry(entry: list[Any]) -> dict[str, Any] | None:
    if not entry or len(entry) < 2:
        return None
    book_id = _fourc_book_id(str(entry[0]))
    if not book_id or book_id in _UI_SKIP_BOOKS:
        return None
    price = _price_int(entry[1])
    if price is None or price == 0:
        return None
    row: dict[str, Any] = {"book_id": book_id, "price": price, "book": book_label(book_id)}
    liq = _liquidity_from_entry(entry)
    if liq is not None:
        row["liquidity"] = liq
    return row


def _best_retail_entry(
    entries: list[list[Any]] | None,
    *,
    allowed_books: frozenset[str] | set[str] | None = None,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for entry in entries or []:
        row = _quote_from_book_entry(entry)
        if not row:
            continue
        book_id = str(row.get("book_id") or "")
        if allowed_books and book_id not in allowed_books:
            continue
        price = int(row["price"])
        if best is None:
            best = row
            continue
        if _better_american(price, int(best["price"])) == price:
            best = row
    return best


def _quote_from_cell(
    cell: list[Any],
    *,
    allowed_books: frozenset[str] | set[str] | None = None,
) -> dict[str, Any] | None:
    if not cell or len(cell) < 5:
        return None
    entries: list[list[Any]] = []
    book_entry = cell[4]
    if isinstance(book_entry, list):
        entries.append(book_entry)
    extras = cell[5] if len(cell) > 5 else None
    if isinstance(extras, list):
        for item in extras:
            if isinstance(item, list) and len(item) >= 2:
                entries.append(item)
    best = _best_retail_entry(entries, allowed_books=allowed_books)
    if not best:
        return None
    try:
        line = float(cell[2]) if cell[2] is not None else None
    except (TypeError, ValueError):
        line = None
    best["line"] = line
    best["side"] = str(cell[1] or "")
    return best


def parse_board_game_quotes(game: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Best retail quote per main game market cell (spread / total / ML)."""
    home, away, _ = _game_names(game)
    main = game.get("main") or {}
    quotes: dict[str, dict[str, Any]] = {}

    for cell in game.get("cells") or []:
        if not cell or len(cell) < 5:
            continue
        mk = str(cell[0] or "")
        side = str(cell[1] or "")
        q = _quote_from_cell(cell)
        if not q:
            continue

        if mk == "sp":
            try:
                home_sp = float(main.get("sp"))
            except (TypeError, ValueError):
                try:
                    home_sp = float(q.get("line") if q.get("line") is not None else 0)
                except (TypeError, ValueError):
                    continue
            if side == "home":
                key, sel, spread_line = "spread_home", home, home_sp
            elif side == "away":
                key, sel, spread_line = "spread_away", away, -home_sp
            else:
                continue
            q["line"] = spread_line
            q["selection"] = sel
            q["market"] = "Spread"
        elif mk == "tot":
            try:
                ln = float(q.get("line") if q.get("line") is not None else main.get("tot"))
            except (TypeError, ValueError):
                continue
            key = "total_over" if side == "over" else "total_under" if side == "under" else ""
            if not key:
                continue
            q["line"] = ln
            q["selection"] = "Over" if side == "over" else "Under"
            q["market"] = "Total"
        elif mk == "ml":
            key = "ml_home" if side == "home" else "ml_away" if side == "away" else ""
            if not key:
                continue
            q["line"] = None
            q["selection"] = home if side == "home" else away
            q["market"] = "ML"
        else:
            continue

        existing = quotes.get(key)
        if not existing:
            quotes[key] = q
            continue
        try:
            if _better_american(int(q["price"]), int(existing["price"])) == int(q["price"]):
                quotes[key] = q
        except (TypeError, ValueError):
            pass

    return quotes


def _prop_side_quotes_from_detail(
    game_id: str,
    prop_id: str,
    line: float,
    *,
    sport: str,
    allowed_books: frozenset[str] | set[str] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        payload = _get(f"game/{game_id}/market/tot?prop={prop_id}", sport=sport)
    except (requests.RequestException, OSError, ValueError):
        return None, None
    lines = payload.get("lines") or {}
    side_map = lines.get(_line_key(line))
    if side_map is None:
        side_map = lines.get(str(float(line)))
    if not isinstance(side_map, dict):
        return None, None
    over = _best_retail_entry(side_map.get("over"), allowed_books=allowed_books)
    under = _best_retail_entry(side_map.get("under"), allowed_books=allowed_books)
    return over, under


def fetch_game_player_props(
    game_id: str,
    *,
    sport: str | None = None,
    home: str | None = None,
    away: str | None = None,
    event: str | None = None,
    allowed_books: frozenset[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Player props for one game — retail over/under at the main line only."""
    sid = str(sport or SPORT_CFB).lower()
    payload = _get(f"game/{game_id}/props", sport=sid)
    home_s, away_s, event_s = str(home or ""), str(away or ""), str(event or "")
    if not home_s or not away_s:
        board = fetch_board(sport=sid)
        for g in board.get("games") or []:
            if str(g.get("id") or "") == str(game_id):
                home_s, away_s, event_s = _game_names(g)
                break

    rows: list[dict[str, Any]] = []
    for prop in payload.get("props") or []:
        if not isinstance(prop, dict):
            continue
        player = str(prop.get("player") or "").strip()
        stat = str(prop.get("stat") or "").strip()
        prop_key = fourc_stat_to_prop_key(stat)
        if not player or not prop_key:
            continue
        main = prop.get("main") or {}
        try:
            line = float(main.get("tot"))
        except (TypeError, ValueError):
            continue

        over_q: dict[str, Any] | None = None
        under_q: dict[str, Any] | None = None
        for cell in prop.get("cells") or []:
            if not cell or str(cell[0] or "") != "tot":
                continue
            q = _quote_from_cell(cell, allowed_books=allowed_books)
            if not q:
                continue
            if str(q.get("side") or "").lower() == "over":
                over_q = q
            elif str(q.get("side") or "").lower() == "under":
                under_q = q

        if not over_q and not under_q:
            over_q, under_q = _prop_side_quotes_from_detail(
                str(game_id),
                str(prop.get("id") or ""),
                line,
                sport=sid,
                allowed_books=allowed_books,
            )
        if not over_q and not under_q:
            continue

        rows.append(
            {
                "prop_id": str(prop.get("id") or ""),
                "player": player.title(),
                "stat": stat,
                "prop_key": prop_key,
                "line": line,
                "home": home_s,
                "away": away_s,
                "event": event_s,
                "over": over_q,
                "under": under_q,
            }
        )
    return rows


def find_board_game_id(
    sport: str,
    home: str,
    away: str,
) -> str | None:
    board = fetch_board(sport=sport)
    for g in board.get("games") or []:
        gh, ga, _ = _game_names(g)
        if teams_match(gh, home) and teams_match(ga, away):
            return str(g.get("id") or "") or None
    return None
