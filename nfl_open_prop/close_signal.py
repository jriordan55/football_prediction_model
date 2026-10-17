"""Flag the close-gap bet on the phone desk.

Today's DraftKings number has to sit at least 0.6 standard deviations from the
average number posted for this player before this week. A lower number is the
over. A higher number is the under. A price shorter than -150 is not a bet.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "odds_api_cache"
DESK = ROOT / "prop_desk_site" / "desk.json"
GAP = 0.6


def _norm(name: str) -> str:
    import sys

    app = str(ROOT / "streamlit_app")
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.nfl_prop_avg_close import _close_player_key

    return _close_player_key(name)


def _price(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _boards() -> dict[tuple[str, str, int, int], float]:
    """Median DraftKings line for each player, market, and week."""
    import sys

    app = str(ROOT / "streamlit_app")
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.nfl_prop_avg_close import _close_player_key
    from lib.prop_pricing import prop_key_from_row

    best: dict[tuple[int, int], tuple[int, Path]] = {}
    for path in CACHE.glob("player_props_board_nfl_20*_w*_v*.json"):
        parts = path.stem.split("_")
        try:
            year = int(parts[4])
            week = int(parts[5][1:])
            version = int(parts[6][1:])
        except (IndexError, ValueError):
            continue
        key = (year, week)
        if key not in best or version > best[key][0]:
            best[key] = (version, path)
    lines: dict[tuple[str, str, int, int], list[float]] = defaultdict(list)
    for (year, week), (_version, path) in best.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        seen: dict[tuple[str, str, float], None] = {}
        for raw in payload.get("rows") or []:
            if str(raw.get("book_id") or raw.get("book") or "").lower() not in {"draftkings", "dk"}:
                continue
            stat = str(raw.get("prop_key") or raw.get("propKey") or prop_key_from_row(raw) or "").lower()
            player = _close_player_key(str(raw.get("player") or ""))
            price = _price(raw.get("line"))
            if not player or not stat or price is None:
                continue
            seen.setdefault((player, stat, price), None)
        rung: dict[tuple[str, str], list[float]] = defaultdict(list)
        for player, stat, price in seen:
            rung[(player, stat)].append(price)
        for (player, stat), prices in rung.items():
            prices.sort()
            lines[(player, stat, year, week)].append(prices[len(prices) // 2])
    return {key: values[0] for key, values in lines.items()}


def _sd(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    width = float(statistics.stdev(values))
    if width <= 1e-9:
        return None
    return width


def signal_for(logs: list, line: float, over, under, closes: dict, name: str, stat: str) -> dict | None:
    window = list(logs or [])[-10:]
    if len(window) < 5:
        return None
    values = []
    posted = []
    for row in window:
        try:
            season, week, _opp, value = int(row[0]), int(row[1]), row[2], float(row[3])
        except (TypeError, ValueError, IndexError):
            continue
        values.append(value)
        prior = closes.get((name, stat, season, week))
        if prior is not None:
            posted.append(prior)
    if len(values) < 5 or len(posted) < 5:
        return None
    width = _sd(values)
    if width is None:
        return None
    gap = (sum(posted) / len(posted) - float(line)) / width
    if gap >= GAP:
        side, price = "Over", _price(over)
    elif gap <= -GAP:
        side, price = "Under", _price(under)
    else:
        return None
    if price is None or price < -150:
        return None
    return {"side": side, "gap": round(gap, 2), "closes": len(posted)}


def attach_signals(sport: dict) -> int:
    """Write ``signal`` onto each NFL prop. Returns how many bets fired."""
    closes = _boards()
    found = 0
    season = int(sport.get("season") or 0)
    week = int(sport.get("week") or 0)
    # A close from the week on the board is not a prior close.
    closes = {
        key: value
        for key, value in closes.items()
        if (key[2], key[3]) < (season, week)
    }
    for player in sport.get("players") or []:
        name = _norm(str(player.get("name") or ""))
        logs = player.get("logs") or {}
        for stat, quote in (player.get("props") or {}).items():
            if not isinstance(quote, dict):
                continue
            quote.pop("signal", None)
            line = quote.get("dkLine")
            if line is None:
                line = quote.get("line")
            if line is None or quote.get("dkOver") is None and quote.get("dkUnder") is None:
                continue
            flag = signal_for(logs.get(stat) or [], float(line), quote.get("dkOver"), quote.get("dkUnder"), closes, name, stat)
            if flag:
                quote["signal"] = flag
                found += 1
    return found


def main() -> None:
    desk = json.loads(DESK.read_text(encoding="utf-8"))
    count = attach_signals(desk["sports"]["nfl"])
    DESK.write_text(json.dumps(desk, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"signals {count}")


if __name__ == "__main__":
    main()
