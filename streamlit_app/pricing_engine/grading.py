"""Grade completed-game pricing rows — actuals, W/L, pregame ROI."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from lib.odds_math import ev_pct
from lib.projection_results import fmt_actual, grade_side
from lib.prop_board_enrich import prop_display_name
from lib.prop_pricing import analyze_prop_line, prop_key_from_row, side_win_prob
from lib.prop_pricing import prop_label_for_key
from lib.prop_results import (
    grade_player_prop,
    model_pick_side,
    parse_espn_boxscore,
    stat_for_prop_key,
)

from .pit import realized_roi_from_result


def _home_away_points(game: dict[str, Any]) -> tuple[int | None, int | None]:
    hp = game.get("homePoints")
    if hp is None:
        hp = game.get("home_score")
    ap = game.get("awayPoints")
    if ap is None:
        ap = game.get("away_score")
    try:
        return (int(hp) if hp is not None else None, int(ap) if ap is not None else None)
    except (TypeError, ValueError):
        return None, None


def _resolve_event_id(
    game: dict[str, Any],
    *,
    home: str = "",
    away: str = "",
    sport: str | None = None,
    year: int | None = None,
    week: int | None = None,
) -> str:
    for key in ("event_id", "eventId", "id"):
        val = game.get(key)
        if val is not None and str(val).strip().isdigit():
            return str(val).strip()
    if not home or not away:
        home = str(game.get("home") or home)
        away = str(game.get("away") or away)
    try:
        from lib.espn_client import fetch_scoreboard
        from lib.nfl_team_registry import teams_match as nfl_match
        from lib.team_registry import teams_match as cfb_match

        from lib.sport_context import SPORT_NFL

        match = nfl_match if sport == SPORT_NFL else cfb_match
        sb = fetch_scoreboard(week=week, year=year, sport=sport)
        if sb.empty:
            return ""
        for _, row in sb.iterrows():
            rh = str(row.get("home") or "")
            ra = str(row.get("away") or "")
            if match(rh, home) and match(ra, away):
                eid = row.get("event_id")
                if eid is not None:
                    return str(eid)
    except Exception:
        pass
    return ""


@lru_cache(maxsize=64)
def _box_stats_for_event(event_id: str, sport: str = "") -> dict[str, dict[str, float]]:
    if not event_id:
        return {}
    try:
        from lib.espn_client import fetch_game_summary

        summary = fetch_game_summary(str(event_id), sport=sport or None)
        return parse_espn_boxscore(summary)
    except Exception:
        return {}


def _quote_side_key(quote_key: str, quote: dict[str, Any], *, home: str, away: str) -> tuple[str, str]:
    market = str(quote.get("market") or "")
    sel = str(quote.get("selection") or "")
    key = str(quote_key or "").lower()

    if market == "Spread" or "spread" in key:
        if sel == home or key.endswith("_home"):
            return "home_cover", "spread"
        return "away_cover", "spread"
    if market == "Total" or "total" in key:
        if "under" in key or sel.lower() == "under":
            return "under", "total"
        return "over", "total"
    if market == "ML" or key.startswith("ml_"):
        if sel == home or key.endswith("_home"):
            return "home_ml", "moneyline"
        return "away_ml", "moneyline"
    return sel.lower(), market.lower()


def _model_prob_for_side(
    side: str,
    quote: dict[str, Any],
    sim: dict[str, Any] | None,
    *,
    home: str,
    away: str,
) -> float | None:
    if not sim:
        return None
    market = str(quote.get("market") or "")
    line = quote.get("line")

    if market == "Spread":
        pick_sel = home if side == "home_cover" else away
        best_prob = None
        best_dist = 1e9
        for m in sim.get("markets") or []:
            if m.get("market") != "Spread" or m.get("period"):
                continue
            if str(m.get("selection") or "") != pick_sel:
                continue
            try:
                prob = float(m.get("prob"))
                mline = float(m.get("line")) if m.get("line") is not None else None
            except (TypeError, ValueError):
                continue
            if not (0.0 < prob < 1.0):
                continue
            if line is not None and mline is not None:
                dist = abs(mline - float(line))
                if dist < best_dist:
                    best_dist = dist
                    best_prob = prob
            elif best_prob is None:
                best_prob = prob
        if best_prob is not None and (line is None or best_dist <= 7.0):
            return best_prob
        return None

    if market == "Total":
        pick_sel = "Over" if side == "over" else "Under"
        best_prob = None
        best_dist = 1e9
        for m in sim.get("markets") or []:
            if m.get("market") != "Total" or m.get("period"):
                continue
            if str(m.get("selection") or "").lower() != pick_sel.lower():
                continue
            try:
                prob = float(m.get("prob"))
                mline = float(m.get("line")) if m.get("line") is not None else None
            except (TypeError, ValueError):
                continue
            if not (0.0 < prob < 1.0):
                continue
            if line is not None and mline is not None:
                dist = abs(mline - float(line))
                if dist < best_dist:
                    best_dist = dist
                    best_prob = prob
            elif best_prob is None:
                best_prob = prob
        if best_prob is not None and (line is None or best_dist <= 7.0):
            return best_prob
        return None

    if market == "ML":
        pick_sel = home if side == "home_ml" else away
        for m in sim.get("markets") or []:
            if m.get("market") == "ML" and m.get("selection") == pick_sel:
                try:
                    prob = float(m.get("prob"))
                    if 0.0 < prob < 1.0:
                        return prob
                except (TypeError, ValueError):
                    continue
    return None


def _model_spread_pick(
    sim: dict[str, Any] | None,
    *,
    home_line: float | None,
) -> str | None:
    if sim is None or home_line is None:
        return None
    sb = sim.get("scoreboard") or {}
    try:
        hm = float(sb.get("home_mean"))
        am = float(sb.get("away_mean"))
        margin = hm - am
        return "home_cover" if margin + float(home_line) > 0 else "away_cover"
    except (TypeError, ValueError):
        return None


def _model_total_pick(sim: dict[str, Any] | None, *, total_line: float | None) -> str | None:
    if sim is None or total_line is None:
        return None
    sb = sim.get("scoreboard") or {}
    try:
        proj = float(sb.get("total_mean"))
        ln = float(total_line)
    except (TypeError, ValueError):
        return None
    if abs(proj - ln) < 0.05:
        return None
    return "over" if proj > ln else "under"


def _model_ml_pick(sim: dict[str, Any] | None, *, home: str, away: str) -> str | None:
    if not sim:
        return None
    p_home = p_away = None
    for m in sim.get("markets") or []:
        if m.get("market") != "ML":
            continue
        if m.get("selection") == home:
            try:
                p_home = float(m.get("prob"))
            except (TypeError, ValueError):
                pass
        elif m.get("selection") == away:
            try:
                p_away = float(m.get("prob"))
            except (TypeError, ValueError):
                pass
    if p_home is None or p_away is None:
        return None
    return "home_ml" if p_home >= p_away else "away_ml"


def model_pick_for_market(
    market: str,
    quotes: dict[str, dict[str, Any]],
    sim: dict[str, Any] | None,
    *,
    home: str,
    away: str,
) -> str | None:
    mkt = str(market or "").lower()
    if mkt == "spread":
        home_q = quotes.get("spread_home") or {}
        try:
            home_line = float(home_q.get("line"))
        except (TypeError, ValueError):
            home_line = None
        return _model_spread_pick(sim, home_line=home_line)
    if mkt == "total":
        over_q = quotes.get("total_over") or {}
        try:
            total_line = float(over_q.get("line"))
        except (TypeError, ValueError):
            total_line = None
        return _model_total_pick(sim, total_line=total_line)
    if mkt == "ml":
        return _model_ml_pick(sim, home=home, away=away)
    return None


def grade_market_quote(
    quote_key: str,
    quote: dict[str, Any],
    *,
    game: dict[str, Any],
    sim: dict[str, Any] | None,
    home: str,
    away: str,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    hp, ap = _home_away_points(game)
    out: dict[str, Any] = {
        "actual": "—",
        "result": None,
        "expected_roi_pct": None,
        "realized_roi_pct": None,
        "model_pick": None,
    }
    if hp is None or ap is None:
        return out

    row_side, group = _quote_side_key(quote_key, quote, home=home, away=away)
    grade_line = quote.get("line")
    if group == "spread" and grade_line is not None and row_side == "away_cover":
        try:
            grade_line = -float(grade_line)
        except (TypeError, ValueError):
            pass
    grade = grade_side(
        side=row_side,
        line=grade_line,
        home_points=hp,
        away_points=ap,
        home_line_scores=game.get("homeLineScores"),
        away_line_scores=game.get("awayLineScores"),
        market=str(quote.get("market") or ""),
    )
    out["actual"] = fmt_actual(row_side, grade.get("actual"), group)
    out["result"] = grade.get("result")

    market = str(quote.get("market") or "")
    model_pick = model_pick_for_market(market, quotes or {}, sim, home=home, away=away)
    out["model_pick"] = model_pick

    if model_pick and row_side == model_pick:
        model_prob = _model_prob_for_side(model_pick, quote, sim, home=home, away=away)
        if model_prob is not None:
            ev = ev_pct(model_prob, quote.get("price"))
            if ev is not None:
                out["expected_roi_pct"] = ev
                realized = realized_roi_from_result(ev / 100.0, out["result"])
                if realized is not None:
                    out["realized_roi_pct"] = round(realized * 100.0, 1)
    return out


def _prop_market_label(prop_key: str) -> str:
    return prop_display_name(prop_label_for_key(prop_key))


def prop_expected_roi_pct(
    row: dict[str, Any],
    projection: float,
    *,
    model_side: str | None = None,
) -> float | None:
    """Pregame expected ROI from log-normal (or normal) win prob × book price."""
    prop_key = str(row.get("prop_key") or prop_key_from_row(row) or "").strip()
    side = model_side or model_pick_side(projection, row.get("line"))
    if not side:
        return 0.0
    try:
        line_f = float(row.get("line"))
    except (TypeError, ValueError):
        return None
    quote = row.get("over") if side == "over" else row.get("under")
    price = (quote or {}).get("price") if isinstance(quote, dict) else None
    if price is None:
        return None
    analyzed = analyze_prop_line(
        projection=float(projection),
        line=line_f,
        prop_key=prop_key,
        position=str(row.get("position") or ""),
        over_price=(row.get("over") or {}).get("price") if isinstance(row.get("over"), dict) else None,
    )
    win_prob = side_win_prob((analyzed or {}).get("over_pct"), side)
    if win_prob is None:
        return None
    return ev_pct(float(win_prob), price)


def grade_prop_row(
    row: dict[str, Any],
    projection: float,
    *,
    game: dict[str, Any],
    box_stats: dict[str, dict[str, float]] | None = None,
    sport: str | None = None,
    year: int | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    player = str(row.get("player") or "")
    prop_key = str(row.get("prop_key") or prop_key_from_row(row) or "").strip()
    market = _prop_market_label(prop_key)
    model_side = model_pick_side(projection, row.get("line"))
    out: dict[str, Any] = {
        "actual": "—",
        "result": None,
        "expected_roi_pct": prop_expected_roi_pct(row, projection, model_side=model_side),
        "realized_roi_pct": None,
        "model_side": model_side,
    }

    stats = box_stats
    if stats is None:
        event_id = _resolve_event_id(
            game,
            home=str(game.get("home") or row.get("home") or ""),
            away=str(game.get("away") or row.get("away") or ""),
            sport=sport,
            year=year,
            week=week,
        )
        stats = _box_stats_for_event(event_id, str(sport or "")) if event_id else {}

    actual = stat_for_prop_key(player, prop_key, stats) if stats and player and prop_key else None
    if model_side:
        graded = grade_player_prop({**row, "side": model_side, "market": market}, actual, grade_side=model_side)
        out["actual"] = graded.get("actual") or ("—" if actual is None else f"{actual:g}")
        out["result"] = graded.get("result")
    elif actual is not None:
        out["actual"] = f"{actual:g}"

    exp = out.get("expected_roi_pct")
    if exp is not None and out.get("result"):
        realized = realized_roi_from_result(float(exp) / 100.0, out["result"])
        if realized is not None:
            out["realized_roi_pct"] = round(realized * 100.0, 1)
    return out
