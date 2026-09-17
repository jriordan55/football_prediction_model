"""DraftKings CFB team yard markets — scraped from sportsbook.draftkings.com only."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DATA_DIR
from .draftkings_sportsbook import (
    CFB_LEAGUE_ID,
    fetch_team_yard_quotes,
    last_fetch_status,
    match_event,
)
from .odds_client import market_label
from .odds_math import american_to_implied, devig_two_way
from .team_registry import resolve_canonical, teams_match

_DK = "draftkings"
_CACHE_DIR = DATA_DIR / "team_yards_odds_cache"
_CACHE_VERSION = 10

_FETCH_STATUS: dict[str, str | None] = {"mode": "unknown", "error": None}


def yard_odds_fetch_status() -> dict[str, str | None]:
    return dict(_FETCH_STATUS)

_TEAM_YARD_KEY = re.compile(
    r"^(alternate_)?team_total_(pass_|rush_|rec_|receiving_)?(yds|yards)(_h1|_h2|_q1|_q2|_q3|_q4)?$",
    re.I,
)

_POINT_TEAM_TOTALS = frozenset(
    {
        "team_totals",
        "alternate_team_totals",
        "team_totals_h1",
        "alternate_team_totals_h1",
        "team_totals_q1",
        "alternate_team_totals_q1",
    }
)

_MILESTONE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?")


def is_team_yards_market_key(market_key: str) -> bool:
    """True for DK team yard markets (excludes point team totals and player props)."""
    mk = str(market_key or "").lower().strip()
    if not mk or mk.startswith("player_"):
        return False
    if mk in _POINT_TEAM_TOTALS:
        return False
    if _TEAM_YARD_KEY.match(mk):
        return True
    if "team_total" in mk and ("yard" in mk or "yds" in mk or "rush" in mk or "rec" in mk or "pass" in mk):
        return True
    return False


def _is_yards_line(market_key: str, line: Any) -> bool:
    if is_team_yards_market_key(str(market_key or "")):
        return True
    mk = str(market_key or "").lower()
    if mk in _POINT_TEAM_TOTALS:
        try:
            return float(line) >= 80.0
        except (TypeError, ValueError):
            return False
    return False


def market_spec(market_key: str) -> dict[str, str]:
    mk = str(market_key or "").lower()
    category = "total"
    if "pass" in mk:
        category = "pass"
    elif "rush" in mk:
        category = "rush"
    elif "rec" in mk or "receiv" in mk:
        category = "receiving"
    period = "game"
    if "_h1" in mk or mk.endswith("_1h"):
        period = "1h"
    elif "_h2" in mk or mk.endswith("_2h"):
        period = "2h"
    elif "_q1" in mk:
        period = "q1"
    elif "_q2" in mk:
        period = "q2"
    elif "_q3" in mk:
        period = "q3"
    elif "_q4" in mk:
        period = "q4"
    return {
        "market_key": mk,
        "label": market_label(mk),
        "category": category,
        "period": period,
    }


def _parse_line(raw: Any, selection: str, description: str) -> float | None:
    if raw is not None and str(raw).strip() not in ("", "nan", "None"):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    for text in (selection, description):
        m = _MILESTONE_RE.search(str(text or ""))
        if m:
            val = float(m.group(1))
            if "+" in str(text) and val == int(val):
                return val - 0.5
            return val
    return None


def _row_team_and_side(selection: str, description: str) -> tuple[str, str | None]:
    sel = str(selection or "").strip()
    desc = str(description or "").strip()
    if re.fullmatch(r"over|under", sel, flags=re.I):
        return desc, sel.lower()
    if re.fullmatch(r"over|under", desc, flags=re.I):
        return sel, desc.lower()
    if re.fullmatch(r"over|under", sel.split()[0] if sel else "", flags=re.I):
        return desc, sel.split()[0].lower()
    side = "over" if "+" in sel or "+" in desc else None
    team = desc if desc and not re.fullmatch(r"over|under", desc, flags=re.I) else sel
    return team, side


def _cache_path(year: int, week: int) -> Path:
    return _CACHE_DIR / f"team_yards_dk_{year}_w{week}_v{_CACHE_VERSION}.json"


def yards_cache_mtime(year: int, week: int) -> float:
    path = _cache_path(year, week)
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


def yards_cache_exists(year: int, week: int) -> bool:
    return _cache_path(year, week).exists()


def _load_disk_cache(year: int, week: int) -> list[dict[str, Any]] | None:
    path = _cache_path(year, week)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and int(payload.get("version") or 0) < _CACHE_VERSION:
            return None
        return payload.get("games") if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _save_disk_cache(
    games: list[dict[str, Any]],
    year: int,
    week: int,
    *,
    subcategories: list[dict[str, Any]] | None = None,
) -> None:
    if not games:
        return
    path = _cache_path(year, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "games": games,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "version": _CACHE_VERSION,
        "source": "draftkings.com",
        "league_id": CFB_LEAGUE_ID,
        "subcategories": subcategories or [],
    }
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def _pair_alt_lines(rows: pd.DataFrame, team: str) -> list[dict[str, Any]]:
    if rows.empty:
        return []

    lines: dict[float, dict[str, Any]] = {}
    for _, r in rows.iterrows():
        sel = str(r.get("selection") or "")
        desc = str(r.get("description") or "")
        team_name, side = _row_team_and_side(sel, desc)
        if not teams_match(team_name, team):
            continue
        ln = _parse_line(r.get("line"), sel, desc)
        if ln is None:
            continue
        if side is None:
            side = "over" if "+" in sel or "+" in desc else "over"
        bucket = lines.setdefault(ln, {"line": ln, "over_price": None, "under_price": None})
        price = r.get("price")
        if side == "over" or "over" in side:
            bucket["over_price"] = price
        elif side == "under" or "under" in side:
            bucket["under_price"] = price

    out: list[dict[str, Any]] = []
    for ln in sorted(lines.keys()):
        b = lines[ln]
        fair = devig_two_way(b.get("over_price"), b.get("under_price"))
        if fair is None and b.get("over_price") is not None:
            fair = american_to_implied(b.get("over_price"))
        out.append(
            {
                "line": ln,
                "line_display": f"{int(ln + 0.5)}+" if ln % 1 else str(ln),
                "over_price": b.get("over_price"),
                "under_price": b.get("under_price"),
                "fair_over": fair,
            }
        )
    return out


def _main_line(alts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not alts:
        return None
    paired = [a for a in alts if a.get("over_price") and a.get("under_price")]
    pool = paired if paired else alts
    best = None
    best_score = float("inf")
    for row in pool:
        op = row.get("over_price")
        up = row.get("under_price")
        try:
            o_imp = american_to_implied(op) or 0.5
            u_imp = american_to_implied(up) if up else (1.0 - o_imp)
            score = abs(o_imp - 0.5) + abs(u_imp - 0.5)
        except (TypeError, ValueError):
            score = 999
        if score < best_score:
            best_score = score
            best = row
    return best


def _median_implied_line(alts: list[dict[str, Any]]) -> float | None:
    if not alts:
        return None
    fairs = [(float(a["line"]), a.get("fair_over")) for a in alts if a.get("fair_over") is not None]
    if len(fairs) < 2:
        main = _main_line(alts)
        return float(main["line"]) if main else None
    fairs.sort(key=lambda x: x[0])
    for i in range(len(fairs) - 1):
        l0, p0 = fairs[i]
        l1, p1 = fairs[i + 1]
        if p0 >= 0.5 >= p1 or p1 >= 0.5 >= p0:
            if abs(p1 - p0) < 1e-6:
                return l0
            w = (p0 - 0.5) / (p0 - p1)
            return round(l0 + w * (l1 - l0), 1)
    main = _main_line(alts)
    return float(main["line"]) if main else None


def _build_team_markets(yards_df: pd.DataFrame, team: str) -> dict[str, dict[str, Any]]:
    if yards_df.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for mk, mdf in yards_df.groupby(yards_df["market_key"].astype(str)):
        if not is_team_yards_market_key(mk):
            sample_line = mdf["line"].dropna().iloc[0] if not mdf["line"].dropna().empty else None
            if not _is_yards_line(mk, sample_line):
                continue
        alts = _pair_alt_lines(mdf, team)
        if not alts:
            continue
        main = _main_line(alts)
        spec = market_spec(mk)
        out[str(mk)] = {
            **spec,
            "main_line": main.get("line") if main else None,
            "main_over_price": main.get("over_price") if main else None,
            "main_under_price": main.get("under_price") if main else None,
            "market_median": _median_implied_line(alts),
            "alt_lines": alts,
            "alt_count": len(alts),
        }
    return out


def _build_event_package(
    event_id: str,
    home: str,
    away: str,
    yards_df: pd.DataFrame,
    *,
    market_keys: list[str],
) -> dict[str, Any]:
    teams: dict[str, Any] = {}
    side_names = [t for t in (home, away) if t]
    if not side_names and not yards_df.empty and "description" in yards_df.columns:
        side_names = sorted({str(d) for d in yards_df["description"].dropna().unique() if str(d).strip()})
    for team in side_names:
        markets = _build_team_markets(yards_df, team)
        if not markets:
            continue
        primary_key = None
        for pref in (
            "alternate_team_total_rush_yds_h1",
            "team_total_rush_yds_h1",
            "team_total_yds",
            "alternate_team_total_yds",
            "team_total_yards",
            "alternate_team_total_rush_yds",
            "team_total_rush_yds",
        ):
            if pref in markets:
                primary_key = pref
                break
        if primary_key is None:
            primary_key = next(iter(sorted(markets.keys())))

        primary = markets[primary_key]
        teams[team] = {
            "team": resolve_canonical(team) or team,
            "primary_market_key": primary_key,
            "markets": markets,
            "main_line": primary.get("main_line"),
            "main_over_price": primary.get("main_over_price"),
            "main_under_price": primary.get("main_under_price"),
            "market_median": primary.get("market_median"),
            "alt_lines": primary.get("alt_lines") or [],
            "market_key": primary_key,
        }
    return {
        "event_id": event_id,
        "home": home,
        "away": away,
        "market_keys": market_keys,
        "discovered_keys": [k for k in market_keys if is_team_yards_market_key(k)],
        "teams": teams,
    }


def fetch_event_team_yards(
    event_id: str,
    home: str,
    away: str,
    *,
    flat_df: pd.DataFrame | None = None,
    tab: str | None = None,
) -> dict[str, Any]:
    """Pull every DK team-yard market + alt line for one event."""
    _ = tab
    if flat_df is None:
        rows, _events, _subs = fetch_team_yard_quotes()
        flat_df = pd.DataFrame(rows) if rows else pd.DataFrame()
    if flat_df.empty:
        return {
            "event_id": event_id,
            "home": home,
            "away": away,
            "market_keys": [],
            "teams": {},
            "error": "no_dk_rows",
        }

    event_rows = flat_df[flat_df["event_id"].astype(str) == str(event_id)]
    if event_rows.empty:
        return {
            "event_id": event_id,
            "home": home,
            "away": away,
            "market_keys": [],
            "teams": {},
            "error": "no_event_rows",
        }

    market_keys = sorted(event_rows["market_key"].astype(str).unique().tolist())
    return _build_event_package(event_id, home, away, event_rows, market_keys=market_keys)


@lru_cache(maxsize=32)
def all_market_labels_for_week(year: int, week: int) -> tuple[str, ...]:
    cached = _load_disk_cache(year, week) or []
    keys: list[str] = []
    for g in cached:
        for tdata in (g.get("teams") or {}).values():
            for mk in (tdata.get("markets") or {}):
                if mk not in keys:
                    keys.append(mk)
    return tuple(sorted(keys, key=lambda k: (market_spec(k)["category"], market_spec(k)["period"], k)))


def load_week_team_yards_odds(
    year: int,
    week: int,
    *,
    matchups: list[dict[str, Any]] | None = None,
    tab: str | None = None,
    refresh: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """All DK team-yard markets — scraped from DraftKings website (Edge/Chrome)."""
    _ = tab
    if not refresh:
        cached = _load_disk_cache(year, week)
        if cached is not None:
            _FETCH_STATUS.update({"mode": "draftkings.com/cache", "error": None})
            return cached

    rows: list[dict[str, Any]] = []
    dk_events: list[dict[str, Any]] = []
    subs: list[dict[str, Any]] = []

    try:
        rows, dk_events, subs = fetch_team_yard_quotes()
        _FETCH_STATUS.update(last_fetch_status())
    except Exception as exc:
        err = str(exc).strip() or f"{type(exc).__name__} (no message)"
        _FETCH_STATUS.update({"mode": "failed", "error": err})

    flat_df = pd.DataFrame(rows) if rows else pd.DataFrame()

    if flat_df.empty:
        stale = _load_disk_cache(year, week)
        if stale:
            _FETCH_STATUS.update(
                {
                    "mode": "draftkings.com/cache",
                    "error": _FETCH_STATUS.get("error"),
                }
            )
        return stale or []

    market_keys = sorted({str(s.get("market_key")) for s in subs if s.get("market_key")})
    if not market_keys and not flat_df.empty:
        market_keys = sorted(flat_df["market_key"].astype(str).unique().tolist())

    targets: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(eid: str, home: str, away: str) -> None:
        if not eid or eid in seen:
            return
        seen.add(eid)
        targets.append((eid, home, away))

    if matchups:
        for m in matchups:
            home = str(m.get("home") or "")
            away = str(m.get("away") or "")
            ev = match_event(dk_events, home, away)
            if ev:
                add(str(ev["event_id"]), str(ev.get("home") or home), str(ev.get("away") or away))
            elif m.get("event_id"):
                add(str(m["event_id"]), home, away)

    for ev in dk_events:
        add(str(ev.get("event_id") or ""), str(ev.get("home") or ""), str(ev.get("away") or ""))

    # If eventgroup missing, still build from row event_ids + CFBD matchups.
    if not targets and not flat_df.empty:
        for eid in flat_df["event_id"].astype(str).unique():
            if not eid or eid == "nan":
                continue
            home = away = ""
            if matchups:
                for m in matchups:
                    if str(m.get("event_id") or "") == eid:
                        home, away = str(m.get("home") or ""), str(m.get("away") or "")
                        break
            add(eid, home, away)

    games: list[dict[str, Any]] = []
    for eid, home, away in targets[: max(0, int(limit))]:
        if flat_df.empty:
            continue
        pkg = _build_event_package(
            eid,
            home,
            away,
            flat_df[flat_df["event_id"].astype(str) == str(eid)],
            market_keys=market_keys,
        )
        if pkg.get("teams"):
            games.append(pkg)

    if games:
        _save_disk_cache(games, year, week, subcategories=subs)
        all_market_labels_for_week.cache_clear()
    return games
