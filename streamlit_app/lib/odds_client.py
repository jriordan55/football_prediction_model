"""Odds data clients — Onyx public API + The Odds API v4."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from .config import (
    DATA_DIR,
    ODDS_API_V4,
    ONYX_API,
    PUBLIC_DIR,
    audit_export_enabled,
    book_allowed,
    book_label,
    normalize_book_id,
    odds_api_key,
)
from .excel_audit import export_pull
from .prop_pricing import normalize_onyx_player

ODDS_API_BOOKMAKERS = (
    "pinnacle,draftkings,fanduel,betmgm,caesars,betrivers,thescore,fanatics"
)

# Per-event markets (not available on bulk /odds) — Sharp Lines + board extensions.
EXTENDED_SHARP_MARKETS = [
    "team_totals",
    "spreads_h1",
    "totals_h1",
    "spreads_q1",
    "totals_q1",
]

MARKET_LABELS: dict[str, str] = {
    "h2h": "Moneyline",
    "spreads": "Spread",
    "totals": "Total",
    "team_totals": "Team Total",
    "spreads_h1": "1H Spread",
    "spreads_h2": "2H Spread",
    "totals_h1": "1H Total",
    "totals_h2": "2H Total",
    "spreads_q1": "Q1 Spread",
    "spreads_q2": "Q2 Spread",
    "spreads_q3": "Q3 Spread",
    "spreads_q4": "Q4 Spread",
    "totals_q1": "Q1 Total",
    "totals_q2": "Q2 Total",
    "totals_q3": "Q3 Total",
    "totals_q4": "Q4 Total",
    "team_totals_q1": "Q1 Team Total",
    "team_totals_q2": "Q2 Team Total",
    "team_total_yds": "Team Total Yards",
    "alternate_team_total_yds": "Alt Team Total Yards",
    "team_total_rush_yds": "Team Rush Yards",
    "alternate_team_total_rush_yds": "Alt Team Rush Yards",
    "team_total_pass_yds": "Team Pass Yards",
    "alternate_team_total_pass_yds": "Alt Team Pass Yards",
    "team_total_rush_yds_h1": "Team Rush Yards 1H",
    "alternate_team_total_rush_yds_h1": "Alt Team Rush Yards 1H",
    "team_total_pass_yds_h1": "Team Pass Yards 1H",
    "alternate_team_total_pass_yds_h1": "Alt Team Pass Yards 1H",
    "team_total_yds_h1": "Team Total Yards 1H",
    "alternate_team_total_yds_h1": "Alt Team Total Yards 1H",
    "team_total_rush_yds_q1": "Team Rush Yards Q1",
    "alternate_team_total_rush_yds_q1": "Alt Team Rush Yards Q1",
    "team_total_rec_yds": "Team Receiving Yards",
    "alternate_team_total_rec_yds": "Alt Team Receiving Yards",
    "team_total_receiving_yds": "Team Receiving Yards",
    "alternate_team_total_receiving_yds": "Alt Team Receiving Yards",
    "team_total_rec_yds_h1": "Team Receiving Yards 1H",
    "alternate_team_total_rec_yds_h1": "Alt Team Receiving Yards 1H",
    "team_total_receiving_yds_h1": "Team Receiving Yards 1H",
    "alternate_team_total_receiving_yds_h1": "Alt Team Receiving Yards 1H",
}


def market_label(key: str) -> str:
    mk = str(key or "").lower()
    return MARKET_LABELS.get(mk, mk.replace("_", " ").title())


def _quota_from_response(r: requests.Response) -> dict[str, Any]:
    return {
        "remaining": r.headers.get("x-requests-remaining"),
        "used": r.headers.get("x-requests-used"),
        "last_cost": r.headers.get("x-requests-last"),
    }


def _extended_event_limit() -> int:
    raw = os.environ.get("THE_ODDS_API_EXTENDED_EVENT_LIMIT", "80")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 80


def _market_batch_size() -> int:
    raw = os.environ.get("THE_ODDS_API_MARKET_BATCH", "6")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 6

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json", "User-Agent": "cfb-streamlit/1.0"})


def _get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 45) -> Any:
    r = SESSION.get(url, params=params, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _active_sport_key() -> str:
    from .sport_context import sport_key

    return sport_key()


def _active_onyx_league() -> str:
    from .sport_context import onyx_league

    return onyx_league()


ONYX_SNAPSHOT_PATH = DATA_DIR / "onyx_cfb_snapshot.json"


def _player_from_snapshot_quote(q: dict[str, Any]) -> str:
    if not q.get("playerId"):
        return ""
    target = str(q.get("target") or q.get("selection") or "")
    target = re.sub(r"\s+(over|under)\s+[\d.]+$", "", target, flags=re.I).strip()
    slug, _, rest = target.partition(" ")
    if rest and "_" in slug and slug.lower() == slug:
        target = rest
    return normalize_onyx_player(target)


def _snapshot_quote_rows(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    league = _active_onyx_league()
    for q in quotes:
        if league and str(q.get("league") or "") not in ("", league):
            continue
        event = str(q.get("event") or "")
        parts = event.split(" @ ", 1)
        away = parts[0].strip() if parts else ""
        home = parts[1].strip() if len(parts) > 1 else ""
        rows.append(
            {
                "event": event,
                "event_id": q.get("gameId"),
                "home": home,
                "away": away,
                "commence_time": q.get("startsAt") or "",
                "market_key": q.get("marketKey") or "",
                "market": q.get("market") or "",
                "selection": q.get("selection") or "",
                "player": _player_from_snapshot_quote(q),
                "side": q.get("side") or "",
                "line": q.get("line"),
                "price": q.get("price") or q.get("american"),
                "is_main": q.get("isMain") is True,
                "book_id": "onyx_odds",
                "book": "Onyx",
                "source": "onyx_snapshot",
                "updated_at": q.get("updatedAt") or "",
            }
        )
    return rows


def _load_onyx_snapshot_df() -> pd.DataFrame:
    if not ONYX_SNAPSHOT_PATH.exists():
        return pd.DataFrame()
    try:
        board = json.loads(ONYX_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return pd.DataFrame()
    quotes = board.get("quotes") or []
    if not quotes:
        return pd.DataFrame()
    return pd.DataFrame(_snapshot_quote_rows(quotes))


def fetch_onyx_raw() -> dict[str, Any]:
    """Fetch all Onyx public odds — filter league client-side (query param breaks API)."""
    r = SESSION.get(
        ONYX_API,
        headers={
            "Accept": "application/json",
            "User-Agent": "mlb-pbp-model/onyx-scraper",
            "Origin": "https://app.onyxodds.com",
            "Referer": "https://app.onyxodds.com/",
        },
        timeout=45,
    )
    r.raise_for_status()
    payload = r.json()
    league = _active_onyx_league()
    if isinstance(payload, dict):
        games = [g for g in payload.values() if isinstance(g, dict)]
    else:
        games = [g for g in (payload or []) if isinstance(g, dict)]
    if league:
        games = [g for g in games if str(g.get("league") or "") == league]
    return games


def _onyx_player_name(sel: dict[str, Any]) -> str:
    if not sel.get("playerId"):
        return ""
    raw = sel.get("selection") or sel.get("playerName") or ""
    if not raw:
        raw = str(sel.get("name") or "")
        raw = re.sub(r"\s+(over|under)\s+[\d.]+$", "", raw, flags=re.I).strip()
    return normalize_onyx_player(raw)


def _is_player_prop_market(market_key: str) -> bool:
    mk = str(market_key or "").lower()
    return mk.startswith("player_") or "touchdown" in mk or "reception" in mk


def flatten_onyx_quotes(raw: dict | list) -> list[dict]:
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    games = raw if isinstance(raw, list) else list((raw or {}).values())
    for game in games:
        if not isinstance(game, dict):
            continue
        away = game.get("awayTeam") or game.get("away_team") or ""
        home = game.get("homeTeam") or game.get("home_team") or ""
        event = f"{away} @ {home}"
        commence = game.get("startDate") or game.get("commence_time") or ""
        markets = game.get("markets") or {}
        for market_key, selections in markets.items():
            if not isinstance(selections, dict):
                continue
            is_prop = _is_player_prop_market(market_key)
            for sel_key, sel in selections.items():
                if not isinstance(sel, dict):
                    continue
                market_label = str(sel.get("marketName") or market_key.replace("_", " ").title())
                rows.append(
                    {
                        "event": event,
                        "event_id": game.get("ojId") or game.get("id"),
                        "home": home,
                        "away": away,
                        "commence_time": commence,
                        "market_key": market_key,
                        "market": market_label,
                        "selection": sel.get("normalizedSelection") or sel_key,
                        "player": _onyx_player_name(sel),
                        "side": sel.get("selectionLine") or "",
                        "line": sel.get("betPoints") or sel.get("selectionPoints"),
                        "price": sel.get("lastPrice") or sel.get("price"),
                        "is_main": sel.get("isMain") is True,
                        "book_id": "onyx_odds",
                        "book": "Onyx",
                        "source": "onyx",
                        "updated_at": now,
                    }
                )
    return rows


def fetch_onyx_df(tab: str | None = None) -> pd.DataFrame:
    try:
        raw = fetch_onyx_raw()
        df = pd.DataFrame(flatten_onyx_quotes(raw))
        origin = "live"
    except Exception:
        df = _load_onyx_snapshot_df()
        origin = "snapshot"
    if df.empty:
        return df
    export_pull("onyx_public_odds", df, tab=tab, origin=origin)
    from .csv_log import log_odds_quotes

    log_odds_quotes(df, source="onyx", tab=tab, dedupe=True)
    return df


def load_cached_slate(tab: str | None = None) -> dict | None:
    path = PUBLIC_DIR / "onyx-labs-slate.json"
    if not path.exists():
        path = PUBLIC_DIR / "football-labs-slate.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        slate = json.load(f)
    if audit_export_enabled():
        export_pull("onyx_slate_cache", slate, tab=tab, origin="cache", extra={"file": path.name})
    return slate


def slate_to_df(slate: dict | None) -> pd.DataFrame:
    if not slate or not slate.get("rows"):
        return pd.DataFrame()
    return pd.DataFrame(slate["rows"])


def fetch_odds_api_events(refresh: bool = False, tab: str | None = None) -> tuple[list[dict], dict]:
    key = odds_api_key()
    quota: dict[str, Any] = {}
    if not key:
        return [], {"error": "THE_ODDS_API_KEY missing"}
    url = f"{ODDS_API_V4}/sports/{_active_sport_key()}/events"
    r = SESSION.get(url, params={"apiKey": key, "dateFormat": "iso"}, timeout=45)
    quota["remaining"] = r.headers.get("x-requests-remaining")
    quota["used"] = r.headers.get("x-requests-used")
    if not r.ok:
        return [], {**quota, "error": r.text[:200]}
    events = r.json()
    export_pull("theoddsapi_events", events, tab=tab, origin="live", extra=quota)
    return events, quota


def fetch_odds_api_sport_odds(
    markets: str = "h2h,spreads,totals",
    bookmakers: str | None = None,
    tab: str | None = None,
) -> tuple[list[dict], dict]:
    key = odds_api_key()
    if not key:
        return [], {"error": "THE_ODDS_API_KEY missing"}
    books = bookmakers or ODDS_API_BOOKMAKERS
    url = f"{ODDS_API_V4}/sports/{_active_sport_key()}/odds"
    params = {
        "apiKey": key,
        "regions": "us",
        "markets": markets,
        "bookmakers": books,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    r = SESSION.get(url, params=params, timeout=60)
    quota = _quota_from_response(r)
    if not r.ok:
        return [], {**quota, "error": r.text[:200]}
    events = r.json()
    flat = flatten_odds_api_events(events)
    export_pull("theoddsapi_sport_odds", flat, tab=tab, origin="live", extra=quota)
    return events, quota


def flatten_odds_api_events(events: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for ev in events or []:
        event = f"{ev.get('away_team')} @ {ev.get('home_team')}"
        commence = ev.get("commence_time")
        for book in ev.get("bookmakers") or []:
            book_id = normalize_book_id(book.get("key"))
            if not book_allowed(book_id):
                continue
            for market in book.get("markets") or []:
                mk = market.get("key")
                for outcome in market.get("outcomes") or []:
                    desc = outcome.get("description")
                    rows.append(
                        {
                            "event": event,
                            "event_id": ev.get("id"),
                            "home": ev.get("home_team"),
                            "away": ev.get("away_team"),
                            "commence_time": commence,
                            "market_key": mk,
                            "market": market_label(str(mk or "")),
                            "selection": outcome.get("name"),
                            "description": desc,
                            "side": outcome.get("name", ""),
                            "line": outcome.get("point"),
                            "price": outcome.get("price"),
                            "book_id": book_id,
                            "book": book_label(book_id),
                            "source": "theoddsapi",
                            "updated_at": book.get("last_update") or datetime.now(timezone.utc).isoformat(),
                        }
                    )
    return pd.DataFrame(rows)


def fetch_odds_api_event_odds(
    event_id: str,
    markets: list[str] | str,
    *,
    bookmakers: str | None = None,
    tab: str | None = None,
    sport_key: str | None = None,
) -> tuple[dict | None, dict[str, Any]]:
    """Single-event odds pull — required for team totals and period markets."""
    key = odds_api_key()
    if not key or not event_id:
        return None, {"error": "THE_ODDS_API_KEY or event_id missing"}

    if isinstance(markets, str):
        market_list = [m.strip() for m in markets.split(",") if m.strip()]
    else:
        market_list = [str(m).strip() for m in markets if str(m).strip()]
    if not market_list:
        return None, {"error": "no markets requested"}

    books = bookmakers or ODDS_API_BOOKMAKERS
    sport = sport_key or _active_sport_key()
    url = f"{ODDS_API_V4}/sports/{sport}/events/{event_id}/odds"
    params = {
        "apiKey": key,
        "regions": "us",
        "bookmakers": books,
        "markets": ",".join(market_list),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    r = SESSION.get(url, params=params, timeout=60)
    quota = _quota_from_response(r)
    if not r.ok:
        return None, {**quota, "error": r.text[:200]}
    event = r.json()
    if tab:
        flat = flatten_odds_api_events([event] if isinstance(event, dict) else [])
        export_pull(
            "theoddsapi_event_odds",
            flat,
            tab=tab,
            origin="live",
            extra={**quota, "event_id": event_id, "markets": market_list},
        )
    return event, quota


def fetch_odds_api_event_markets(
    event_id: str,
    *,
    bookmakers: str | None = None,
    tab: str | None = None,
    sport_key: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """List available market keys for one event (discovery — 1 credit)."""
    key = odds_api_key()
    if not key or not event_id:
        return [], {"error": "THE_ODDS_API_KEY or event_id missing"}
    prefer = normalize_book_id((bookmakers or "draftkings").split(",")[0])
    sport = sport_key or _active_sport_key()
    url = f"{ODDS_API_V4}/sports/{sport}/events/{event_id}/markets"
    params = {"apiKey": key, "regions": "us,eu", "dateFormat": "iso"}
    r = SESSION.get(url, params=params, timeout=45)
    quota = _quota_from_response(r)
    if not r.ok:
        return [], {**quota, "error": r.text[:200]}
    payload = r.json()
    keys: list[str] = []
    books = payload.get("bookmakers") or []
    dk_books = [b for b in books if normalize_book_id(b.get("key")) == prefer]
    scan = dk_books if dk_books else books[:1]
    for book in scan:
        for market in book.get("markets") or []:
            mk = str(market.get("key") or "").strip()
            if mk and mk not in keys:
                keys.append(mk)
    if tab:
        export_pull(
            "theoddsapi_event_markets",
            {"event_id": event_id, "market_keys": keys},
            tab=tab,
            origin="live",
            extra=quota,
        )
    return keys, quota


def fetch_extended_sharp_odds(
    events: list[dict],
    *,
    event_limit: int | None = None,
    tab: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Pull team totals + 1H/Q1 spread/total for each event (per-event endpoint).
    Credits ≈ events × ceil(len(EXTENDED_SHARP_MARKETS) / batch_size).
    """
    limit = _extended_event_limit() if event_limit is None else max(0, int(event_limit))
    use_events = list(events or [])
    if limit > 0:
        use_events = use_events[:limit]

    batch = _market_batch_size()
    quota: dict[str, Any] = {"extended_api_calls": 0, "extended_events": len(use_events)}
    frames: list[pd.DataFrame] = []
    errors = 0

    for ev in use_events:
        event_id = ev.get("id")
        if not event_id:
            continue
        for i in range(0, len(EXTENDED_SHARP_MARKETS), batch):
            slice_mk = EXTENDED_SHARP_MARKETS[i : i + batch]
            event, q = fetch_odds_api_event_odds(event_id, slice_mk, tab=tab)
            quota["extended_api_calls"] = int(quota.get("extended_api_calls") or 0) + 1
            for k, v in q.items():
                if k not in ("error",):
                    quota[k] = v
            if not event or q.get("error"):
                errors += 1
                continue
            df = flatten_odds_api_events([event] if isinstance(event, dict) else [])
            if not df.empty:
                frames.append(df)

    quota["extended_errors"] = errors
    if not frames:
        return pd.DataFrame(), quota
    return pd.concat(frames, ignore_index=True), quota


def game_lines_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    keys = {"h2h", "spreads", "totals", "moneyline", "point_spread", "total_points", "spread"}
    mask = df["market_key"].astype(str).str.lower().isin(keys) | df["market"].astype(str).str.lower().isin(
        {"moneyline", "spread", "total points", "total"}
    )
    return df.loc[mask].copy()


def props_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    from .prop_pricing import is_combo_prop_market

    mk = df["market_key"].astype(str).str.lower()
    mask = mk.str.contains("player|touchdown|reception|passing|rushing|receiving", case=False, na=False)
    mask &= df["player"].notna() & (df["player"].astype(str).str.len() > 1)
    mask &= ~df.apply(lambda r: is_combo_prop_market(r.to_dict()), axis=1)
    return df.loc[mask].copy()
