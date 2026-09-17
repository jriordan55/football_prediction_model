"""Weather forecasts via Open-Meteo (free, no API key)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import DATA_DIR
from .excel_audit import export_pull

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FORECAST_CACHE_DIR = DATA_DIR / "weather_forecast_cache"
FORECAST_CACHE_TTL_SEC = 7200  # 2h — forecasts rarely need sub-hour refresh


def _forecast_cache_path(lat: float, lon: float, kickoff_iso: str) -> Path:
    kick_day = str(kickoff_iso or "")[:10] or "unknown"
    key = f"{lat:.4f}|{lon:.4f}|{kick_day}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return FORECAST_CACHE_DIR / f"{digest}.json"


def _read_forecast_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(str(payload.get("saved_at")).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - saved_at).total_seconds()
        if age > FORECAST_CACHE_TTL_SEC:
            return None
        rows = payload.get("rows") or []
        if not rows:
            return None
        return pd.DataFrame(rows)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _write_forecast_cache(path: Path, df: pd.DataFrame) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "rows": df.to_dict(orient="records"),
        }
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except OSError:
        pass


def _fetch_hourly_forecast_live(
    lat: float,
    lon: float,
    kickoff_iso: str,
    *,
    matchup: str | None = None,
    tab: str | None = None,
) -> pd.DataFrame:
    """Hourly weather from kickoff for ~4 hours (network)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "America/New_York",
        "forecast_days": 3,
    }
    r = requests.get(OPEN_METEO, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    hourly = data.get("hourly") or {}
    df = pd.DataFrame(hourly)
    if df.empty or "time" not in df.columns:
        return df
    df["time"] = pd.to_datetime(df["time"]).astype(str)
    try:
        kick = pd.to_datetime(kickoff_iso)
        if kick.tzinfo is None:
            kick = kick.tz_localize("UTC").tz_convert("America/New_York")
        else:
            kick = kick.tz_convert("America/New_York")
        kick_times = pd.to_datetime(df["time"])
        window = df[
            (kick_times >= kick - pd.Timedelta(minutes=30))
            & (kick_times <= kick + pd.Timedelta(hours=3))
        ]
        out = window.reset_index(drop=True)
    except Exception:
        out = df.head(4).reset_index(drop=True)
    if matchup:
        out = out.copy()
        out["matchup"] = matchup
        out["kickoff"] = kickoff_iso
        out["lat"] = lat
        out["lon"] = lon
    export_pull("openmeteo_forecast", out, tab=tab, origin="live", extra={"matchup": matchup or ""})
    return out


def fetch_hourly_forecast(
    lat: float,
    lon: float,
    kickoff_iso: str,
    *,
    matchup: str | None = None,
    tab: str | None = None,
) -> pd.DataFrame:
    """Disk-cached hourly forecast — avoids Open-Meteo on every tab visit."""
    cache_path = _forecast_cache_path(lat, lon, kickoff_iso)
    cached = _read_forecast_cache(cache_path)
    if cached is not None and not cached.empty:
        return cached
    out = _fetch_hourly_forecast_live(lat, lon, kickoff_iso, matchup=matchup, tab=tab)
    if not out.empty:
        _write_forecast_cache(cache_path, out)
    return out


try:
    import streamlit as st

    @st.cache_data(ttl=FORECAST_CACHE_TTL_SEC, show_spinner=False)
    def fetch_hourly_forecast_cached(
        lat: float,
        lon: float,
        kickoff_iso: str,
        matchup: str,
        tab: str | None,
    ) -> pd.DataFrame:
        return fetch_hourly_forecast(lat, lon, kickoff_iso, matchup=matchup or None, tab=tab)

except Exception:

    def fetch_hourly_forecast_cached(
        lat: float,
        lon: float,
        kickoff_iso: str,
        matchup: str,
        tab: str | None,
    ) -> pd.DataFrame:
        return fetch_hourly_forecast(lat, lon, kickoff_iso, matchup=matchup or None, tab=tab)


def weather_total_adjustment(wind_mph: float, temp_f: float, precip_pct: float) -> float:
    """Heuristic total adjustment in points (model-style, not meteorological)."""
    adj = 0.0
    if wind_mph >= 15:
        adj -= min(2.5, (wind_mph - 10) * 0.15)
    if temp_f >= 95:
        adj += min(1.5, (temp_f - 90) * 0.08)
    if temp_f <= 32:
        adj -= min(1.0, (35 - temp_f) * 0.05)
    if precip_pct >= 50:
        adj -= min(2.0, precip_pct * 0.025)
    return round(adj, 1)


def weather_icon(code: int | float | None) -> str:
    """Simple WMO weather-code → display icon."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "☁"
    if c == 0:
        return "☀"
    if c in (1, 2):
        return "⛅"
    if c == 3:
        return "☁"
    if c in (45, 48):
        return "🌫"
    if c in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "🌧"
    if c in (71, 73, 75, 77, 85, 86):
        return "🌨"
    if c in (95, 96, 99):
        return "⛈"
    return "☁"


def kickoff_hourly_slots(hourly: pd.DataFrame, *, slots: int = 4) -> list[dict[str, Any]]:
    """Return KICK, +1H, +2H, +3H forecast slices."""
    labels = ["KICK", "+1H", "+2H", "+3H"][:slots]
    out: list[dict[str, Any]] = []
    if hourly.empty:
        return [{"label": lb, "temp_f": None, "precip_pct": None, "icon": "☁"} for lb in labels]
    for i, lb in enumerate(labels):
        idx = min(i, len(hourly) - 1)
        row = hourly.iloc[idx]
        out.append(
            {
                "label": lb,
                "temp_f": row.get("temperature_2m"),
                "precip_pct": row.get("precipitation_probability"),
                "wind_mph": row.get("wind_speed_10m"),
                "icon": weather_icon(row.get("weather_code")),
            }
        )
    return out


def forecast_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    return {
        "wind_mph": float(df["wind_speed_10m"].mean()) if "wind_speed_10m" in df else None,
        "temp_f": float(df["temperature_2m"].mean()) if "temperature_2m" in df else None,
        "precip_pct": float(df["precipitation_probability"].max()) if "precipitation_probability" in df else None,
    }
