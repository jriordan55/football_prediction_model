"""Sport selection — CFB vs NFL shared config for Bettor Odds."""
from __future__ import annotations

import os
from typing import Any

import streamlit as st

from pathlib import Path

SPORT_CFB = "cfb"
SPORT_NFL = "nfl"

_THEME_CFB = {
    "accent": "#60a5fa",
    "accent_deep": "#2563eb",
    "accent_light": "#93c5fd",
    "accent_rgb": "37, 99, 235",
    "accent_light_rgb": "147, 197, 253",
    "accent_glow": "rgba(37, 99, 235, 0.45)",
    "gradient_a": "rgba(37, 99, 235, 0.22)",
    "gradient_b": "rgba(147, 197, 253, 0.08)",
    "border_soft": "rgba(147, 197, 253, 0.35)",
    "border_mid": "rgba(147, 197, 253, 0.4)",
    "border_strong": "rgba(147, 197, 253, 0.45)",
    "chip_bg": "rgba(37, 99, 235, 0.14)",
    "btn_start": "#1d4ed8",
    "btn_end": "#3b82f6",
}

_THEME_NFL = {
    "accent": "#f87171",
    "accent_deep": "#dc2626",
    "accent_light": "#fca5a5",
    "accent_rgb": "220, 38, 38",
    "accent_light_rgb": "252, 165, 165",
    "accent_glow": "rgba(220, 38, 38, 0.45)",
    "gradient_a": "rgba(220, 38, 38, 0.22)",
    "gradient_b": "rgba(252, 165, 165, 0.08)",
    "border_soft": "rgba(252, 165, 165, 0.35)",
    "border_mid": "rgba(252, 165, 165, 0.4)",
    "border_strong": "rgba(252, 165, 165, 0.45)",
    "chip_bg": "rgba(220, 38, 38, 0.14)",
    "btn_start": "#b91c1c",
    "btn_end": "#ef4444",
}

SPORT_CONFIG: dict[str, dict[str, Any]] = {
    SPORT_CFB: {
        "id": SPORT_CFB,
        "label": "CFB",
        "page_title": "CFB",
        "sport_key": "americanfootball_ncaaf",
        "onyx_league": "NCAAF",
        "optic_league": "NCAAF",
        "espn_path": "college-football",
        "espn_referer": "https://www.espn.com/college-football/",
        "espn_search_league": "college-football",
        "default_year": int(os.getenv("CFB_SEASON", "2026")),
        "default_week": int(os.getenv("CFB_WEEK", "1")),
        "min_week": 0,
        "max_week": 16,
        "has_week0": True,
        "theme": _THEME_CFB,
        "games_cache_prefix": "cfbd_games",
        "slate_cache_tag": "",
        "odds_cache_tag": "ncaaf",
        "csv_log_tag": "cfb",
    },
    SPORT_NFL: {
        "id": SPORT_NFL,
        "label": "NFL",
        "page_title": "NFL",
        "sport_key": "americanfootball_nfl",
        "onyx_league": "NFL",
        "optic_league": "NFL",
        "espn_path": "nfl",
        "espn_referer": "https://www.espn.com/nfl/",
        "espn_search_league": "nfl",
        "default_year": int(os.getenv("NFL_SEASON", "2026")),
        "default_week": int(os.getenv("NFL_WEEK", "1")),
        "min_week": 1,
        "max_week": 18,
        "has_week0": False,
        "theme": _THEME_NFL,
        "games_cache_prefix": "nfl_games",
        "slate_cache_tag": "_nfl",
        "odds_cache_tag": "nfl",
        "csv_log_tag": "nfl",
    },
}


def _filters_key(sport: str) -> str:
    return f"app_filters_{sport}"


def _save_filters(sport: str) -> None:
    st.session_state[_filters_key(sport)] = {
        "year": int(st.session_state.get("app_year", 2026)),
        "week": int(st.session_state.get("app_week", 1)),
    }


def _load_filters(sport: str) -> None:
    saved = st.session_state.get(_filters_key(sport))
    cfg = get_sport_config(sport)
    year = int(cfg["default_year"])
    week = int(cfg["default_week"])
    if saved:
        year = int(saved.get("year", year))
        week = int(saved.get("week", week))

    try:
        from lib.games import display_week_complete
        from lib.week_availability import current_display_week

        rolled = int(current_display_week(year, sport))
        if not saved:
            week = rolled
        elif display_week_complete(year, week, sport=sport) and week < rolled:
            week = rolled
    except Exception:
        if not saved:
            _, week = default_season_week(sport)

    st.session_state.app_year = year
    st.session_state.app_week = week


def init_sport() -> str:
    """Default CFB on first load; deep-link ?sport= only applies once."""
    if "app_sport" not in st.session_state:
        sport = SPORT_CFB
        if hasattr(st, "query_params"):
            raw = st.query_params.get("sport")
            if isinstance(raw, list):
                raw = raw[0] if raw else None
            if raw in (SPORT_CFB, SPORT_NFL):
                sport = raw
        st.session_state.app_sport = sport
    return str(st.session_state.app_sport)


def get_sport() -> str:
    return init_sport()


def resolve_sport(sport: str | None = None) -> str:
    """Normalize sport id — always one of cfb/nfl."""
    sid = sport or get_sport()
    return sid if sid in SPORT_CONFIG else SPORT_CFB


def cache_sport() -> str:
    """Include in every @st.cache_data signature so CFB/NFL never share entries."""
    return get_sport()


def sport_disk_tag(sport: str | None = None) -> str:
    """Filesystem segment for sport-scoped disk caches."""
    return resolve_sport(sport)


def get_sport_config(sport: str | None = None) -> dict[str, Any]:
    sid = sport or get_sport()
    return SPORT_CONFIG.get(sid, SPORT_CONFIG[SPORT_CFB])


def week_options(sport: str | None = None) -> list[int]:
    cfg = get_sport_config(sport)
    return list(range(int(cfg["min_week"]), int(cfg["max_week"]) + 1))


def default_season_week(sport: str | None = None) -> tuple[int, int]:
    cfg = get_sport_config(sport)
    return int(cfg["default_year"]), int(cfg["default_week"])


def _reset_sport_ui_state() -> None:
    st.session_state["bo_page"] = "Game Projections"
    drop_prefixes = ("gp_", "pp_", "yp_", "live_", "sl_", "mm_", "wx_", "sh_")
    for key in list(st.session_state.keys()):
        if key.startswith(drop_prefixes):
            del st.session_state[key]
    for key in ("gp_selected_matchup", "pp_selected_row", "live_selected_event"):
        st.session_state.pop(key, None)


def set_sport(sport: str) -> None:
    sport = sport if sport in SPORT_CONFIG else SPORT_CFB
    prev = st.session_state.get("app_sport")
    if prev == sport:
        return
    if prev:
        _save_filters(str(prev))
    st.session_state.app_sport = sport
    _load_filters(sport)
    _reset_sport_ui_state()
    if hasattr(st, "query_params"):
        try:
            st.query_params["sport"] = sport
        except Exception:
            pass
    try:
        from .team_logos import clear_logo_caches

        clear_logo_caches()
    except Exception:
        pass
    try:
        st.cache_data.clear()
    except Exception:
        pass
    try:
        from .game_board import _board_inputs, _scoreboard_cache_token_cached

        _scoreboard_cache_token_cached.cache_clear()
        _board_inputs.cache_clear()
    except Exception:
        pass


def sport_key(sport: str | None = None) -> str:
    return str(get_sport_config(sport)["sport_key"])


def onyx_league(sport: str | None = None) -> str:
    return str(get_sport_config(sport)["onyx_league"])


def espn_site_url(sport: str | None = None) -> str:
    path = get_sport_config(sport)["espn_path"]
    return f"https://site.web.api.espn.com/apis/site/v2/sports/football/{path}"


def espn_core_url(sport: str | None = None) -> str:
    path = get_sport_config(sport)["espn_path"]
    return f"https://sports.core.api.espn.com/v2/sports/football/leagues/{path}"


def espn_gamelog_url(athlete_id: str, sport: str | None = None) -> str:
    path = get_sport_config(sport)["espn_path"]
    return (
        f"https://site.web.api.espn.com/apis/common/v3/sports/football/{path}/athletes/{athlete_id}"
    )


def espn_athlete_gamelog_url(athlete_id: str, sport: str | None = None) -> str:
    path = get_sport_config(sport)["espn_path"]
    return (
        f"https://site.web.api.espn.com/apis/common/v3/sports/football/{path}/athletes/{athlete_id}/gamelog"
    )


def espn_team_api_url(team_id: str, resource: str, sport: str | None = None) -> str:
    path = get_sport_config(sport)["espn_path"]
    return f"https://site.api.espn.com/apis/site/v2/sports/football/{path}/teams/{team_id}/{resource}"


def slate_cache_paths(*, year: int, week: int, sport: str | None = None) -> list[str]:
    cfg = get_sport_config(sport)
    tag = str(cfg["slate_cache_tag"])
    return [
        f"onyx_slate_{year}_w{week}{tag}.json",
        f"onyx_slate_{year}_w{week}{tag}_500.json",
        f"onyx_slate_{year}_w{week}{tag}_5000.json",
    ]


def odds_cache_paths(sport: str | None = None) -> tuple[str, str]:
    tag = get_sport_config(sport)["odds_cache_tag"]
    return f"sport_odds_lines_{tag}.json", f"sport_odds_meta_{tag}.json"


def multi_book_lines_cache_paths(sport: str | None = None) -> tuple[str, str]:
    tag = get_sport_config(sport)["odds_cache_tag"]
    return f"multi_book_game_lines_{tag}.json", f"multi_book_game_lines_meta_{tag}.json"


def multi_book_props_cache_paths(sport: str | None = None) -> tuple[str, str]:
    tag = get_sport_config(sport)["odds_cache_tag"]
    return f"multi_book_props_{tag}.json", f"multi_book_props_meta_{tag}.json"


def projection_grades_filename(sport: str | None = None) -> str:
    tag = get_sport_config(sport)["csv_log_tag"]
    return f"projection_grades_{tag}.csv"


def opener_snapshot_path(sport: str | None = None) -> Path:
    from .config import DATA_DIR

    tag = get_sport_config(sport)["odds_cache_tag"]
    return Path(DATA_DIR) / f"last_odds_snapshot_{tag}.json"
