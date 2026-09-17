"""Optional bridge to Node Football Labs API (localhost:3000)."""
from __future__ import annotations

from typing import Any

import requests

from .config import NODE_API, audit_export_enabled
from .excel_audit import export_json_pull


def node_get(path: str, params: dict | None = None, timeout: int = 120, *, source: str | None = None, tab: str | None = None) -> dict | None:
    url = f"{NODE_API.rstrip('/')}{path}"
    try:
        r = requests.get(url, params=params or {}, timeout=timeout)
        if r.ok:
            payload = r.json()
            if source and audit_export_enabled():
                export_json_pull(source, payload, tab=tab, origin="node_api", extra={"path": path})
            return payload
    except requests.RequestException:
        return None
    return None


def fetch_labs_matchup(home: str, away: str, year: int = 2026, week: int = 1, source: str = "onyx", tab: str | None = None) -> dict | None:
    return node_get(
        "/api/labs/matchup",
        {"home": home, "away": away, "year": year, "week": week, "source": source, "sims": 500},
        timeout=90,
        source="node_labs_matchup",
        tab=tab,
    )


def fetch_pinnacle_edges(refresh: bool = False, min_edge: float = 2, tab: str | None = None) -> dict | None:
    return node_get(
        "/api/labs/pinnacle-edges",
        {"refresh": "1" if refresh else "0", "minEdge": min_edge},
        source="node_pinnacle_edges",
        tab=tab,
    )


def fetch_labs_results(year: int = 2026, week: int = 1, source: str = "onyx", tab: str | None = None) -> dict | None:
    return node_get(
        "/api/labs/results",
        {"year": year, "week": week, "source": source},
        source="node_labs_results",
        tab=tab,
    )


def fetch_onyx_slate(year: int = 2026, week: int = 1, tab: str | None = None) -> dict | None:
    return node_get(
        "/api/onyx/slate",
        {"year": year, "week": week, "sims": 500, "props": "1"},
        source="node_onyx_slate",
        tab=tab,
    )


def node_available() -> bool:
    url = f"{NODE_API.rstrip('/')}/api/health"
    try:
        r = requests.get(url, timeout=1)
        return r.ok
    except requests.RequestException:
        return False
