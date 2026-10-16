"""Which games belong in a player's rate.

An NFL game counts at 10 offensive snaps, whether or not he started. A college game counts when he started.
Snap counts are the nflverse table. The start comes from the ESPN game roster.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

import requests

from nfl_open_prop.injuries import norm_name

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "espn_cache"
MIN_SNAPS = 10
_SNAP_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{year}.csv"
_FRESH_SECONDS = 12 * 60 * 60
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_ABBR = {
    "GNB": "GB",
    "NOR": "NO",
    "NWE": "NE",
    "TAM": "TB",
    "KAN": "KC",
    "SFO": "SF",
    "JAC": "JAX",
    "LVR": "LV",
    "WSH": "WAS",
    "WFT": "WAS",
    "LA": "LAR",
    "SD": "LAC",
    "OAK": "LV",
    "STL": "LAR",
}


def abbr(code: str) -> str:
    text = str(code or "").strip().upper()
    return _ABBR.get(text, text)


def team_abbr(name: str) -> str:
    from lib.nfl_team_registry import NFL_TEAMS, team_key

    key = team_key(name)
    for canonical, code, _aliases in NFL_TEAMS:
        if team_key(canonical) == key:
            return abbr(code)
    return ""


def load_snaps(years: tuple[int, ...]) -> dict[tuple[int, int, str], list[dict[str, Any]]]:
    index: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for year in years:
        for row in _snap_rows(year):
            if str(row.get("game_type") or "").upper() == "PRE":
                continue
            try:
                season = int(row.get("season") or 0)
                week = int(row.get("week") or 0)
                snaps = int(float(row.get("offense_snaps") or 0))
            except (TypeError, ValueError):
                continue
            name = norm_name(str(row.get("player") or ""))
            if not name or week <= 0:
                continue
            index.setdefault((season, week, name), []).append(
                {"team": abbr(str(row.get("team") or "")), "opp": abbr(str(row.get("opponent") or "")), "snaps": snaps}
            )
    return index


def offense_snaps(
    index: dict[tuple[int, int, str], list[dict[str, Any]]],
    name: str,
    season: int,
    week: int,
    team: str,
    opponent: str,
) -> int | None:
    rows = index.get((int(season), int(week), norm_name(name))) or []
    if not rows:
        return None
    opp = abbr(opponent)
    side = abbr(team)
    opp_hits = [row for row in rows if row["opp"] == opp]
    if len(opp_hits) == 1:
        return int(opp_hits[0]["snaps"])
    team_hits = [row for row in rows if row["team"] == side] if side else []
    if len(team_hits) == 1:
        return int(team_hits[0]["snaps"])
    if len(rows) == 1:
        return int(rows[0]["snaps"])
    return None


def load_role(event_id: str) -> dict[str, tuple[bool, bool]] | None:
    path = CACHE / f"roles_{event_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, dict):
        return None
    out: dict[str, tuple[bool, bool]] = {}
    for pid, role in players.items():
        if not isinstance(role, dict):
            continue
        out[str(pid)] = (bool(role.get("starter")), bool(role.get("didNotPlay")))
    return out


def fetch_role(event_id: str, league: str) -> dict[str, tuple[bool, bool]] | None:
    cached = load_role(event_id)
    if cached is not None:
        return cached
    url = (
        "https://sports.core.api.espn.com/v2/sports/football/leagues/"
        f"{league}/events/{event_id}/competitions/{event_id}/competitors"
    )
    try:
        response = requests.get(url, timeout=30, headers=_HEADERS)
        response.raise_for_status()
        items = response.json().get("items") or []
    except (requests.RequestException, ValueError):
        return None
    players: dict[str, dict[str, Any]] = {}
    loaded = 0
    for item in items:
        ref = ((item.get("roster") or {}).get("$ref")) if isinstance(item, dict) else None
        if not ref:
            continue
        try:
            roster = requests.get(str(ref), timeout=30, headers=_HEADERS)
            roster.raise_for_status()
            entries = roster.json().get("entries") or []
        except (requests.RequestException, ValueError):
            continue
        loaded += 1
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("playerId") or "")
            if not pid:
                continue
            position = entry.get("position") or {}
            position_id = ""
            if isinstance(position, dict):
                ref_id = str(position.get("$ref") or "")
                position_id = ref_id.split("/positions/")[-1].split("?")[0] if "/positions/" in ref_id else ""
            players[pid] = {
                "starter": bool(entry.get("starter")),
                "didNotPlay": bool(entry.get("didNotPlay")),
                "position": position_id,
            }
    if loaded < 2 or not players:
        return None
    path = CACHE / f"roles_{event_id}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"players": players}), encoding="utf-8")
    except OSError:
        pass
    return {pid: (bool(role["starter"]), bool(role["didNotPlay"])) for pid, role in players.items()}


def _snap_rows(year: int) -> list[dict[str, str]]:
    path = CACHE / f"snap_counts_{year}.csv"
    fresh = path.exists() and (year < 2026 or time.time() - path.stat().st_mtime < _FRESH_SECONDS)
    if not fresh:
        try:
            response = requests.get(_SNAP_URL.format(year=year), timeout=60, headers=_HEADERS)
            response.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        except (requests.RequestException, OSError):
            if not path.exists():
                return []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []
