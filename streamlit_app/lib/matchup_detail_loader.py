"""Matchup detail — disk cache + full build on refresh."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import DATA_DIR
from lib.matchup_builder import build_matchup_fallback
from lib.node_bridge import fetch_labs_matchup, node_available
from lib.slate_loader import load_slate_df
from lib.sport_context import resolve_sport, sport_disk_tag
from lib.team_logos import team_logo_url
from lib.team_registry import team_key
from lib.view_disk_cache import parse_cache_timestamp

DETAIL_CACHE_DIR = DATA_DIR / "game_board_cache"
DETAIL_CACHE_VERSION = 4


def _detail_week_path(sport: str, year: int, week: int) -> Path:
    tag = sport_disk_tag(sport)
    return DETAIL_CACHE_DIR / f"matchup_details_{tag}_{int(year)}_w{int(week)}_v{DETAIL_CACHE_VERSION}.json"


def _legacy_detail_week_path(year: int, week: int) -> Path | None:
    for ver in (3, 2, 1):
        path = DETAIL_CACHE_DIR / f"matchup_details_{int(year)}_w{int(week)}_v{ver}.json"
        if path.exists():
            return path
    legacy = DETAIL_CACHE_DIR / f"matchup_details_{sport_disk_tag('cfb')}_{int(year)}_w{int(week)}_v3.json"
    return legacy if legacy.exists() else None


def _payload_sport_ok(payload: dict[str, Any], sport: str) -> bool:
    saved = payload.get("sport")
    return not saved or str(saved) == sport_disk_tag(sport)


def _matchup_cache_key(home: str, away: str) -> str:
    return f"{team_key(away)}|{team_key(home)}"


def _read_detail_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _detail_from_payload(
    payload: dict[str, Any],
    sport: str,
    home_canon: str,
    away_canon: str,
) -> dict[str, Any] | None:
    if not _payload_sport_ok(payload, sport):
        return None
    details = payload.get("details") or {}
    if not isinstance(details, dict):
        return None
    hit = details.get(_matchup_cache_key(home_canon, away_canon))
    return hit if isinstance(hit, dict) else None


def _detail_cache_paths(sport: str, year: int, week: int) -> list[Path]:
    paths = [_detail_week_path(sport, year, week)]
    legacy = _legacy_detail_week_path(int(year), int(week))
    if legacy is not None:
        paths.append(legacy)
    return paths


def _has_advanced_profile(detail: dict[str, Any]) -> bool:
    ap = detail.get("advancedProfile") or {}
    offense = ap.get("offense") or []
    return bool(offense)


def _has_matchup_props(detail: dict[str, Any]) -> bool:
    return bool(detail.get("plusEvProps"))


def _detail_frozen_ok(detail: dict[str, Any], *, historical: bool) -> bool:
    """Completed-week cache is usable only when props + advanced stats were saved."""
    if not historical:
        return False
    return _has_matchup_props(detail) and _has_advanced_profile(detail)


def _merge_fallback_into_detail(
    detail: dict[str, Any],
    fallback: dict[str, Any],
    *,
    historical: bool,
) -> dict[str, Any]:
    """Always prefer fresh props/stats from fallback for live games."""
    out = dict(detail)
    fb_props = fallback.get("plusEvProps") or []
    fb_adv = fallback.get("advancedProfile") or {}

    if not historical or not _has_matchup_props(out):
        out["plusEvProps"] = fb_props if fb_props else out.get("plusEvProps") or []
    if not historical or not _has_advanced_profile(out):
        if fb_adv.get("offense"):
            out["advancedProfile"] = fb_adv

    for key in ("teamProfiles", "starters", "teamColors", "projectedScore"):
        if not out.get(key) and fallback.get(key):
            out[key] = fallback[key]

    fb_ml = fallback.get("marketLines") or {}
    ml = dict(out.get("marketLines") or {})
    if not historical:
        for k in ("spread", "total", "openSpread", "closeSpread", "openTotal", "closeTotal"):
            if fb_ml.get(k) is not None:
                ml[k] = fb_ml[k]
    else:
        if ml.get("spread") is None and fb_ml.get("spread") is not None:
            ml["spread"] = fb_ml["spread"]
        if ml.get("total") is None and fb_ml.get("total") is not None:
            ml["total"] = fb_ml["total"]
    out["marketLines"] = ml

    if historical:
        out["actualScore"] = fallback.get("actualScore") or out.get("actualScore")
        meta = dict(out.get("meta") or {})
        fb_meta = fallback.get("meta") or {}
        meta["historical"] = True
        meta["final"] = bool(fb_meta.get("final"))
        out["meta"] = meta
    else:
        meta = dict(out.get("meta") or {})
        meta.update({k: v for k, v in (fallback.get("meta") or {}).items() if v is not None})
        meta["historical"] = False
        out["meta"] = meta

    logos = dict(out.get("logos") or {})
    for side in ("home", "away"):
        if fallback.get("logos", {}).get(side):
            logos[side] = fallback["logos"][side]
    out["logos"] = logos
    return out


def matchup_detail_cache_updated_at(sport: str, year: int, week: int) -> datetime | None:
    sport = resolve_sport(sport)
    for path in _detail_cache_paths(sport, year, week):
        payload = _read_detail_payload(path)
        if payload and _payload_sport_ok(payload, sport):
            return parse_cache_timestamp(payload.get("updatedAt"))
    return None


def clear_matchup_details_cache(sport: str, year: int, week: int) -> None:
    sport = resolve_sport(sport)
    for path in _detail_cache_paths(sport, year, week):
        try:
            payload = _read_detail_payload(path)
            if payload and _payload_sport_ok(payload, sport):
                path.unlink(missing_ok=True)
        except OSError:
            pass


def load_cached_matchup_detail(
    sport: str,
    year: int,
    week: int,
    home_canon: str,
    away_canon: str,
) -> dict[str, Any] | None:
    sport = resolve_sport(sport)
    canonical = _detail_week_path(sport, year, week)
    for path in _detail_cache_paths(sport, year, week):
        payload = _read_detail_payload(path)
        if not payload:
            continue
        hit = _detail_from_payload(payload, sport, home_canon, away_canon)
        if hit:
            if path != canonical:
                save_matchup_detail_cache(sport, int(year), int(week), home_canon, away_canon, hit)
            return hit
    return None


def save_matchup_detail_cache(
    sport: str,
    year: int,
    week: int,
    home_canon: str,
    away_canon: str,
    detail: dict[str, Any],
) -> None:
    sport = resolve_sport(sport)
    path = _detail_week_path(sport, year, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "sport": sport_disk_tag(sport),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "details": {},
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing.get("details"), dict) and _payload_sport_ok(existing, sport):
                payload["details"] = dict(existing["details"])
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    payload["details"][_matchup_cache_key(home_canon, away_canon)] = detail
    payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def _build_fallback(
    sport: str,
    home_canon: str,
    away_canon: str,
    *,
    year: int,
    week: int,
    historical: bool,
    board_row: dict[str, Any],
) -> dict[str, Any]:
    slate_week = 1 if int(week) == 0 else int(week)
    slate = load_slate_df(sport, int(year), slate_week, historical=historical)
    return build_matchup_fallback(
        home_canon,
        away_canon,
        board_row=board_row,
        slate=slate,
        year=int(year),
        week=int(week),
        historical=historical,
    )


def build_matchup_detail_payload(
    sport: str,
    home_canon: str,
    away_canon: str,
    *,
    year: int,
    week: int,
    historical: bool,
    board_row: dict[str, Any],
    tab: str | None = None,
) -> dict[str, Any]:
    fallback = _build_fallback(
        sport, home_canon, away_canon,
        year=int(year), week=int(week), historical=historical, board_row=board_row,
    )

    detail = None
    if node_available() and not historical:
        detail = fetch_labs_matchup(home_canon, away_canon, int(year), int(week), tab=tab)

    if not detail or detail.get("error"):
        return fallback

    if not (detail.get("starters") or {}).get("home", {}).get("starters"):
        detail["starters"] = fallback.get("starters")
    detail.setdefault("logos", {})
    detail["logos"]["home"] = team_logo_url(
        home_canon,
        fallback=str(board_row.get("home_logo") or detail["logos"].get("home") or ""),
    )
    detail["logos"]["away"] = team_logo_url(
        away_canon,
        fallback=str(board_row.get("away_logo") or detail["logos"].get("away") or ""),
    )
    meta = detail.setdefault("meta", {})
    meta.setdefault("startDate", board_row.get("date"))
    meta.setdefault("venue", board_row.get("venue"))
    meta.setdefault("year", int(year))
    meta.setdefault("week", int(week))
    for key in ("venue_city", "venue_state", "indoor", "lat", "lon"):
        if board_row.get(key) is not None:
            meta.setdefault(key, board_row.get(key))
    detail["teamProfiles"] = fallback.get("teamProfiles")
    detail.setdefault("teamColors", fallback.get("teamColors"))
    return _merge_fallback_into_detail(detail, fallback, historical=historical)


def load_matchup_detail(
    sport: str,
    home_canon: str,
    away_canon: str,
    *,
    year: int,
    week: int,
    historical: bool,
    board_row: dict[str, Any],
    tab: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    sport = resolve_sport(sport)
    cached = None if force else load_cached_matchup_detail(sport, int(year), int(week), home_canon, away_canon)

    if cached and historical and _detail_frozen_ok(cached, historical=True) and not force:
        return cached

    fallback = _build_fallback(
        sport, home_canon, away_canon,
        year=int(year), week=int(week), historical=historical, board_row=board_row,
    )

    if cached and not force:
        detail = _merge_fallback_into_detail(cached, fallback, historical=historical)
    else:
        detail = build_matchup_detail_payload(
            sport,
            home_canon,
            away_canon,
            year=int(year),
            week=int(week),
            historical=historical,
            board_row=board_row,
            tab=tab,
        )
        if not _has_matchup_props(detail) or not _has_advanced_profile(detail):
            detail = _merge_fallback_into_detail(detail, fallback, historical=historical)

    save_matchup_detail_cache(sport, int(year), int(week), home_canon, away_canon, detail)
    return detail
