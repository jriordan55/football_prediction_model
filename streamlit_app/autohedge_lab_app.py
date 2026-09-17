"""Standalone Streamlit app — Autohedge & TKO staking lab."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from views.autohedge_lab import render

st.set_page_config(
    page_title="Autohedge & TKO Lab",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render()
