"""Persist pricing-engine projections, odds, and lines with timestamps."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .config import DATA_DIR, SNAPSHOT_DIR, csv_log_enabled
from .sport_context import SPORT_CFB, SPORT_NFL, sport_disk_tag

PRICING_HISTORY_DIR = DATA_DIR / "pricing_history"
SCHEMA_VERSION = 1
METHODOLOGY = {
    "version": 3,
    "game_sim": "pricing_engine.simulator.run_matchup_simulation",
    "props": "pricing_engine.ui.props_table.compute_prop_projection",
    "pit": "pricing_engine.pit",
    "sims": 3000,
}

_CARD_QUOTE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("away_spread", "spreads", "away"),
    ("home_spread", "spreads", "home"),
    ("away_spread_fd", "spreads", "away"),
    ("home_spread_fd", "spreads", "home"),
    ("over", "totals", "over"),
    ("under", "totals", "under"),
    ("over_fd", "totals", "over"),
    ("under_fd", "totals", "under"),
    ("away_team_over", "team_totals", "away"),
    ("home_team_over", "team_totals", "home"),
    ("away_team_over_fd", "team_totals", "away"),
    ("home_team_over_fd", "team_totals", "home"),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def week_dir(sport: str, year: int, week: int) -> Path:
    return PRICING_HISTORY_DIR / sport_disk_tag(sport) / str(int(year)) / f"w{int(week)}"


def captures_dir(sport: str, year: int, week: int) -> Path:
    return week_dir(sport, year, week) / "captures"


def manifest_path() -> Path:
    return PRICING_HISTORY_DIR / "manifest.json"


def _safe_stamp(ts: str) -> str:
    return ts.replace(":", "").replace("+", "p").replace(".", "-")


def capture_filename(captured_at: str, capture_type: str) -> str:
    return f"{_safe_stamp(captured_at)}_{capture_type}.json"


def _load_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "captures": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("captures", [])
            return data
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return {"schema_version": SCHEMA_VERSION, "captures": []}


def _write_manifest(data: dict[str, Any]) -> None:
    manifest_path().parent.mkdir(parents=True, exist_ok=True)
    manifest_path().write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def register_capture(meta: dict[str, Any]) -> None:
    manifest = _load_manifest()
    captures: list[dict[str, Any]] = manifest["captures"]
    key = (
        meta.get("sport"),
        meta.get("year"),
        meta.get("week"),
        meta.get("captured_at"),
        meta.get("capture_type"),
    )
    captures = [c for c in captures if (
        c.get("sport"), c.get("year"), c.get("week"), c.get("captured_at"), c.get("capture_type"),
    ) != key]
    captures.append(meta)
    captures.sort(key=lambda c: str(c.get("captured_at") or ""))
    manifest["captures"] = captures
    manifest["updated_at"] = _utc_now_iso()
    _write_manifest(manifest)


def save_capture(payload: dict[str, Any]) -> Path:
    sport = str(payload["sport"])
    year = int(payload["year"])
    week = int(payload["week"])
    captured_at = str(payload.get("captured_at") or _utc_now_iso())
    capture_type = str(payload.get("capture_type") or "snapshot")

    out_dir = captures_dir(sport, year, week)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = capture_filename(captured_at, capture_type)
    path = out_dir / fname
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    register_capture(
        {
            "sport": sport,
            "year": year,
            "week": week,
            "captured_at": captured_at,
            "capture_type": capture_type,
            "path": str(path.relative_to(PRICING_HISTORY_DIR)),
            "game_count": len(payload.get("games") or []),
            "quote_count": sum(len(g.get("odds_quotes") or []) for g in (payload.get("games") or [])),
            "prop_count": sum(len(g.get("props") or []) for g in (payload.get("games") or [])),
            "source": payload.get("source"),
        }
    )
    _append_csv_log(payload)
    return path


def _append_csv_log(payload: dict[str, Any]) -> None:
    if not csv_log_enabled():
        return
    from .csv_log import append_csv_log

    rows: list[dict[str, Any]] = []
    for game in payload.get("games") or []:
        sb = (game.get("sim") or {}).get("scoreboard") or {}
        rows.append(
            {
                "matchup": f'{game.get("away")} @ {game.get("home")}',
                "home": game.get("home"),
                "away": game.get("away"),
                "event_id": game.get("event_id"),
                "spread_line": (game.get("lines") or {}).get("spread"),
                "total_line": (game.get("lines") or {}).get("total"),
                "proj_home": sb.get("home_mean"),
                "proj_away": sb.get("away_mean"),
                "total_proj": sb.get("total_mean"),
                "captured_at": payload.get("captured_at"),
                "capture_type": payload.get("capture_type"),
                "quote_count": len(game.get("odds_quotes") or []),
                "prop_count": len(game.get("props") or []),
            }
        )
    if rows:
        append_csv_log(
            rows,
            record_type="pricing_capture",
            source="pricing_engine",
            year=int(payload["year"]),
            week=int(payload["week"]),
            extra={"capture_type": payload.get("capture_type"), "sport": payload.get("sport")},
        )


def load_game_cards_file(sport: str, year: int, week: int) -> list[dict[str, Any]]:
    cache_dir = DATA_DIR / "game_board_cache"
    tag = sport_disk_tag(sport)
    candidates = [
        cache_dir / f"game_cards_{tag}_{int(year)}_w{int(week)}.json",
        cache_dir / f"game_cards_{int(year)}_w{int(week)}.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cards = payload.get("cards") or []
            if isinstance(cards, list) and cards:
                return cards
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            continue
    return []


def _props_board_paths(sport: str, year: int, week: int) -> list[Path]:
    cache_dir = DATA_DIR / "odds_api_cache"
    tag = "ncaaf" if sport == SPORT_CFB else "nfl"
    patterns = [
        f"player_props_board_{tag}_{year}_w{week}_v*.json",
        f"player_props_board_{tag}_{year}_w{week}.json",
    ]
    found: list[Path] = []
    for pat in patterns:
        found.extend(sorted(cache_dir.glob(pat), reverse=True))
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def load_props_board(sport: str, year: int, week: int) -> list[dict[str, Any]]:
    for path in _props_board_paths(sport, year, week):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows") if isinstance(payload, dict) else payload
            if isinstance(rows, list) and rows:
                return rows
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            continue
    return []


def quotes_from_game_card(card: dict[str, Any]) -> list[dict[str, Any]]:
    home = str(card.get("home") or "")
    away = str(card.get("away") or "")
    event = str(card.get("matchup") or card.get("label") or f"{away} @ {home}")
    event_id = card.get("event_id")
    kickoff = card.get("start") or card.get("startDate")
    quotes: list[dict[str, Any]] = []

    for field, market_key, side_hint in _CARD_QUOTE_FIELDS:
        q = card.get(field)
        if not isinstance(q, dict):
            continue
        line = q.get("line")
        price = q.get("price")
        if line is None and price in (None, "", "—", "\u2014"):
            continue
        book_id = q.get("book_id") or q.get("book")
        selection = home if side_hint == "home" else away if side_hint == "away" else side_hint.title()
        quotes.append(
            {
                "event": event,
                "event_id": event_id,
                "home": home,
                "away": away,
                "commence_time": kickoff,
                "market_key": market_key,
                "selection": selection,
                "line": line,
                "price": price,
                "book_id": book_id,
                "source": "game_board_cache",
            }
        )
    return quotes


def _parse_float(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def lines_from_card(card: dict[str, Any]) -> dict[str, float | None]:
    spread = _parse_float(card.get("spread_line"))
    if spread is None:
        hs = card.get("home_spread") or {}
        spread = _parse_float(hs.get("line"))
    total = _parse_float(card.get("total_line"))
    if total is None:
        over = card.get("over") or {}
        total = _parse_float(over.get("line"))
    return {"spread": spread, "total": total}


def props_for_matchup(
    props_rows: list[dict[str, Any]],
    *,
    home: str,
    away: str,
) -> list[dict[str, Any]]:
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    out: list[dict[str, Any]] = []
    for row in props_rows:
        rh = str(row.get("home") or "")
        ra = str(row.get("away") or "")
        if not rh or not ra:
            continue
        if cfb_match(rh, home) and cfb_match(ra, away):
            out.append(row)
        elif nfl_match(rh, home) and nfl_match(ra, away):
            out.append(row)
    return out


def _normalize_prop_row(row: dict[str, Any]) -> dict[str, Any]:
    side = str(row.get("side") or "").lower()
    price = row.get("price")
    over = under = None
    if side == "over":
        over = {"book_id": row.get("book_id") or row.get("book"), "price": price, "line": row.get("line")}
    elif side == "under":
        under = {"book_id": row.get("book_id") or row.get("book"), "price": price, "line": row.get("line")}
    return {
        "player": row.get("player"),
        "prop_key": row.get("propKey") or row.get("prop_key"),
        "stat": row.get("market") or row.get("stat"),
        "line": row.get("line"),
        "home": row.get("home"),
        "away": row.get("away"),
        "over": over,
        "under": under,
        "book_id": row.get("book_id") or row.get("book"),
        "stored_model_proj": row.get("modelProj") or row.get("projection"),
    }


def _dedupe_props(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[tuple, dict[str, Any]] = {}
    for raw in rows:
        pkey = (
            str(raw.get("player") or ""),
            str(raw.get("propKey") or raw.get("prop_key") or ""),
            str(raw.get("line") or ""),
            str(raw.get("book_id") or raw.get("book") or ""),
            str(raw.get("side") or ""),
        )
        norm = _normalize_prop_row(raw)
        key = (norm["player"], norm["prop_key"], str(norm.get("line") or ""))
        if key not in keyed:
            keyed[key] = norm
        else:
            if norm.get("over"):
                keyed[key]["over"] = norm["over"]
            if norm.get("under"):
                keyed[key]["under"] = norm["under"]
    return list(keyed.values())


def load_snapshot_df(dates: Iterable[str] | None = None) -> pd.DataFrame:
    if not SNAPSHOT_DIR.exists():
        return pd.DataFrame()
    files = sorted(SNAPSHOT_DIR.glob("*.jsonl"))
    if dates:
        want = {str(d) for d in dates}
        files = [f for f in files if f.stem in want]
    rows: list[dict[str, Any]] = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "snapshot_at" in df.columns:
        df["snapshot_at"] = pd.to_datetime(df["snapshot_at"], utc=True, errors="coerce")
    if "commence_time" in df.columns:
        df["commence_time"] = pd.to_datetime(df["commence_time"], utc=True, errors="coerce")
    return df


def _matchup_key(home: str, away: str) -> tuple[str, str]:
    from lib.nfl_team_registry import team_key as nfl_key
    from lib.team_registry import team_key as cfb_key

    hk = cfb_key(home) or nfl_key(home) or home.lower()
    ak = cfb_key(away) or nfl_key(away) or away.lower()
    return hk, ak


def index_snapshot_df(df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    """Group snapshot rows by normalized home/away keys."""
    if df.empty:
        return {}
    from lib.nfl_team_registry import team_key as nfl_key
    from lib.team_registry import team_key as cfb_key

    tmp = df.copy()
    keys: list[tuple[str, str]] = []
    for _, row in tmp.iterrows():
        rh = str(row.get("home") or "")
        ra = str(row.get("away") or "")
        hk = cfb_key(rh) or nfl_key(rh) or rh.lower()
        ak = cfb_key(ra) or nfl_key(ra) or ra.lower()
        keys.append((hk, ak))
    tmp["_match_key"] = keys
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for key, grp in tmp.groupby("_match_key", sort=False):
        out[key] = grp.drop(columns=["_match_key"])
    return out


def quotes_from_snapshot_df(
    df: pd.DataFrame,
    *,
    home: str,
    away: str,
    snapshot_at: pd.Timestamp | None = None,
    indexed: dict[tuple[str, str], pd.DataFrame] | None = None,
) -> list[dict[str, Any]]:
    if indexed is not None:
        cur = indexed.get(_matchup_key(home, away), pd.DataFrame())
    else:
        cur = df
    if cur.empty:
        return []
    if snapshot_at is not None and "snapshot_at" in cur.columns:
        cur = cur[cur["snapshot_at"] == snapshot_at]
    hits: list[dict[str, Any]] = []
    for row in cur.to_dict("records"):
        rec = dict(row)
        rec["source"] = rec.get("source") or "streamlit_odds_snapshots"
        hits.append(rec)
    return hits


def merge_quotes(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for q in group:
            key = (
                str(q.get("event") or ""),
                str(q.get("market_key") or ""),
                str(q.get("selection") or ""),
                str(q.get("book_id") or ""),
                str(q.get("line") or ""),
                str(q.get("price") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
    return out


def build_games_from_props(props_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in props_rows:
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        if not home or not away:
            continue
        key = (home, away)
        if key not in by_key:
            by_key[key] = {
                "home": home,
                "away": away,
                "matchup": row.get("event") or f"{away} @ {home}",
                "event_id": row.get("event_id"),
                "start": row.get("startDate"),
            }
    return list(by_key.values())


def run_game_sim(
    sport: str,
    home: str,
    away: str,
    *,
    year: int,
    week: int,
    spread: float | None,
    total: float | None,
) -> dict[str, Any]:
    from pricing_engine.constants import DEFAULT_SIMS
    from pricing_engine.simulator import run_matchup_simulation

    return run_matchup_simulation(
        sport,
        home,
        away,
        season=year,
        week=week,
        market_spread=spread,
        market_total=total,
        live_game=None,
        n_sims=DEFAULT_SIMS,
    )


def run_prop_projection(
    prop: dict[str, Any],
    *,
    year: int,
    week: int,
) -> float | None:
    from pricing_engine.ui.props_table import compute_prop_projection

    return compute_prop_projection(prop, year=year, week=week)


def build_game_record(
    sport: str,
    card: dict[str, Any],
    *,
    year: int,
    week: int,
    props_rows: list[dict[str, Any]],
    extra_quotes: list[dict[str, Any]] | None = None,
    run_props: bool = True,
    run_sim: bool = True,
) -> dict[str, Any]:
    home = str(card.get("home") or "")
    away = str(card.get("away") or "")
    lines = lines_from_card(card)
    quotes = merge_quotes(quotes_from_game_card(card), extra_quotes or [])
    sim = (
        run_game_sim(
            sport, home, away, year=year, week=week,
            spread=lines.get("spread"), total=lines.get("total"),
        )
        if run_sim
        else {}
    )

    raw_props = props_for_matchup(props_rows, home=home, away=away)
    props = _dedupe_props(raw_props)
    if run_props:
        for prop in props:
            proj = run_prop_projection(prop, year=year, week=week)
            if proj is not None:
                prop["projection"] = proj

    return {
        "home": home,
        "away": away,
        "matchup": card.get("matchup") or f"{away} @ {home}",
        "event_id": card.get("event_id"),
        "kickoff": card.get("start") or card.get("startDate"),
        "completed": bool(card.get("completed")),
        "home_score": card.get("home_score"),
        "away_score": card.get("away_score"),
        "lines": lines,
        "odds_quotes": quotes,
        "sim": sim,
        "props": props,
    }


def build_week_capture(
    sport: str,
    year: int,
    week: int,
    *,
    capture_type: str = "backfill",
    captured_at: str | None = None,
    source: str = "game_board_cache",
    snapshot_df: pd.DataFrame | None = None,
    snapshot_at: pd.Timestamp | None = None,
    run_props: bool = True,
    run_sim: bool = True,
    projection_lookup: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cards = load_game_cards_file(sport, year, week)
    props_rows = load_props_board(sport, year, week)
    if not cards and props_rows:
        cards = build_games_from_props(props_rows)

    snap_index = index_snapshot_df(snapshot_df) if snapshot_df is not None else None
    games: list[dict[str, Any]] = []
    for card in cards:
        home = str(card.get("home") or "")
        away = str(card.get("away") or "")
        extra: list[dict[str, Any]] = []
        if snapshot_df is not None:
            extra = quotes_from_snapshot_df(
                snapshot_df,
                home=home,
                away=away,
                snapshot_at=snapshot_at,
                indexed=snap_index,
            )
        if projection_lookup and (home, away) in projection_lookup:
            base = projection_lookup[(home, away)]
            games.append(
                {
                    **base,
                    "odds_quotes": merge_quotes(base.get("odds_quotes") or [], extra),
                }
            )
            continue
        games.append(
            build_game_record(
                sport,
                card,
                year=year,
                week=week,
                props_rows=props_rows,
                extra_quotes=extra,
                run_props=run_props,
                run_sim=run_sim,
            )
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "year": int(year),
        "week": int(week),
        "captured_at": captured_at or _utc_now_iso(),
        "capture_type": capture_type,
        "methodology": METHODOLOGY,
        "source": source,
        "games": games,
    }


_UI_SKIP_BOOKS = frozenset({"4c", "4codds", "onyx"})


def _matchup_teams(home: str, away: str, rh: str, ra: str) -> bool:
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    return (cfb_match(rh, home) and cfb_match(ra, away)) or (nfl_match(rh, home) and nfl_match(ra, away))


def _capture_files(sport: str, year: int, week: int) -> list[Path]:
    cdir = captures_dir(sport, year, week)
    if not cdir.exists():
        return []
    backfill = sorted(cdir.glob("*_backfill.json"), reverse=True)
    other = sorted(
        (p for p in cdir.glob("*.json") if "_backfill" not in p.name),
        reverse=True,
    )
    return backfill + other


def load_pregame_capture_any_week(
    sport: str,
    year: int,
    home: str,
    away: str,
    *,
    week_hint: int | None = None,
) -> tuple[dict[str, Any] | None, int | None]:
    """Search week hint ±1 then full season range for a frozen pregame capture."""
    from .sport_context import get_sport_config

    cfg = get_sport_config(sport)
    min_w = int(cfg.get("min_week", 0))
    max_w = int(cfg.get("max_week", 18))
    order: list[int] = []
    if week_hint is not None:
        for w in (week_hint, week_hint - 1, week_hint + 1):
            if min_w <= w <= max_w and w not in order:
                order.append(w)
    for w in range(min_w, max_w + 1):
        if w not in order:
            order.append(w)
    for w in order:
        hit = load_pregame_capture(sport, year, w, home, away)
        if hit:
            return hit, w
    return None, week_hint


def load_pregame_capture(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict[str, Any] | None:
    """Earliest non-inplay capture for a matchup (pregame reference)."""
    files = sorted(
        (p for p in _capture_files(sport, year, week) if "_inplay" not in p.name),
        key=lambda p: p.name,
    )
    for fp in files:
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        cap_type = str(payload.get("capture_type") or "")
        if cap_type == "inplay":
            continue
        for game in payload.get("games") or []:
            if _matchup_teams(home, away, str(game.get("home") or ""), str(game.get("away") or "")):
                return {
                    **game,
                    "_captured_at": payload.get("captured_at"),
                    "_capture_type": payload.get("capture_type"),
                    "_source": payload.get("source"),
                    "_sim": game.get("sim"),
                    "_odds_quotes": game.get("odds_quotes") or [],
                    "_props": game.get("props") or [],
                }
    return None


def load_archived_game(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict[str, Any] | None:
    """Best stored game bundle for a completed/historical week."""
    for fp in _capture_files(sport, year, week):
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        for game in payload.get("games") or []:
            if _matchup_teams(home, away, str(game.get("home") or ""), str(game.get("away") or "")):
                return {
                    **game,
                    "_captured_at": payload.get("captured_at"),
                    "_capture_type": payload.get("capture_type"),
                    "_source": payload.get("source"),
                }
    return None


def _better_american(a: int, b: int) -> int:
    if a >= 0 and b >= 0:
        return a if a > b else b
    if a < 0 and b < 0:
        return a if a > b else b
    return a if a > b else b


def _parse_price(val: object) -> int | None:
    if val is None:
        return None
    text = str(val).strip().replace("\u2014", "")
    if not text or text == "—":
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def flat_quotes_to_board(
    quotes: list[dict[str, Any]],
    *,
    home: str,
    away: str,
) -> dict[str, dict[str, Any]]:
    """Convert archived flat quote rows to pricing board keys."""
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    def _is_home(sel: str) -> bool:
        return cfb_match(sel, home) or nfl_match(sel, home)

    def _is_away(sel: str) -> bool:
        return cfb_match(sel, away) or nfl_match(sel, away)

    buckets: dict[str, list[dict[str, Any]]] = {}

    for raw in quotes:
        bid = str(raw.get("book_id") or "").lower()
        if bid in _UI_SKIP_BOOKS:
            continue
        mk = str(raw.get("market_key") or raw.get("market") or "").lower()
        sel = str(raw.get("selection") or "")
        price = _parse_price(raw.get("price"))
        if price is None:
            continue

        q = {
            "book_id": bid,
            "price": price,
            "line": raw.get("line"),
            "selection": sel,
            "source": raw.get("source") or "pricing_history",
        }

        if mk in {"spreads", "spread"}:
            if _is_home(sel):
                key, q["market"] = "spread_home", "Spread"
                q["selection"] = home
            elif _is_away(sel):
                key, q["market"] = "spread_away", "Spread"
                q["selection"] = away
            else:
                continue
        elif mk in {"totals", "total"}:
            side = str(raw.get("side") or sel).lower()
            if side in {"over", "o"} or sel.lower() == "over":
                key, q["market"], q["selection"] = "total_over", "Total", "Over"
            elif side in {"under", "u"} or sel.lower() == "under":
                key, q["market"], q["selection"] = "total_under", "Total", "Under"
            else:
                continue
        elif mk in {"h2h", "moneyline", "ml"}:
            if _is_home(sel):
                key, q["market"], q["selection"] = "ml_home", "ML", home
                q["line"] = None
            elif _is_away(sel):
                key, q["market"], q["selection"] = "ml_away", "ML", away
                q["line"] = None
            else:
                continue
        else:
            continue

        buckets.setdefault(key, []).append(q)

    out: dict[str, dict[str, Any]] = {}
    for key, opts in buckets.items():
        best = opts[0]
        for cand in opts[1:]:
            try:
                if _better_american(int(cand["price"]), int(best["price"])) == int(cand["price"]):
                    best = cand
            except (TypeError, ValueError):
                pass
        out[key] = best
    return out


def prepare_props_for_ui(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge flat over/under rows and ensure prop_key + book odds for pricing UI."""
    from lib.prop_pricing import prop_key_from_row

    if not props:
        return []
    has_sides = any(str(p.get("side") or "").lower() in {"over", "under"} for p in props)
    base = _dedupe_props(props) if has_sides else [dict(p) for p in props]
    out: list[dict[str, Any]] = []
    for raw in base:
        over = raw.get("over")
        under = raw.get("under")
        if not over and not under:
            side = str(raw.get("side") or "").lower()
            price = raw.get("price")
            book = raw.get("book_id") or raw.get("book")
            if side == "over" and price is not None:
                over = {"book_id": book, "price": price, "line": raw.get("line")}
            elif side == "under" and price is not None:
                under = {"book_id": book, "price": price, "line": raw.get("line")}
        if not over and not under:
            continue
        bid = str(raw.get("book_id") or "").lower()
        if bid in _UI_SKIP_BOOKS and not (over or under):
            continue
        row = dict(raw)
        pkey = str(row.get("prop_key") or row.get("propKey") or prop_key_from_row(row) or "")
        row["prop_key"] = pkey
        row["over"] = over
        row["under"] = under
        if not row.get("stat"):
            row["stat"] = str(row.get("market") or pkey.replace("_", " ")).upper()
        stored = row.get("stored_model_proj")
        if stored is not None and row.get("modelProj") is None:
            row["modelProj"] = stored
        out.append(row)
    return out


def archived_props_for_ui(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Props rows formatted for the pricing props table."""
    return prepare_props_for_ui(props)


def prop_projection_map_from_archived(props: list[dict[str, Any]]) -> dict[tuple[str, str, str], float]:
    from pricing_engine.ui.props_table import prop_projection_key

    out: dict[tuple[str, str, str], float] = {}
    for raw in props:
        proj = raw.get("projection")
        if proj is None:
            continue
        try:
            out[prop_projection_key(raw)] = round(float(proj), 1)
        except (TypeError, ValueError):
            pass
    return out


def daily_snapshot_times(df: pd.DataFrame) -> dict[str, dict[str, pd.Timestamp | None]]:
    """First and last snapshot timestamp per calendar day."""
    if df.empty or "snapshot_at" not in df.columns:
        return {}
    out: dict[str, dict[str, pd.Timestamp | None]] = {}
    tmp = df.dropna(subset=["snapshot_at"]).copy()
    tmp["day"] = tmp["snapshot_at"].dt.strftime("%Y-%m-%d")
    for day, grp in tmp.groupby("day"):
        grp = grp.sort_values("snapshot_at")
        out[str(day)] = {
            "open": grp["snapshot_at"].iloc[0],
            "close": grp["snapshot_at"].iloc[-1],
        }
    return out
