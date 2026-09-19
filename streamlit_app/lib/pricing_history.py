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
    "version": 6,
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
    from lib.prop_reprice import attach_prop_teams

    return attach_prop_teams(out)


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


_CAPTURE_PRIORITY: dict[str, int] = {
    "game_close": 0,
    "game_open": 1,
    "pregame_view": 2,
    "daily_close": 3,
    "snapshot": 4,
    "backfill": 5,
}

_PREGAME_CAPTURE_TYPES = frozenset({"game_close", "game_open", "pregame_view", "daily_close", "snapshot", "backfill"})


def _week_search_order(sport: str, week_hint: int | None) -> list[int]:
    from .sport_context import get_sport_config

    cfg = get_sport_config(sport)
    min_w = int(cfg.get("min_week", 0))
    max_w = int(cfg.get("max_week", 18))
    order: list[int] = []
    if week_hint is not None:
        for w in (int(week_hint), int(week_hint) - 1, int(week_hint) + 1):
            if min_w <= w <= max_w and w not in order:
                order.append(w)
    for w in range(min_w, max_w + 1):
        if w not in order:
            order.append(w)
    return order


def _stored_bundle_score(capture_type: str, game: dict[str, Any], captured_at: str) -> tuple:
    pri = _CAPTURE_PRIORITY.get(str(capture_type or ""), 99)
    has_sim = 1 if game.get("sim") else 0
    nprops = len(game.get("props") or [])
    nquotes = len(game.get("odds_quotes") or [])
    return (pri, -has_sim, -nprops, -nquotes, str(captured_at or ""))


def matchup_has_capture(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
    *,
    capture_types: frozenset[str] | None = None,
) -> bool:
    for fp in _capture_files(sport, year, week):
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        cap_type = str(payload.get("capture_type") or "")
        if capture_types and cap_type not in capture_types:
            continue
        for game in payload.get("games") or []:
            if _matchup_teams(home, away, str(game.get("home") or ""), str(game.get("away") or "")):
                return True
    return False


def load_stored_game_bundle(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict[str, Any] | None:
    """Best archived game bundle for a matchup (game_close preferred)."""
    best: tuple[tuple, dict[str, Any]] | None = None
    for wk in _week_search_order(sport, week):
        for fp in _capture_files(sport, year, wk):
            try:
                payload = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            cap_type = str(payload.get("capture_type") or "")
            captured_at = str(payload.get("captured_at") or "")
            for game in payload.get("games") or []:
                if not _matchup_teams(home, away, str(game.get("home") or ""), str(game.get("away") or "")):
                    continue
                bundle = {
                    **game,
                    "_captured_at": captured_at,
                    "_capture_type": cap_type,
                    "_source": payload.get("source"),
                    "_week": wk,
                }
                score = _stored_bundle_score(cap_type, game, captured_at)
                if best is None or score < best[0]:
                    best = (score, bundle)
    return best[1] if best else None


def load_game_card_matchup(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict[str, Any] | None:
    for card in load_game_cards_file(sport, year, week):
        if _matchup_teams(home, away, str(card.get("home") or ""), str(card.get("away") or "")):
            return card
    return None


def board_quotes_to_flat(
    quotes: dict[str, dict[str, Any]],
    *,
    home: str,
    away: str,
    event_id: str | None = None,
) -> list[dict[str, Any]]:
    market_key = {"Spread": "spreads", "Total": "totals", "ML": "h2h"}
    out: list[dict[str, Any]] = []
    for q in quotes.values():
        if not q:
            continue
        mk = market_key.get(str(q.get("market") or ""), str(q.get("market") or "").lower())
        out.append(
            {
                "market_key": mk,
                "selection": q.get("selection"),
                "line": q.get("line"),
                "price": q.get("price"),
                "book_id": q.get("book_id"),
                "home": home,
                "away": away,
                "event_id": event_id,
                "source": q.get("source") or "pregame_view",
            }
        )
    return out


def quotes_from_open_close_lines(
    home: str,
    away: str,
    lines: dict[str, Any],
    *,
    use_close: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build pricing-board quotes from archived closing/opening lines."""
    src = str(lines.get("source") or lines.get("book") or "history")
    prefix = "close" if use_close else "open"
    spread = lines.get(f"{prefix}Spread")
    if spread is None:
        spread = lines.get("closeSpread") if use_close else lines.get("openSpread")
    if spread is None:
        spread = lines.get("spread")
    total = lines.get(f"{prefix}Total")
    if total is None:
        total = lines.get("closeTotal") if use_close else lines.get("openTotal")
    if total is None:
        total = lines.get("total")
    home_ml = lines.get(f"{prefix}HomeMoneyline")
    if home_ml is None:
        home_ml = lines.get("closeHomeMoneyline") if use_close else lines.get("openHomeMoneyline")
    away_ml = lines.get(f"{prefix}AwayMoneyline")
    if away_ml is None:
        away_ml = lines.get("closeAwayMoneyline") if use_close else lines.get("openAwayMoneyline")

    out: dict[str, dict[str, Any]] = {}
    if spread is not None:
        try:
            sp = float(spread)
            out["spread_home"] = {
                "book_id": src,
                "market": "Spread",
                "selection": home,
                "line": sp,
                "price": None,
                "source": src,
            }
            out["spread_away"] = {
                "book_id": src,
                "market": "Spread",
                "selection": away,
                "line": -sp,
                "price": None,
                "source": src,
            }
        except (TypeError, ValueError):
            pass
    if total is not None:
        try:
            tot = float(total)
            out["total_over"] = {
                "book_id": src,
                "market": "Total",
                "selection": "Over",
                "line": tot,
                "price": None,
                "source": src,
            }
            out["total_under"] = {
                "book_id": src,
                "market": "Total",
                "selection": "Under",
                "line": tot,
                "price": None,
                "source": src,
            }
        except (TypeError, ValueError):
            pass
    if home_ml is not None:
        try:
            out["ml_home"] = {
                "book_id": src,
                "market": "ML",
                "selection": home,
                "line": None,
                "price": int(float(home_ml)),
                "source": src,
            }
        except (TypeError, ValueError):
            pass
    if away_ml is not None:
        try:
            out["ml_away"] = {
                "book_id": src,
                "market": "ML",
                "selection": away,
                "line": None,
                "price": int(float(away_ml)),
                "source": src,
            }
        except (TypeError, ValueError):
            pass
    return out


def resolve_historical_lines(
    home: str,
    away: str,
    *,
    sport: str,
    year: int,
    week: int,
    game: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Closing lines for a finished game — ESPN pickcenter, snapshots, fastR."""
    g = game or {}
    event_id = g.get("event_id") or g.get("id")
    kickoff = g.get("kickoff") or g.get("startDate")

    from lib.game_line_history import opening_closing_lines

    oc = opening_closing_lines(
        home,
        away,
        event_id=str(event_id) if event_id else None,
        year=int(year),
        kickoff=kickoff,
        skip_espn=False,
    )
    try:
        from lib.fastr_market_lines import fastr_open_close_lines_cached

        fr = fastr_open_close_lines_cached(home, away, int(year), week=int(week), sport=sport)
        for key in (
            "openSpread",
            "closeSpread",
            "openTotal",
            "closeTotal",
            "closeHomeMoneyline",
            "closeAwayMoneyline",
            "openHomeMoneyline",
            "openAwayMoneyline",
        ):
            if oc.get(key) is None and fr.get(key) is not None:
                oc[key] = fr.get(key)
        if not oc.get("source") and fr.get("source"):
            oc["source"] = fr.get("source")
    except Exception:
        pass
    return oc


def _quotes_have_retail_board(quotes: dict[str, dict[str, Any]]) -> bool:
    return any(
        str((q or {}).get("book_id") or "").lower() not in ("4c", "4codds", "onyx", "")
        and ((q or {}).get("line") is not None or (q or {}).get("price") is not None)
        for q in quotes.values()
    )


def _matchup_slug(home: str, away: str) -> str:
    from lib.nfl_team_registry import team_key as nfl_key
    from lib.team_registry import team_key as cfb_key

    hk = cfb_key(home) or nfl_key(home) or str(home or "").lower().replace(" ", "_")
    ak = cfb_key(away) or nfl_key(away) or str(away or "").lower().replace(" ", "_")
    return f"{ak}_at_{hk}"


def pregame_snapshot_path(sport: str, year: int, week: int, home: str, away: str) -> Path:
    return week_dir(sport, year, week) / "games" / f"{_matchup_slug(home, away)}.json"


def load_pregame_snapshot(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict[str, Any] | None:
    """Canonical frozen pregame bundle for one matchup (updated every odds poll pre-kickoff)."""
    for wk in _week_search_order(sport, week):
        path = pregame_snapshot_path(sport, year, wk, home, away)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        quotes = data.get("quotes")
        if not quotes and data.get("odds_quotes"):
            quotes = flat_quotes_to_board(data["odds_quotes"], home=home, away=away)
        return {
            **data,
            "home": data.get("home") or home,
            "away": data.get("away") or away,
            "quotes": quotes or {},
            "props": data.get("props") or [],
            "sim": data.get("sim"),
            "lines": data.get("lines") or {},
            "odds_quotes": data.get("odds_quotes")
            or board_quotes_to_flat(quotes or {}, home=home, away=away, event_id=data.get("event_id")),
            "_captured_at": data.get("updated_at") or data.get("captured_at"),
            "_capture_type": "pregame_snapshot",
            "_source": data.get("source") or "pricing_ui",
            "_week": wk,
        }
    return None


def save_pregame_snapshot(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
    *,
    game: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    props: list[dict[str, Any]],
    sim: dict[str, Any] | None,
    prop_map: dict[tuple[str, str, str], float] | None = None,
) -> bool:
    """Always upsert the canonical pregame file while the game is still upcoming."""
    from pricing_engine.pit import game_is_final
    from pricing_engine.ui.props_table import prop_projection_key

    if game_is_final(game):
        return False
    if not _quotes_have_retail_board(quotes) and not props:
        return False

    spread = quotes.get("spread_home", {}).get("line")
    if spread is None and quotes.get("spread_away", {}).get("line") is not None:
        try:
            spread = -float(quotes["spread_away"]["line"])
        except (TypeError, ValueError):
            spread = None
    total = quotes.get("total_over", {}).get("line") or quotes.get("total_under", {}).get("line")

    props_out: list[dict[str, Any]] = []
    for raw in props:
        row = dict(raw)
        if prop_map:
            pkey = prop_projection_key(row)
            if pkey in prop_map:
                row["projection"] = prop_map[pkey]
        props_out.append(row)

    event_id = game.get("event_id") or game.get("id")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "year": int(year),
        "week": int(week),
        "home": home,
        "away": away,
        "event_id": event_id,
        "kickoff": game.get("kickoff") or game.get("startDate"),
        "updated_at": _utc_now_iso(),
        "lines": {"spread": spread, "total": total},
        "quotes": quotes,
        "odds_quotes": board_quotes_to_flat(
            quotes, home=home, away=away, event_id=str(event_id) if event_id else None,
        ),
        "props": props_out,
        "sim": sim,
        "methodology": METHODOLOGY,
        "source": "pricing_ui",
    }

    try:
        payload["open_close"] = resolve_historical_lines(
            home, away, sport=sport, year=int(year), week=int(week), game=game,
        )
    except Exception:
        pass

    _write_snapshot_file(sport, year, week, home, away, payload)
    return True


def _write_snapshot_file(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
    payload: dict[str, Any],
) -> Path:
    path = pregame_snapshot_path(sport, year, week, home, away)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def build_completed_backtest_bundle(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
    game: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Point-in-time backtest bundle for a finished game without a UI snapshot.
    Odds + props from The Odds API archives; sim/projections frozen at display week.
    """
    from lib.game_line_history import closing_board_quotes_from_snapshots
    from pricing_engine.simulator import run_matchup_simulation
    from pricing_engine.ui.props_table import build_prop_projection_map, prop_projection_key

    quotes: dict[str, dict[str, Any]] = {}
    props: list[dict[str, Any]] = []
    sim: dict[str, Any] | None = None
    source = "theoddsapi"

    stored = load_stored_game_bundle(sport, year, week, home, away)
    if stored:
        flat = stored.get("odds_quotes") or []
        if flat:
            quotes = flat_quotes_to_board(flat, home=home, away=away)
            source = str(stored.get("_source") or "pricing_history")
        props = archived_props_for_ui(stored.get("props") or [])
        sim = stored.get("sim")

    kickoff = game.get("kickoff") or game.get("startDate")
    if not _quotes_have_retail_board(quotes):
        snap_quotes = closing_board_quotes_from_snapshots(home, away, kickoff=kickoff)
        if snap_quotes:
            quotes = snap_quotes
            source = "theoddsapi"

    if not props:
        board_rows = props_for_matchup(load_props_board(sport, year, week), home=home, away=away)
        props = archived_props_for_ui(board_rows)
        if props and source != "theoddsapi":
            source = "props_board_cache"

    if not _quotes_have_retail_board(quotes) and not props:
        return None

    spread = quotes.get("spread_home", {}).get("line")
    if spread is None and quotes.get("spread_away", {}).get("line") is not None:
        try:
            spread = -float(quotes["spread_away"]["line"])
        except (TypeError, ValueError):
            spread = None
    total = quotes.get("total_over", {}).get("line") or quotes.get("total_under", {}).get("line")

    if sim is None:
        sim = run_matchup_simulation(
            sport, home, away,
            season=year, week=week,
            market_spread=spread, market_total=total,
        )

    prop_map = build_prop_projection_map(
        props, year=year, week=week, sim=sim, home=home, away=away, backtest=True,
    )
    props_out: list[dict[str, Any]] = []
    for raw in props:
        row = dict(raw)
        pkey = prop_projection_key(row)
        if pkey in prop_map:
            row["projection"] = prop_map[pkey]
        props_out.append(row)

    event_id = game.get("event_id") or game.get("id")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sport": sport,
        "year": int(year),
        "week": int(week),
        "home": home,
        "away": away,
        "event_id": event_id,
        "kickoff": kickoff,
        "updated_at": _utc_now_iso(),
        "lines": {"spread": spread, "total": total},
        "quotes": quotes,
        "odds_quotes": board_quotes_to_flat(
            quotes, home=home, away=away, event_id=str(event_id) if event_id else None,
        ),
        "props": props_out,
        "sim": sim,
        "methodology": METHODOLOGY,
        "source": source,
        "backtest": True,
    }
    try:
        payload["open_close"] = resolve_historical_lines(
            home, away, sport=sport, year=int(year), week=int(week), game=game,
        )
    except Exception:
        pass

    _write_snapshot_file(sport, year, week, home, away, payload)
    archived = {
        **payload,
        "_captured_at": payload["updated_at"],
        "_capture_type": "completed_backtest",
        "_source": source,
    }
    return {
        "quotes": quotes,
        "props": props_out,
        "archived_game": archived,
        "source": source,
        "stored": archived,
        "fetched_at": payload["updated_at"],
    }


def load_final_pregame_bundle(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
    *,
    game: dict[str, Any] | None = None,
    live_quotes: dict[str, dict[str, Any]] | None = None,
    live_props: list[dict[str, Any]] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """
    Restore pregame odds, props, and sim for a completed game.
    Falls back through pricing_history, board cache, props cache, and line archives.
    """
    quotes = dict(live_quotes or {})
    props = list(live_props or [])
    archived_game: dict[str, Any] | None = None
    source = "live"

    snap = load_pregame_snapshot(sport, year, week, home, away)
    if snap:
        archived_game = snap
        if snap.get("quotes"):
            quotes = snap["quotes"]
            source = "pregame_snapshot"
        if snap.get("props"):
            props = archived_props_for_ui(snap["props"])
            source = "pregame_snapshot"
        return {
            "quotes": quotes,
            "props": props,
            "archived_game": archived_game,
            "source": source,
            "stored": snap,
        }

    stored = load_stored_game_bundle(sport, year, week, home, away)
    if stored:
        archived_game = stored
        if not _quotes_have_retail_board(quotes):
            flat = stored.get("odds_quotes") or []
            if flat:
                quotes = flat_quotes_to_board(flat, home=home, away=away)
                source = str(stored.get("_source") or "pricing_history")
        if not props:
            props = archived_props_for_ui(stored.get("props") or [])
            if props:
                source = str(stored.get("_source") or "pricing_history")

    if not _quotes_have_retail_board(quotes):
        card = load_game_card_matchup(sport, year, week, home, away)
        if card:
            flat = merge_quotes(quotes_from_game_card(card), [])
            if flat:
                quotes = flat_quotes_to_board(flat, home=home, away=away)
                source = "game_board_cache"
                if archived_game is None:
                    archived_game = {
                        "home": home,
                        "away": away,
                        "event_id": card.get("event_id"),
                        "lines": lines_from_card(card),
                        "odds_quotes": flat,
                    }

    if not props:
        board_rows = props_for_matchup(load_props_board(sport, year, week), home=home, away=away)
        props = archived_props_for_ui(board_rows)
        if props:
            source = "props_board_cache" if source == "live" else source

    if strict and game and not _quotes_have_retail_board(quotes) and not props:
        backtest = build_completed_backtest_bundle(sport, year, week, home, away, game)
        if backtest:
            return backtest

    if not strict and not _quotes_have_retail_board(quotes):
        hist = resolve_historical_lines(home, away, sport=sport, year=year, week=week, game=game)
        hist_quotes = quotes_from_open_close_lines(home, away, hist, use_close=True)
        if hist_quotes:
            quotes = hist_quotes
            source = str(hist.get("source") or "line_history")
            if archived_game is None:
                archived_game = {
                    "home": home,
                    "away": away,
                    "event_id": (game or {}).get("event_id") or (game or {}).get("id"),
                    "lines": {"spread": hist.get("closeSpread"), "total": hist.get("closeTotal")},
                    "odds_quotes": board_quotes_to_flat(hist_quotes, home=home, away=away),
                }

    if archived_game and stored and stored.get("sim"):
        archived_game = {**archived_game, "sim": stored.get("sim"), "props": stored.get("props") or props}

    return {
        "quotes": quotes,
        "props": props,
        "archived_game": archived_game,
        "source": source,
        "stored": stored,
    }


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


OPEN_CLOSE_STATE_PATH = PRICING_HISTORY_DIR / "open_close_state.json"
CLOSE_BEFORE_KICKOFF_MINUTES = 20


def load_open_close_state() -> dict[str, Any]:
    if not OPEN_CLOSE_STATE_PATH.exists():
        return {"schema_version": 1, "games": {}}
    try:
        data = json.loads(OPEN_CLOSE_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("games", {})
            return data
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return {"schema_version": 1, "games": {}}


def save_open_close_state(state: dict[str, Any]) -> None:
    OPEN_CLOSE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now_iso()
    OPEN_CLOSE_STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _game_state_key(sport: str, game_id: str) -> str:
    return f"{sport}:{game_id}"


def open_close_captured(state: dict[str, Any], sport: str, game_id: str, capture_type: str) -> bool:
    entry = (state.get("games") or {}).get(_game_state_key(sport, game_id)) or {}
    return bool(entry.get(capture_type))


def mark_open_close_captured(
    state: dict[str, Any],
    *,
    sport: str,
    game_id: str,
    capture_type: str,
    path: str,
    captured_at: str,
) -> None:
    games = state.setdefault("games", {})
    key = _game_state_key(sport, game_id)
    entry = dict(games.get(key) or {})
    entry[capture_type] = {"path": path, "captured_at": captured_at}
    games[key] = entry


def _parse_kickoff_ts(kickoff: Any) -> datetime | None:
    if kickoff is None:
        return None
    try:
        if isinstance(kickoff, (int, float)):
            ts = float(kickoff)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        text = str(kickoff).strip()
        if text.isdigit():
            ts = float(text)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OSError):
        return None


def should_capture_game_open(
    *,
    kickoff: Any,
    now: datetime | None = None,
    already_captured: bool,
    has_quotes: bool,
) -> bool:
    if already_captured or not has_quotes:
        return False
    kick = _parse_kickoff_ts(kickoff)
    cur = now or datetime.now(timezone.utc)
    if kick is not None and kick <= cur:
        return False
    return True


def should_capture_game_close(
    *,
    kickoff: Any,
    now: datetime | None = None,
    already_captured: bool,
    has_quotes: bool,
) -> bool:
    if already_captured or not has_quotes:
        return False
    kick = _parse_kickoff_ts(kickoff)
    cur = now or datetime.now(timezone.utc)
    if kick is None:
        return False
    if kick <= cur:
        return True
    mins = (kick - cur).total_seconds() / 60.0
    return mins <= CLOSE_BEFORE_KICKOFF_MINUTES


def load_game_open_close(
    sport: str,
    year: int,
    week: int,
    home: str,
    away: str,
) -> dict[str, Any] | None:
    """Return opening/closing lines from kickoff-aware 4C captures when available."""
    out: dict[str, Any] = {}
    for capture_type, prefix in (("game_open", "open"), ("game_close", "close")):
        for fp in sorted(captures_dir(sport, year, week).glob(f"*_{capture_type}.json")):
            try:
                payload = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            for game in payload.get("games") or []:
                if not _matchup_teams(home, away, str(game.get("home") or ""), str(game.get("away") or "")):
                    continue
                lines = game.get("lines") or {}
                if lines.get("spread") is not None:
                    out[f"{prefix}Spread"] = lines.get("spread")
                if lines.get("total") is not None:
                    out[f"{prefix}Total"] = lines.get("total")
                out[f"{prefix}CapturedAt"] = payload.get("captured_at")
                out[f"{prefix}Source"] = payload.get("source") or "4codds"
                break
    return out or None


def capture_exists_today(capture_type: str, *, sport: str | None = None) -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for entry in _load_manifest().get("captures") or []:
        if str(entry.get("capture_type") or "") != capture_type:
            continue
        if sport and str(entry.get("sport") or "") != sport:
            continue
        captured_at = str(entry.get("captured_at") or "")
        if captured_at.startswith(today):
            return True
    return False
