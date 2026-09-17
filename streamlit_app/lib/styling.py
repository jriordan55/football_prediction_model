"""Bettor Odds / Football Labs UI for Streamlit."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

FL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&family=Press+Start+2P&display=swap');

:root {
  --bo-bg: #050508;
  --bo-bg-elevated: #0c0c12;
  --bo-surface: #111118;
  --bo-surface-2: #16161f;
  --bo-border: rgba(255, 255, 255, 0.07);
  --bo-border-strong: rgba(255, 255, 255, 0.12);
  --bo-text: #fafafa;
  --bo-muted: #a1a1aa;
  --bo-dim: #71717a;
  --bo-purple: #60a5fa;
  --bo-purple-deep: #2563eb;
  --bo-purple-glow: rgba(37, 99, 235, 0.45);
  --bo-accent-rgb: 37, 99, 235;
  --bo-accent-light-rgb: 147, 197, 253;
  --bo-gradient-a: rgba(37, 99, 235, 0.22);
  --bo-gradient-b: rgba(147, 197, 253, 0.08);
  --bo-green: #4ade80;
  --bo-green-dim: rgba(74, 222, 128, 0.12);
  --bo-orange: #fb923c;
  --bo-danger: #f87171;
  --bo-blue: #60a5fa;
  --bo-mono: "JetBrains Mono", ui-monospace, monospace;
  --bo-sans: "Inter", system-ui, sans-serif;
}

html, body, .stApp, [class*="css"] {
  font-family: var(--bo-sans) !important;
  color: var(--bo-text) !important;
  color-scheme: dark;
}

.stApp {
  background:
    radial-gradient(ellipse 90% 60% at 50% -30%, var(--bo-gradient-a), transparent 50%),
    radial-gradient(ellipse 50% 40% at 100% 0%, var(--bo-gradient-b), transparent 40%),
    var(--bo-bg) !important;
}

header[data-testid="stHeader"], footer, #MainMenu, .stDeployButton { visibility: hidden !important; height: 0 !important; }

/* Hide Streamlit execution status bar (Running _function_name…) */
[data-testid="stStatusWidget"],
.stStatusWidget,
div[data-testid="stToolbarActions"],
.viewerBadge_container__ {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  overflow: hidden !important;
}

.block-container {
  padding: 0 28px 48px !important;
  max-width: 1180px !important;
}

section[data-testid="stSidebar"] { display: none !important; }
[data-testid="stSidebarCollapsedControl"] { display: none !important; }
section.main > div { max-width: 1180px; }

/* Force readable markdown */
.main h1, .main h2, .main h3, .main h4, .main h5, .main h6,
.main p, .main span, .main label, .main li,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
.stMarkdown, .stCaption { color: var(--bo-text) !important; }

.stCaption, .fl-section-sub, .fl-mono { color: var(--bo-muted) !important; }

/* Hide default alerts — use bo-callout */
[data-testid="stAlert"] { display: none !important; }

.stButton > button {
  background: linear-gradient(135deg, var(--bo-purple-deep), var(--bo-purple)) !important;
  color: #fff !important;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.35) !important;
  border-radius: 12px !important;
  font-weight: 600 !important;
  font-size: 14px !important;
  padding: 0.65rem 1.25rem !important;
  box-shadow: 0 0 32px rgba(var(--bo-accent-rgb), 0.25) !important;
  transition: transform 0.15s, box-shadow 0.15s !important;
}
.stButton > button:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 0 40px rgba(var(--bo-accent-rgb), 0.4) !important;
  border-color: rgba(var(--bo-accent-light-rgb), 0.55) !important;
  color: #fff !important;
}

.stSelectbox > div > div,
.stNumberInput input, .stTextInput input, .stMultiSelect > div > div,
.stMultiSelect [data-baseweb="select"] {
  background: var(--bo-bg-elevated) !important;
  border: 1px solid var(--bo-border) !important;
  color: var(--bo-text) !important;
  border-radius: 10px !important;
  min-height: 42px !important;
}
.stMultiSelect [data-baseweb="select"] > div,
.stMultiSelect [data-baseweb="select"] span,
.stMultiSelect [data-baseweb="select"] input,
.stMultiSelect [data-baseweb="tag"] {
  background: transparent !important;
  color: var(--bo-text) !important;
  -webkit-text-fill-color: var(--bo-text) !important;
  font-size: 14px !important;
  font-weight: 500 !important;
}
.stMultiSelect [data-baseweb="tag"] {
  background: rgba(var(--bo-accent-rgb), 0.22) !important;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.35) !important;
  border-radius: 6px !important;
}
.stMultiSelect [data-baseweb="tag"] span { color: var(--bo-purple) !important; }
.stMultiSelect svg { fill: var(--bo-muted) !important; }
div[data-testid="stMultiSelect"] label,
.stMultiSelect label {
  font-size: 11px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.08em !important;
  color: var(--bo-dim) !important;
  margin-bottom: 4px !important;
}
.bo-pp-filter-bar {
  margin: 0 0 16px;
  padding: 14px 16px;
  background: var(--bo-surface);
  border: 1px solid var(--bo-border);
  border-radius: 14px;
}
.stSelectbox [data-baseweb="select"] {
  background: transparent !important;
}
.stSelectbox [data-baseweb="select"] > div,
.stSelectbox [data-baseweb="select"] span,
.stSelectbox [data-baseweb="select"] input {
  color: var(--bo-text) !important;
  -webkit-text-fill-color: var(--bo-text) !important;
  font-size: 14px !important;
  font-weight: 500 !important;
}
.stSelectbox [data-baseweb="select"] svg {
  fill: var(--bo-muted) !important;
}

/* Selectbox dropdown popover — dark theme + scrollable list */
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div {
  background: var(--bo-surface) !important;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.32) !important;
  border-radius: 12px !important;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55), 0 0 28px rgba(var(--bo-accent-rgb), 0.14) !important;
  overflow: hidden !important;
}
div[data-baseweb="popover"] [data-baseweb="menu"],
div[data-baseweb="popover"] ul[role="listbox"] {
  background: var(--bo-surface) !important;
  max-height: min(380px, 55vh) !important;
  overflow-y: auto !important;
  overflow-x: hidden !important;
  padding: 6px !important;
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--bo-accent-light-rgb), 0.45) var(--bo-surface-2);
}
div[data-baseweb="popover"] ul[role="listbox"]::-webkit-scrollbar {
  width: 8px;
}
div[data-baseweb="popover"] ul[role="listbox"]::-webkit-scrollbar-track {
  background: var(--bo-surface-2);
  border-radius: 4px;
}
div[data-baseweb="popover"] ul[role="listbox"]::-webkit-scrollbar-thumb {
  background: rgba(var(--bo-accent-light-rgb), 0.4);
  border-radius: 4px;
}
div[data-baseweb="popover"] ul[role="listbox"]::-webkit-scrollbar-thumb:hover {
  background: rgba(var(--bo-accent-light-rgb), 0.6);
}
div[data-baseweb="popover"] li[role="option"],
div[data-baseweb="popover"] [data-baseweb="menu"] li {
  color: var(--bo-text) !important;
  background: transparent !important;
  font-size: 14px !important;
  font-weight: 500 !important;
  padding: 11px 14px !important;
  border-radius: 8px !important;
  min-height: 42px !important;
  line-height: 1.35 !important;
}
div[data-baseweb="popover"] li[role="option"]:hover,
div[data-baseweb="popover"] li[role="option"][aria-selected="true"],
div[data-baseweb="popover"] [data-baseweb="menu"] li:hover {
  background: rgba(var(--bo-accent-rgb), 0.2) !important;
  color: var(--bo-purple) !important;
}
div[data-baseweb="popover"] li[role="option"][aria-selected="true"] {
  font-weight: 600 !important;
  box-shadow: inset 3px 0 0 var(--bo-purple);
}
div[data-baseweb="popover"] li[data-highlighted="true"],
div[data-baseweb="popover"] [data-baseweb="menu"] li[data-highlighted="true"] {
  background: rgba(var(--bo-accent-rgb), 0.2) !important;
  color: var(--bo-purple) !important;
}
div[data-baseweb="popover"] div[data-testid="stMarkdownContainer"] p,
div[data-baseweb="popover"] div[data-testid="stMarkdownContainer"] span {
  color: var(--bo-text) !important;
}

/* BaseWeb popover portal — force dark everywhere (fixes white dropdown) */
body div[data-baseweb="popover"],
body div[data-baseweb="popover"] > div,
body div[data-baseweb="popover"] ul,
body div[data-baseweb="popover"] li,
body div[data-baseweb="popover"] [data-baseweb="menu"],
body div[data-baseweb="popover"] [data-baseweb="menu"] ul,
body div[data-baseweb="popover"] [data-baseweb="menu"] li {
  background-color: var(--bo-surface) !important;
  background: var(--bo-surface) !important;
}
body div[data-baseweb="popover"] li[role="option"],
body div[data-baseweb="popover"] li[role="option"] > div,
body div[data-baseweb="popover"] li[role="option"] span {
  color: var(--bo-text) !important;
  -webkit-text-fill-color: var(--bo-text) !important;
  background-color: transparent !important;
}
body div[data-baseweb="popover"] li[role="option"]:hover,
body div[data-baseweb="popover"] li[role="option"][aria-selected="true"],
body div[data-baseweb="popover"] li[data-highlighted="true"] {
  background-color: rgba(var(--bo-accent-rgb), 0.22) !important;
  color: var(--bo-purple) !important;
}
div[data-testid="stSelectboxVirtualDropdown"],
div[data-testid="stSelectboxVirtualDropdown"] > div,
div[data-testid="stSelectboxVirtualDropdown"] ul {
  background: var(--bo-surface) !important;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.32) !important;
  border-radius: 12px !important;
}
div[data-testid="stSelectboxVirtualDropdown"] li {
  color: var(--bo-text) !important;
  background: transparent !important;
}
div[data-testid="stSelectboxVirtualDropdown"] li:hover,
div[data-testid="stSelectboxVirtualDropdown"] li[aria-selected="true"] {
  background: rgba(var(--bo-accent-rgb), 0.22) !important;
  color: var(--bo-purple) !important;
}

/* Slider — dark track/thumb */
.stSlider [data-baseweb="slider"] div[data-testid="stTickBar"] {
  background: var(--bo-surface-2) !important;
}
.stSlider [data-baseweb="slider"] div[data-testid="stThumbValue"],
.stSlider [data-baseweb="slider"] div[role="slider"] {
  background: var(--bo-purple-deep) !important;
  border-color: var(--bo-purple) !important;
}
.stSlider [data-baseweb="slider"] > div > div {
  background: rgba(var(--bo-accent-rgb), 0.35) !important;
}
.stSlider label, .stSlider [data-testid="stWidgetLabel"] {
  color: var(--bo-dim) !important;
}
.stNumberInput [data-testid="stNumberInputStepDown"],
.stNumberInput [data-testid="stNumberInputStepUp"] {
  display: none !important;
}
.stNumberInput input { padding-left: 12px !important; }
div[data-testid="stNumberInput"] label,
div[data-testid="stSelectbox"] label,
.stSelectbox label {
  font-size: 11px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.08em !important;
  color: var(--bo-dim) !important;
  margin-bottom: 4px !important;
}

.bo-filter-bar {
  display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap;
  margin: 0 0 28px; padding: 14px 16px;
  background: var(--bo-surface); border: 1px solid var(--bo-border); border-radius: 14px;
}
.bo-filter-bar .filter-field { min-width: 100px; max-width: 140px; }
.bo-filter-bar .filter-grow { flex: 1; min-width: 120px; }

/* Game projection cards — compact matchup grid */
div[data-testid="stHorizontalBlock"]:has(.bo-mg-card) {
  gap: 0.5rem !important;
  align-items: stretch !important;
}
div[data-testid="column"]:has(.bo-mg-card) {
  display: flex !important;
  flex-direction: column !important;
  align-self: stretch !important;
  padding-left: 0.25rem !important;
  padding-right: 0.25rem !important;
}
div[data-testid="column"]:has(.bo-mg-card) > div {
  flex: 1 1 auto !important;
  display: flex !important;
  flex-direction: column !important;
}
div[data-testid="column"]:has(.bo-mg-card) div[data-testid="stMarkdown"] {
  flex: 1 1 auto !important;
  margin-bottom: 0 !important;
}
div[data-testid="column"]:has(.bo-mg-card) div[data-testid="stButton"] {
  margin-top: auto !important;
  margin-bottom: 0 !important;
  flex-shrink: 0 !important;
}
div[data-testid="column"]:has(.bo-mg-card) div[data-testid="stButton"] > button {
  min-height: 28px !important;
  height: 28px !important;
  padding: 0 10px !important;
  font-size: 10px !important;
  letter-spacing: 0.04em;
  border-radius: 8px !important;
}

.bo-mg-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
@media (max-width: 1400px) { .bo-mg-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (max-width: 1024px) { .bo-mg-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 768px) { .bo-mg-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .bo-mg-grid { grid-template-columns: 1fr; } }

.bo-mg-card {
  display: flex; flex-direction: column; text-decoration: none; color: inherit;
  position: relative;
  height: 100%;
  min-height: 248px;
  padding: 8px 8px 6px;
  margin-bottom: 0;
  border-radius: 12px;
  border: 1px solid #d1d5db;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.bo-mg-body {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
}
.bo-mg-card:hover {
  border-color: #93c5fd;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.12);
}
.bo-mg-card.active { border-color: #2563eb; box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.35); }
.bo-mg-card.gotn {
  border: 2px solid #f97316;
  box-shadow: 0 0 0 1px rgba(249, 115, 22, 0.25);
}
.bo-mg-gotn {
  position: absolute; top: -1px; left: 50%; transform: translate(-50%, -50%);
  padding: 3px 8px; border-radius: 5px;
  background: #f97316; color: #111;
  font-size: 8px; font-weight: 900; letter-spacing: 0.1em;
}
.bo-mg-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 6px; min-height: 14px;
}
.bo-mg-time { font-size: 9px; font-weight: 600; color: #64748b; }
.bo-mg-tv-logo { height: 12px; max-width: 56px; object-fit: contain; }
.bo-mg-tv-text { font-size: 8px; font-weight: 700; color: var(--bo-dim); letter-spacing: 0.04em; }
.bo-mg-logos {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  margin-bottom: 4px;
}
.bo-mg-team-logo {
  width: 36px; height: 36px; object-fit: contain;
  border-radius: 8px; background: rgba(255,255,255,0.03);
}
.bo-mg-team-logo.bo-logo-fallback {
  width: 36px; height: 36px; border-radius: 8px; background: #1a1a22;
}
.bo-mg-match {
  text-align: center; font-size: 11px; font-weight: 800;
  letter-spacing: 0.05em; color: #0f172a; margin-bottom: 2px;
}
.bo-mg-score {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  font-size: 20px; font-weight: 800; margin-bottom: 6px; line-height: 1;
}
.bo-mg-score.proj { color: #059669; }
.bo-mg-score.final { color: #047857; font-size: 20px; }
.bo-mg-score-sep { color: #94a3b8; font-weight: 600; font-size: 14px; }
.bo-mg-finals-head {
  display: flex; align-items: baseline; justify-content: space-between;
  margin: 16px 0 8px; padding-top: 6px;
  border-top: 1px solid #e2e8f0;
}
.bo-mg-finals-label {
  font-size: 10px; font-weight: 800; letter-spacing: 0.12em;
  text-transform: uppercase; color: #64748b;
}
.bo-mg-finals-count { font-size: 10px; color: #94a3b8; }
.bo-mg-card-final { opacity: 0.92; }
.bo-mg-grid-finals { margin-bottom: 6px; }
.bo-mg-section { margin-top: 4px; padding-top: 4px; border-top: 1px solid #e2e8f0; min-height: 42px; }
.bo-mg-sec-label {
  font-size: 8px; font-weight: 700; letter-spacing: 0.1em;
  color: #64748b; margin-bottom: 3px;
  display: flex; align-items: center; gap: 4px;
}
.bo-mg-quotes { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 6px; align-items: start; }
.bo-mg-cell, .bo-mg-quote { display: flex; flex-direction: column; gap: 2px; min-width: 0; min-height: 32px; }
.bo-mg-q-label {
  font-size: 9px; font-weight: 700; color: #1e293b;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  line-height: 1.2;
}
.bo-mg-q-empty { font-size: 9px; color: #94a3b8; font-weight: 600; }
.bo-mg-edge {
  font-size: 8px; font-weight: 800; font-family: var(--bo-mono);
  padding: 0 4px; border-radius: 3px; line-height: 1.25; flex-shrink: 0;
}
.bo-mg-edge-pos { color: #4ade80; background: rgba(74, 222, 128, 0.12); }
.bo-mg-edge-neg { color: #f87171; background: rgba(248, 113, 113, 0.12); }
.bo-mg-edge-neutral { color: var(--bo-dim); background: rgba(161, 161, 170, 0.1); }
.bo-mg-q-meta {
  display: flex; align-items: center; flex-wrap: wrap; gap: 4px 6px; min-width: 0;
}
.bo-mg-q-book {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 9px; color: #475569;
}
.bo-mg-book-logo { width: 11px !important; height: 11px !important; border-radius: 2px; }
.bo-mg-q-price { font-size: 9px; font-weight: 700; color: #0f172a; font-family: var(--bo-mono); }

/* Live games board */
.bo-live-page { margin-top: 8px; }
.bo-live-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
@media (max-width: 1100px) { .bo-live-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 680px) { .bo-live-grid { grid-template-columns: 1fr; } }

.bo-live-card {
  display: block;
  text-decoration: none !important;
  color: #fafafa !important;
  -webkit-text-fill-color: #fafafa !important;
  background: linear-gradient(180deg, rgba(18,18,26,0.98), rgba(10,10,14,0.98));
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 14px;
  padding: 14px 16px 12px;
  margin-bottom: 6px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.bo-live-card:hover { border-color: rgba(255,255,255,0.12); }
.bo-live-card-active {
  border-color: rgba(250, 204, 21, 0.75);
  box-shadow: 0 0 0 1px rgba(250, 204, 21, 0.25), 0 0 28px rgba(250, 204, 21, 0.12);
}
.bo-live-card-final { opacity: 0.88; }
.bo-live-card.active { border-color: rgba(56, 189, 248, 0.5); }

.bo-live-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-bottom: 12px; min-height: 18px;
}
.bo-live-dot {
  width: 7px; height: 7px; border-radius: 50%; background: #fb923c;
  box-shadow: 0 0 8px rgba(251,146,60,0.8); display: inline-block; margin-right: 6px;
}
.bo-live-badge {
  font-size: 10px; font-weight: 800; letter-spacing: 0.08em; color: #fb923c;
  display: inline-flex; align-items: center;
}
.bo-live-badge-final { color: var(--bo-dim); }
.bo-live-badge-pre { color: var(--bo-muted); letter-spacing: 0.04em; }
.bo-live-situation {
  font-size: 10px; font-weight: 600; color: var(--bo-muted); font-family: var(--bo-mono);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 62%;
}

.bo-live-scoreboard {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px;
}
.bo-live-team { text-align: center; min-width: 0; }
.bo-live-logo { width: 36px; height: 36px; object-fit: contain; margin-bottom: 4px; }
.bo-live-abbr { font-size: 11px; font-weight: 700; color: #a1a1aa !important; -webkit-text-fill-color: #a1a1aa !important; letter-spacing: 0.06em; }
.bo-live-score { font-size: 34px; font-weight: 800; line-height: 1.05; color: #fff !important; -webkit-text-fill-color: #fff !important; font-family: var(--bo-mono); }
.bo-live-wp { font-size: 11px; color: #71717a !important; -webkit-text-fill-color: #71717a !important; font-family: var(--bo-mono); margin-top: 2px; }

.bo-live-xfinal {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px;
  padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.06);
  font-family: var(--bo-mono); font-size: 10px;
}
.bo-live-xf-label { font-weight: 800; color: #71717a !important; -webkit-text-fill-color: #71717a !important; letter-spacing: 0.08em; }
.bo-live-xf-scores { color: #fafafa !important; -webkit-text-fill-color: #fafafa !important; font-weight: 600; }
.bo-live-xf-meta { color: #a1a1aa !important; -webkit-text-fill-color: #a1a1aa !important; }
.bo-live-lucky {
  margin-left: auto; font-size: 10px; font-weight: 700; color: #facc15;
  background: rgba(250,204,21,0.12); padding: 2px 6px; border-radius: 6px;
}

.bo-live-empty { color: #a1a1aa !important; padding: 24px; text-align: center; }

/* ESPN-style live scoreboard */
.bo-live-espn-bar {
  display: grid;
  grid-template-columns: 6px 1fr 6px;
  border-radius: 14px;
  overflow: hidden;
  margin-bottom: 18px;
  border: 1px solid rgba(255,255,255,0.08);
  background: linear-gradient(180deg, rgba(16,16,22,0.98), rgba(8,8,12,0.98));
  box-shadow: 0 8px 32px rgba(0,0,0,0.45);
}
.bo-live-espn-stripe { min-height: 100%; }
.bo-live-espn-body { padding: 14px 18px 16px; }
.bo-live-espn-top {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px 14px;
  margin-bottom: 14px; padding-bottom: 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.bo-live-espn-badge {
  font-size: 10px; font-weight: 900; letter-spacing: 0.1em;
  padding: 3px 8px; border-radius: 4px;
}
.bo-live-espn-badge-live {
  color: #fff !important; background: #dc2626;
  display: inline-flex; align-items: center; gap: 6px;
}
.bo-live-espn-badge-final { color: #a1a1aa !important; background: rgba(255,255,255,0.06); }
.bo-live-espn-badge-pre { color: #71717a !important; background: rgba(255,255,255,0.04); }
.bo-live-espn-clock {
  font-family: var(--bo-mono); font-size: 13px; font-weight: 700;
  color: #fafafa !important; -webkit-text-fill-color: #fafafa !important;
}
.bo-live-espn-sit {
  font-family: var(--bo-mono); font-size: 12px; font-weight: 600;
  color: #facc15 !important; -webkit-text-fill-color: #facc15 !important;
}
.bo-live-espn-venue {
  margin-left: auto; font-size: 11px; color: #71717a !important;
  -webkit-text-fill-color: #71717a !important;
}
.bo-live-espn-matchup {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: center;
}
@media (max-width: 720px) {
  .bo-live-espn-matchup { grid-template-columns: 1fr; gap: 16px; }
  .bo-live-espn-mid { order: -1; }
}
.bo-live-espn-side {
  display: flex; flex-direction: column; gap: 6px;
  padding: 12px 14px; border-radius: 10px;
  background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.04);
}
.bo-live-espn-side-main {
  display: flex; align-items: center; gap: 14px;
}
.bo-live-espn-home .bo-live-espn-side-main { justify-content: flex-end; }
.bo-live-espn-home .bo-live-espn-teammeta { text-align: right; }
.bo-live-espn-has-ball {
  border-color: rgba(250, 204, 21, 0.45);
  box-shadow: inset 0 0 0 1px rgba(250, 204, 21, 0.15);
}
.bo-live-espn-logo {
  width: 64px !important; height: 64px !important; object-fit: contain;
  border-radius: 8px; background: rgba(255,255,255,0.04); flex-shrink: 0;
}
.bo-live-espn-logo.bo-logo-fallback {
  width: 64px; height: 64px; border-radius: 8px; background: rgba(255,255,255,0.05);
}
.bo-live-espn-teammeta { flex: 1; min-width: 0; }
.bo-live-espn-abbr {
  font-size: 22px; font-weight: 900; letter-spacing: 0.04em; line-height: 1.1;
  color: #fafafa !important; -webkit-text-fill-color: #fafafa !important;
}
.bo-live-espn-full {
  font-size: 11px; font-weight: 600; color: #71717a !important;
  -webkit-text-fill-color: #71717a !important; margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.bo-live-espn-score {
  font-family: var(--bo-mono); font-size: 42px; font-weight: 800; line-height: 1;
  color: #fff !important; -webkit-text-fill-color: #fff !important;
  min-width: 48px; text-align: center; flex-shrink: 0;
}
.bo-live-espn-win {
  font-family: var(--bo-mono); font-size: 12px; font-weight: 700;
  color: #a1a1aa !important; -webkit-text-fill-color: #a1a1aa !important;
}
.bo-live-espn-home .bo-live-espn-win { text-align: right; }
.bo-live-espn-away .bo-live-espn-win { text-align: left; }
.bo-live-espn-mid { text-align: center; min-width: 72px; }
.bo-live-espn-at {
  font-size: 14px; font-weight: 700; color: #52525b !important;
  -webkit-text-fill-color: #52525b !important; margin-bottom: 6px;
}
.bo-live-espn-wpbar {
  display: flex; height: 6px; border-radius: 999px; overflow: hidden;
  background: rgba(255,255,255,0.06); max-width: 120px; margin: 0 auto;
}
.bo-live-espn-wpaway, .bo-live-espn-wphome { height: 100%; transition: width 0.3s ease; }
.bo-live-metric-logo {
  width: 22px !important; height: 22px !important; object-fit: contain;
  vertical-align: middle; margin-right: 6px;
}
.bo-live-metric-logo.bo-logo-fallback {
  width: 22px; height: 22px; border-radius: 4px; display: inline-block;
  background: rgba(255,255,255,0.05); vertical-align: middle; margin-right: 6px;
}
.bo-live-metric-head { text-align: right !important; }
.bo-live-metric-head-inner {
  display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px;
}

.bo-live-detail,
.bo-live-detail div,
.bo-live-detail span,
.bo-live-detail td,
.bo-live-detail th {
  color: #fafafa !important;
  -webkit-text-fill-color: #fafafa !important;
}
.bo-live-detail-hero {
  text-align: center; padding: 16px 0 20px;
  border-bottom: 1px solid rgba(255,255,255,0.06); margin-bottom: 16px;
}
.bo-live-detail-status { font-size: 12px; color: #a1a1aa !important; -webkit-text-fill-color: #a1a1aa !important; font-family: var(--bo-mono); margin-bottom: 8px; }
.bo-live-detail-scoreline { font-size: 28px; font-weight: 800; font-family: var(--bo-mono); color: #fafafa !important; -webkit-text-fill-color: #fafafa !important; }
.bo-live-detail-scoreline span { color: #fafafa !important; -webkit-text-fill-color: #fafafa !important; }
.bo-live-detail-sep { color: #71717a !important; -webkit-text-fill-color: #71717a !important; margin: 0 10px; }

.bo-live-detail-grid {
  display: grid; grid-template-columns: 1fr 1.2fr; gap: 16px;
}
@media (max-width: 900px) { .bo-live-detail-grid { grid-template-columns: 1fr; } }

.bo-live-detail-panel {
  background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px; padding: 12px 14px;
}
.bo-live-metrics { width: 100%; border-collapse: collapse; font-size: 12px; }
.bo-live-metrics th { text-align: right; color: #71717a !important; -webkit-text-fill-color: #71717a !important; font-size: 10px; padding: 4px 6px; }
.bo-live-metrics th:first-child { text-align: left; }
.bo-live-metric-label { color: #a1a1aa !important; -webkit-text-fill-color: #a1a1aa !important; padding: 6px 4px; }
.bo-live-metric-away, .bo-live-metric-home {
  text-align: right; font-family: var(--bo-mono); font-weight: 600; padding: 6px 6px;
  color: #fafafa !important; -webkit-text-fill-color: #fafafa !important;
}

.bo-live-pbp-wrap { max-height: 520px; overflow-y: auto; }
.bo-live-pbp-head { font-size: 11px; font-weight: 800; letter-spacing: 0.08em; color: #71717a !important; -webkit-text-fill-color: #71717a !important; margin-bottom: 10px; }
.bo-live-pbp-row {
  display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: start;
  padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px;
}
.bo-live-pbp-time { font-family: var(--bo-mono); font-size: 10px; color: #71717a !important; -webkit-text-fill-color: #71717a !important; }
.bo-live-pbp-text { color: #fafafa !important; -webkit-text-fill-color: #fafafa !important; line-height: 1.35; }
.bo-live-pbp-score {
  font-size: 9px; font-weight: 800; color: #4ade80; background: rgba(74,222,128,0.12);
  padding: 2px 5px; border-radius: 4px; letter-spacing: 0.06em;
}

.bo-gp-stack { display: flex; flex-direction: column; gap: 10px; }
.bo-gp-stack [data-testid="column"]:last-child div[data-testid="stButton"] {
  margin-top: 28px !important;
}
.bo-gp-stack [data-testid="column"]:last-child div[data-testid="stButton"] > button {
  width: 44px !important; min-width: 44px !important; height: 44px !important; min-height: 44px !important;
  padding: 0 !important; border-radius: 12px !important;
  background: rgba(255,255,255,0.04) !important;
  border: 1px solid rgba(255,255,255,0.1) !important;
  box-shadow: none !important;
  color: var(--bo-muted) !important;
  font-size: 22px !important; font-weight: 500 !important; line-height: 1 !important;
}
.bo-gp-stack [data-testid="column"]:last-child div[data-testid="stButton"] > button:hover {
  background: rgba(56, 189, 248, 0.1) !important;
  border-color: rgba(56, 189, 248, 0.35) !important;
  color: #7dd3fc !important;
  transform: none !important;
  box-shadow: none !important;
}
.bo-gp-card {
  display: grid; grid-template-columns: 72px 1fr auto 72px; gap: 14px; align-items: center;
  padding: 16px 20px; border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.06); background: rgba(15, 17, 27, 0.95);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}
.bo-gp-card.final { border-color: rgba(74, 222, 128, 0.18); }
.bo-gp-card.active { border-color: rgba(56, 189, 248, 0.35); }
.bo-gp-final-badge {
  display: inline-block; margin-right: 8px; padding: 2px 6px; border-radius: 4px;
  font-size: 9px; font-weight: 800; letter-spacing: 0.08em; vertical-align: middle;
  color: #86efac; background: rgba(74, 222, 128, 0.12); border: 1px solid rgba(74, 222, 128, 0.25);
}
.bo-gp-logos { display: flex; gap: 8px; align-items: center; }
.bo-gp-logo {
  width: 40px; height: 40px; object-fit: contain;
  border-radius: 8px; background: rgba(255,255,255,0.03);
}
.bo-gp-logo-fallback { width: 40px; height: 40px; border-radius: 8px; background: #1a1a22; }
.bo-gp-match {
  font-size: 15px; font-weight: 800; letter-spacing: 0.06em;
  color: var(--bo-text); margin-bottom: 10px; text-transform: uppercase;
}
.bo-gp-bars { display: grid; gap: 7px; margin-bottom: 8px; max-width: 420px; }
.bo-gp-bar-row { display: flex; align-items: center; gap: 10px; }
.bo-gp-bar-label {
  font-size: 9px; font-weight: 700; letter-spacing: 0.12em;
  color: var(--bo-dim); width: 44px; flex-shrink: 0;
}
.bo-gp-side-track {
  flex: 1; height: 7px; border-radius: 4px; background: rgba(255,255,255,0.06); overflow: hidden;
}
.bo-gp-bar.side {
  height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, #0891b2, #38bdf8, #7dd3fc); min-width: 8px;
}
.bo-gp-tot-track {
  flex: 1; height: 7px; border-radius: 4px; overflow: hidden;
  display: flex; background: rgba(255,255,255,0.06);
}
.bo-gp-tot-neg { height: 100%; background: linear-gradient(90deg, #ea580c, #fb923c); }
.bo-gp-tot-pos { height: 100%; background: linear-gradient(90deg, #0891b2, #38bdf8); }
.bo-gp-meta {
  font-size: 11px; color: var(--bo-dim); font-family: var(--bo-mono); letter-spacing: 0.02em;
}
.bo-gp-score {
  display: flex; align-items: baseline; justify-content: center; gap: 6px;
  font-family: var(--bo-mono); min-width: 88px;
}
.bo-gp-score-away { font-size: 26px; font-weight: 800; color: var(--bo-muted); }
.bo-gp-score-home { font-size: 30px; font-weight: 800; color: var(--bo-text); }
.bo-gp-score-sep { font-size: 22px; color: var(--bo-dim); font-weight: 600; }
.bo-gp-edge { text-align: right; min-width: 68px; }
.bo-gp-edge-side {
  font-family: var(--bo-mono); font-size: 22px; font-weight: 800; color: #38bdf8; line-height: 1.1;
}
.bo-gp-edge-tot {
  font-family: var(--bo-mono); font-size: 11px; font-weight: 700;
  color: #fb923c; margin-top: 6px; letter-spacing: 0.02em;
}

.bo-empty {
  padding: 48px 24px; text-align: center; border: 1px dashed var(--bo-border);
  border-radius: 16px; color: var(--bo-muted); font-size: 15px;
}
.bo-skeleton {
  height: 72px; border-radius: 14px; margin-bottom: 8px;
  background: linear-gradient(90deg, var(--bo-surface) 25%, var(--bo-surface-2) 50%, var(--bo-surface) 75%);
  background-size: 200% 100%; animation: bo-shimmer 1.4s ease infinite;
}
@keyframes bo-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

.stRadio > div[role="radiogroup"] {
  gap: 8px !important;
  flex-wrap: nowrap !important;
  overflow-x: auto !important;
  padding-bottom: 4px !important;
  scrollbar-width: none;
}
.stRadio > div[role="radiogroup"]::-webkit-scrollbar { display: none; }
.stRadio > div[role="radiogroup"] > label {
  flex-shrink: 0 !important;
  background: var(--bo-surface) !important;
  border: 1px solid var(--bo-border) !important;
  border-radius: 999px !important;
  padding: 0.5rem 1rem !important;
  color: var(--bo-muted) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  margin: 0 !important;
}
.stRadio > div[role="radiogroup"] > label:hover {
  border-color: var(--bo-border-strong) !important;
  color: var(--bo-text) !important;
}
.stRadio > div[role="radiogroup"] > label[data-checked="true"],
.stRadio div[aria-checked="true"] > label {
  background: rgba(var(--bo-accent-rgb), 0.18) !important;
  border-color: rgba(var(--bo-accent-light-rgb), 0.5) !important;
  color: var(--bo-purple) !important;
}

.stTabs [data-baseweb="tab-list"] {
  gap: 4px; border-bottom: none; background: transparent; padding: 0 0 16px;
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important;
  border-radius: 999px !important;
  border: 1px solid transparent !important;
  color: var(--bo-muted) !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  padding: 8px 16px !important;
}
.stTabs [aria-selected="true"] {
  background: rgba(var(--bo-accent-rgb), 0.2) !important;
  color: var(--bo-purple) !important;
  border-color: rgba(var(--bo-accent-light-rgb), 0.45) !important;
  box-shadow: 0 0 20px rgba(var(--bo-accent-rgb), 0.15) !important;
}
.stTabs [data-baseweb="tab-highlight"] { background: var(--bo-purple) !important; height: 2px !important; }

/* Projections board — reference layout */
.bo-proj-board { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
.bo-proj-row {
  display: grid; grid-template-columns: 52px 1fr auto auto 36px; gap: 14px; align-items: center;
  padding: 14px 16px; border-radius: 16px;
  border: 1px solid var(--bo-border);
  background: linear-gradient(135deg, rgba(17, 17, 24, 0.92) 0%, rgba(12, 12, 18, 0.98) 100%);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.bo-proj-row:hover {
  border-color: rgba(var(--bo-accent-light-rgb), 0.28);
  box-shadow: 0 0 20px rgba(var(--bo-accent-rgb), 0.08);
}
.bo-proj-date-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 40px; padding: 6px 8px; border-radius: 10px;
  background: var(--bo-surface-2); border: 1px solid var(--bo-border);
  font-size: 11px; font-weight: 700; color: var(--bo-muted); font-family: var(--bo-mono);
}
.bo-proj-match { min-width: 0; }
.bo-proj-team { display: inline-flex; align-items: center; gap: 8px; margin-right: 6px; }
.bo-proj-at { color: var(--bo-dim); font-size: 12px; margin: 0 4px; }
.bo-proj-logo, .bo-logo-fallback {
  width: 28px; height: 28px; border-radius: 50%; object-fit: contain;
  background: rgba(255,255,255,0.04); flex-shrink: 0;
}
.bo-logo-fallback { border: 1px solid var(--bo-border); }
.bo-proj-abbr { font-size: 13px; font-weight: 700; color: var(--bo-text); letter-spacing: 0.02em; min-width: 32px; }
.bo-proj-score {
  font-family: var(--bo-mono); font-size: 14px; font-weight: 700;
  color: #a5b4fc; min-width: 24px;
}
.bo-proj-sub { font-size: 11px; color: var(--bo-dim); margin-top: 6px; }
.bo-proj-mid { text-align: right; white-space: nowrap; }
.bo-proj-num { font-family: var(--bo-mono); font-size: 15px; font-weight: 700; color: var(--bo-text); }
.bo-proj-sep { color: var(--bo-dim); margin: 0 3px; }
.bo-proj-line { font-family: var(--bo-mono); font-size: 14px; color: var(--bo-muted); }
.bo-proj-diff {
  display: inline-block; margin-left: 8px; font-family: var(--bo-mono); font-size: 12px; font-weight: 600;
}
.bo-proj-diff.pos { color: var(--bo-orange); }
.bo-proj-diff.neg { color: #93c5fd; }
.bo-proj-diff.flat { color: var(--bo-dim); }
.bo-proj-pick {
  font-size: 12px; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase;
  white-space: nowrap; text-align: right; min-width: 88px;
}
.bo-pick-over { color: var(--bo-orange); }
.bo-pick-under { color: #93c5fd; }
.bo-proj-add {
  width: 32px; height: 32px; border-radius: 50%; border: 1px solid var(--bo-border);
  background: var(--bo-surface-2); color: var(--bo-muted); font-size: 18px; line-height: 1;
  cursor: default; display: flex; align-items: center; justify-content: center; padding: 0;
}

/* Injury room feed */
.bo-inj-board { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.bo-inj-row {
  display: grid; grid-template-columns: 52px 44px 1fr; gap: 14px; align-items: start;
  padding: 14px 16px; border-radius: 16px;
  border: 1px solid var(--bo-border);
  background: linear-gradient(135deg, rgba(17, 17, 24, 0.92) 0%, rgba(12, 12, 18, 0.98) 100%);
}
.bo-inj-row:hover { border-color: rgba(var(--bo-accent-light-rgb), 0.22); }
.bo-inj-time { font-size: 11px; color: var(--bo-dim); font-family: var(--bo-mono); padding-top: 4px; }
.bo-inj-avatar {
  width: 40px; height: 40px; border-radius: 50%; object-fit: cover;
  border: 2px solid rgba(255,255,255,0.08); background: var(--bo-surface-2);
}
.bo-inj-top { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.bo-inj-player { font-size: 15px; font-weight: 700; color: var(--bo-text); }
.bo-inj-meta { font-size: 12px; color: var(--bo-muted); }
.bo-inj-badge {
  display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.06em;
  text-transform: uppercase; padding: 4px 8px; border-radius: 6px; margin-bottom: 6px;
}
.bo-inj-badge-out { background: rgba(248, 113, 113, 0.15); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.35); }
.bo-inj-badge-doubt { background: rgba(251, 146, 60, 0.12); color: #fdba74; border: 1px solid rgba(251, 146, 60, 0.3); }
.bo-inj-badge-question { background: rgba(251, 191, 36, 0.1); color: #fcd34d; border: 1px solid rgba(251, 191, 36, 0.28); }
.bo-inj-badge-active { background: rgba(74, 222, 128, 0.12); color: #86efac; border: 1px solid rgba(74, 222, 128, 0.3); }
.bo-inj-badge-listed { background: rgba(161, 161, 170, 0.1); color: var(--bo-muted); border: 1px solid var(--bo-border); }
.bo-inj-note { font-size: 12px; line-height: 1.45; color: var(--bo-dim); }

@media (max-width: 900px) {
  .bo-proj-row { grid-template-columns: 44px 1fr; grid-template-rows: auto auto; }
  .bo-proj-mid, .bo-proj-pick, .bo-proj-add { grid-column: 2; }
  .bo-proj-add { display: none; }
}

/* Matchup detail — reference layout */
.bo-mu-page { margin-top: 8px; }
.bo-mu-hero {
  border: 1px solid var(--bo-border); border-radius: 20px;
  background: linear-gradient(180deg, var(--bo-surface-2) 0%, var(--bo-surface) 100%);
  padding: 24px 28px; margin-bottom: 20px; position: relative; overflow: hidden;
}
.bo-mu-hero::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--bo-purple), transparent); opacity: 0.45;
}
.bo-mu-hero-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.12em;
  color: var(--bo-dim); margin-bottom: 16px;
}
.bo-mu-scoreboard {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 16px; align-items: center;
}
.bo-mu-team-col { text-align: center; }
.bo-mu-team-col.home { text-align: center; }
.bo-mu-team-name { font-size: 14px; font-weight: 700; color: var(--bo-text); margin-top: 8px; }
.bo-mu-logo-xl { width: 64px; height: 64px; }
.bo-mu-logo-lg { width: 48px; height: 48px; }
.bo-mu-logo-xs { width: 18px; height: 18px; display: inline-block; vertical-align: middle; margin-right: 6px; }
.bo-mu-score-center { text-align: center; min-width: 220px; }
.bo-mu-scores { display: flex; align-items: center; justify-content: center; gap: 16px; margin-bottom: 8px; }
.bo-mu-score-box {
  font-family: var(--bo-mono); font-size: clamp(1.75rem, 4.5vw, 2.5rem); font-weight: 800;
  color: var(--bo-green); font-variant-numeric: tabular-nums; line-height: 1;
  padding: 10px 18px; border-radius: 12px;
  background: rgba(74, 222, 128, 0.08);
  border: 1px solid rgba(74, 222, 128, 0.35);
  box-shadow: 0 0 28px rgba(74, 222, 128, 0.18), inset 0 0 20px rgba(74, 222, 128, 0.06);
}
.bo-mu-score {
  font-family: var(--bo-mono); font-size: clamp(2rem, 5vw, 2.75rem); font-weight: 800;
  color: var(--bo-green); text-shadow: 0 0 24px rgba(74, 222, 128, 0.35);
  font-variant-numeric: tabular-nums; line-height: 1;
}
.bo-mu-vs { color: var(--bo-dim); font-size: 14px; font-weight: 600; }
.bo-mu-meta { font-size: 12px; color: var(--bo-muted); margin-bottom: 12px; }
.bo-pill-row { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 8px; }
.bo-pill {
  display: inline-block; padding: 6px 12px; border-radius: 999px;
  background: rgba(255,255,255,0.06); border: 1px solid var(--bo-border);
  font-size: 12px; font-weight: 600; color: var(--bo-text); font-family: var(--bo-mono);
}
.bo-mu-layout {
  display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.95fr); gap: 20px; align-items: start;
}
.bo-mu-profile {
  border: 1px solid var(--bo-border); border-radius: 16px; background: var(--bo-surface);
  padding: 18px; border-top: 3px solid var(--team-accent, var(--bo-purple));
}
.bo-mu-profile-head { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
.bo-mu-profile-head h3 { margin: 0; font-size: 17px; font-weight: 700; }
.bo-mu-profile-head p { margin: 2px 0 0; font-size: 12px; color: var(--bo-muted); }
.bo-mu-mascot { font-size: 11px; color: var(--bo-dim); margin-top: 2px; }
.bo-mu-bar-row { margin-bottom: 10px; }
.bo-mu-bar-head {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 11px; color: var(--bo-muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.06em;
}
.bo-mu-bar-pct { font-weight: 700; color: var(--bo-text); }
.bo-mu-bar-rank { color: var(--bo-dim); font-family: var(--bo-mono); }
.bo-mu-bar-track { height: 8px; border-radius: 999px; background: var(--bo-bg-elevated); overflow: hidden; }
.bo-mu-bar-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--bo-purple-deep), var(--bo-purple)); }
.bo-mu-bar-fill.purple { background: linear-gradient(90deg, var(--bo-purple-deep), var(--bo-purple)); }
.bo-mu-coach { margin: 12px 0 4px; font-size: 12px; color: var(--bo-muted); }
.bo-mu-coach strong { color: var(--bo-text); font-size: 13px; }
.bo-mu-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.bo-mu-tag {
  font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 3px 8px; border-radius: 6px; background: var(--bo-bg-elevated); border: 1px solid var(--bo-border);
  color: var(--bo-dim);
}
.bo-mu-tempo { margin-top: 6px; font-size: 11px; color: var(--bo-dim); font-family: var(--bo-mono); }
.bo-mu-starter-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px 10px; margin-top: 6px;
}
.bo-mu-starter-cell { font-size: 11px; line-height: 1.45; }
.bo-mu-starter-pos { color: var(--team-accent, var(--bo-purple)); font-weight: 700; }
.bo-mu-starter-name { color: var(--bo-purple); }
.bo-mu-inj-panel {
  border: 1px solid var(--bo-border); border-radius: 16px; background: var(--bo-surface);
  padding: 16px 18px; margin: 16px 0;
}
.bo-mu-inj-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.bo-mu-inj-col h4 { margin: 0 0 8px; font-size: 13px; font-weight: 700; color: var(--bo-text); }
.bo-mu-inj-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 8px 0; border-bottom: 1px solid var(--bo-border); font-size: 12px;
}
.bo-mu-inj-row:last-child { border-bottom: none; }
.bo-mu-inj-since { font-size: 10px; color: var(--bo-dim); width: 100%; }
.bo-mu-dim { color: var(--bo-dim); }
.bo-grade-badge {
  display: inline-block; min-width: 34px; text-align: center;
  padding: 4px 6px; border-radius: 8px; font-size: 11px; font-weight: 700;
  border: 1px solid rgba(251, 191, 36, 0.45); background: rgba(251, 191, 36, 0.08); color: #fcd34d;
}
.bo-grade-badge.grade-high { border-color: rgba(74, 222, 128, 0.45); background: rgba(74, 222, 128, 0.1); color: #86efac; }
.bo-grade-badge.grade-mid { border-color: rgba(251, 191, 36, 0.45); background: rgba(251, 191, 36, 0.08); color: #fcd34d; }
.bo-grade-badge.grade-low { border-color: rgba(161, 161, 170, 0.35); background: rgba(161, 161, 170, 0.08); color: var(--bo-muted); }
.bo-mu-prop-player { display: flex; align-items: center; gap: 10px; }
.bo-mu-logo-prop, .bo-mu-prop-avatar { width: 32px; height: 32px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.bo-mu-prop-avatar.bo-logo-fallback { border-radius: 50%; }
.bo-mu-label { display: block; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--bo-dim); margin-bottom: 4px; }
.bo-mu-starters { font-size: 12px; color: var(--bo-muted); line-height: 1.55; margin-top: 12px; }
.bo-mu-injuries { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--bo-border); }
.bo-mu-inj { font-size: 11px; color: var(--bo-muted); margin-bottom: 4px; }
.bo-mu-aside { position: sticky; top: 12px; min-width: 0; }
.bo-mu-props-wrap {
  overflow-x: visible;
  border: 1px solid var(--bo-border); border-radius: 16px;
  background: var(--bo-surface);
}
.bo-mu-props-wrap .bo-props-table {
  width: 100%; min-width: 0; table-layout: fixed; font-size: 12px;
}
.bo-mu-props-wrap .bo-props-table th,
.bo-mu-props-wrap .bo-props-table td {
  padding: 8px 10px; overflow: hidden; text-overflow: ellipsis;
}
.bo-mu-props-wrap col.mu-col-player { width: 42%; }
.bo-mu-props-wrap col.mu-col-prop { width: 38%; }
.bo-mu-props-wrap col.mu-col-mine { width: 16%; }
.bo-mu-props-wrap col.mu-col-roi { width: 16%; }
.bo-mu-props-wrap .player-cell { min-width: 0; }
.bo-mu-props-wrap .bo-mu-prop-name { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bo-mu-props-wrap .player-cell .pos { font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bo-mu-props-wrap .bo-mu-prop-market { font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bo-mu-props-wrap .bo-mu-logo-prop, .bo-mu-props-wrap .bo-mu-prop-avatar { width: 28px; height: 28px; }
.bo-mu-props-wrap .bo-mu-prop-player { gap: 8px; min-width: 0; }
.bo-mu-props-hist { overflow-x: auto; }
.bo-mu-props-hist .bo-props-table { min-width: 640px; table-layout: auto; }
.bo-mu-empty, .bo-mu-muted { font-size: 13px; color: var(--bo-muted); padding: 16px; }
.bo-mu-advanced-head { display: flex; justify-content: space-between; align-items: baseline; margin: 20px 0 10px; }
.bo-props-table .edge-pos { color: var(--bo-green); font-weight: 600; font-family: var(--bo-mono); }
.bo-props-table .edge-neg { color: var(--bo-danger); font-family: var(--bo-mono); }
.bo-props-table .side-over { color: var(--bo-orange); font-weight: 700; }
.bo-props-table .side-under { color: #93c5fd; font-weight: 700; }
.bo-props-table .side-yes { color: var(--bo-green); font-weight: 700; }
.bo-props-table tr.result-hit td { background: rgba(74, 222, 128, 0.06); }
.bo-props-table tr.result-miss td { background: rgba(248, 113, 113, 0.05); }
.bo-result { font-size: 10px; font-weight: 800; letter-spacing: 0.06em; padding: 3px 7px; border-radius: 6px; }
.bo-result.hit { color: #86efac; background: rgba(74, 222, 128, 0.12); border: 1px solid rgba(74, 222, 128, 0.35); }
.bo-result.miss { color: #fca5a5; background: rgba(248, 113, 113, 0.1); border: 1px solid rgba(248, 113, 113, 0.3); }
.bo-result.push { color: var(--bo-muted); background: rgba(161, 161, 170, 0.1); border: 1px solid var(--bo-border); }
.bo-mu-hero-final { border-color: rgba(74, 222, 128, 0.25); }
.bo-mu-scores-final { gap: 20px; }
.bo-mu-score-stack { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.bo-mu-score-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--bo-dim); }
.bo-mu-score-box.final { color: #fff; background: rgba(74, 222, 128, 0.15); border-color: rgba(74, 222, 128, 0.5); }
.bo-mu-proj-pill { font-size: 11px; color: var(--bo-muted); font-family: var(--bo-mono); }
.bo-advanced-table .rank-good { color: #67e8f9; }
.bo-advanced-table .rank-bad { color: var(--bo-orange); }
.bo-advanced-table .rank-mid { color: var(--bo-text); }

.bo-profile-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px;
}
.bo-section-title {
  font-size: 12px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--bo-dim); margin: 0 0 12px;
}
.bo-props-table-wrap {
  border: 1px solid var(--bo-border); border-radius: 16px;
  background: var(--bo-surface); overflow-x: auto;
}
.bo-props-table {
  width: 100%; border-collapse: collapse; font-size: 13px; min-width: 520px;
}
.bo-props-table th, .bo-props-table td {
  padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--bo-border);
  background: transparent !important;
  color: var(--bo-text) !important;
}
.bo-props-table tbody tr { background: transparent !important; }
.bo-props-table tbody tr:hover td { background: rgba(var(--bo-accent-rgb), 0.06) !important; }
.bo-props-table th {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--bo-dim); font-weight: 600;
}
.bo-props-table tr:last-child td { border-bottom: none; }
.bo-props-table tr.play-row td { background: rgba(74, 222, 128, 0.04); }
.bo-props-table .grade { font-weight: 700; color: var(--bo-purple); }
.bo-props-table .player-cell { white-space: normal; min-width: 120px; }
.bo-props-table .player-cell .pos { font-size: 11px; color: var(--bo-dim); }
.bo-props-table .mono { font-family: var(--bo-mono); }
.bo-mu-props-simple .bo-mu-prop-market { color: #6ee7b7; font-weight: 600; }
.bo-mu-props-simple .bo-mu-prop-mine { color: var(--bo-text); font-weight: 700; }
.bo-mu-props-simple .bo-mu-prop-roi { font-weight: 600; }
.bo-advanced-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-bottom: 8px;
}
.bo-advanced-panel {
  border: 1px solid var(--bo-border); border-radius: 16px;
  background: var(--bo-surface); overflow: hidden;
}
.bo-advanced-panel h3 {
  margin: 0; padding: 12px 14px; font-size: 12px;
  border-bottom: 1px solid var(--bo-border); background: var(--bo-surface-2);
}
.bo-advanced-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.bo-advanced-table th, .bo-advanced-table td {
  padding: 8px 10px; border-bottom: 1px solid var(--bo-border);
}
.bo-advanced-table th {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--bo-dim); font-weight: 600;
}
.bo-advanced-table tr:last-child td { border-bottom: none; }
.bo-advanced-table .metric { color: var(--bo-muted); }
.bo-advanced-table .val { font-family: var(--bo-mono); }
.bo-advanced-table .rank { font-size: 10px; color: var(--bo-dim); margin-left: 4px; }

@media (max-width: 1100px) {
  .bo-mu-layout { grid-template-columns: 1fr; }
  .bo-mu-aside { position: static; }
  .bo-mu-scoreboard { grid-template-columns: 1fr; gap: 12px; }
  .bo-mu-inj-cols { grid-template-columns: 1fr; }
  .bo-mu-starter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

div[data-testid="stDataFrame"] {
  border: 1px solid var(--bo-border) !important;
  border-radius: 14px !important;
  overflow: hidden !important;
  background: var(--bo-surface) !important;
}

/* Bettor Odds shell — REDZONE-style header */
.bo-shell { padding-top: 4px; }
.bo-rz-header {
  display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 20px;
  padding: 14px 0 12px; border-bottom: 1px solid var(--bo-border);
}
.bo-rz-brand { display: flex; align-items: center; gap: 10px; min-width: 180px; }
.bo-rz-mark {
  width: 34px; height: 34px; border-radius: 10px;
  background: linear-gradient(145deg, var(--bo-purple-deep) 0%, #f5e642 100%);
  box-shadow: 0 0 24px rgba(var(--bo-accent-rgb), 0.35);
}
.bo-rz-brand-text {
  font-weight: 800; font-size: 15px; letter-spacing: 0.06em; color: var(--bo-text);
  text-transform: uppercase;
}
.bo-rz-brand-text em { font-style: normal; color: var(--bo-muted); font-weight: 600; }
.bo-rz-search {
  display: flex; align-items: center; height: 42px; padding: 0 16px;
  border-radius: 12px; border: 1px solid var(--bo-border);
  background: rgba(255,255,255,0.03); color: var(--bo-dim); font-size: 14px;
}
.bo-rz-sports { display: flex; gap: 8px; }
.bo-rz-sports-spacer { min-width: 120px; }
.bo-rz-sport {
  padding: 8px 14px; border-radius: 10px; font-size: 12px; font-weight: 700;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--bo-dim);
  border: 1px solid transparent;
}
.bo-rz-sport.active {
  color: var(--bo-text); border-color: rgba(var(--bo-accent-light-rgb),0.35);
  background: rgba(124,58,237,0.12);
}
.bo-rz-sport.disabled { opacity: 0.45; }

.bo-rz-primary-nav {
  display: flex; flex-wrap: wrap; gap: 6px 22px; align-items: center;
  padding: 14px 0 10px; border-bottom: 1px solid var(--bo-border);
}
.bo-rz-primary-nav [data-testid="column"] .stButton > button {
  background: transparent !important; border: none !important; box-shadow: none !important;
  color: var(--bo-muted) !important; font-size: 14px !important; font-weight: 500 !important;
  padding: 6px 0 !important; min-height: unset !important; border-radius: 0 !important;
  justify-content: flex-start !important;
}
.bo-rz-primary-nav [data-testid="column"] .stButton > button:hover {
  color: var(--bo-text) !important; transform: none !important;
}
.bo-rz-primary-nav [data-testid="column"] .stButton > button[kind="primary"] {
  color: var(--bo-text) !important; background: transparent !important;
  box-shadow: inset 0 -2px 0 var(--bo-purple) !important;
}
.bo-rz-more-wrap { margin-left: auto; max-width: 180px; }

.bo-rz-overflow-row {
  display: flex; flex-wrap: wrap; gap: 8px; padding: 0 0 18px;
  border-bottom: 1px solid var(--bo-border); margin-bottom: 8px;
}
.bo-rz-overflow-row [data-testid="column"] .stButton > button {
  background: transparent !important; border: 1px solid var(--bo-border) !important;
  box-shadow: none !important; color: var(--bo-dim) !important;
  font-size: 11px !important; font-weight: 600 !important; padding: 6px 10px !important;
  min-height: unset !important; border-radius: 999px !important;
}
.bo-rz-overflow-row [data-testid="column"] .stButton > button[kind="primary"] {
  color: var(--bo-purple) !important; border-color: rgba(var(--bo-accent-light-rgb),0.4) !important;
  background: rgba(124,58,237,0.08) !important;
}

.bo-rz-subnav {
  display: flex; gap: 10px; flex-wrap: wrap; padding: 16px 0 20px;
}
.bo-rz-subnav .stButton > button {
  background: transparent !important; box-shadow: none !important;
  border: 1px solid var(--bo-border) !important; border-radius: 999px !important;
  color: var(--bo-muted) !important; font-size: 12px !important; padding: 8px 16px !important;
}
.bo-rz-subnav .stButton > button[kind="primary"] {
  border-color: rgba(var(--bo-accent-light-rgb),0.45) !important; color: var(--bo-purple) !important;
  background: rgba(124,58,237,0.1) !important;
}

/* Weekly Brief cards */
.bo-rz-card {
  position: relative; overflow: hidden;
  margin: 0 0 18px; padding: 22px 24px 20px;
  border-radius: 18px; border: 1px solid var(--bo-border);
  background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%), var(--bo-surface);
}
.bo-rz-card-top {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  margin-bottom: 14px;
}
.bo-rz-eyebrow {
  font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--bo-blue);
}
.bo-rz-meta {
  font-size: 10px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--bo-dim);
}
.bo-rz-title {
  margin: 0 0 8px; font-size: clamp(1.35rem, 3vw, 1.75rem); font-weight: 800;
  letter-spacing: -0.02em; line-height: 1.15; color: var(--bo-text);
}
.bo-rz-sub { margin: 0 0 18px; font-size: 14px; line-height: 1.5; color: var(--bo-muted); }
.bo-rz-bullets { list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }
.bo-rz-bullets li { display: flex; gap: 12px; align-items: flex-start; }
.bo-rz-dot {
  width: 8px; height: 8px; border-radius: 50%; margin-top: 7px; flex-shrink: 0;
  background: var(--bo-blue); box-shadow: 0 0 10px rgba(96,165,250,0.45);
}
.bo-rz-bullets strong { display: block; font-size: 14px; color: var(--bo-text); margin-bottom: 2px; }
.bo-rz-bullets p { margin: 0; font-size: 13px; line-height: 1.45; color: var(--bo-muted); }
.bo-rz-card-foot {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--bo-border);
}
.bo-rz-copy-hint {
  font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--bo-purple); border: 1px solid rgba(var(--bo-accent-light-rgb),0.35);
  border-radius: 8px; padding: 6px 10px;
}
.bo-rz-foot-note { font-size: 11px; color: var(--bo-dim); }
.bo-rz-card-accent {
  position: absolute; left: 0; right: 0; bottom: 0; height: 3px;
  background: linear-gradient(90deg, var(--bo-purple-deep) 0%, var(--bo-purple) 45%, #fb923c 100%);
}

.bo-rz-weather-list { display: grid; gap: 14px; }
.bo-rz-weather-row, .bo-rz-lean-row {
  display: grid; grid-template-columns: 1.4fr 1fr auto; gap: 14px; align-items: center;
  padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05);
}
.bo-rz-weather-row:last-child, .bo-rz-lean-row:last-child { border-bottom: none; }
.bo-rz-weather-match { display: flex; align-items: center; gap: 10px; min-width: 0; }
.bo-rz-logo {
  width: 28px; height: 28px; object-fit: contain; border-radius: 50%;
  background: rgba(255,255,255,0.04); flex-shrink: 0;
}
.bo-rz-logo-fallback {
  width: 28px; height: 28px; border-radius: 50%; background: #1a1a22; flex-shrink: 0;
}
.bo-rz-match-label { font-size: 13px; font-weight: 700; color: var(--bo-text); letter-spacing: 0.04em; }
.bo-rz-match-sub { font-size: 11px; color: var(--bo-dim); margin-top: 2px; }
.bo-rz-weather-stats { font-size: 12px; color: var(--bo-muted); white-space: nowrap; }
.bo-rz-total-bar-wrap {
  display: flex; align-items: center; gap: 8px; justify-content: flex-end; min-width: 140px;
}
.bo-rz-total-label {
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--bo-dim);
}
.bo-rz-total-bar {
  height: 8px; border-radius: 4px; min-width: 8px;
}
.bo-rz-total-bar.neg { background: linear-gradient(90deg, #3b82f6, #60a5fa); }
.bo-rz-total-bar.pos { background: linear-gradient(90deg, #fb923c, #fbbf24); }
.bo-rz-total-val {
  font-family: var(--bo-mono); font-size: 13px; font-weight: 700; min-width: 42px; text-align: right;
}
.bo-rz-empty { padding: 28px; text-align: center; color: var(--bo-muted); font-size: 14px; }

/* Weather report — reference layout */
.bo-mu-weather { margin: 22px 0 8px; }
.bo-mu-weather .bo-wx-card { margin-top: 10px; }
.bo-wx-stack { display: grid; gap: 18px; margin-top: 8px; }
.bo-wx-card {
  position: relative; display: grid;
  grid-template-columns: minmax(160px, 1.1fr) minmax(280px, 2fr) minmax(100px, 0.7fr);
  gap: 20px; align-items: center;
  padding: 18px 22px 22px; border-radius: 14px;
  background: linear-gradient(180deg, rgba(18,18,26,0.98), rgba(10,10,14,0.98));
  border: 1px solid rgba(255,255,255,0.06);
  overflow: hidden;
}
.bo-wx-accent {
  position: absolute; left: 0; right: 0; bottom: 0; height: 3px;
  background: linear-gradient(90deg, var(--bo-purple-deep) 0%, var(--bo-purple) 35%, #fbbf24 100%);
}
.bo-wx-left { min-width: 0; }
.bo-wx-logos { display: flex; gap: 8px; margin-bottom: 10px; }
.bo-wx-logo {
  width: 32px; height: 32px; object-fit: contain; border-radius: 50%;
  background: rgba(255,255,255,0.05);
}
.bo-wx-logo-fallback {
  width: 32px; height: 32px; border-radius: 50%; background: #1a1a22;
}
.bo-wx-matchup {
  font-size: 15px; font-weight: 800; color: var(--bo-text);
  letter-spacing: 0.02em; margin-bottom: 6px;
}
.bo-wx-meta {
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--bo-dim);
}
.bo-wx-hours {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;
  padding: 12px 14px; border-radius: 10px;
  background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.05);
}
.bo-wx-hour { text-align: center; }
.bo-wx-hour-label {
  font-size: 10px; font-weight: 800; letter-spacing: 0.1em;
  color: #fbbf24; margin-bottom: 8px;
}
.bo-wx-hour-icon { font-size: 22px; line-height: 1; margin-bottom: 6px; }
.bo-wx-hour-temp {
  font-size: 22px; font-weight: 800; color: var(--bo-text); line-height: 1.1;
}
.bo-wx-hour-precip { font-size: 12px; color: var(--bo-muted); margin-top: 4px; }
.bo-wx-impact { text-align: center; padding-right: 6px; }
.bo-wx-impact-val {
  font-size: 34px; font-weight: 800; line-height: 1; letter-spacing: -0.02em;
}
.bo-wx-impact-val.pos { color: #4ade80; }
.bo-wx-impact-val.neg { color: #38bdf8; }
.bo-wx-impact-label {
  margin-top: 6px; font-size: 10px; font-weight: 700; letter-spacing: 0.12em;
  color: var(--bo-dim); text-transform: uppercase;
}

/* Sharp lines — odds comparison grid (props-style board) */
.bo-og-scroll {
  overflow-x: auto; margin-top: 12px;
  border: 1px solid var(--bo-border); border-radius: 14px;
  background: var(--bo-surface);
}
.bo-og-table {
  width: 100%; border-collapse: collapse; font-size: 12px;
  min-width: 980px;
}
.bo-og-table thead th {
  position: sticky; top: 0; z-index: 2;
  background: var(--bo-surface-2);
  border-bottom: 1px solid var(--bo-border-strong);
  padding: 10px 8px; text-align: center; font-weight: 700;
  color: var(--bo-muted); white-space: nowrap;
}
.bo-og-th-game { text-align: left !important; min-width: 180px; padding-left: 14px !important; }
.bo-og-th-time { min-width: 72px; }
.bo-og-th-ref, .bo-og-th-best, .bo-og-th-book { min-width: 88px; }

.bo-og-table tbody tr { border-bottom: 1px solid rgba(255,255,255,0.05); }
.bo-og-table tbody tr:hover { background: rgba(255,255,255,0.02); }
.bo-og-info, .bo-og-timecell {
  vertical-align: middle; padding: 10px 12px;
  border-right: 1px solid rgba(255,255,255,0.04);
}
.bo-og-market { font-weight: 700; color: var(--bo-text); font-size: 13px; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.bo-og-ref-tag {
  font-size: 9px; font-weight: 800; letter-spacing: 0.06em;
  padding: 2px 5px; border-radius: 4px;
  color: #93c5fd; background: rgba(59,130,246,0.15);
}
.bo-og-matchup {
  display: flex; align-items: center; gap: 6px;
  color: var(--bo-muted); font-size: 11px; font-weight: 600;
}
.bo-og-logo { width: 18px; height: 18px; object-fit: contain; }
.bo-og-date { font-weight: 600; color: var(--bo-text); }
.bo-og-time { font-size: 11px; color: var(--bo-dim); margin-top: 2px; }

.bo-og-cell {
  padding: 7px 8px; text-align: center;
  font-family: var(--bo-mono); font-size: 11px; font-weight: 600;
  color: var(--bo-text); white-space: nowrap;
  border-right: 1px solid rgba(255,255,255,0.03);
}
.bo-og-cell.empty { color: var(--bo-dim); }
.bo-og-cell.edge {
  box-shadow: inset 0 0 0 2px #22c55e;
  border-radius: 6px;
  background: rgba(34, 197, 94, 0.08);
  color: #bbf7d0;
}

.bo-og-best-book { display: block; margin-top: 3px; }
.bo-og-book-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 28px; padding: 2px 5px; border-radius: 4px;
  font-size: 9px; font-weight: 800; letter-spacing: 0.04em;
  color: #fff;
}
.bo-og-book-badge.pin { background: #1d4ed8; }
.bo-og-book-badge.dk { background: #15803d; }
.bo-og-book-badge.fd { background: #0284c7; }
.bo-og-book-badge.mgm { background: #a16207; }
.bo-og-book-badge.czr { background: #0f766e; }
.bo-og-book-badge.br { background: #c2410c; }
.bo-og-book-badge.espn { background: #dc2626; }
.bo-og-book-badge.hr { background: var(--bo-purple-deep); }
.bo-og-book-badge.fan { background: #475569; }
.bo-og-book-badge.bk { background: #334155; }

.bo-og-team-strip {
  display: flex; gap: 8px; overflow-x: auto; padding: 8px 0 12px;
  scrollbar-width: thin;
}
.bo-og-team-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 10px; border-radius: 999px;
  border: 1px solid var(--bo-border);
  background: var(--bo-bg-elevated);
  font-size: 11px; font-weight: 600; color: var(--bo-muted);
  white-space: nowrap; flex-shrink: 0;
}
.bo-og-team-chip.active {
  border-color: rgba(34, 197, 94, 0.55);
  color: #bbf7d0;
  background: rgba(34, 197, 94, 0.1);
}
.bo-og-chip-logo { width: 16px; height: 16px; object-fit: contain; }
.bo-og-chip-txt { margin-left: 2px; }

/* Sharp Lines — reference +EV prop board */
.bo-sl-toolbar {
  background: linear-gradient(180deg, #0c1526 0%, #0a1220 100%);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 12px;
  padding: 10px 12px 8px;
  margin: 8px 0 6px;
}
.bo-sl-filter-label {
  display: block;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #64748b;
  margin-bottom: 4px;
  min-height: 14px;
}
.bo-sl-toolbar [data-testid="stHorizontalBlock"] { gap: 8px !important; align-items: flex-end; }
.bo-sl-toolbar .stButton button {
  background: rgba(37, 99, 235, 0.18) !important;
  border: 1px solid rgba(59, 130, 246, 0.35) !important;
  color: #e2e8f0 !important;
  font-size: 11px !important;
  font-weight: 700 !important;
  border-radius: 8px !important;
  padding: 6px 8px !important;
  min-height: 36px !important;
  white-space: nowrap !important;
}
.bo-sl-toolbar [data-baseweb="select"] > div {
  background: rgba(15, 23, 42, 0.9) !important;
  border-color: rgba(100, 116, 139, 0.35) !important;
  border-radius: 8px !important;
  min-height: 36px !important;
}
.bo-sl-toolbar [data-baseweb="select"] span {
  color: #e2e8f0 !important;
  font-size: 12px !important;
  font-weight: 600 !important;
}
.bo-sl-toolbar .stCheckbox label span { font-size: 12px !important; color: #cbd5e1 !important; }
.bo-sl-toolbar-wrap {
  background: #0a1628; border: 1px solid rgba(59,130,246,0.25);
  border-radius: 10px; padding: 8px 10px; margin: 8px 0 6px;
}
.bo-sl-toolbar-wrap [data-testid="stHorizontalBlock"] { gap: 6px !important; align-items: center; }
.bo-sl-toolbar-wrap .stButton button {
  background: #1d4ed8 !important; border: 1px solid #2563eb !important;
  color: #fff !important; font-size: 12px !important; font-weight: 700 !important;
  border-radius: 8px !important; padding: 6px 10px !important; min-height: 34px !important;
}
.bo-sl-toolbar-wrap [data-baseweb="select"] > div {
  background: #1e3a8a !important; border-color: #2563eb !important;
  border-radius: 8px !important; min-height: 34px !important;
}
.bo-sl-toolbar-wrap [data-baseweb="select"] span { color: #e2e8f0 !important; font-size: 12px !important; font-weight: 600 !important; }
.bo-sl-meta { font-size: 11px; color: var(--bo-dim); margin: 4px 0 8px; padding-left: 4px; }

.bo-sl-scroll {
  overflow-x: auto; margin-top: 4px;
  border: 1px solid rgba(255,255,255,0.06); border-radius: 0;
  background: #060d18;
}
.bo-sl-table {
  width: 100%; border-collapse: collapse; font-size: 12px; min-width: 1320px;
}
.bo-sl-table thead th {
  position: sticky; top: 0; z-index: 2;
  background: #0b1526;
  border-bottom: 1px solid rgba(255,255,255,0.08);
  padding: 10px 8px; text-align: center; font-weight: 700;
  color: #94a3b8; white-space: nowrap; vertical-align: middle;
  font-size: 11px; letter-spacing: 0.04em; text-transform: none;
}
.bo-sl-th.player, .bo-sl-th.prop { text-align: left; padding-left: 12px; }
.bo-sl-th.ev { min-width: 84px; }
.bo-sl-th.best { min-width: 96px; }
.bo-sl-th.book { min-width: 64px; }
.bo-sl-hide-odds { display: block; font-size: 9px; font-weight: 600; color: #64748b; margin-top: 2px; }
.bo-sl-u { display: inline-block; font-size: 9px; color: #38bdf8; margin-left: 2px; }
.bo-sl-book-hdr {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  font-size: 9px; font-weight: 800; letter-spacing: 0.05em; color: #cbd5e1;
}
.bo-sl-book-logo { border-radius: 4px; display: block; }
.bo-sl-row { border-bottom: 1px solid rgba(255,255,255,0.04); height: 52px; }
.bo-sl-row:hover { background: rgba(255,255,255,0.025); }
.bo-sl-ev {
  font-weight: 800; font-family: var(--bo-mono); text-align: center;
  padding: 10px 8px; font-size: 15px; letter-spacing: -0.02em;
}
.bo-sl-ev.pos { color: #22c55e; }
.bo-sl-ev.neg { color: #f87171; }
.bo-sl-best { text-align: center; padding: 6px 8px; vertical-align: middle; }
.bo-sl-best-inner {
  display: inline-flex; align-items: center; gap: 6px;
  justify-content: center;
}
.bo-sl-best-odds {
  font-family: var(--bo-mono); font-weight: 800; font-size: 14px;
  color: #4ade80;
}
.bo-sl-player { padding: 6px 12px; min-width: 170px; vertical-align: middle; }
.bo-sl-player-inner { display: flex; align-items: center; gap: 10px; }
.bo-sl-plogo { width: 28px; height: 28px; object-fit: contain; flex-shrink: 0; }
.bo-sl-plogo-ph { width: 28px; height: 28px; border-radius: 50%; background: rgba(255,255,255,0.06); flex-shrink: 0; }
.bo-sl-player-meta { line-height: 1.15; }
.bo-sl-name { font-weight: 700; color: #f1f5f9; font-size: 13px; }
.bo-sl-pos { font-size: 10px; color: #64748b; font-weight: 600; margin-top: 2px; }
.bo-sl-prop {
  padding: 6px 12px; font-weight: 700; color: #e2e8f0;
  font-size: 12px; text-transform: lowercase; vertical-align: middle;
}
.bo-sl-fair, .bo-sl-imp, .bo-sl-kelly {
  text-align: center; font-family: var(--bo-mono); padding: 8px 6px;
  color: #cbd5e1; font-size: 12px; vertical-align: middle;
}
.bo-sl-kelly { color: #7dd3fc; font-weight: 700; }
.bo-sl-opp {
  text-align: center; padding: 6px; vertical-align: middle;
  display: flex; align-items: center; justify-content: center; gap: 5px;
  font-weight: 700; font-size: 12px; color: #e2e8f0;
}
.bo-sl-ologo { width: 20px; height: 20px; object-fit: contain; }
.bo-sl-opp-txt { font-family: var(--bo-mono); }
.bo-sl-def-cell { text-align: center; padding: 8px; vertical-align: middle; }
.bo-sl-def { font-weight: 800; font-family: var(--bo-mono); font-size: 12px; }
.bo-sl-def.good { color: #4ade80; }
.bo-sl-def.bad { color: #f87171; }
.bo-sl-def.mid { color: #94a3b8; }
.bo-sl-cell {
  text-align: center; font-family: var(--bo-mono); font-size: 12px; font-weight: 600;
  padding: 8px 6px; color: #94a3b8; white-space: nowrap; vertical-align: middle;
  border-left: 1px solid rgba(255,255,255,0.03);
}
.bo-sl-cell.empty { color: #334155; }
.bo-sl-cell.edge {
  color: #4ade80; font-weight: 800; font-size: 13px;
  background: rgba(34, 197, 94, 0.08);
}
.bo-sl-book-line {
  display: block; font-size: 10px; font-weight: 700; color: #64748b; line-height: 1.2;
}
.bo-sl-cell.edge .bo-sl-book-line { color: #86efac; }
.bo-sl-book-price { display: block; line-height: 1.2; }

/* Legacy sharp board rows */
.bo-sharp-board { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.bo-sharp-row {
  display: grid;
  grid-template-columns: 44px minmax(180px, 1.4fr) minmax(120px, 0.9fr) 72px minmax(80px, 0.7fr) 88px;
  gap: 12px; align-items: center;
  padding: 12px 14px;
  border: 1px solid var(--bo-border);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
}
.bo-sharp-row:hover { border-color: rgba(167,139,250,0.35); }
.bo-sharp-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 34px; padding: 4px 6px; border-radius: 8px;
  font-size: 10px; font-weight: 800; letter-spacing: 0.08em;
  color: var(--bo-purple); background: rgba(124,58,237,0.14);
}
.bo-sharp-prices { font-size: 10px; color: var(--bo-dim); margin-top: 4px; font-family: var(--bo-mono); }
.bo-sharp-edge-wrap { text-align: right; }
.bo-sharp-edge-main {
  display: block; font-family: var(--bo-mono); font-size: 16px; font-weight: 800; line-height: 1.1;
}
.bo-sharp-edge-main.pos { color: #fb923c; }
.bo-sharp-edge-main.neg { color: #93c5fd; }
.bo-sharp-edge-main.flat { color: var(--bo-dim); }
.bo-sharp-edge-bar {
  display: block; height: 3px; border-radius: 999px; margin-top: 6px;
  background: linear-gradient(90deg, transparent, currentColor);
  opacity: 0.85;
}
.bo-sharp-edge-bar.pos { color: #fb923c; }
.bo-sharp-edge-bar.neg { color: #93c5fd; }
.bo-sharp-book { font-size: 11px; color: var(--bo-dim); text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

@media (max-width: 900px) {
  .bo-sharp-row { grid-template-columns: 40px 1fr; grid-template-rows: auto auto auto; }
  .bo-proj-mid, .bo-sharp-edge-wrap, .bo-proj-pick, .bo-sharp-book { grid-column: 2; }
}

/* Legacy sharp table (unused) */
.bo-sharp-table {
  display: grid; gap: 0; margin-top: 12px;
  border: 1px solid var(--bo-border); border-radius: 14px;
  background: var(--bo-surface); overflow: hidden;
}
.bo-sharp-row {
  display: grid; grid-template-columns: 1.6fr 0.7fr 1fr 0.7fr 1fr 0.6fr;
  gap: 12px; align-items: center; padding: 12px 16px;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  font-size: 13px;
}
.bo-sharp-row:last-child { border-bottom: none; }
.bo-sharp-game { font-weight: 700; color: var(--bo-text); }
.bo-sharp-market, .bo-sharp-side { color: var(--bo-muted); }
.bo-sharp-fair, .bo-sharp-book { font-family: var(--bo-mono); color: var(--bo-text); }
.bo-sharp-bookname { font-size: 11px; color: var(--bo-dim); margin-left: 4px; }
.bo-sharp-edge { font-family: var(--bo-mono); font-weight: 700; text-align: right; }
.bo-sharp-edge.pos { color: var(--bo-green); }
.bo-sharp-edge.neg { color: var(--bo-blue); }

.bo-rz-watch-list, .bo-rz-prop-list { display: grid; gap: 10px; }
.bo-rz-watch-row, .bo-rz-prop-row {
  display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center;
  padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05);
}
.bo-rz-prop-row { grid-template-columns: 1.2fr 1fr auto auto; }
.bo-rz-watch-edge, .bo-rz-prop-edge {
  font-family: var(--bo-mono); font-size: 13px; font-weight: 700; color: var(--bo-green);
}
.bo-rz-prop-player { font-size: 14px; font-weight: 600; color: var(--bo-text); }
.bo-rz-prop-meta { font-size: 12px; color: var(--bo-dim); }
.bo-rz-prop-price { font-family: var(--bo-mono); font-size: 12px; color: var(--bo-muted); }

@media (max-width: 900px) {
  .bo-rz-header { grid-template-columns: 1fr; gap: 12px; }
  .bo-rz-weather-row, .bo-rz-lean-row { grid-template-columns: 1fr; gap: 8px; }
  .bo-rz-total-bar-wrap { justify-content: flex-start; }
  .bo-wx-card { grid-template-columns: 1fr; gap: 14px; }
  .bo-wx-impact { text-align: left; padding-right: 0; }
}

.bo-topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 0 16px; border-bottom: 1px solid var(--bo-border); margin-bottom: 0;
}
.bo-brand { display: flex; align-items: center; gap: 12px; }
.bo-brand-mark {
  width: 32px; height: 32px; border-radius: 10px;
  background: linear-gradient(145deg, var(--bo-purple-deep), var(--bo-purple));
  box-shadow: 0 0 28px var(--bo-purple-glow);
}
.bo-brand-text { font-weight: 800; font-size: 15px; letter-spacing: -0.02em; color: var(--bo-text); }
.bo-brand-sub { font-size: 11px; color: var(--bo-dim); font-weight: 500; margin-top: 1px; }
.bo-ticker {
  font-family: var(--bo-mono); font-size: 11px; color: var(--bo-dim);
  letter-spacing: 0.06em; text-transform: uppercase;
}
.bo-ticker em { color: var(--bo-purple); font-style: normal; }

.bo-nav-wrap {
  padding: 0 0 20px; margin-bottom: 24px;
  border-bottom: 1px solid var(--bo-border);
  max-width: 360px;
}
.bo-nav-label {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--bo-dim); margin-bottom: 8px;
}
.bo-nav-details {
  position: relative;
  width: 100%;
}
.bo-nav-details > summary {
  list-style: none;
  cursor: pointer;
  user-select: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--bo-surface);
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.35);
  border-radius: 12px;
  padding: 13px 16px;
  min-height: 48px;
  font-size: 15px;
  font-weight: 600;
  color: var(--bo-text);
  box-shadow: 0 0 20px rgba(var(--bo-accent-rgb), 0.08);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.bo-nav-details > summary::-webkit-details-marker { display: none; }
.bo-nav-details > summary::marker { display: none; content: ""; }
.bo-nav-details > summary::after {
  content: "▾";
  color: var(--bo-muted);
  font-size: 14px;
  line-height: 1;
  transition: transform 0.15s;
}
.bo-nav-details[open] > summary {
  border-color: rgba(var(--bo-accent-light-rgb), 0.55);
  box-shadow: 0 0 28px rgba(var(--bo-accent-rgb), 0.18);
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
}
.bo-nav-details[open] > summary::after {
  transform: rotate(180deg);
  color: var(--bo-purple);
}
.bo-nav-details > summary:hover {
  border-color: rgba(var(--bo-accent-light-rgb), 0.5);
}
.bo-nav-menu {
  margin-top: 6px;
  background: var(--bo-surface);
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.32);
  border-radius: 12px;
  max-height: min(380px, 55vh);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 6px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55), 0 0 28px rgba(var(--bo-accent-rgb), 0.12);
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--bo-accent-light-rgb), 0.45) var(--bo-surface-2);
}
.bo-nav-menu::-webkit-scrollbar { width: 8px; }
.bo-nav-menu::-webkit-scrollbar-track {
  background: var(--bo-surface-2);
  border-radius: 4px;
}
.bo-nav-menu::-webkit-scrollbar-thumb {
  background: rgba(var(--bo-accent-light-rgb), 0.4);
  border-radius: 4px;
}
.bo-nav-menu::-webkit-scrollbar-thumb:hover {
  background: rgba(var(--bo-accent-light-rgb), 0.6);
}
.bo-nav-link {
  display: block;
  padding: 11px 14px;
  border-radius: 8px;
  color: var(--bo-text) !important;
  text-decoration: none !important;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.35;
  transition: background 0.12s, color 0.12s;
}
.bo-nav-link:hover {
  background: rgba(var(--bo-accent-rgb), 0.16);
  color: var(--bo-purple) !important;
}
.bo-nav-link-active {
  background: rgba(var(--bo-accent-rgb), 0.22);
  color: var(--bo-purple) !important;
  font-weight: 600;
  box-shadow: inset 3px 0 0 var(--bo-purple);
}

/* Nav — Streamlit button menu (no page links) */
.bo-nav-wrap [data-testid="stButton"]:first-of-type button {
  background: var(--bo-surface) !important;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.35) !important;
  border-radius: 12px !important;
  min-height: 48px !important;
  text-align: left !important;
  font-size: 15px !important;
  font-weight: 600 !important;
  color: var(--bo-text) !important;
  box-shadow: 0 0 20px rgba(var(--bo-accent-rgb), 0.08) !important;
}
.bo-nav-menu-streamlit + div [data-testid="stButton"] button,
.bo-nav-wrap [data-testid="stButton"]:not(:first-of-type) button {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  text-align: left !important;
  font-weight: 500 !important;
  font-size: 14px !important;
  color: var(--bo-text) !important;
  padding: 10px 14px !important;
  min-height: 40px !important;
  justify-content: flex-start !important;
}
.bo-nav-wrap [data-testid="stButton"]:not(:first-of-type) button:hover,
.bo-nav-wrap [data-testid="stButton"] button[kind="primary"] {
  background: rgba(var(--bo-accent-rgb), 0.2) !important;
  color: var(--bo-purple) !important;
  border: none !important;
}
.bo-nav-menu-streamlit {
  display: block;
  margin-top: 6px;
  padding: 6px;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.32);
  border-radius: 12px;
  background: var(--bo-surface);
  max-height: min(380px, 55vh);
  overflow-y: auto;
}

/* Player props board */
.bo-pp-filters { margin-bottom: 16px !important; }
.bo-pp-board-compact .bo-pp-table { table-layout: fixed; width: 100%; font-size: 11px; border-collapse: collapse; }
.bo-pp-board-compact .bo-pp-table col.player-col { width: 22%; }
.bo-pp-board-compact .bo-pp-table col.prop-col { width: 11%; }
.bo-pp-board-compact .bo-pp-table col.line-col { width: 6%; }
.bo-pp-board-compact .bo-pp-table col.mine-col { width: 7%; }
.bo-pp-board-compact .bo-pp-table col.side-col { width: 7%; }
.bo-pp-board-compact .bo-pp-table col.price-col { width: 7%; }
.bo-pp-board-compact .bo-pp-table col.p-col { width: 9%; }
.bo-pp-board-compact .bo-pp-table col.be-col { width: 7%; }
.bo-pp-board-compact .bo-pp-table col.roi-col { width: 8%; }
.bo-pp-board-compact .bo-pp-table col.edge-col { width: 8%; }
.bo-pp-board-compact .bo-pp-table th,
.bo-pp-board-compact .bo-pp-table td { padding: 8px 5px; overflow: hidden; }
.bo-pp-board-compact .bo-pp-prop { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 0; }
.bo-pp-board-compact .bo-pp-prop-line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
.bo-pp-board-compact .bo-pp-player-inner { min-width: 0; gap: 8px; }
.bo-pp-board-compact .bo-pp-name { font-size: 12px; }
.bo-pp-board-compact .bo-pp-matchup-row { margin-top: 4px; }
.bo-pp-board-compact .bo-pp-avatar-wrap { width: 32px; height: 32px; }
.bo-pp-board-compact .bo-pp-headshot { width: 32px; height: 32px; }
.bo-pp-board-compact .bo-pp-team-badge { width: 14px; height: 14px; }
.bo-pp-line, .bo-pp-be, .bo-pp-price-cell {
  font-family: var(--bo-mono); font-size: 11px; text-align: center; white-space: nowrap;
}
.bo-pp-mine-val { font-family: var(--bo-mono); font-size: 12px; font-weight: 600; }
.bo-pp-pprob { text-align: center; white-space: nowrap; }
.bo-pp-p-label { display: block; font-size: 8px; color: var(--bo-dim); letter-spacing: 0.04em; }
.bo-pp-p-val { font-family: var(--bo-mono); font-size: 11px; font-weight: 600; }
.bo-pp-roi-pos { color: var(--bo-green); font-family: var(--bo-mono); font-weight: 700; }
.bo-pp-roi-neg { color: var(--bo-danger); font-family: var(--bo-mono); font-weight: 700; }
.bo-pp-roi { font-family: var(--bo-mono); font-size: 11px; text-align: center; white-space: nowrap; }
.bo-pp-board-compact .bo-pp-edge { font-size: 11px; text-align: center; }
.bo-props-table-wrap.bo-pp-board-compact { overflow-x: hidden; }
.bo-pp-table th {
  font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--bo-dim); font-weight: 600; padding: 10px 12px;
}
.bo-pp-table td { vertical-align: middle; padding: 14px 12px; }
.bo-pp-grade-cell { width: 52px; text-align: center; }
.bo-pp-grade {
  display: inline-block; min-width: 38px; padding: 4px 6px;
  border: 1px solid rgba(251, 146, 60, 0.55); border-radius: 8px;
  font-family: var(--bo-mono); font-size: 12px; font-weight: 700; color: #fb923c;
}
.bo-pp-grade-na { border-color: var(--bo-border); color: var(--bo-dim); font-weight: 500; }
.bo-pp-player-inner { display: flex; gap: 12px; align-items: flex-start; min-width: 200px; }
.bo-pp-player-text { min-width: 0; }
.bo-pp-avatar-wrap { position: relative; flex-shrink: 0; width: 40px; height: 40px; }
.bo-pp-headshot, .bo-pp-headshot-lg {
  width: 40px; height: 40px; border-radius: 50%; object-fit: cover;
  background: var(--bo-surface-2); border: 1px solid var(--bo-border);
}
.bo-pp-headshot-lg { width: 52px; height: 52px; }
.bo-pp-team-badge {
  position: absolute; right: -3px; bottom: -3px; width: 18px; height: 18px;
  border-radius: 50%; object-fit: contain; background: var(--bo-bg);
  border: 1px solid var(--bo-border);
}
.bo-pp-name-row, .bo-pp-detail-name-row {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.bo-pp-name, .bo-pp-detail-name { font-weight: 700; font-size: 14px; color: var(--bo-text); line-height: 1.2; }
.bo-pp-pos { font-size: 11px; font-weight: 600; color: #93c5fd; letter-spacing: 0.04em; }
.bo-pp-detail-meta { font-size: 11px; color: var(--bo-dim); margin-top: 2px; }
.bo-pp-matchup-row {
  display: flex; align-items: center; gap: 5px; margin-top: 6px;
  font-size: 11px; color: var(--bo-muted);
}
.bo-pp-at { color: var(--bo-dim); font-size: 10px; }
.bo-pp-kick { margin-left: 4px; color: var(--bo-dim); font-family: var(--bo-mono); font-size: 10px; }
.bo-pp-match-logo { width: 18px; height: 18px; object-fit: contain; border-radius: 2px; }
.bo-pp-opp-sp {
  margin-left: 2px; font-family: var(--bo-mono); font-size: 9px; font-weight: 600;
  color: var(--bo-dim); letter-spacing: 0.02em; white-space: nowrap;
}
.bo-pp-opp-tough { color: #93c5fd; }
.bo-pp-opp-soft { color: #fb923c; }
.bo-pp-prop-line { font-weight: 600; font-size: 13px; color: var(--bo-muted); white-space: nowrap; }
.bo-pp-ours-val { font-family: var(--bo-mono); font-size: 14px; font-weight: 600; color: var(--bo-text); }
.bo-pp-edge { font-family: var(--bo-mono); font-weight: 700; font-size: 13px; color: #fb923c; white-space: nowrap; }
.bo-pp-fair, .bo-pp-kelly {
  font-family: var(--bo-mono); font-size: 11px; text-align: center; color: var(--bo-muted);
}
.bo-pp-kelly { color: #93c5fd; font-weight: 700; }
.bo-pp-book-logo { vertical-align: middle; border-radius: 3px; }
.bo-pp-side-over { color: #fb923c; font-weight: 800; font-size: 12px; letter-spacing: 0.06em; }
.bo-pp-side-under { color: #93c5fd; font-weight: 800; font-size: 12px; letter-spacing: 0.06em; }
.bo-pp-book-row { display: flex; align-items: center; gap: 8px; }
.bo-pp-book-pill {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 28px; height: 22px; padding: 0 6px; border-radius: 4px;
  font-size: 9px; font-weight: 800; letter-spacing: 0.04em; color: #fff;
}
.bo-book-fd { background: #1493ff; }
.bo-book-dk { background: #22c55e; }
.bo-book-mgm { background: #ca8a04; }
.bo-book-czr { background: #eab308; color: #111; }
.bo-book-pin { background: var(--bo-purple-deep); }
.bo-book-generic { background: #52525b; }
.bo-pp-price { font-family: var(--bo-mono); font-size: 13px; font-weight: 600; color: var(--bo-text); }
.bo-pp-row { cursor: pointer; transition: background 0.12s ease; }
.bo-pp-row:hover td { background: rgba(var(--bo-accent-rgb), 0.05) !important; }
.bo-pp-row-active td { background: rgba(var(--bo-accent-rgb), 0.08) !important; }
.bo-pp-actual, .bo-pp-result { text-align: center; font-family: var(--bo-mono); font-size: 12px; }
.bo-result.push { color: #94a3b8; font-weight: 700; }
.bo-pp-perf-wrap { margin-top: 20px; min-height: 180px; }
.bo-pp-perf-inline {
  margin-top: 6px !important;
  margin-bottom: 14px !important;
  min-height: 0 !important;
}
.bo-pp-perf-inline .bo-pp-recent {
  margin-top: 0;
  border-radius: 0 0 12px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.04);
}
div[data-testid="block-container"]:has(.bo-pp-perf-inline) div[data-testid="stSelectbox"] [data-baseweb="select"] > div {
  border-radius: 12px 12px 0 0 !important;
  border-bottom-color: rgba(255, 255, 255, 0.04) !important;
}
.bo-pp-perf-hint {
  margin-top: 16px; padding: 14px 16px; border-radius: 12px;
  border: 1px dashed rgba(255,255,255,0.1); color: rgba(255,255,255,0.45);
  font-size: 13px; text-align: center;
}
.bo-pp-perf-panel { margin-bottom: 8px; }
.bo-pp-detail {
  border: 1px solid var(--bo-border); border-radius: 16px; background: var(--bo-surface);
  padding: 18px; margin-bottom: 12px;
}
.bo-pp-detail-head { display: flex; gap: 14px; align-items: flex-start; margin-bottom: 16px; }
.bo-pp-detail-note { font-size: 11px; color: var(--bo-dim); margin-top: 8px; }
.bo-pp-picker-label {
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--bo-dim); margin-bottom: 8px;
}
.bo-pp-mini { font-size: 12px; }
.bo-pp-detail-empty { color: var(--bo-muted); padding: 24px; text-align: center; }
.bo-pp-select-wrap { margin-bottom: 0; }
.bo-pp-select-wrap [data-baseweb="select"] > div {
  background: var(--bo-surface-2) !important;
  border: 1px solid rgba(var(--bo-accent-rgb), 0.35) !important;
  border-radius: 10px !important;
  color: var(--bo-text) !important;
  min-height: 42px !important;
}
.bo-pp-select-wrap [data-baseweb="select"] svg { fill: var(--bo-muted) !important; }
.bo-pp-select-wrap [data-baseweb="popover"] ul,
.bo-pp-select-wrap [data-baseweb="menu"] {
  background: #141418 !important;
  border: 1px solid rgba(var(--bo-accent-rgb), 0.35) !important;
  border-radius: 10px !important;
}
.bo-pp-select-wrap [data-baseweb="popover"] li,
.bo-pp-select-wrap [role="option"] {
  color: var(--bo-text) !important;
  background: transparent !important;
}
.bo-pp-select-wrap [data-baseweb="popover"] li:hover,
.bo-pp-select-wrap [aria-selected="true"] {
  background: rgba(var(--bo-accent-rgb), 0.22) !important;
  color: #c4b5fd !important;
}
.bo-pp-recent {
  border: 1px solid var(--bo-border); border-radius: 16px; background: var(--bo-surface);
  padding: 16px 16px 12px; margin-top: 4px;
}
.bo-pp-recent-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; }
.bo-pp-recent-title { font-size: 11px; font-weight: 700; letter-spacing: 0.1em; color: var(--bo-dim); }
.bo-pp-recent-median { font-size: 11px; color: var(--bo-dim); font-family: var(--bo-mono); }
.bo-pp-bars {
  display: flex; align-items: flex-end; gap: 10px; min-height: 150px;
  padding-top: 8px; overflow-x: auto;
}
.bo-pp-bar-wrap { flex: 1; min-width: 48px; max-width: 56px; display: flex; flex-direction: column; align-items: center; }
.bo-pp-bar-val { font-size: 11px; color: var(--bo-text); font-family: var(--bo-mono); font-weight: 600; margin-bottom: 6px; }
.bo-pp-bar { width: 100%; max-width: 40px; border-radius: 6px 6px 0 0; min-height: 6px; }
.bo-pp-bar-over { background: linear-gradient(180deg, #4ade80 0%, #16a34a 100%); }
.bo-pp-bar-under { background: linear-gradient(180deg, #fca5a5 0%, #ef4444 100%); }
.bo-pp-bar-mid { background: #71717a; }
.bo-pp-bar-axis { margin-top: 8px; height: 28px; display: flex; align-items: center; justify-content: center; }
.bo-pp-bar-logo { width: 24px; height: 24px; object-fit: contain; }
.bo-pp-bar-abbr { font-size: 9px; color: var(--bo-dim); font-weight: 700; letter-spacing: 0.04em; }
.bo-pp-recent-empty { color: var(--bo-muted); font-size: 12px; padding: 20px 8px; text-align: center; }
div[data-testid="stRadio"] > div { gap: 8px !important; }
div[data-testid="stRadio"] label {
  background: var(--bo-surface) !important; border: 1px solid var(--bo-border) !important;
  border-radius: 999px !important; padding: 4px 12px !important; font-size: 11px !important;
}
div[data-testid="stRadio"] label[data-checked="true"] {
  border-color: rgba(74, 222, 128, 0.45) !important; color: var(--bo-green) !important;
}

.bo-hero-eyebrow {
  font-family: var(--bo-mono); font-size: 11px; font-weight: 600;
  letter-spacing: 0.16em; text-transform: uppercase; color: var(--bo-purple); margin-bottom: 10px;
}
.bo-hero-title {
  margin: 0 0 8px; font-size: clamp(1.75rem, 4vw, 2.35rem); font-weight: 800;
  letter-spacing: -0.03em; line-height: 1.08; color: var(--bo-text) !important;
}
.bo-hero-sub {
  margin: 0 0 24px; font-size: 15px; line-height: 1.55; color: var(--bo-muted) !important; max-width: 640px;
}

.bo-terminal {
  display: none !important;
}

.bo-status-bar {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  padding: 10px 0 18px; margin-bottom: 4px;
  font-size: 13px; color: var(--bo-muted); border-bottom: 1px solid var(--bo-border);
}
.bo-status-live {
  color: var(--bo-green); font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.06em;
}
.bo-status-live::before {
  content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: var(--bo-green); margin-right: 6px; vertical-align: middle;
  box-shadow: 0 0 8px rgba(74, 222, 128, 0.6);
}
.bo-status-sep { color: var(--bo-dim); }
.bo-status-page { color: var(--bo-text); font-weight: 500; }

.bo-feature-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 20px 0;
}
.bo-feature-card {
  padding: 18px; border-radius: 14px; border: 1px solid var(--bo-border);
  background: var(--bo-surface);
}
.bo-feature-card h4 { margin: 0 0 6px; font-size: 14px; color: var(--bo-text); font-weight: 600; }
.bo-feature-card p { margin: 0; font-size: 13px; line-height: 1.5; color: var(--bo-muted); }

@media (max-width: 768px) {
  .bo-feature-grid { grid-template-columns: 1fr; }
}

.bo-stats {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
  background: var(--bo-border); border: 1px solid var(--bo-border);
  border-radius: 16px; overflow: hidden; margin-bottom: 24px;
}
.bo-stat { padding: 20px; background: var(--bo-surface); text-align: center; }
.bo-stat-label {
  display: block; font-size: 10px; color: var(--bo-muted); text-transform: uppercase;
  letter-spacing: 0.1em; margin-bottom: 8px; font-weight: 600;
}
.bo-stat strong { font-size: 28px; font-weight: 700; color: var(--bo-text); font-variant-numeric: tabular-nums; }
.bo-stat strong.purple { color: var(--bo-purple); }
.bo-stat strong.green { color: var(--bo-green); }

.bo-section-label {
  font-size: 13px; font-weight: 700; color: var(--bo-text) !important; margin: 28px 0 6px;
  letter-spacing: -0.01em;
}
.bo-section-hint { font-size: 13px; color: var(--bo-muted) !important; margin: 0 0 14px; }

.bo-callout {
  padding: 14px 16px; border-radius: 12px; margin-bottom: 16px;
  font-size: 14px; line-height: 1.5; border: 1px solid var(--bo-border);
}
.bo-callout-info { background: rgba(96, 165, 250, 0.08); border-color: rgba(96, 165, 250, 0.25); color: #bfdbfe !important; }
.bo-callout-success { background: var(--bo-green-dim); border-color: rgba(74, 222, 128, 0.3); color: #bbf7d0 !important; }
.bo-callout-warn { background: rgba(251, 146, 60, 0.08); border-color: rgba(251, 146, 60, 0.28); color: #fed7aa !important; }

.bo-panel {
  border: 1px solid var(--bo-border); border-radius: 16px;
  background: linear-gradient(180deg, var(--bo-surface-2) 0%, var(--bo-surface) 100%);
  padding: 16px 18px; margin-bottom: 10px;
}
.bo-panel-out { border-left: 3px solid var(--bo-danger); }
.bo-panel-q { border-left: 3px solid var(--bo-orange); }
.bo-panel-hit { border-left: 3px solid var(--bo-green); }

.bo-ev-card {
  display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center;
  padding: 14px 16px; margin-bottom: 8px;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.2); border-radius: 14px;
  background: rgba(17, 17, 24, 0.9);
}
.bo-ev-card:hover { border-color: rgba(var(--bo-accent-light-rgb), 0.4); }
.bo-proj-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.bo-ev-title { font-weight: 700; font-size: 14px; color: var(--bo-text); margin-bottom: 4px; }
.bo-ev-meta { font-size: 12px; color: var(--bo-muted); }
.bo-ev-pct { font-family: var(--bo-mono); font-size: 18px; font-weight: 600; color: var(--bo-green); text-align: right; }

/* +EV feed — Bettor Odds style */
.bo-feed { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
.bo-feed-item {
  display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: center;
  padding: 16px 18px; border-radius: 16px;
  border: 1px solid rgba(var(--bo-accent-light-rgb), 0.18);
  background: linear-gradient(135deg, rgba(17, 17, 24, 0.95) 0%, rgba(12, 12, 18, 0.98) 100%);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.bo-feed-item:hover {
  border-color: rgba(var(--bo-accent-light-rgb), 0.38);
  box-shadow: 0 0 24px rgba(var(--bo-accent-rgb), 0.12);
}
.bo-feed-top { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.bo-feed-player { font-size: 15px; font-weight: 700; color: var(--bo-text); letter-spacing: -0.01em; }
.bo-feed-lean { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; }
.bo-feed-market { font-size: 12px; color: var(--bo-muted); margin-bottom: 2px; }
.bo-feed-game { font-size: 12px; color: var(--bo-dim); margin-bottom: 10px; }
.bo-feed-prices { display: flex; gap: 18px; font-size: 12px; color: var(--bo-muted); margin-bottom: 8px; }
.bo-feed-prices strong { color: var(--bo-text); font-weight: 600; font-family: var(--bo-mono); }
.bo-feed-book {
  display: inline-block; font-size: 11px; font-weight: 600; color: var(--bo-purple);
  background: rgba(var(--bo-accent-rgb), 0.12); border: 1px solid rgba(var(--bo-accent-light-rgb), 0.25);
  padding: 3px 10px; border-radius: 999px;
}
.bo-feed-edge { text-align: right; min-width: 72px; }
.bo-feed-ev { font-family: var(--bo-mono); font-size: 22px; font-weight: 700; color: var(--bo-green); line-height: 1; }
.bo-feed-ev-label { font-size: 10px; font-weight: 600; color: var(--bo-dim); text-transform: uppercase; letter-spacing: 0.1em; margin-top: 4px; }

.bo-filter-inline {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  margin: 0 0 20px; padding: 0;
}
.bo-filter-inline .stSlider { flex: 1; min-width: 200px; max-width: 360px; }

/* Market moves — "Money on the dogs" chart */
.bo-dog-panel {
  position: relative; overflow: hidden;
  margin: 8px 0 24px; padding: 28px 20px 36px;
  border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.08);
  background:
    radial-gradient(ellipse 80% 55% at 50% 115%, rgba(var(--bo-accent-rgb), 0.35), transparent 55%),
    linear-gradient(180deg, #0a0a10 0%, #060608 100%);
}
.bo-dog-head { text-align: center; margin-bottom: 28px; }
.bo-dog-title {
  font-family: "Press Start 2P", var(--bo-mono);
  font-size: clamp(0.55rem, 1.8vw, 0.72rem);
  line-height: 1.7; letter-spacing: 0.04em;
  color: #f4f4f5; margin: 0 0 16px; text-transform: uppercase;
}
.bo-dog-badge {
  display: inline-block; padding: 10px 18px; border-radius: 6px;
  background: #f5e642; color: #0a0a0a;
  font-size: 11px; font-weight: 800; letter-spacing: 0.06em;
  text-transform: uppercase;
}
.bo-dog-stage {
  position: relative; height: 300px; max-width: 920px; margin: 0 auto;
  display: flex; align-items: center; justify-content: center;
}
.bo-dog-axis {
  position: absolute; left: 4%; right: 4%; top: 50%; height: 1px;
  background: rgba(255, 255, 255, 0.12); z-index: 1;
}
.bo-dog-bars {
  position: relative; z-index: 2;
  display: flex; align-items: stretch; justify-content: center;
  gap: clamp(12px, 2.5vw, 28px); height: 100%; width: 100%;
  padding: 0 8px;
}
.bo-dog-col {
  position: relative; width: 52px; flex-shrink: 0; height: 100%;
}
.bo-dog-logo {
  position: absolute; left: 50%; transform: translateX(-50%);
  width: 34px; height: 34px; object-fit: contain;
  filter: drop-shadow(0 2px 8px rgba(0,0,0,0.45)); z-index: 3;
}
.bo-dog-bar {
  position: absolute; left: 50%; transform: translateX(-50%);
  width: 38px; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  min-height: 10px; z-index: 2;
}
.bo-dog-bar.up {
  bottom: 50%; margin-bottom: 2px;
  background: linear-gradient(180deg, #fff176 0%, #f5e642 45%, #d4c40a 100%);
  box-shadow: 0 0 18px rgba(245, 230, 66, 0.45);
}
.bo-dog-bar.down {
  top: 50%; margin-top: 2px;
  background: linear-gradient(180deg, var(--bo-purple) 0%, var(--bo-purple-deep) 50%, var(--bo-purple-deep) 100%);
  box-shadow: 0 0 18px rgba(155, 93, 229, 0.45);
}
.bo-dog-col.pos .bo-dog-logo { bottom: calc(50% + var(--bar-h, 40px) + 8px); }
.bo-dog-col.neg .bo-dog-logo { top: calc(50% + var(--bar-h, 40px) + 8px); }
.bo-dog-val {
  font-size: 11px; font-weight: 800; color: #0a0a0a;
  background: rgba(0, 0, 0, 0.22); padding: 2px 6px; border-radius: 4px;
  white-space: nowrap;
}
.bo-dog-bar.down .bo-dog-val { color: #fff; background: rgba(0, 0, 0, 0.28); }

/* Market Moves — Pinnacle kickoff chart */
.bo-mm-panel {
  margin: 8px 0 24px; padding: 22px 18px 18px;
  border-radius: 18px; border: 1px solid rgba(255, 255, 255, 0.08);
  background:
    radial-gradient(ellipse 85% 55% at 50% 110%, rgba(var(--bo-accent-rgb), 0.28), transparent 58%),
    linear-gradient(180deg, #0e1117 0%, #060608 100%);
}
.bo-mm-chart-wrap {
  display: flex; gap: 10px; align-items: stretch;
}
.bo-mm-yaxis {
  position: relative; width: 52px; flex-shrink: 0;
  font-family: var(--bo-mono); font-size: 10px; color: var(--bo-dim);
  height: calc(var(--mm-axis-h, 220px) + 44px); margin-top: 44px;
}
.bo-mm-ylabel {
  position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  font-size: 9px; font-weight: 700; letter-spacing: 0.08em;
  line-height: 1.35; color: var(--bo-muted); text-transform: uppercase;
}
.bo-mm-ytick {
  position: absolute; left: 0; right: 0; height: 0;
  border-top: 1px dashed rgba(255, 255, 255, 0.06);
}
.bo-mm-ytick span {
  position: absolute; right: 4px; top: -7px;
}
.bo-mm-plot {
  position: relative; flex: 1; min-width: 0;
  padding-top: 44px;
}
.bo-mm-zero {
  position: absolute; left: 0; right: 0; top: calc(44px + var(--mm-zero, 55px));
  height: 0; border-top: 1px dashed rgba(255, 255, 255, 0.22); z-index: 1;
}
.bo-mm-scroll {
  overflow-x: auto; overflow-y: hidden; padding-bottom: 6px;
  scrollbar-color: rgba(255,255,255,0.15) transparent;
}
.bo-mm-bars {
  position: relative; display: flex; align-items: stretch; gap: 10px;
  min-width: max-content; height: var(--mm-h, 220px); z-index: 2;
}
.bo-mm-col {
  position: relative; width: 34px; flex-shrink: 0; height: 100%;
}
.bo-mm-logo {
  position: absolute; top: -40px; left: 50%; transform: translateX(-50%);
  width: 28px; height: 28px; object-fit: contain;
  filter: drop-shadow(0 2px 6px rgba(0,0,0,0.5)); z-index: 3;
}
.bo-mm-logo-fallback {
  border-radius: 50%; background: #1a1a22;
}
.bo-mm-bar {
  position: absolute; left: 50%; transform: translateX(-50%);
  width: 16px; border-radius: 3px;
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 800; z-index: 2;
}
.bo-mm-bar.up {
  background: linear-gradient(180deg, #a2f9b8 0%, #4ade80 55%, #22c55e 100%);
  box-shadow: 0 0 14px rgba(74, 222, 128, 0.35);
  color: #052e16;
}
.bo-mm-bar.down {
  background: linear-gradient(180deg, var(--bo-purple) 0%, var(--bo-purple-deep) 50%, var(--bo-purple-deep) 100%);
  box-shadow: 0 0 14px rgba(155, 93, 229, 0.35);
  color: #fff;
}
.bo-mm-dot {
  position: absolute; left: 50%; width: 6px; height: 6px; border-radius: 50%;
  background: rgba(255,255,255,0.25); transform: translate(-50%, -50%); z-index: 2;
}
.bo-mm-footer { margin-top: 18px; }
.bo-mm-callout {
  text-align: center; font-size: 12px; font-weight: 700;
  letter-spacing: 0.04em; color: var(--bo-text); margin-bottom: 14px;
}
.bo-mm-off { color: #f5e642; }
.bo-mm-meta {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; font-size: 11px; font-weight: 700;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--bo-muted);
}
.bo-mm-legend { display: inline-flex; align-items: center; gap: 8px; }
.bo-mm-swatch {
  display: inline-block; width: 12px; height: 12px; border-radius: 2px;
}
.bo-mm-swatch.up { background: #4ade80; }
.bo-mm-swatch.down { background: var(--bo-purple-deep); }
.bo-mm-order { margin-left: auto; color: var(--bo-dim); font-weight: 600; }

/* CLV Report — Bettor Odds */
.bo-clv-panel {
  border: 1px solid var(--bo-border); border-radius: 18px; background: var(--bo-surface);
  padding: 20px 22px; margin-bottom: 0;
}
.bo-clv-panel-hero {
  min-height: 520px; display: flex; flex-direction: column;
  background:
    radial-gradient(ellipse 90% 55% at 50% 0%, rgba(74, 222, 128, 0.12), transparent 58%),
    linear-gradient(180deg, rgba(255,255,255,0.02), transparent 40%),
    var(--bo-surface);
}
.bo-clv-panel-books { min-height: 520px; }
.bo-clv-panel-travel { margin-top: 0; }
.bo-clv-gap { height: 14px; }
.bo-clv-section-gap { height: 16px; }
.bo-clv-eyebrow {
  font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--bo-dim); margin-bottom: 14px;
}
.bo-clv-hero-pct {
  font-family: var(--bo-mono); font-size: clamp(3rem, 8vw, 4.5rem); font-weight: 800;
  color: var(--bo-green); line-height: 1; margin-bottom: 8px;
}
.bo-clv-glow-yes {
  color: var(--bo-green);
  text-shadow: 0 0 28px rgba(74, 222, 128, 0.55), 0 0 8px rgba(74, 222, 128, 0.35);
}
.bo-clv-glow-no {
  color: #fb923c;
  text-shadow: 0 0 28px rgba(251, 146, 60, 0.45), 0 0 8px rgba(251, 146, 60, 0.3);
}
.bo-clv-hero-sub { font-size: 14px; color: var(--bo-muted); margin-bottom: 22px; }
.bo-clv-hero-sub strong { color: var(--bo-text); }
.bo-clv-grid {
  display: grid; grid-template-columns: repeat(10, minmax(28px, 1fr)); gap: 8px;
  flex: 1; align-content: start; margin-bottom: 18px;
  max-height: 360px; overflow-y: auto; padding: 4px 6px 4px 2px;
  background: rgba(0, 0, 0, 0.18); border-radius: 12px;
  border: 1px solid var(--bo-border);
}
.bo-clv-cell-wrap { position: relative; width: 100%; aspect-ratio: 1; min-height: 28px; min-width: 28px; }
.bo-clv-cell-wrap:hover { z-index: 30; }
.bo-clv-cell {
  width: 100%; height: 100%; border-radius: 8px; min-height: 28px; cursor: default;
  display: flex; align-items: center; justify-content: center;
  border: 2px solid rgba(255, 255, 255, 0.28);
  box-sizing: border-box;
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}
.bo-clv-cell-wrap:hover .bo-clv-cell { transform: scale(1.08); }
.bo-clv-cell-tag {
  font-family: var(--bo-mono); font-size: 10px; font-weight: 900; letter-spacing: 0.06em;
  line-height: 1; pointer-events: none; user-select: none;
}
.bo-clv-cell-yes {
  background: #4ade80; border-color: #86efac;
  box-shadow: 0 0 0 1px rgba(74, 222, 128, 0.35), 0 2px 10px rgba(74, 222, 128, 0.35);
  color: #052e16;
}
.bo-clv-cell-no {
  background: #fb923c; border-color: #fdba74;
  box-shadow: 0 0 0 1px rgba(251, 146, 60, 0.35), 0 2px 10px rgba(251, 146, 60, 0.28);
  color: #431407;
}
.bo-clv-tip {
  display: none; position: absolute; left: 50%; bottom: calc(100% + 6px);
  transform: translateX(-50%); z-index: 40; min-width: 148px; max-width: 220px;
  padding: 8px 10px; border-radius: 8px; background: rgba(10, 14, 22, 0.97);
  border: 1px solid var(--bo-border); box-shadow: 0 10px 28px rgba(0, 0, 0, 0.5);
  pointer-events: none; text-align: center;
}
.bo-clv-cell-wrap:hover .bo-clv-tip { display: block; }
.bo-clv-tip-match {
  font-size: 11px; font-weight: 700; color: var(--bo-text); margin-bottom: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.bo-clv-tip-meta {
  font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
  color: var(--bo-muted); margin-bottom: 4px; line-height: 1.35;
}
.bo-clv-tip-clv { font-family: var(--bo-mono); font-size: 12px; font-weight: 800; color: var(--bo-green); }
.bo-clv-cell-wrap.no-toward .bo-clv-tip-clv { color: #fb923c; }
.bo-clv-legend {
  display: flex; flex-wrap: wrap; gap: 12px 18px; font-size: 11px; color: var(--bo-muted); margin-bottom: 18px;
}
.bo-clv-legend-note { color: var(--bo-dim); font-size: 10px; }
.bo-clv-dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.bo-clv-dot.yes { background: var(--bo-green); }
.bo-clv-dot.no { background: #fb923c; }
.bo-clv-foot-stats {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
  border-top: 1px solid var(--bo-border); padding-top: 16px; margin-top: auto;
}
.bo-clv-stat-card {
  background: rgba(0,0,0,0.28); border: 1px solid rgba(255,255,255,0.06);
  border-radius: 10px; padding: 10px 12px;
}
.bo-clv-stat-card span { font-size: 10px; color: var(--bo-dim); letter-spacing: 0.06em; }
.bo-clv-stat-card strong {
  display: block; font-family: var(--bo-mono); font-size: 14px; color: var(--bo-text); margin-bottom: 4px;
}
.bo-clv-best-sub {
  font-size: 10px; color: var(--bo-dim); margin: -8px 0 14px; line-height: 1.4;
}
.bo-clv-donut-wrap { display: flex; gap: 18px; align-items: flex-start; }
.bo-clv-donut {
  width: 120px; height: 120px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.bo-clv-donut-hole {
  width: 88px; height: 88px; border-radius: 50%; background: var(--bo-surface);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; padding: 4px;
}
.bo-clv-donut-logo { width: 22px !important; height: 22px !important; border-radius: 4px; }
.bo-clv-donut-top { font-family: var(--bo-mono); font-size: 16px; font-weight: 800; color: var(--bo-text); line-height: 1; }
.bo-clv-donut-label {
  font-size: 8px; color: var(--bo-dim); text-transform: uppercase; letter-spacing: 0.06em;
  text-align: center; line-height: 1.2;
}
.bo-clv-book-list { flex: 1; min-width: 0; }
.bo-clv-book-head {
  display: grid; grid-template-columns: 28px 1fr 72px 64px; gap: 8px; align-items: center;
  padding: 0 0 6px; margin-bottom: 4px; border-bottom: 1px solid var(--bo-border);
  font-size: 9px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--bo-dim);
}
.bo-clv-book-row {
  display: grid; grid-template-columns: 28px 1fr auto; gap: 10px; align-items: center;
  padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px;
}
.bo-clv-book-beat {
  font-size: 11px; color: var(--bo-dim); font-family: var(--bo-mono); min-width: 36px; text-align: right;
}
.bo-clv-book-logo { width: 22px !important; height: 22px !important; border-radius: 4px; display: block; }
.bo-clv-book-logo-fallback { width: 22px; height: 22px; border-radius: 4px; background: rgba(255,255,255,0.08); }
.bo-clv-book-name { color: var(--bo-text); font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bo-clv-clv-val {
  font-family: var(--bo-mono); font-size: 11px; font-weight: 700; color: var(--bo-green); text-align: right;
}
.bo-clv-clv-val.neg { color: #fb923c; }
.bo-clv-book-share-wrap { text-align: right; line-height: 1.15; }
.bo-clv-book-share { font-family: var(--bo-mono); font-weight: 800; font-size: 15px; display: block; min-width: 42px; text-align: right; }
.bo-clv-travel-hub {
  display: grid; grid-template-columns: 1fr 3px 1fr; gap: 20px; align-items: stretch; min-height: 280px;
}
.bo-clv-travel-spine {
  background: linear-gradient(180deg, rgba(74,222,128,0.5), rgba(251,146,60,0.45));
  border-radius: 999px; margin: 28px 0;
}
.bo-clv-branch-stack { display: flex; flex-direction: column; gap: 10px; }
.bo-clv-branch { display: flex; align-items: stretch; gap: 8px; }
.bo-clv-branch-left { flex-direction: row-reverse; }
.bo-clv-branch-right { flex-direction: row; }
.bo-clv-branch-line {
  width: 28px; min-height: 2px; align-self: center; border-radius: 999px; flex-shrink: 0;
}
.bo-clv-branch.toward .bo-clv-branch-line {
  background: linear-gradient(90deg, transparent, var(--bo-green));
  box-shadow: 0 0 10px rgba(74, 222, 128, 0.45);
}
.bo-clv-branch.against .bo-clv-branch-line {
  background: linear-gradient(90deg, transparent, #fb923c);
  box-shadow: 0 0 10px rgba(251, 146, 60, 0.35);
}
.bo-clv-branch-left.toward .bo-clv-branch-line { background: linear-gradient(270deg, transparent, var(--bo-green)); }
.bo-clv-branch-left.against .bo-clv-branch-line { background: linear-gradient(270deg, transparent, #fb923c); }
.bo-clv-branch-card {
  flex: 1; background: rgba(0,0,0,0.22); border: 1px solid rgba(255,255,255,0.06);
  border-radius: 10px; padding: 8px 10px;
}
.bo-clv-branch-clv { font-family: var(--bo-mono); font-size: 10px; color: var(--bo-green); margin-top: 4px; }
.bo-clv-branch.against .bo-clv-branch-clv { color: #fb923c; }
.bo-clv-travel-foot { font-size: 10px; color: var(--bo-dim); margin-top: 14px; text-align: center; }
.bo-clv-travel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.bo-clv-travel-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
@media (max-width: 1100px) {
  .bo-clv-travel-grid-3 { grid-template-columns: 1fr; }
}
.bo-clv-travel-head {
  font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--bo-dim); margin-bottom: 10px; text-align: center;
}
.bo-clv-travel-row {
  border-left: 3px solid transparent; padding: 8px 8px 8px 10px; margin-bottom: 8px;
  background: rgba(255,255,255,0.02); border-radius: 0 8px 8px 0;
}
.bo-clv-travel-row.toward { border-left-color: var(--bo-green); }
.bo-clv-travel-row.against { border-left-color: #fb923c; }
.bo-clv-travel-logos { display: flex; gap: 4px; margin-bottom: 4px; }
.bo-clv-travel-logo { width: 16px; height: 16px; object-fit: contain; }
.bo-clv-travel-match { font-size: 11px; font-weight: 700; color: var(--bo-text); }
.bo-clv-travel-line { font-size: 10px; font-family: var(--bo-mono); color: var(--bo-muted); margin-top: 2px; }
.bo-clv-travel-delta { color: var(--bo-dim); }
.bo-clv-empty { font-size: 12px; color: var(--bo-muted); padding: 24px 8px; text-align: center; }
.bo-clv-footer {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--bo-border);
  font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--bo-dim);
}

.fl-tag-under { color: #93c5fd; font-weight: 700; }
.fl-tag-over { color: var(--bo-orange); font-weight: 700; }
.fl-tag-ev { color: var(--bo-green); font-weight: 700; font-family: var(--bo-mono); }
.fl-mono { font-family: var(--bo-mono); font-size: 12px; color: var(--bo-muted) !important; }

.fl-section-sub { font-size: 13px; font-weight: 600; color: var(--bo-text) !important; letter-spacing: -0.01em; }
.fl-methodology { font-family: var(--bo-mono); font-size: 12px; color: var(--bo-dim) !important; margin-top: 16px; line-height: 1.6; }
.fl-methodology code { color: var(--bo-purple); background: rgba(124,58,237,0.12); padding: 2px 6px; border-radius: 4px; }

.stMetric label, .stMetric [data-testid="stMetricValue"] { color: var(--bo-text) !important; }
.stMetric [data-testid="stMetricDelta"] { color: var(--bo-muted) !important; }

.stExpander { border: 1px solid var(--bo-border) !important; border-radius: 12px !important; background: var(--bo-surface) !important; }

[data-testid="stPlotlyChart"] .modebar { display: none !important; }
.stCaption, [data-testid="stCaptionContainer"] { color: var(--bo-dim) !important; font-size: 12px !important; }
.gp-odds-sync {
  color: var(--bo-dim) !important;
  font-size: 12px !important;
  margin: 0 0 0.35rem 0 !important;
  line-height: 1.4;
}

@media (max-width: 768px) {
  .bo-stats { grid-template-columns: repeat(2, 1fr); }
  .bo-ticker { display: none; }
}
</style>
"""


def apply_theme() -> None:
    from lib.sport_context import get_sport, get_sport_config

    cfg = get_sport_config(get_sport())
    t = cfg["theme"]
    sport_css = f"""
<style>
:root {{
  --bo-purple: {t["accent"]};
  --bo-purple-deep: {t["accent_deep"]};
  --bo-purple-glow: {t["accent_glow"]};
  --bo-accent-rgb: {t["accent_rgb"]};
  --bo-accent-light-rgb: {t["accent_light_rgb"]};
  --bo-gradient-a: {t["gradient_a"]};
  --bo-gradient-b: {t["gradient_b"]};
}}
.stApp {{
  background:
    radial-gradient(ellipse 90% 60% at 50% -30%, {t["gradient_a"]}, transparent 50%),
    radial-gradient(ellipse 50% 40% at 100% 0%, {t["gradient_b"]}, transparent 40%),
    var(--bo-bg) !important;
}}
.stButton > button {{
  background: linear-gradient(135deg, {t["btn_start"]}, {t["btn_end"]}) !important;
  border: 1px solid {t["border_soft"]} !important;
  box-shadow: 0 0 32px {t["accent_glow"]} !important;
}}
.stButton > button:hover {{
  box-shadow: 0 0 40px {t["accent_glow"]} !important;
  border-color: {t["border_strong"]} !important;
}}
.bo-rz-mark {{
  background: linear-gradient(145deg, {t["accent_deep"]} 0%, #f5e642 100%) !important;
  box-shadow: 0 0 24px {t["accent_glow"]} !important;
}}
.bo-rz-card-accent, .bo-wx-accent {{
  background: linear-gradient(90deg, {t["accent_deep"]} 0%, {t["accent"]} 45%, #fb923c 100%) !important;
}}
.bo-mu-bar-fill.purple {{
  background: linear-gradient(90deg, {t["btn_start"]}, {t["accent"]}) !important;
}}
.bo-rz-sport.active {{
  border-color: {t["border_mid"]} !important;
  background: {t["chip_bg"]} !important;
}}
.bo-rz-header-row {{
  padding: 14px 0 12px !important;
  border-bottom: 1px solid var(--bo-border) !important;
  margin-bottom: 0 !important;
}}
.bo-rz-header-row [data-testid="column"] {{ padding-top: 0 !important; }}
.bo-rz-header-row [data-testid="column"]:nth-child(3) .stButton > button,
.bo-rz-header-row [data-testid="column"]:nth-child(4) .stButton > button {{
  padding: 8px 14px !important;
  min-height: 36px !important;
  width: 100% !important;
  border-radius: 10px !important;
  font-size: 12px !important;
  font-weight: 700 !important;
  letter-spacing: 0.06em !important;
  text-transform: uppercase !important;
}}
.bo-rz-header-row [data-testid="column"]:nth-child(3) .stButton > button[kind="secondary"],
.bo-rz-header-row [data-testid="column"]:nth-child(4) .stButton > button[kind="secondary"] {{
  background: transparent !important;
  border: 1px solid transparent !important;
  color: var(--bo-dim) !important;
  opacity: 0.45 !important;
}}
.bo-rz-header-row [data-testid="column"]:nth-child(3) .stButton > button[kind="primary"],
.bo-rz-header-row [data-testid="column"]:nth-child(4) .stButton > button[kind="primary"] {{
  background: {t["chip_bg"]} !important;
  border: 1px solid {t["border_mid"]} !important;
  color: var(--bo-text) !important;
  opacity: 1 !important;
}}
</style>
"""
    st.markdown(FL_CSS + sport_css, unsafe_allow_html=True)


def render_shell_header(*, week: int, year: int, markets: str = "—") -> None:
    from lib.sport_context import SPORT_CFB, SPORT_NFL, get_sport, set_sport

    sport = get_sport()
    st.markdown('<div class="bo-shell bo-rz-header-row">', unsafe_allow_html=True)
    c_search, c_nfl, c_cfb = st.columns([7.7, 0.65, 0.65])
    with c_search:
        st.markdown(
            '<div class="bo-rz-search">Search teams, players…</div>',
            unsafe_allow_html=True,
        )
    with c_nfl:
        if st.button(
            "NFL",
            key="bo_sport_nfl",
            use_container_width=True,
            type="primary" if sport == SPORT_NFL else "secondary",
        ):
            if sport != SPORT_NFL:
                set_sport(SPORT_NFL)
                st.rerun()
    with c_cfb:
        if st.button(
            "CFB",
            key="bo_sport_cfb",
            use_container_width=True,
            type="primary" if sport == SPORT_CFB else "secondary",
        ):
            if sport != SPORT_CFB:
                set_sport(SPORT_CFB)
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_primary_nav(
    nav_items: list[tuple[str, str]],
    *,
    active: str,
    key: str = "bo_page",
) -> str:
    """Horizontal primary nav — returns selected route (page or page:subtab)."""
    if key not in st.session_state:
        st.session_state[key] = active
    current = st.session_state[key]

    cols = st.columns(len(nav_items))
    for col, (label, route) in zip(cols, nav_items):
        with col:
            if st.button(
                label,
                key=f"{key}__btn_{route}_{label}",
                use_container_width=True,
                type="primary" if current == route else "secondary",
            ):
                st.session_state[key] = route
                current = route
    return st.session_state[key]


def render_live_status(*, page: str, week: int, year: int) -> None:
    """Subtle product status bar — no dev/terminal chrome."""
    page_label = html.escape(page)
    st.markdown(
        f"""
<div class="bo-status-bar">
  <span class="bo-status-live">Live</span>
  <span class="bo-status-sep">·</span>
  <span>Week {week}</span>
  <span class="bo-status-sep">·</span>
  <span>{year} Season</span>
  <span class="bo-status-sep">·</span>
  <span class="bo-status-page">{page_label}</span>
</div>
""",
        unsafe_allow_html=True,
    )


def render_nav_dropdown(options: list[str], *, key: str = "bo_main_nav", default: str | None = None) -> str:
    """Section picker — in-app only (no link navigation / new tabs)."""
    fallback = default if default and default in options else options[0]
    open_key = f"{key}__open"
    if key not in st.session_state:
        st.session_state[key] = fallback
    current = st.session_state[key]
    if current not in options:
        current = fallback
        st.session_state[key] = current

    st.markdown('<div class="bo-nav-wrap"><div class="bo-nav-label">Section</div>', unsafe_allow_html=True)
    if st.button(f"{current}  ▾", key=f"{key}__trigger", use_container_width=True, type="secondary"):
        st.session_state[open_key] = not st.session_state.get(open_key, False)
        st.rerun()

    if st.session_state.get(open_key, False):
        st.markdown('<div class="bo-nav-menu-streamlit">', unsafe_allow_html=True)
        for i, opt in enumerate(options):
            active = opt == current
            if st.button(
                opt,
                key=f"{key}__opt_{i}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                st.session_state[key] = opt
                st.session_state[open_key] = False
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    return st.session_state[key]


def week_season_filters(prefix: str, *, year: int, week: int) -> tuple[int, int]:
    """Compact season/week picker — select menus, not stepper widgets."""
    from lib.sport_context import default_season_week, get_sport, week_options

    opts = week_options()
    st.markdown('<div class="bo-filter-bar">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1, 3])
    seasons = [2026]
    week_idx = opts.index(week) if week in opts else 0
    year = 2026
    with c1:
        st.markdown('<div class="filter-field">', unsafe_allow_html=True)
        y = st.selectbox(
            "Season",
            seasons,
            index=0,
            key=f"{prefix}_year_{get_sport()}",
        )
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="filter-field">', unsafe_allow_html=True)
        w = st.selectbox(
            "Week",
            opts,
            index=week_idx,
            key=f"{prefix}_week_{get_sport()}",
            format_func=lambda x: str(x),
        )
        st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    return int(y), int(w)


def loading_skeleton(n: int = 4) -> None:
    st.markdown("".join('<div class="bo-skeleton"></div>' * n), unsafe_allow_html=True)


def empty_state(message: str) -> None:
    st.markdown(f'<div class="bo-empty">{html.escape(message)}</div>', unsafe_allow_html=True)


def render_terminal(status: str = "live · refreshed on load") -> None:
    """Deprecated — use render_live_status."""
    render_live_status(page=status, week=1, year=2026)


def section_header(title: str, subtitle: str = "", eyebrow: str = "") -> None:
    parts = []
    if eyebrow:
        parts.append(f'<div class="bo-hero-eyebrow">{html.escape(eyebrow)}</div>')
    parts.append(f'<h1 class="bo-hero-title">{html.escape(title)}</h1>')
    if subtitle:
        parts.append(f'<p class="bo-hero-sub">{html.escape(subtitle)}</p>')
    st.markdown("\n".join(parts), unsafe_allow_html=True)


def section_label(title: str, hint: str = "") -> None:
    st.markdown(f'<div class="bo-section-label">{html.escape(title)}</div>', unsafe_allow_html=True)
    if hint:
        st.markdown(f'<p class="bo-section-hint">{html.escape(hint)}</p>', unsafe_allow_html=True)


def stat_strip(items: list[tuple[str, str, str]]) -> None:
    cols = "".join(
        f'<div class="bo-stat"><span class="bo-stat-label">{html.escape(lbl)}</span>'
        f'<strong class="{acc}">{html.escape(str(val))}</strong></div>'
        for lbl, val, acc in items
    )
    st.markdown(f'<div class="bo-stats">{cols}</div>', unsafe_allow_html=True)


def callout(msg: str, kind: str = "info") -> None:
    cls = {"info": "bo-callout-info", "success": "bo-callout-success", "warn": "bo-callout-warn"}.get(kind, "bo-callout-info")
    st.markdown(f'<div class="bo-callout {cls}">{msg}</div>', unsafe_allow_html=True)


def show_dataframe(df: pd.DataFrame, **kwargs) -> None:
    if df is None or df.empty:
        callout("No rows to display.", "info")
        return
    hide = {
        "book_id",
        "source",
        "snapshot_source",
        "pull_timestamp_utc",
        "pull_source",
        "pull_tab",
        "pull_origin",
        "market_key",
    }
    out = df.copy()
    out = out.drop(columns=[c for c in hide if c in out.columns], errors="ignore")
    rename = {
        "book": "Book",
        "event": "Game",
        "selection": "Side",
        "price": "Price",
        "line": "Line",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    if "Book" in out.columns:
        from lib.display import customer_book

        out["Book"] = out["Book"].map(customer_book)
    st.dataframe(out, use_container_width=True, hide_index=True, **kwargs)


def status_class(status: str) -> str:
    s = (status or "").upper()
    if any(x in s for x in ("OUT", "IR", "INJURED", "SUSPEND")):
        return "bo-panel-out"
    if "DOUBT" in s or "QUESTION" in s or "DAY" in s:
        return "bo-panel-q"
    if "EXPECTED" in s or "ACTIVE" in s or "PROBABLE" in s:
        return "bo-panel-hit"
    return "bo-panel"


# Legacy aliases
render_fl_header = render_shell_header
stat_pill = stat_strip
