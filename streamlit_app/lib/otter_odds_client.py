"""Otter Odds v2 API — CFB/NFL game lines (https://docs.otterodds.com)."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

from .config import book_label, load_env, normalize_book_id, otter_odds_api_key
from .odds_client import market_label
from .sport_context import SPORT_CFB, SPORT_NFL
from .team_registry import teams_match

OTTER_API = "https://api.otterodds.com"
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


def otter_odds_enabled() -> bool:
    load_env()
    raw = os.getenv("OTTER_ODDS_ENABLED", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return bool(otter_odds_api_key())


def otter_auth_mode() -> str:
    """How Otter is authenticated — for UI labels only."""
    load_env()
    if (os.getenv("OTTER_ODDS_API_KEY") or os.getenv("OTTER_API_KEY") or "").strip():
        return "api_key"
    if (os.getenv("OTTER_ODDS_SESSION_TOKEN") or os.getenv("OTTER_SESSION_TOKEN") or "").strip():
        return "session"
    return "none"


def otter_refresh_sec() -> int:
    load_env()
    try:
        return max(2, int(os.getenv("OTTER_ODDS_REFRESH_SEC", "3")))
    except (TypeError, ValueError):
        return 3


def otter_league_id(sport: str | None = None) -> str:
    load_env()
    sid = str(sport or SPORT_CFB).lower()
    if sid == SPORT_NFL:
        return os.getenv("OTTER_ODDS_LEAGUE_NFL", "nfl").strip() or "nfl"
    return os.getenv("OTTER_ODDS_LEAGUE_CFB", "ncaaf").strip() or "ncaaf"


def otter_phase() -> str:
    load_env()
    phase = os.getenv("OTTER_ODDS_PHASE", "pregame").strip().lower()
    return phase if phase in ("pregame", "live") else "pregame"


def _auth_headers() -> dict[str, str]:
    key = otter_odds_api_key()
    if not key:
        raise RuntimeError(
            "Otter Odds token missing. Add OTTER_ODDS_API_KEY (Settings in app.otterodds.com) "
            "or OTTER_ODDS_SESSION_TOKEN (Bearer token from browser DevTools while logged in)."
        )
    return {"Authorization": f"Bearer {key}"}


def _get(path: str, *, params: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{OTTER_API}/{path.lstrip('/')}"
    headers = _auth_headers()
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 503:
                time.sleep(min(30, 1 + attempt * 2))
                continue
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
        except (requests.RequestException, ValueError) as exc:
            last_err = exc
            time.sleep(1 + attempt)
    if last_err:
        raise last_err
    return {}


def _paginate(path: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    base = dict(params)
    while True:
        page = _get(path, params=base)
        chunk = page.get("data") or []
        if isinstance(chunk, list):
            out.extend([x for x in chunk if isinstance(x, dict)])
        pagination = page.get("pagination") or {}
        cursor = pagination.get("next_cursor")
        if not cursor:
            break
        base["cursor"] = cursor
    return out


def _event_participants(event: dict[str, Any]) -> tuple[str, str, str]:
    home = away = ""
    for p in event.get("participants") or []:
        if not isinstance(p, dict):
            continue
        role = str(p.get("role") or "").lower()
        name = str(p.get("name") or "")
        if role == "home":
            home = name
        elif role == "away":
            away = name
    label = event.get("name") or f"{away} @ {home}"
    return home, away, str(label)


def _market_key(cmp: dict[str, Any]) -> str | None:
    market = cmp.get("market") or {}
    period = str(market.get("period") or "game").lower()
    if period not in ("game", "full_game", "match"):
        return None
    mtype = str(market.get("type") or "").lower()
    subtype = str(market.get("subtype") or "").lower()
    name = str(market.get("name") or "").lower()
    blob = " ".join((mtype, subtype, name))
    if "team" in blob and "total" in blob:
        return "team_totals"
    if mtype in ("moneyline", "h2h", "ml") or "moneyline" in blob:
        return "h2h"
    if "spread" in blob or mtype in ("spread", "point_spread"):
        return "spreads"
    if "total" in blob or mtype in ("total", "game_total", "over_under"):
        return "totals"
    return None


def _selection_text(
    market_key: str,
    outcome_name: str,
    *,
    home: str,
    away: str,
    cmp: dict[str, Any],
) -> str:
    on = outcome_name.lower()
    if market_key == "totals":
        return "Over" if "over" in on else "Under" if "under" in on else outcome_name.title()
    if market_key == "h2h":
        if on in ("home", "draw"):
            return home if on == "home" else "Draw"
        if on == "away":
            return away
        if teams_match(on, home):
            return home
        if teams_match(on, away):
            return away
        return outcome_name.title()
    if market_key == "spreads":
        if on == "home":
            return home
        if on == "away":
            return away
        if teams_match(on, home):
            return home
        if teams_match(on, away):
            return away
        return outcome_name.title()
    if market_key == "team_totals":
        sel = cmp.get("selection") or {}
        return str(sel.get("name") or outcome_name.title())
    return outcome_name.title()


def _quote_row(
    cmp: dict[str, Any],
    *,
    market_key: str,
    selection: str,
    line: float | None,
    book_id: str,
    price: Any,
    updated_at: str | None,
) -> dict[str, Any]:
    event = cmp.get("event") or {}
    home, away, event_label = _event_participants(event)
    try:
        price_i = int(float(price)) if price is not None else None
    except (TypeError, ValueError):
        price_i = None
    bid = normalize_book_id(book_id) or book_id
    desc = None
    if market_key == "team_totals":
        desc = selection
    return {
        "event": event_label,
        "event_id": str(event.get("id") or ""),
        "home": home,
        "away": away,
        "commence_time": event.get("start_time"),
        "market_key": market_key,
        "market": market_label(market_key),
        "selection": selection,
        "description": desc,
        "side": selection,
        "line": line,
        "price": price_i,
        "book_id": bid,
        "book": book_label(bid),
        "source": "otterodds",
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
    }


def _flatten_comparison(cmp: dict[str, Any]) -> list[dict[str, Any]]:
    market_key = _market_key(cmp)
    if not market_key:
        return []
    home, away, _ = _event_participants(cmp.get("event") or {})
    base_line = cmp.get("line")
    lifecycle = cmp.get("lifecycle") or {}
    updated_at = lifecycle.get("last_confirmed_at") or cmp.get("updated_at")
    rows: list[dict[str, Any]] = []
    for outcome in cmp.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        oname = str(outcome.get("name") or "")
        selection = _selection_text(market_key, oname, home=home, away=away, cmp=cmp)
        for offer in outcome.get("offers") or []:
            if not isinstance(offer, dict):
                continue
            status = (offer.get("status") or {}).get("state")
            if status and str(status).lower() != "open":
                continue
            book = offer.get("bookmaker") or {}
            bid = str(book.get("id") or "")
            if not bid:
                continue
            price = (offer.get("price") or {}).get("american")
            if price is None:
                continue
            oline = offer.get("line")
            line = float(oline) if oline is not None else (float(base_line) if base_line is not None else None)
            olife = offer.get("lifecycle") or {}
            rows.append(
                _quote_row(
                    cmp,
                    market_key=market_key,
                    selection=selection,
                    line=line,
                    book_id=bid,
                    price=price,
                    updated_at=olife.get("last_confirmed_at") or updated_at,
                )
            )
    return rows


def fetch_otter_comparisons(
    sport: str | None = None,
    *,
    phase: str | None = None,
    league_id: str | None = None,
) -> list[dict[str, Any]]:
    sid = str(sport or SPORT_CFB).lower()
    params: dict[str, Any] = {
        "league_id": league_id or otter_league_id(sid),
        "phase": phase or otter_phase(),
        "limit": 500,
    }
    return _paginate("v2/odds-comparisons", params=params)


def fetch_otter_events(sport: str | None = None, *, phase: str | None = None) -> list[dict[str, Any]]:
    sid = str(sport or SPORT_CFB).lower()
    params = {
        "league_id": otter_league_id(sid),
        "phase": phase or otter_phase(),
        "limit": 500,
    }
    return _paginate("v2/events", params=params)


def fetch_otter_game_lines(
    sport: str | None = None,
    *,
    phase: str | None = None,
    tab: str | None = None,
) -> pd.DataFrame:
    _ = tab
    comparisons = fetch_otter_comparisons(sport, phase=phase)
    rows: list[dict[str, Any]] = []
    for cmp in comparisons:
        rows.extend(_flatten_comparison(cmp))
    return _dedupe(pd.DataFrame(rows))


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    cols = [c for c in ["event_id", "market_key", "selection", "book_id", "line", "price", "description"] if c in df.columns]
    if cols:
        df = df.drop_duplicates(subset=cols, keep="last")
    return df.reset_index(drop=True)
