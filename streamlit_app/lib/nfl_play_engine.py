"""Play-by-play game simulation for NFL props.

Each simulated game walks the state the way a broadcast does. On a normal
down the action model samples a pass or a run. A pass then samples the
depth, the result (complete, incomplete, intercepted, sacked), the yards
after the catch, and a fumble. Fourth down uses its own decision: go, field
goal, or punt, fit to what teams actually do. The clock runs off after the
play, and the score, down, and field position carry into the next one.

Team tendencies shift the league probability in logit space, so a pass-heavy
team stays a pass-heavy team without the probability ever leaving 0 to 1.
The ball is given to a player in proportion to his own last 10 games, on any
team, and only if he is on the current depth chart. A player who is ruled out
hands that role to the next player at his position, up to a normal starter
week. A small share is kept.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import DATA_DIR

CACHE = DATA_DIR / "fastr_player_cache" / "nfl"
_PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"

_DIST_EDGES = (1, 3, 6, 10)  # bins: 1, 2-3, 4-6, 7-10, 11+
_FIELD_EDGES = (10, 20, 50, 80)  # yardline_100 bins
_SCORE_EDGES = (-13, -6, 7, 14)
_AIR_EDGES = (0, 6, 13, 21)
_LEAGUE_YPC = 4.3
_LEAGUE_CATCH = 0.64


def _logit(p: np.ndarray | float) -> np.ndarray | float:
    p = np.clip(p, 1e-3, 1.0 - 1e-3)
    return np.log(p / (1.0 - p))


def _expit(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -12, 12)))


def _bin_from_edges(values: np.ndarray, edges: tuple[int, ...]) -> np.ndarray:
    return np.searchsorted(edges, values, side="right").astype(np.int16)


def _dist_bin(ydstogo: np.ndarray) -> np.ndarray:
    return _bin_from_edges(np.clip(ydstogo, 1, 30), _DIST_EDGES)


def _field_bin(yardline: np.ndarray) -> np.ndarray:
    return _bin_from_edges(np.clip(yardline, 1, 99), _FIELD_EDGES)


def _score_bin(diff: np.ndarray) -> np.ndarray:
    return _bin_from_edges(diff, _SCORE_EDGES)


def _air_bin(air: np.ndarray) -> np.ndarray:
    return _bin_from_edges(air, _AIR_EDGES)


def _situation_index(down: np.ndarray, dist: np.ndarray, field: np.ndarray, qtr: np.ndarray, score: np.ndarray) -> np.ndarray:
    """Down is 0..2 for downs 1..3."""
    return ((((down * 5 + dist) * 5 + field) * 4 + qtr) * 5 + score).astype(np.int32)


def _fourth_index(dist: np.ndarray, field: np.ndarray, qtr: np.ndarray, score: np.ndarray) -> np.ndarray:
    return (((dist * 5 + field) * 4 + qtr) * 5 + score).astype(np.int32)


def _load_plays(year: int, week: int) -> pd.DataFrame:
    from .nfl_game_sim import _download

    frames = []
    columns = [
        "season", "week", "season_type", "posteam", "defteam", "play_type", "down", "ydstogo",
        "yardline_100", "qtr", "score_differential", "air_yards", "yards_after_catch",
        "yards_gained", "passing_yards", "receiving_yards", "pass_attempt", "rush_attempt",
        "complete_pass", "interception", "sack", "fumble_lost", "field_goal_result", "kick_distance",
    ]
    for season in (year - 1, year):
        path = CACHE / f"pbp_{season}.parquet"
        if not _download(_PBP.format(season=season), path):
            continue
        frame = pd.read_parquet(path, columns=[c for c in columns if c])
        frame["season"] = pd.to_numeric(frame["season"], errors="coerce")
        frame["week"] = pd.to_numeric(frame["week"], errors="coerce")
        if "season_type" in frame.columns:
            frame = frame[frame["season_type"].isin(["REG", "POST"])]
        if season == int(year):
            frame = frame[frame["week"] < int(week)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    plays = pd.concat(frames, ignore_index=True)
    plays = plays[plays["posteam"].notna() & plays["down"].notna() & plays["yardline_100"].notna()]
    for col in ("ydstogo", "yardline_100", "qtr", "score_differential", "air_yards", "yards_after_catch", "yards_gained", "kick_distance", "passing_yards", "receiving_yards"):
        plays[col] = pd.to_numeric(plays[col], errors="coerce")
    for col in ("pass_attempt", "rush_attempt", "complete_pass", "interception", "sack", "fumble_lost"):
        if col not in plays.columns:
            plays[col] = 0
        plays[col] = pd.to_numeric(plays[col], errors="coerce").fillna(0)
    return plays


def _shrink_rate(success: np.ndarray, trials: np.ndarray, prior: float, prior_n: float) -> np.ndarray:
    return (success + prior * prior_n) / np.maximum(trials + prior_n, 1.0)


class PlayEngine:
    def __init__(self, tables: dict[str, Any]):
        self.pass_p = tables["pass_p"]
        self.team_delta = tables["team_delta"]
        self.fourth = tables["fourth"]  # (500, 3) go, fg, punt
        self.team_fourth = tables["team_fourth"]
        self.rush_pools = tables["rush_pools"]
        self.air_pools = tables["air_pools"]
        self.yac_pools = tables["yac_pools"]
        self.complete_p = tables["complete_p"]
        self.int_p = tables["int_p"]
        self.sack_p = tables["sack_p"]
        self.fumble_p = float(tables["fumble_p"])
        self.fg_make = tables["fg_make"]
        self.league_ypa = float(tables.get("league_ypa") or 7.0)
        self.league_ypt = float(tables.get("league_ypt") or 7.5)
        self.def_pass = tables.get("def_pass") or {}
        self.def_rush = tables.get("def_rush") or {}

    def pass_probability(self, team: str, down: np.ndarray, dist: np.ndarray, field: np.ndarray, qtr: np.ndarray, score: np.ndarray) -> np.ndarray:
        idx = _situation_index(np.clip(down, 1, 3) - 1, dist, field, np.clip(qtr, 1, 4) - 1, score)
        base = self.pass_p[idx]
        return _expit(_logit(base) + self.team_delta.get(team, 0.0))

    def fourth_choice(self, team: str, dist: np.ndarray, field: np.ndarray, qtr: np.ndarray, score: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """0 go, 1 field goal, 2 punt."""
        idx = _fourth_index(dist, field, np.clip(qtr, 1, 4) - 1, score)
        probs = self.fourth[idx].copy()
        delta = self.team_fourth.get(team, 0.0)
        # Shift the go-for-it probability in logit space, then renorm the rest.
        go = _expit(_logit(probs[:, 0]) + delta)
        rest = np.maximum(1.0 - go, 1e-6)
        other = probs[:, 1] + probs[:, 2]
        fg = rest * np.divide(probs[:, 1], np.maximum(other, 1e-6))
        punt = rest - fg
        stacked = np.cumsum(np.column_stack([go, fg, punt]), axis=1)
        return (rng.random(len(go))[:, None] > stacked).sum(axis=1).astype(np.int8)


def _defense_factors(plays: pd.DataFrame, year: int) -> tuple[dict[str, float], dict[str, float]]:
    """Yards allowed versus the league. This season counts three times last season, then shrinks."""
    if plays.empty or "defteam" not in plays.columns:
        return {}, {}
    from .nfl_game_sim import abbr

    frame = plays[plays["defteam"].notna()]
    if frame.empty:
        return {}, {}
    seasons = pd.to_numeric(frame["season"], errors="coerce").to_numpy()
    defense = frame["defteam"].astype(str).to_numpy()
    pass_att = frame["pass_attempt"].to_numpy(dtype=float)
    rush_att = frame["rush_attempt"].to_numpy(dtype=float)
    pass_yds = frame["passing_yards"].to_numpy(dtype=float) if "passing_yards" in frame.columns else frame["yards_gained"].to_numpy(dtype=float)
    rush_col = "rushing_yards" if "rushing_yards" in frame.columns else "yards_gained"
    rush_yds = frame[rush_col].to_numpy(dtype=float)

    def _rates(attempts: np.ndarray, yards: np.ndarray) -> dict[str, float]:
        used = attempts > 0
        yards = np.where(np.isfinite(yards), yards, 0.0)
        league = float(yards[used].sum() / max(float(attempts[used].sum()), 1.0))
        if league <= 0:
            return {}
        out = {}
        for team in np.unique(defense):
            on = defense == team
            cur = on & (seasons == int(year)) & used
            old = on & (seasons != int(year)) & used
            n_cur = float(attempts[cur].sum()) if cur.any() else 0.0
            n_old = float(attempts[old].sum()) if old.any() else 0.0
            y_cur = float(yards[cur].sum() / n_cur) if n_cur else league
            y_old = float(yards[old].sum() / n_old) if n_old else league
            n = 3.0 * n_cur + n_old
            if n <= 0:
                continue
            rate = (3.0 * n_cur * y_cur + n_old * y_old) / n
            shrink = n / (n + 400.0)
            factor = 1.0 + shrink * (rate / league - 1.0)
            if not np.isfinite(factor):
                continue
            out[abbr(str(team))] = float(np.clip(factor, 0.90, 1.10))
        return out

    return _rates(pass_att, pass_yds), _rates(rush_att, rush_yds)


def fit_engine(year: int, week: int) -> PlayEngine:
    return fit_plays(_load_plays(year, week), year=year)


def fit_plays(plays: pd.DataFrame, *, year: int | None = None) -> PlayEngine:
    """Fit the situation model. `plays` already uses this module's column names."""
    empty = _empty_engine()
    if plays is None or plays.empty:
        return empty
    scrimmage = plays[plays["play_type"].isin(["pass", "run"]) & plays["down"].between(1, 3)]
    if scrimmage.empty:
        return empty
    down = scrimmage["down"].to_numpy(dtype=int) - 1
    dist = _dist_bin(scrimmage["ydstogo"].fillna(10).to_numpy(dtype=float))
    field = _field_bin(scrimmage["yardline_100"].to_numpy(dtype=float))
    qtr = np.clip(scrimmage["qtr"].fillna(1).to_numpy(dtype=int), 1, 4) - 1
    score = _score_bin(scrimmage["score_differential"].fillna(0).to_numpy(dtype=float))
    idx = _situation_index(down, dist, field, qtr, score)
    is_pass = (scrimmage["play_type"] == "pass").to_numpy()
    pass_n = np.bincount(idx, weights=is_pass.astype(float), minlength=1500)
    play_n = np.bincount(idx, minlength=1500).astype(float)
    down_rate = np.array([
        float(is_pass[down == d].mean()) if np.any(down == d) else 0.55 for d in range(3)
    ])
    # Every cell, including ones the sample never hit, shrinks toward that down's
    # league pass rate. An empty cell is not a cell where teams never pass.
    cell_down = np.arange(1500) // 500
    pass_p = (pass_n + 40.0 * down_rate[cell_down]) / (play_n + 40.0)
    pass_p = np.clip(pass_p, 0.08, 0.92)

    league_pass = float(is_pass.mean())
    if year is None and "season" in plays.columns:
        year = int(pd.to_numeric(plays["season"], errors="coerce").max() or 0)
    year = int(year or 0)
    team_delta: dict[str, float] = {}
    from .nfl_game_sim import abbr

    seasons = scrimmage["season"].to_numpy()
    posteams = scrimmage["posteam"].astype(str).to_numpy()
    for team in np.unique(posteams):
        on_team = posteams == team
        current = on_team & (seasons == int(year))
        earlier = on_team & ~current
        n_cur = int(current.sum())
        n_old = int(earlier.sum())
        p_cur = float(is_pass[current].mean()) if n_cur else league_pass
        p_old = float(is_pass[earlier].mean()) if n_old else league_pass
        # This season counts three times, so a team that changed its offense
        # is not stuck on last year's rate.
        n = 3.0 * n_cur + n_old
        rate = (3.0 * n_cur * p_cur + n_old * p_old) / n
        shrink = n / (n + 400.0)
        team_delta[abbr(team)] = float(shrink * (_logit(rate) - _logit(league_pass)))

    fourth_plays = plays[plays["down"] == 4]
    fourth = np.full((500, 3), 1.0 / 3.0)
    team_fourth: dict[str, float] = {}
    if not fourth_plays.empty:
        kind = np.zeros(len(fourth_plays), dtype=int)
        ptype = fourth_plays["play_type"].astype(str)
        kind[ptype.isin(["pass", "run"]).to_numpy()] = 0
        kind[(ptype == "field_goal").to_numpy()] = 1
        kind[(ptype == "punt").to_numpy()] = 2
        known = ptype.isin(["pass", "run", "field_goal", "punt"]).to_numpy()
        dist4 = _dist_bin(fourth_plays["ydstogo"].fillna(10).to_numpy(dtype=float))
        field4 = _field_bin(fourth_plays["yardline_100"].to_numpy(dtype=float))
        qtr4 = np.clip(fourth_plays["qtr"].fillna(1).to_numpy(dtype=int), 1, 4) - 1
        score4 = _score_bin(fourth_plays["score_differential"].fillna(0).to_numpy(dtype=float))
        idx4 = _fourth_index(dist4, field4, qtr4, score4)
        counts = np.zeros((500, 3))
        for choice in range(3):
            counts[:, choice] = np.bincount(idx4[known & (kind == choice)], minlength=500)
        league_go = float((kind[known] == 0).mean()) if known.any() else 0.15
        fourth = counts + np.array([league_go, (1 - league_go) * 0.45, (1 - league_go) * 0.55]) * 25.0
        fourth = fourth / fourth.sum(axis=1, keepdims=True)
        for team, group in fourth_plays.groupby(fourth_plays["posteam"].astype(str)):
            gtype = group["play_type"].astype(str)
            go = gtype.isin(["pass", "run"])
            if len(group) < 8:
                continue
            rate = float(go.mean())
            shrink = len(group) / (len(group) + 40.0)
            from .nfl_game_sim import abbr

            team_fourth[abbr(str(team))] = float(shrink * (_logit(rate) - _logit(league_go)))

    runs = plays[(plays["play_type"] == "run") & plays["yards_gained"].notna()]
    rush_pools = _pools(
        runs,
        lambda frame: _field_bin(frame["yardline_100"].to_numpy(dtype=float)) * 2
        + (frame["ydstogo"].fillna(10).to_numpy(dtype=float) <= 2).astype(int),
        10,
        "yards_gained",
    )
    passes = plays[(plays["play_type"] == "pass") & (plays["sack"] == 0) & plays["air_yards"].notna()]
    air_pools = _pools(passes, lambda frame: _field_bin(frame["yardline_100"].to_numpy(dtype=float)), 5, "air_yards")
    yac = passes[passes["complete_pass"] == 1]
    yac_pools = _pools(yac, lambda frame: _air_bin(frame["air_yards"].fillna(0).to_numpy(dtype=float)), 5, "yards_after_catch")
    complete_p = np.full(5, 0.64)
    int_p = np.full(5, 0.02)
    if not passes.empty:
        ab = _air_bin(passes["air_yards"].to_numpy(dtype=float))
        complete_p = _shrink_rate(
            np.bincount(ab, weights=passes["complete_pass"].to_numpy(dtype=float), minlength=5),
            np.bincount(ab, minlength=5).astype(float),
            0.64,
            40,
        )
        int_p = _shrink_rate(
            np.bincount(ab, weights=(passes["interception"] == 1).to_numpy(dtype=float), minlength=5),
            np.bincount(ab, minlength=5).astype(float),
            0.025,
            80,
        )
        int_p = np.minimum(int_p, 0.5 * (1.0 - complete_p))
    sack_rows = plays[plays["play_type"].isin(["pass", "run"]) & plays["down"].between(1, 3)]
    sack_p = np.full(3, 0.07)
    if not sack_rows.empty:
        # Sack rate among dropbacks, by down. Dropbacks are pass plays including sacks.
        drop = plays[(plays["pass_attempt"] == 1) | (plays["sack"] == 1)]
        if not drop.empty:
            dwn = np.clip(drop["down"].fillna(1).to_numpy(dtype=int), 1, 3) - 1
            sack_p = _shrink_rate(
                np.bincount(dwn, weights=(drop["sack"] == 1).to_numpy(dtype=float), minlength=3),
                np.bincount(dwn, minlength=3).astype(float),
                0.07,
                40,
            )
    fumbles = plays[plays["play_type"].isin(["pass", "run"])]
    fumble_p = float((fumbles["fumble_lost"] == 1).mean()) if len(fumbles) else 0.008
    fumble_p = float(np.clip(fumble_p, 0.002, 0.02))

    fg = plays[plays["play_type"] == "field_goal"]
    fg_make = np.full(8, 0.85)
    if not fg.empty and "field_goal_result" in fg.columns:
        dist_fg = fg["kick_distance"] if "kick_distance" in fg.columns else fg["yardline_100"] + 17
        dist_fg = pd.to_numeric(dist_fg, errors="coerce").fillna(fg["yardline_100"] + 17)
        made = fg["field_goal_result"].astype(str).str.lower().eq("made").to_numpy()
        bucket = np.clip(((dist_fg.to_numpy(dtype=float) - 18) / 6).astype(int), 0, 7)
        fg_make = _shrink_rate(np.bincount(bucket, weights=made.astype(float), minlength=8), np.bincount(bucket, minlength=8).astype(float), 0.8, 12)

    attempts = plays["pass_attempt"] == 1
    thrown = attempts & (plays["sack"] == 0)
    league_ypa = float(plays.loc[attempts, "passing_yards"].sum() / max(float(attempts.sum()), 1.0))
    league_ypt = float(plays.loc[thrown, "receiving_yards"].sum() / max(float(thrown.sum()), 1.0))
    def_pass, def_rush = _defense_factors(plays, year)

    return PlayEngine({
        "pass_p": pass_p.astype(np.float32),
        "team_delta": team_delta,
        "fourth": fourth.astype(np.float32),
        "team_fourth": team_fourth,
        "rush_pools": rush_pools,
        "air_pools": air_pools,
        "yac_pools": yac_pools,
        "complete_p": complete_p.astype(np.float32),
        "int_p": int_p.astype(np.float32),
        "sack_p": sack_p.astype(np.float32),
        "fumble_p": fumble_p,
        "fg_make": fg_make.astype(np.float32),
        "league_ypa": float(np.clip(league_ypa, 5.0, 9.0)),
        "league_ypt": float(np.clip(league_ypt, 4.0, 12.0)),
        "def_pass": def_pass,
        "def_rush": def_rush,
    })


def _pools(frame: pd.DataFrame, bin_of, n_bins: int, column: str) -> list[np.ndarray]:
    if frame.empty:
        return [np.array([0.0], dtype=np.float32) for _ in range(n_bins)]
    bins = bin_of(frame)
    values = frame[column].to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    bins = bins[np.isfinite(frame[column].to_numpy(dtype=float))]
    pools = []
    fallback = values if len(values) else np.array([0.0])
    for b in range(n_bins):
        pool = values[bins == b] if len(bins) == len(values) else fallback
        # bins was filtered with values, lengths match
        pool = frame.loc[np.isfinite(frame[column].to_numpy(dtype=float)), column].to_numpy(dtype=float)
        # recompute cleanly
        break
    clean_val = frame[column].to_numpy(dtype=float)
    ok = np.isfinite(clean_val)
    clean_val = clean_val[ok]
    clean_bin = bin_of(frame)[ok]
    fallback = clean_val if len(clean_val) else np.array([0.0], dtype=np.float32)
    pools = []
    for b in range(n_bins):
        pool = clean_val[clean_bin == b]
        pools.append(pool.astype(np.float32) if len(pool) >= 20 else fallback.astype(np.float32))
    return pools


def _empty_engine() -> PlayEngine:
    return PlayEngine({
        "pass_p": np.full(1500, 0.57, dtype=np.float32),
        "team_delta": {},
        "fourth": np.array([[0.12, 0.35, 0.53]], dtype=np.float32).repeat(500, axis=0),
        "team_fourth": {},
        "rush_pools": [np.array([3.0], dtype=np.float32) for _ in range(10)],
        "air_pools": [np.array([7.0], dtype=np.float32) for _ in range(5)],
        "yac_pools": [np.array([4.0], dtype=np.float32) for _ in range(5)],
        "complete_p": np.full(5, 0.64, dtype=np.float32),
        "int_p": np.full(5, 0.02, dtype=np.float32),
        "sack_p": np.full(3, 0.07, dtype=np.float32),
        "fumble_p": 0.008,
        "fg_make": np.full(8, 0.85, dtype=np.float32),
        "league_ypa": 7.0,
        "league_ypt": 7.5,
        "def_pass": {},
        "def_rush": {},
    })


def _draw_pool(pools: list[np.ndarray], bins: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(len(bins), dtype=np.float32)
    for b, pool in enumerate(pools):
        mask = bins == b
        count = int(mask.sum())
        if count:
            out[mask] = pool[rng.integers(0, len(pool), size=count)]
    return out


def _pick(weights: np.ndarray, rng: np.random.Generator, n: int) -> np.ndarray:
    """Sample a player. Shares that do not add up to 1 stay unclaimed."""
    if n == 0 or len(weights) == 0:
        return np.full(n, -1, dtype=np.int16)
    total = float(weights.sum())
    if total <= 1e-8:
        return np.full(n, -1, dtype=np.int16)
    roll = rng.random(n)
    if total > 1.0:
        cdf = np.cumsum(weights / total)
        return np.searchsorted(cdf, roll, side="right").astype(np.int16).clip(0, len(weights) - 1)
    cdf = np.cumsum(weights)
    picked = np.searchsorted(cdf, roll, side="right")
    return np.where(roll >= total, -1, picked).astype(np.int16)


def _cap_shares(raw: np.ndarray) -> np.ndarray:
    """Keep the largest share whole. Teammates only fill what is left.

    Each share is that player's own last 10 games, so they overlap. Dividing
    them all down to 1 cuts a 60% back to a third of the carries.
    """
    out = np.zeros_like(raw)
    used = 0.0
    for index in np.argsort(-raw):
        share = float(raw[index])
        if share <= 0 or used >= 1.0:
            break
        take = min(share, 1.0 - used)
        out[index] = take
        used += take
    return out


def _on_depth_chart(player: dict[str, Any], key: str) -> bool:
    """Historical shares from players no longer on the chart do not get carries."""
    if player.get("chart") is not False:
        return True
    pos = str(player.get("position") or "")
    if key == "rush_share":
        return pos not in {"RB", "FB"}
    if key == "target_share":
        return pos not in {"RB", "WR", "TE", "FB"}
    return True


def _roster_weights(roster: list[dict[str, Any]], key: str) -> np.ndarray:
    raw = np.zeros(len(roster), dtype=float)
    for i, player in enumerate(roster):
        if player.get("out") or not _on_depth_chart(player, key):
            continue
        raw[i] = max(float(player.get(key) or 0.0), 0.0)
    return _cap_shares(raw)


def simulate_game(
    engine: PlayEngine,
    home: list[dict[str, Any]],
    away: list[dict[str, Any]],
    *,
    n: int,
    rng: np.random.Generator,
    max_steps: int = 170,
) -> dict[str, dict[str, np.ndarray]]:
    """Simulate `n` copies of one game. Returns norm-name -> stat -> draws."""
    sides = [home, away]
    names = [[str(p["name"]) for p in roster] for roster in sides]
    rush_w = [_roster_weights(roster, "rush_share") for roster in sides]
    tgt_w = [_roster_weights(roster, "target_share") for roster in sides]
    ypc = [np.array([float(p.get("ypc") or _LEAGUE_YPC) for p in roster] or [0.0]) for roster in sides]
    catch = [np.array([float(p.get("catch") or _LEAGUE_CATCH) for p in roster] or [0.0]) for roster in sides]
    ypt = [np.array([float(p.get("ypt") or engine.league_ypt) for p in roster] or [engine.league_ypt]) for roster in sides]
    qb = []
    qb_ypa = []
    for roster in sides:
        weights = np.zeros(len(roster), dtype=float)
        ypas = np.array(
            [float(p.get("ypa") or engine.league_ypa) for p in roster] or [engine.league_ypa],
            dtype=float,
        )
        starter = None
        starter_key = None
        for i, player in enumerate(roster):
            if player.get("out") or float(player.get("play") or 0.0) <= 0:
                continue
            if str(player.get("position") or "") != "QB":
                continue
            if player.get("chart") is False:
                continue
            depth = player.get("depth")
            key = (depth is None, 99 if depth is None else int(depth))
            if starter_key is None or key < starter_key:
                starter_key = key
                starter = i
        if starter is not None:
            weights[starter] = 1.0
        else:
            for i, player in enumerate(roster):
                if player.get("out"):
                    continue
                share = float(player.get("pass_share") or 0.0)
                play = float(player.get("play") or 0.0)
                position = str(player.get("position") or "")
                if play >= 0.5 or position == "QB":
                    share = max(share, float(player.get("pass_when_in") or 0.0))
                if play <= 0 or share <= 0:
                    continue
                if position != "QB" and share <= 0.08:
                    continue
                weights[i] = share * play
        if len(roster) == 0 or float(weights.sum()) <= 0:
            qb.append(np.zeros(n, dtype=np.int16))
            qb_ypa.append(np.full(n, engine.league_ypa))
            continue
        cdf = np.cumsum(weights / weights.sum())
        choice = np.searchsorted(cdf, rng.random(n), side="right").astype(np.int16).clip(0, len(roster) - 1)
        qb.append(choice)
        qb_ypa.append(ypas[choice])
    team_code = []
    for roster in sides:
        team_code.append(str(roster[0]["team"]) if roster else "")

    stats = ("pass_yds", "pass_yds_q1", "pass_tds", "pass_attempts", "pass_completions", "pass_ints", "rush_yds", "rush_attempts", "rush_tds", "rec_yds", "receptions", "rec_tds")
    box: list[dict[str, np.ndarray]] = []
    for roster in sides:
        box.append({stat: np.zeros((max(len(roster), 1), n), dtype=np.float32) for stat in stats})

    qtr = np.ones(n, dtype=np.int8)
    clock = np.full(n, 900, dtype=np.int16)
    yard = np.full(n, 70, dtype=np.int16)  # yards to the goal; 70 is a touchback at the 30
    down = np.ones(n, dtype=np.int8)
    dist = np.full(n, 10, dtype=np.int16)
    off = np.ones(n, dtype=np.int8)  # away receives the opening kickoff
    home_score = np.zeros(n, dtype=np.int16)
    away_score = np.zeros(n, dtype=np.int16)
    alive = np.ones(n, dtype=bool)
    home_received_open = np.zeros(n, dtype=bool)

    def score_diff_off() -> np.ndarray:
        return np.where(off == 0, home_score - away_score, away_score - home_score)

    def add(side: int, player: np.ndarray, stat: str, amount: np.ndarray, mask: np.ndarray) -> None:
        if not names[side]:
            return
        for i in range(len(names[side])):
            take = mask & (player == i)
            if take.any():
                box[side][stat][i, take] += amount[take]

    for _step in range(int(max_steps)):
        if not alive.any():
            break
        active = alive.copy()
        # Kneel it out when the leader is inside two minutes of the fourth.
        diff = score_diff_off()
        kneel = active & (qtr == 4) & (clock < 140) & (diff > 0) & (down < 4)
        if kneel.any():
            clock[kneel] = np.maximum(clock[kneel] - 40, 0)
        active = active & ~kneel

        dist_b = _dist_bin(dist.astype(float))
        field_b = _field_bin(yard.astype(float))
        score_b = _score_bin(diff.astype(float))
        is_fourth = active & (down == 4)
        choice = np.full(n, -1, dtype=np.int8)
        if is_fourth.any():
            for side, code in enumerate(team_code):
                mask = is_fourth & (off == side)
                if mask.any():
                    choice[mask] = engine.fourth_choice(code, dist_b[mask], field_b[mask], qtr[mask], score_b[mask], rng)

        go = active & ((down < 4) | (choice == 0))
        kick_fg = active & (choice == 1)
        punt = active & (choice == 2)

        # Field goals.
        if kick_fg.any():
            distance = np.clip(yard + 17, 18, 62)
            bucket = np.clip(((distance - 18) / 6).astype(int), 0, 7)
            made = rng.random(n) < engine.fg_make[bucket]
            made = made & kick_fg
            missed = kick_fg & ~made
            home_score[made & (off == 0)] += 3
            away_score[made & (off == 1)] += 3
            _kickoff(off, yard, down, dist, made)
            # Missed kick: the other team takes over at the spot.
            if missed.any():
                off[missed] = 1 - off[missed]
                yard[missed] = np.clip(100 - yard[missed], 1, 99)
                down[missed] = 1
                dist[missed] = 10
            clock[kick_fg] = np.maximum(clock[kick_fg] - 8, 0)

        # Punts. Net 40, touchback to the 20 when it would reach the end zone.
        if punt.any():
            net = 40 + rng.normal(0, 8, n)
            spot = yard - net
            touchback = punt & (spot <= 0)
            normal = punt & ~touchback
            off[punt] = 1 - off[punt]
            yard[touchback] = 80
            yard[normal] = np.clip(spot[normal], 1, 99)
            down[punt] = 1
            dist[punt] = 10
            clock[punt] = np.maximum(clock[punt] - 12, 0)

        if not go.any():
            _advance_clock_boundary(qtr, clock, alive, off, yard, down, dist, home_received_open)
            continue

        is_pass = np.zeros(n, dtype=bool)
        for side, code in enumerate(team_code):
            mask = go & (off == side)
            if not mask.any():
                continue
            is_pass[mask] = rng.random(int(mask.sum())) < engine.pass_probability(
                code, down[mask], dist_b[mask], field_b[mask], qtr[mask], score_b[mask]
            )
        is_run = go & ~is_pass

        # Runs.
        if is_run.any():
            short = (dist <= 2).astype(np.int16)
            pools_i = field_b * 2 + short
            gained = _draw_pool(engine.rush_pools, pools_i, rng)
            for side in (0, 1):
                mask = is_run & (off == side)
                if not mask.any() or not names[side]:
                    continue
                who = _pick(rush_w[side], rng, n)
                opp = team_code[1 - side] if side < len(team_code) else ""
                def_r = engine.def_rush.get(opp, 1.0)
                if not np.isfinite(def_r):
                    def_r = 1.0
                scale = np.ones(n, dtype=float)
                known = who >= 0
                if known.any():
                    scale[known] = ypc[side][who[known]] / _LEAGUE_YPC
                scale *= def_r
                scale = np.clip(scale, 0.85, 1.15)
                yards = np.round(gained * scale).astype(np.int16)
                _apply_gain(
                    mask, who, yards, side, off, yard, down, dist, qtr,
                    add, "rush_yds", "rush_attempts", "rush_tds", rng, engine.fumble_p,
                    home_score, away_score,
                )
            clock[is_run] = np.maximum(clock[is_run] - rng.integers(28, 42, int(is_run.sum())), 0)

        # Passes.
        if is_pass.any():
            sack_rate = engine.sack_p[np.clip(down, 1, 3) - 1]
            sack = is_pass & (rng.random(n) < sack_rate)
            thrown = is_pass & ~sack
            air = _draw_pool(engine.air_pools, field_b, rng)
            ab = _air_bin(air)
            complete_p = engine.complete_p[ab]
            int_p = engine.int_p[ab]
            incomplete = np.zeros(n, dtype=bool)
            for side in (0, 1):
                mask = thrown & (off == side)
                if not mask.any() or not names[side]:
                    continue
                who = _pick(tgt_w[side], rng, n)
                known = who >= 0
                catch_rate = np.full(n, _LEAGUE_CATCH)
                ypt_rate = np.full(n, engine.league_ypt)
                if known.any():
                    catch_rate[known] = catch[side][who[known]]
                    ypt_rate[known] = ypt[side][who[known]]
                skill = _expit(_logit(complete_p) + _logit(catch_rate) - _logit(_LEAGUE_CATCH))
                roll = rng.random(n)
                complete = mask & (roll < skill)
                intercepted = mask & ~complete & (roll < skill + int_p)
                fell = mask & ~complete & ~intercepted
                incomplete |= fell
                yac = _draw_pool(engine.yac_pools, ab, rng)
                opp = team_code[1 - side] if side < len(team_code) else ""
                def_p = engine.def_pass.get(opp, 1.0)
                if not np.isfinite(def_p):
                    def_p = 1.0
                yards = np.round((air + yac) * np.clip(ypt_rate / max(engine.league_ypt, 1.0), 0.70, 1.70) * def_p).astype(np.int16)
                gain = np.minimum(
                    np.round((air + yac) * np.clip(qb_ypa[side] / max(engine.league_ypa, 1.0), 0.80, 1.28) * def_p),
                    yard,
                ).astype(np.float32)
                scored = complete & (yards >= yard)
                qb_i = qb[side]
                _apply_gain(
                    complete, who, yards, side, off, yard, down, dist, qtr,
                    add, "rec_yds", "receptions", "rec_tds", rng, engine.fumble_p,
                    home_score, away_score,
                )
                add(side, qb_i, "pass_yds", gain, complete)
                add(side, qb_i, "pass_yds_q1", gain, complete & (qtr == 1))
                add(side, qb_i, "pass_tds", np.ones(n, dtype=np.float32), scored)
                add(side, qb_i, "pass_attempts", np.ones(n, dtype=np.float32), mask)
                add(side, qb_i, "pass_completions", np.ones(n, dtype=np.float32), complete)
                add(side, qb_i, "pass_ints", np.ones(n, dtype=np.float32), intercepted)
                if intercepted.any():
                    off[intercepted] = 1 - off[intercepted]
                    yard[intercepted] = np.clip(100 - np.maximum(yard[intercepted], 1), 1, 99)
                    down[intercepted] = 1
                    dist[intercepted] = np.minimum(10, yard[intercepted])
            if sack.any():
                lost = rng.integers(1, 10, n).astype(np.int16)
                for side in (0, 1):
                    mask = sack & (off == side)
                    if not mask.any() or not names[side]:
                        continue
                    qb_i = qb[side]
                    add(side, qb_i, "pass_yds", -lost.astype(np.float32), mask)
                    yard[mask] = np.minimum(yard[mask] + lost[mask], 109)
                    dist[mask] = dist[mask] + lost[mask]
                    down[mask] = down[mask] + 1
                    turnover = mask & (down > 4)
                    if turnover.any():
                        off[turnover] = 1 - off[turnover]
                        yard[turnover] = np.clip(100 - np.minimum(yard[turnover], 99), 1, 99)
                        down[turnover] = 1
                        dist[turnover] = 10
            clock[incomplete] = np.maximum(clock[incomplete] - 6, 0)
            burn = is_pass & ~incomplete
            clock[burn] = np.maximum(clock[burn] - rng.integers(26, 40, int(burn.sum())), 0)

        _advance_clock_boundary(qtr, clock, alive, off, yard, down, dist, home_received_open)

    # The situation model shrinks a pass-heavy quarterback back toward the league.
    # Move halfway to the dropbacks in his own last 10 starts.
    for side in (0, 1):
        if not names[side]:
            continue
        starter_i = int(qb[side][0])
        if str(sides[side][starter_i].get("position") or "") != "QB":
            continue
        recent = float(sides[side][starter_i].get("dropbacks") or 0.0)
        if recent < 15:
            continue
        att = box[side]["pass_attempts"][starter_i]
        played = att >= 8
        if int(played.sum()) < 20:
            continue
        center = float(np.median(att[played]))
        if center < 10:
            continue
        factor = float(np.clip(1.0 + 0.5 * (recent / center - 1.0), 0.90, 1.18))
        if abs(factor - 1.0) < 0.02:
            continue
        for stat in ("pass_yds", "pass_yds_q1", "pass_attempts", "pass_completions"):
            box[side][stat][starter_i] *= factor
        for stat in ("rec_yds", "receptions"):
            box[side][stat] *= factor

    out: dict[str, dict[str, np.ndarray]] = {}
    for side in (0, 1):
        for i, name in enumerate(names[side]):
            key = name
            bucket = out.setdefault(key, {})
            for stat in stats:
                bucket[stat] = box[side][stat][i]
    return out


def _kickoff(off, yard, down, dist, made) -> None:
    if not np.any(made):
        return
    off[made] = 1 - off[made]
    yard[made] = 70
    down[made] = 1
    dist[made] = 10


def _advance_clock_boundary(qtr, clock, alive, off, yard, down, dist, home_received_open) -> None:
    expired = alive & (clock <= 0)
    if not expired.any():
        return
    new_qtr = expired & (qtr < 4)
    done = expired & (qtr >= 4)
    alive[done] = False
    qtr[new_qtr] += 1
    clock[new_qtr] = 900
    half = new_qtr & (qtr == 3)
    if half.any():
        # Second-half kickoff to the team that kicked off to open the game.
        off[half] = np.where(home_received_open[half], 1, 0)
        yard[half] = 70
        down[half] = 1
        dist[half] = 10


def _apply_gain(mask, who, yards, side, off, yard, down, dist, qtr, add, yds_stat, att_stat, td_stat, rng, fumble_p, home_score, away_score) -> None:
    if not mask.any():
        return
    add(side, who, att_stat, np.ones(len(mask), dtype=np.float32), mask)
    scored = mask & (yards >= yard)
    stayed = mask & ~scored
    gain = np.minimum(yards, yard).astype(np.float32)
    add(side, who, yds_stat, gain, mask)
    add(side, who, td_stat, np.ones(len(mask), dtype=np.float32), scored)
    if scored.any():
        bonus = np.where(rng.random(len(mask)) < 0.94, 7, 6).astype(np.int16)
        if side == 0:
            home_score[scored] = home_score[scored] + bonus[scored]
        else:
            away_score[scored] = away_score[scored] + bonus[scored]
        _score_td(scored, side, off, yard, down, dist)
    fumbled = stayed & (rng.random(len(mask)) < fumble_p)
    clean = stayed & ~fumbled
    yard[clean] = np.maximum(yard[clean] - yards[clean], 1)
    made_line = clean & (yards >= dist)
    short = clean & ~made_line
    down[made_line] = 1
    dist[made_line] = np.minimum(10, yard[made_line])
    down[short] = down[short] + 1
    dist[short] = np.maximum(dist[short] - yards[short], 1)
    turnover = (short & (down > 4)) | fumbled
    if turnover.any():
        off[turnover] = 1 - off[turnover]
        yard[turnover] = np.clip(100 - yard[turnover], 1, 99)
        down[turnover] = 1
        dist[turnover] = 10


def _score_td(scored, side, off, yard, down, dist) -> None:
    off[scored] = 1 - off[scored]
    yard[scored] = 70
    down[scored] = 1
    dist[scored] = 10


def project_week(year: int, week: int, *, n_sims: int, n_keep: int) -> tuple[list[dict[str, Any]], np.ndarray]:
    """One week of games. Each row is a player stat and the draws behind it."""
    from .nfl_game_sim import abbr, fit, norm_name, team_abbr
    from .nfl_games import load_nfl_games

    engine = fit_engine(year, week)
    model = fit(year, week)
    by_team: dict[str, list[dict[str, Any]]] = {}
    for player in model["players"].values():
        by_team.setdefault(str(player["team"]), []).append(player)

    z = 1.2815515655446004
    series: list[np.ndarray] = []
    index: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rng = np.random.default_rng(int(year) * 100 + int(week))
    games = [g for g in load_nfl_games(year) if int(g.get("week") or 0) == int(week)]
    if not games:
        games = []
    for game in games:
        home_name = str(game.get("homeTeam") or game.get("home") or "")
        away_name = str(game.get("awayTeam") or game.get("away") or "")
        home = abbr(team_abbr(home_name))
        away = abbr(team_abbr(away_name))
        home_roster = by_team.get(home) or []
        away_roster = by_team.get(away) or []
        if not home_roster and not away_roster:
            continue
        game_rng = np.random.default_rng(rng.integers(0, 2**31 - 1))
        simulated = simulate_game(engine, home_roster, away_roster, n=n_sims, rng=game_rng)
        _stash(simulated, home_roster + away_roster, series, index, seen, z, n_keep, game_rng)
    draws = np.vstack(series) if series else np.zeros((0, n_keep), dtype=np.float32)
    return index, draws


def _stash(simulated, roster, series, index, seen, z, n_keep, rng) -> None:
    from .nfl_game_sim import norm_name

    play_of = {str(p["name"]): 0.0 if p.get("out") else float(p.get("play") or 1.0) for p in roster}
    team_of = {str(p["name"]): str(p.get("team") or "") for p in roster}
    order = rng.permutation(next(iter(simulated.values()))["pass_yds"].shape[0])[:n_keep] if simulated else []
    for name, stats in simulated.items():
        key_name = norm_name(name)
        derived = {
            "pass_rush_yds": stats["pass_yds"] + stats["rush_yds"],
            "rr_yds": stats["rush_yds"] + stats["rec_yds"],
            "prd_yds": stats["pass_yds"] + stats["rush_yds"] + stats["rec_yds"],
            "prd_tds": stats["pass_tds"] + stats["rush_tds"] + stats["rec_tds"],
        }
        merged = {**stats, **derived}
        started = stats["pass_attempts"] >= 8
        starter_rate = float(started.mean()) if len(started) else 0.0
        conditional = {
            "pass_yds", "pass_yds_q1", "pass_tds", "pass_attempts", "pass_completions",
            "pass_ints", "pass_rush_yds", "prd_yds", "prd_tds",
        }
        for stat, draws in merged.items():
            if (key_name, stat) in seen:
                continue
            seen.add((key_name, stat))
            # A prop voids when he does not play, so the number is the games
            # he actually threw. Zeros from a backup getting the start were
            # pulling a full game down by about 100 yards.
            sample = draws
            if stat in conditional and starter_rate < 0.98 and int(started.sum()) >= 20:
                sample = draws[started]
            center = float(np.median(sample))
            sd = float(np.std(sample, ddof=1)) if len(sample) > 1 else 0.0
            if not np.isfinite(sd):
                sd = 0.0
            kept = draws[order].astype(np.float32) if len(order) else draws.astype(np.float32)
            series.append(kept)
            index.append({
                "player": name,
                "name": key_name,
                "team": team_of.get(name, ""),
                "stat": stat,
                "mean": round(center, 2),
                "median": round(center, 2),
                "center": round(center, 2),
                "sd": round(sd, 2),
                "p10": round(float(max(0.0, center - z * sd)), 2),
                "p90": round(float(center + z * sd), 2),
                "play": round(play_of.get(name, 1.0), 3),
            })
