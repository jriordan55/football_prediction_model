"""College prop projection from a play-by-play simulation of the game.

Same game walk as the NFL desk. A situation model samples pass or run from
the down, the distance, the field position, the quarter, and the score.
The clock and the score carry forward. The ball goes to a player in
proportion to his own recent share. The number on the desk is the median
of those simulated games. A player who is ruled out is set to zero.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import DATA_DIR
from .nfl_game_sim import _ratings, norm_name

CACHE = DATA_DIR / "fastr_player_cache" / "cfb"
PBP_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "cfbfastR_cfb_pbp/play_by_play_{season}.parquet"
)
N_SIMS = 400
MODEL_VERSION = 1
KEEP_DRAWS = 400
_MAX_STEPS = 210

_PASS = {
    "Pass Reception", "Pass Incompletion", "Passing Touchdown",
    "Interception Return", "Interception Return Touchdown", "Sack",
}
_RUN = {"Rush", "Rushing Touchdown"}
_PUNT = {"Punt"}
_FG = {"Field Goal Good", "Field Goal Missed", "Blocked Field Goal", "Blocked Field Goal Touchdown"}
_COLUMNS = [
    "season", "week", "season_type", "home_team_division", "pos_team", "play_type",
    "play_text", "down", "distance", "yards_to_goal", "period", "pos_score_diff",
    "yards_gained", "yds_sacked", "yds_fg", "fg_made", "penalty_no_play", "fumble_vec",
    "change_of_poss",     "passer_player_name", "completion_player", "sack_taken_player",
    "interception_thrown_player", "incompletion_player", "rusher_player_name", "rush_player",
    "receiver_player_name", "reception_player", "target_player", "id_play",
]


@lru_cache(maxsize=4096)
def school_code(name: str) -> str:
    """Stable team code. Skips the sport switch so a college run never asks Streamlit."""
    from .team_registry import (
        _alias_map,
        _clean_name,
        _expand_st_tokens,
        _resolve_prefix,
        normalize_team_key,
    )

    clean = _clean_name(name or "")
    if not clean:
        return ""
    aliases = _alias_map()
    key = normalize_team_key(clean)
    canonical = aliases.get(key)
    if canonical is None:
        parts = clean.split()
        canonical = _resolve_prefix(parts, aliases)
        if canonical is None:
            expanded = _expand_st_tokens(parts)
            if expanded != parts:
                canonical = _resolve_prefix(expanded, aliases)
    return normalize_team_key(canonical or clean).upper()


def _download(season: int) -> Path | None:
    path = CACHE / f"pbp_{season}.parquet"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    try:
        import requests

        response = requests.get(PBP_URL.format(season=season), timeout=180, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception:
        return path if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return path


def _read_season(season: int) -> pd.DataFrame:
    path = _download(int(season))
    if path is None:
        return pd.DataFrame()
    try:
        frame = pd.read_parquet(path, columns=_COLUMNS)
    except Exception:
        frame = pd.read_parquet(path)
    return frame


def _people(raw: pd.Series) -> pd.Series:
    text = raw.astype(str).str.strip()
    bad = (
        raw.isna()
        | text.str.contains(r"[#()]|shotgun|huddle|caught|kickoff|punt", case=False, regex=True)
        | ~text.str.contains(" ", regex=False)
        | text.isin(["None", "nan", ""])
    )
    return raw.where(~bad)


def _pick(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    out = pd.Series(pd.NA, index=frame.index, dtype="object")
    for column in columns:
        if column not in frame.columns:
            continue
        out = out.where(out.notna(), _people(frame[column]))
    return out


def _prepare(year: int, week: int) -> pd.DataFrame:
    frames = []
    for season in (int(year) - 1, int(year)):
        frame = _read_season(season)
        if frame.empty:
            continue
        frame["season"] = pd.to_numeric(frame.get("season"), errors="coerce")
        frame["week"] = pd.to_numeric(frame.get("week"), errors="coerce")
        if "season_type" in frame.columns:
            kind = frame["season_type"].astype(str).str.lower()
            frame = frame[kind.isin(["regular", "postseason", "post", "2", "3"])]
        if "home_team_division" in frame.columns:
            frame = frame[frame["home_team_division"].astype(str).str.lower().eq("fbs")]
        if season == int(year):
            frame = frame[frame["week"] < int(week)]
        if "penalty_no_play" in frame.columns:
            frame = frame[frame["penalty_no_play"] != True]  # noqa: E712
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    plays = pd.concat(frames, ignore_index=True)
    if "id_play" in plays.columns:
        plays = plays.drop_duplicates("id_play")
    label = plays["play_type"].astype(str)
    kneel = plays["play_text"].astype(str).str.contains("kneel", case=False, na=False) if "play_text" in plays.columns else False
    kind = np.full(len(plays), "other", dtype=object)
    kind[label.isin(_PASS).to_numpy()] = "pass"
    kind[label.isin(_RUN).to_numpy() & ~np.asarray(kneel)] = "run"
    kind[label.isin(_PUNT).to_numpy()] = "punt"
    kind[label.isin(_FG).to_numpy()] = "field_goal"
    plays["kind"] = kind
    codes = {str(name): school_code(str(name)) for name in plays["pos_team"].dropna().unique()}
    plays["posteam"] = plays["pos_team"].map(lambda name: codes.get(str(name), ""))
    plays = plays[plays["posteam"].astype(str).str.len() > 0]
    plays["passer"] = _pick(plays, ("passer_player_name", "completion_player", "sack_taken_player", "interception_thrown_player", "incompletion_player"))
    plays["rusher"] = _pick(plays, ("rush_player", "rusher_player_name"))
    plays["receiver"] = _pick(plays, ("reception_player", "target_player", "receiver_player_name"))
    gained = pd.to_numeric(plays["yards_gained"], errors="coerce")
    sack = label.eq("Sack")
    complete = label.isin(["Pass Reception", "Passing Touchdown"])
    interception = label.isin(["Interception Return", "Interception Return Touchdown"])
    plays["passing_yards"] = gained.where(~sack, gained.fillna(0))
    plays["receiving_yards"] = gained.where(complete, 0).fillna(0)
    plays["air_yards"] = np.where(label.isin(_PASS) & ~sack, 0.0, np.nan)
    plays["yards_after_catch"] = np.where(complete, gained.fillna(0), np.where(label.isin(_PASS) & ~sack, 0.0, np.nan))
    plays["pass_attempt"] = label.isin(_PASS).astype(float)
    plays["rush_attempt"] = (plays["kind"] == "run").astype(float)
    plays["complete_pass"] = complete.astype(float)
    plays["interception"] = interception.astype(float)
    plays["sack"] = sack.astype(float)
    fumble = pd.to_numeric(plays["fumble_vec"], errors="coerce").fillna(0) if "fumble_vec" in plays.columns else 0
    changed = pd.to_numeric(plays["change_of_poss"], errors="coerce").fillna(0) if "change_of_poss" in plays.columns else 0
    plays["fumble_lost"] = ((fumble == 1) & (changed == 1)).astype(float)
    made = plays["fg_made"].astype(str).str.lower().isin(["true", "1", "1.0"]) if "fg_made" in plays.columns else label.eq("Field Goal Good")
    plays["field_goal_result"] = np.where(label.isin(_FG) & made, "made", np.where(label.isin(_FG), "missed", ""))
    distance = pd.to_numeric(plays["yds_fg"], errors="coerce") if "yds_fg" in plays.columns else np.nan
    plays["kick_distance"] = distance
    plays["ydstogo"] = pd.to_numeric(plays["distance"], errors="coerce")
    plays["yardline_100"] = pd.to_numeric(plays["yards_to_goal"], errors="coerce")
    plays["qtr"] = pd.to_numeric(plays["period"], errors="coerce")
    plays["score_differential"] = pd.to_numeric(plays["pos_score_diff"], errors="coerce")
    plays["down"] = pd.to_numeric(plays["down"], errors="coerce")
    plays["play_type"] = plays["kind"]
    plays["yards_gained"] = gained
    return plays


def _player_weeks(plays: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    if plays.empty:
        return pd.DataFrame(), {}
    display: dict[str, str] = {}
    seen: dict[str, int] = {}

    def identify(raw: object) -> str:
        text = str(raw or "").strip()
        key = norm_name(text)
        if " " not in key or len(key) < 5:
            return ""
        seen[key] = seen.get(key, 0) + 1
        if key not in display or seen[key] == 1:
            display[key] = text
        return key

    rows: list[pd.DataFrame] = []
    work = plays.copy()
    gained = pd.to_numeric(work["yards_gained"], errors="coerce")
    to_goal = pd.to_numeric(work["yardline_100"], errors="coerce")
    scored = gained + 0.01 >= to_goal
    work["pass_td"] = ((work["play_type"] == "pass") & work["complete_pass"].eq(1) & scored).astype(float)
    work["rush_td"] = ((work["play_type"] == "run") & scored).astype(float)

    passes = work[work["play_type"] == "pass"].copy()
    if not passes.empty:
        passes["player_id"] = passes["passer"].map(identify)
        passes = passes[passes["player_id"] != ""]
        rows.append(passes.groupby(["season", "week", "posteam", "player_id"], dropna=True).agg(
            pass_att=("pass_attempt", "sum"),
            pass_cmp=("complete_pass", "sum"),
            pass_yds=("passing_yards", "sum"),
            pass_tds=("pass_td", "sum"),
            pass_ints=("interception", "sum"),
            sacks=("sack", "sum"),
        ).reset_index())
    rushes = work[work["play_type"] == "run"].copy()
    if not rushes.empty:
        rushes["player_id"] = rushes["rusher"].map(identify)
        rushes = rushes[rushes["player_id"] != ""]
        rows.append(rushes.groupby(["season", "week", "posteam", "player_id"], dropna=True).agg(
            rush_att=("rush_attempt", "sum"),
            rush_yds=("yards_gained", "sum"),
            rush_tds=("rush_td", "sum"),
        ).reset_index())
    thrown = work[(work["play_type"] == "pass") & work["sack"].eq(0)].copy()
    if not thrown.empty:
        thrown["player_id"] = thrown["receiver"].map(identify)
        thrown = thrown[thrown["player_id"] != ""]
        rows.append(thrown.groupby(["season", "week", "posteam", "player_id"], dropna=True).agg(
            targets=("pass_attempt", "sum"),
            receptions=("complete_pass", "sum"),
            rec_yds=("receiving_yards", "sum"),
            rec_tds=("pass_td", "sum"),
        ).reset_index())
    if not rows:
        return pd.DataFrame(), {}
    out = rows[0]
    for extra in rows[1:]:
        out = out.merge(extra, on=["season", "week", "posteam", "player_id"], how="outer")
    for col in ("pass_att", "pass_cmp", "pass_yds", "pass_tds", "pass_ints", "sacks", "rush_att", "rush_yds", "rush_tds", "targets", "receptions", "rec_yds", "rec_tds"):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["player_id"] = out["player_id"].astype(str)
    return out, display


def _apply_injuries(players: dict[str, dict[str, Any]]) -> None:
    listed: dict[tuple[str, str], float] = {}
    out: set[tuple[str, str]] = set()
    path = DATA_DIR / "espn_cache" / "injuries_cfb.json"
    rows: list[dict] = []
    if path.exists():
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
        except (OSError, json.JSONDecodeError):
            rows = []
    if not rows:
        try:
            import sys

            root = str(Path(__file__).resolve().parents[2])
            if root not in sys.path:
                sys.path.insert(0, root)
            from nfl_open_prop.injuries import load_injuries

            rows = load_injuries("cfb")
        except Exception:
            rows = []
    for row in rows:
        raw = row.get("availability")
        if raw is None:
            continue
        availability = float(raw)
        key = (norm_name(str(row.get("name") or "")), school_code(str(row.get("team") or "")))
        if availability <= 0.25:
            out.add(key)
        else:
            listed[key] = availability
    desk_avail: dict[str, float] = {}
    desk_path = Path(__file__).resolve().parents[2] / "prop_desk_site" / "desk.json"
    if desk_path.exists():
        try:
            sport = json.loads(desk_path.read_text(encoding="utf-8"))["sports"]["cfb"]
            for player in sport.get("players") or []:
                raw = player.get("availability")
                if raw is None:
                    continue
                desk_avail[norm_name(str(player.get("name") or ""))] = float(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            desk_avail = {}
    for player in players.values():
        name = norm_name(player["name"])
        key = (name, player["team"])
        if name in desk_avail and desk_avail[name] < 0.99:
            availability = desk_avail[name]
        elif key in out:
            availability = 0.0
        elif key in listed:
            availability = listed[key]
        else:
            continue
        if availability <= 0.25:
            player["play"] = 0.0
            player["out"] = True
        else:
            player["play"] = float(np.clip(availability, 0.0, 0.98))


def fit(year: int, week: int, plays: pd.DataFrame | None = None) -> dict[str, Any]:
    prepared = plays if plays is not None else _prepare(year, week)
    weeks, names = _player_weeks(prepared)
    players = _ratings(weeks, names)
    _apply_injuries(players)
    return {"players": players, "year": year, "week": week}


def _games(year: int, week: int) -> list[tuple[str, str]]:
    desk_path = Path(__file__).resolve().parents[2] / "prop_desk_site" / "desk.json"
    if desk_path.exists():
        try:
            sport = json.loads(desk_path.read_text(encoding="utf-8"))["sports"]["cfb"]
            if int(sport.get("season") or 0) == int(year) and int(sport.get("week") or 0) == int(week):
                pairs = []
                seen = set()
                for player in sport.get("players") or []:
                    home = str(player.get("home") or "")
                    away = str(player.get("away") or "")
                    key = (school_code(away), school_code(home))
                    if not home or not away or key in seen or "" in key:
                        continue
                    seen.add(key)
                    pairs.append((home, away))
                if pairs:
                    return pairs
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    from .cfbd_games import list_games_for_display_week

    return [(str(game.get("home") or ""), str(game.get("away") or "")) for game in list_games_for_display_week(year, week)]


def project_week(year: int, week: int, *, n_sims: int, n_keep: int) -> tuple[list[dict[str, Any]], np.ndarray]:
    from .nfl_play_engine import _stash, fit_plays, simulate_game

    plays = _prepare(year, week)
    engine = fit_plays(plays, year=year)
    model = fit(year, week, plays)
    by_team: dict[str, list[dict[str, Any]]] = {}
    for player in model["players"].values():
        by_team.setdefault(str(player["team"]), []).append(player)
    z = 1.2815515655446004
    series: list[np.ndarray] = []
    index: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rng = np.random.default_rng(int(year) * 100 + int(week) + 17)
    for home_name, away_name in _games(year, week):
        home = school_code(home_name)
        away = school_code(away_name)
        home_roster = by_team.get(home) or []
        away_roster = by_team.get(away) or []
        if not home_roster and not away_roster:
            print(f"cfb sim skipped {away_name} at {home_name}", flush=True)
            continue
        print(f"cfb sim {away_name} at {home_name}", flush=True)
        game_rng = np.random.default_rng(rng.integers(0, 2**31 - 1))
        simulated = simulate_game(
            engine, home_roster, away_roster, n=n_sims, rng=game_rng, max_steps=_MAX_STEPS,
        )
        _stash(simulated, home_roster + away_roster, series, index, seen, z, n_keep, game_rng)
    draws = np.vstack(series) if series else np.zeros((0, n_keep), dtype=np.float32)
    return index, draws


def _cache_path(year: int, week: int) -> Path:
    return CACHE / f"sim_{year}_w{week}.npz"


def _meta_path(year: int, week: int) -> Path:
    return CACHE / f"sim_{year}_w{week}.json"


def simulate_week(year: int, week: int, *, n_sims: int = N_SIMS, refresh: bool = False) -> dict[str, Any]:
    """Cache one week of college play-by-play simulations."""
    meta_path = _meta_path(year, week)
    data_path = _cache_path(year, week)
    if meta_path.exists() and data_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if int(meta.get("n_sims") or 0) == int(n_sims) and int(meta.get("model") or 0) == MODEL_VERSION:
            draws = np.load(data_path)["draws"]
            return {"meta": meta, "draws": draws}
    index, draws = project_week(year, week, n_sims=int(n_sims), n_keep=KEEP_DRAWS)
    meta = {"year": year, "week": week, "n_sims": int(n_sims), "model": MODEL_VERSION, "rows": index}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(data_path, draws=draws)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return {"meta": meta, "draws": draws}
