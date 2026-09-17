"""Aggregate CFB injury feeds — Covers primary, OpticOdds optional, ESPN fallback."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import DATA_DIR, DEFAULT_YEAR, load_env
from .covers_injuries import fetch_covers_injuries
from .excel_audit import export_pull
from .espn_client import NON_INJURY, SESSION
from .sport_context import espn_site_url, get_sport_config

OPTIC_BASE = "https://api.opticodds.com/api/v3"
STALE_BEFORE = f"{DEFAULT_YEAR - 1}-07-01"


def _parse_iso(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_stale(ts: Any) -> bool:
    dt = _parse_iso(ts)
    if not dt:
        return False
    try:
        cutoff = datetime.fromisoformat(STALE_BEFORE).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < cutoff


def _fetch_espn_injuries() -> pd.DataFrame:
    from .sport_context import SPORT_NFL, get_sport
    from .nfl_team_registry import resolve_canonical as resolve_nfl
    from .team_registry import resolve_canonical as resolve_cfb

    data = SESSION.get(f"{espn_site_url()}/injuries", timeout=30).json()
    rows: list[dict[str, Any]] = []
    resolve = resolve_nfl if get_sport() == SPORT_NFL else resolve_cfb

    for bucket in data.get("injuries") or []:
        team_raw = bucket.get("displayName") or bucket.get("team", {}).get("displayName") or ""
        team = resolve(team_raw) or team_raw
        abbr = bucket.get("team", {}).get("abbreviation") or ""
        for inj in bucket.get("injuries") or []:
            athlete = inj.get("athlete") or {}
            status = (
                (inj.get("status") or inj.get("type") or {}).get("description")
                if isinstance(inj.get("status"), dict)
                else inj.get("status")
            )
            status = str(status or "").strip()
            if status.lower() in NON_INJURY:
                continue
            updated = inj.get("date") or data.get("timestamp")
            if _is_stale(updated):
                continue
            rows.append(
                {
                    "player": athlete.get("displayName") or "",
                    "position": (athlete.get("position") or {}).get("abbreviation") or inj.get("position") or "",
                    "team": team,
                    "team_abbr": abbr,
                    "headshot": (athlete.get("headshot") or {}).get("href") or "",
                    "status": status.upper() if status else "LISTED",
                    "injury": inj.get("details", {}).get("type") if isinstance(inj.get("details"), dict) else inj.get("type") or "",
                    "note": inj.get("longComment") or inj.get("shortComment") or "",
                    "updated": updated or datetime.now(timezone.utc).isoformat(),
                    "source": "espn",
                }
            )
    return pd.DataFrame(rows)


def _fetch_optic_injuries() -> pd.DataFrame:
    load_env()
    key = os.getenv("OPTICODDS_API_KEY") or os.getenv("ODDSJAM_API_KEY")
    if not key:
        return pd.DataFrame()
    try:
        r = requests.get(
            f"{OPTIC_BASE}/injuries",
            params={"league": get_sport_config()["optic_league"], "key": key},
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return pd.DataFrame()

    items = payload.get("data") or payload.get("injuries") or []
    rows: list[dict[str, Any]] = []
    for inj in items:
        player = inj.get("player") or {}
        team = inj.get("team") or {}
        status = str(inj.get("status") or inj.get("designation") or "").strip()
        if status.lower() in NON_INJURY:
            continue
        rows.append(
            {
                "player": player.get("name") or "",
                "position": player.get("position") or "",
                "team": team.get("name") or team.get("abbreviation") or "",
                "team_abbr": team.get("abbreviation") or "",
                "headshot": player.get("logo") or "",
                "status": status.upper() if status else "LISTED",
                "injury": inj.get("injury") or inj.get("body_part") or "",
                "note": inj.get("note") or inj.get("description") or "",
                "updated": inj.get("updated_at") or inj.get("date") or datetime.now(timezone.utc).isoformat(),
                "source": "opticodds",
            }
        )
    return pd.DataFrame(rows)


def _load_scraper_cache() -> pd.DataFrame:
    path = Path(DATA_DIR) / "cfb_injuries_history.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except (OSError, ValueError):
        return pd.DataFrame()
    if df.empty:
        return df
    latest = df.groupby(["team", "player"], as_index=False).last()
    rows = []
    for _, row in latest.iterrows():
        status = str(row.get("status") or "").strip()
        if status.lower() in NON_INJURY:
            continue
        rows.append(
            {
                "player": row.get("player") or "",
                "position": row.get("position") or "",
                "team": row.get("team") or "",
                "team_abbr": row.get("team") or "",
                "headshot": "",
                "status": status.upper() if status else "LISTED",
                "injury": row.get("injury") or "",
                "note": row.get("note") or "",
                "updated": row.get("captured_at") or datetime.now(timezone.utc).isoformat(),
                "source": "scraper_cache",
            }
        )
    return pd.DataFrame(rows)


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    show = df.copy()
    show["player_key"] = show["player"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True)
    show["team_key"] = show["team_abbr"].fillna(show["team"]).astype(str).str.upper()
    show = show.sort_values(["player_key", "team_key", "updated"], ascending=[True, True, False])
    show = show.drop_duplicates(subset=["player_key", "team_key"], keep="first")
    return show.drop(columns=["player_key", "team_key"], errors="ignore")


def fetch_league_injuries(tab: str | None = None) -> pd.DataFrame:
    """Best available league-wide injury table for the active sport."""
    from .sport_context import SPORT_NFL, get_sport

    sport = get_sport()
    frames: list[pd.DataFrame] = []

    if sport == SPORT_NFL:
        source_fns = (
            (_fetch_optic_injuries, "opticodds"),
            (_fetch_espn_injuries, "espn"),
        )
        source_order = ("opticodds", "espn")
    else:
        source_fns = (
            (_fetch_optic_injuries, "opticodds"),
            (fetch_covers_injuries, "covers"),
            (_fetch_espn_injuries, "espn"),
            (_load_scraper_cache, "scraper_cache"),
        )
        source_order = ("opticodds", "covers", "espn", "scraper_cache")

    for fn, _src in source_fns:
        try:
            part = fn()
            if not part.empty:
                frames.append(part)
        except Exception:
            continue

    if not frames:
        df = pd.DataFrame()
    else:
        merged = pd.concat(frames, ignore_index=True)
        rank = {s: i for i, s in enumerate(source_order)}
        merged["_rank"] = merged.get("source", "covers").map(lambda s: rank.get(s, 99))
        merged = merged.sort_values(["player", "team_abbr", "_rank"]).drop(columns=["_rank"], errors="ignore")
        df = _dedupe(merged)

    export_name = "nfl_injuries" if sport == SPORT_NFL else "cfb_injuries"
    export_pull(export_name, df, tab=tab, origin="live")
    return df
