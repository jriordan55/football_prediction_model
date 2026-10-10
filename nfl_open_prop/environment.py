"""Scale a prop mean for the opponent defense and this game's spread and total.

The player's log is already his own offense, so that rating is not applied
again. Defense comes from the saved nfelo EPA (NFL) or SP+ (college) files.
The spread and total piece follows the board's game-environment factors.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NFELO_PATH = ROOT / "data" / "nfelo_ratings.json"
SP_DIR = ROOT / "data" / "puntandrally"
DEF_CAP = 24.0
EPA_DEF_STD = 0.08

NFL_BASELINE_TOTAL = 45.0
CFB_BASELINE_TOTAL = 54.0

_VOLUME = {
    "pass_yds",
    "pass_attempts",
    "pass_completions",
    "pass_rush_yds",
    "rec_yds",
    "receptions",
    "rr_yds",
    "rush_yds",
    "rush_attempts",
}
_TD = {"pass_tds", "tds"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _script_key(prop_key: str) -> str:
    if prop_key == "rr_yds":
        return "rush_yds"
    if prop_key == "pass_rush_yds":
        return "pass_yds"
    if prop_key in ("rec_yds", "receptions"):
        return "pass_yds"
    return prop_key


def team_margin(side: str | None, spread: float | None) -> float | None:
    """Scoring margin for the player's team. Positive means his team is favored."""
    if spread is None or side not in ("home", "away"):
        return None
    if side == "home":
        return -float(spread)
    return float(spread)


def script_factor(prop_key: str, margin: float | None) -> float:
    if margin is None:
        return 1.0
    key = _script_key(prop_key)
    if key in ("pass_yds", "pass_attempts", "pass_completions"):
        if margin > 7:
            lead = min(28.0, float(margin))
            return max(0.78, 1.0 - (lead - 7.0) * 0.016)
        if margin < -7:
            trail = min(21.0, abs(float(margin)))
            return min(1.08, 1.0 + trail * 0.006)
    if key == "pass_tds" and margin > 10:
        return max(0.85, 1.0 - (min(28.0, margin) - 10.0) * 0.012)
    if key in ("rush_yds", "rush_attempts"):
        if margin > 10:
            return min(1.12, 1.0 + (min(28.0, margin) - 10.0) * 0.008)
        if margin < -10:
            trail = min(28.0, abs(float(margin)))
            return max(0.82, 1.0 - (trail - 10.0) * 0.012)
    if key == "tds" and margin > 10:
        return max(0.88, 1.0 - (min(28.0, margin) - 10.0) * 0.006)
    return 1.0


def volume_factor(
    prop_key: str,
    *,
    side: str | None,
    spread: float | None,
    total: float | None,
    sport: str,
) -> float:
    if prop_key not in _VOLUME and prop_key not in _TD:
        return 1.0
    baseline = NFL_BASELINE_TOTAL if sport == "nfl" else CFB_BASELINE_TOTAL
    vol = 1.0
    if spread is not None and total is not None and side in ("home", "away"):
        team_pts = (float(total) - float(spread)) / 2.0 if side == "home" else (float(total) + float(spread)) / 2.0
        raw = team_pts / max(baseline / 2.0, 1.0)
        vol = _clamp(0.86 + (raw - 1.0) * 0.72, 0.84, 1.16)
    elif total is not None:
        vol = _clamp(1.0 + (float(total) - baseline) / baseline * 0.48, 0.88, 1.12)
    if prop_key in _TD:
        return 0.55 + 0.45 * vol
    return vol


def market_factor(
    prop_key: str,
    *,
    side: str | None,
    spread: float | None,
    total: float | None,
    sport: str,
) -> float:
    if spread is None and total is None:
        return 1.0
    vol = volume_factor(prop_key, side=side, spread=spread, total=total, sport=sport)
    return vol * script_factor(prop_key, team_margin(side, spread))


def _team_key(name: str) -> str:
    from lib.team_registry import normalize_team_key

    return normalize_team_key(name)


def _sensitivity(prop_key: str) -> float:
    return {
        "pass_yds": 0.55,
        "pass_attempts": 0.45,
        "pass_completions": 0.45,
        "pass_rush_yds": 0.55,
        "rush_yds": 0.65,
        "rush_attempts": 0.6,
        "rr_yds": 0.6,
        "rec_yds": 0.55,
        "receptions": 0.55,
        "pass_tds": 0.45,
        "tds": 0.45,
    }.get(prop_key, 0.55)


@lru_cache(maxsize=1)
def _nfelo() -> dict[str, dict]:
    if not NFELO_PATH.exists():
        return {}
    payload = json.loads(NFELO_PATH.read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for row in (payload.get("teams") or {}).values():
        for label in (row.get("team"), row.get("team_nick"), row.get("abbr")):
            key = _team_key(str(label or ""))
            if key:
                index[key] = row
    return index


def _latest_sp_path() -> Path | None:
    files = sorted(SP_DIR.glob("sp_*_w*.json"))
    if files:
        return files[-1]
    index = SP_DIR / "sp_2026_index.json"
    return index if index.exists() else None


@lru_cache(maxsize=1)
def _sp() -> dict[str, dict]:
    path = _latest_sp_path()
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    teams = payload.get("teams") or {}
    rows = teams.values() if isinstance(teams, dict) else teams
    for row in rows:
        for label in (row.get("key"), row.get("team")):
            key = _team_key(str(label or ""))
            if key:
                index[key] = row
    return index


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[len(ordered) // 2]


def defense_multiplier(sport: str, opponent: str, prop_key: str, season: int, week: int) -> float:
    """1.0 is a league-average defense. Above 1 is a softer defense."""
    del season, week
    key = _team_key(opponent)
    if not key:
        return 1.0
    sense = _sensitivity(prop_key)
    if sport == "cfb":
        index = _sp()
        row = index.get(key)
        if not row or row.get("spDef") is None:
            return 1.0
        median = _median([float(item["spDef"]) for item in index.values() if item.get("spDef") is not None])
        delta = float(row["spDef"]) - median
        raw = (delta / max(median, 1.0)) * 100.0 * sense
    else:
        index = _nfelo()
        row = index.get(key)
        if not row:
            return 1.0
        field = "def_epa_rush" if prop_key in ("rush_yds", "rush_attempts", "rr_yds") else "def_epa_pass"
        values = [float(item.get(field) or 0.0) for item in index.values()]
        delta = float(row.get(field) or 0.0) - _median(values)
        raw = (delta / EPA_DEF_STD) * 100.0 * sense * 0.35
    pct = max(-DEF_CAP, min(DEF_CAP, raw))
    return 1.0 + pct / 100.0
