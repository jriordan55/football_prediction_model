"""Odds API credit guard — disk cache, manual pull only, change detection."""

from __future__ import annotations



import hashlib

import json

from datetime import datetime, timezone

from pathlib import Path

from typing import Any



import pandas as pd



from .config import DATA_DIR
from .csv_log import log_odds_quotes
from .odds_client import (

    EXTENDED_SHARP_MARKETS,

    fetch_extended_sharp_odds,

    fetch_odds_api_events,

    fetch_odds_api_sport_odds,

    flatten_odds_api_events,

    game_lines_df,

)



CACHE_DIR = DATA_DIR / "odds_api_cache"


def _lines_cache() -> Path:
    from .sport_context import odds_cache_paths

    name, _ = odds_cache_paths()
    return CACHE_DIR / name


def _meta_cache() -> Path:
    from .sport_context import odds_cache_paths

    _, name = odds_cache_paths()
    return CACHE_DIR / name

MIN_PULL_INTERVAL_SEC = 900  # 15 min — reuse last bulk response unless forced after interval

EXTENDED_PULL_INTERVAL_SEC = 900  # separate throttle for per-event extended pulls





def has_extended_markets(df: pd.DataFrame) -> bool:

    if df is None or df.empty or "market_key" not in df.columns:

        return False

    keys = set(df["market_key"].astype(str).str.lower())

    return any(mk in keys for mk in EXTENDED_SHARP_MARKETS)





def lines_fingerprint(df: pd.DataFrame) -> str:

    if df is None or df.empty:

        return "empty"

    cols = [c for c in ["event", "market_key", "selection", "description", "book_id", "line", "price"] if c in df.columns]

    if not cols:

        return "empty"

    use = df[cols].copy().fillna("")

    for c in cols:

        use[c] = use[c].astype(str)

    use = use.sort_values(cols).reset_index(drop=True)

    digest = hashlib.sha256(use.to_csv(index=False).encode()).hexdigest()

    return digest[:16]





def _load_meta() -> dict[str, Any]:

    if not _meta_cache().exists():

        return {}

    try:

        with open(_meta_cache(), encoding="utf-8") as f:

            return json.load(f)

    except (json.JSONDecodeError, OSError):

        return {}





def _save_meta(meta: dict[str, Any]) -> None:

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    with open(_meta_cache(), "w", encoding="utf-8") as f:

        json.dump(meta, f, indent=2)





def _save_lines(df: pd.DataFrame) -> None:

    if df.empty:

        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    df.to_json(_lines_cache(), orient="records")





def load_cached_lines() -> pd.DataFrame:

    """Last saved Odds API lines — zero credits."""

    if not _lines_cache().exists():

        return pd.DataFrame()

    try:

        return pd.read_json(_lines_cache())

    except Exception:

        return pd.DataFrame()





def cache_age_minutes() -> float | None:

    meta = _load_meta()

    ts = meta.get("fetched_at")

    if not ts:

        return None

    try:

        fetched = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

        if fetched.tzinfo is None:

            fetched = fetched.replace(tzinfo=timezone.utc)

        return (datetime.now(timezone.utc) - fetched).total_seconds() / 60

    except (TypeError, ValueError):

        return None





def _age_sec(ts: str | None) -> float | None:

    if not ts:

        return None

    try:

        fetched = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

        if fetched.tzinfo is None:

            fetched = fetched.replace(tzinfo=timezone.utc)

        return (datetime.now(timezone.utc) - fetched).total_seconds()

    except (TypeError, ValueError):

        return None





def _merge_line_frames(base_df: pd.DataFrame, ext_df: pd.DataFrame) -> pd.DataFrame:

    if ext_df.empty:

        return base_df

    if base_df.empty:

        return ext_df

    df = pd.concat([base_df, ext_df], ignore_index=True)

    dedupe_cols = [

        c for c in ["event_id", "market_key", "selection", "description", "book_id", "line", "price"] if c in df.columns

    ]

    if dedupe_cols:

        df = df.drop_duplicates(subset=dedupe_cols, keep="last")

    return df





def _pull_extended_only(

    existing: pd.DataFrame,

    *,

    tab: str | None,

    meta: dict[str, Any],

    force: bool,

) -> tuple[pd.DataFrame, dict[str, Any]]:

    """Per-event extended markets — bypasses bulk throttle when never fetched."""

    ext_age = _age_sec(meta.get("extended_fetched_at"))

    if has_extended_markets(existing) and not force:

        if ext_age is not None and ext_age < EXTENDED_PULL_INTERVAL_SEC:

            return existing, {**meta, "cached": True, "api_call": False, "extended_cached": True}



    events, ev_quota = fetch_odds_api_events(tab=tab)

    if not events:

        return existing, {**meta, **ev_quota, "extended_error": "no events for extended pull"}



    ext_df, ext_quota = fetch_extended_sharp_odds(events, tab=tab)

    merged = _merge_line_frames(existing, ext_df)

    now = datetime.now(timezone.utc).isoformat()



    new_meta = {

        **meta,

        "fetched_at": meta.get("fetched_at") or now,

        "extended_fetched_at": now,

        "fingerprint": lines_fingerprint(merged),

        "rows": len(merged),

        "extended_rows": len(ext_df),

        "extended_api_calls": ext_quota.get("extended_api_calls"),

        "extended_events": ext_quota.get("extended_events"),

        "extended_errors": ext_quota.get("extended_errors"),

        "api_call": True,

        "extended_pull": True,

        "cached": False,

        **ev_quota,

        **{k: v for k, v in ext_quota.items() if k not in ev_quota},

    }



    _save_lines(merged)

    _save_meta(new_meta)

    log_odds_quotes(merged, source="theoddsapi_extended", tab=tab, dedupe=False)

    return merged, new_meta





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


def _cache_is_fourc(df: pd.DataFrame, meta: dict[str, Any]) -> bool:
    if str(meta.get("source") or "") == "4codds":
        return True
    if df is None or df.empty or "source" not in df.columns:
        return False
    return bool((df["source"].astype(str) == "4codds").any())


def _cache_is_otter(df: pd.DataFrame, meta: dict[str, Any]) -> bool:
    if str(meta.get("source") or "") == "otterodds":
        return True
    if df is None or df.empty or "source" not in df.columns:
        return False
    return bool((df["source"].astype(str) == "otterodds").any())


def _cache_is_live_board(df: pd.DataFrame, meta: dict[str, Any]) -> bool:
    return _cache_is_otter(df, meta) or _cache_is_fourc(df, meta)


def ensure_fourc_lines(*, tab: str | None = None, force: bool = False) -> pd.DataFrame:
    """Pull live board odds (Otter or 4C) into disk cache — safe on every page load."""
    if not _use_live_board_odds():
        return load_cached_lines()
    cached = load_cached_lines()
    meta = _load_meta()
    need_force = force or not _cache_is_live_board(cached, meta)
    df, _ = pull_sport_odds_lines(force=need_force, tab=tab, require_extended=False)
    return df


def _pull_interval_sec() -> int:
    if _use_otter_odds():
        from .otter_odds_client import otter_refresh_sec

        return otter_refresh_sec()
    if _use_fourc_odds():
        from .fourc_odds_client import fourc_refresh_sec

        return fourc_refresh_sec()
    return MIN_PULL_INTERVAL_SEC


def pull_sport_odds_lines(

    *,

    force: bool = False,

    tab: str | None = None,

    require_extended: bool = False,

) -> tuple[pd.DataFrame, dict[str, Any]]:

    """

    Return sport game lines. Uses disk cache unless force=True.

    CFB defaults to live 4C Odds (4codds.com). NFL / fallback uses The Odds API.

    When require_extended=True, also pulls team totals + 1H/Q1 if missing from cache

    (works even when bulk pull is throttled).

    """

    meta = _load_meta()

    cached = load_cached_lines()

    age_sec = _age_sec(meta.get("fetched_at"))

    min_interval = _pull_interval_sec()

    use_live = _use_live_board_odds()
    use_fourc = _use_fourc_odds()
    use_otter = _use_otter_odds()

    # Replace legacy cache when a live board source is configured.
    if use_live and not cached.empty and not _cache_is_live_board(cached, meta):
        cached = pd.DataFrame()
        force = True



    if not force and not cached.empty:

        if use_live:
            if age_sec is not None and age_sec < min_interval and _cache_is_live_board(cached, meta):
                src = "otterodds_cache" if _cache_is_otter(cached, meta) else "4codds_cache"
                log_odds_quotes(cached, source=src, tab=tab, dedupe=True)
                return cached, {**meta, "cached": True, "api_call": False}
        else:
            if age_sec is not None and age_sec < min_interval:
                log_odds_quotes(cached, source="theoddsapi_cache", tab=tab, dedupe=True)
                return cached, {**meta, "cached": True, "api_call": False}

            if require_extended and not has_extended_markets(cached):
                return _pull_extended_only(cached, tab=tab, meta=meta, force=False)

            log_odds_quotes(cached, source="theoddsapi_cache", tab=tab, dedupe=True)
            return cached, {**meta, "cached": True, "api_call": False}



    if force and not cached.empty and age_sec is not None and age_sec < min_interval:

        if require_extended and not has_extended_markets(cached) and not _use_live_board_odds():

            return _pull_extended_only(cached, tab=tab, meta=meta, force=True)

        return cached, {

            **meta,

            "cached": True,

            "api_call": False,

            "skipped_pull": True,

            "reason": f"Using cache from {int(age_sec // 60)} min ago — wait before pulling again.",

        }



    if _use_otter_odds():

        from .otter_odds_client import fetch_otter_game_lines
        from .sport_context import get_sport

        df = fetch_otter_game_lines(get_sport(), tab=tab)

        fp = lines_fingerprint(df)

        now = datetime.now(timezone.utc).isoformat()

        new_meta = {

            "fetched_at": now,

            "fingerprint": fp,

            "rows": len(df),

            "api_call": True,

            "source": "otterodds",

        }

        if not df.empty:

            _save_lines(df)

            log_odds_quotes(df, source="otterodds", tab=tab, dedupe=not force)

        _save_meta(new_meta)

        return df, new_meta



    if _use_fourc_odds():

        from .fourc_odds_client import fetch_fourc_game_lines
        from .sport_context import get_sport

        df = fetch_fourc_game_lines(get_sport(), matchups=None, deep=True, tab=tab)

        fp = lines_fingerprint(df)

        now = datetime.now(timezone.utc).isoformat()

        new_meta = {

            "fetched_at": now,

            "fingerprint": fp,

            "rows": len(df),

            "api_call": True,

            "source": "4codds",

        }

        if not df.empty:

            _save_lines(df)

            log_odds_quotes(df, source="4codds", tab=tab, dedupe=not force)

        _save_meta(new_meta)

        return df, new_meta



    events, quota = fetch_odds_api_sport_odds(tab=tab)

    base_df = game_lines_df(flatten_odds_api_events(events))

    ext_df, ext_quota = fetch_extended_sharp_odds(events, tab=tab)

    df = _merge_line_frames(base_df, ext_df)

    fp = lines_fingerprint(df)

    now = datetime.now(timezone.utc).isoformat()



    unchanged = bool(meta.get("fingerprint") == fp and not cached.empty)

    new_meta = {

        "fetched_at": now,

        "extended_fetched_at": now if not ext_df.empty else meta.get("extended_fetched_at"),

        "fingerprint": fp,

        "rows": len(df),

        "base_rows": len(base_df),

        "extended_rows": len(ext_df),

        "api_call": True,

        "unchanged": unchanged,

        **quota,

        **{k: v for k, v in ext_quota.items() if k not in quota},

    }



    if not df.empty:

        _save_lines(df)

        log_odds_quotes(df, source="theoddsapi", tab=tab, dedupe=not force)

    _save_meta(new_meta)



    return df, new_meta


