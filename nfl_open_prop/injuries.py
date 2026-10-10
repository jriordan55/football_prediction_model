"""ESPN injury report for the prop desk.

Out and doubtful teammates are named on the player page. When that player is
also on the slate, their recent production is shared with the teammates who
play the same position group.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "espn_cache"
URLS = {
    "nfl": "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
    "cfb": "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/injuries",
}
USAGE = {
    "pass_yds": frozenset({"QB"}),
    "pass_tds": frozenset({"QB"}),
    "pass_attempts": frozenset({"QB"}),
    "pass_completions": frozenset({"QB"}),
    "pass_rush_yds": frozenset({"QB"}),
    "rush_yds": frozenset({"RB", "FB"}),
    "rush_attempts": frozenset({"RB", "FB"}),
    "rr_yds": frozenset({"RB", "FB"}),
    "rec_yds": frozenset({"WR", "TE"}),
    "receptions": frozenset({"WR", "TE"}),
    "tds": frozenset({"QB", "RB", "FB", "WR", "TE"}),
}
_FRESH_SECONDS = 3 * 60 * 60


def norm_name(value: str) -> str:
    text = str(value or "").lower().replace(".", " ").replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z\s]", " ", text).split())


def availability_of(status: str) -> float | None:
    text = str(status or "").strip().lower()
    if not text or text in {"active", "healthy"} or text.startswith("probable"):
        return None
    if "doubtful" in text:
        return 0.25
    if "out" in text or "reserve" in text or text in {"ir", "pup", "suspended", "inactive"}:
        return 0.0
    if "questionable" in text or "day" in text:
        return 0.55
    return None


def load_injuries(sport: str) -> list[dict]:
    path = CACHE / f"injuries_{sport}.json"
    cached = _read(path)
    rows = list((cached or {}).get("rows") or [])
    fresh = cached and time.time() - float(cached.get("fetchedAt") or 0) < _FRESH_SECONDS
    if fresh and rows and any(row.get("id") for row in rows[:30]):
        return rows
    try:
        response = requests.get(URLS[sport], timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        rows = _parse(response.json())
    except (requests.RequestException, ValueError, KeyError):
        return list((cached or {}).get("rows") or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetchedAt": time.time(), "rows": rows}), encoding="utf-8")
    return rows


def _athlete_id(athlete: dict) -> str:
    href = str((athlete.get("headshot") or {}).get("href") or "")
    match = re.search(r"/(\d+)\.png", href)
    if match:
        return match.group(1)
    for note in (athlete.get("notes") or {}).get("items") or []:
        ref = str((note.get("injury") or {}).get("$ref") or "")
        found = re.search(r"/athletes/(\d+)/", ref)
        if found:
            return found.group(1)
    return str(athlete.get("id") or "")


def _parse(payload: dict) -> list[dict]:
    rows = []
    for bucket in payload.get("injuries") or []:
        team = str(bucket.get("displayName") or "")
        for item in bucket.get("injuries") or []:
            athlete = item.get("athlete") or {}
            status = item.get("status")
            if isinstance(status, dict):
                status = status.get("description") or status.get("name") or ""
            avail = availability_of(str(status or ""))
            if avail is None:
                continue
            name = str(athlete.get("displayName") or "")
            position = str((athlete.get("position") or {}).get("abbreviation") or "").upper()
            if not name or not team:
                continue
            rows.append(
                {
                    "id": _athlete_id(athlete),
                    "name": name,
                    "team": team,
                    "position": position,
                    "status": str(status or ""),
                    "availability": avail,
                }
            )
    return rows


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
