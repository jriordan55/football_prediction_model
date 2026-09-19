"""ESPN athlete gamelog — recent game stats for prop detail charts."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

import pandas as pd
import requests

from .config import DATA_DIR
from .excel_audit import export_pull
from .prop_reprice import _load_baselines, _name_team_key, _normalize_name, _team_key

SEARCH = "https://site.web.api.espn.com/apis/common/v3/search"


def _gamelog_url(aid: str) -> str:
    from .sport_context import espn_athlete_gamelog_url

    return espn_athlete_gamelog_url(aid)


def _search_league() -> str:
    from .sport_context import get_sport_config

    return str(get_sport_config()["espn_search_league"])


def _referer() -> str:
    from .sport_context import get_sport_config

    return str(get_sport_config()["espn_referer"])

STAT_FIELDS = {
    "pass_yds": "passingYards",
    "pass_attempts": "passingAttempts",
    "pass_completions": "completions",
    "rush_yds": "rushingYards",
    "rush_attempts": "rushingAttempts",
    "rec_yds": "receivingYards",
    "receptions": "receptions",
    "pass_tds": "passingTouchdowns",
    "tds": "rushingTouchdowns",  # anytime TD proxy from rush TD column when no better field
    "rec_tds": "receivingTouchdowns",
}

Q1_PASS_YDS_SHARE = 0.28


def gamelog_prop_series(df: pd.DataFrame, prop_key: str) -> pd.Series | None:
    """Stat series from ESPN gamelog; Q1 pass yds derived from full-game passing yards."""
    pk = (prop_key or "").lower()
    if pk == "tds":
        rush = df["tds"] if "tds" in df.columns else 0.0
        rec = df["rec_tds"] if "rec_tds" in df.columns else 0.0
        return (rush + rec).clip(lower=0.0)
    if pk in df.columns:
        return df[pk]
    if pk == "pass_yds_q1" and "pass_yds" in df.columns:
        return df["pass_yds"] * Q1_PASS_YDS_SHARE
    return None


def anytime_td_hit_series(df: pd.DataFrame) -> pd.Series | None:
    """Binary 1/0 — player scored at least one TD (rush + rec) in the game."""
    series = gamelog_prop_series(df, "tds")
    if series is None:
        return None
    return (series.fillna(0.0) >= 1.0).astype(float)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.espn.com",
        "Referer": _referer(),
    }
)


def _team_matches_label(team: str, label: str) -> bool:
    if not team or not label:
        return False
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfl_team_registry import resolve_canonical, teams_match

        canon = resolve_canonical(team) or team
        label_team = str(label).strip()
        if "·" in label_team:
            label_team = label_team.split("·")[-1].strip()
        if label_team.lower().startswith("nfl - "):
            label_team = label_team[6:].strip()
        if teams_match(canon, label_team) or teams_match(canon, label):
            return True
        return False

    hay = label.lower()
    parts = [p for p in re.split(r"[\s.&'-]+", team.lower()) if p and p not in {"university", "the", "of", "state"}]
    if team.lower() in hay:
        return True
    for p in parts:
        if len(p) >= 3 and p in hay:
            return True
    return False


@lru_cache(maxsize=512)
def search_espn_athlete_id(name: str, team: str | None = None) -> str | None:
    norm = _normalize_name(name)
    if not norm:
        return None
    try:
        r = requests.get(
            SEARCH,
            params={"query": name, "limit": 12, "type": "player", "sport": "football", "league": _search_league()},
            headers=SESSION.headers,
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
    except (requests.RequestException, ValueError):
        return None

    if team:
        for item in items:
            if _normalize_name(item.get("displayName")) == norm and _team_matches_label(team, item.get("label") or ""):
                return str(item.get("id"))
        return None
    for item in items:
        if _normalize_name(item.get("displayName")) == norm:
            return str(item.get("id"))
    return None


@lru_cache(maxsize=1024)
def espn_team_for_player(name: str, home: str | None, away: str | None) -> str | None:
    """Pick home/away team label from ESPN search."""
    norm = _normalize_name(name)
    if not norm:
        return None
    try:
        r = requests.get(
            SEARCH,
            params={"query": name, "limit": 12, "type": "player", "sport": "football", "league": _search_league()},
            headers=SESSION.headers,
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
    except (requests.RequestException, ValueError):
        items = []

    for item in items:
        if _normalize_name(item.get("displayName")) != norm:
            continue
        label = item.get("label") or ""
        if home and _team_matches_label(home, label):
            return home
        if away and _team_matches_label(away, label):
            return away

    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from .nfl_team_registry import resolve_canonical, teams_match as nfl_teams_match

        for item in items:
            if _normalize_name(item.get("displayName")) != norm:
                continue
            label = str(item.get("label") or "")
            label_team = label.split("·")[-1].strip() if "·" in label else label
            if label_team.lower().startswith("nfl - "):
                label_team = label_team[6:].strip()
            canon = resolve_canonical(label_team) or label_team
            if home and nfl_teams_match(canon, home):
                return home
            if away and nfl_teams_match(canon, away):
                return away

    for tm in (home, away):
        if tm and search_espn_athlete_id(name, tm):
            return tm
    return None


def athlete_id_for_player(name: str, team: str | None = None) -> str | None:
    if team:
        bl = _load_baselines().get(_name_team_key(name, team))
        if bl and bl.get("espn_id"):
            return str(bl["espn_id"])
    aid = search_espn_athlete_id(name, team)
    if aid:
        return aid
    norm = _normalize_name(name)
    for key, row in _load_baselines().items():
        if not key.startswith(f"{norm}|") or not row.get("espn_id"):
            continue
        if team and _team_key(row.get("team") or "") != _team_key(team):
            continue
        return str(row["espn_id"])
    return search_espn_athlete_id(name)


def _gamelog_seasons(season: int) -> tuple[int, ...]:
    """Prefer requested season; fall back to prior year when early-season logs are empty."""
    yr = int(season)
    return (yr, yr - 1) if yr > 2000 else (yr,)


@lru_cache(maxsize=256)
def fetch_gamelog_df(athlete_id: str, season: int = 2025) -> pd.DataFrame:
    if not athlete_id:
        return pd.DataFrame()
    url = _gamelog_url(athlete_id)
    try:
        r = SESSION.get(url, params={"season": season}, timeout=25)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return pd.DataFrame()

    events_meta = data.get("events") or {}
    names: list[str] = data.get("names") or []

    def idx(field: str) -> int | None:
        try:
            return names.index(field)
        except ValueError:
            return None

    idx_map = {k: idx(v) for k, v in STAT_FIELDS.items() if idx(v) is not None}

    rows: list[dict[str, Any]] = []
    for st in data.get("seasonTypes") or []:
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                eid = str(ev.get("eventId") or "")
                meta = events_meta.get(eid) or {}
                opp = (meta.get("opponent") or {}).get("displayName") or meta.get("shortName") or "Opp"
                week = meta.get("week")
                stats = ev.get("stats") or []
                row: dict[str, Any] = {
                    "event_id": eid,
                    "week": week,
                    "opponent": opp,
                    "opponent_logo": (meta.get("opponent") or {}).get("logo") or "",
                    "opponent_abbr": (meta.get("opponent") or {}).get("abbreviation") or "",
                    "game_date": meta.get("gameDate"),
                }
                for prop, i in idx_map.items():
                    try:
                        raw = stats[i] if i is not None and len(stats) > i else None
                        row[prop] = float(raw) if raw not in (None, "", "-") else 0.0
                    except (TypeError, ValueError):
                        row[prop] = 0.0
                rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty and "week" in df.columns:
        df = df.sort_values(["week", "game_date"], ascending=[True, True])
    export_pull("espn_athlete_gamelog", df, origin="live", extra={"athlete_id": athlete_id, "season": season})
    return df


@lru_cache(maxsize=256)
def fetch_gamelog_df_combined(athlete_id: str, season: int = 2025) -> pd.DataFrame:
    """Chronological gamelog spanning prior + current season (for rolling windows)."""
    if not athlete_id:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for yr in _gamelog_seasons(season):
        df = fetch_gamelog_df(athlete_id, yr)
        if df.empty:
            continue
        tagged = df.copy()
        tagged["_season"] = int(yr)
        frames.append(tagged)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    sort_cols = [c for c in ("game_date", "_season", "week") if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=True)
    return out.reset_index(drop=True)


@lru_cache(maxsize=256)
def fetch_gamelog_df_resolved(athlete_id: str, season: int = 2025) -> pd.DataFrame:
    """Rolling gamelog — last season + current season combined."""
    return fetch_gamelog_df_combined(athlete_id, season)


@lru_cache(maxsize=512)
def athlete_headshot(athlete_id: str) -> str | None:
    if not athlete_id:
        return None
    from .sport_context import espn_gamelog_url

    url = espn_gamelog_url(athlete_id)
    try:
        r = SESSION.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        athlete = data.get("athlete") or data
        href = (athlete.get("headshot") or {}).get("href")
        return str(href) if href else None
    except (requests.RequestException, ValueError, TypeError):
        return None


def prop_hit_grade(
    athlete_id: str | None,
    prop_key: str,
    line: float | None,
    side: str,
    *,
    season: int | None = None,
    limit: int = 10,
) -> tuple[int | None, int | None]:
    """Hits vs line in recent games — for GRADE column (e.g. 7/10)."""
    if not athlete_id or line is None:
        return None, None
    from .config import DEFAULT_YEAR

    yr = int(season) if season is not None else DEFAULT_YEAR
    series = recent_stat_series(athlete_id, prop_key, season=yr, limit=limit)
    if not series:
        return None, None
    under = "under" in str(side or "").lower()
    hits = 0
    for g in series:
        val = float(g.get("value") or 0)
        if under:
            if val < line:
                hits += 1
        elif val > line:
            hits += 1
    return hits, len(series)


def recent_stat_series(
    athlete_id: str,
    prop_key: str,
    *,
    season: int = 2025,
    limit: int = 10,
) -> list[dict[str, Any]]:
    df = fetch_gamelog_df_combined(athlete_id, season)
    if df.empty:
        return []
    series = gamelog_prop_series(df, prop_key)
    if series is None:
        return []
    tail = df.tail(limit)
    vals = series.tail(limit)
    out: list[dict[str, Any]] = []
    for (_, r), val in zip(tail.iterrows(), vals, strict=False):
        opp = str(r.get("opponent") or "Opp")
        opp_short = opp.replace(" Mountaineers", "").replace(" Tar Heels", "").replace(" Mustangs", "")
        opp_short = opp_short.replace(" Cyclones", "").replace(" Bearcats", "").split()[-1] if " " in opp_short else opp_short
        if len(opp_short) > 14:
            opp_short = opp.split()[0][:12]
        wk = r.get("week")
        log_season = int(r.get("_season") or season)
        label = f"{opp_short} {str(log_season)[-2:]}w{wk}" if wk is not None else opp_short
        out.append(
            {
                "label": label,
                "value": float(val or 0),
                "week": wk,
                "season": log_season,
                "opponent": opp,
                "opponent_logo": str(r.get("opponent_logo") or ""),
                "opponent_abbr": str(r.get("opponent_abbr") or opp_short[:4].upper()),
            }
        )
    return out
