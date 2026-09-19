"""BCF Toys FEI ratings — load, lookup, refresh from bcftoys.com."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import DATA_DIR

FEI_CSV = DATA_DIR / "historical" / "cfb_fei_ratings.csv"
FEI_CALIB_PATH = DATA_DIR / "historical" / "cfb_fei_calibration.json"


def _team_keys(name: str) -> list[str]:
    from .team_registry import normalize_team_key, resolve_canonical

    canonical = resolve_canonical(str(name or "").strip()) or str(name or "").strip()
    keys: list[str] = []
    seen: set[str] = set()

    def add(val: str) -> None:
        k = normalize_team_key(val)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    add(canonical)
    if canonical.endswith(" State"):
        add(canonical.replace(" State", " St"))
        add(canonical.replace(" State", " St."))
    return keys


@lru_cache(maxsize=1)
def _load_fei_df() -> pd.DataFrame:
    if not FEI_CSV.exists():
        return pd.DataFrame(columns=["season", "week", "team", "fei", "ofei", "dfei", "sfei", "rk"])
    return pd.read_csv(FEI_CSV)


def lookup_fei_team(
    team_name: str,
    *,
    season: int | None = None,
    week: int | None = None,
) -> dict[str, Any] | None:
    """Best available FEI row for team at or before requested week."""
    from .config import DEFAULT_WEEK, DEFAULT_YEAR
    from .team_registry import resolve_canonical

    yr = int(season if season is not None else DEFAULT_YEAR)
    wk = int(week if week is not None else DEFAULT_WEEK)
    df = _load_fei_df()
    if df.empty:
        return None

    sub = df[(df["season"].astype(int) == yr) & (df["week"].astype(int) <= wk)]
    if sub.empty:
        sub = df[df["season"].astype(int) == yr]
    if sub.empty:
        sub = df
    if sub.empty:
        return None

    latest_week = int(sub["week"].max())
    sub = sub[sub["week"].astype(int) == latest_week]

    canon = resolve_canonical(team_name) or team_name
    for key in _team_keys(canon):
        for _, row in sub.iterrows():
            row_team = str(row.get("team") or "")
            if key in _team_keys(row_team):
                return {
                    "team": resolve_canonical(row_team) or row_team,
                    "fei": float(row["fei"]),
                    "ofei": float(row.get("ofei") or row["fei"]),
                    "dfei": float(row.get("dfei") or -row["fei"]),
                    "sfei": float(row.get("sfei") or 0),
                    "rk": int(row.get("rk") or 0),
                    "season": yr,
                    "week": latest_week,
                }
    return None


def _fei_float(val: str) -> float:
    return float(str(val).replace("−", "-").replace("–", "-").strip() or "0")


def _parse_fei_html(text: str, *, season: int, week: int) -> list[dict[str, Any]]:
    """Parse bcftoys HTML table (current page format)."""
    rows: list[dict[str, Any]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
        parts = [re.sub(r"<[^>]+>", "", c).strip().replace("\xa0", "") for c in cells]
        if len(parts) < 11:
            continue
        try:
            rk = int(parts[0])
        except ValueError:
            continue
        if parts[1] in ("Team", "") or "Opponent" in parts[0]:
            continue
        try:
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "rk": rk,
                    "team": parts[1],
                    "fei": _fei_float(parts[4]),
                    "ofei": _fei_float(parts[6]),
                    "dfei": _fei_float(parts[8]),
                    "sfei": _fei_float(parts[10]),
                }
            )
        except (IndexError, ValueError):
            continue
    return rows


def _parse_fei_markdown(text: str, *, season: int, week: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p != ""]
        if len(parts) < 10:
            continue
        try:
            rk = int(parts[0])
        except ValueError:
            continue
        if parts[1] in ("Team", "---") or "Opponent" in parts[0]:
            continue

        try:
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "rk": rk,
                    "team": parts[1],
                    "fei": _fei_float(parts[4]),
                    "ofei": _fei_float(parts[5]),
                    "dfei": _fei_float(parts[7]),
                    "sfei": _fei_float(parts[9]),
                }
            )
        except (IndexError, ValueError):
            continue
    return rows


def parse_fei_page(text: str, *, season: int, week: int) -> list[dict[str, Any]]:
    """Parse FEI rows from bcftoys markdown or HTML tables."""
    rows = _parse_fei_markdown(text, season=season, week=week)
    if rows:
        return rows
    return _parse_fei_html(text, season=season, week=week)


def fetch_fei_ratings(season: int, week: int | None = None) -> list[dict[str, Any]]:
    """Pull current-season FEI table from bcftoys.com."""
    url = f"https://bcftoys.com/{season}-fei"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "mlb-pbp-model/1.0"})
    resp.raise_for_status()
    wk = int(week or 0)
    m = re.search(r"through Week\s+(\d+)", resp.text, re.I)
    if m and not week:
        wk = int(m.group(1))
    if wk <= 0:
        wk = 1
    return parse_fei_page(resp.text, season=season, week=wk)


def upsert_fei_ratings(rows: list[dict[str, Any]]) -> Path:
    FEI_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)
    if FEI_CSV.exists():
        old = pd.read_csv(FEI_CSV)
        keys = ["season", "week", "team"]
        old = old[~old.set_index(keys).index.isin(new_df.set_index(keys).index)]
        out = pd.concat([old, new_df], ignore_index=True)
    else:
        out = new_df
    out = out.sort_values(["season", "week", "rk"]).drop_duplicates(["season", "week", "team"], keep="last")
    out.to_csv(FEI_CSV, index=False)
    _load_fei_df.cache_clear()
    return FEI_CSV


def ensure_fei_ratings(*, season: int, week: int | None = None) -> None:
    """Refresh FEI CSV when stale or missing for the requested season/week."""
    wk = int(week or 1)
    if FEI_CSV.exists():
        df = pd.read_csv(FEI_CSV)
        hit = df[(df["season"].astype(int) == season) & (df["week"].astype(int) >= wk)]
        if not hit.empty:
            return
    try:
        rows = fetch_fei_ratings(season, week=wk)
        if rows:
            upsert_fei_ratings(rows)
    except (OSError, requests.RequestException, ValueError):
        pass
