"""Multi-book player props — The Odds API only, disk cache, auto-refresh on change."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR, odds_api_key
from .odds_client import ODDS_API_BOOKMAKERS, fetch_odds_api_event_odds, fetch_odds_api_events
from .prop_pricing import normalize_onyx_player
from .team_registry import teams_match

CACHE_DIR = DATA_DIR / "odds_api_cache"
MIN_PULL_INTERVAL_SEC = 900  # 15 min between API checks


def _cache_path() -> Path:
    from .sport_context import multi_book_props_cache_paths

    name, _ = multi_book_props_cache_paths()
    return CACHE_DIR / name


def _meta_path() -> Path:
    from .sport_context import multi_book_props_cache_paths

    _, name = multi_book_props_cache_paths()
    return CACHE_DIR / name


def props_cache_path() -> Path:
    return _cache_path()

# NCAAF player prop markets from The Odds API (non-alternate).
CFB_PLAYER_PROP_MARKETS: tuple[str, ...] = (
    "player_pass_yds",
    "player_pass_tds",
    "player_pass_attempts",
    "player_pass_completions",
    "player_pass_interceptions",
    "player_pass_longest_completion",
    "player_pass_rush_yds",
    "player_pass_rush_reception_tds",
    "player_pass_rush_reception_yds",
    "player_rush_yds",
    "player_rush_tds",
    "player_rush_attempts",
    "player_rush_longest",
    "player_rush_reception_tds",
    "player_rush_reception_yds",
    "player_reception_yds",
    "player_reception_tds",
    "player_receptions",
    "player_reception_longest",
    "player_anytime_td",
    "player_1st_td",
    "player_last_td",
    "player_field_goals",
    "player_kicking_points",
    "player_pats",
    "player_sacks",
    "player_solo_tackles",
    "player_tackles_assists",
    "player_tds_over",
)

NFL_PLAYER_PROP_MARKETS: tuple[str, ...] = (
    "player_pass_yds",
    "player_pass_yds_q1",
    "player_pass_attempts",
    "player_pass_completions",
    "player_pass_tds",
    "player_rush_yds",
    "player_rush_attempts",
    "player_reception_yds",
    "player_receptions",
)

MARKET_TO_PROP: dict[str, dict[str, Any]] = {
    "player_pass_yds": {"prop": "pass_yds", "label": "Pass Yds", "binary": False},
    "player_pass_yds_q1": {"prop": "pass_yds_q1", "label": "1Q Pass Yds", "binary": False},
    "player_pass_tds": {"prop": "pass_tds", "label": "Pass TDs", "binary": False},
    "player_pass_attempts": {"prop": "pass_attempts", "label": "Pass Att", "binary": False},
    "player_pass_completions": {"prop": "pass_completions", "label": "Completions", "binary": False},
    "player_pass_interceptions": {"prop": "pass_ints", "label": "Pass INTs", "binary": False},
    "player_pass_longest_completion": {"prop": "pass_long", "label": "Long Pass", "binary": False},
    "player_pass_rush_yds": {"prop": "pass_rush_yds", "label": "Pass+Rush Yds", "binary": False},
    "player_pass_rush_reception_tds": {"prop": "prd_tds", "label": "PRD TDs", "binary": False},
    "player_pass_rush_reception_yds": {"prop": "prd_yds", "label": "PRD Yds", "binary": False},
    "player_rush_yds": {"prop": "rush_yds", "label": "Rush Yds", "binary": False},
    "player_rush_tds": {"prop": "rush_tds", "label": "Rush TDs", "binary": False},
    "player_rush_attempts": {"prop": "rush_attempts", "label": "Rush Att", "binary": False},
    "player_rush_longest": {"prop": "rush_long", "label": "Long Rush", "binary": False},
    "player_rush_reception_tds": {"prop": "rr_tds", "label": "RR TDs", "binary": False},
    "player_rush_reception_yds": {"prop": "rr_yds", "label": "RR Yds", "binary": False},
    "player_reception_yds": {"prop": "rec_yds", "label": "Rec Yds", "binary": False},
    "player_reception_tds": {"prop": "rec_tds", "label": "Rec TDs", "binary": False},
    "player_receptions": {"prop": "receptions", "label": "Receptions", "binary": False},
    "player_reception_longest": {"prop": "rec_long", "label": "Long Rec", "binary": False},
    "player_anytime_td": {"prop": "tds", "label": "Anytime TD", "binary": True},
    "player_1st_td": {"prop": "first_td", "label": "1st TD", "binary": True},
    "player_last_td": {"prop": "last_td", "label": "Last TD", "binary": True},
    "player_field_goals": {"prop": "field_goals", "label": "FGs", "binary": False},
    "player_kicking_points": {"prop": "kicking_pts", "label": "Kicking Pts", "binary": False},
    "player_pats": {"prop": "pats", "label": "PATs", "binary": False},
    "player_sacks": {"prop": "sacks", "label": "Sacks", "binary": False},
    "player_solo_tackles": {"prop": "solo_tackles", "label": "Solo Tkl", "binary": False},
    "player_tackles_assists": {"prop": "tackles_assists", "label": "Tkl+Ast", "binary": False},
    "player_tds_over": {"prop": "tds_over", "label": "TDs Over", "binary": False},
}

PROP_MARKET_FILTER_OPTIONS: list[str] = ["All Props"] + sorted(
    {d["label"] for d in MARKET_TO_PROP.values()}
)


def player_prop_markets() -> tuple[str, ...]:
    """Sport-specific player prop markets for The Odds API."""
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        return NFL_PLAYER_PROP_MARKETS
    return CFB_PLAYER_PROP_MARKETS

_MARKET_BATCH = 8


def _normalize_player_key(name: str) -> str:
    s = normalize_onyx_player(name) or str(name or "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _fmt_american(price: Any) -> str | None:
    if price is None:
        return None
    try:
        n = int(float(str(price).replace("+", "").replace("−", "-")))
        return f"+{n}" if n > 0 else str(n)
    except (TypeError, ValueError):
        return str(price)


def parse_bookmaker_props(bookmaker: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    buckets: dict[str, dict[str, Any]] = {}
    from .config import normalize_book_id

    book_id = normalize_book_id(bookmaker.get("key") or "")
    if book_id in {"onyx", "onyxodds", "onyx_odds"}:
        return []

    for market in bookmaker.get("markets") or []:
        mk = str(market.get("key") or "")
        if "alternate" in mk.lower():
            continue
        defn = MARKET_TO_PROP.get(mk)
        if not defn:
            continue
        for outcome in market.get("outcomes") or []:
            player = outcome.get("description")
            if not player or re.match(r"^(over|under|yes|no)$", str(player).strip(), re.I):
                continue
            side_name = str(outcome.get("name") or "").strip()
            if re.match(r"^over$|^yes$", side_name, re.I):
                side = "over"
            elif re.match(r"^under$|^no$", side_name, re.I):
                side = "under"
            else:
                continue
            try:
                line = (
                    float(outcome.get("point"))
                    if outcome.get("point") is not None
                    else (0.5 if defn["binary"] else None)
                )
            except (TypeError, ValueError):
                line = None
            if line is None:
                continue
            key = f"{_normalize_player_key(player)}|{defn['prop']}|{line}"
            if key not in buckets:
                buckets[key] = {
                    "player": player,
                    "propKey": defn["prop"],
                    "market": defn["label"],
                    "market_key": mk,
                    "line": line,
                    "over_price": None,
                    "under_price": None,
                }
            buckets[key][f"{side}_price"] = _fmt_american(outcome.get("price"))

    for b in buckets.values():
        if b.get("over_price") or b.get("under_price"):
            rows.append({**b, "book_id": book_id})
    return rows


def match_odds_event_ids(matchups: list[dict[str, Any]] | None = None, *, limit: int = 20) -> list[str]:
    """Map ESPN scoreboard games to The Odds API event IDs."""
    events, _ = fetch_odds_api_events()
    if not events:
        return []
    if not matchups:
        return [str(e.get("id") or "") for e in events[:limit] if e.get("id")]

    ids: list[str] = []
    for m in matchups:
        home = str(m.get("home") or "")
        away = str(m.get("away") or "")
        for ev in events:
            eid = ev.get("id")
            if not eid:
                continue
            if teams_match(ev.get("home_team"), home) and teams_match(ev.get("away_team"), away):
                ids.append(str(eid))
                break
    return ids[:limit]


def fetch_event_player_props(
    event_id: str,
    *,
    bookmakers: str | None = None,
    markets: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if not odds_api_key() or not event_id:
        return []

    books = bookmakers or ODDS_API_BOOKMAKERS
    market_keys = markets or player_prop_markets()
    out: list[dict[str, Any]] = []
    home = away = ""

    for i in range(0, len(market_keys), _MARKET_BATCH):
        batch = list(market_keys[i : i + _MARKET_BATCH])
        event, _quota = fetch_odds_api_event_odds(event_id, batch, bookmakers=books)
        if not event:
            continue
        home = event.get("home_team") or home
        away = event.get("away_team") or away
        for bm in event.get("bookmakers") or []:
            for row in parse_bookmaker_props(bm):
                side = "over" if row.get("over_price") else "under"
                out.append(
                    {
                        **row,
                        "side": side,
                        "price": row.get("over_price") or row.get("under_price"),
                        "event_id": event_id,
                        "home": home,
                        "away": away,
                        "event": f"{away} @ {home}",
                        "source": "theoddsapi",
                    }
                )
    return out


def _read_disk_cache() -> pd.DataFrame:
    if not _cache_path().exists():
        return pd.DataFrame()
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if df.empty:
            return df
        bid = df.get("book_id")
        if bid is not None:
            df = df[~bid.astype(str).str.lower().isin({"onyx", "onyx_odds", "onyxodds"})]
        return df
    except (json.JSONDecodeError, OSError):
        return pd.DataFrame()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _age_sec(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        fetched = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (_utc_now() - fetched).total_seconds()
    except (TypeError, ValueError):
        return None


def _matchup_key(matchups: list[dict[str, Any]] | None) -> str:
    if not matchups:
        return "all"
    pairs = sorted(f"{m.get('away', '')}|{m.get('home', '')}" for m in matchups)
    digest = hashlib.sha256("|".join(pairs).encode()).hexdigest()
    return digest[:16]


def _props_fingerprint(rows: list[dict[str, Any]] | pd.DataFrame) -> str:
    if rows is None or (isinstance(rows, pd.DataFrame) and rows.empty) or not rows:
        return "empty"
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    cols = [c for c in ["player", "propKey", "line", "book_id", "over_price", "under_price"] if c in df.columns]
    if not cols:
        return "empty"
    use = df[cols].copy().fillna("")
    for c in cols:
        use[c] = use[c].astype(str)
    use = use.sort_values(cols).reset_index(drop=True)
    return hashlib.sha256(use.to_csv(index=False).encode()).hexdigest()[:16]


def _load_meta() -> dict[str, Any]:
    if not _meta_path().exists():
        return {}
    try:
        return json.loads(_meta_path().read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(meta: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _meta_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _fetch_all_props(matchups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    event_ids = match_odds_event_ids(matchups)
    if not event_ids:
        return []
    rows: list[dict[str, Any]] = []
    for eid in event_ids:
        rows.extend(fetch_event_player_props(str(eid)))
    return rows


def ensure_multi_book_props(
    matchups: tuple[dict[str, Any], ...] | None = None,
    *,
    force: bool = False,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Return cached props instantly. Fetch from API only when cache is empty, week
    changed, interval elapsed, or force=True. Writes disk only when fingerprint changes.
    """
    cached = _read_disk_cache()
    meta = _load_meta()
    matchup_list = list(matchups) if matchups else None
    mkey = _matchup_key(matchup_list)
    age = _age_sec(meta.get("updatedAt") or meta.get("checkedAt"))
    week_changed = mkey != str(meta.get("matchupKey") or "")

    need_fetch = force or cached.empty or week_changed
    if not need_fetch:
        return cached, {**meta, "cached": True, "api_call": False, "stale": age is not None and age >= MIN_PULL_INTERVAL_SEC}

    if not odds_api_key():
        return cached, {**meta, "cached": bool(not cached.empty), "api_call": False, "error": "no_key"}

    new_rows = _fetch_all_props(matchup_list)
    now = _utc_now().isoformat()
    new_fp = _props_fingerprint(new_rows)
    old_fp = str(meta.get("fingerprint") or "")

    if new_rows and new_fp != old_fp:
        _write_disk_cache(
            new_rows,
            fingerprint=new_fp,
            matchup_key=mkey,
            tab=tab,
            year=year,
            week=week,
        )
        new_meta = {
            **meta,
            "fingerprint": new_fp,
            "matchupKey": mkey,
            "updatedAt": now,
            "checkedAt": now,
            "count": len(new_rows),
            "api_call": True,
            "changed": True,
            "cached": False,
        }
        _save_meta(new_meta)
        return pd.DataFrame(new_rows), new_meta

    check_meta = {
        **meta,
        "fingerprint": old_fp or new_fp,
        "matchupKey": mkey if new_rows or week_changed else meta.get("matchupKey", mkey),
        "checkedAt": now,
        "api_call": True,
        "changed": False,
        "unchanged": bool(new_rows),
        "cached": bool(not cached.empty),
    }
    _save_meta(check_meta)

    if cached.empty and new_rows:
        _write_disk_cache(
            new_rows,
            fingerprint=new_fp,
            matchup_key=mkey,
            tab=tab,
            year=year,
            week=week,
        )
        check_meta["updatedAt"] = now
        check_meta["count"] = len(new_rows)
        check_meta["fingerprint"] = new_fp
        _save_meta(check_meta)
        return pd.DataFrame(new_rows), check_meta

    return cached, check_meta


def _write_disk_cache(
    rows: list[dict[str, Any]],
    *,
    fingerprint: str,
    matchup_key: str,
    tab: str | None = None,
    year: int | None = None,
    week: int | None = None,
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    now = _utc_now().isoformat()
    payload = {
        "rows": rows,
        "updatedAt": now,
        "count": len(rows),
        "fingerprint": fingerprint,
        "matchupKey": matchup_key,
    }
    _cache_path().write_text(json.dumps(payload), encoding="utf-8")
    if rows:
        from .csv_log import log_player_prop_quotes

        log_player_prop_quotes(
            pd.DataFrame(rows),
            source="theoddsapi_multi_book",
            tab=tab,
            year=year,
            week=week,
            dedupe=False,
        )


def load_multi_book_props(
    matchups: tuple[dict[str, Any], ...] | None = None,
    *,
    force: bool = False,
) -> pd.DataFrame:
    """Load props — disk read by default; ensure_multi_book_props handles network."""
    if force:
        df, _ = ensure_multi_book_props(matchups, force=True)
        return df
    return _read_disk_cache()


def revalidate_multi_book_props_if_stale(
    matchups: tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    """Background-style refresh: only writes when lines changed. Returns meta."""
    cached = _read_disk_cache()
    if cached.empty or not odds_api_key():
        return {"changed": False, "api_call": False}
    meta = _load_meta()
    age = _age_sec(meta.get("checkedAt") or meta.get("updatedAt"))
    if age is not None and age < MIN_PULL_INTERVAL_SEC:
        return {**meta, "changed": False, "api_call": False, "skipped": True}
    _, pull_meta = ensure_multi_book_props(matchups, force=False)
    return pull_meta


def props_cache_needs_refresh(matchups: tuple[dict[str, Any], ...] | None) -> bool:
    """True when disk cache is missing or for a different scoreboard."""
    if not _cache_path().exists():
        return True
    meta = _load_meta()
    mkey = _matchup_key(list(matchups) if matchups else None)
    return mkey != str(meta.get("matchupKey") or "")


def cache_age_minutes() -> int | None:
    if not _cache_path().exists():
        return None
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        ts = payload.get("updatedAt")
        if not ts:
            return None
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
    except (json.JSONDecodeError, OSError, ValueError):
        return None
