"""NFL team name resolution — 32 franchises + book/ESPN aliases."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

NFL_TEAMS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Arizona Cardinals", "ARI", ("Cardinals", "Arizona", "ARI")),
    ("Atlanta Falcons", "ATL", ("Falcons", "Atlanta", "ATL")),
    ("Baltimore Ravens", "BAL", ("Ravens", "Baltimore", "BAL")),
    ("Buffalo Bills", "BUF", ("Bills", "Buffalo", "BUF")),
    ("Carolina Panthers", "CAR", ("Panthers", "Carolina", "CAR")),
    ("Chicago Bears", "CHI", ("Bears", "Chicago", "CHI")),
    ("Cincinnati Bengals", "CIN", ("Bengals", "Cincinnati", "CIN")),
    ("Cleveland Browns", "CLE", ("Browns", "Cleveland", "CLE")),
    ("Dallas Cowboys", "DAL", ("Cowboys", "Dallas", "DAL")),
    ("Denver Broncos", "DEN", ("Broncos", "Denver", "DEN")),
    ("Detroit Lions", "DET", ("Lions", "Detroit", "DET")),
    ("Green Bay Packers", "GB", ("Packers", "Green Bay", "GB", "GNB")),
    ("Houston Texans", "HOU", ("Texans", "Houston", "HOU")),
    ("Indianapolis Colts", "IND", ("Colts", "Indianapolis", "IND")),
    ("Jacksonville Jaguars", "JAX", ("Jaguars", "Jacksonville", "JAX", "JAC")),
    ("Kansas City Chiefs", "KC", ("Chiefs", "Kansas City", "KC", "KAN")),
    ("Las Vegas Raiders", "LV", ("Raiders", "Las Vegas", "LV", "LVR", "Oakland Raiders", "Oakland")),
    ("Los Angeles Chargers", "LAC", ("Chargers", "LA Chargers", "Los Angeles Chargers", "LAC", "San Diego Chargers")),
    ("Los Angeles Rams", "LAR", ("Rams", "LA Rams", "Los Angeles Rams", "LAR", "St. Louis Rams")),
    ("Miami Dolphins", "MIA", ("Dolphins", "Miami", "MIA")),
    ("Minnesota Vikings", "MIN", ("Vikings", "Minnesota", "MIN")),
    ("New England Patriots", "NE", ("Patriots", "New England", "NE", "NWE")),
    ("New Orleans Saints", "NO", ("Saints", "New Orleans", "NO", "NOR")),
    ("New York Giants", "NYG", ("Giants", "NY Giants", "New York Giants", "NYG")),
    ("New York Jets", "NYJ", ("Jets", "NY Jets", "New York Jets", "NYJ")),
    ("Philadelphia Eagles", "PHI", ("Eagles", "Philadelphia", "PHI")),
    ("Pittsburgh Steelers", "PIT", ("Steelers", "Pittsburgh", "PIT")),
    ("San Francisco 49ers", "SF", ("49ers", "San Francisco", "SF", "SFO", "Niners")),
    ("Seattle Seahawks", "SEA", ("Seahawks", "Seattle", "SEA")),
    ("Tampa Bay Buccaneers", "TB", ("Buccaneers", "Bucs", "Tampa Bay", "TB", "TAM")),
    ("Tennessee Titans", "TEN", ("Titans", "Tennessee", "TEN")),
    ("Washington Commanders", "WAS", ("Commanders", "Washington", "WAS", "Washington Football Team", "Redskins")),
]

ODDS_ALIASES: dict[str, str] = {
    "la rams": "Los Angeles Rams",
    "la chargers": "Los Angeles Chargers",
    "ny giants": "New York Giants",
    "ny jets": "New York Jets",
}


def normalize_team_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for alias, canonical in ODDS_ALIASES.items():
        out[normalize_team_key(alias)] = canonical
    for canonical, abbr, aliases in NFL_TEAMS:
        out[normalize_team_key(canonical)] = canonical
        out[normalize_team_key(abbr)] = canonical
        city = canonical.rsplit(" ", 1)[0]
        out[normalize_team_key(city)] = canonical
        for alias in aliases:
            out[normalize_team_key(alias)] = canonical
    return out


def resolve_canonical(name: str | None) -> str | None:
    clean = str(name or "").strip()
    if not clean:
        return None
    key = normalize_team_key(clean)
    if key in _alias_map():
        return _alias_map()[key]
    parts = clean.split()
    for n in range(len(parts), 0, -1):
        prefix = " ".join(parts[:n])
        pk = normalize_team_key(prefix)
        if pk in _alias_map():
            return _alias_map()[pk]
    return clean


def team_key(name: str | None) -> str:
    return normalize_team_key(resolve_canonical(name) or name or "")


def teams_match(a: str | None, b: str | None) -> bool:
    ak, bk = team_key(a), team_key(b)
    return bool(ak and bk and ak == bk)


def teams_match_strict(team_name: str | None, api_name: str | None) -> bool:
    return teams_match(team_name, api_name)


def list_canonical_schools() -> list[str]:
    return sorted({canonical for canonical, _, _ in NFL_TEAMS})


def match_selection_to_side(selection: str | None, home: str | None, away: str | None) -> str | None:
    if not selection:
        return None
    home_hit = teams_match_strict(home, selection)
    away_hit = teams_match_strict(away, selection)
    if home_hit and not away_hit:
        return "home"
    if away_hit and not home_hit:
        return "away"
    return None
