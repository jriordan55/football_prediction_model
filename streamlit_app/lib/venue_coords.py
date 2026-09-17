"""Resolve stadium coordinates for weather forecasts."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import requests

from lib.config import DATA_DIR
from lib.team_registry import normalize_team_key, resolve_canonical

CACHE_PATH = DATA_DIR / "venue_coords_cache.json"
FBS_TEAMS_PATH = DATA_DIR / "cfbd_fbs_teams.json"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"


@lru_cache(maxsize=1)
def _team_stadium_coords() -> dict[str, tuple[float, float]]:
    """Home stadium lat/lon keyed by normalized school name."""
    out: dict[str, tuple[float, float]] = {}
    if not FBS_TEAMS_PATH.exists():
        return out
    try:
        teams = json.loads(FBS_TEAMS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    for t in teams:
        loc = t.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            coords = (float(lat), float(lon))
        except (TypeError, ValueError):
            continue
        school = str(t.get("school") or "")
        if school:
            out[normalize_team_key(school)] = coords
        for alias in t.get("alternateNames") or []:
            out[normalize_team_key(str(alias))] = coords
    return out


def _load_cache() -> dict[str, dict[str, float]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, dict[str, float]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _cache_key(*, venue: str, city: str, state: str, home: str) -> str:
    parts = [str(home or "").strip().lower(), str(venue or "").strip().lower(), str(city or "").strip().lower(), str(state or "").strip().lower()]
    return "|".join(p for p in parts if p)


def _geocode_query(*, venue: str, city: str, state: str, home: str) -> str | None:
    city = str(city or "").strip()
    state = str(state or "").strip()
    venue = str(venue or "").strip()
    home = str(home or "").strip()
    if city and state:
        return f"{city}, {state}, USA"
    if city:
        return f"{city}, USA"
    if venue and state:
        return f"{venue}, {state}, USA"
    if venue:
        return f"{venue}, USA"
    return None


def _fetch_coords(query: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            GEOCODE,
            params={"name": query, "count": 5, "language": "en", "format": "json", "countryCode": "US"},
            timeout=12,
        )
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
        if not results:
            return None
        hit = results[0]
        return float(hit["latitude"]), float(hit["longitude"])
    except Exception:
        return None


@lru_cache(maxsize=512)
def resolve_venue_coords(
    venue: str = "",
    city: str = "",
    state: str = "",
    home: str = "",
) -> tuple[float, float] | None:
    """Return lat/lon for a stadium — CFBD team file, disk cache, then geocoding."""
    home_canon = resolve_canonical(home) or home
    stadiums = _team_stadium_coords()
    for candidate in (home_canon, home):
        key = normalize_team_key(str(candidate or ""))
        if key in stadiums:
            return stadiums[key]

    key = _cache_key(venue=venue, city=city, state=state, home=home_canon or home)
    if key:
        cache = _load_cache()
        if key in cache:
            hit = cache[key]
            return float(hit["lat"]), float(hit["lon"])

    query = _geocode_query(venue=venue, city=city, state=state, home=home_canon or home)
    if not query:
        return None

    coords = _fetch_coords(query)
    if coords is None and venue and city:
        coords = _fetch_coords(f"{venue}, {city}, USA")

    if coords and key:
        cache = _load_cache()
        cache[key] = {"lat": coords[0], "lon": coords[1], "query": query}
        _save_cache(cache)
    return coords


def coords_for_game(row: dict[str, Any] | Any) -> tuple[float, float] | None:
    """Extract coordinates from a scoreboard / board row."""
    if hasattr(row, "to_dict"):
        row = row.to_dict()
    lat, lon = row.get("lat"), row.get("lon")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    return resolve_venue_coords(
        venue=str(row.get("venue") or ""),
        city=str(row.get("venue_city") or ""),
        state=str(row.get("venue_state") or ""),
        home=str(row.get("home") or ""),
    )
