"""Pricing page CSS — extends bettor-odds theme."""
from __future__ import annotations

import html


def pricing_board_css() -> str:
    return """
<style>
.bo-pe-header {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: center;
  padding: 18px 20px; margin-bottom: 12px;
  border: 1px solid var(--bo-border); border-radius: 16px;
  background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, var(--bo-surface) 100%);
}
.bo-pe-team { display: flex; align-items: center; gap: 12px; }
.bo-pe-team.home { justify-content: flex-end; }
.bo-pe-team-meta.right { text-align: right; }
.bo-pe-logo { width: 52px; height: 52px; object-fit: contain; }
.bo-pe-abbr {
  font-size: 22px; font-weight: 800; letter-spacing: -0.02em; color: var(--bo-text);
  border-left: 3px solid var(--team-color, #52525b); padding-left: 8px;
}
.bo-pe-team.home .bo-pe-abbr { border-left: none; border-right: 3px solid var(--team-color, #52525b); padding-right: 8px; padding-left: 0; }
.bo-pe-name { font-size: 12px; color: var(--bo-muted); margin-top: 2px; max-width: 180px; }
.bo-pe-center { text-align: center; min-width: 140px; }
.bo-pe-score {
  font-family: var(--bo-mono); font-size: 28px; font-weight: 800; color: var(--bo-text);
  display: flex; align-items: center; justify-content: center; gap: 8px;
}
.bo-pe-score.proj span { color: var(--bo-text); }
.bo-pe-score.live span { color: #4ade80; }
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
.bo-pe-sel { font-weight: 600; color: var(--bo-text); }
.bo-pe-line { font-family: var(--bo-mono); color: var(--bo-muted); font-size: 12px; }
.bo-pe-proj {
  font-family: var(--bo-mono); font-size: 15px; font-weight: 700; color: var(--bo-text);
}
.bo-pe-model-price {
  font-family: var(--bo-mono); font-weight: 700; color: var(--bo-text);
}
.bo-pe-model-price.bo-pe-edge-pos { color: #4ade80; }
.bo-pe-model-price.bo-pe-edge-neg { color: #f87171; }
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
}
.bo-pe-odds-cell { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }
.bo-pe-odds { font-family: var(--bo-mono); font-size: 15px; font-weight: 700; color: var(--bo-text); }
.bo-pe-empty { color: var(--bo-dim); }
.bo-pe-empty-block {
  padding: 24px; text-align: center; color: var(--bo-muted);
  border: 1px dashed var(--bo-border); border-radius: 12px; margin-bottom: 16px;
}
.bo-pe-prop-name { font-size: 15px; font-weight: 800; letter-spacing: 0.02em; color: var(--bo-text); }
.bo-pe-prop-match { font-size: 11px; color: var(--bo-muted); margin-top: 3px; }
.bo-pe-prop-line, .bo-pe-prop-proj {
  font-family: var(--bo-mono); font-size: 16px; font-weight: 700; color: var(--bo-text);
}
.bo-pe-props { margin-top: 8px; }
.bo-pe-prop-tabs { margin-bottom: 12px; }
/* Override sport accent on pricing page — white default, edge colors only on model column */
.bo-pe-board .bo-pe-model-price { color: var(--bo-text) !important; }
.bo-pe-board .bo-pe-model-price.bo-pe-edge-pos { color: #4ade80 !important; }
.bo-pe-board .bo-pe-model-price.bo-pe-edge-neg { color: #f87171 !important; }
.bo-pe-score.final span { color: var(--bo-text); font-weight: 800; }
.bo-pe-result {
  display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.06em;
  padding: 3px 8px; border-radius: 999px; text-transform: uppercase;
}
.bo-pe-result.hit { background: rgba(74, 222, 128, 0.15); color: #4ade80; }
.bo-pe-result.miss { background: rgba(248, 113, 113, 0.15); color: #f87171; }
.bo-pe-result.push { background: rgba(161, 161, 170, 0.15); color: #a1a1aa; }
.bo-pe-actual, .bo-pe-roi { font-family: var(--bo-mono); font-size: 12px; color: var(--bo-muted); }
.bo-pe-roi.bo-pe-edge-pos { color: #4ade80; }
.bo-pe-roi.bo-pe-edge-neg { color: #f87171; }
</style>
"""


def esc(val: str | None) -> str:
    return html.escape(str(val or ""))
