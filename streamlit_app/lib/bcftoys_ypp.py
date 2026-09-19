"""BCF Toys YPP table — drive-based efficiency metrics for CFB yard modeling."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR, ROOT
from .team_registry import resolve_canonical, team_key, teams_match

CACHE_PATH = DATA_DIR / "bcftoys_ypp_2026.json"
UPLOAD_CANDIDATES = [
    ROOT / "uploads" / "2026-ypp-1.md",
    Path(__file__).resolve().parents[2] / "uploads" / "2026-ypp-1.md",
    Path.home() / ".cursor" / "projects" / "c-Users-student-Documents-football-prediction-model" / "uploads" / "2026-ypp-1.md",
]


def _parse_float(raw: str) -> float | None:
    s = str(raw or "").strip().replace(",", "")
    if not s or s in ("—", "-", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_pct(raw: str) -> float | None:
    v = _parse_float(raw)
    if v is None:
        return None
    if 0 <= v <= 1:
        return v
    if v > 1 and v <= 100:
        return v / 100.0
    return v


def parse_ypp_markdown(text: str) -> list[dict[str, Any]]:
    """Parse BCF Toys YPP markdown table rows."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        if "**Rk**" in line or "---" in line or "Net Yards Per Play" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 18:
            continue
        rank_raw = parts[1]
        if not rank_raw.isdigit():
            continue
        team = parts[2]
        if not team or team.lower() == "team":
            continue
        row = {
            "rank": int(rank_raw),
            "team": team,
            "canonical": resolve_canonical(team) or team,
            "record": parts[3],
            "fbs_record": parts[4],
            "npp": _parse_float(parts[5]),
            "opp": _parse_float(parts[6]),
            "opp_rank": _parse_float(parts[7]),
            "o4_plus": _parse_pct(parts[8]),
            "o7_plus": _parse_pct(parts[10]),
            "o10_plus": _parse_pct(parts[12]),
            "dpp": _parse_float(parts[15]) if len(parts) > 15 else None,
            "d4_plus": _parse_pct(parts[17]) if len(parts) > 17 else None,
            "d7_plus": _parse_pct(parts[19]) if len(parts) > 19 else None,
            "d10_plus": _parse_pct(parts[21]) if len(parts) > 21 else None,
        }
        rows.append(row)
    return rows


def _load_source_text() -> str | None:
    if CACHE_PATH.exists():
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("rows"):
                return None  # already cached structured
        except (json.JSONDecodeError, OSError):
            pass
    for path in UPLOAD_CANDIDATES:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


@lru_cache(maxsize=1)
def load_ypp_index() -> dict[str, dict[str, Any]]:
    """Team-keyed BCF Toys YPP metrics."""
    if CACHE_PATH.exists():
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            rows = payload.get("rows") if isinstance(payload, dict) else payload
            if isinstance(rows, list) and rows:
                return _index_rows(rows)
        except (json.JSONDecodeError, OSError):
            pass

    text = _load_source_text()
    rows: list[dict[str, Any]] = []
    if text:
        rows = parse_ypp_markdown(text)
    if rows:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(
                json.dumps({"source": "bcftoys.com/2026-ypp", "rows": rows}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
    return _index_rows(rows)


def _index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        canon = resolve_canonical(row.get("team") or "") or row.get("team")
        keys = {team_key(canon), team_key(row.get("team") or "")}
        for k in keys:
            if k:
                out[k] = {**row, "canonical": canon}
    return out


def lookup_ypp(team: str) -> dict[str, Any] | None:
    idx = load_ypp_index()
    canon = resolve_canonical(team) or team
    hit = idx.get(team_key(canon))
    if hit:
        return hit
    for k, row in idx.items():
        if teams_match(row.get("team") or "", team):
            return row
    return None


def matchup_ypp_edge(offense_team: str, defense_team: str) -> dict[str, Any]:
    """Offense YPP vs opponent DPP — positive favors more yards."""
    off = lookup_ypp(offense_team) or {}
    deff = lookup_ypp(defense_team) or {}
    opp = off.get("opp")
    dpp = deff.get("dpp")
    league_opp = 5.8
    league_dpp = 5.8
    off_adj = (float(opp) / league_opp) if opp else 1.0
    def_adj = (float(dpp) / league_dpp) if dpp else 1.0
    combined = off_adj / max(0.65, def_adj)
    return {
        "off_opp": opp,
        "def_dpp": dpp,
        "off_o7_plus": off.get("o7_plus"),
        "def_d7_plus": deff.get("d7_plus"),
        "npp": off.get("npp"),
        "edge_factor": round(max(0.82, min(1.22, combined)), 3),
    }
