"""Multi-book game lines for Sharp Lines — spreads, totals, derivatives only."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from pathlib import Path

from .config import DATA_DIR, odds_api_key
from .csv_log import log_odds_quotes
from .odds_client import (
    ODDS_API_BOOKMAKERS,
    fetch_odds_api_event_odds,
    fetch_odds_api_events,
    fetch_odds_api_sport_odds,
    flatten_odds_api_events,
    market_label,
)
from .team_registry import teams_match

CACHE_DIR = DATA_DIR / "odds_api_cache"


def _cache_path() -> Path:
    from .sport_context import multi_book_lines_cache_paths

    name, _ = multi_book_lines_cache_paths()
    return CACHE_DIR / name


def _meta_path() -> Path:
    from .sport_context import multi_book_lines_cache_paths

    _, name = multi_book_lines_cache_paths()
    return CACHE_DIR / name


def lines_cache_path() -> Path:
    return _cache_path()


def _min_pull_interval_sec() -> int:
    from .fourc_odds_client import fourc_refresh_sec
    from .otter_odds_client import otter_refresh_sec
    from .sport_context import SPORT_CFB, SPORT_NFL, get_sport

    if get_sport() in (SPORT_CFB, SPORT_NFL):
        if _use_otter_odds():
            return otter_refresh_sec()
        if _use_fourc_odds():
            return fourc_refresh_sec()
    return 900


def _use_otter_odds() -> bool:
    from .otter_odds_client import otter_odds_enabled
    from .sport_context import SPORT_CFB, SPORT_NFL, get_sport

    return get_sport() in (SPORT_CFB, SPORT_NFL) and otter_odds_enabled()


def _use_fourc_odds() -> bool:
    from .fourc_odds_client import fourc_odds_enabled
    from .sport_context import SPORT_CFB, SPORT_NFL, get_sport

    if _use_otter_odds():
        return False
    return get_sport() in (SPORT_CFB, SPORT_NFL) and fourc_odds_enabled()


def _use_live_board_odds() -> bool:
    return _use_otter_odds() or _use_fourc_odds()


MIN_PULL_INTERVAL_SEC = 900  # legacy default; CFB/4C uses _min_pull_interval_sec()

# Sharp Lines markets only — no player props, no moneyline.
SHARP_GAME_MARKETS: tuple[str, ...] = (
    "spreads",
    "totals",
    "team_totals",
    "spreads_q1",
    "spreads_q2",
    "spreads_q3",
    "spreads_q4",
    "spreads_h1",
    "spreads_h2",
    "totals_q1",
    "totals_q2",
    "totals_q3",
    "totals_q4",
    "team_totals_q1",
    "team_totals_q2",
)

BULK_MARKETS = ("spreads", "totals")
EVENT_MARKETS = tuple(m for m in SHARP_GAME_MARKETS if m not in BULK_MARKETS)

MARKET_FILTER_OPTIONS: list[str] = ["All Markets"] + [market_label(m) for m in SHARP_GAME_MARKETS]

_MARKET_BATCH = 8


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
    return hashlib.sha256("|".join(pairs).encode()).hexdigest()[:16]


def _lines_fingerprint(df: pd.DataFrame) -> str:
    if df.empty:
        return "empty"
    cols = [c for c in ["event_id", "market_key", "selection", "description", "book_id", "line", "price"] if c in df.columns]
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


def _filter_sharp_markets(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    allowed = set(SHARP_GAME_MARKETS)
    return df[df["market_key"].astype(str).str.lower().isin(allowed)].copy()


def match_odds_event_ids(matchups: list[dict[str, Any]] | None = None, *, limit: int = 25) -> list[str]:
    events, _ = fetch_odds_api_events()
    if not events:
        return []
    if not matchups:
        return [str(e.get("id") or "") for e in events[:limit] if e.get("id")]
    ids: list[str] = []
    for m in matchups:
        home, away = str(m.get("home") or ""), str(m.get("away") or "")
        for ev in events:
            eid = ev.get("id")
            if not eid:
                continue
            if teams_match(ev.get("home_team"), home) and teams_match(ev.get("away_team"), away):
                ids.append(str(eid))
                break
    return ids[:limit]


def _fetch_all_game_lines(matchups: list[dict[str, Any]] | None) -> pd.DataFrame:
    from .fourc_odds_client import fetch_fourc_game_lines
    from .otter_odds_client import fetch_otter_game_lines
    from .sport_context import get_sport

    if _use_otter_odds():
        df = fetch_otter_game_lines(get_sport())
        if not df.empty:
            return _filter_sharp_markets(df)

    if _use_fourc_odds():
        df = fetch_fourc_game_lines(get_sport(), matchups, deep=True)
        if not df.empty:
            return _filter_sharp_markets(df)

    frames: list[pd.DataFrame] = []

    events, _ = fetch_odds_api_sport_odds(markets=",".join(BULK_MARKETS))
    if events:
        bulk = _filter_sharp_markets(flatten_odds_api_events(events))
        if not bulk.empty:
            frames.append(bulk)

    event_ids = match_odds_event_ids(matchups)
    for eid in event_ids:
        for i in range(0, len(EVENT_MARKETS), _MARKET_BATCH):
            batch = list(EVENT_MARKETS[i : i + _MARKET_BATCH])
            event, _ = fetch_odds_api_event_odds(eid, batch, bookmakers=ODDS_API_BOOKMAKERS)
            if not event:
                continue
            part = _filter_sharp_markets(flatten_odds_api_events([event]))
            if not part.empty:
                frames.append(part)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    dedupe = [c for c in ["event_id", "market_key", "selection", "description", "book_id", "line", "price"] if c in out.columns]
    if dedupe:
        out = out.drop_duplicates(subset=dedupe, keep="last")
    return out


def _read_disk_cache() -> pd.DataFrame:
    if not _cache_path().exists():
        return pd.DataFrame()
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        return _filter_sharp_markets(df)
    except (json.JSONDecodeError, OSError):
        return pd.DataFrame()


def _write_disk_cache(df: pd.DataFrame, *, fingerprint: str, matchup_key: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    rows = df.to_dict(orient="records")
    payload = {
        "rows": rows,
        "updatedAt": _utc_now().isoformat(),
        "count": len(rows),
        "fingerprint": fingerprint,
        "matchupKey": matchup_key,
    }
    _cache_path().write_text(json.dumps(payload), encoding="utf-8")


def _cache_has_derivatives(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    keys = set(df["market_key"].astype(str).str.lower())
    return bool(keys & set(EVENT_MARKETS))


def ensure_multi_book_game_lines(
    matchups: tuple[dict[str, Any], ...] | None = None,
    *,
    force: bool = False,
    display_week: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cached = _read_disk_cache()
    meta = _load_meta()
    matchup_list = list(matchups) if matchups else None
    mkey = _matchup_key(matchup_list)
    if display_week is not None:
        mkey = f"w{int(display_week)}|{mkey}"
    week_changed = mkey != str(meta.get("matchupKey") or "")
    use_live = _use_live_board_odds()
    use_fourc = _use_fourc_odds()
    need_fetch = force or cached.empty or week_changed
    if not use_live:
        need_fetch = need_fetch or not _cache_has_derivatives(cached)
    elif not force and not cached.empty and not week_changed:
        age = _age_sec(meta.get("checkedAt") or meta.get("updatedAt"))
        if age is not None and age < _min_pull_interval_sec():
            need_fetch = False

    if not need_fetch:
        return cached, {**meta, "cached": True, "api_call": False}

    if not use_live and not odds_api_key():
        return cached, {**meta, "cached": bool(not cached.empty), "api_call": False, "error": "no_key"}

    new_df = _fetch_all_game_lines(matchup_list)
    now = _utc_now().isoformat()
    new_fp = _lines_fingerprint(new_df)
    old_fp = str(meta.get("fingerprint") or "")

    if not new_df.empty and new_fp != old_fp:
        _write_disk_cache(new_df, fingerprint=new_fp, matchup_key=mkey)
        log_odds_quotes(new_df, source="4codds" if use_fourc else "theoddsapi_multi_book", dedupe=False)
        new_meta = {
            **meta,
            "fingerprint": new_fp,
            "matchupKey": mkey,
            "updatedAt": now,
            "checkedAt": now,
            "count": len(new_df),
            "api_call": True,
            "changed": True,
        }
        _save_meta(new_meta)
        return new_df, new_meta

    check_meta = {
        **meta,
        "matchupKey": mkey if week_changed else meta.get("matchupKey", mkey),
        "checkedAt": now,
        "api_call": True,
        "changed": False,
        "unchanged": bool(not new_df.empty),
        "cached": bool(not cached.empty),
    }
    _save_meta(check_meta)

    if cached.empty and not new_df.empty:
        _write_disk_cache(new_df, fingerprint=new_fp, matchup_key=mkey)
        log_odds_quotes(new_df, source="4codds" if use_fourc else "theoddsapi_multi_book", dedupe=False)
        check_meta["updatedAt"] = now
        check_meta["fingerprint"] = new_fp
        check_meta["count"] = len(new_df)
        _save_meta(check_meta)
        return new_df, check_meta

    return cached, check_meta


def load_multi_book_game_lines(*, force: bool = False, matchups: tuple[dict[str, Any], ...] | None = None) -> pd.DataFrame:
    if force:
        df, _ = ensure_multi_book_game_lines(matchups, force=True)
        return df
    return _read_disk_cache()


def lines_cache_needs_refresh(matchups: tuple[dict[str, Any], ...] | None, display_week: int | None = None) -> bool:
    from .fourc_odds_client import fourc_odds_enabled
    from .sport_context import SPORT_CFB, get_sport

    if not _cache_path().exists():
        return True
    meta = _load_meta()
    mkey = _matchup_key(list(matchups) if matchups else None)
    if display_week is not None:
        mkey = f"w{int(display_week)}|{mkey}"
    if mkey != str(meta.get("matchupKey") or ""):
        return True
    if _use_fourc_odds():
        age = _age_sec(meta.get("checkedAt") or meta.get("updatedAt"))
        return age is None or age >= _min_pull_interval_sec()
    cached = _read_disk_cache()
    return not _cache_has_derivatives(cached)


def cache_age_minutes() -> int | None:
    if not _cache_path().exists():
        return None
    try:
        payload = json.loads(_cache_path().read_text(encoding="utf-8"))
        ts = payload.get("updatedAt")
        if not ts:
            return None
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return int((_utc_now() - dt).total_seconds() / 60)
    except (json.JSONDecodeError, OSError, ValueError):
        return None
