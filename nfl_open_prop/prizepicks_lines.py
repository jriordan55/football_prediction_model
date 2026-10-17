"""Current PrizePicks totals for the six prop-board markets."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKETS = [
    "player_pass_yds",
    "player_pass_attempts",
    "player_rush_yds",
    "player_rush_attempts",
    "player_reception_yds",
    "player_receptions",
]
KEEP = {"pass_yds", "pass_attempts", "rush_yds", "rush_attempts", "rec_yds", "receptions"}


def _norm(value: str) -> str:
    text = value.lower().replace(".", " ").replace("'", "").replace("’", "")
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", text)
    text = re.sub(r"[^a-z\s]", " ", text)
    return " ".join(text.split())


def attach_prizepicks(sport: dict) -> int:
    """Write today's PrizePicks line onto each matching prop. Returns how many landed."""
    app = str(ROOT / "streamlit_app")
    if app not in sys.path:
        sys.path.insert(0, app)
    from lib.multi_book_props import parse_bookmaker_props
    from lib.odds_client import fetch_odds_api_event_odds, fetch_odds_api_events

    events, quota = fetch_odds_api_events(sport_key="americanfootball_nfl")
    if quota.get("error"):
        print(f"prizepicks events {quota['error']}", flush=True)
        return 0
    by_name = {_norm(str(player.get("name") or "")): player for player in sport.get("players") or []}
    matched = 0
    for event in events:
        payload, _cost = fetch_odds_api_event_odds(
            str(event.get("id") or ""),
            MARKETS,
            bookmakers="prizepicks",
            sport_key="americanfootball_nfl",
            regions="us_dfs",
        )
        if not payload:
            continue
        home = payload.get("home_team") or event.get("home_team") or ""
        away = payload.get("away_team") or event.get("away_team") or ""
        start = payload.get("commence_time") or event.get("commence_time") or ""
        for book in payload.get("bookmakers") or []:
            if str(book.get("key") or "") != "prizepicks":
                continue
            for quote in parse_bookmaker_props(book):
                stat = str(quote.get("propKey") or "")
                if stat not in KEEP:
                    continue
                player = by_name.get(_norm(str(quote.get("player") or "")))
                if player is None:
                    continue
                try:
                    line = float(quote.get("line"))
                except (TypeError, ValueError):
                    continue
                slot = (player.get("props") or {}).get(stat)
                if not isinstance(slot, dict):
                    slot = {}
                    player.setdefault("props", {})[stat] = slot
                slot["ppLine"] = line
                slot["ppOver"] = quote.get("over_price")
                slot["line"] = line
                player["home"] = home or player.get("home")
                player["away"] = away or player.get("away")
                if start:
                    player["start"] = start
                matched += 1
    sport["ppUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    return matched
