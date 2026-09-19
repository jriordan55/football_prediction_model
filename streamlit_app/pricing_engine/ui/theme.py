"""Pricing page CSS — monochrome black/white, no sport accent tints."""
from __future__ import annotations

import html

import streamlit as st

from lib.styling import FL_CSS


def pricing_monochrome_css() -> str:
    return """
<style>
:root {
  --bo-bg: #000000;
  --bo-bg-elevated: #0a0a0a;
  --bo-surface: #0d0d0d;
  --bo-surface-2: #141414;
  --bo-border: rgba(255, 255, 255, 0.12);
  --bo-border-strong: rgba(255, 255, 255, 0.22);
  --bo-text: #ffffff;
  --bo-muted: #d4d4d4;
  --bo-dim: #a3a3a3;
  --bo-purple: #ffffff;
  --bo-purple-deep: #e5e5e5;
  --bo-purple-glow: rgba(255, 255, 255, 0.08);
  --bo-accent-rgb: 255, 255, 255;
  --bo-accent-light-rgb: 255, 255, 255;
  --bo-gradient-a: transparent;
  --bo-gradient-b: transparent;
  --bo-green: #ffffff;
  --bo-danger: #ffffff;
  --bo-orange: #ffffff;
  --bo-blue: #ffffff;
}

html, body, .stApp, [class*="css"] {
  color-scheme: dark;
}

.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
.main,
section.main > div {
  background: #000000 !important;
}

.stApp {
  background: #000000 !important;
}

[data-testid="stStatusWidget"],
.stStatusWidget,
div[data-testid="stToolbarActions"] {
  display: none !important;
}

.stButton > button {
  background: #111111 !important;
  border: 1px solid rgba(255, 255, 255, 0.18) !important;
  color: #ffffff !important;
  box-shadow: none !important;
}

.stButton > button:hover {
  border-color: rgba(255, 255, 255, 0.35) !important;
  box-shadow: none !important;
}

.stButton > button[kind="primary"] {
  background: #000000 !important;
  color: #ffffff !important;
  border-color: rgba(255, 255, 255, 0.35) !important;
}

.bo-rz-header-row [data-testid="column"]:nth-child(3) .stButton > button,
.bo-rz-header-row [data-testid="column"]:nth-child(4) .stButton > button {
  background: #000000 !important;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
  color: #737373 !important;
  opacity: 1 !important;
  box-shadow: none !important;
}

.bo-rz-header-row [data-testid="column"]:nth-child(3) .stButton > button[kind="primary"],
.bo-rz-header-row [data-testid="column"]:nth-child(4) .stButton > button[kind="primary"] {
  background: #000000 !important;
  border: 1px solid rgba(255, 255, 255, 0.35) !important;
  color: #ffffff !important;
}

.bo-pe-prop-tabs .stButton > button {
  background: #000000 !important;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
  color: #737373 !important;
  box-shadow: none !important;
  font-weight: 600 !important;
}

.bo-pe-prop-tabs .stButton > button:hover {
  border-color: rgba(255, 255, 255, 0.28) !important;
  color: #ffffff !important;
}

.bo-pe-prop-tabs .stButton > button[kind="primary"] {
  background: #000000 !important;
  color: #ffffff !important;
  border-color: rgba(255, 255, 255, 0.35) !important;
}

.bo-pe-header {
  background: #0a0a0a !important;
  border-color: rgba(255, 255, 255, 0.12) !important;
}

.bo-pe-abbr {
  border-left-color: #ffffff !important;
  color: #ffffff !important;
}

.bo-pe-team.home .bo-pe-abbr {
  border-right-color: #ffffff !important;
}

.bo-pe-score.live span {
  color: #ffffff !important;
}

.bo-pe-board .bo-pe-model-price.bo-pe-edge-pos,
.bo-pe-board .bo-pe-model-price.bo-pe-edge-neg,
.bo-pe-edge-pos,
.bo-pe-edge-neg,
.bo-pe-roi.bo-pe-edge-pos,
.bo-pe-roi.bo-pe-edge-neg,
.bo-pe-result.hit,
.bo-pe-result.miss {
  color: #ffffff !important;
}

.bo-pe-result.hit,
.bo-pe-result.miss,
.bo-pe-result.push {
  background: rgba(255, 255, 255, 0.12) !important;
  color: #ffffff !important;
}

.bo-pe-props .bo-pp-table th,
.bo-pe-props .bo-pp-table td,
.bo-pe-table th,
.bo-pe-table td {
  text-align: center !important;
  vertical-align: middle !important;
}

.bo-pe-props .bo-pp-player,
.bo-pe-props .bo-pe-prop-player {
  text-align: center !important;
}

.bo-pe-player-centered {
  justify-content: center !important;
  align-items: center !important;
}

.bo-pe-props .bo-pp-player-text {
  text-align: center !important;
}

.bo-pe-props .bo-pp-name-row {
  justify-content: center !important;
}

.bo-pe-props .bo-pp-matchup-row {
  justify-content: center !important;
}

.bo-pe-pos,
.bo-pp-pos {
  color: #d4d4d4 !important;
}

.bo-pp-side-over,
.bo-pp-side-under,
.bo-pp-opp-tough,
.bo-pp-opp-soft,
.bo-pp-edge {
  color: #ffffff !important;
}

.bo-pe-odds-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex-wrap: wrap;
}

.bo-pe-liq {
  font-family: var(--bo-mono);
  font-size: 10px;
  font-weight: 500;
  color: #a3a3a3;
  white-space: nowrap;
}

.bo-pe-edge {
  font-family: var(--bo-mono);
  font-size: 13px;
  font-weight: 700;
  color: #ffffff;
}

.bo-pe-props .bo-pp-table tbody tr:hover td {
  background: rgba(255, 255, 255, 0.04) !important;
}
</style>
"""


def pricing_board_css() -> str:
    return """
<style>
.bo-pe-header {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: center;
  padding: 18px 20px; margin-bottom: 12px;
  border: 1px solid var(--bo-border); border-radius: 16px;
  background: #0a0a0a;
}
.bo-pe-team { display: flex; align-items: center; gap: 12px; }
.bo-pe-team.home { justify-content: flex-end; }
.bo-pe-team-meta.right { text-align: right; }
.bo-pe-logo { width: 52px; height: 52px; object-fit: contain; }
.bo-pe-abbr {
  font-size: 22px; font-weight: 800; letter-spacing: -0.02em; color: var(--bo-text);
  border-left: 3px solid #ffffff; padding-left: 8px;
}
.bo-pe-team.home .bo-pe-abbr { border-left: none; border-right: 3px solid #ffffff; padding-right: 8px; padding-left: 0; }
.bo-pe-name { font-size: 12px; color: var(--bo-muted); margin-top: 2px; max-width: 180px; }
.bo-pe-center { text-align: center; min-width: 140px; }
.bo-pe-score {
  font-family: var(--bo-mono); font-size: 28px; font-weight: 800; color: var(--bo-text);
  display: flex; align-items: center; justify-content: center; gap: 8px;
}
.bo-pe-score.proj span { color: var(--bo-text); }
.bo-pe-score.live span { color: var(--bo-text); }
.bo-pe-score-sep { color: var(--bo-dim); font-weight: 600; }
.bo-pe-score-lbl { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--bo-dim); margin-top: 2px; }
.bo-pe-kick { font-size: 12px; color: var(--bo-muted); margin-top: 6px; }
.bo-pe-status { font-size: 11px; color: var(--bo-muted); margin-top: 4px; font-weight: 600; }
.bo-pe-board { margin-bottom: 20px; border-radius: 16px; overflow: hidden; }
.bo-pe-table .bo-pe-section {
  padding: 8px 14px !important; font-size: 10px !important; font-weight: 700 !important;
  letter-spacing: 0.1em !important; text-transform: uppercase !important;
  color: var(--bo-dim) !important; background: var(--bo-surface-2) !important;
}
.bo-pe-table th,
.bo-pe-table td {
  text-align: center !important;
  vertical-align: middle !important;
}
.bo-pe-sel { font-weight: 600; color: var(--bo-text); }
.bo-pe-line { font-family: var(--bo-mono); color: var(--bo-muted); font-size: 12px; }
.bo-pe-proj {
  font-family: var(--bo-mono); font-size: 15px; font-weight: 700; color: var(--bo-text);
}
.bo-pe-model-price {
  font-family: var(--bo-mono); font-weight: 700; color: var(--bo-text);
}
.bo-pe-model-price.bo-pe-edge-pos { color: var(--bo-text); }
.bo-pe-model-price.bo-pe-edge-neg { color: var(--bo-text); }
.bo-pe-model-price.bo-pe-neutral { color: var(--bo-text); }
.bo-pe-book {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--bo-mono); font-size: 12px; color: var(--bo-text);
  background: var(--bo-surface-2); padding: 4px 8px; border-radius: 8px;
  border: 1px solid var(--bo-border);
}
.bo-pe-book-logo { width: 16px; height: 16px; border-radius: 3px; }
.bo-pe-game-select [data-baseweb="select"] > div {
  border-radius: 12px !important; min-height: 48px !important;
  border-color: var(--bo-border) !important;
  background: var(--bo-surface) !important;
  color: #ffffff !important;
}
.bo-pe-odds-cell { display: inline-flex; align-items: center; justify-content: center; gap: 6px; flex-wrap: wrap; }
.bo-pe-odds { font-family: var(--bo-mono); font-size: 15px; font-weight: 700; color: var(--bo-text); }
.bo-pe-empty { color: var(--bo-dim); }
.bo-pe-empty-block {
  padding: 24px; text-align: center; color: var(--bo-muted);
  border: 1px dashed var(--bo-border); border-radius: 12px; margin-bottom: 16px;
}
.bo-pe-prop-name { font-size: 15px; font-weight: 800; letter-spacing: 0.02em; color: var(--bo-text); }
.bo-pe-prop-match { font-size: 11px; color: var(--bo-muted); margin-top: 3px; }
.bo-pe-prop-line, .bo-pe-prop-proj, .bo-pe-prop-pick, .bo-pe-edge-cell {
  font-family: var(--bo-mono); font-size: 16px; font-weight: 700; color: var(--bo-text);
}
.bo-pe-props { margin-top: 8px; }
.bo-pe-prop-tabs { margin-bottom: 12px; }
.bo-pe-score.final span { color: var(--bo-text); font-weight: 800; }
.bo-pe-result {
  display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.06em;
  padding: 3px 8px; border-radius: 999px; text-transform: uppercase;
}
.bo-pe-result.hit { background: rgba(255, 255, 255, 0.12); color: #ffffff; }
.bo-pe-result.miss { background: rgba(255, 255, 255, 0.12); color: #ffffff; }
.bo-pe-result.push { background: rgba(255, 255, 255, 0.12); color: #a3a3a3; }
.bo-pe-actual, .bo-pe-roi { font-family: var(--bo-mono); font-size: 12px; color: var(--bo-muted); }
.bo-pe-roi.bo-pe-edge-pos { color: #ffffff; }
.bo-pe-roi.bo-pe-edge-neg { color: #ffffff; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.player-col { width: 24%; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.line-col { width: 8%; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.proj-col { width: 8%; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.pick-col { width: 10%; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.edge-col { width: 8%; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.over-col { width: 14%; }
.bo-pe-props .bo-pp-board-compact .bo-pp-table col.under-col { width: 14%; }
.bo-pe-props .bo-pe-edge.bo-pe-edge-pos { color: #4ade80 !important; }
.bo-pe-props .bo-pe-edge.bo-pe-edge-neg { color: #f87171 !important; }
.bo-pe-props .bo-pe-edge.bo-pe-edge-neutral { color: #a3a3a3 !important; }

/* Play-by-play log — team rows, DraftKings only */
.bo-pbp-log { margin-top: 4px; margin-bottom: 20px; }
.bo-pbp-log-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 10px;
}
.bo-pbp-log-title {
  font-size: 15px; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--bo-text);
}
.bo-pbp-book {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--bo-muted); padding: 6px 10px; border-radius: 999px;
  border: 1px solid var(--bo-border); background: var(--bo-surface);
}
.bo-pbp-book-logo { width: 16px; height: 16px; border-radius: 3px; }
.bo-pbp-live-tag {
  font-size: 11px; color: var(--bo-dim); margin: 0 0 12px 0;
}
.bo-pbp-card {
  border: 1px solid var(--bo-border); border-radius: 14px;
  background: var(--bo-surface); margin-bottom: 12px; overflow: hidden;
}
.bo-pbp-card-head {
  padding: 10px 14px; border-bottom: 1px solid var(--bo-border);
  background: var(--bo-surface-2);
}
.bo-pbp-situation {
  font-family: var(--bo-mono); font-size: 11px; font-weight: 700;
  letter-spacing: 0.04em; color: var(--bo-muted); text-transform: uppercase;
}
.bo-pbp-play-text {
  margin: 0; padding: 12px 14px; font-size: 13px; line-height: 1.45;
  color: var(--bo-text); border-bottom: 1px solid var(--bo-border);
}
.bo-pbp-table { margin: 0 !important; border: none !important; border-radius: 0 !important; }
.bo-pbp-table thead th {
  font-size: 9px !important; letter-spacing: 0.08em !important;
  text-transform: uppercase !important; color: var(--bo-dim) !important;
  padding: 8px 6px !important; background: transparent !important;
}
.bo-pbp-table tbody td {
  padding: 10px 6px !important; font-size: 12px !important;
  border-top: 1px solid rgba(255, 255, 255, 0.06) !important;
}
.bo-pbp-table tbody tr:last-child td { padding-bottom: 12px !important; }
.bo-pbp-team { text-align: left !important; min-width: 140px; }
.bo-pbp-team-inner {
  display: flex; align-items: center; gap: 10px; justify-content: flex-start;
}
.bo-pbp-logo {
  width: 28px; height: 28px; object-fit: contain; flex-shrink: 0;
}
.bo-pbp-logo.bo-logo-fallback { width: 28px; height: 28px; }
.bo-pbp-team-name {
  font-size: 13px; font-weight: 700; color: var(--bo-text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 160px;
}
.bo-pbp-mono { font-family: var(--bo-mono); color: var(--bo-muted); }
.bo-pbp-score { font-size: 15px !important; font-weight: 800 !important; color: var(--bo-text) !important; }
.bo-pbp-odds { font-weight: 700 !important; color: var(--bo-text) !important; }
.bo-pbp-odds-cell, .bo-pbp-line-cell {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
}
.bo-pbp-cell-book { width: 14px; height: 14px; border-radius: 2px; flex-shrink: 0; opacity: 0.9; }
.bo-pbp-odds-val { font-family: var(--bo-mono); }
.bo-pbp-total { vertical-align: middle !important; }
.bo-pbp-tot-stack {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 6px; min-height: 52px;
}
.bo-pbp-tot-over, .bo-pbp-tot-under {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  font-family: var(--bo-mono); line-height: 1.2;
}
.bo-pbp-tot-over { font-weight: 700; color: var(--bo-text); }
.bo-pbp-tot-under { font-size: 11px; color: var(--bo-muted); }
.bo-pbp-tot-line { white-space: nowrap; }
.bo-pbp-table col.team-col { width: 18%; }
.bo-pbp-table col.score-col { width: 7%; }
.bo-pbp-table col.spr-col { width: 9%; }
.bo-pbp-table col.tot-col { width: 9%; }
.bo-pbp-table col.odds-col { width: 10%; }
.bo-pbp-table col.pct-col { width: 8%; }
</style>
"""


def apply_pricing_theme() -> None:
    """Monochrome pricing shell — no CFB/NFL blue/red sport tint."""
    st.markdown(FL_CSS + pricing_monochrome_css(), unsafe_allow_html=True)


def esc(val: str | None) -> str:
    return html.escape(str(val or ""))
