"""Historical weather → total adjustment from past outdoor CFB games."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import pandas as pd
import requests

from lib.cfbd_games import load_cfbd_games
from lib.config import DATA_DIR

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
COEF_CACHE = DATA_DIR / "weather_coefficients.json"

# Literature-style priors for CFB totals (pts on O/U). Continuous — no cliff at 10 mph wind.
DEFAULT_COEF: dict[str, float] = {
    "wind_per_mph": -0.11,       # each mph above calm baseline
    "wind_baseline_mph": 4.0,
    "temp_cold_per_deg": -0.045,  # each °F below comfort floor
    "temp_cold_floor_f": 62.0,
    "temp_hot_per_deg": -0.028,   # each °F above comfort ceiling (slows pace)
    "temp_hot_ceiling_f": 78.0,
    "precip_per_pct": -0.042,     # each precip-prob point above drizzle floor
    "precip_floor_pct": 8.0,
    "base_total": 54.0,
}


def summarize_kickoff_weather(slots: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate kickoff → +3h slots for projection (worst-case wind/rain, coldest temp)."""
    if not slots:
        return {"wind_mph": 0.0, "temp_f": 70.0, "precip_pct": 0.0}

    winds: list[float] = []
    temps: list[float] = []
    precips: list[float] = []
    for s in slots:
        try:
            if s.get("wind_mph") is not None:
                winds.append(float(s["wind_mph"]))
        except (TypeError, ValueError):
            pass
        try:
            if s.get("temp_f") is not None:
                temps.append(float(s["temp_f"]))
        except (TypeError, ValueError):
            pass
        try:
            if s.get("precip_pct") is not None:
                precips.append(float(s["precip_pct"]))
        except (TypeError, ValueError):
            pass

    return {
        "wind_mph": max(winds) if winds else 0.0,
        "temp_f": min(temps) if temps else 70.0,
        "precip_pct": max(precips) if precips else 0.0,
    }


def _load_coef_cache() -> dict[str, float] | None:
    if not COEF_CACHE.exists():
        return None
    try:
        raw = json.loads(COEF_CACHE.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "wind_per_mph" in raw:
            return {**DEFAULT_COEF, **{k: float(v) for k, v in raw.items() if k in DEFAULT_COEF}}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return None


def _save_coef_cache(coef: dict[str, float]) -> None:
    try:
        COEF_CACHE.parent.mkdir(parents=True, exist_ok=True)
        COEF_CACHE.write_text(json.dumps(coef, indent=2), encoding="utf-8")
    except OSError:
        pass


@lru_cache(maxsize=1)
def _outdoor_games_with_totals(year: int) -> pd.DataFrame:
    games = load_cfbd_games(year)
    rows: list[dict[str, Any]] = []
    for g in games:
        if g.get("seasonType") != "regular":
            continue
        hp, ap = g.get("homePoints"), g.get("awayPoints")
        if hp is None or ap is None:
            continue
        if g.get("homeClassification") != "fbs" or g.get("awayClassification") != "fbs":
            continue
        lat = g.get("venueLat") or g.get("latitude")
        lon = g.get("venueLon") or g.get("longitude")
        if lat is None or lon is None:
            continue
        rows.append(
            {
                "home": g.get("homeTeam"),
                "away": g.get("awayTeam"),
                "startDate": g.get("startDate"),
                "total_points": int(hp) + int(ap),
                "lat": float(lat),
                "lon": float(lon),
            }
        )
    return pd.DataFrame(rows)


def _fetch_archive_hour(lat: float, lon: float, day: str) -> dict[str, float | None]:
    try:
        r = requests.get(
            ARCHIVE,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": day[:10],
                "end_date": day[:10],
                "hourly": "temperature_2m,wind_speed_10m,precipitation",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "UTC",
            },
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        hourly = data.get("hourly") or {}
        if not hourly.get("time"):
            return {}
        idx = min(len(hourly["time"]) - 1, max(0, len(hourly["time"]) // 2))
        return {
            "temp_f": float(hourly["temperature_2m"][idx]) if hourly.get("temperature_2m") else None,
            "wind_mph": float(hourly["wind_speed_10m"][idx]) if hourly.get("wind_speed_10m") else None,
            "precip_mm": float(hourly["precipitation"][idx]) if hourly.get("precipitation") else None,
        }
    except Exception:
        return {}


def _fit_coefficients(samples: pd.DataFrame) -> dict[str, float]:
    """Simple OLS-style slopes vs league mean total, blended with priors."""
    base = float(samples["total"].mean())
    prior = DEFAULT_COEF.copy()
    prior["base_total"] = round(base, 1)
    n = len(samples)
    if n < 15:
        return prior

    def _slope(col: str, ref: float, *, per_unit: float, key: str, default: float) -> float:
        above = samples[samples[col] > ref]
        if len(above) < 5:
            return default
        raw = float(above["total"].mean() - base) / max(1.0, float(above[col].mean() - ref))
        # shrink toward prior when sample is thin
        w = min(1.0, len(above) / 40.0)
        return round((1 - w) * default + w * raw, 4)

    wind = _slope("wind", prior["wind_baseline_mph"], per_unit=1.0, key="wind_per_mph", default=prior["wind_per_mph"])
    cold = _slope("temp", prior["temp_cold_floor_f"], per_unit=1.0, key="temp_cold_per_deg", default=prior["temp_cold_per_deg"])
    # invert for cold: lower temp → lower total
    cold_rows = samples[samples["temp"] < prior["temp_cold_floor_f"]]
    if len(cold_rows) >= 5:
        cold = (float(cold_rows["total"].mean() - base) / max(1.0, prior["temp_cold_floor_f"] - float(cold_rows["temp"].mean())))
        cold = round(max(-0.12, min(-0.01, cold)), 4)

    hot_rows = samples[samples["temp"] > prior["temp_hot_ceiling_f"]]
    hot = prior["temp_hot_per_deg"]
    if len(hot_rows) >= 5:
        hot = (float(hot_rows["total"].mean() - base) / max(1.0, float(hot_rows["temp"].mean()) - prior["temp_hot_ceiling_f"]))
        hot = round(max(-0.08, min(0.02, hot)), 4)

    rain_rows = samples[samples["precip_pct"] > prior["precip_floor_pct"]]
    precip = prior["precip_per_pct"]
    if len(rain_rows) >= 5:
        precip = (float(rain_rows["total"].mean() - base) / max(1.0, float(rain_rows["precip_pct"].mean()) - prior["precip_floor_pct"]))
        precip = round(max(-0.10, min(-0.01, precip)), 4)

    return {
        **prior,
        "wind_per_mph": wind,
        "temp_cold_per_deg": cold,
        "temp_hot_per_deg": hot,
        "precip_per_pct": precip,
        "base_total": round(base, 1),
        "sample_games": n,
    }


@lru_cache(maxsize=4)
def historical_weather_coefficients(year: int = 2025) -> dict[str, float]:
    """
    Empirical coefficients: pts change in actual total per unit weather.
    Cached to disk; falls back to calibrated priors when CFBD archive is unavailable.
    """
    cached = _load_coef_cache()
    if cached:
        return cached

    df = _outdoor_games_with_totals(year)
    if df.empty or len(df) < 20:
        return DEFAULT_COEF.copy()

    samples: list[dict[str, float]] = []
    for _, g in df.head(120).iterrows():
        wx = _fetch_archive_hour(g["lat"], g["lon"], str(g["startDate"]))
        if not wx:
            continue
        precip_mm = float(wx.get("precip_mm") or 0)
        samples.append(
            {
                "total": float(g["total_points"]),
                "wind": float(wx.get("wind_mph") or 0),
                "temp": float(wx.get("temp_f") or 70),
                "precip_pct": min(100.0, precip_mm * 40.0),  # rough mm → prob scale for archive
            }
        )

    if len(samples) < 15:
        return DEFAULT_COEF.copy()

    coef = _fit_coefficients(pd.DataFrame(samples))
    _save_coef_cache(coef)
    return coef


def project_total_adjustment(
    wind_mph: float,
    temp_f: float,
    precip_pct: float = 0.0,
    *,
    year: int = 2025,
) -> float:
    """
    Project O/U total adjustment in points from kickoff-window weather.
    Continuous — almost every outdoor game gets a non-zero move.
    """
    coef = historical_weather_coefficients(year)
    wind = max(0.0, float(wind_mph))
    temp = float(temp_f)
    precip = max(0.0, min(100.0, float(precip_pct)))

    wind_adj = coef["wind_per_mph"] * max(0.0, wind - coef["wind_baseline_mph"])
    cold_adj = coef["temp_cold_per_deg"] * max(0.0, coef["temp_cold_floor_f"] - temp)
    hot_adj = coef["temp_hot_per_deg"] * max(0.0, temp - coef["temp_hot_ceiling_f"])
    precip_adj = coef["precip_per_pct"] * max(0.0, precip - coef["precip_floor_pct"])

    adj = wind_adj + cold_adj + hot_adj + precip_adj
    return round(max(-5.0, min(2.5, adj)), 1)
