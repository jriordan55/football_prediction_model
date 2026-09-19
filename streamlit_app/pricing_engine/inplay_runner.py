"""In-play pricing runner — PBP snapshots for all live games."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lib.espn_client import fetch_game_summary
from lib.espn_live import derive_situation, discover_live_games, fetch_game_plays
from lib.fourc_odds_client import (
    fetch_board,
    fetch_game_player_props,
    parse_board_game_quotes,
    _game_names,
)
from lib.inplay_storage import append_rows, load_seen_plays, save_seen_plays
from lib.pbp_snapshots import backfill_missing_play_snapshots, ensure_total_quotes, record_play_snapshot
from lib.pricing_history import (
    METHODOLOGY,
    SCHEMA_VERSION,
    load_pregame_capture_any_week,
    save_capture,
)
from lib.prop_results import parse_espn_boxscore
from lib.sport_context import SPORT_CFB, SPORT_NFL, get_sport_config, set_sport
from pricing_engine.constants import DEFAULT_SIMS
from pricing_engine.fourc_board import _find_board_game
from pricing_engine.inplay_context import build_play_context
from pricing_engine.inplay_markets import game_market_rows, prop_market_rows
from pricing_engine.situation_model import parse_play_situation
from pricing_engine.inplay_props import enrich_live_prop_row
from pricing_engine.simulator import run_matchup_simulation
from pricing_engine.ui.props_table import build_prop_projection_map, row_prop_key


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_lines(quotes: dict[str, dict[str, Any]], pregame: dict[str, Any] | None) -> tuple[float | None, float | None]:
    spread = quotes.get("spread_home", {}).get("line")
    if spread is None and quotes.get("spread_away", {}).get("line") is not None:
        try:
            spread = -float(quotes["spread_away"]["line"])
        except (TypeError, ValueError):
            spread = None
    total = quotes.get("total_over", {}).get("line") or quotes.get("total_under", {}).get("line")
    if spread is None and pregame:
        spread = pregame.get("spread") or pregame.get("market_spread")
    if total is None and pregame:
        total = pregame.get("total") or pregame.get("market_total")
    try:
        spread = float(spread) if spread is not None else None
    except (TypeError, ValueError):
        spread = None
    try:
        total = float(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    return spread, total


def _load_odds_and_props(
    sport: str,
    home: str,
    away: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    quotes: dict[str, dict[str, Any]] = {}
    props: list[dict[str, Any]] = []
    board_game: dict[str, Any] | None = None
    try:
        board = fetch_board(sport=sport)
    except Exception:
        board = {}
    game = _find_board_game(board, home, away, sport=sport) if board else None
    if game:
        board_game = game
        quotes = parse_board_game_quotes(game)
        gid = str(game.get("id") or "")
        if gid:
            home_n, away_n, event = _game_names(game)
            try:
                props = fetch_game_player_props(gid, sport=sport, home=home_n, away=away_n, event=event)
            except Exception:
                props = []
    from lib.pricing_history import prepare_props_for_ui

    return quotes, prepare_props_for_ui(props), board_game


def _pregame_prop_map(pregame: dict[str, Any] | None, *, year: int, week: int) -> dict[tuple[str, str, str], float]:
    if not pregame:
        return {}
    props = pregame.get("_props") or pregame.get("props") or []
    return build_prop_projection_map(props, year=year, week=week)


def _pregame_sim(
    sport: str,
    home: str,
    away: str,
    *,
    year: int,
    week: int,
    pregame: dict[str, Any] | None,
    spread: float | None,
    total: float | None,
) -> dict[str, Any] | None:
    if pregame and pregame.get("sim"):
        return pregame["sim"]
    return run_matchup_simulation(
        sport, home, away,
        season=year, week=week,
        market_spread=spread, market_total=total,
        live_game=None, n_sims=DEFAULT_SIMS,
    )


def capture_game(
    sport: str,
    year: int,
    week: int,
    *,
    event_id: str,
    home: str,
    away: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Process new PBP events for one live game; append CSV rows + inplay JSON capture."""
    set_sport(sport)
    captured_at = captured_at or _utc_now_iso()
    pregame, resolved_week = load_pregame_capture_any_week(
        sport, year, home, away, week_hint=week,
    )
    if resolved_week is not None:
        week = int(resolved_week)
    quotes, props, board_game = _load_odds_and_props(sport, home, away)
    spread, total = _resolve_lines(quotes, pregame)
    quotes = ensure_total_quotes(quotes, game=board_game, pregame_total=total)
    pregame_sim = _pregame_sim(sport, home, away, year=year, week=week, pregame=pregame, spread=spread, total=total)
    pregame_props = _pregame_prop_map(pregame, year=year, week=week)

    summary = fetch_game_summary(event_id, sport=sport)
    plays = fetch_game_plays(event_id, sport=sport)
    if not plays:
        from lib.espn_live import _plays_from_drives

        plays = _plays_from_drives(summary)
    situation = derive_situation(summary, {"status": {"state": "in"}}, plays)
    from lib.espn_live import _team_yards

    away_yards, home_yards = _team_yards(summary)
    box_stats = parse_espn_boxscore(summary)
    wp = (summary.get("winprobability") or [{}])[-1] if summary.get("winprobability") else {}
    win_prob_home = wp.get("homeWinPercentage")
    if win_prob_home is not None:
        try:
            win_prob_home = float(win_prob_home)
            if win_prob_home <= 1.0:
                win_prob_home *= 100.0
        except (TypeError, ValueError):
            win_prob_home = None

    seen = load_seen_plays(event_id)
    new_plays = [p for p in plays if str(p.get("id") or "") and str(p.get("id")) not in seen]
    if not new_plays and plays:
        new_plays = [plays[-1]]

    all_rows: list[dict[str, Any]] = []
    last_play: dict[str, Any] | None = None
    sim: dict[str, Any] | None = pregame_sim
    live_props: list[dict[str, Any]] = []
    latest_quotes = quotes
    latest_spread = spread
    latest_total = total
    latest_board = board_game

    for play in new_plays:
        pid = str(play.get("id") or f"tick-{captured_at}")
        quotes, _, board_game = _load_odds_and_props(sport, home, away)
        spread, total = _resolve_lines(quotes, pregame)
        quotes = ensure_total_quotes(quotes, game=board_game, pregame_total=total)
        latest_quotes, latest_spread, latest_total, latest_board = quotes, spread, total, board_game
        period = int(play.get("period") or situation.get("period") or 1)
        clock = play.get("clock") or situation.get("clock")
        play_sit = parse_play_situation(play, situation, home=home, away=away)
        ml_home_q = quotes.get("ml_home") or {}
        live_game = {
            "status": "in",
            "period": period,
            "clock": clock,
            "home_score": play_sit.home_score,
            "away_score": play_sit.away_score,
            "win_prob_home": win_prob_home,
            "market_ml_home": ml_home_q.get("price"),
            "situation": play_sit,
            "down": play_sit.down,
            "distance": play_sit.distance,
            "yard_line": play_sit.yard_line,
            "possession": play_sit.possession_text,
        }
        sim = run_matchup_simulation(
            sport, home, away,
            season=year, week=week,
            market_spread=spread, market_total=total,
            live_game=live_game, n_sims=DEFAULT_SIMS,
        )
        if sport == SPORT_CFB:
            live_props = props
        else:
            live_props = []
            for prop in props:
                pk = row_prop_key(prop)
                pre_proj = pregame_props.get(
                    (str(prop.get("player") or ""), str(prop.get("prop_key") or pk or ""), str(prop.get("line") or ""))
                )
                live_props.append(
                    enrich_live_prop_row(
                        prop,
                        box_stats=box_stats,
                        pregame_proj=pre_proj,
                        period=period,
                        clock_seconds=play_sit.clock_seconds or situation.get("clockSeconds"),
                        sit=play_sit,
                        home=home,
                        away=away,
                        live_sim=sim,
                        pregame_sim=pregame_sim,
                        home_team_box=home_yards,
                        away_team_box=away_yards,
                    )
                )
        pregame_bundle = {"sim": pregame_sim, "odds_quotes": (pregame or {}).get("_odds_quotes") or []}
        play_context = build_play_context(
            play=play,
            situation=situation,
            away_yards=away_yards,
            home_yards=home_yards,
            win_prob_home=win_prob_home,
            pregame=pregame,
        )
        all_rows.extend(
            game_market_rows(
                sport=sport,
                year=year,
                week=week,
                event_id=event_id,
                home=home,
                away=away,
                play=play,
                quotes=quotes,
                sim=sim,
                pregame=pregame_bundle,
                captured_at=captured_at,
                play_context=play_context,
            )
        )
        all_rows.extend(
            prop_market_rows(
                sport=sport,
                year=year,
                week=week,
                event_id=event_id,
                home=home,
                away=away,
                play=play,
                props=live_props,
                captured_at=captured_at,
                play_context=play_context,
            )
        )
        record_play_snapshot(
            sport=sport,
            event_id=str(event_id),
            play=play,
            quotes=quotes,
            sim=sim,
            home=home,
            away=away,
            source="daemon",
            captured_at=captured_at,
            game=board_game,
            pregame_total=total,
        )
        seen.add(pid)
        last_play = play

    backfill_missing_play_snapshots(
        sport=sport,
        year=year,
        week=week,
        event_id=str(event_id),
        home=home,
        away=away,
        quotes=latest_quotes,
        market_spread=latest_spread,
        market_total=latest_total,
    )

    csv_path = append_rows(all_rows)

    game_record = {
        "home": home,
        "away": away,
        "event_id": event_id,
        "pregame_captured_at": (pregame or {}).get("_captured_at"),
        "pregame_capture_type": (pregame or {}).get("_capture_type"),
        "new_plays": len(new_plays),
        "rows_written": len(all_rows),
        "last_play": last_play,
        "odds_quotes": [
            {**q, "market_key": q.get("market"), "selection": q.get("selection")}
            for q in quotes.values()
        ],
        "sim": sim,
        "props": live_props,
    }
    save_capture(
        {
            "schema_version": SCHEMA_VERSION,
            "sport": sport,
            "year": year,
            "week": week,
            "captured_at": captured_at,
            "capture_type": "inplay",
            "methodology": METHODOLOGY,
            "source": "inplay_runner",
            "games": [game_record],
        }
    )

    save_seen_plays(
        event_id,
        seen,
        meta={"home": home, "away": away, "sport": sport, "last_captured_at": captured_at},
    )

    xlsx_path: str | None = None
    from pricing_engine.pit import game_is_final

    comps = (summary.get("header") or {}).get("competitions") or [{}]
    comp = comps[0] if comps else {}
    competitors = comp.get("competitors") or []
    home_score = away_score = None
    for c in competitors:
        if str(c.get("homeAway") or "").lower() == "home":
            home_score = c.get("score")
        elif str(c.get("homeAway") or "").lower() == "away":
            away_score = c.get("score")
    final_game = {
        "homePoints": home_score,
        "awayPoints": away_score,
        "status": comp.get("status"),
        "completed": str((comp.get("status") or {}).get("type", {}).get("state") or "").lower() in ("post", "final"),
    }
    if game_is_final(final_game):
        try:
            from lib.pbp_export import export_pbp_game_xlsx

            exported = export_pbp_game_xlsx(
                sport=sport,
                event_id=str(event_id),
                home=home,
                away=away,
                year=year,
                week=week,
                quotes=latest_quotes,
                pregame=pregame,
                pregame_sim=pregame_sim,
            )
            if exported:
                xlsx_path = str(exported)
        except Exception:
            pass

    return {
        "event_id": event_id,
        "home": home,
        "away": away,
        "new_plays": len(new_plays),
        "rows_written": len(all_rows),
        "csv_path": str(csv_path),
        "xlsx_path": xlsx_path,
    }


def run_sport_week(sport: str, year: int, week: int) -> list[dict[str, Any]]:
    set_sport(sport)
    results: list[dict[str, Any]] = []
    captured_at = _utc_now_iso()
    live_games = discover_live_games(sport, year=year, default_week=week)
    for game in live_games:
        eid = game.get("event_id")
        if not eid:
            continue
        game_year = int(game.get("year") or year)
        game_week = int(game.get("week") or week)
        try:
            results.append(
                capture_game(
                    sport, game_year, game_week,
                    event_id=eid,
                    home=game["home"],
                    away=game["away"],
                    captured_at=captured_at,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "event_id": eid,
                    "home": game.get("home"),
                    "away": game.get("away"),
                    "error": str(exc),
                }
            )
    return results


def run_all() -> dict[str, Any]:
    from lib.sport_context import init_sport

    init_sport()
    summary: dict[str, Any] = {"captured_at": _utc_now_iso(), "sports": {}}
    for sport in (SPORT_CFB, SPORT_NFL):
        set_sport(sport)
        cfg = get_sport_config(sport)
        year = int(cfg.get("default_year") or 2026)
        week = int(cfg.get("default_week") or 1)
        games = run_sport_week(sport, year, week)
        summary["sports"][sport] = {
            "year": year,
            "week_hint": week,
            "live_games_found": len(games),
            "games": games,
        }
    return summary
