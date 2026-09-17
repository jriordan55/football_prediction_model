"""Canonical CFB team names — mirrors puntandrally/teams.js."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from .config import DATA_DIR

REGISTRY_PATH = DATA_DIR / "cfb_team_registry.json"
FBS_TEAMS_PATH = DATA_DIR / "cfbd_fbs_teams.json"

# Odds API / book labels that differ from CFBD canonical names.
ODDS_API_ALIASES: dict[str, str] = {
    "mississippi": "Ole Miss",
    "mississippi rebels": "Ole Miss",
    "ole miss rebels": "Ole Miss",
    "citadel bulldogs": "The Citadel",
    "the citadel bulldogs": "The Citadel",
    "appalachian state": "App State",
    "appalachian state mountaineers": "App State",
    "nicholls state": "Nicholls",
    "nicholls state colonels": "Nicholls",
    "sam houston state": "Sam Houston",
    "sam houston state bearkats": "Sam Houston",
    "houston baptist": "Houston",
    "houston baptist huskies": "Houston",
    "southern mississippi": "Southern Miss",
    "southern mississippi golden eagles": "Southern Miss",
    "ul monroe": "UL Monroe",
    "ul monroe warhawks": "UL Monroe",
    "louisiana ragin cajuns": "Louisiana",
    "louisiana ragin' cajuns": "Louisiana",
    "hawaii": "Hawai'i",
    "hawaii rainbow warriors": "Hawai'i",
    "san jose state": "San José State",
    "san jose state spartans": "San José State",
    "liu": "Long Island University",
    "liu sharks": "Long Island University",
    "se louisiana": "SE Louisiana",
    "southeastern louisiana": "SE Louisiana",
    "southeastern louisiana lions": "SE Louisiana",
    "miami (fl)": "Miami",
    "miami fl": "Miami",
    "miami-fl": "Miami",
    "miami-oh": "Miami (OH)",
    "miami oh": "Miami (OH)",
    "miami (oh)": "Miami (OH)",
    "nc state": "NC State",
    "north carolina state": "NC State",
    "ul lafayette": "Louisiana",
    "ul-lafayette": "Louisiana",
    "ul monroe": "UL Monroe",
    "ga tech": "Georgia Tech",
    "georgia tech": "Georgia Tech",
    "florida atlantic": "Florida Atlantic",
    "florida international": "Florida International",
    "south florida": "South Florida",
    "usf": "South Florida",
    "fiu": "Florida International",
    "fau": "Florida Atlantic",
    "middle tennessee": "Middle Tennessee",
    "mtsu": "Middle Tennessee",
    "western kentucky": "Western Kentucky",
    "wku": "Western Kentucky",
    "boise state": "Boise State",
    "fresno state": "Fresno State",
    "san diego state": "San Diego State",
    "sdsu": "San Diego State",
    "colorado state": "Colorado State",
    "csu": "Colorado State",
    "iowa state": "Iowa State",
    "kansas state": "Kansas State",
    "oklahoma state": "Oklahoma State",
    "okstate": "Oklahoma State",
    "oregon state": "Oregon State",
    "washington state": "Washington State",
    "wsu": "Washington State",
    "arizona state": "Arizona State",
    "asu": "Arizona State",
    "penn state": "Penn State",
    "psu": "Penn State",
    "michigan state": "Michigan State",
    "msu": "Michigan State",
    "mississippi state": "Mississippi State",
    "miss state": "Mississippi State",
    "texas state": "Texas State",
    "txst": "Texas State",
    "texas tech": "Texas Tech",
    "ttu": "Texas Tech",
    "texas a&m": "Texas A&M",
    "tamu": "Texas A&M",
    "georgia state": "Georgia State",
    "gsu": "Georgia State",
    "georgia southern": "Georgia Southern",
    "app state": "App State",
    "appalachian state": "App State",
    "northern arizona": "Northern Arizona",
    "northern illinois": "Northern Illinois",
    "niu": "Northern Illinois",
    "central michigan": "Central Michigan",
    "cmu": "Central Michigan",
    "eastern michigan": "Eastern Michigan",
    "western michigan": "Western Michigan",
    "bowling green": "Bowling Green",
    "bgsu": "Bowling Green",
    "kent state": "Kent State",
    "ball state": "Ball State",
    "arkansas state": "Arkansas State",
    "astate": "Arkansas State",
    "louisiana tech": "Louisiana Tech",
    "louisiana monroe": "UL Monroe",
    "ole miss": "Ole Miss",
    "pittsburgh": "Pittsburgh",
    "pitt": "Pittsburgh",
    "usc": "USC",
    "southern california": "USC",
    "byu": "BYU",
    "ucf": "UCF",
    "smu": "SMU",
    "tcu": "TCU",
    "unlv": "UNLV",
    "utsa": "UTSA",
    "utep": "UTEP",
    "uconn": "UConn",
    "umass": "Massachusetts",
}


def _resolve_prefix(parts: list[str], aliases: dict[str, str]) -> str | None:
    if len(parts) < 1:
        return None
    for n in range(len(parts), 0, -1):
        prefix = " ".join(parts[:n])
        sk = normalize_team_key(prefix)
        if sk in aliases:
            return aliases[sk]
    return None


def _expand_st_tokens(parts: list[str]) -> list[str]:
    """Odds API often abbreviates State as St (e.g. Youngstown St Penguins)."""
    out: list[str] = []
    for i, part in enumerate(parts):
        if part.lower() == "st" and i > 0:
            out.append("State")
        else:
            out.append(part)
    return out


def _add_state_abbrev_aliases(out: dict[str, str]) -> None:
    """Texas St -> Texas State for every * State school in the registry."""
    for canonical in set(out.values()):
        if not str(canonical).endswith(" State"):
            continue
        base = str(canonical)[: -len(" State")]
        if not base:
            continue
        st_key = normalize_team_key(f"{base} St")
        if st_key not in out:
            out[st_key] = canonical


def normalize_team_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&#039;", "'").replace("&amp;", "&")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _clean_name(name: str) -> str:
    return str(name or "").strip().replace("&#039;", "'").replace("&amp;", "&")


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    out: dict[str, str] = {}

    for alias, canonical in ODDS_API_ALIASES.items():
        out[normalize_team_key(alias)] = canonical

    if REGISTRY_PATH.exists():
        try:
            reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            for alias, canonical in (reg.get("aliasToCanonical") or {}).items():
                out[normalize_team_key(alias)] = canonical
            for team in reg.get("teams") or []:
                canonical = team.get("canonical")
                if not canonical:
                    continue
                out[normalize_team_key(canonical)] = canonical
                for alias in team.get("aliases") or []:
                    out[normalize_team_key(alias)] = canonical
        except (json.JSONDecodeError, OSError):
            pass

    if FBS_TEAMS_PATH.exists():
        try:
            for t in json.loads(FBS_TEAMS_PATH.read_text(encoding="utf-8")):
                school = t.get("school")
                if not school:
                    continue
                out[normalize_team_key(school)] = school
                abbr = str(t.get("abbreviation") or "")
                if abbr:
                    out[normalize_team_key(abbr)] = school
                for alias in t.get("alternateNames") or []:
                    out[normalize_team_key(alias)] = school
        except (json.JSONDecodeError, OSError):
            pass

    _add_state_abbrev_aliases(out)
    return out


def resolve_canonical(name: str | None) -> str | None:
    """Resolve any team label to canonical school — longest alias prefix wins."""
    try:
        from .sport_context import SPORT_NFL, get_sport

        if get_sport() == SPORT_NFL:
            from . import nfl_team_registry as nfl

            return nfl.resolve_canonical(name)
    except Exception:
        pass

    clean = _clean_name(name or "")
    if not clean:
        return None

    aliases = _alias_map()
    key = normalize_team_key(clean)
    if key in aliases:
        return aliases[key]

    parts = clean.split()
    hit = _resolve_prefix(parts, aliases)
    if hit:
        return hit

    expanded = _expand_st_tokens(parts)
    if expanded != parts:
        hit = _resolve_prefix(expanded, aliases)
        if hit:
            return hit

    return clean


def team_key(name: str | None) -> str:
    try:
        from .sport_context import SPORT_NFL, get_sport

        if get_sport() == SPORT_NFL:
            from . import nfl_team_registry as nfl

            return nfl.team_key(name)
    except Exception:
        pass
    canonical = resolve_canonical(name) or name or ""
    return normalize_team_key(canonical)


def teams_match(a: str | None, b: str | None) -> bool:
    try:
        from .sport_context import SPORT_NFL, get_sport

        if get_sport() == SPORT_NFL:
            from . import nfl_team_registry as nfl

            return nfl.teams_match(a, b)
    except Exception:
        pass
    return team_key(a) == team_key(b) and bool(team_key(a))


def teams_match_strict(team_name: str | None, api_name: str | None) -> bool:
    """Strict match — canonical keys must match exactly (no substring tricks)."""
    a_key = team_key(team_name)
    b_key = team_key(api_name)
    return bool(a_key and b_key and a_key == b_key)


def teams_are_distinct(a: str | None, b: str | None) -> bool:
    """True when two labels resolve to different canonical schools."""
    ak, bk = team_key(a), team_key(b)
    return bool(ak and bk and ak != bk)


def find_prefix_collision_pairs(schools: list[str] | None = None) -> list[tuple[str, str]]:
    """Return (short, long) pairs where normalized keys share a prefix."""
    if schools is None:
        schools = list_canonical_schools()
    keys = {normalize_team_key(s): s for s in schools}
    pairs: list[tuple[str, str]] = []
    ordered = sorted(keys)
    for i, short_k in enumerate(ordered):
        for long_k in ordered[i + 1 :]:
            if long_k.startswith(short_k) and len(long_k) > len(short_k):
                pairs.append((keys[short_k], keys[long_k]))
    return pairs


def list_canonical_schools() -> list[str]:
    aliases = _alias_map()
    return sorted(set(aliases.values()))


@lru_cache(maxsize=1)
def cfbd_abbr_lookup() -> dict[str, str]:
    """CFBD official abbreviations — no network."""
    out: dict[str, str] = {}
    if FBS_TEAMS_PATH.exists():
        try:
            for t in json.loads(FBS_TEAMS_PATH.read_text(encoding="utf-8")):
                school = str(t.get("school") or "").strip()
                abbr = str(t.get("abbreviation") or "").strip().upper()
                if school and abbr:
                    out[school] = abbr
                    out[normalize_team_key(school)] = abbr
        except (json.JSONDecodeError, OSError):
            pass
    return out


@lru_cache(maxsize=4)
def official_abbr_lookup(year: int = 2026) -> dict[str, str]:
    """ESPN + CFBD official abbreviations keyed by canonical school name."""
    out: dict[str, str] = dict(cfbd_abbr_lookup())

    try:
        from .espn_client import fetch_scoreboard_season_cached

        sb = fetch_scoreboard_season_cached(int(year))
        for _, row in sb.iterrows():
            for side in ("home", "away"):
                name = str(row.get(side) or "").strip()
                abbr = str(row.get(f"{side}_abbr") or "").strip().upper()
                if not name or not abbr:
                    continue
                out[name] = abbr
                canon = resolve_canonical(name)
                if canon:
                    out[canon] = abbr
                    out[normalize_team_key(canon)] = abbr
    except Exception:
        pass

    return out


def official_team_abbr(name: str, *, year: int = 2026) -> str | None:
    """Return ESPN/CFBD abbreviation for a school name, if known."""
    clean = _clean_name(name or "")
    if not clean:
        return None
    lookup = official_abbr_lookup(int(year))
    if clean in lookup:
        return lookup[clean]
    canon = resolve_canonical(clean)
    if canon and canon in lookup:
        return lookup[canon]
    key = normalize_team_key(clean)
    if key in lookup:
        return lookup[key]
    if canon:
        ck = normalize_team_key(canon)
        if ck in lookup:
            return lookup[ck]
    return None


def audit_team_mappings(
    schools: list[str] | None = None,
    *,
    mascots: tuple[str, ...] = ("Wildcats", "Bulldogs", "Tigers", "Eagles", "Cougars"),
) -> list[str]:
    """Return human-readable failures for team resolution across the slate."""
    schools = schools or list_canonical_schools()
    failures: list[str] = []
    for school in schools:
        for mascot in mascots:
            label = f"{school} {mascot}"
            canon = resolve_canonical(label)
            if normalize_team_key(canon) != normalize_team_key(school):
                failures.append(f"resolve: {label!r} -> {canon!r} (expected {school!r})")
    for short, long in find_prefix_collision_pairs(schools):
        if teams_match(f"{long} Wildcats", short):
            failures.append(f"collision: {long!r} label matched shorter {short!r}")
    return failures


def match_selection_to_side(selection: str | None, home: str | None, away: str | None) -> str | None:
    """Map a bookmaker spread selection to 'home' or 'away' for this matchup."""
    try:
        from .sport_context import SPORT_NFL, get_sport

        if get_sport() == SPORT_NFL:
            from . import nfl_team_registry as nfl

            return nfl.match_selection_to_side(selection, home, away)
    except Exception:
        pass
    if not selection:
        return None

    home_hit = teams_match_strict(home, selection)
    away_hit = teams_match_strict(away, selection)
    if home_hit and not away_hit:
        return "home"
    if away_hit and not home_hit:
        return "away"
    return None
