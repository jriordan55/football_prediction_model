"""CFBD + ESPN team logo URLs — never render empty logo slots."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import DATA_DIR
from .sport_context import SPORT_NFL
from .team_registry import FBS_TEAMS_PATH, resolve_canonical, team_key, teams_match

_LOGO_SIZE = 64


@lru_cache(maxsize=4)
def _cfbd_logo_index(sport: str) -> dict[str, str]:
    if sport == SPORT_NFL:
        return {}

    path = FBS_TEAMS_PATH
    if not path.exists():
        return {}
    try:
        teams = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, str] = {}
    for row in teams:
        if not isinstance(row, dict):
            continue
        tid = row.get("id")
        if tid is None:
            continue
        logos = row.get("logos") or []
        url = ""
        for candidate in logos:
            s = str(candidate)
            if f"/logos/{_LOGO_SIZE}/" in s:
                url = s
                break
        if not url:
            url = f"https://cdn.collegefootballdata.com/logos/{_LOGO_SIZE}/{tid}.png"
        school = str(row.get("school") or "")
        if school:
            out[team_key(school)] = url
        abbr = str(row.get("abbreviation") or "")
        if abbr:
            out[team_key(abbr)] = url
        for alt in row.get("alternateNames") or []:
            if alt:
                out[team_key(str(alt))] = url
    return out


@lru_cache(maxsize=4)
def _espn_logo_index(sport: str) -> dict[str, str]:
    from .espn_client import fetch_scoreboard_cached
    from .nfl_team_registry import NFL_TEAMS

    out: dict[str, str] = {}
    for year in (2026, 2025):
        try:
            sb = fetch_scoreboard_cached(year=year)
        except Exception:
            continue
        if sb.empty:
            continue
        for _, row in sb.iterrows():
            for side in ("home", "away"):
                name = str(row.get(side) or "")
                logo = str(row.get(f"{side}_logo") or "").strip()
                abbr = str(row.get(f"{side}_abbr") or "").strip()
                if name and logo and logo.lower() != "nan":
                    canon = resolve_canonical(name) or name
                    out[team_key(canon)] = logo
                    if abbr:
                        out[team_key(abbr)] = logo
        if out:
            break

    if sport == SPORT_NFL:
        for canonical, abbr, _aliases in NFL_TEAMS:
            key = team_key(canonical)
            if key not in out and abbr:
                abbr_key = team_key(abbr)
                if abbr_key in out:
                    out[key] = out[abbr_key]
    return out


def clear_logo_caches() -> None:
    _cfbd_logo_index.cache_clear()
    _espn_logo_index.cache_clear()


def team_logo_url(team_name: str | None, *, fallback: str | None = None) -> str:
    """Best logo URL for a team — sport-aware; NFL never uses CFBD."""
    from .sport_context import get_sport

    sport = get_sport()
    raw = str(team_name or "").strip()
    fb = str(fallback or "").strip()
    if not raw and fb:
        raw = fb
    if not raw:
        return fb if fb and fb.lower() not in ("nan", "none", "") else ""

    canon = resolve_canonical(raw) or raw
    key = team_key(canon)

    espn = _espn_logo_index(sport)
    if key in espn:
        return espn[key]
    for bk, url in espn.items():
        if teams_match(bk, canon):
            return url

    cfbd = _cfbd_logo_index(sport)
    if cfbd:
        if key in cfbd:
            return cfbd[key]
        for bk, url in cfbd.items():
            if teams_match(bk, canon):
                return url

    if fb and fb.lower() not in ("nan", "none", ""):
        return fb

    return ""


def enrich_row_logos(row: dict[str, Any]) -> dict[str, Any]:
    """Fill homeLogo/awayLogo/teamLogo on a slate or board row."""
    out = dict(row)
    home = out.get("home")
    away = out.get("away")
    if home:
        hl = team_logo_url(
            str(home),
            fallback=str(out.get("homeLogo") or out.get("home_logo") or ""),
        )
        if hl:
            out["homeLogo"] = hl
            out["home_logo"] = hl
    if away:
        al = team_logo_url(
            str(away),
            fallback=str(out.get("awayLogo") or out.get("away_logo") or ""),
        )
        if al:
            out["awayLogo"] = al
            out["away_logo"] = al
    team = out.get("team") or out.get("teamLabel")
    if team:
        tl = team_logo_url(str(team), fallback=str(out.get("teamLogo") or out.get("team_logo") or ""))
        if tl:
            out["teamLogo"] = tl
            out["team_logo"] = tl
    return out


def logo_img_html(url: str | None, *, cls: str = "bo-logo", alt: str = "") -> str:
    """Render img tag or invisible 1px placeholder — never broken-image icon."""
    import html

    src = str(url or "").strip()
    if src and src.lower() not in ("nan", "none", ""):
        return f'<img class="{cls}" src="{html.escape(src)}" alt="{html.escape(alt)}" loading="lazy" />'
    return f'<div class="{cls} bo-logo-fallback" aria-hidden="true"></div>'
