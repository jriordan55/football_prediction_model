"""Unified live odds mirror — Otter Odds (preferred) or 4C for browser DOM patches."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.book_logos import book_logo_fallback_url, book_logo_url
from lib.fourc_live_feed import _board_snapshot as _fourc_board_snapshot
from lib.fourc_live_feed import ensure_fourc_live_feed as _ensure_fourc_only
from lib.fourc_odds_client import fourc_odds_enabled, fourc_refresh_sec
from lib.otter_odds_client import (
    _event_participants,
    _market_key,
    fetch_otter_comparisons,
    otter_odds_enabled,
    otter_refresh_sec,
)
from lib.sharp_edges import _better_american
from lib.sport_context import SPORT_CFB, SPORT_NFL

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}


def live_odds_enabled() -> bool:
    return otter_odds_enabled() or fourc_odds_enabled()


def live_feed_path(sport: str) -> Path:
    tag = "cfb" if str(sport).lower() != SPORT_NFL else "nfl"
    return _STATIC_DIR / f"odds_live_{tag}.json"


def live_feed_updated_at(sport: str | None = None) -> datetime | None:
    from lib.view_disk_cache import parse_cache_timestamp

    sid = str(sport or SPORT_CFB).lower()
    path = live_feed_path(sid)
    if not path.exists():
        # Legacy 4C file
        legacy = _STATIC_DIR / f"fourc_live_{'nfl' if sid == SPORT_NFL else 'cfb'}.json"
        path = legacy if legacy.exists() else path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = parse_cache_timestamp(data.get("updated_at"))
        if ts is not None:
            return ts
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _format_price(price: Any) -> str:
    if price is None:
        return "—"
    try:
        n = int(float(price))
    except (TypeError, ValueError):
        return str(price)
    return f"+{n}" if n > 0 else str(n)


def _pack_quote(book_id: str, price: Any, *, line: float | None = None) -> dict[str, Any]:
    bid = str(book_id or "")
    out: dict[str, Any] = {
        "book_id": bid,
        "price": _format_price(price),
        "logo_url": book_logo_url(bid),
        "logo_fallback": book_logo_fallback_url(bid),
    }
    if line is not None:
        out["line"] = line
    return out


def _better_quote(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a:
        return b
    if not b:
        return a
    try:
        pa = str(a.get("price") or "").replace("+", "")
        pb = str(b.get("price") or "").replace("+", "")
        winner = _better_american(int(float(pa)), int(float(pb)))
        return a if winner == int(float(pa)) else b
    except (TypeError, ValueError):
        return a


def _otter_board_snapshot(sport: str) -> dict[str, Any]:
    comparisons = fetch_otter_comparisons(sport)
    games_out: dict[str, Any] = {}
    for cmp in comparisons:
        market_key = _market_key(cmp)
        if market_key not in ("spreads", "totals"):
            continue
        event = cmp.get("event") or {}
        gid = str(event.get("id") or "")
        if not gid:
            continue
        home, away, _ = _event_participants(event)
        game = games_out.setdefault(
            gid,
            {"home": home, "away": away, "quotes": {}},
        )
        quotes = game["quotes"]
        base_line = cmp.get("line")
        for outcome in cmp.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            oname = str(outcome.get("name") or "").lower()
            best_offer = None
            best_price = None
            best_bid = None
            best_line = base_line
            for offer in outcome.get("offers") or []:
                if not isinstance(offer, dict):
                    continue
                if str((offer.get("status") or {}).get("state") or "").lower() != "open":
                    continue
                book = offer.get("bookmaker") or {}
                bid = str(book.get("id") or "")
                price = (offer.get("price") or {}).get("american")
                if not bid or price is None:
                    continue
                if best_price is None or _better_american(price, best_price) == price:
                    best_price = price
                    best_bid = bid
                    oline = offer.get("line")
                    if oline is not None:
                        best_line = oline
            if best_bid is None or best_price is None:
                continue
            best_offer = _pack_quote(best_bid, best_price, line=float(best_line) if best_line is not None else None)
            if market_key == "spreads":
                if oname == "home" or oname == home.lower():
                    key = "spread_home"
                elif oname == "away" or oname == away.lower():
                    key = "spread_away"
                    if best_offer.get("line") is not None:
                        try:
                            best_offer["line"] = -float(best_offer["line"])
                        except (TypeError, ValueError):
                            pass
                else:
                    continue
            else:
                key = "total_over" if "over" in oname else "total_under" if "under" in oname else ""
            if not key:
                continue
            quotes[key] = _better_quote(quotes.get(key), best_offer) or best_offer

    return {
        "sport": sport,
        "source": "otterodds",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "games": games_out,
    }


def _board_snapshot(sport: str) -> dict[str, Any]:
    if otter_odds_enabled():
        return _otter_board_snapshot(sport)
    snap = _fourc_board_snapshot(sport)
    snap["source"] = "4codds"
    return snap


def write_live_snapshot(sport: str) -> Path:
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    path = live_feed_path(sport)
    payload = _board_snapshot(sport)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path


def _poll_loop(sport: str) -> None:
    interval = otter_refresh_sec() if otter_odds_enabled() else max(2, min(fourc_refresh_sec(), 5))
    while True:
        try:
            if live_odds_enabled():
                write_live_snapshot(sport)
        except Exception:
            pass
        time.sleep(interval)


def ensure_odds_live_feed(sport: str | None = None) -> None:
    """Start daemon thread mirroring Otter or 4C odds JSON for client-side patches."""
    if not live_odds_enabled():
        return
    sid = str(sport or SPORT_CFB).lower()
    with _lock:
        if sid in _threads and _threads[sid].is_alive():
            return
        try:
            write_live_snapshot(sid)
        except Exception:
            if fourc_odds_enabled() and not otter_odds_enabled():
                _ensure_fourc_only(sid)
                return
        t = threading.Thread(target=_poll_loop, args=(sid,), daemon=True, name=f"odds-live-{sid}")
        _threads[sid] = t
        t.start()


# Back-compat aliases
ensure_fourc_live_feed = ensure_odds_live_feed
