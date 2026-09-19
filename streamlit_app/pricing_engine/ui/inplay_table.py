"""Render play-by-play pricing log — team-centric, best available price."""

from __future__ import annotations

import html

from typing import Any

from lib.book_logos import book_logo_img
from lib.team_logos import logo_img_html


def _esc(val: object) -> str:
    if val is None:
        return "—"
    txt = str(val).strip()
    if not txt or txt.lower() in ("nan", "none"):
        return "—"
    return html.escape(txt)


def _situation_chip(quarter: object, clock: object, down: object, distance: object, yard_line: object) -> str:
    parts: list[str] = []
    if quarter is not None:
        parts.append(f"Q{html.escape(str(quarter))}")
    if clock:
        parts.append(html.escape(str(clock)))
    if down is not None and distance is not None:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(int(down), "th")
        parts.append(f"{int(down)}{suffix} &amp; {int(distance)}")
    elif down is not None:
        parts.append(f"{int(down)} down")
    if yard_line:
        parts.append(html.escape(str(yard_line)))
    return " · ".join(parts) if parts else "—"


def _book_odds_cell(val: object, book_id: object) -> str:
    bid = str(book_id or "").strip()
    price = _esc(val)
    if price == "—":
        return price
    if not bid:
        return f'<span class="bo-pbp-odds-val">{price}</span>'
    logo = book_logo_img(bid, size=14, cls="bo-pbp-cell-book")
    return f'<span class="bo-pbp-odds-cell">{logo}<span class="bo-pbp-odds-val">{price}</span></span>'


def _book_line_cell(val: object, book_id: object) -> str:
    bid = str(book_id or "").strip()
    line = _esc(val)
    if line == "—":
        return line
    if not bid:
        return f'<span class="bo-pbp-line-cell">{line}</span>'
    logo = book_logo_img(bid, size=14, cls="bo-pbp-cell-book")
    return f'<span class="bo-pbp-line-cell">{line}{logo}</span>'


def _total_stack_html(team: dict[str, Any]) -> str:
    line = _esc(team.get("total"))
    over = _esc(team.get("total_over_odds"))
    under = _esc(team.get("total_under_odds"))
    over_book = str(team.get("total_over_book") or team.get("total_book") or "").strip()
    under_book = str(team.get("total_under_book") or team.get("total_book") or "").strip()

    if line == "—" and over == "—" and under == "—":
        return "—"

    over_bits: list[str] = []
    if line != "—":
        over_bits.append(line)
    if over != "—":
        over_bits.append(f"O {over}")
    over_txt = " ".join(over_bits) if over_bits else "—"
    over_logo = book_logo_img(over_book, size=14, cls="bo-pbp-cell-book") if over_book else ""
    over_row = (
        f'<div class="bo-pbp-tot-over">'
        f'<span class="bo-pbp-tot-line">{over_txt}</span>{over_logo}'
        f"</div>"
    )

    under_txt = f"U {under}" if under != "—" else "—"
    under_logo = book_logo_img(under_book, size=14, cls="bo-pbp-cell-book") if under_book else ""
    under_row = (
        f'<div class="bo-pbp-tot-under">'
        f'<span class="bo-pbp-tot-line">{under_txt}</span>{under_logo}'
        f"</div>"
    )

    return f'<div class="bo-pbp-tot-stack">{over_row}{under_row}</div>'


def _team_cells(team: dict[str, Any], *, include_total: bool) -> str:
    logo = logo_img_html(str(team.get("logo") or ""), cls="bo-pbp-logo", alt=str(team.get("name") or ""))
    total_cell = f'<td class="bo-pbp-mono bo-pbp-total" rowspan="2">{_total_stack_html(team)}</td>' if include_total else ""
    return (
        f'<td class="bo-pbp-team"><div class="bo-pbp-team-inner">{logo}'
        f'<span class="bo-pbp-team-name">{_esc(team.get("name"))}</span></div></td>'
        f'<td class="bo-pbp-mono bo-pbp-score">{_esc(team.get("score"))}</td>'
        f'<td class="bo-pbp-mono">{_esc(team.get("exp_spread"))}</td>'
        f'<td class="bo-pbp-mono">{_book_line_cell(team.get("spread"), team.get("spread_book"))}</td>'
        f'<td class="bo-pbp-mono">{_esc(team.get("exp_total"))}</td>'
        f"{total_cell}"
        f'<td class="bo-pbp-mono bo-pbp-odds">{_book_odds_cell(team.get("ml_odds"), team.get("ml_book"))}</td>'
        f'<td class="bo-pbp-mono">{_esc(team.get("win_pct"))}</td>'
        f'<td class="bo-pbp-mono bo-pbp-odds">{_esc(team.get("exp_price"))}</td>'
    )


def _play_table_rows(away: dict[str, Any], home: dict[str, Any]) -> str:
    return (
        f"<tr>{_team_cells(away, include_total=True)}</tr>"
        f"<tr>{_team_cells(home, include_total=False)}</tr>"
    )


def render_pbp_log_html(
    plays: list[dict[str, Any]],
    *,
    live: bool = False,
) -> str:
    if not plays:
        msg = (
            "Waiting for ESPN plays — log fills automatically once the game is live."
            if live
            else "No play-by-play available yet."
        )
        return f'<div class="bo-pe-empty-block">{html.escape(msg)}</div>'

    parts = [
        '<div class="bo-pbp-log">',
        '<div class="bo-pbp-log-head">',
        '<div class="bo-pbp-log-title">Play-by-Play Log</div>',
        '<div class="bo-pbp-book"><span>Best price available</span></div>',
        "</div>",
    ]

    for play in plays:
        sit = _situation_chip(
            play.get("quarter"),
            play.get("clock"),
            play.get("down"),
            play.get("distance"),
            play.get("yard_line"),
        )
        away = play.get("away") or {}
        home = play.get("home") or {}
        parts.extend([
            '<article class="bo-pbp-card">',
            f'<header class="bo-pbp-card-head"><span class="bo-pbp-situation">{sit}</span></header>',
            f'<p class="bo-pbp-play-text">{_esc(play.get("play_text"))}</p>',
            '<div class="bo-props-table-wrap bo-pe-props">',
            '<table class="bo-props-table bo-pe-table bo-pbp-table">',
            "<colgroup>",
            '<col class="team-col" /><col class="score-col" />',
            '<col class="spr-col" /><col class="spr-col" />',
            '<col class="tot-col" /><col class="tot-col" />',
            '<col class="odds-col" /><col class="pct-col" /><col class="odds-col" />',
            "</colgroup>",
            "<thead><tr>",
            "<th>Team</th><th>Score</th>",
            "<th>Exp Spr</th><th>Spread</th>",
            "<th>Exp Tot</th><th>Total</th>",
            "<th>ML Odds</th><th>Win %</th><th>Exp Price</th>",
            "</tr></thead><tbody>",
            _play_table_rows(away, home),
            "</tbody></table></div></article>",
        ])

    parts.append("</div>")
    return "".join(parts)


def render_inplay_html(
    plays: list[dict[str, Any]] | Any,
    *,
    live: bool = False,
    market_filter: str = "all",
) -> str:
    if plays is None:
        plays = []
    if hasattr(plays, "empty"):
        plays = [] if plays.empty else []
    return render_pbp_log_html(plays, live=live)
