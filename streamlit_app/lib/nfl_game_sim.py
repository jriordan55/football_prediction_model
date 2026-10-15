"""Full-game NFL prop sim from nflfastR play-by-play.

Play volume and play-call rates come from the play-by-play. Each player's
share and efficiency is a Kalman rating updated after every game, the same
idea as a rating that moves only as far as the snaps support. A game is then
a Monte Carlo draw of plays, play type, and who is on the field.
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
N_SIMS = 1200
KEEP_DRAWS = 400

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
        slots = calendar.get(team, [])[-12:]
        pass_att_cal = []
        rush_att_cal = []
        targets_cal = []
        team_p_cal = []
        team_r_cal = []
        for season, week in slots:
            row = own.get((season, week))
            pass_att_cal.append(0.0 if row is None else float(row.pass_att))
            rush_att_cal.append(0.0 if row is None else float(row.rush_att))
            targets_cal.append(0.0 if row is None else float(row.targets))
            team_p_cal.append(float(team_pass.get((season, week, team), 0) or 0))
            team_r_cal.append(float(team_rush.get((season, week, team), 0) or 0))
        pass_att_cal = np.array(pass_att_cal)
        rush_att_cal = np.array(rush_att_cal)
        targets_cal = np.array(targets_cal)
        team_p = np.array(team_p_cal)
        team_r = np.array(team_r_cal)
        pass_share, _ = _kalman(np.divide(pass_att_cal, np.maximum(team_p, 1)), np.maximum(team_p, 1), start=0.02, q=0.01, r_scale=10)
        rush_share, _ = _kalman(np.divide(rush_att_cal, np.maximum(team_r, 1)), np.maximum(team_r, 1), start=0.02, q=0.01, r_scale=8)
        tgt_share, _ = _kalman(np.divide(targets_cal, np.maximum(team_p, 1)), np.maximum(team_p, 1), start=0.02, q=0.01, r_scale=8)
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
    out = {}
    for row in rows:
        raw = row.get("availability")
        availability = 1.0 if raw is None else float(raw)
        if availability > 0.25:
            continue
        out[(norm_name(str(row.get("name") or "")), abbr(team_abbr(str(row.get("team") or ""))))] = True
    for player in players.values():
        if out.get((norm_name(player["name"]), player["team"])):
            player["play"] = 0.0


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


def _drop_crumbs(roster: list[dict[str, Any]], key: str, floor: float) -> None:
    """A one-play sample should not steal carries from the starter."""
    if not roster:
        return
    best = max(float(player[key]) for player in roster)
    for player in roster:
        share = float(player[key])
        if share < floor and share < best - 1e-9:
            player[key] = 0.0


def _renorm(shares: np.ndarray, active: np.ndarray) -> np.ndarray:
    weighted = shares * active
    total = weighted.sum(axis=0)
    total = np.where(total <= 1e-6, 1.0, total)
    return weighted / total


def _gamma(rng: np.random.Generator, count: np.ndarray, mean: float) -> np.ndarray:
    scale = max(float(mean), 0.5)
    draws = rng.gamma(np.maximum(count, 1e-3), scale)
    return np.where(count >= 0.5, draws, 0.0)


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
    active = np.vstack([rng.random(n) < max(player["play"], 0) for player in roster])
    pass_share = _renorm(np.vstack([np.full(n, player["pass_share"]) for player in roster]), active)
    rush_share = _renorm(np.vstack([np.full(n, player["rush_share"]) for player in roster]), active)
    tgt_share = _renorm(np.vstack([np.full(n, player["target_share"]) for player in roster]), active)
    out: dict[str, dict[str, np.ndarray]] = {}
    rec_yards_team = np.zeros(n)
    rec_td_team = np.zeros(n)
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
        rec_yards_team += rec_yds
        rec_td_team += rec_tds
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
    # Passing production has to agree with the catches in the same simulated game.
    for player in roster:
        share = pass_share[roster.index(player)]
        row = out[player["name"]]
        row["pass_yds"] = rec_yards_team * share
        row["pass_tds"] = rec_td_team * share
        row["pass_completions"] = sum(out[mate["name"]]["receptions"] * share for mate in roster)
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
        _drop_crumbs(roster, "pass_share", 0.2)
        _drop_crumbs(roster, "rush_share", 0.05)
        _drop_crumbs(roster, "target_share", 0.04)
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


def simulate_week(year: int, week: int, *, n_sims: int = N_SIMS, refresh: bool = False) -> dict[str, Any]:
    """Draw the week and cache a short sample of every player's distribution."""
    meta_path = _meta_path(year, week)
    data_path = _cache_path(year, week)
    if meta_path.exists() and data_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if int(meta.get("n_sims") or 0) == int(n_sims):
            draws = np.load(data_path)["draws"]
            return {"meta": meta, "draws": draws}

    from .nfl_games import load_nfl_games

    model = fit(year, week)
    games = [game for game in load_nfl_games(year) if int(game.get("week") or 0) == int(week)]
    if not games:
        games = [{"homeTeam": home, "awayTeam": away, "spread": 0} for home, away in _fallback_games(model)]
    series: list[np.ndarray] = []
    index: list[dict[str, Any]] = []
    step = max(n_sims // KEEP_DRAWS, 1)
    for game in games:
        home = str(game.get("homeTeam") or game.get("home") or "")
        away = str(game.get("awayTeam") or game.get("away") or "")
        if not home or not away:
            continue
        spread = float(game.get("spread") or 0)
        seed = abs(hash((year, week, home, away))) % (2**31)
        box = simulate_game(model, team_abbr(home), team_abbr(away), spread=spread, n_sims=n_sims, seed=seed)
        for name, stats in box.items():
            for stat, values in stats.items():
                sample = np.asarray(values[::step][:KEEP_DRAWS], dtype=np.float32)
                if sample.size < 20:
                    continue
                series.append(sample)
                index.append({
                    "player": name,
                    "name": norm_name(name),
                    "team": team_abbr(home) if _on_team(model, name, team_abbr(home)) else team_abbr(away),
                    "stat": stat,
                    "mean": round(float(np.mean(values)), 2),
                    "median": round(float(np.median(values)), 2),
                    "p10": round(float(np.quantile(values, 0.1)), 2),
                    "p90": round(float(np.quantile(values, 0.9)), 2),
                    "play": round(float(_play_of(model, name)), 3),
                })
    draws = np.vstack(series) if series else np.zeros((0, KEEP_DRAWS), dtype=np.float32)
    meta = {"year": year, "week": week, "n_sims": int(n_sims), "rows": index}
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
