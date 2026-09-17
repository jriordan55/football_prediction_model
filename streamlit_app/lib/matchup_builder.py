"""Build matchup detail payload without Node (full Python fallback)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import pandas as pd

from lib.games import lookup_completed_game, merge_completed
from lib.config import DATA_DIR
from lib.depth_chart import build_projected_starters
from lib.odds_cache import load_cached_lines
from lib.projection_results import grade_slate_rows
from lib.matchup_prop_enrich import (
    enrich_matchup_prop_rows,
    format_prop_projection,
    has_real_market_price,
    is_real_market_prop,
    resolve_prop_projection,
)
from lib.prop_results import grade_player_prop, model_pick_side, parse_espn_boxscore, stat_from_box
from lib.sdvs_profile import build_advanced_profile, team_tempo
from lib.nfl_projections import price_game
from lib.sp_projections import lookup_sp_team
from lib.team_logos import team_logo_url
from lib.team_registry import resolve_canonical, team_key, teams_match

FBS_TEAMS = DATA_DIR / "cfbd_fbs_teams.json"


@lru_cache(maxsize=1)
def _load_fbs_meta() -> dict[str, dict[str, Any]]:
    if not FBS_TEAMS.exists():
        return {}
    try:
        teams = json.loads(FBS_TEAMS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    abbr_map: dict[str, str] = {}
    for t in teams:
        school = t.get("school")
        if school:
            out[school] = {
                "conference": t.get("conference"),
                "mascot": t.get("mascot"),
                "color": t.get("color"),
            }
        abbr = str(t.get("abbreviation") or "").upper()
        if abbr and school:
            abbr_map[abbr] = school
    out["__abbr__"] = abbr_map
    return out


def _school_from_abbr(abbr: str | None) -> str | None:
    if not abbr:
        return None
    meta = _load_fbs_meta()
    return (meta.get("__abbr__") or {}).get(str(abbr).upper())


def _sp_rankings(*, season: int | None = None, display_week: int | None = None) -> dict[str, Any]:
    from lib.sp_projections import _load_sp_index
    from lib.team_registry import normalize_team_key

    teams = list(_load_sp_index(season, display_week).values())
    total = len(teams) or 1

    def _register(out: dict[str, int], label: str, rank: int) -> None:
        if not label:
            return
        raw = str(label).strip().lower()
        out[raw] = rank
        out[normalize_team_key(raw)] = rank

    def rank_map(field: str, *, ascending: bool = False) -> dict[str, int]:
        ordered = sorted(teams, key=lambda t: float(t.get(field, 0)), reverse=not ascending)
        out: dict[str, int] = {}
        for i, t in enumerate(ordered):
            rank = i + 1
            for label in (t.get("key"), t.get("team")):
                _register(out, str(label or ""), rank)
        return out

    sp_r = rank_map("sp")
    off_r = rank_map("spOff")
    def_r = rank_map("spDef", ascending=True)

    def pct(rank: int | None) -> int | None:
        if not rank:
            return None
        return round(((total - rank + 1) / total) * 100)

    def lookup(team_name: str, sp: dict[str, Any] | None, field: str) -> int | None:
        maps = {"sp": sp_r, "off": off_r, "def": def_r}
        m = maps.get(field, sp_r)
        for cand in (
            normalize_team_key(str(sp.get("team") if sp else "")),
            normalize_team_key(team_name),
            str(sp.get("key") if sp else "").lower(),
            str(sp.get("team") if sp else "").lower(),
        ):
            if cand and cand in m:
                return m[cand]
        return None

    return {"total": total, "sp": sp_r, "off": off_r, "def": def_r, "pct": pct, "lookup": lookup}


def _team_color(name: str) -> str:
    color = (_load_fbs_meta().get(name) or {}).get("color")
    if color and str(color).startswith("#"):
        return str(color)
    from lib.sport_context import get_sport, get_sport_config

    return str(get_sport_config(get_sport())["theme"]["accent_deep"])


def _prop_grade(roi: Any, *, play: bool = False) -> str:
    try:
        r = float(roi)
    except (TypeError, ValueError):
        return "—"
    score = min(9, max(1, round(r * 35 + 2)))
    if not play:
        score = min(score, 6)
    return f"{score}/9"


def _resolve_team_name(name: str, slate: pd.DataFrame, abbr: str | None = None) -> str:
    from lib.sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from lib.nfl_team_registry import resolve_canonical as resolve_nfl

        resolved = resolve_nfl(name) or resolve_nfl(abbr)
        if resolved:
            return resolved

    school = _school_from_abbr(abbr) or resolve_canonical(name)
    if school:
        return school
    n = str(name or "").strip()
    if not slate.empty:
        for col in ("home", "away"):
            for val in slate[col].dropna().unique():
                if teams_match(val, n):
                    return str(val)
    return n


def _props_for_matchup(
    home: str,
    away: str,
    *,
    slate: pd.DataFrame,
    year: int,
    week: int,
    event_id: str | None = None,
) -> pd.DataFrame:
    rows = _slate_rows_for_game(slate, home, away)
    props = rows[rows["group"] == "prop"] if "group" in rows.columns and not rows.empty else pd.DataFrame()
    if props.empty and not rows.empty and "player" in rows.columns:
        props = rows[rows["player"].notna() & (rows["player"].astype(str).str.len() > 1)]
    if not props.empty:
        return _filter_props_to_game(props, home, away, event_id=event_id)

    from lib.player_props_board_cache import load_player_props_board
    from lib.sport_context import SPORT_NFL, cache_sport, get_sport

    board = load_player_props_board(get_sport(), int(year), int(week))
    if not board.empty:
        game_props = _filter_props_to_game(board, home, away, event_id=event_id)
        if not game_props.empty:
            game_props = game_props.copy()
            game_props["year"] = int(year)
            game_props["week"] = int(week)
            return game_props

    if get_sport() != SPORT_NFL:
        return pd.DataFrame()

    from lib.nfl_draftkings_props import load_nfl_draftkings_props

    all_props = load_nfl_draftkings_props(cache_sport(), int(year), int(week))
    if all_props.empty:
        return all_props

    game_props = _filter_props_to_game(all_props, home, away, event_id=event_id)
    game_props["year"] = int(year)
    game_props["week"] = int(week)
    return game_props


def _filter_props_to_game(
    props: pd.DataFrame,
    home: str,
    away: str,
    *,
    event_id: str | None = None,
) -> pd.DataFrame:
    if props.empty:
        return props
    out = props.copy()
    if event_id and "event_id" in out.columns:
        eid = str(event_id).strip()
        if eid:
            out = out[out["event_id"].astype(str).str.strip() == eid]
    if out.empty:
        return out

    def _in_game(r: pd.Series) -> bool:
        h, a = r.get("home"), r.get("away")
        if not h or not a:
            return False
        return (teams_match(h, home) and teams_match(a, away)) or (
            teams_match(h, away) and teams_match(a, home)
        )

    return out.loc[out.apply(_in_game, axis=1)].copy()


def _slate_rows_for_game(slate: pd.DataFrame, home: str, away: str) -> pd.DataFrame:
    if slate.empty:
        return slate
    hk, ak = team_key(home), team_key(away)
    home_keys = slate["home"].map(lambda x: team_key(str(x or "")))
    away_keys = slate["away"].map(lambda x: team_key(str(x or "")))
    return slate.loc[(home_keys == hk) & (away_keys == ak)]


def _median_line(rows: pd.DataFrame, market_key: str) -> float | None:
    if rows.empty:
        return None
    mk = rows["market_key"].astype(str).str.lower() if "market_key" in rows.columns else pd.Series(dtype=str)
    mkt = rows["market"].astype(str).str.lower() if "market" in rows.columns else pd.Series(dtype=str)
    if market_key == "spread":
        sub = rows[(mk == "spreads") | (mkt == "spread")]
    else:
        sub = rows[(mk == "totals") | (mkt.str.contains("total", na=False))]
    if sub.empty:
        return None
    lines = pd.to_numeric(sub["line"], errors="coerce").dropna()
    if lines.empty:
        return None
    return round(float(lines.median()) * 2) / 2


def _market_lines_from_odds(home: str, away: str) -> dict[str, Any]:
    df = load_cached_lines()
    if df.empty:
        return {"spread": None, "total": None}

    sub = df[
        df.apply(lambda r: teams_match(r.get("home"), home) and teams_match(r.get("away"), away), axis=1)
    ]
    if sub.empty:
        return {"spread": None, "total": None}

    spread_line = None
    spreads = sub[sub["market_key"].astype(str).str.lower() == "spreads"]
    if not spreads.empty:
        home_rows = spreads[spreads["selection"].astype(str).apply(lambda s: teams_match(s, home))]
        if not home_rows.empty:
            spread_line = pd.to_numeric(home_rows["line"], errors="coerce").dropna()
            spread_line = round(float(spread_line.median()) * 2) / 2 if not spread_line.empty else None

    total_line = _median_line(sub, "total")
    return {"spread": spread_line, "total": total_line}


def _market_lines_from_slate(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"spread": None, "total": None}
    spread_row = rows[(rows.get("group") == "spread") | (rows.get("market") == "Spread")].head(1)
    total_row = rows[(rows.get("group") == "total") | (rows.get("market") == "Total")].head(1)
    spread_line = spread_row.iloc[0]["line"] if len(spread_row) else None
    total_line = total_row.iloc[0]["line"] if len(total_row) else None
    try:
        spread_line = round(float(spread_line) * 2) / 2 if spread_line is not None else None
    except (TypeError, ValueError):
        spread_line = None
    try:
        total_line = round(float(total_line) * 2) / 2 if total_line is not None else None
    except (TypeError, ValueError):
        total_line = None
    return {"spread": spread_line, "total": total_line}


def _team_profile(
    name: str,
    conference: str | None = None,
    *,
    sdvs_year: int = 2026,
    sp_season: int | None = None,
    sp_week: int | None = None,
) -> dict[str, Any]:
    yr = int(sp_season if sp_season is not None else sdvs_year)
    sp = lookup_sp_team(name, season=yr, display_week=sp_week)
    meta = (_load_fbs_meta().get(name) or {})
    ranks = _sp_rankings(season=yr, display_week=sp_week)
    key = str(sp.get("key", "")) if sp else ""
    sp_rank = ranks["lookup"](name, sp, "sp")
    off_rank = ranks["lookup"](name, sp, "off")
    def_rank = ranks["lookup"](name, sp, "def")
    tempo = team_tempo(name, sdvs_year=sdvs_year)
    return {
        "team": name,
        "logo": None,
        "conference": conference or meta.get("conference"),
        "mascot": meta.get("mascot"),
        "sp": {
            "sp": sp.get("sp") if sp else None,
            "spOff": sp.get("spOff") if sp else None,
            "spDef": sp.get("spDef") if sp else None,
            "rank": sp_rank,
            "rankTotal": ranks["total"],
            "offRank": off_rank,
            "offPct": ranks["pct"](off_rank),
            "defRank": def_rank,
            "defPct": ranks["pct"](def_rank),
        }
        if sp
        else None,
        "tempo": tempo,
    }


def _pregame_score_from_slate(rows: pd.DataFrame, priced: dict[str, Any]) -> dict[str, Any]:
    from lib.clv_report import _model_spread
    from lib.sp_projections import align_team_scores, team_scores_from_home_spread

    if priced.get("home_score") is not None and priced.get("away_score") is not None:
        spread = priced.get("spread")
        total = priced.get("total")
        home_score, away_score = align_team_scores(
            priced.get("home_score"),
            priced.get("away_score"),
            spread=spread,
            total=total,
        )
        return {
            "away_score": away_score,
            "home_score": home_score,
            "total": total,
            "spread": spread,
        }

    total = home_spread = None
    if not rows.empty:
        tr = rows[(rows.get("group") == "total") | (rows.get("market") == "Total")]
        sr = rows[(rows.get("group") == "spread") | (rows.get("market") == "Spread")]
        if len(tr):
            try:
                total = float(tr.iloc[0].get("modelProj"))
            except (TypeError, ValueError):
                total = None
        if len(sr):
            home_rows = sr[sr["side"].astype(str) == "home_cover"]
            pick = home_rows.iloc[0] if not home_rows.empty else sr.iloc[0]
            home_spread = _model_spread(pick)

    if total is not None and home_spread is not None:
        return team_scores_from_home_spread(float(home_spread), float(total))
    return priced


def _prop_row_pick_score(row: dict[str, Any], *, model_side: str | None = None) -> tuple:
    side = str(row.get("side") or row.get("sideLabel") or "").lower()
    side_match = 1 if model_side and model_side in side else 0
    play = 1 if bool(row.get("play")) else 0
    try:
        roi = float(row.get("roi") if row.get("roi") is not None else row.get("edgeDisplay") or 0)
    except (TypeError, ValueError):
        roi = 0.0
    return (side_match, play, roi)


def _dedupe_prop_rows_by_line(
    rows: list[dict[str, Any]],
    *,
    historical: bool,
) -> list[dict[str, Any]]:
    """One row per player + market + line (slate often has separate over/under rows)."""
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        if not is_real_market_prop(row):
            continue
        key = (row.get("player"), row.get("market"), row.get("line"))
        buckets.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []
    for group in buckets.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        if historical:
            projection = None
            for r in group:
                projection = resolve_prop_projection(r, historical=True)
                if projection is not None:
                    break
            model_side = model_pick_side(projection, group[0].get("line"))
            out.append(max(group, key=lambda r: _prop_row_pick_score(r, model_side=model_side)))
        else:
            plays = [r for r in group if r.get("play")]
            pool = plays if plays else group
            out.append(max(pool, key=lambda r: _prop_row_pick_score(r)))
    return out


def _grade_props_for_game(
    props: list[dict[str, Any]],
    game: dict[str, Any],
    *,
    box_stats: dict[str, dict[str, float]] | None = None,
    historical: bool = False,
) -> list[dict[str, Any]]:
    game_rows = [p for p in props if p.get("group") != "prop" and str(p.get("category") or "") == "game"]
    prop_rows = [p for p in props if p.get("group") == "prop" or p.get("player")]

    graded_game: list[dict[str, Any]] = []
    if (
        game
        and game.get("homePoints") is not None
        and game.get("awayPoints") is not None
        and game_rows
    ):
        graded_game = grade_slate_rows(game_rows, game)
    graded_map = {(r.get("player"), r.get("market"), r.get("side")): r for r in graded_game}

    prop_rows = _dedupe_prop_rows_by_line(prop_rows, historical=historical)

    out = []
    for row in prop_rows:
        if not is_real_market_prop(row):
            continue
        projection = resolve_prop_projection(row, historical=historical)
        if projection is None:
            continue
        item = {
            "grade": _prop_grade(row.get("roi"), play=bool(row.get("play"))),
            "player": row.get("player"),
            "position": row.get("position"),
            "team": row.get("team") or row.get("teamLabel"),
            "teamLogo": row.get("teamLogo"),
            "prop": row.get("market"),
            "projection": projection,
            "edgePct": row.get("edgeDisplay"),
            "side": str(row.get("sideLabel") or row.get("side") or "").title(),
            "price": row.get("price"),
            "book": row.get("book"),
            "play": bool(row.get("play")),
            "line": row.get("line"),
            "group": row.get("group"),
            "sideRaw": row.get("side"),
        }
        if box_stats and row.get("player"):
            actual = stat_from_box(str(row.get("player")), str(row.get("market")), box_stats)
            model_side = model_pick_side(projection, row.get("line")) if historical else None
            grade_side = model_side
            if historical and not grade_side:
                grade_side = str(row.get("side") or row.get("sideLabel") or "").lower() or None
            item = grade_player_prop(
                {**item, "side": row.get("side"), "sideLabel": row.get("sideLabel")},
                actual,
                grade_side=grade_side,
            )
            if historical and grade_side:
                item["side"] = grade_side.title()
                item["modelSide"] = grade_side
        if historical:
            if item.get("actual") in (None, "—") or item.get("result") is None:
                continue
            if not has_real_market_price(row):
                continue
        out.append(item)

    for g in graded_game:
        out.append(
            {
                "grade": "—",
                "player": None,
                "prop": g.get("market"),
                "projection": g.get("modelProj"),
                "edgePct": g.get("edgeDisplay"),
                "side": str(g.get("sideLabel") or g.get("side") or "").title(),
                "line": g.get("line"),
                "actual": g.get("actual"),
                "result": g.get("result"),
                "play": bool(g.get("play")),
                "group": g.get("group"),
            }
        )
    return out


def build_matchup_fallback(
    home: str,
    away: str,
    *,
    board_row: pd.Series | dict,
    slate: pd.DataFrame,
    year: int,
    week: int,
    historical: bool = False,
) -> dict[str, Any]:
    if isinstance(board_row, pd.Series):
        board_row = board_row.to_dict()

    home = _resolve_team_name(home, slate, board_row.get("home_abbr"))
    away = _resolve_team_name(away, slate, board_row.get("away_abbr"))
    home_conf = board_row.get("home_conference")
    away_conf = board_row.get("away_conference")

    rows = _slate_rows_for_game(slate, home, away)
    priced = price_game(home, away, season=int(year), display_week=int(week)) or {}
    pregame = _pregame_score_from_slate(rows, priced) if historical else priced
    if pregame.get("home_score") is not None and pregame.get("away_score") is not None:
        from lib.sp_projections import align_team_scores

        hs, aw = align_team_scores(
            pregame.get("home_score"),
            pregame.get("away_score"),
            spread=pregame.get("spread"),
            total=pregame.get("total"),
        )
        pregame = {**pregame, "home_score": hs, "away_score": aw}

    completed = lookup_completed_game(year, home, away)
    board_completed = bool(board_row.get("completed"))
    completed = merge_completed(
        completed,
        home,
        away,
        home_pts=board_row.get("home_score"),
        away_pts=board_row.get("away_score"),
        status_completed=board_completed,
    )
    from lib.game_status import game_is_final, game_past_kickoff

    is_final = game_is_final(
        completed=bool(completed and completed.get("completed")),
        home_pts=completed.get("homePoints") if completed else None,
        away_pts=completed.get("awayPoints") if completed else None,
        start_date=board_row.get("date") or (completed.get("startDate") if completed else None),
    )

    if is_final:
        from lib.game_line_history import opening_closing_lines

        market = opening_closing_lines(
            home,
            away,
            event_id=board_row.get("event_id"),
            slate_rows=rows,
            year=year,
            kickoff=board_row.get("date"),
        )
    else:
        market = _market_lines_from_slate(rows)
        if market["spread"] is None and market["total"] is None:
            odds_market = _market_lines_from_odds(home, away)
            market = {
                "spread": market["spread"] or odds_market["spread"],
                "total": market["total"] or odds_market["total"],
            }
        if market["spread"] is None and priced.get("spread") is not None:
            market["spread"] = priced["spread"]
        if market["total"] is None and priced.get("total") is not None:
            market["total"] = priced["total"]

    archive_props = is_final or (int(week) == 0 and game_past_kickoff(board_row.get("date")))

    props = _props_for_matchup(
        home,
        away,
        slate=slate,
        year=year,
        week=week,
        event_id=board_row.get("event_id"),
    )

    plus_ev_props: list[dict[str, Any]] = []
    if archive_props:
        box_stats = None
        event_id = board_row.get("event_id")
        if event_id:
            try:
                from lib.espn_client import fetch_game_summary

                summary = fetch_game_summary(str(event_id))
                box_stats = parse_espn_boxscore(summary)
            except Exception:
                box_stats = None
        slate_rows = rows.to_dict("records") if not rows.empty else props.to_dict("records")
        prop_only = [
            r
            for r in slate_rows
            if (r.get("group") == "prop" or (r.get("player") and str(r.get("player")).strip()))
            and is_real_market_prop(r)
        ]
        if not prop_only and not props.empty:
            prop_only = [r for _, r in props.iterrows() if is_real_market_prop(r.to_dict())]
        enriched = enrich_matchup_prop_rows(
            prop_only,
            home=home,
            away=away,
            board_row=board_row if isinstance(board_row, dict) else dict(board_row),
            historical=True,
        )
        plus_ev_props = _grade_props_for_game(enriched, completed, box_stats=box_stats, historical=True)
        plus_ev_props = [p for p in plus_ev_props if p.get("group") == "prop" or p.get("player")][:40]
    else:
        prop_only = [
            r.to_dict()
            for _, r in props.iterrows()
            if is_real_market_prop(r.to_dict())
        ]
        enriched_live = enrich_matchup_prop_rows(
            prop_only,
            home=home,
            away=away,
            board_row=board_row if isinstance(board_row, dict) else dict(board_row),
            historical=False,
        )
        sort_col = "roi" if "roi" in props.columns else "edgeDisplay" if "edgeDisplay" in props.columns else None
        pool = pd.DataFrame(enriched_live)
        if sort_col and not pool.empty and sort_col in pool.columns:
            play_props = pool[pool["play"] == True] if "play" in pool.columns else pool.iloc[0:0]  # noqa: E712
            if not play_props.empty:
                pool = play_props
            elif "roi" in pool.columns:
                try:
                    pos = pool[pool["roi"].astype(float) > 0.005]
                    if not pos.empty:
                        pool = pos
                except (TypeError, ValueError):
                    pass
            pool = pool.sort_values(sort_col, ascending=False)
        for _, r in pool.head(40).iterrows():
            play = bool(r.get("play"))
            roi = r.get("roi")
            row_dict = r.to_dict()
            projection = resolve_prop_projection(row_dict, historical=False)
            if projection is None:
                try:
                    projection = round(float(row_dict.get("line")), 1)
                except (TypeError, ValueError):
                    continue
            team_name = r.get("team") or r.get("teamLabel")
            if not team_name or not (
                teams_match(team_name, home) or teams_match(team_name, away)
            ):
                continue
            team_logo = team_logo_url(
                str(team_name or ""),
                fallback=str(r.get("teamLogo") or ""),
            )
            try:
                roi_f = float(roi) if roi is not None else None
            except (TypeError, ValueError):
                roi_f = None
            expected_roi = r.get("expected_roi")
            try:
                expected_roi_f = float(expected_roi) if expected_roi is not None else roi_f
            except (TypeError, ValueError):
                expected_roi_f = roi_f
            plus_ev_props.append(
                {
                    "grade": _prop_grade(roi, play=play),
                    "player": r.get("player"),
                    "position": r.get("position"),
                    "team": team_name,
                    "teamLogo": team_logo or r.get("teamLogo"),
                    "prop": r.get("market"),
                    "projection": projection,
                    "modelProj": projection,
                    "edgePct": r.get("edgeDisplay") or r.get("edge"),
                    "side": str(r.get("sideLabel") or r.get("side") or "").title(),
                    "price": r.get("price"),
                    "book": r.get("book"),
                    "play": play,
                    "line": r.get("line"),
                    "roi": roi_f,
                    "expected_roi": expected_roi_f,
                    "winProb": r.get("winProb"),
                }
            )

    sdvs_year = int(year)
    sp_week = 1 if int(week) == 0 else int(week)
    home_starters = build_projected_starters(home)
    away_starters = build_projected_starters(away)

    meta = {
        "home": home,
        "away": away,
        "label": f"{away} @ {home}",
        "week": week,
        "year": year,
        "startDate": board_row.get("date"),
        "venue": board_row.get("venue"),
        "venue_city": board_row.get("venue_city"),
        "venue_state": board_row.get("venue_state"),
        "indoor": board_row.get("indoor"),
        "lat": board_row.get("lat"),
        "lon": board_row.get("lon"),
        "neutral": False,
        "homeAbbr": board_row.get("home_abbr"),
        "awayAbbr": board_row.get("away_abbr"),
        "historical": archive_props,
        "final": is_final,
        "event_id": board_row.get("event_id"),
        "home_score": completed.get("homePoints") if completed else None,
        "away_score": completed.get("awayPoints") if completed else None,
    }

    if plus_ev_props:
        from lib.projection_archive import archive_matchup_props

        archive_matchup_props(
            plus_ev_props,
            meta=meta,
            year=int(year),
            week=int(week),
            historical=archive_props,
            tab="Matchup Detail",
        )

    return {
        "teamColors": {"home": _team_color(home), "away": _team_color(away)},
        "meta": meta,
        "logos": {
            "home": team_logo_url(home, fallback=str(board_row.get("home_logo") or "")),
            "away": team_logo_url(away, fallback=str(board_row.get("away_logo") or "")),
        },
        "projectedScore": {
            "awayScore": pregame.get("away_score"),
            "homeScore": pregame.get("home_score"),
            "total": pregame.get("total"),
            "spread": pregame.get("spread"),
        },
        "actualScore": {
            "awayScore": completed.get("awayPoints"),
            "homeScore": completed.get("homePoints"),
            "total": completed.get("total"),
            "margin": completed.get("margin"),
        }
        if is_final and completed
        else None,
        "marketLines": {
            **market,
            "homeMl": None,
            "awayMl": None,
        },
        "teamProfiles": {
            "home": {
                **_team_profile(
                    home, home_conf, sdvs_year=sdvs_year, sp_season=sdvs_year, sp_week=sp_week,
                ),
                "logo": team_logo_url(home, fallback=str(board_row.get("home_logo") or "")),
            },
            "away": {
                **_team_profile(
                    away, away_conf, sdvs_year=sdvs_year, sp_season=sdvs_year, sp_week=sp_week,
                ),
                "logo": team_logo_url(away, fallback=str(board_row.get("away_logo") or "")),
            },
        },
        "starters": {
            "home": home_starters,
            "away": away_starters,
        },
        "plusEvProps": plus_ev_props,
        "advancedProfile": build_advanced_profile(home, away, sdvs_year=sdvs_year),
    }
