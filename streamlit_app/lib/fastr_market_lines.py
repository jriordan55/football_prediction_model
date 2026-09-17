"""Opening/closing spread & total from cfbfastR and nflfastR public releases."""
from __future__ import annotations

import gzip
import io
import json
import os
import time
from functools import lru_cache
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import pandas as pd

from .cfbd_games import cfbd_week_for_display
from .config import DATA_DIR, load_env
from .sport_context import SPORT_CFB, SPORT_NFL, cache_sport
from .team_registry import resolve_canonical, team_key, teams_match

CACHE_DIR = DATA_DIR / "fastr_lines_cache"
CFB_ODDS_URL = (
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/betting/csv/cfb_line_odds.csv.gz"
)
NFL_GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NFL_INITIAL_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/initial_lines.csv"
CFBD_API = "https://api.collegefootballdata.com"

_CFB_PROVIDERS = ("consensus", "DraftKings", "draftkings", "ESPN Bet", "Caesars", "numberfire")
_NFL_BOOKS = ("WSGT", "DraftKings", "DK", "consensus")


def _match_key(home: str, away: str, sport: str) -> tuple[str, str]:
    if sport == SPORT_NFL:
        from . import nfl_team_registry as nfl

        return nfl.team_key(away), nfl.team_key(home)
    return team_key(away), team_key(home)


def _round_line(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return round(f * 2) / 2


def _round_ml(val: Any) -> int | None:
    if val is None:
        return None
    try:
        n = int(round(float(val)))
    except (TypeError, ValueError):
        return None
    return n if n != 0 else None


def _http_get(url: str, *, timeout: int = 120) -> bytes:
    req = Request(url, headers={"User-Agent": "mlb-pbp-model/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _download(path: Any, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _http_get(url)
    path.write_bytes(data)


def _cfbd_key() -> str:
    load_env()
    return (os.getenv("CFBD_API_KEY") or "").strip()


def _pick_provider(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    for pref in _CFB_PROVIDERS:
        for row in rows:
            prov = str(row.get("provider") or row.get("line_provider") or "").strip()
            if prov.lower() == pref.lower():
                return row
    return rows[0]


def _ml_fields(row: dict[str, Any]) -> dict[str, int | None]:
    return {
        "openHomeMoneyline": _round_ml(
            row.get("home_moneyline_open")
            or row.get("homeMoneylineOpen")
            or row.get("home_ml_open")
        ),
        "closeHomeMoneyline": _round_ml(
            row.get("home_moneyline")
            or row.get("homeMoneyline")
            or row.get("home_ml")
        ),
        "openAwayMoneyline": _round_ml(
            row.get("away_moneyline_open")
            or row.get("awayMoneylineOpen")
            or row.get("away_ml_open")
        ),
        "closeAwayMoneyline": _round_ml(
            row.get("away_moneyline")
            or row.get("awayMoneyline")
            or row.get("away_ml")
        ),
    }


def _merge_line_fields(primary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    """Fill missing spread/total/ML from alternate providers in the same game group."""
    merged = dict(primary)
    for row in rows:
        extra = _line_fields(row)
        for key, val in extra.items():
            if merged.get(key) is None and val is not None:
                merged[key] = val
    return merged


def _line_fields(row: dict[str, Any]) -> dict[str, float | int | None]:
    spread_close = row.get("spread") or row.get("spread_close")
    spread_open = row.get("spread_open") or row.get("spreadOpen")
    total_close = row.get("over_under") or row.get("overUnder") or row.get("total")
    total_open = row.get("over_under_open") or row.get("overUnderOpen") or row.get("total_open")
    return {
        "openSpread": _round_line(spread_open),
        "closeSpread": _round_line(spread_close),
        "openTotal": _round_line(total_open),
        "closeTotal": _round_line(total_close),
        **_ml_fields(row),
    }


def _fetch_cfbd_year(year: int) -> list[dict[str, Any]]:
    key = _cfbd_key()
    if not key:
        return []

    out: list[dict[str, Any]] = []
    headers = {"Accept": "application/json", "Authorization": f"Bearer {key}"}

    def _pull(week: int, season_type: str) -> None:
        url = f"{CFBD_API}/lines?year={year}&week={week}&seasonType={season_type}"
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=60) as resp:
                chunk = json.loads(resp.read().decode("utf-8"))
        except (URLError, json.JSONDecodeError, OSError, TimeoutError):
            return
        if isinstance(chunk, list):
            out.extend(chunk)
        time.sleep(0.25)

    for week in range(1, 17):
        _pull(week, "regular")
    for week in range(1, 6):
        _pull(week, "postseason")
    return out


def _flatten_cfbd_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in games:
        home = resolve_canonical(game.get("homeTeam") or game.get("home_team")) or game.get("homeTeam")
        away = resolve_canonical(game.get("awayTeam") or game.get("away_team")) or game.get("awayTeam")
        if not home or not away:
            continue
        base = {
            "season": game.get("season") or game.get("year"),
            "week": game.get("week"),
            "home_team": home,
            "away_team": away,
        }
        lines = game.get("lines") or []
        if lines:
            picked = _pick_provider([dict(ln) for ln in lines if isinstance(ln, dict)])
            if picked:
                rows.append({**base, **picked})
                continue
        top = _line_fields(game)
        if any(top.values()):
            rows.append({**base, **{k: v for k, v in top.items() if v is not None}})
    return rows


def _load_cfb_csv_rows() -> pd.DataFrame:
    cache = CACHE_DIR / "cfb_line_odds.csv.gz"
    if not cache.exists():
        try:
            _download(cache, CFB_ODDS_URL)
        except (URLError, OSError, TimeoutError):
            return pd.DataFrame()
    try:
        with gzip.open(cache, "rt", encoding="utf-8", errors="replace") as fh:
            return pd.read_csv(fh, low_memory=False)
    except (OSError, pd.errors.ParserError, gzip.BadGzipFile):
        return pd.DataFrame()


def _cfb_year_rows(year: int) -> list[dict[str, Any]]:
    cache_path = CACHE_DIR / f"cfb_lines_v2_{year}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (json.JSONDecodeError, OSError):
            pass

    rows: list[dict[str, Any]] = []
    api_games = _fetch_cfbd_year(year)
    if api_games:
        rows = _flatten_cfbd_games(api_games)

    if not rows:
        df = _load_cfb_csv_rows()
        if not df.empty:
            season_col = "season" if "season" in df.columns else "year" if "year" in df.columns else None
            if season_col:
                df = df[pd.to_numeric(df[season_col], errors="coerce") == int(year)]
            rows = df.to_dict(orient="records")

    if rows:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(rows, default=str), encoding="utf-8")
        except OSError:
            pass
    return rows


def _cfb_index(year: int, week: int | None) -> dict[tuple[str, str], dict[str, float | None]]:
    rows = _cfb_year_rows(year)
    if not rows:
        return {}

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        home = resolve_canonical(row.get("home_team") or row.get("homeTeam")) or row.get("home_team")
        away = resolve_canonical(row.get("away_team") or row.get("awayTeam")) or row.get("away_team")
        if not home or not away:
            continue
        try:
            row_week = int(row.get("week"))
        except (TypeError, ValueError):
            row_week = None
        if week is not None:
            cfbd_wk = cfbd_week_for_display(int(week))
            if cfbd_wk is not None and row_week is not None and row_week != cfbd_wk:
                continue
        key = _match_key(str(home), str(away), SPORT_CFB)
        grouped.setdefault(key, []).append(row)

    out: dict[tuple[str, str], dict[str, float | None]] = {}
    for key, grp in grouped.items():
        picked = _pick_provider(grp)
        if not picked:
            continue
        lines = _merge_line_fields(_line_fields(picked), grp)
        if any(v is not None for v in lines.values()):
            out[key] = lines
    return out


def _nfl_abbr(team: str) -> str:
    from . import nfl_team_registry as nfl

    canon = resolve_canonical(team) or team
    for full, abbr, _aliases in nfl.NFL_TEAMS:
        if teams_match(canon, full) or teams_match(canon, abbr):
            return abbr
    return str(canon or "").upper()[:3]


def _load_nfl_games() -> pd.DataFrame:
    cache = CACHE_DIR / "nfl_games.csv"
    if not cache.exists():
        try:
            _download(cache, NFL_GAMES_URL)
        except (URLError, OSError, TimeoutError):
            return pd.DataFrame()
    try:
        return pd.read_csv(cache, low_memory=False)
    except (OSError, pd.errors.ParserError):
        return pd.DataFrame()


def _load_nfl_initial() -> pd.DataFrame:
    cache = CACHE_DIR / "nfl_initial_lines.csv"
    if not cache.exists():
        try:
            _download(cache, NFL_INITIAL_URL)
        except (URLError, OSError, TimeoutError):
            return pd.DataFrame()
    try:
        return pd.read_csv(cache, low_memory=False)
    except (OSError, pd.errors.ParserError):
        return pd.DataFrame()


def _parse_nfl_about(about: str) -> tuple[int | None, int | None, str | None, str | None]:
    parts = str(about or "").split("_")
    if len(parts) < 4:
        return None, None, None, None
    try:
        season = int(parts[0])
        week = int(parts[1])
    except (TypeError, ValueError):
        return None, None, None, None
    away_abbr = parts[2].upper()
    home_abbr = parts[3].upper()
    return season, week, away_abbr, home_abbr


def _nfl_initial_index(initial: pd.DataFrame) -> dict[tuple[int, int, str, str], dict[str, float | None]]:
    """Keyed by (season, week, away_abbr, home_abbr)."""
    if initial.empty:
        return {}
    work = initial.copy()
    parsed = work["about"].map(_parse_nfl_about)
    work["season_p"] = parsed.map(lambda x: x[0])
    work["week_p"] = parsed.map(lambda x: x[1])
    work["away_p"] = parsed.map(lambda x: x[2])
    work["home_p"] = parsed.map(lambda x: x[3])
    work = work.dropna(subset=["season_p", "week_p", "away_p", "home_p"])
    out: dict[tuple[int, int, str, str], dict[str, float | None]] = {}
    for (season, wk, away_abbr, home_abbr), grp in work.groupby(
        ["season_p", "week_p", "away_p", "home_p"], dropna=False
    ):
        entry: dict[str, float | int | None] = {
            "openSpread": None,
            "openTotal": None,
            "openHomeMoneyline": None,
            "openAwayMoneyline": None,
        }
        spread_grp = grp[grp["type"].astype(str).str.upper() == "SPREAD"]
        if not spread_grp.empty:
            spread_grp = spread_grp.assign(
                _rank=spread_grp["sportsbook"].map(
                    lambda v: next((i for i, p in enumerate(_NFL_BOOKS) if str(v or "").upper() == p.upper()), 99)
                )
            ).sort_values(["_rank", "sportsbook"])
            home_rows = spread_grp[spread_grp["side"].astype(str).str.upper() == str(home_abbr).upper()]
            pick = home_rows.iloc[0] if not home_rows.empty else spread_grp.iloc[0]
            entry["openSpread"] = _round_line(pick.get("line"))
        total_grp = grp[
            (grp["type"].astype(str).str.upper() == "TOTAL")
            & (grp["side"].astype(str).str.lower() == "over")
        ]
        if not total_grp.empty:
            lines = pd.to_numeric(total_grp["line"], errors="coerce").dropna()
            entry["openTotal"] = _round_line(lines.median()) if not lines.empty else None
        ml_grp = grp[grp["type"].astype(str).str.upper().isin({"MONEYLINE", "ML"})]
        if not ml_grp.empty:
            ml_grp = ml_grp.assign(
                _rank=ml_grp["sportsbook"].map(
                    lambda v: next((i for i, p in enumerate(_NFL_BOOKS) if str(v or "").upper() == p.upper()), 99)
                )
            ).sort_values(["_rank", "sportsbook"])
            home_rows = ml_grp[ml_grp["side"].astype(str).str.upper() == str(home_abbr).upper()]
            away_rows = ml_grp[ml_grp["side"].astype(str).str.upper() == str(away_abbr).upper()]
            if not home_rows.empty:
                entry["openHomeMoneyline"] = _round_ml(home_rows.iloc[0].get("line"))
            if not away_rows.empty:
                entry["openAwayMoneyline"] = _round_ml(away_rows.iloc[0].get("line"))
        if any(v is not None for v in entry.values()):
            out[(int(season), int(wk), str(away_abbr), str(home_abbr))] = entry
    return out


def _nfl_index(year: int, week: int | None) -> dict[tuple[str, str], dict[str, float | None]]:
    games = _load_nfl_games()
    if games.empty:
        return {}

    g = games.copy()
    g["season"] = pd.to_numeric(g.get("season"), errors="coerce")
    g = g[g["season"] == int(year)]
    if week is not None:
        g["week"] = pd.to_numeric(g.get("week"), errors="coerce")
        g = g[g["week"] == int(week)]
    if g.empty:
        return {}

    initial_idx = _nfl_initial_index(_load_nfl_initial())
    out: dict[tuple[str, str], dict[str, float | None]] = {}
    for _, row in g.iterrows():
        home_raw = str(row.get("home_team") or "")
        away_raw = str(row.get("away_team") or "")
        from . import nfl_team_registry as nfl

        home = nfl.resolve_canonical(home_raw) or home_raw
        away = nfl.resolve_canonical(away_raw) or away_raw
        key = _match_key(home, away, SPORT_NFL)
        try:
            wk = int(row.get("week"))
        except (TypeError, ValueError):
            wk = int(week) if week is not None else None

        close_spread = _round_line(row.get("spread_line"))
        if close_spread is not None:
            close_spread = _round_line(-float(close_spread))

        entry = {
            "openSpread": None,
            "closeSpread": close_spread,
            "openTotal": None,
            "closeTotal": _round_line(row.get("total_line")),
            "openHomeMoneyline": None,
            "closeHomeMoneyline": _round_ml(row.get("home_moneyline")),
            "openAwayMoneyline": None,
            "closeAwayMoneyline": _round_ml(row.get("away_moneyline")),
        }
        if wk is not None:
            open_hit = initial_idx.get((int(year), wk, _nfl_abbr(away), _nfl_abbr(home)))
            if open_hit:
                entry["openSpread"] = open_hit.get("openSpread")
                entry["openTotal"] = open_hit.get("openTotal")
                entry["openHomeMoneyline"] = open_hit.get("openHomeMoneyline")
                entry["openAwayMoneyline"] = open_hit.get("openAwayMoneyline")
        if any(v is not None for v in entry.values()):
            out[key] = entry
    return out


@lru_cache(maxsize=16)
def _fastr_index(sport: str, year: int, week: int | None) -> dict[tuple[str, str], dict[str, float | None]]:
    if sport == SPORT_NFL:
        return _nfl_index(int(year), week)
    return _cfb_index(int(year), week)


def fastr_open_close_lines(
    home: str,
    away: str,
    *,
    year: int,
    week: int | None = None,
    sport: str | None = None,
) -> dict[str, Any]:
    """Return opener/closer spread, total, and moneyline from cfbfastR / nflfastR releases."""
    sp = sport or cache_sport()
    ak, hk = _match_key(home, away, sp)
    hit = _fastr_index(sp, int(year), week).get((ak, hk))
    if not hit:
        return {
            "openSpread": None,
            "closeSpread": None,
            "openTotal": None,
            "closeTotal": None,
            "openHomeMoneyline": None,
            "closeHomeMoneyline": None,
            "openAwayMoneyline": None,
            "closeAwayMoneyline": None,
            "source": "cfbfastR" if sp == SPORT_CFB else "nflfastR",
        }
    source = "cfbfastR" if sp == SPORT_CFB else "nflfastR"
    return {**hit, "source": source}


def fastr_open_close_lines_cached(
    home: str,
    away: str,
    year: int,
    *,
    week: int | None = None,
    sport: str | None = None,
) -> dict[str, Any]:
    return fastr_open_close_lines(home, away, year=year, week=week, sport=sport)
