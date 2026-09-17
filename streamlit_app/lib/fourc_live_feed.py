"""Background 4C board sync — writes JSON for invisible browser-side DOM patches."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.book_logos import book_logo_fallback_url, book_logo_url
from lib.fourc_odds_client import (
    _flatten_board_cells,
    _fourc_book_id,
    _game_names,
    fetch_board,
    fourc_odds_enabled,
    fourc_refresh_sec,
)
from lib.sport_context import SPORT_CFB, SPORT_NFL

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}


def _format_price(price: Any) -> str:
    if price is None:
        return "—"
    try:
        n = int(float(price))
    except (TypeError, ValueError):
        return str(price)
    return f"+{n}" if n > 0 else str(n)


def _spread_label(abbr: str, line: Any) -> str:
    if line is None:
        return f"{abbr} —"
    try:
        n = float(line)
    except (TypeError, ValueError):
        return f"{abbr} —"
    if n > 0:
        return f"{abbr} +{n:g}"
    if n < 0:
        return f"{abbr} {n:g}"
    return f"{abbr} PK"


def _best_entry(entries: list[list[Any]] | None) -> dict[str, Any] | None:
    if not entries:
        return None
    best: dict[str, Any] | None = None
    for entry in entries:
        if not entry or len(entry) < 2:
            continue
        book_id = _fourc_book_id(str(entry[0]))
        if not book_id:
            continue
        price = entry[1]
        if price is None:
            continue
        row = {
            "book_id": book_id,
            "price": _format_price(price),
            "raw_price": price,
            "logo_url": book_logo_url(book_id),
            "logo_fallback": book_logo_fallback_url(book_id),
        }
        if best is None:
            best = row
            continue
        try:
            a, b = int(float(price)), int(float(best["raw_price"]))
        except (TypeError, ValueError):
            continue
        # Higher American price is better for bettor
        if (a >= 0 and b >= 0 and a > b) or (a < 0 and b < 0 and a > b) or (a > b):
            best = row
    if best:
        best.pop("raw_price", None)
    return best


def _board_snapshot(sport: str) -> dict[str, Any]:
    board = fetch_board(sport=sport)
    games_out: dict[str, Any] = {}
    for game in board.get("games") or []:
        gid = str(game.get("id") or "")
        if not gid:
            continue
        home, away, _ = _game_names(game)
        main = game.get("main") or {}
        cells = game.get("cells") or []
        cell_map: dict[str, dict[str, Any]] = {}
        for cell in cells:
            if not cell or len(cell) < 5:
                continue
            mk = str(cell[0] or "")
            side = str(cell[1] or "")
            line = cell[2]
            book_entry = cell[4]
            entries = [book_entry] if isinstance(book_entry, list) else None
            best = _best_entry(entries)
            if not best:
                continue
            if mk == "sp":
                key = f"spread_{side}"
                try:
                    ln = float(line) if line is not None else float(main.get("sp"))
                except (TypeError, ValueError):
                    ln = None
                if side == "away" and ln is not None:
                    ln = -ln
                best["line"] = ln
            elif mk == "tot":
                key = f"total_{side}"
                try:
                    best["line"] = float(line) if line is not None else float(main.get("tot"))
                except (TypeError, ValueError):
                    best["line"] = None
            else:
                continue
            cell_map[key] = best

        rows = _flatten_board_cells(game)
        if rows:
            for row in rows:
                mk = str(row.get("market_key") or "").lower()
                sel = str(row.get("selection") or "").lower()
                if mk == "spreads":
                    if sel == home.lower() or home.lower() in sel:
                        key = "spread_home"
                    elif sel == away.lower() or away.lower() in sel:
                        key = "spread_away"
                    else:
                        continue
                elif mk == "totals":
                    key = "total_over" if "over" in sel else "total_under" if "under" in sel else ""
                else:
                    continue
                if not key:
                    continue
                bid = str(row.get("book_id") or "")
                cand = {
                    "book_id": bid,
                    "price": _format_price(row.get("price")),
                    "line": row.get("line"),
                    "logo_url": book_logo_url(bid),
                    "logo_fallback": book_logo_fallback_url(bid),
                }
                existing = cell_map.get(key)
                if not existing:
                    cell_map[key] = cand

        games_out[gid] = {
            "home": home,
            "away": away,
            "main": main,
            "quotes": cell_map,
        }

    return {
        "sport": sport,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "games": games_out,
    }


def live_feed_path(sport: str) -> Path:
    tag = "cfb" if str(sport).lower() != SPORT_NFL else "nfl"
    return _STATIC_DIR / f"fourc_live_{tag}.json"


def live_feed_updated_at(sport: str | None = None) -> datetime | None:
    """Timestamp of the latest 4C board mirror (odds sync, not projection cache)."""
    from lib.view_disk_cache import parse_cache_timestamp

    sid = str(sport or SPORT_CFB).lower()
    path = live_feed_path(sid)
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


def write_live_snapshot(sport: str) -> Path:
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    path = live_feed_path(sport)
    payload = _board_snapshot(sport)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path


def _poll_loop(sport: str) -> None:
    interval = max(2, min(fourc_refresh_sec(), 5))
    while True:
        try:
            if fourc_odds_enabled():
                write_live_snapshot(sport)
        except Exception:
            pass
        time.sleep(interval)


def ensure_fourc_live_feed(sport: str | None = None) -> None:
    """Start a daemon thread that mirrors 4C board JSON for client-side patches."""
    if not fourc_odds_enabled():
        return
    sid = str(sport or SPORT_CFB).lower()
    with _lock:
        if sid in _threads and _threads[sid].is_alive():
            return
        try:
            write_live_snapshot(sid)
        except Exception:
            pass
        t = threading.Thread(target=_poll_loop, args=(sid,), daemon=True, name=f"fourc-live-{sid}")
        _threads[sid] = t
        t.start()
