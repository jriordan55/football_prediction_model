"""Persist every data pull to timestamped Excel workbooks."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR, audit_export_enabled

EXPORT_DIR = DATA_DIR / "streamlit_exports"
_LOCK = threading.Lock()

# Excel sheet names max 31 chars
_SHEET_ALIASES = {
    "theoddsapi_sport_odds": "odds_api_lines",
    "theoddsapi_events": "odds_api_events",
    "onyx_public_odds": "onyx_quotes",
    "onyx_slate_cache": "onyx_slate",
    "espn_injuries": "espn_injuries",
    "espn_scoreboard": "espn_scoreboard",
    "espn_athlete_gamelog": "espn_gamelog",
    "openmeteo_forecast": "weather_hourly",
    "node_labs_matchup": "node_matchup",
    "node_pinnacle_edges": "node_edges",
    "node_labs_results": "node_results",
    "node_onyx_slate": "node_slate",
    "odds_snapshot": "odds_snapshot",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stamp_str(dt: datetime | None = None) -> str:
    dt = dt or _now_utc()
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _file_stamp(dt: datetime | None = None) -> str:
    dt = dt or _now_utc()
    return dt.strftime("%Y%m%d_%H%M%S")


def _safe_sheet(name: str) -> str:
    base = _SHEET_ALIASES.get(name, name)
    base = re.sub(r"[^\w]", "_", base)[:31]
    return base or "data"


def _to_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        if "rows" in data and isinstance(data["rows"], list):
            return pd.DataFrame(data["rows"])
        if "edges" in data and isinstance(data["edges"], list):
            return pd.DataFrame(data["edges"])
        if "lineResults" in data or "scoreRows" in data:
            parts = []
            for key in ("lineResults", "scoreRows", "propResults"):
                val = data.get(key)
                if isinstance(val, list):
                    for row in val:
                        parts.append({**row, "_section": key})
                elif isinstance(val, dict):
                    parts.append({**val, "_section": key})
            if parts:
                return pd.DataFrame(parts)
        return pd.json_normalize(data, sep="_")
    return pd.DataFrame([{"value": str(data)}])


def export_pull(
    source: str,
    data: Any,
    *,
    tab: str | None = None,
    origin: str = "live",
    extra: dict | None = None,
) -> Path | None:
    """
    Write pull to:
      1) timestamped file  data/streamlit_exports/{source}_{YYYYMMDD_HHMMSS}.xlsx
      2) daily master      data/streamlit_exports/cfb_master_{YYYY-MM-DD}.xlsx (append rows)
    Every row includes pull_timestamp_utc, pull_source, pull_tab, pull_origin.
    """
    if not audit_export_enabled():
        return None

    df = _to_dataframe(data)
    if df.empty and data is not None and not isinstance(data, (list, dict, pd.DataFrame)):
        return None

    ts = _now_utc()
    stamp = _stamp_str(ts)
    meta = {
        "pull_timestamp_utc": stamp,
        "pull_source": source,
        "pull_tab": tab or "",
        "pull_origin": origin,
        "row_count": len(df),
    }
    if extra:
        for k, v in extra.items():
            meta[f"meta_{k}"] = v

    for col in reversed(list(meta.keys())):
        df.insert(0, col, meta[col])

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    sheet = _safe_sheet(source)
    out_file = EXPORT_DIR / f"{source}_{_file_stamp(ts)}.xlsx"
    master_file = EXPORT_DIR / f"cfb_master_{ts.strftime('%Y-%m-%d')}.xlsx"

    with _LOCK:
        try:
            df.to_excel(out_file, sheet_name=sheet[:31], index=False, engine="openpyxl")
        except Exception:
            # fallback csv if openpyxl missing
            csv_path = out_file.with_suffix(".csv")
            df.to_csv(csv_path, index=False)
            out_file = csv_path

        _append_master(master_file, sheet, df, meta)

    return out_file


def _append_master(master: Path, sheet: str, df: pd.DataFrame, log_row: dict) -> None:
    sheets: dict[str, pd.DataFrame] = {}
    if master.exists():
        try:
            book = pd.ExcelFile(master, engine="openpyxl")
            for name in book.sheet_names:
                sheets[name] = pd.read_excel(book, sheet_name=name, engine="openpyxl")
        except Exception:
            sheets = {}

    if sheet in sheets:
        sheets[sheet] = pd.concat([sheets[sheet], df], ignore_index=True)
    else:
        sheets[sheet] = df

    if "pull_log" in sheets:
        sheets["pull_log"] = pd.concat([sheets["pull_log"], pd.DataFrame([log_row])], ignore_index=True)
    else:
        sheets["pull_log"] = pd.DataFrame([log_row])

    with pd.ExcelWriter(master, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)


def export_json_pull(source: str, payload: dict | None, **kwargs) -> Path | None:
    if not payload:
        return None
    return export_pull(source, payload, **kwargs)


def latest_master_path() -> Path | None:
    if not EXPORT_DIR.exists():
        return None
    masters = sorted(EXPORT_DIR.glob("cfb_master_*.xlsx"), reverse=True)
    return masters[0] if masters else None


def export_dir() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR
