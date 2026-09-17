"""Filter Onyx props to ESPN/CFBD projected starters."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

import pandas as pd

from .depth_chart import build_projected_starters
from .team_registry import resolve_canonical, team_key


def _normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=256)
def _starters_for_team(team: str) -> tuple[tuple[str, str, str], ...]:
    canonical = resolve_canonical(team) or team
    payload = build_projected_starters(canonical)
    rows: list[tuple[str, str, str]] = []
    for starter in payload.get("starters") or []:
        name = str(starter.get("name") or "").strip()
        if not name:
            continue
        pos = str(starter.get("position") or starter.get("label") or "").strip()
        rows.append((_normalize_name(name), name, pos))
    return tuple(rows)


def build_starter_index(teams: list[str]) -> dict[str, dict[str, Any]]:
    """Map normalized player name -> {name, position, team} for projected starters."""
    out: dict[str, dict[str, Any]] = {}
    for team in teams:
        if not team:
            continue
        canonical = resolve_canonical(team) or team
        for norm, name, pos in _starters_for_team(canonical):
            out[norm] = {"name": name, "position": pos, "team": canonical}
    return out


def starter_for_player(
    player: str,
    *,
    home: str | None,
    away: str | None,
    team: str | None = None,
) -> dict[str, Any] | None:
    norm = _normalize_name(player)
    if not norm:
        return None
    teams = [resolve_canonical(t) or t for t in (team, home, away) if t]
    for tm in teams:
        for _, name, pos in _starters_for_team(tm):
            if _normalize_name(name) == norm:
                return {"name": name, "position": pos, "team": tm}
    index = build_starter_index(teams)
    return index.get(norm)


def is_projected_starter(player: str, *, home: str | None, away: str | None, team: str | None = None) -> bool:
    return starter_for_player(player, home=home, away=away, team=team) is not None


def filter_to_projected_starters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    teams: set[str] = set()
    for col in ("home", "away", "team", "teamLabel"):
        if col in df.columns:
            teams.update(str(v) for v in df[col].dropna().unique() if str(v).strip())
    starter_index = build_starter_index(sorted(teams))

    keep: list[pd.Series] = []
    for _, row in df.iterrows():
        player = str(row.get("player") or "")
        norm = _normalize_name(player)
        if norm in starter_index:
            keep.append(row)
            continue
        if is_projected_starter(
            player,
            home=str(row.get("home") or ""),
            away=str(row.get("away") or ""),
            team=str(row.get("team") or row.get("teamLabel") or ""),
        ):
            keep.append(row)
    if not keep:
        return pd.DataFrame()
    return pd.DataFrame(keep).reset_index(drop=True)


def enrich_starter_metadata(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    positions: list[str | None] = []
    teams: list[str | None] = []
    for _, row in out.iterrows():
        info = starter_for_player(
            str(row.get("player") or ""),
            home=str(row.get("home") or ""),
            away=str(row.get("away") or ""),
            team=str(row.get("team") or row.get("teamLabel") or ""),
        )
        positions.append(str(row.get("position") or "") or (info or {}).get("position"))
        teams.append(str(row.get("team") or row.get("teamLabel") or "") or (info or {}).get("team"))
    out["position"] = positions
    out["team"] = teams
    return out
