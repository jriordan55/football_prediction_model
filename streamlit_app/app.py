"""College Football Betting Labs — professional B2C dashboard."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from lib.app_filters import init_app_filters
from lib.config import load_env
from lib.sport_context import SPORT_CFB, get_sport, get_sport_config, init_sport
from lib.styling import apply_theme, render_primary_nav, render_shell_header
from views import game_projections, live, market_moves, player_projections, sharp_lines, yard_projections

load_env()
init_sport()
init_app_filters()

_sport_cfg = get_sport_config()
_yr, _wk = _sport_cfg["default_year"], _sport_cfg["default_week"]

st.set_page_config(
    page_title=str(_sport_cfg["page_title"]),
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

apply_theme()

PAGES = {
    "Game Projections": game_projections,
    "Live": live,
    "Player Projections": player_projections,
    "Yard Projections": yard_projections,
    "Market Moves": market_moves,
    "Sharp Lines": sharp_lines,
}

PRIMARY_NAV = [
    ("Game Projections", "Game Projections"),
    ("Live", "Live"),
    ("Player Projections", "Player Projections"),
    *([("Yard Projections", "Yard Projections")] if get_sport() == SPORT_CFB else []),
    ("Market Moves", "Market Moves"),
    ("Sharp Lines", "Sharp Lines"),
]

render_shell_header(week=int(_wk), year=int(_yr), markets="2K+")

st.markdown('<div class="bo-rz-primary-nav">', unsafe_allow_html=True)
page_key = render_primary_nav(PRIMARY_NAV, active="Game Projections", key=f"bo_page_{init_sport()}")
st.markdown("</div>", unsafe_allow_html=True)

try:
    PAGES[page_key].render()
except Exception as exc:
    from lib.styling import callout

    callout("This view couldn't load right now. Try refreshing the page.", "warn")
    st.exception(exc)
