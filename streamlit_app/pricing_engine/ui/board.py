"""Pricing board — only markets with live sportsbook quotes."""
from __future__ import annotations

import html
from typing import Any

from lib.book_logos import book_logo_img
from lib.display import team_abbr
from lib.odds_math import ev_pct, implied_to_american
from lib.team_logos import enrich_row_logos, logo_img_html

from pricing_engine.grading import grade_market_quote


def esc(val: object) -> str:
    return html.escape(str(val if val is not None else ""))


def _fmt_spread_line(line: float | None) -> str:
    if line is None:
        return ""
    return f"{float(line):+.1f}"


def _book_cell(quote: dict[str, Any] | None) -> str:
    if not quote:
        return '<span class="bo-pe-empty">—</span>'
    bid = str(quote.get("book_id") or "")
    price = quote.get("price")
    if price is None:
        return '<span class="bo-pe-empty">—</span>'
    if isinstance(price, str):
        price_txt = price
    else:
        try:
            pi = int(float(price))
            price_txt = f"+{pi}" if pi > 0 else str(pi)
        except (TypeError, ValueError):
            price_txt = str(price)
    logo = book_logo_img(bid, size=18, cls="bo-pe-book-logo")
    return f'<div class="bo-pe-odds-cell">{logo}<span class="bo-pe-odds">{esc(price_txt)}</span></div>'


def _spread_projection(
    quote: dict[str, Any],
    sim: dict[str, Any] | None,
    *,
    home: str,
    away: str,
) -> str:
    if not sim:
        return "—"
    sb = sim.get("scoreboard") or {}
    hm, am = sb.get("home_mean"), sb.get("away_mean")
    if hm is None or am is None:
        return "—"
    margin = float(hm) - float(am)
    spread_home = -margin
    sel = str(quote.get("selection") or "")
    if sel == home:
        return f"{spread_home:+.1f}"
    if sel == away:
        return f"{-spread_home:+.1f}"
    return f"{spread_home:+.1f}"


def _projection_for_quote(
    quote: dict[str, Any],
    sim: dict[str, Any] | None,
    *,
    home: str,
    away: str,
) -> str:
    if not sim:
        return "—"
    market = str(quote.get("market") or "")
    sel = str(quote.get("selection") or "")
    sb = sim.get("scoreboard") or {}

    if market == "Spread":
        return _spread_projection(quote, sim, home=home, away=away)
    if market == "Total":
        total = sb.get("total_mean")
        if total is not None:
            return f"{float(total):g}"
    if market == "Team Total":
        sel_l = sel.lower()
        if sel_l == home.lower() or sel == home:
            val = sb.get("home_mean")
        elif sel_l == away.lower() or sel == away:
            val = sb.get("away_mean")
        else:
            val = None
        if val is not None:
            return f"{float(val):g}"
    if market == "ML":
        for m in sim.get("markets") or []:
            if m.get("market") == "ML" and m.get("selection") == sel:
                prob = m.get("prob")
                if prob is not None:
                    return f"{float(prob) * 100:.1f}%"
    return "—"


def _model_for_quote(
    quote: dict[str, Any],
    sim: dict[str, Any] | None,
) -> tuple[str, float | None]:
    if not sim:
        return "—", None
    market = str(quote.get("market") or "")
    sel = str(quote.get("selection") or "")
    line = quote.get("line")

    for m in sim.get("markets") or []:
        if m.get("market") != market:
            continue
        if market == "Total" and str(m.get("selection") or "").lower() != sel.lower():
            continue
        if market in ("Spread", "ML") and m.get("selection") != sel:
            continue
        if line is not None and m.get("line") is not None:
            try:
                if abs(float(m["line"]) - float(line)) > 0.6:
                    continue
            except (TypeError, ValueError):
                pass
        prob = m.get("prob")
        try:
            prob_f = float(prob) if prob is not None else None
        except (TypeError, ValueError):
            prob_f = None
        price = m.get("price") or (implied_to_american(prob_f) if prob_f else "—")
        return str(price or "—"), prob_f
    return "—", None


def _result_badge(result: object) -> str:
    r = str(result or "").lower()
    if r == "hit":
        return '<span class="bo-pe-result hit">WIN</span>'
    if r == "miss":
        return '<span class="bo-pe-result miss">LOSS</span>'
    if r == "push":
        return '<span class="bo-pe-result push">PUSH</span>'
    return "—"


def _fmt_roi_pct(val: object) -> str:
    try:
        pct = float(val)
        sign = "+" if pct > 0 else ""
        return f"{sign}{pct:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _edge_class(model_prob: float | None, book_price: Any) -> str:
    if model_prob is None or book_price is None:
        return "bo-pe-neutral"
    ev = ev_pct(model_prob, book_price)
    if ev is None:
        return "bo-pe-neutral"
    if ev > 0.5:
        return "bo-pe-edge-pos"
    if ev < -0.5:
        return "bo-pe-edge-neg"
    return "bo-pe-neutral"


def _book_price_raw(quote: dict[str, Any] | None) -> Any:
    if not quote:
        return None
    price = quote.get("price")
    if isinstance(price, str):
        return price.replace("+", "")
    return price


def render_matchup_header(
    game: dict[str, Any],
    sim: dict[str, Any] | None,
    *,
    live: dict[str, Any] | None = None,
) -> str:
    enriched = enrich_row_logos(dict(game))
    home = str(enriched.get("home") or (sim or {}).get("home") or "")
    away = str(enriched.get("away") or (sim or {}).get("away") or "")
    home_logo = logo_img_html(str(enriched.get("homeLogo") or enriched.get("home_logo") or ""), cls="bo-pe-logo")
    away_logo = logo_img_html(str(enriched.get("awayLogo") or enriched.get("away_logo") or ""), cls="bo-pe-logo")

    from lib.matchup_builder import _team_color

    home_color = _team_color(home)
    away_color = _team_color(away)

    sb = (sim or {}).get("scoreboard") or {}
    kickoff = esc(enriched.get("kickoff") or enriched.get("date_str") or "")

    status_txt = ""
    if live and live.get("status"):
        clock = live.get("clock") or ""
        status_txt = f'{esc(str(live.get("status")))} · {esc(str(clock))}'

    hp = game.get("homePoints") if game else None
    ap = game.get("awayPoints") if game else None
    if hp is None and game:
        hp = game.get("home_score")
    if ap is None and game:
        ap = game.get("away_score")
    final = hp is not None and ap is not None and bool(game and game.get("completed"))

    if final:
        score_html = (
            f'<div class="bo-pe-score final"><span>{esc(str(ap))}</span>'
            f'<span class="bo-pe-score-sep">–</span><span>{esc(str(hp))}</span></div>'
        )
        score_html += '<div class="bo-pe-score-lbl">Final</div>'
    elif live and live.get("home_score") is not None:
        score_html = (
            f'<div class="bo-pe-score live"><span>{esc(str(live.get("away_score", 0)))}</span>'
            f'<span class="bo-pe-score-sep">–</span><span>{esc(str(live.get("home_score", 0)))}</span></div>'
        )
    elif sb.get("away_mean") is not None and sb.get("home_mean") is not None:
        score_html = (
            f'<div class="bo-pe-score proj"><span>{esc(str(sb.get("away_mean")))}</span>'
            f'<span class="bo-pe-score-sep">–</span><span>{esc(str(sb.get("home_mean")))}</span></div>'
        )
        score_html += '<div class="bo-pe-score-lbl">Proj score</div>'
    else:
        score_html = ""

    return (
        f'<div class="bo-pe-header">'
        f'<div class="bo-pe-team away" style="--team-color:{esc(away_color)}">'
        f"{away_logo}"
        f'<div class="bo-pe-team-meta">'
        f'<div class="bo-pe-abbr">{esc(team_abbr(away))}</div>'
        f'<div class="bo-pe-name">{esc(away)}</div>'
        f"</div></div>"
        f'<div class="bo-pe-center">'
        f"{score_html}"
        f'<div class="bo-pe-kick">{kickoff}</div>'
        f'<div class="bo-pe-status">{status_txt}</div>'
        f"</div>"
        f'<div class="bo-pe-team home" style="--team-color:{esc(home_color)}">'
        f'<div class="bo-pe-team-meta right">'
        f'<div class="bo-pe-abbr">{esc(team_abbr(home))}</div>'
        f'<div class="bo-pe-name">{esc(home)}</div>'
        f"</div>"
        f"{home_logo}"
        f"</div></div>"
    )


def render_market_table(
    quotes: dict[str, dict[str, Any]],
    sim: dict[str, Any] | None,
    *,
    game: dict[str, Any] | None = None,
    live: dict[str, Any] | None = None,
    completed: bool = False,
) -> str:
    home = str((game or {}).get("home") or (sim or {}).get("home") or "")
    away = str((game or {}).get("away") or (sim or {}).get("away") or "")

    order = [
        ("spread_away", "Spread"),
        ("spread_home", "Spread"),
        ("total_over", "Total"),
        ("total_under", "Total"),
        ("ml_away", "ML"),
        ("ml_home", "ML"),
    ]

    rows: list[str] = []
    current_section = ""
    for key, default_market in order:
        q = quotes.get(key)
        if not q:
            continue
        market = str(q.get("market") or default_market)
        section = market
        sel = str(q.get("selection") or "")
        line = q.get("line")

        line_str = ""
        if line is not None:
            if market == "Spread":
                line_str = _fmt_spread_line(float(line))
            elif market == "Total":
                line_str = f"{float(line):g}"
            else:
                line_str = ""

        proj_str = _projection_for_quote(q, sim, home=home, away=away)
        model_price, model_prob = _model_for_quote(q, sim)
        edge_cls = _edge_class(model_prob, _book_price_raw(q))

        grade_cols = ""
        if completed and game:
            graded = grade_market_quote(
                key, q, game=game, sim=sim, home=home, away=away, quotes=quotes,
            )
            roi_txt = _fmt_roi_pct(graded.get("expected_roi_pct"))
            roi_cls = edge_cls if graded.get("expected_roi_pct") is not None else "bo-pe-neutral"
            grade_cols = (
                f'<td class="bo-pe-actual">{esc(graded.get("actual"))}</td>'
                f"<td>{_result_badge(graded.get('result'))}</td>"
                f'<td class="bo-pe-roi {roi_cls}">{esc(roi_txt)}</td>'
            )

        colspan = 8 if completed else 5
        if section != current_section:
            current_section = section
            rows.append(f'<tr><td colspan="{colspan}" class="bo-pe-section">{esc(section)}</td></tr>')

        rows.append(
            f"<tr>"
            f'<td class="bo-pe-sel">{esc(sel)}</td>'
            f'<td class="bo-pe-line">{esc(line_str)}</td>'
            f'<td class="bo-pe-proj">{esc(proj_str)}</td>'
            f'<td><span class="bo-pe-model-price {edge_cls}">{esc(model_price or "—")}</span></td>'
            f"<td>{_book_cell(q)}</td>"
            f"{grade_cols}"
            f"</tr>"
        )

    header = render_matchup_header(game or {"home": home, "away": away}, sim, live=live)
    if not rows:
        body = '<div class="bo-pe-empty-block">No sportsbook lines available for this game right now.</div>'
        return f"{header}{body}"

    hist_head = "<th>Actual</th><th>Result</th><th>Exp ROI</th>" if completed else ""
    return (
        f"{header}"
        f'<div class="bo-props-table-wrap bo-pe-board" data-pe-home="{esc(home)}" data-pe-away="{esc(away)}">'
        f'<table class="bo-props-table bo-pe-table">'
        f"<thead><tr>"
        f"<th>Selection</th><th>Line</th><th>Proj</th><th>Model</th><th>Book</th>{hist_head}"
        f"</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f"</table></div>"
    )
