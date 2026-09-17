"""ESPN projected starters — depthcharts API with roster fallback."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import DATA_DIR
from .espn_client import SESSION
from .sport_context import espn_site_url, espn_team_api_url, get_sport_config
from .team_registry import resolve_canonical, team_key, teams_match

ESPN_TEAM_IDS_PATH = DATA_DIR / "espn_team_ids.json"


def _parse_depth_positions(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Parse ESPN depthcharts payload when position blocks are present."""
    starters: list[dict[str, str]] = []
    positions = payload.get("positions") or payload.get("depthchart") or payload.get("items") or []
    if isinstance(positions, dict):
        positions = positions.get("items") or positions.get("positions") or []

    for block in positions or []:
        if not isinstance(block, dict):
            continue
        group = str(block.get("position") or block.get("name") or block.get("abbreviation") or "").upper()
        if group not in {"QB", "RB", "WR", "TE"}:
            continue
        athletes = block.get("athletes") or block.get("items") or block.get("players") or []
        depth = 0
        for athlete in athletes:
            if not isinstance(athlete, dict):
                continue
            depth += 1
            name = athlete.get("displayName") or athlete.get("fullName") or athlete.get("name")
            if not name:
                continue
            label = f"{group}{depth}" if depth > 1 or group in {"RB", "WR"} else group
            if group == "RB" and depth == 1:
                label = "RB1"
            elif group == "RB" and depth == 2:
                label = "RB2"
            elif group == "WR":
                label = f"WR{depth}"
            starters.append(
                {
                    "name": str(name),
                    "position": group,
                    "label": label,
                }
            )
            slot_limit = 1 if group in {"QB", "TE"} else (2 if group == "RB" else 3)
            if depth >= slot_limit:
                break
    return starters


@lru_cache(maxsize=1)
def _load_espn_team_ids() -> dict[str, str]:
    if ESPN_TEAM_IDS_PATH.exists():
        try:
            raw = json.loads(ESPN_TEAM_IDS_PATH.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in (raw.get("by_team_key") or raw).items()}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


@lru_cache(maxsize=1)
def _scoreboard_team_ids() -> dict[str, str]:
    """Build canonical team key -> ESPN numeric id from season scoreboard."""
    from .espn_client import _get

    site = espn_site_url()
    out: dict[str, str] = {}
    for year in (2026, 2025):
        try:
            data = _get(f"{site}/scoreboard?dates={year}&limit=400")
        except Exception:
            continue
        for ev in data.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            for side in comp.get("competitors") or []:
                tm = side.get("team") or {}
                tid = str(tm.get("id") or "")
                name = tm.get("displayName") or tm.get("shortDisplayName")
                if tid and name:
                    out[team_key(resolve_canonical(name) or name)] = tid
        if out:
            break
    return out


def espn_team_id(team_name: str) -> str | None:
    canonical = resolve_canonical(team_name) or team_name
    key = team_key(canonical)
    cached = _load_espn_team_ids().get(key)
    if cached:
        return cached
    board = _scoreboard_team_ids()
    if key in board:
        return board[key]
    for bk, tid in board.items():
        if teams_match(bk, canonical):
            return tid
    return None


def fetch_espn_depthcharts(team_name: str) -> dict[str, Any]:
    """Return projected starters from ESPN depthcharts when published."""
    tid = espn_team_id(team_name)
    if not tid:
        return {"starters": [], "depthSource": "espn_unmapped", "starterCount": 0}

    path = get_sport_config()["espn_path"]
    url = espn_team_api_url(tid, "depthcharts")
    try:
        r = SESSION.get(
            url,
            timeout=20,
            headers={"Referer": f"https://www.espn.com/{path}/team/depth/_/id/{tid}"},
        )
        payload = r.json() if r.ok else {}
    except (json.JSONDecodeError, OSError, ValueError):
        payload = {}

    starters = _parse_depth_positions(payload)
    source = "espn_depthcharts" if starters else "espn_unavailable"

    return {
        "starters": starters[:11],
        "depthSource": source,
        "starterCount": len(starters[:11]),
        "espnTeamId": tid,
    }


def _parse_espn_roster_offense(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Offense skill positions from ESPN team roster API."""
    raw: list[dict[str, Any]] = []
    for grp in payload.get("athletes") or []:
        if str(grp.get("position") or "").lower() != "offense":
            continue
        for athlete in grp.get("items") or []:
            if not isinstance(athlete, dict):
                continue
            pos_obj = athlete.get("position")
            if isinstance(pos_obj, dict):
                pos = str(pos_obj.get("abbreviation") or pos_obj.get("name") or "").upper()
            else:
                pos = str(pos_obj or "").upper()
            if pos not in {"QB", "RB", "WR", "TE", "FB", "HB"}:
                continue
            if pos in {"FB", "HB"}:
                pos = "RB"
            name = athlete.get("displayName") or athlete.get("fullName")
            if name:
                raw.append({"name": str(name), "position": pos})
    return raw


def fetch_espn_roster_offense(team_name: str) -> list[dict[str, str]]:
    import requests as _requests

    tid = espn_team_id(team_name)
    if not tid:
        return []
    path = get_sport_config()["espn_path"]
    url = espn_team_api_url(tid, "roster")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": f"https://www.espn.com/{path}/team/roster/_/id/{tid}",
    }
    try:
        r = _requests.get(url, timeout=20, headers=headers)
        payload = r.json() if r.ok else {}
    except (json.JSONDecodeError, OSError, ValueError):
        payload = {}
    return _parse_espn_roster_offense(payload)
