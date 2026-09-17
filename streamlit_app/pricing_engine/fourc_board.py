"""Live odds board — fresh API pull every poll, no stale Streamlit cache."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from lib.fourc_odds_client import (
    fetch_board,
    fetch_game_player_props,
    fourc_odds_enabled,
    parse_board_game_quotes,
    _game_names,
)
from lib.odds_live_feed import ensure_odds_live_feed, live_feed_path
from lib.sport_context import SPORT_NFL
from lib.team_registry import teams_match as cfb_teams_match


def ensure_live_feed(sport: str) -> None:
    if fourc_odds_enabled():
        ensure_odds_live_feed(sport)


def _norm_team(val: object) -> str:
    if isinstance(val, dict):
        return str(val.get("name") or "")
    return str(val or "")


def _load_board_cache(sport: str) -> dict[str, Any]:
    path = live_feed_path(sport)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _teams_match(sport: str, a: str, b: str) -> bool:
    from lib.nfl_team_registry import teams_match as nfl_teams_match

    fn = nfl_teams_match if sport == SPORT_NFL else cfb_teams_match
    return fn(a, b)


def _match_game(g: dict[str, Any], home: str, away: str, *, sport: str) -> bool:
    gh = _norm_team(g.get("home"))
    ga = _norm_team(g.get("away"))
    return _teams_match(sport, gh, home) and _teams_match(sport, ga, away)


def _find_cached_game(cache: dict[str, Any], home: str, away: str, *, sport: str) -> dict[str, Any] | None:
    for g in (cache.get("games") or {}).values():
        if isinstance(g, dict) and _match_game(g, home, away, sport=sport):
            return g
    return None


def _find_board_game(board: dict[str, Any], home: str, away: str, *, sport: str) -> dict[str, Any] | None:
    for g in board.get("games") or []:
        gh = _norm_team((g.get("home") or {}).get("name") if isinstance(g.get("home"), dict) else g.get("home"))
        ga = _norm_team((g.get("away") or {}).get("name") if isinstance(g.get("away"), dict) else g.get("away"))
        if _teams_match(sport, gh, home) and _teams_match(sport, ga, away):
            return g
    return None


def _quotes_from_cache(sport: str, home: str, away: str) -> dict[str, dict[str, Any]]:
    cache = _load_board_cache(sport)
    cached = _find_cached_game(cache, home, away, sport=sport)
    if not cached or not cached.get("quotes"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, q in (cached.get("quotes") or {}).items():
        bid = str((q or {}).get("book_id") or "").lower()
        if bid in ("4c", "4codds"):
            continue
        if q:
            out[key] = dict(q)
    return out


def _quotes_have_retail(quotes: dict[str, dict[str, Any]]) -> bool:
    return any(
        str((q or {}).get("book_id") or "").lower() not in ("4c", "4codds")
        for q in quotes.values()
    )


def load_matchup_odds(
    sport: str,
    home: str,
    away: str,
    *,
    year: int | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """
    Single fresh pull for game lines + player props.
    Called every ODDS_POLL_SEC from the pricing fragment — intentionally uncached.
    Falls back to pricing_history archive when 4C has no lines for this matchup.
    """
    fetched_at = datetime.now(timezone.utc)
    source = "4codds"
    archived_game: dict[str, Any] | None = None
    quotes: dict[str, dict[str, Any]] = {}
    props: list[dict[str, Any]] = []

    if fourc_odds_enabled():
        try:
            board = fetch_board(sport=sport)
        except Exception:
            board = {}

        game = _find_board_game(board, home, away, sport=sport) if board else None
        quotes = parse_board_game_quotes(game) if game else _quotes_from_cache(sport, home, away)

        if game:
            gid = str(game.get("id") or "")
            if gid:
                home_n, away_n, event = _game_names(game)
                try:
                    props = fetch_game_player_props(
                        gid, sport=sport, home=home_n, away=away_n, event=event,
                    )
                except Exception:
                    props = []

    from lib.pricing_history import prepare_props_for_ui

    props = prepare_props_for_ui(props)

    if year is not None and week is not None and (not _quotes_have_retail(quotes) or not props):
        from lib.pricing_history import (
            flat_quotes_to_board,
            load_archived_game,
        )

        archived_game = load_archived_game(sport, int(year), int(week), home, away)
        if archived_game:
            if not _quotes_have_retail(quotes):
                flat = archived_game.get("odds_quotes") or []
                quotes = flat_quotes_to_board(flat, home=home, away=away)
                if quotes:
                    source = "pricing_history"
            if not props:
                props = prepare_props_for_ui(archived_game.get("props") or [])
                if props:
                    source = "pricing_history"
            cap_at = archived_game.get("_captured_at")
            if cap_at:
                try:
                    fetched_at = datetime.fromisoformat(str(cap_at).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    pass

    if not props and year is not None and week is not None:
        from lib.pricing_history import load_props_board, props_for_matchup

        board_rows = props_for_matchup(
            load_props_board(sport, int(year), int(week)),
            home=home,
            away=away,
        )
        props = prepare_props_for_ui(board_rows)
        if props and source == "4codds":
            source = "props_board_cache"

    return {
        "quotes": quotes,
        "props": props,
        "fetched_at": fetched_at,
        "source": source,
        "archived_game": archived_game,
    }


def quotes_for_matchup(sport: str, home: str, away: str) -> dict[str, dict[str, Any]]:
    return load_matchup_odds(sport, home, away)["quotes"]


def player_props_for_matchup(sport: str, home: str, away: str) -> list[dict[str, Any]]:
    return load_matchup_odds(sport, home, away)["props"]
