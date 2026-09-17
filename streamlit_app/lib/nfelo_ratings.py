"""nfelo power ratings — load + team lookup (https://www.nfeloapp.com/nfl-power-ratings/)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .nfl_team_registry import normalize_team_key, resolve_canonical, team_key

RATINGS_PATH = DATA_DIR / "nfelo_ratings.json"
FETCH_SCRIPT = DATA_DIR.parent / "scripts" / "fetch_nfelo_ratings.py"
NFelo_URL = "https://www.nfeloapp.com/nfl-power-ratings/"
MAX_AGE_HOURS = 18


def _mtime_age_hours(path: Path) -> float | None:
    if not path.exists():
        return None
    age_sec = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age_sec / 3600.0


def ensure_nfelo_ratings(*, force: bool = False) -> bool:
    """Refresh nfelo ratings when cache is missing or stale."""
    age = _mtime_age_hours(RATINGS_PATH)
    if not force and age is not None and age < MAX_AGE_HOURS:
        return True
    if not FETCH_SCRIPT.exists():
        return RATINGS_PATH.exists()
    try:
        subprocess.run(
            [sys.executable, str(FETCH_SCRIPT)],
            cwd=str(FETCH_SCRIPT.parent.parent),
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        _load_nfelo_index.cache_clear()
        _def_rank_map.cache_clear()
        _league_median_def_epa.cache_clear()
        return RATINGS_PATH.exists()
    except (OSError, subprocess.SubprocessError):
        return RATINGS_PATH.exists()


@lru_cache(maxsize=1)
def _load_nfelo_index() -> dict[str, dict[str, Any]]:
    if not RATINGS_PATH.exists():
        return {}
    try:
        raw = json.loads(RATINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    teams = raw.get("teams") if isinstance(raw, dict) else None
    if isinstance(teams, dict):
        return teams
    by_name = raw.get("by_name") if isinstance(raw, dict) else None
    if isinstance(by_name, dict):
        return {team_key(k): v for k, v in by_name.items() if isinstance(v, dict)}
    return {}


def lookup_nfelo_team(team_name: str, *, refresh: bool = True) -> dict[str, Any] | None:
    if refresh:
        ensure_nfelo_ratings()
    index = _load_nfelo_index()
    if not index:
        return None
    for cand in (resolve_canonical(team_name), team_name):
        if not cand:
            continue
        hit = index.get(team_key(cand))
        if hit:
            return {**hit, "team": hit.get("team") or cand}
        nk = normalize_team_key(str(cand))
        for row in index.values():
            if normalize_team_key(str(row.get("team") or "")) == nk:
                return row
    return None


def nfelo_meta() -> dict[str, Any]:
    if not RATINGS_PATH.exists():
        return {"source": NFelo_URL, "updated_at": None, "team_count": 0}
    try:
        raw = json.loads(RATINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"source": NFelo_URL, "updated_at": None, "team_count": 0}
    return {
        "source": raw.get("source") or NFelo_URL,
        "updated_at": raw.get("updated_at"),
        "team_count": raw.get("team_count") or len(raw.get("teams") or {}),
    }


@lru_cache(maxsize=1)
def _def_rank_map() -> dict[str, int]:
    """Lower def_epa_rank = better defense (rank 1 = best)."""
    index = _load_nfelo_index()
    ranks: dict[str, int] = {}
    for row in index.values():
        rank = row.get("def_epa_rank")
        if rank is None:
            continue
        for label in (row.get("team"), row.get("abbr")):
            if not label:
                continue
            nk = normalize_team_key(str(label))
            if nk and nk not in ranks:
                ranks[nk] = int(rank)
    return ranks


def lookup_nfelo_def_rank(team_name: str) -> int | None:
    ensure_nfelo_ratings()
    ranks = _def_rank_map()
    for cand in (team_name, resolve_canonical(team_name)):
        if not cand:
            continue
        hit = ranks.get(normalize_team_key(str(cand)))
        if hit:
            return hit
    return None


@lru_cache(maxsize=8)
def _league_median_def_epa(prop_key: str) -> float:
    index = _load_nfelo_index()
    field = _def_epa_field(prop_key)
    vals = sorted(float(row.get(field) or 0.0) for row in index.values())
    if not vals:
        return 0.0
    return vals[len(vals) // 2]


def _def_epa_field(prop_key: str) -> str:
    pk = (prop_key or "").lower()
    if pk in ("rush_yds", "rush_attempts"):
        return "def_epa_rush"
    if pk in ("pass_yds", "pass_yds_q1", "pass_tds", "pass_attempts", "pass_completions"):
        return "def_epa_pass"
    return "def_epa_pass"


def clear_nfelo_cache() -> None:
    _load_nfelo_index.cache_clear()
    _def_rank_map.cache_clear()
    _league_median_def_epa.cache_clear()
