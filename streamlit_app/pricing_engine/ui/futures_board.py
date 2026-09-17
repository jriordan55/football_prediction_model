"""Futures board — season market grid."""
from __future__ import annotations

from typing import Any

from .theme import esc


def render_futures_table(futures: dict[str, Any]) -> str:
    teams = futures.get("teams") or []
    is_nfl = str(futures.get("sport") or "").lower() == "nfl"

    rows: list[str] = []
    for t in teams:
        name = t.get("team") or ""
        mw = t.get("mean_wins")
        sb = t.get("sb_win") or 0
        conf = t.get("conf_winner") or 0
        div = t.get("division_winner") or 0
        po = t.get("make_playoffs") or 0

        def _price(prob: float) -> str:
            from lib.odds_math import implied_to_american

            return implied_to_american(prob) or "—"

        rows.append(
            f"""<tr>
              <td class="pe-market">{esc(name)}</td>
              <td class="pe-line">{esc(str(mw) if mw is not None else "")}</td>
              <td><span class="pe-price">{esc(_price(po))}</span></td>
              {"<td><span class='pe-price'>" + esc(_price(div)) + "</span></td>" if is_nfl else ""}
              <td><span class="pe-price">{esc(_price(conf))}</span></td>
              <td><span class="pe-price">{esc(_price(sb))}</span></td>
            </tr>"""
        )

    nfl_cols = "<th>Div</th>" if is_nfl else ""
    return f"""
<div class="pe-board">
  <table class="pe-table">
    <thead><tr>
      <th>Team</th><th>Exp W</th><th>Playoffs</th>{nfl_cols}<th>Conf</th><th>SB</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</div>"""


def render_win_total_lines(futures: dict[str, Any], *, team: str | None = None) -> str:
    markets = futures.get("markets") or []
    wt = [m for m in markets if m.get("market") == "Win Total" and m.get("selection") == "Over"]
    if team:
        wt = [m for m in wt if str(m.get("team") or "").lower() == team.lower()]
    wt = wt[:24]
    cards = []
    for m in wt:
        cards.append(
            f"""<div class="pe-prop-card">
              <div class="pe-prop-name">{esc(m.get("team"))}</div>
              <div class="pe-prop-row"><span>O {esc(str(m.get("line")))}</span>
              <span class="pe-price">{esc(str(m.get("price") or "—"))}</span></div>
            </div>"""
        )
    if not cards:
        return ""
    return f'<div class="pe-props-grid">{"".join(cards)}</div>'
