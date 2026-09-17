"""Flatten in-play game + prop markets into CSV rows."""
from __future__ import annotations

from typing import Any

from lib.odds_math import ev_pct, implied_to_american

from .grading import grade_market_quote
from .ui.board import _fmt_spread_line, _model_for_quote, _projection_for_quote


def _live_game_dict(
    *,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    period: int,
    clock: str | None,
    win_prob_home: float | None,
) -> dict[str, Any]:
    return {
        "status": "in",
        "period": period,
        "clock": clock,
        "home_score": home_score,
        "away_score": away_score,
        "win_prob_home": win_prob_home,
    }


def game_market_rows(
    *,
    sport: str,
    year: int,
    week: int,
    event_id: str,
    home: str,
    away: str,
    play: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    sim: dict[str, Any] | None,
    pregame: dict[str, Any] | None,
    captured_at: str,
    play_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One row per priced market at this play snapshot."""
    hp = int(play.get("homeScore") or play.get("home_score") or 0)
    ap = int(play.get("awayScore") or play.get("away_score") or 0)
    game = {
        "home": home,
        "away": away,
        "homePoints": hp,
        "awayPoints": ap,
        "completed": False,
        "event_id": event_id,
    }
    pregame_sim = (pregame or {}).get("sim") or {}
    pregame_quotes = {}
    for q in (pregame or {}).get("odds_quotes") or []:
        mk = str(q.get("market_key") or "")
        sel = str(q.get("selection") or "").lower()
        if "spread" in mk:
            key = "spread_home" if sel == home.lower() or "home" in mk else "spread_away"
        elif "total" in mk:
            key = "total_over" if sel == "over" else "total_under"
        elif "h2h" in mk or mk == "ml":
            key = "ml_home" if sel == home.lower() else "ml_away"
        else:
            continue
        pregame_quotes[key] = q

    order = [
        ("spread_away", "Spread"),
        ("spread_home", "Spread"),
        ("total_over", "Total"),
        ("total_under", "Total"),
        ("ml_away", "ML"),
        ("ml_home", "ML"),
    ]
    rows: list[dict[str, Any]] = []
    for key, default_market in order:
        q = quotes.get(key)
        if not q:
            continue
        proj_str = _projection_for_quote(q, sim, home=home, away=away)
        model_price, model_prob = _model_for_quote(q, sim)
        graded = grade_market_quote(key, q, game=game, sim=sim, home=home, away=away, quotes=quotes)
        pq = pregame_quotes.get(key) or {}
        pre_proj = _projection_for_quote(q, pregame_sim, home=home, away=away) if pregame_sim else None
        rows.append(
            {
                "captured_at": captured_at,
                "sport": sport,
                "year": year,
                "week": week,
                "event_id": event_id,
                "play_id": play.get("id"),
                "period": play.get("period"),
                "clock": play.get("clock"),
                "play_text": play.get("text"),
                **(play_context or {}),
                "home": home,
                "away": away,
                "home_score": hp,
                "away_score": ap,
                "market_type": "game",
                "market": str(q.get("market") or default_market),
                "selection": q.get("selection"),
                "line": q.get("line"),
                "book_id": q.get("book_id"),
                "book_price": q.get("price"),
                "model_projection": proj_str,
                "model_price": model_price,
                "model_prob": model_prob,
                "actual_result": graded.get("actual"),
                "bet_result": graded.get("result"),
                "expected_roi_pct": graded.get("expected_roi_pct"),
                "pregame_line": pq.get("line"),
                "pregame_book_price": pq.get("price"),
                "pregame_projection": pre_proj,
            }
        )
    return rows


def prop_market_rows(
    *,
    sport: str,
    year: int,
    week: int,
    event_id: str,
    home: str,
    away: str,
    play: dict[str, Any],
    props: list[dict[str, Any]],
    captured_at: str,
    play_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prop in props:
        for side_key, side_label in (("over", "Over"), ("under", "Under")):
            quote = prop.get(side_key)
            if not quote or quote.get("price") is None:
                continue
            rows.append(
                {
                    "captured_at": captured_at,
                    "sport": sport,
                    "year": year,
                    "week": week,
                    "event_id": event_id,
                    "play_id": play.get("id"),
                    "period": play.get("period"),
                    "clock": play.get("clock"),
                    "play_text": play.get("text"),
                    **(play_context or {}),
                    "home": home,
                    "away": away,
                    "home_score": play.get("homeScore"),
                    "away_score": play.get("awayScore"),
                    "market_type": "prop",
                    "market": prop.get("stat") or prop.get("prop_key"),
                    "selection": side_label,
                    "player": prop.get("player"),
                    "line": prop.get("line"),
                    "book_id": quote.get("book_id"),
                    "book_price": quote.get("price"),
                    "model_projection": prop.get("live_projection"),
                    "model_price": prop.get("model_price"),
                    "model_prob": None,
                    "actual_stat": prop.get("actual_stat"),
                    "bet_result": None,
                    "expected_roi_pct": prop.get("expected_roi_pct") if prop.get("model_side") == side_key else None,
                    "pregame_projection": prop.get("pregame_projection"),
                    "pregame_line": prop.get("line"),
                    "pregame_book_price": quote.get("price"),
                }
            )
    return rows
