"""Projected starters — ESPN depthcharts first, CFBD+PPA fallback."""
from __future__ import annotations

import csv
import json
from functools import lru_cache
from typing import Any

from .config import DATA_DIR
from .espn_depth import fetch_espn_depthcharts, fetch_espn_roster_offense
from .team_registry import resolve_canonical, team_key

ROSTER_PATH = DATA_DIR / "cfbd_rosters_2026.json"
PPA_PATH = DATA_DIR / "cfb_2026_expected_ppa.csv"

OFFENSE_SLOTS = [
    ("QB", 1, "QB"),
    ("RB", 2, "RB"),
    ("TE", 1, "TE"),
    ("WR", 3, "WR"),
]


@lru_cache(maxsize=1)
def _load_rosters() -> dict[str, list[dict[str, Any]]]:
    if not ROSTER_PATH.exists():
        return {}
    try:
        raw = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    by_team: dict[str, list[dict[str, Any]]] = {}
    for p in raw:
        school = resolve_canonical(p.get("current_team")) or p.get("current_team")
        if not school:
            continue
        key = team_key(school)
        by_team.setdefault(key, []).append(
            {
                "athlete_id": str(p.get("athlete_id") or ""),
                "name": p.get("name"),
                "position": p.get("position"),
            }
        )
    return by_team


@lru_cache(maxsize=1)
def _load_ppa_index() -> tuple[dict[str, dict], dict[str, dict]]:
    by_athlete: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    if not PPA_PATH.exists():
        return by_athlete, by_name
    try:
        with open(PPA_PATH, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                ppa = row.get("expected_ppa")
                try:
                    ppa_f = float(ppa) if ppa not in (None, "") else None
                except (TypeError, ValueError):
                    ppa_f = None
                entry = {
                    "expected_ppa": ppa_f,
                    "position_group": row.get("position_group"),
                }
                aid = str(row.get("athlete_id") or "")
                if aid:
                    by_athlete[aid] = entry
                name_key = f"{row.get('name')}|{row.get('position')}"
                by_name[name_key] = entry
    except OSError:
        pass
    return by_athlete, by_name


def _pos_group(position: str | None, ppa_group: str | None = None) -> str | None:
    if ppa_group and ppa_group in {"QB", "RB", "WR", "TE"}:
        return ppa_group
    pos = str(position or "").upper()
    if pos in {"QB", "RB", "WR", "TE"}:
        return pos
    if pos in {"FB", "HB"}:
        return "RB"
    if pos in {"SE", "FL", "SL"}:
        return "WR"
    return None


def _build_cfbd_starters(team_name: str) -> dict[str, Any]:
    canonical = resolve_canonical(team_name) or team_name
    key = team_key(canonical)
    roster = _load_rosters().get(key, [])
    by_athlete, by_name = _load_ppa_index()

    enriched: list[dict[str, Any]] = []
    for p in roster:
        aid = p.get("athlete_id")
        ppa = by_athlete.get(str(aid)) or by_name.get(f"{p.get('name')}|{p.get('position')}") or {}
        group = _pos_group(p.get("position"), ppa.get("position_group"))
        if not group:
            continue
        enriched.append(
            {
                **p,
                "position_group": group,
                "expected_ppa": ppa.get("expected_ppa"),
            }
        )

    by_group: dict[str, list[dict]] = {}
    for p in enriched:
        g = p["position_group"]
        by_group.setdefault(g, []).append(p)
    for pool in by_group.values():
        pool.sort(key=lambda x: float(x.get("expected_ppa") or -1), reverse=True)

    starters: list[dict[str, str]] = []
    for group, count, prefix in OFFENSE_SLOTS:
        players = by_group.get(group, [])[:count]
        for i, p in enumerate(players):
            label = f"{prefix}{i + 1}" if count > 1 else prefix
            starters.append(
                {
                    "name": p.get("name"),
                    "position": p.get("position") or group,
                    "label": label,
                }
            )

    return {
        "starters": starters,
        "depthSource": "cfbd_rosters+expected_ppa",
        "starterCount": len(starters),
    }


def _build_espn_roster_starters(team_name: str) -> dict[str, Any]:
    """Rank ESPN roster offense by expected PPA within position group."""
    roster = fetch_espn_roster_offense(team_name)
    if not roster:
        return {"starters": [], "depthSource": "espn_roster_empty", "starterCount": 0}

    _, by_name = _load_ppa_index()
    enriched: list[dict[str, Any]] = []
    for p in roster:
        name = p.get("name") or ""
        pos = p.get("position") or ""
        ppa = by_name.get(f"{name}|{pos}") or by_name.get(f"{name}|QB") or {}
        enriched.append(
            {
                **p,
                "position_group": pos,
                "expected_ppa": ppa.get("expected_ppa"),
            }
        )

    by_group: dict[str, list[dict]] = {}
    for p in enriched:
        g = p["position_group"]
        by_group.setdefault(g, []).append(p)
    for pool in by_group.values():
        pool.sort(key=lambda x: float(x.get("expected_ppa") or -1), reverse=True)

    starters: list[dict[str, str]] = []
    for group, count, prefix in OFFENSE_SLOTS:
        players = by_group.get(group, [])[:count]
        for i, p in enumerate(players):
            label = f"{prefix}{i + 1}" if count > 1 else prefix
            starters.append(
                {
                    "name": p.get("name"),
                    "position": p.get("position") or group,
                    "label": label,
                }
            )

    return {
        "starters": starters,
        "depthSource": "espn_roster+expected_ppa",
        "starterCount": len(starters),
    }


def build_projected_starters(team_name: str) -> dict[str, Any]:
    """ESPN depthcharts when published; otherwise ESPN roster + PPA, then CFBD."""
    return _build_projected_starters_cached(team_name)


@lru_cache(maxsize=128)
def _build_projected_starters_cached(team_name: str) -> dict[str, Any]:
    espn = fetch_espn_depthcharts(team_name)
    if espn.get("starters") and espn.get("depthSource") == "espn_depthcharts":
        return espn

    espn_roster = _build_espn_roster_starters(team_name)
    if espn_roster.get("starters"):
        espn_roster["espnStatus"] = espn.get("depthSource")
        if espn.get("espnTeamId"):
            espn_roster["espnTeamId"] = espn["espnTeamId"]
        return espn_roster

    from .sport_context import SPORT_CFB, get_sport

    if get_sport() != SPORT_CFB:
        out = {"starters": [], "depthSource": "espn_only", "starterCount": 0}
        out["espnStatus"] = espn.get("depthSource")
        if espn.get("espnTeamId"):
            out["espnTeamId"] = espn["espnTeamId"]
        return out

    cfbd = _build_cfbd_starters(team_name)
    cfbd["espnStatus"] = espn.get("depthSource")
    if espn.get("espnTeamId"):
        cfbd["espnTeamId"] = espn["espnTeamId"]
    return cfbd
