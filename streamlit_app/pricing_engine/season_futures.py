"""Season futures Monte Carlo — win totals, playoffs, Super Bowl."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from lib.odds_math import implied_to_american
from lib.sport_context import SPORT_CFB, SPORT_NFL

from .constants import CFB_R_DISPERSION, DEFAULT_SIMS, NFL_R_DISPERSION
from .ratings import price_base_game
from .score_sim import sample_negbin, sample_poisson

# NFL divisions — canonical full names
NFL_DIVISIONS: dict[str, dict[str, list[str]]] = {
    "AFC": {
        "East": ["Buffalo Bills", "Miami Dolphins", "New England Patriots", "New York Jets"],
        "North": ["Baltimore Ravens", "Cincinnati Bengals", "Cleveland Browns", "Pittsburgh Steelers"],
        "South": ["Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Tennessee Titans"],
        "West": ["Denver Broncos", "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers"],
    },
    "NFC": {
        "East": ["Dallas Cowboys", "New York Giants", "Philadelphia Eagles", "Washington Commanders"],
        "North": ["Chicago Bears", "Detroit Lions", "Green Bay Packers", "Minnesota Vikings"],
        "South": ["Atlanta Falcons", "Carolina Panthers", "New Orleans Saints", "Tampa Bay Buccaneers"],
        "West": ["Arizona Cardinals", "Los Angeles Rams", "San Francisco 49ers", "Seattle Seahawks"],
    },
}

NFL_TEAM_META: dict[str, dict[str, str]] = {}
for conf, divs in NFL_DIVISIONS.items():
    for div, teams in divs.items():
        for t in teams:
            NFL_TEAM_META[t] = {"conference": conf, "division": div}


@dataclass
class TeamSeasonState:
    team: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    pts_for: int = 0
    pts_against: int = 0
    div_wins: int = 0
    conf: str = ""
    division: str = ""
    stage: str = "missed_playoffs"
    conf_finish: int = 16
    div_finish: int = 4


def _resolve_nfl(name: str) -> str:
    from lib.nfl_team_registry import resolve_canonical

    return resolve_canonical(name) or name


def _resolve_cfb(name: str) -> str:
    from lib.team_registry import resolve_canonical

    return resolve_canonical(name) or name


def _trim_to_reg_season(games: list[dict[str, Any]], *, max_per_team: int) -> list[dict[str, Any]]:
    """Keep up to max_per_team games per franchise (17 NFL / 12 CFB regular)."""
    games = sorted(games, key=lambda g: (g.get("week") or 99, g.get("home") or ""))
    team_count: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for g in games:
        home, away = g["home"], g["away"]
        if team_count.get(home, 0) >= max_per_team or team_count.get(away, 0) >= max_per_team:
            continue
        kept.append(g)
        team_count[home] = team_count.get(home, 0) + 1
        team_count[away] = team_count.get(away, 0) + 1
    return kept


def load_season_schedule(sport: str, year: int) -> list[dict[str, Any]]:
    sid = str(sport).lower()
    if sid == SPORT_NFL:
        from lib.nfl_games import load_nfl_games

        raw = load_nfl_games(year)
        by_matchup: dict[str, dict[str, Any]] = {}
        for g in raw:
            if g.get("seasonType") not in (None, "regular", "Regular"):
                if str(g.get("seasonType") or "").lower() != "regular":
                    continue
            home = _resolve_nfl(g.get("homeTeam") or g.get("home") or "")
            away = _resolve_nfl(g.get("awayTeam") or g.get("away") or "")
            if not home or not away:
                continue
            if home in ("AFC", "NFC") or away in ("AFC", "NFC"):
                continue
            if home not in NFL_TEAM_META or away not in NFL_TEAM_META:
                continue
            try:
                wk = int(g.get("week") or 0)
            except (TypeError, ValueError):
                wk = 0
            if wk < 1 or wk > 18:
                continue
            key = f"{away}|{home}"
            row = {
                "home": home,
                "away": away,
                "week": wk,
                "neutral": bool(g.get("neutralSite")),
                "completed": bool(g.get("completed")),
                "home_score": g.get("homePoints") or g.get("home_score"),
                "away_score": g.get("awayPoints") or g.get("away_score"),
                "divisional": _is_divisional(home, away, sport),
            }
            prev = by_matchup.get(key)
            if prev is None or (row["completed"] and not prev.get("completed")):
                by_matchup[key] = row
        return _trim_to_reg_season(list(by_matchup.values()), max_per_team=17)

    from lib.cfbd_games import load_cfbd_games

    raw = load_cfbd_games(year)
    if not raw:
        from lib.espn_client import fetch_scoreboard_season

        df = fetch_scoreboard_season(year, sport=SPORT_CFB)
        games = []
        for _, row in df.iterrows():
            home = _resolve_cfb(row.get("home") or "")
            away = _resolve_cfb(row.get("away") or "")
            if not home or not away:
                continue
            games.append(
                {
                    "home": home,
                    "away": away,
                    "week": row.get("week"),
                    "neutral": False,
                    "completed": bool(row.get("completed")),
                    "home_score": row.get("home_score"),
                    "away_score": row.get("away_score"),
                    "divisional": False,
                }
            )
        return _trim_to_reg_season(games, max_per_team=12)

    games = []
    for g in raw:
        if g.get("seasonType") != "regular":
            continue
        if g.get("homeClassification") != "fbs" or g.get("awayClassification") != "fbs":
            continue
        home = _resolve_cfb(g.get("homeTeam") or "")
        away = _resolve_cfb(g.get("awayTeam") or "")
        if not home or not away:
            continue
        games.append(
            {
                "home": home,
                "away": away,
                "week": g.get("week"),
                "neutral": bool(g.get("neutralSite")),
                "completed": bool(g.get("completed")),
                "home_score": g.get("homePoints"),
                "away_score": g.get("awayPoints"),
                "divisional": g.get("homeConference") == g.get("awayConference"),
            }
        )
    return _trim_to_reg_season(games, max_per_team=12)


def _is_divisional(home: str, away: str, sport: str) -> bool:
    if str(sport).lower() != SPORT_NFL:
        return False
    home = _resolve_nfl(home)
    away = _resolve_nfl(away)
    mh = NFL_TEAM_META.get(home) or {}
    ma = NFL_TEAM_META.get(away) or {}
    return mh.get("division") == ma.get("division") and bool(mh.get("division"))


def _init_team_states(teams: set[str], sport: str) -> dict[str, TeamSeasonState]:
    out: dict[str, TeamSeasonState] = {}
    for t in teams:
        st = TeamSeasonState(team=t)
        if str(sport).lower() == SPORT_NFL:
            meta = NFL_TEAM_META.get(t) or {}
            st.conf = meta.get("conference", "")
            st.division = meta.get("division", "")
        out[t] = st
    return out


def _apply_result(states: dict[str, TeamSeasonState], home: str, away: str, hs: int, aws: int, *, divisional: bool):
    h, a = states[home], states[away]
    h.pts_for += hs
    h.pts_against += aws
    a.pts_for += aws
    a.pts_against += hs
    if hs > aws:
        h.wins += 1
        a.losses += 1
        if divisional:
            h.div_wins += 1
    elif aws > hs:
        a.wins += 1
        h.losses += 1
        if divisional:
            a.div_wins += 1
    else:
        h.ties += 1
        a.ties += 1


def _simulate_game(
    sport: str,
    home: str,
    away: str,
    *,
    year: int,
    neutral: bool,
    divisional: bool,
) -> tuple[int, int]:
    base = price_base_game(sport, home, away, season=year, neutral=neutral)
    if not base:
        return random.randint(17, 28), random.randint(17, 28)
    hl = float(base.get("home_lambda") or 24)
    al = float(base.get("away_lambda") or 24)
    use_negbin = str(sport).lower() == SPORT_NFL
    r = NFL_R_DISPERSION if use_negbin else CFB_R_DISPERSION
    if use_negbin:
        return sample_negbin(hl, r), sample_negbin(al, r)
    return sample_poisson(hl), sample_poisson(al)


def _win_pct(st: TeamSeasonState) -> float:
    g = st.wins + st.losses + st.ties
    if g <= 0:
        return 0.0
    return (st.wins + 0.5 * st.ties) / g


def _net_pts(st: TeamSeasonState) -> int:
    return st.pts_for - st.pts_against


def _seed_conference(states: dict[str, TeamSeasonState], conf: str) -> list[str]:
    teams = [t for t, s in states.items() if s.conf == conf]
    teams.sort(key=lambda t: (-_win_pct(states[t]), -_net_pts(states[t]), t))
    return teams


def _division_winners(states: dict[str, TeamSeasonState], conf: str) -> dict[str, str]:
    winners: dict[str, str] = {}
    divs = NFL_DIVISIONS.get(conf) or {}
    for div, members in divs.items():
        ranked = sorted(members, key=lambda t: (-_win_pct(states[t]), -_net_pts(states[t]), t))
        winners[div] = ranked[0]
    return winners


def _simulate_playoff_game(sport: str, home: str, away: str, *, year: int) -> str:
    hs, aws = _simulate_game(sport, home, away, year=year, neutral=False, divisional=False)
    if hs >= aws:
        return home
    return away


def _run_nfl_playoffs(states: dict[str, TeamSeasonState], sport: str, year: int) -> str:
    """Simplified 7-team bracket per conference → Super Bowl winner."""
    sb_teams: list[str] = []
    for conf in ("AFC", "NFC"):
        seeds = _seed_conference(states, conf)
        div_winners = set(_division_winners(states, conf).values())
        playoff = seeds[:7]
        if not playoff:
            continue
        # Reseed: division winners top 4 slots by record
        div_sorted = sorted([t for t in playoff if t in div_winners], key=lambda t: (-_win_pct(states[t]), -_net_pts(states[t])))
        wild = [t for t in playoff if t not in div_winners]
        bracket = div_sorted + wild
        while len(bracket) < 7:
            bracket.append(bracket[-1])

        seed1 = bracket[0]
        wc = bracket[1:7]
        # Wild card (3 games): 2v7, 3v6, 4v5 — higher seed hosts
        wc_pairs = [(wc[0], wc[5]), (wc[1], wc[4]), (wc[2], wc[3])]
        div_winners_round = [seed1]
        for higher, lower in wc_pairs:
            div_winners_round.append(_simulate_playoff_game(sport, higher, lower, year=year))

        # Divisional: reseed top vs bottom
        div_winners_round.sort(key=lambda t: (-_win_pct(states[t]), -_net_pts(states[t])))
        semi_pairs = [(div_winners_round[0], div_winners_round[3]), (div_winners_round[1], div_winners_round[2])]
        conf_finalists = [_simulate_playoff_game(sport, a, b, year=year) for a, b in semi_pairs]
        conf_champ = _simulate_playoff_game(sport, conf_finalists[0], conf_finalists[1], year=year)
        sb_teams.append(conf_champ)
        for t in playoff:
            if t not in (conf_champ, conf_finalists[0], conf_finalists[1]):
                states[t].stage = "wild_card" if t in wc else states[t].stage
        states[conf_finalists[0]].stage = "conference"
        states[conf_finalists[1]].stage = "conference"
        states[conf_champ].stage = "conference"

    if len(sb_teams) == 2:
        sb_winner = _simulate_playoff_game(sport, sb_teams[0], sb_teams[1], year=year)
        states[sb_winner].stage = "sb_win"
        loser = sb_teams[1] if sb_winner == sb_teams[0] else sb_teams[0]
        states[loser].stage = "sb_loss"
        return sb_winner
    return ""


def _finalize_standings(states: dict[str, TeamSeasonState], sport: str) -> None:
    if str(sport).lower() == SPORT_NFL:
        for conf in ("AFC", "NFC"):
            ranked = _seed_conference(states, conf)
            for i, t in enumerate(ranked, 1):
                states[t].conf_finish = i
            for div, members in (NFL_DIVISIONS.get(conf) or {}).items():
                div_rank = sorted(members, key=lambda t: (-_win_pct(states[t]), -_net_pts(states[t]), t))
                for i, t in enumerate(div_rank, 1):
                    states[t].div_finish = i
                if div_rank:
                    states[div_rank[0]].stage = states[div_rank[0]].stage if states[div_rank[0]].stage != "missed_playoffs" else "missed_playoffs"
        for conf in ("AFC", "NFC"):
            seeds = _seed_conference(states, conf)[:7]
            div_w = _division_winners(states, conf)
            for t in seeds:
                if states[t].stage == "missed_playoffs":
                    states[t].stage = "wild_card"
            for t in div_w.values():
                if states[t].stage in ("missed_playoffs", "wild_card"):
                    states[t].stage = "divisional"
    else:
        # CFB: bowl eligibility proxy at 6+ wins
        ranked = sorted(states.keys(), key=lambda t: (-states[t].wins, -_net_pts(states[t]), t))
        for i, t in enumerate(ranked, 1):
            states[t].conf_finish = i
            if states[t].wins >= 6:
                states[t].stage = "wild_card"


def run_season_futures(
    sport: str,
    year: int,
    *,
    n_sims: int = DEFAULT_SIMS,
) -> dict[str, Any]:
    schedule = load_season_schedule(sport, year)
    if not schedule:
        return {"error": "no_schedule", "sport": sport, "year": year}

    teams: set[str] = set()
    for g in schedule:
        teams.add(g["home"])
        teams.add(g["away"])

    is_nfl = str(sport).lower() == SPORT_NFL
    reg_games = 17 if is_nfl else 12

    records: list[dict[str, TeamSeasonState]] = []

    for _ in range(n_sims):
        states = _init_team_states(teams, sport)
        for g in schedule:
            home, away = g["home"], g["away"]
            if g.get("completed") and g.get("home_score") is not None and g.get("away_score") is not None:
                _apply_result(
                    states,
                    home,
                    away,
                    int(g["home_score"]),
                    int(g["away_score"]),
                    divisional=bool(g.get("divisional")),
                )
            else:
                hs, aws = _simulate_game(
                    sport,
                    home,
                    away,
                    year=year,
                    neutral=bool(g.get("neutral")),
                    divisional=bool(g.get("divisional")),
                )
                _apply_result(states, home, away, hs, aws, divisional=bool(g.get("divisional")))

        if is_nfl:
            _run_nfl_playoffs(states, sport, year)
        _finalize_standings(states, sport)
        records.append({t: states[t] for t in states})

    team_probs: dict[str, dict[str, Any]] = {}
    for t in teams:
        team_probs[t] = {
            "team": t,
            "sb_win": 0.0,
            "conf_winner": 0.0,
            "division_winner": 0.0,
            "make_playoffs": 0.0,
            "win_total": {},
        }

    win_dist: dict[str, list[int]] = {t: [] for t in teams}

    for rec in records:
        for t, st in rec.items():
            win_dist[t].append(st.wins)
            tp = team_probs[t]
            if st.stage == "sb_win":
                tp["sb_win"] += 1
            if st.stage in ("sb_win", "sb_loss", "conference"):
                tp["conf_winner"] += 1
            if st.div_finish == 1 and is_nfl:
                tp["division_winner"] += 1
            if st.stage != "missed_playoffs":
                tp["make_playoffs"] += 1

    markets: list[dict[str, Any]] = []
    for t, tp in team_probs.items():
        n = len(records)
        for key in ("sb_win", "conf_winner", "division_winner", "make_playoffs"):
            p = tp[key] / n
            tp[key] = round(p, 4)
            if p > 0.001 or key in ("make_playoffs", "division_winner"):
                markets.append(
                    {
                        "team": t,
                        "market": key.replace("_", " ").title(),
                        "prob": round(p, 4),
                        "price": implied_to_american(p),
                    }
                )

        wins = win_dist[t]
        mean_w = sum(wins) / len(wins)
        tp["mean_wins"] = round(mean_w, 2)
        for line in (reg_games - 0.5, mean_w + 0.5, mean_w - 0.5):
            if line < 0:
                continue
            over = sum(1 for w in wins if w > line) / len(wins)
            markets.append(
                {
                    "team": t,
                    "market": "Win Total",
                    "line": line,
                    "selection": "Over",
                    "prob": round(over, 4),
                    "price": implied_to_american(over),
                }
            )

    standings = sorted(
        team_probs.values(),
        key=lambda x: (-x.get("sb_win", 0), -x.get("mean_wins", 0)),
    )

    return {
        "sport": sport,
        "year": year,
        "n_sims": n_sims,
        "n_games": len(schedule),
        "n_teams": len(teams),
        "teams": standings,
        "markets": markets,
    }
