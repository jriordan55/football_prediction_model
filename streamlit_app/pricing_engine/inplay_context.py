"""Shared play snapshot context for in-play CSV rows."""
from __future__ import annotations

from typing import Any


def build_play_context(
    *,
    play: dict[str, Any],
    situation: dict[str, Any],
    away_yards: dict[str, float],
    home_yards: dict[str, float],
    win_prob_home: float | None,
    pregame: dict[str, Any] | None,
) -> dict[str, Any]:
    hp = win_prob_home
    away_wp = (100.0 - hp) if hp is not None else None
    return {
        "down": play.get("down") or _down_from_situation(situation),
        "distance": play.get("distance") or _distance_from_situation(situation),
        "possession": play.get("possession") or situation.get("possession"),
        "yard_line": play.get("yardLine") or situation.get("yardLine"),
        "home_win_prob": round(hp, 1) if hp is not None else None,
        "away_win_prob": round(away_wp, 1) if away_wp is not None else None,
        "home_pass_yds": _num(home_yards.get("pass")),
        "home_rush_yds": _num(home_yards.get("rush")),
        "home_total_yds": _num(home_yards.get("total")),
        "away_pass_yds": _num(away_yards.get("pass")),
        "away_rush_yds": _num(away_yards.get("rush")),
        "away_total_yds": _num(away_yards.get("total")),
        "pregame_capture_at": (pregame or {}).get("_captured_at"),
        "pregame_capture_type": (pregame or {}).get("_capture_type"),
    }


def _num(val: object) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val), 1)
    except (TypeError, ValueError):
        return None


def _down_from_situation(situation: dict[str, Any]) -> int | None:
    text = str(situation.get("downDistance") or "")
    part = text.split()[0] if text else ""
    try:
        return int(part)
    except (TypeError, ValueError):
        return None


def _distance_from_situation(situation: dict[str, Any]) -> int | None:
    text = str(situation.get("downDistance") or "")
    if "&" not in text:
        return None
    part = text.split("&", 1)[1].strip().split()[0]
    try:
        return int(part)
    except (TypeError, ValueError):
        return None
