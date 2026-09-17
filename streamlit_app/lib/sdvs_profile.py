"""Advanced team profile from SDVS team summaries CSV."""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .team_registry import team_key

SDVS_DIR = DATA_DIR / "sdvs_team_summaries" / "cache"

ADVANCED_OFFENSE = [
    ("EPAplay_off", "EPAplay_off_rank", "PPA / play", "ppa"),
    ("success_off", "success_off_rank", "Success rate", "pct"),
    ("explosive_off", "explosive_off_rank", "Explosiveness", "num2"),
    ("EPAplay_off_rush", "EPAplay_off_rush_rank", "Rush PPA", "ppa"),
    ("success_off_rush", "success_off_rush_rank", "Rush success", "pct"),
    ("EPAplay_off_pass", "EPAplay_off_pass_rank", "Pass PPA", "ppa"),
    ("success_off_pass", "success_off_pass_rank", "Pass success", "pct"),
    ("success_off", "success_off_rank", "Std-downs success", "pct"),
    ("late_down_success_off", "late_down_success_off_rank", "Passing-downs success", "pct"),
    ("line_yards_off", "line_yards_off_rank", "Line yards", "num2"),
    ("play_stuffed_off", "play_stuffed_off_rank", "Stuff rate", "pct"),
    ("playsgame_off", "playsgame_off_rank", "Plays / game", "num1"),
    ("drivesgame_off", "drivesgame_off_rank", "Drives / game", "num1"),
]

ADVANCED_DEFENSE = [
    ("EPAplay_def", "EPAplay_def_rank", "PPA / play allowed", "ppa"),
    ("success_def", "success_def_rank", "Success rate allowed", "pct"),
    ("explosive_def", "explosive_def_rank", "Explosiveness allowed", "num2"),
    ("EPAplay_def_rush", "EPAplay_def_rush_rank", "Rush PPA allowed", "ppa"),
    ("success_def_rush", "success_def_rush_rank", "Rush success allowed", "pct"),
    ("EPAplay_def_pass", "EPAplay_def_pass_rank", "Pass PPA allowed", "ppa"),
    ("success_def_pass", "success_def_pass_rank", "Pass success allowed", "pct"),
    ("success_def", "success_def_rank", "Std-downs success allowed", "pct"),
    ("late_down_success_def", "late_down_success_def_rank", "Passing-downs success allowed", "pct"),
    ("line_yards_def", "line_yards_def_rank", "Line yards allowed", "num2"),
    ("play_stuffed_def", "play_stuffed_def_rank", "Stuff rate", "pct"),
    ("playsgame_def", "playsgame_def_rank", "Plays / game", "num1"),
    ("drivesgame_def", "drivesgame_def_rank", "Drives / game", "num1"),
]


def _fmt_metric(value: Any, fmt: str) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if fmt == "pct":
        return f"{n * 100:.1f}%"
    if fmt == "ppa":
        return f"{n:.3f}"
    if fmt == "num2":
        return f"{n:.2f}"
    if fmt == "num1":
        return f"{n:.1f}"
    return str(n)


@lru_cache(maxsize=4)
def load_sdvs_latest(year: int) -> dict[str, Any] | None:
    path = SDVS_DIR / f"summaries_{year}.csv"
    if not path.exists():
        return None

    latest: dict[str, dict[str, str]] = {}
    max_week = 0
    try:
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    through = int(float(row.get("through_week") or 0))
                except (TypeError, ValueError):
                    continue
                max_week = max(max_week, through)
                team = row.get("pos_team") or ""
                key = team_key(team)
                if key not in latest or through >= int(float(latest[key].get("through_week") or 0)):
                    latest[key] = dict(row)
    except OSError:
        return None

    if not latest:
        return None
    return {"latest": latest, "maxWeek": max_week, "year": year}


def _lookup_row(sdvs: dict[str, Any] | None, team_name: str) -> dict[str, str] | None:
    if not sdvs:
        return None
    return (sdvs.get("latest") or {}).get(team_key(team_name))


def _build_table(metrics: list[tuple], away_row: dict | None, home_row: dict | None) -> list[dict[str, Any]]:
    out = []
    for key, rank_key, label, fmt in metrics:
        ar, hr = away_row or {}, home_row or {}
        out.append(
            {
                "label": label,
                "away": {
                    "value": _fmt_metric(ar.get(key), fmt),
                    "rank": f"#{round(float(ar.get(rank_key)))}" if ar.get(rank_key) not in (None, "") else "—",
                },
                "home": {
                    "value": _fmt_metric(hr.get(key), fmt),
                    "rank": f"#{round(float(hr.get(rank_key)))}" if hr.get(rank_key) not in (None, "") else "—",
                },
            }
        )
    return out


def build_nfelo_advanced_profile(home: str, away: str) -> dict[str, Any]:
    from .nfelo_ratings import _load_nfelo_index, lookup_nfelo_team, nfelo_meta
    from .nfl_team_registry import team_key as nfl_team_key

    away_row = lookup_nfelo_team(away)
    home_row = lookup_nfelo_team(home)
    index = _load_nfelo_index()
    meta = nfelo_meta()
    season = None
    for row in (away_row, home_row):
        if row and row.get("season") is not None:
            season = int(row["season"])
            break

    def _rank_map(field: str, *, ascending: bool = False) -> dict[str, int]:
        ordered = sorted(
            index.values(),
            key=lambda r: float(r.get(field) or 0.0),
            reverse=not ascending,
        )
        out: dict[str, int] = {}
        for i, row in enumerate(ordered):
            nk = nfl_team_key(str(row.get("team") or ""))
            if nk and nk not in out:
                out[nk] = i + 1
        return out

    nfelo_offense = [
        ("off_epa_play", "EPA / play", "ppa", False),
        ("off_epa_pass", "Pass EPA", "ppa", False),
        ("off_epa_rush", "Rush EPA", "ppa", False),
        ("pts_for", "Points / game", "num1", False),
    ]
    nfelo_defense = [
        ("def_epa_play", "EPA / play allowed", "ppa", True),
        ("def_epa_pass", "Pass EPA allowed", "ppa", True),
        ("def_epa_rush", "Rush EPA allowed", "ppa", True),
        ("pts_against", "Points allowed / game", "num1", True),
    ]

    def _build_nfelo_table(
        metrics: list[tuple[str, str, str, bool]],
        away: dict[str, Any] | None,
        home: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for field, label, fmt, asc in metrics:
            ranks = _rank_map(field, ascending=asc)
            ar, hr = away or {}, home or {}
            away_nk = nfl_team_key(str(ar.get("team") or ""))
            home_nk = nfl_team_key(str(hr.get("team") or ""))
            out.append(
                {
                    "label": label,
                    "away": {
                        "value": _fmt_metric(ar.get(field), fmt),
                        "rank": f"#{ranks[away_nk]}" if away_nk in ranks else "—",
                    },
                    "home": {
                        "value": _fmt_metric(hr.get(field), fmt),
                        "rank": f"#{ranks[home_nk]}" if home_nk in ranks else "—",
                    },
                }
            )
        return out

    return {
        "sdvsYear": season or meta.get("updated_at", "")[:4] or "",
        "sdvsWeek": 16,
        "offense": _build_nfelo_table(nfelo_offense, away_row, home_row),
        "defense": _build_nfelo_table(nfelo_defense, away_row, home_row),
    }


def build_advanced_profile(home: str, away: str, *, sdvs_year: int | None = None) -> dict[str, Any]:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        return build_nfelo_advanced_profile(home, away)

    year = sdvs_year or 2026
    sdvs = load_sdvs_latest(year)
    away_row = _lookup_row(sdvs, away)
    home_row = _lookup_row(sdvs, home)
    return {
        "sdvsYear": year,
        "sdvsWeek": sdvs.get("maxWeek") if sdvs else None,
        "offense": _build_table(ADVANCED_OFFENSE, away_row, home_row),
        "defense": _build_table(ADVANCED_DEFENSE, away_row, home_row),
    }


def team_tempo(team_name: str, *, sdvs_year: int = 2026) -> dict[str, Any] | None:
    row = _lookup_row(load_sdvs_latest(sdvs_year), team_name)
    if not row:
        return None
    try:
        plays = float(row.get("playsgame_off"))
        rank = int(float(row.get("playsgame_off_rank")))
    except (TypeError, ValueError):
        return None
    return {"playsPerGame": plays, "playsRank": rank, "secondsPerPlay": None}
