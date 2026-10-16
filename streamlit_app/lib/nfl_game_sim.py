"""NFL prop projection from a play-by-play simulation of the game.

Each game is simulated play by play. A situation model samples pass or run,
then the result of that play, and the clock and score carry forward. The
number on the desk is the median of those simulated games. A player who is
ruled out is set to zero.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import DATA_DIR

CACHE = DATA_DIR / "fastr_player_cache" / "nfl"
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.parquet"
N_SIMS = 400
MODEL_VERSION = 4
KEEP_DRAWS = 400
MIN_SNAPS = 10
LAST_GAMES = 10
HISTORY_FROM = 2016
_SNAP_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{year}.csv"
_SKILL = {"QB", "RB", "FB", "WR", "TE"}
_YARD_STATS = {
    "pass_yds", "pass_yds_q1", "pass_rush_yds", "rush_yds", "rec_yds", "rr_yds", "prd_yds",
    "pass_long", "rush_long", "rec_long",
}

STATS = (
    "pass_yds",
    "pass_yds_q1",
    "pass_tds",
    "pass_attempts",
    "pass_completions",
    "pass_ints",
    "pass_long",
    "pass_rush_yds",
    "rush_yds",
    "rush_attempts",
    "rush_tds",
    "rush_long",
    "rec_yds",
    "receptions",
    "rec_tds",
    "rec_long",
    "rr_yds",
    "rr_tds",
    "prd_yds",
    "prd_tds",
    "tds",
    "first_td",
    "last_td",
    "fantasy",
)

_ABBR = {
    "GNB": "GB", "NOR": "NO", "NWE": "NE", "TAM": "TB", "KAN": "KC",
    "SFO": "SF", "JAC": "JAX", "LVR": "LV", "WSH": "WAS", "LA": "LAR",
}


def norm_name(value: str) -> str:
    text = str(value or "").lower().replace(".", " ").replace("'", "").replace("’", "")
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", text)
    return " ".join(re.sub(r"[^a-z\s]", " ", text).split())


def abbr(code: str) -> str:
    text = str(code or "").strip().upper()
    return _ABBR.get(text, text)


def team_abbr(name: str) -> str:
    from .nfl_team_registry import NFL_TEAMS, team_key

    key = team_key(name)
    for canonical, code, _aliases in NFL_TEAMS:
        if team_key(canonical) == key:
            return abbr(code)
    return abbr(name)


def _download(url: str, path: Path) -> bool:
    if path.exists() and path.stat().st_size > 1000:
        return True
    try:
        import requests

        response = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception:
        return path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return True


def _pbp(season: int) -> pd.DataFrame:
    path = CACHE / f"pbp_{season}.parquet"
    if not _download(PBP_URL.format(season=season), path):
        return pd.DataFrame()
    columns = [
        "season", "week", "posteam", "play_type", "pass_attempt", "rush_attempt",
        "complete_pass", "interception", "sack", "passing_yards", "rushing_yards",
        "receiving_yards", "pass_touchdown", "rush_touchdown", "yards_gained",
        "passer_player_id", "rusher_player_id", "receiver_player_id",
        "score_differential", "qb_kneel", "qb_spike",
    ]
    try:
        frame = pd.read_parquet(path, columns=columns)
    except Exception:
        frame = pd.read_parquet(path)
    return frame


def _names() -> dict[str, str]:
    path = CACHE / "players.parquet"
    if not _download(PLAYERS_URL, path):
        return {}
    frame = pd.read_parquet(path)
    id_col = "gsis_id" if "gsis_id" in frame.columns else "player_id"
    name_col = "display_name" if "display_name" in frame.columns else "football_name"
    if id_col not in frame.columns or name_col not in frame.columns:
        return {}
    out = {}
    for pid, name in zip(frame[id_col].astype(str), frame[name_col].astype(str)):
        if pid and pid != "None" and name and name != "None":
            out[pid] = name
    return out


def _player_weeks(pbp: pd.DataFrame) -> pd.DataFrame:
    plays = pbp.copy()
    for col in ("pass_attempt", "rush_attempt", "complete_pass", "interception", "sack", "pass_touchdown", "rush_touchdown", "qb_kneel", "qb_spike"):
        if col not in plays.columns:
            plays[col] = 0
        plays[col] = pd.to_numeric(plays[col], errors="coerce").fillna(0)
    for col in ("passing_yards", "rushing_yards", "receiving_yards", "yards_gained", "score_differential"):
        if col not in plays.columns:
            plays[col] = 0
        plays[col] = pd.to_numeric(plays[col], errors="coerce").fillna(0)
    plays = plays[plays["posteam"].notna()]
    plays = plays[(plays["pass_attempt"] == 1) | ((plays["rush_attempt"] == 1) & (plays["qb_kneel"] == 0) & (plays["qb_spike"] == 0))]

    rows: list[pd.DataFrame] = []
    passes = plays[plays["pass_attempt"] == 1]
    if not passes.empty:
        grouped = passes.groupby(["season", "week", "posteam", "passer_player_id"], dropna=True)
        rows.append(grouped.agg(
            pass_att=("pass_attempt", "sum"),
            pass_cmp=("complete_pass", "sum"),
            pass_yds=("passing_yards", "sum"),
            pass_tds=("pass_touchdown", "sum"),
            pass_ints=("interception", "sum"),
            sacks=("sack", "sum"),
        ).reset_index().rename(columns={"passer_player_id": "player_id"}))
    rushes = plays[plays["rush_attempt"] == 1]
    if not rushes.empty:
        grouped = rushes.groupby(["season", "week", "posteam", "rusher_player_id"], dropna=True)
        rows.append(grouped.agg(
            rush_att=("rush_attempt", "sum"),
            rush_yds=("rushing_yards", "sum"),
            rush_tds=("rush_touchdown", "sum"),
        ).reset_index().rename(columns={"rusher_player_id": "player_id"}))
    caught = passes[passes["receiver_player_id"].notna() & (passes["sack"] == 0)]
    if not caught.empty:
        grouped = caught.groupby(["season", "week", "posteam", "receiver_player_id"], dropna=True)
        rows.append(grouped.agg(
            targets=("pass_attempt", "sum"),
            receptions=("complete_pass", "sum"),
            rec_yds=("receiving_yards", "sum"),
            rec_tds=("pass_touchdown", "sum"),
        ).reset_index().rename(columns={"receiver_player_id": "player_id"}))
    if not rows:
        return pd.DataFrame()
    out = rows[0]
    for extra in rows[1:]:
        out = out.merge(extra, on=["season", "week", "posteam", "player_id"], how="outer")
    for col in ("pass_att", "pass_cmp", "pass_yds", "pass_tds", "pass_ints", "sacks", "rush_att", "rush_yds", "rush_tds", "targets", "receptions", "rec_yds", "rec_tds"):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["player_id"] = out["player_id"].astype(str)
    out["posteam"] = out["posteam"].map(abbr)
    return out


def _share(counts: np.ndarray, team: np.ndarray) -> float:
    """Fraction of the team's plays over the window, shrunk a little when the sample is short."""
    num = float(np.sum(counts))
    den = float(np.sum(team))
    if den <= 0:
        return 0.02
    return float(np.clip((num + 0.4) / (den + 10.0), 0.0, 0.98))


def _kalman(values: np.ndarray, weights: np.ndarray, *, start: float, q: float, r_scale: float) -> tuple[float, float]:
    """One rating. Weight is how many plays that game was worth."""
    x = float(start)
    p = 0.25
    for value, weight in zip(values, weights):
        if not np.isfinite(value) or weight <= 0:
            p += q
            continue
        p += q
        r = r_scale / max(float(weight), 1.0)
        gain = p / (p + r)
        x = x + gain * (float(value) - x)
        p = (1.0 - gain) * p
    return x, p


def _ratings(weeks: pd.DataFrame, names: dict[str, str]) -> dict[str, dict[str, Any]]:
    """player id -> current shares, efficiencies, team, and chance he plays."""
    if weeks.empty:
        return {}
    team_pass = weeks.groupby(["season", "week", "posteam"])["pass_att"].sum()
    team_rush = weeks.groupby(["season", "week", "posteam"])["rush_att"].sum()
    calendar: dict[str, list[tuple[int, int]]] = {}
    for season, week, team in team_pass.index:
        calendar.setdefault(str(team), []).append((int(season), int(week)))
    for games in calendar.values():
        games.sort()
    league_ypa = float(weeks["pass_yds"].sum() / max(weeks["pass_att"].sum(), 1))
    league_cmp = float(weeks["pass_cmp"].sum() / max(weeks["pass_att"].sum(), 1))
    league_ypc = float(weeks["rush_yds"].sum() / max(weeks["rush_att"].sum(), 1))
    league_ypt = float(weeks["rec_yds"].sum() / max(weeks["targets"].sum(), 1))
    league_catch = float(weeks["receptions"].sum() / max(weeks["targets"].sum(), 1))
    players: dict[str, dict[str, Any]] = {}
    ordered = weeks.sort_values(["season", "week"])
    for pid, group in ordered.groupby("player_id"):
        group = group.sort_values(["season", "week"])
        team = str(group["posteam"].iloc[-1])
        own = {
            (int(row.season), int(row.week)): row
            for row in group.itertuples(index=False)
            if str(row.posteam) == team
        }
        # Weeks before he joined this team are not games he sat. Zeros start
        # at his first snap, so a new starter is not treated as a nine-week backup.
        slots = [slot for slot in calendar.get(team, []) if not own or slot >= min(own)][-6:]
        pass_att_cal = []
        rush_att_cal = []
        targets_cal = []
        pass_yds_cal = []
        rush_yds_cal = []
        rec_yds_cal = []
        team_p_cal = []
        team_r_cal = []
        for season, week in slots:
            row = own.get((season, week))
            if row is None:
                # A missed game is a zero share of that offense. It is not a
                # zero in his yards-per-play rate, because the rate only uses
                # games where he had the chances.
                pass_att_cal.append(0.0)
                rush_att_cal.append(0.0)
                targets_cal.append(0.0)
                pass_yds_cal.append(0.0)
                rush_yds_cal.append(0.0)
                rec_yds_cal.append(0.0)
                team_p_cal.append(float(team_pass.get((season, week, team), 0) or 0))
                team_r_cal.append(float(team_rush.get((season, week, team), 0) or 0))
                continue
            pass_att_cal.append(float(row.pass_att))
            rush_att_cal.append(float(row.rush_att))
            targets_cal.append(float(row.targets))
            pass_yds_cal.append(float(row.pass_yds))
            rush_yds_cal.append(float(row.rush_yds))
            rec_yds_cal.append(float(row.rec_yds))
            team_p_cal.append(float(team_pass.get((season, week, team), 0) or 0))
            team_r_cal.append(float(team_rush.get((season, week, team), 0) or 0))
        pass_att_cal = np.array(pass_att_cal)
        rush_att_cal = np.array(rush_att_cal)
        targets_cal = np.array(targets_cal)
        pass_yds_cal = np.array(pass_yds_cal)
        rush_yds_cal = np.array(rush_yds_cal)
        rec_yds_cal = np.array(rec_yds_cal)
        team_p = np.array(team_p_cal)
        team_r = np.array(team_r_cal)
        # Recent games count more. A role he held last year should not
        # drown the share he has had this season.
        if len(pass_att_cal):
            age = np.arange(len(pass_att_cal), dtype=float)[::-1]
            weight = np.exp(-age / 4.0)
            weight *= len(weight) / weight.sum()
            pass_share = _share(pass_att_cal * weight, team_p * weight)
            rush_share = _share(rush_att_cal * weight, team_r * weight)
            tgt_share = _share(targets_cal * weight, team_p * weight)
        else:
            pass_share = rush_share = tgt_share = 0.02
        pass_att = group["pass_att"].to_numpy(dtype=float)
        rush_att = group["rush_att"].to_numpy(dtype=float)
        targets = group["targets"].to_numpy(dtype=float)
        ypa, _ = _kalman(np.divide(group["pass_yds"], np.maximum(pass_att, 1)), pass_att, start=league_ypa, q=0.04, r_scale=40)
        cmp, _ = _kalman(np.divide(group["pass_cmp"], np.maximum(pass_att, 1)), pass_att, start=league_cmp, q=0.002, r_scale=12)
        ptd, _ = _kalman(np.divide(group["pass_tds"], np.maximum(pass_att, 1)), pass_att, start=0.045, q=0.0004, r_scale=4)
        intr, _ = _kalman(np.divide(group["pass_ints"], np.maximum(pass_att, 1)), pass_att, start=0.022, q=0.0002, r_scale=3)
        ypc, _ = _kalman(np.divide(group["rush_yds"], np.maximum(rush_att, 1)), rush_att, start=league_ypc, q=0.03, r_scale=8)
        rtd, _ = _kalman(np.divide(group["rush_tds"], np.maximum(rush_att, 1)), rush_att, start=0.03, q=0.0003, r_scale=2)
        catch, _ = _kalman(np.divide(group["receptions"], np.maximum(targets, 1)), targets, start=league_catch, q=0.002, r_scale=6)
        ypt, _ = _kalman(np.divide(group["rec_yds"], np.maximum(targets, 1)), targets, start=league_ypt, q=0.05, r_scale=12)
        ctd, _ = _kalman(np.divide(group["rec_tds"], np.maximum(targets, 1)), targets, start=0.04, q=0.0004, r_scale=2)
        if len(pass_att_cal):
            def _recent(num: np.ndarray, den: np.ndarray, fallback: float, minimum: float) -> float:
                denom = float(np.sum(den * weight))
                if denom < minimum:
                    return fallback
                return float(np.sum(num * weight) / denom)

            ypa = 0.6 * _recent(pass_yds_cal, pass_att_cal, ypa, 40) + 0.4 * ypa
            ypc = 0.6 * _recent(rush_yds_cal, rush_att_cal, ypc, 15) + 0.4 * ypc
            ypt = 0.6 * _recent(rec_yds_cal, targets_cal, ypt, 12) + 0.4 * ypt
            active_qb = pass_att_cal >= 8
            pass_when_in = _share(pass_att_cal[active_qb], team_p[active_qb]) if active_qb.any() else pass_share
        else:
            pass_when_in = pass_share
        played_game = (pass_att_cal >= 5) | (rush_att_cal >= 3) | (targets_cal >= 2)
        recent = played_game[-4:] if len(played_game) else np.array([])
        played = float(np.mean(recent)) if len(recent) else 0.0
        if len(pass_att_cal) and (
            pass_att_cal[-1] >= 15 or rush_att_cal[-1] >= 8 or targets_cal[-1] >= 4
        ):
            played = max(played, 0.92)
        elif len(pass_att_cal) and pass_att_cal[-1] < 1 and rush_att_cal[-1] < 1 and targets_cal[-1] < 1:
            played = min(played, 0.35)
        name = names.get(str(pid)) or ""
        if not name:
            continue
        players[str(pid)] = {
            "name": name,
            "team": team,
            "play": float(np.clip(played, 0.02, 0.98)),
            "pass_share": float(np.clip(pass_share, 0, 1)),
            "pass_when_in": float(np.clip(pass_when_in, 0, 1)),
            "rush_share": float(np.clip(rush_share, 0, 1)),
            "target_share": float(np.clip(tgt_share, 0, 1)),
            "ypa": float(np.clip(ypa, 3, 12)),
            "cmp": float(np.clip(cmp, 0.4, 0.8)),
            "ptd": float(np.clip(ptd, 0.005, 0.12)),
            "int": float(np.clip(intr, 0.005, 0.08)),
            "ypc": float(np.clip(ypc, 2, 8)),
            "rtd": float(np.clip(rtd, 0.005, 0.12)),
            "catch": float(np.clip(catch, 0.4, 0.85)),
            "ypt": float(np.clip(ypt, 4, 16)),
            "ctd": float(np.clip(ctd, 0.005, 0.15)),
        }
    return players


def _team_env(weeks: pd.DataFrame) -> dict[str, dict[str, float]]:
    if weeks.empty:
        return {}
    league_plays = float((weeks.groupby(["season", "week", "posteam"])[["pass_att", "rush_att"]].sum().sum(axis=1)).mean())
    league_pass = float(weeks["pass_att"].sum() / max(weeks["pass_att"].sum() + weeks["rush_att"].sum(), 1))
    env = {}
    for team, group in weeks.groupby("posteam"):
        plays = group.groupby(["season", "week"])[["pass_att", "rush_att"]].sum()
        per_game = (plays["pass_att"] + plays["rush_att"]).to_numpy(dtype=float)
        n = max(len(per_game), 1)
        pace = (float(per_game.mean()) * n + league_plays * 6) / (n + 6)
        pass_rate = (float(group["pass_att"].sum()) + league_pass * 80) / (float(group["pass_att"].sum() + group["rush_att"].sum()) + 80)
        env[str(team)] = {"plays": float(np.clip(pace, 48, 78)), "pass_rate": float(np.clip(pass_rate, 0.42, 0.68))}
    return env


def _apply_injuries(players: dict[str, dict[str, Any]]) -> None:
    path = DATA_DIR / "espn_cache" / "injuries_nfl.json"
    if not path.exists():
        return
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    except (OSError, json.JSONDecodeError):
        return
    listed: dict[tuple[str, str], float] = {}
    out = {}
    for row in rows:
        raw = row.get("availability")
        if raw is None:
            continue
        availability = float(raw)
        key = (norm_name(str(row.get("name") or "")), abbr(team_abbr(str(row.get("team") or ""))))
        if availability <= 0.25:
            out[key] = True
        else:
            listed[key] = availability
    for player in players.values():
        key = (norm_name(player["name"]), player["team"])
        if out.get(key):
            player["play"] = 0.0
            player["out"] = True
        elif key in listed:
            player["play"] = float(np.clip(listed[key], 0.0, 0.98))


def fit(year: int, week: int) -> dict[str, Any]:
    """Ratings through the week before `week`."""
    frames = []
    for season in (year - 1, year):
        pbp = _pbp(season)
        if pbp.empty:
            continue
        pbp["season"] = pd.to_numeric(pbp["season"], errors="coerce")
        pbp["week"] = pd.to_numeric(pbp["week"], errors="coerce")
        if season == year:
            pbp = pbp[pbp["week"] < int(week)]
        if not pbp.empty:
            frames.append(_player_weeks(pbp))
    weeks = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    players = _ratings(weeks, _names())
    _apply_injuries(players)
    return {"players": players, "teams": _team_env(weeks), "year": year, "week": week}


def _use_shares(roster: list[dict[str, Any]], key: str, n: int, rng: np.random.Generator) -> np.ndarray:
    """Each player keeps his own share of the offense.

    Shares are not scaled up to 100%. The carries of someone who merely might
    sit stay his. Only a player ruled out hands his share to teammates who
    still have a role, in proportion to the share they already had.
    """
    raw = np.array([float(player[key]) for player in roster], dtype=float)
    out = np.array([bool(player.get("out")) for player in roster])
    ghost = np.array([player.get("name") == "__other__" for player in roster])
    eligible = ~out & ~ghost & (raw > 0)
    if out.any() and eligible.any() and float(raw[eligible].sum()) > 1e-6:
        vacated = float(raw[out].sum())
        raw = raw.copy()
        raw[out] = 0.0
        raw[eligible] = raw[eligible] + vacated * raw[eligible] / float(raw[eligible].sum())
    play = np.array([max(float(player["play"]), 0.0) for player in roster])
    active = rng.random((len(roster), n)) < play[:, None]
    return raw[:, None] * active


def _gamma(rng: np.random.Generator, count: np.ndarray, mean: float) -> np.ndarray:
    scale = max(float(mean), 0.5)
    draws = rng.gamma(np.maximum(count, 1e-3), scale)
    return np.where(count >= 0.05, draws, 0.0)


def _longest(rng: np.random.Generator, count: np.ndarray, mean: float) -> np.ndarray:
    """Rough longest play: the top of `count` exponential gains."""
    n = np.maximum(count, 0)
    base = max(float(mean), 1.0) * np.log1p(n)
    noise = rng.lognormal(0, 0.22, size=n.shape)
    return np.where(n >= 1, np.clip(base * noise, 0, 80), 0.0)


def _simulate_side(
    rng: np.random.Generator,
    roster: list[dict[str, Any]],
    *,
    plays: np.ndarray,
    pass_rate: np.ndarray,
    margin: float,
) -> dict[str, dict[str, np.ndarray]]:
    n = len(plays)
    rate = np.clip(pass_rate - 0.006 * margin + rng.normal(0, 0.025, n), 0.38, 0.72)
    n_pass = rng.binomial(np.maximum(plays.astype(int), 1), rate).astype(float)
    n_rush = np.maximum(plays - n_pass, 0)
    if not roster:
        return {}
    pass_share = _use_shares(roster, "pass_share", n, rng)
    rush_share = _use_shares(roster, "rush_share", n, rng)
    tgt_share = _use_shares(roster, "target_share", n, rng)
    out: dict[str, dict[str, np.ndarray]] = {}
    for i, player in enumerate(roster):
        att = n_pass * pass_share[i]
        rushes = n_rush * rush_share[i]
        targets = n_pass * 0.93 * tgt_share[i]
        comps = rng.binomial(np.maximum(att.round().astype(int), 0), player["cmp"]).astype(float)
        receptions = rng.binomial(np.maximum(targets.round().astype(int), 0), player["catch"]).astype(float)
        pass_yds = _gamma(rng, comps, player["ypa"] / max(player["cmp"], 0.5))
        rush_yds = _gamma(rng, rushes, player["ypc"])
        rec_yds = _gamma(rng, receptions, player["ypt"] / max(player["catch"], 0.5))
        pass_tds = rng.poisson(np.maximum(att, 0) * player["ptd"]).astype(float)
        rush_tds = rng.poisson(np.maximum(rushes, 0) * player["rtd"]).astype(float)
        rec_tds = rng.poisson(np.maximum(targets, 0) * player["ctd"]).astype(float)
        ints = rng.binomial(np.maximum(att.round().astype(int), 0), player["int"]).astype(float)
        out[player["name"]] = {
            "pass_yds": pass_yds,
            "pass_attempts": att,
            "pass_completions": comps,
            "pass_tds": pass_tds,
            "pass_ints": ints,
            "pass_long": _longest(rng, comps, player["ypa"] / max(player["cmp"], 0.5)),
            "rush_yds": rush_yds,
            "rush_attempts": rushes,
            "rush_tds": rush_tds,
            "rush_long": _longest(rng, rushes, player["ypc"]),
            "rec_yds": rec_yds,
            "receptions": receptions,
            "rec_tds": rec_tds,
            "rec_long": _longest(rng, receptions, player["ypt"] / max(player["catch"], 0.5)),
            "score_tds": rush_tds + rec_tds,
        }
    return out


def _finish(box: dict[str, dict[str, np.ndarray]], n: int, rng: np.random.Generator, n_plays: np.ndarray) -> dict[str, dict[str, np.ndarray]]:
    earliest = {}
    latest = {}
    for name, row in box.items():
        scores = row["score_tds"]
        unit = rng.random(n)
        earliest[name] = np.where(scores > 0, n_plays * (unit ** (1.0 / np.maximum(scores, 1))), np.inf)
        latest[name] = np.where(scores > 0, n_plays * (1.0 - (1.0 - unit) ** (1.0 / np.maximum(scores, 1))), -np.inf)
    names = list(earliest)
    if names:
        first_mat = np.vstack([earliest[name] for name in names])
        last_mat = np.vstack([latest[name] for name in names])
        finite = np.isfinite(first_mat).any(axis=0)
        first_idx = np.argmin(np.where(np.isfinite(first_mat), first_mat, 1e18), axis=0)
        last_idx = np.argmax(np.where(np.isfinite(last_mat), last_mat, -1e18), axis=0)
    else:
        finite, first_idx, last_idx = None, None, None
    finished = {}
    q1 = rng.beta(8, 24, n)
    for i, name in enumerate(box):
        row = box[name]
        pass_yds = row["pass_yds"]
        rush_yds = row["rush_yds"]
        rec_yds = row["rec_yds"]
        score = row["score_tds"]
        fantasy = (
            pass_yds / 25.0 + row["pass_tds"] * 4.0 - row["pass_ints"]
            + rush_yds / 10.0 + row["rush_tds"] * 6.0
            + rec_yds / 10.0 + row["receptions"] + row["rec_tds"] * 6.0
        )
        finished[name] = {
            "pass_yds": pass_yds,
            "pass_yds_q1": pass_yds * q1,
            "pass_tds": row["pass_tds"],
            "pass_attempts": row["pass_attempts"],
            "pass_completions": row["pass_completions"],
            "pass_ints": row["pass_ints"],
            "pass_long": row["pass_long"],
            "pass_rush_yds": pass_yds + rush_yds,
            "rush_yds": rush_yds,
            "rush_attempts": row["rush_attempts"],
            "rush_tds": row["rush_tds"],
            "rush_long": row["rush_long"],
            "rec_yds": rec_yds,
            "receptions": row["receptions"],
            "rec_tds": row["rec_tds"],
            "rec_long": row["rec_long"],
            "rr_yds": rush_yds + rec_yds,
            "rr_tds": row["rush_tds"] + row["rec_tds"],
            "prd_yds": pass_yds + rush_yds + rec_yds,
            "prd_tds": row["pass_tds"] + row["rush_tds"] + row["rec_tds"],
            "tds": (score >= 1).astype(float),
            "first_td": np.zeros(n),
            "last_td": np.zeros(n),
            "fantasy": fantasy,
        }
    if names and finite is not None:
        for i, name in enumerate(names):
            finished[name]["first_td"] = ((first_idx == i) & finite).astype(float)
            finished[name]["last_td"] = ((last_idx == i) & finite).astype(float)
    return finished


def simulate_game(
    model: dict[str, Any],
    home: str,
    away: str,
    *,
    spread: float = 0.0,
    n_sims: int = N_SIMS,
    seed: int = 0,
) -> dict[str, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    home_abbr, away_abbr = abbr(home), abbr(away)
    by_team: dict[str, list[dict[str, Any]]] = {}
    for player in model["players"].values():
        if player["play"] <= 0 and player["pass_share"] < 0.05 and player["rush_share"] < 0.05 and player["target_share"] < 0.05:
            continue
        by_team.setdefault(player["team"], []).append(dict(player))
    for roster in by_team.values():
        ghost = {
            "name": "__other__",
            "play": 1.0,
            "pass_share": max(0.0, 1.0 - sum(player["pass_share"] for player in roster)),
            "rush_share": max(0.0, 1.0 - sum(player["rush_share"] for player in roster)),
            "target_share": max(0.0, 1.0 - sum(player["target_share"] for player in roster)),
            "ypa": 7.0, "cmp": 0.64, "ptd": 0.045, "int": 0.022,
            "ypc": 4.3, "rtd": 0.025, "catch": 0.65, "ypt": 7.5, "ctd": 0.035,
        }
        if ghost["pass_share"] + ghost["rush_share"] + ghost["target_share"] > 0.02:
            roster.append(ghost)
    env = model["teams"]
    home_env = env.get(home_abbr) or {"plays": 62.0, "pass_rate": 0.56}
    away_env = env.get(away_abbr) or {"plays": 62.0, "pass_rate": 0.56}
    # Home spread is the home team's line. Negative means the home team is favored.
    margin = -float(spread)
    box: dict[str, dict[str, np.ndarray]] = {}
    n_plays = np.zeros(n_sims)
    for side, side_env, side_margin in (
        (home_abbr, home_env, margin),
        (away_abbr, away_env, -margin),
    ):
        plays = np.clip(rng.normal(side_env["plays"], 5.5, n_sims), 45, 85)
        n_plays = np.maximum(n_plays, plays)
        side_box = _simulate_side(
            rng,
            by_team.get(side) or [],
            plays=plays,
            pass_rate=np.full(n_sims, side_env["pass_rate"]),
            margin=side_margin,
        )
        box.update(side_box)
    return _finish(box, n_sims, rng, n_plays)


def _cache_path(year: int, week: int) -> Path:
    return CACHE / f"sim_{year}_w{week}.npz"


def _meta_path(year: int, week: int) -> Path:
    return CACHE / f"sim_{year}_w{week}.json"


def _spread(stat: str, center: float, sample: np.ndarray) -> float:
    """Width of the distribution. The sample's own spread, with a floor so one quiet streak is not a certainty."""
    sd = float(np.std(sample, ddof=1)) if len(sample) > 1 else 0.0
    if not np.isfinite(sd):
        sd = 0.0
    if stat in _YARD_STATS:
        floor = max(8.0, 0.18 * max(center, 1.0))
    elif stat in {"pass_attempts", "pass_completions", "rush_attempts", "receptions"}:
        floor = max(1.2, 0.22 * max(center, 1.0))
    else:
        floor = max(0.4, 0.35 * max(center, 0.5))
    return float(max(sd, floor))


def _snap_table(seasons: range) -> pd.DataFrame:
    """offensive snaps joined to the nflverse player id."""
    players_path = CACHE / "players.parquet"
    if not players_path.exists():
        return pd.DataFrame(columns=["gsis_id", "season", "week", "snaps"])
    ids = pd.read_parquet(players_path, columns=["gsis_id", "pfr_id"])
    ids = ids.dropna(subset=["gsis_id", "pfr_id"])
    ids["pfr_id"] = ids["pfr_id"].astype(str).str.upper()
    ids = ids.drop_duplicates("pfr_id")
    frames = []
    snap_dir = DATA_DIR / "espn_cache"
    snap_dir.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        path = snap_dir / f"snap_counts_{season}.csv"
        if not path.exists() or path.stat().st_size < 1000:
            try:
                import requests

                response = requests.get(_SNAP_URL.format(year=season), timeout=90, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                path.write_bytes(response.content)
            except Exception:
                if not path.exists():
                    continue
        try:
            snaps = pd.read_csv(path, usecols=["season", "game_type", "week", "pfr_player_id", "offense_snaps"])
        except Exception:
            continue
        snaps = snaps[snaps["game_type"].astype(str).str.upper() != "PRE"]
        snaps["pfr_id"] = snaps["pfr_player_id"].astype(str).str.upper()
        snaps["snaps"] = pd.to_numeric(snaps["offense_snaps"], errors="coerce").fillna(0)
        snaps["season"] = pd.to_numeric(snaps["season"], errors="coerce")
        snaps["week"] = pd.to_numeric(snaps["week"], errors="coerce")
        snaps = snaps.dropna(subset=["season", "week"])
        snaps = snaps.groupby(["pfr_id", "season", "week"], as_index=False)["snaps"].max()
        frames.append(snaps)
    if not frames:
        return pd.DataFrame(columns=["gsis_id", "season", "week", "snaps"])
    snaps = pd.concat(frames, ignore_index=True)
    joined = snaps.merge(ids, on="pfr_id", how="inner")
    return joined[["gsis_id", "season", "week", "snaps"]]


def _ensure_player_week(season: int, through_week: int) -> None:
    """Refresh the current season when the cached file is behind the weeks already played."""
    path = CACHE / f"player_week_{season}.parquet"
    if path.exists() and season < 2026:
        return
    have = 0
    if path.exists():
        try:
            have = int(pd.to_numeric(pd.read_parquet(path, columns=["week"])["week"], errors="coerce").max() or 0)
        except Exception:
            have = 0
    if have >= int(through_week):
        return
    url = (
        "https://github.com/nflverse/nflverse-data/releases/download/stats_player/"
        f"stats_player_week_{season}.parquet"
    )
    try:
        import requests

        response = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        fresh = pd.read_parquet(io_bytes(response.content))
    except Exception:
        return
    if fresh.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh.to_parquet(path, index=False)


def io_bytes(payload: bytes):
    import io

    return io.BytesIO(payload)


def _player_games(year: int, week: int) -> pd.DataFrame:
    """One row per player-game he actually played, before this week."""
    _ensure_player_week(int(year), max(int(week) - 1, 1))
    columns = [
        "player_id", "player_display_name", "position", "season", "week", "season_type", "team",
        "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
        "carries", "rushing_yards", "rushing_tds", "receptions", "receiving_yards", "receiving_tds",
    ]
    frames = []
    for season in range(HISTORY_FROM, int(year) + 1):
        path = CACHE / f"player_week_{season}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path, columns=columns)
        if "season_type" in frame.columns:
            frame = frame[frame["season_type"].isin(["REG", "POST"])]
        frame["season"] = pd.to_numeric(frame["season"], errors="coerce")
        frame["week"] = pd.to_numeric(frame["week"], errors="coerce")
        if season == int(year):
            frame = frame[frame["week"] < int(week)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    games = pd.concat(frames, ignore_index=True)
    games = games[games["position"].isin(_SKILL)]
    snaps = _snap_table(range(HISTORY_FROM, int(year) + 1))
    games = games.merge(
        snaps,
        left_on=["player_id", "season", "week"],
        right_on=["gsis_id", "season", "week"],
        how="inner",
    )
    games = games[games["snaps"] >= MIN_SNAPS]
    for col in (
        "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
        "carries", "rushing_yards", "rushing_tds", "receptions", "receiving_yards", "receiving_tds",
    ):
        games[col] = pd.to_numeric(games[col], errors="coerce").fillna(0.0)
    return games


def _stat_frame(games: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "player_id": games["player_id"].astype(str),
        "name": games["player_display_name"].astype(str),
        "team": games["team"].map(abbr),
        "season": games["season"].astype(int),
        "week": games["week"].astype(int),
        "pass_yds": games["passing_yards"],
        "pass_tds": games["passing_tds"],
        "pass_attempts": games["attempts"],
        "pass_completions": games["completions"],
        "pass_ints": games["passing_interceptions"],
        "pass_yds_q1": games["passing_yards"] * 0.24,
        "rush_yds": games["rushing_yards"],
        "rush_attempts": games["carries"],
        "rush_tds": games["rushing_tds"],
        "rec_yds": games["receiving_yards"],
        "receptions": games["receptions"],
        "rec_tds": games["receiving_tds"],
    })
    out["pass_rush_yds"] = out["pass_yds"] + out["rush_yds"]
    out["rr_yds"] = out["rush_yds"] + out["rec_yds"]
    out["prd_yds"] = out["pass_yds"] + out["rush_yds"] + out["rec_yds"]
    out["prd_tds"] = out["pass_tds"] + out["rush_tds"] + out["rec_tds"]
    return out


def player_centers(year: int, week: int) -> dict[str, dict[str, Any]]:
    """name -> {team, play, out, stats: {stat: (median, sd)}} from his own last 10 snap games."""
    games = _player_games(year, week)
    if games.empty:
        return {}
    table = _stat_frame(games).sort_values(["season", "week"])
    stat_cols = [col for col in table.columns if col not in {"player_id", "name", "team", "season", "week"}]
    centers: dict[str, dict[str, Any]] = {}
    for pid, group in table.groupby("player_id", sort=False):
        recent = group.tail(LAST_GAMES)
        name = str(recent["name"].iloc[-1] or "")
        if not name or name == "None":
            continue
        stats = {}
        for stat in stat_cols:
            values = recent[stat].to_numpy(dtype=float)
            center = float(np.median(values))
            stats[stat] = (center, _spread(stat, center, values))
        centers[str(pid)] = {
            "name": name,
            "team": str(recent["team"].iloc[-1]),
            "play": 1.0,
            "out": False,
            "stats": stats,
        }
    _zero_out(centers)
    return centers


def _zero_out(players: dict[str, dict[str, Any]]) -> None:
    path = DATA_DIR / "espn_cache" / "injuries_nfl.json"
    if not path.exists():
        return
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    except (OSError, json.JSONDecodeError):
        return
    out = set()
    for row in rows:
        raw = row.get("availability")
        availability = 1.0 if raw is None else float(raw)
        if availability > 0.25:
            continue
        out.add((norm_name(str(row.get("name") or "")), abbr(team_abbr(str(row.get("team") or "")))))
    for player in players.values():
        if (norm_name(player["name"]), player["team"]) in out:
            player["play"] = 0.0
            player["out"] = True
            player["stats"] = {stat: (0.0, 0.0) for stat in player["stats"]}


def simulate_week(year: int, week: int, *, n_sims: int = N_SIMS, refresh: bool = False) -> dict[str, Any]:
    """Cache one week of play-by-play simulations."""
    meta_path = _meta_path(year, week)
    data_path = _cache_path(year, week)
    if meta_path.exists() and data_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if int(meta.get("n_sims") or 0) == int(n_sims) and int(meta.get("model") or 0) == MODEL_VERSION:
            draws = np.load(data_path)["draws"]
            return {"meta": meta, "draws": draws}

    from .nfl_play_engine import project_week

    index, draws = project_week(year, week, n_sims=int(n_sims), n_keep=KEEP_DRAWS)
    meta = {"year": year, "week": week, "n_sims": int(n_sims), "model": MODEL_VERSION, "rows": index}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(data_path, draws=draws)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return {"meta": meta, "draws": draws}


def _on_team(model: dict[str, Any], name: str, team: str) -> bool:
    want = norm_name(name)
    for player in model["players"].values():
        if norm_name(player["name"]) == want:
            return player["team"] == team
    return False


def _play_of(model: dict[str, Any], name: str) -> float:
    want = norm_name(name)
    for player in model["players"].values():
        if norm_name(player["name"]) == want:
            return float(player["play"])
    return 0.5


def _fallback_games(model: dict[str, Any]) -> list[tuple[str, str]]:
    teams = sorted({player["team"] for player in model["players"].values()})
    return [(teams[i], teams[i + 1]) for i in range(0, len(teams) - 1, 2)]


def lookup_row(bundle: dict[str, Any], player: str, stat: str) -> tuple[dict[str, Any] | None, np.ndarray | None]:
    want = norm_name(player)
    index = bundle.get("_index")
    if index is None:
        index = {(row["name"], row["stat"]): i for i, row in enumerate(bundle["meta"]["rows"])}
        bundle["_index"] = index
    i = index.get((want, stat))
    if i is None:
        return None, None
    return bundle["meta"]["rows"][i], bundle["draws"][i]


def over_prob(draws: np.ndarray, line: float, *, binary: bool = False) -> float:
    if draws is None or len(draws) == 0:
        return float("nan")
    if binary:
        return float(np.mean(draws >= 1))
    return float(np.mean(draws > float(line)))


def projection_for(year: int, week: int, player: str, stat: str) -> float | None:
    bundle = simulate_week(year, week)
    row, _draws = lookup_row(bundle, player, stat)
    if not row:
        return None
    return float(row["median"])
