"""Player props table — live book odds, stable cached projections."""
from __future__ import annotations

import html
import math
from typing import Any

from lib.book_logos import book_logo_img
from lib.display import team_abbr
from lib.prop_median_projection import _position_prior, median_matchup_projection
from lib.prop_pricing import PROP_KEYS, prop_key_from_row
from lib.prop_results import model_pick_side
from lib.prop_reprice import (
    baseline_projection,
    reprice_prop_row,
    resolve_prop_team,
    _stored_projection as reprice_stored_projection,
)

from pricing_engine.grading import grade_prop_row


def esc(val: object) -> str:
    return html.escape(str(val if val is not None else ""))


def row_prop_key(raw: dict[str, Any]) -> str:
    return str(raw.get("prop_key") or prop_key_from_row(raw) or "")


def prop_projection_key(row: dict[str, Any]) -> tuple[str, str, str]:
    try:
        line = f"{float(row.get('line')):g}"
    except (TypeError, ValueError):
        line = ""
    return (
        str(row.get("player") or ""),
        str(row.get("prop_key") or prop_key_from_row(row) or ""),
        line,
    )


def _enrich_prop_meta(row: dict[str, Any], *, year: int | None, week: int | None) -> dict[str, Any]:
    prop_key = str(row.get("prop_key") or prop_key_from_row(row) or "")
    meta = dict(row)
    meta["propKey"] = prop_key
    meta["prop_key"] = prop_key
    if year is not None:
        meta["year"] = year
    if week is not None:
        meta["week"] = week
    team = resolve_prop_team(meta, skip_starters=True)
    if team:
        meta["team"] = team
        if not meta.get("position"):
            try:
                from lib.prop_reprice import _baseline_on_team

                bl = _baseline_on_team(str(row.get("player") or ""), team)
                if bl and bl.get("position"):
                    meta["position"] = bl.get("position")
            except Exception:
                pass
    return meta


def compute_prop_projection(row: dict[str, Any], *, year: int | None, week: int | None) -> float:
    """Point-in-time prop projection — EWMA + matchup, never the posted line."""
    meta = _enrich_prop_meta(row, year=year, week=week)
    prop_key = str(meta.get("prop_key") or "")
    player = str(meta.get("player") or "")
    team = meta.get("team")

    if prop_key in PROP_KEYS and player:
        proj = median_matchup_projection(
            meta,
            skip_gamelog=False,
            skip_starters=True,
            line_as_prior=False,
        )
        if proj is not None:
            return round(float(proj), 1)

    stored = reprice_stored_projection(meta)
    if stored is not None:
        return stored

    if prop_key in PROP_KEYS and player:
        proj = baseline_projection(
            player,
            team,
            prop_key,
            home=meta.get("home"),
            away=meta.get("away"),
            skip_gamelog=False,
        )
        if proj is not None:
            return round(float(proj), 1)

        repriced = reprice_prop_row(meta, skip_gamelog=False, skip_starters=True)
        for key in ("modelProj", "projection"):
            try:
                val = float(repriced.get(key))
                if math.isfinite(val) and val >= 0:
                    return round(val, 1)
            except (TypeError, ValueError):
                continue

    position = str(meta.get("position") or "")
    prior = _position_prior(prop_key, position)
    if prior is not None:
        return round(float(prior), 1)

    return 0.0


def build_prop_projection_map(
    props: list[dict[str, Any]],
    *,
    year: int | None,
    week: int | None,
) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    for raw in props:
        out[prop_projection_key(raw)] = compute_prop_projection(raw, year=year, week=week)
    return out


def _book_cell(quote: dict[str, Any] | None) -> str:
    if not quote or quote.get("price") is None:
        return '<span class="bo-pe-empty">—</span>'
    bid = str(quote.get("book_id") or "")
    try:
        pi = int(float(quote.get("price")))
        price = f"+{pi}" if pi > 0 else str(pi)
    except (TypeError, ValueError):
        price = str(quote.get("price") or "—")
    logo = book_logo_img(bid, size=18, cls="bo-pe-book-logo")
    return f'<div class="bo-pe-odds-cell">{logo}<span class="bo-pe-odds">{esc(price)}</span></div>'


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
        if not math.isfinite(pct):
            return "—"
        sign = "+" if pct > 0 else ""
        return f"{sign}{pct:.1f}%"
    except (TypeError, ValueError):
        return "—"


def render_player_props_table(
    props: list[dict[str, Any]],
    *,
    projections: dict[tuple[str, str, str], float] | None = None,
    year: int | None = None,
    week: int | None = None,
    stat_filter: str | None = None,
    game: dict[str, Any] | None = None,
    completed: bool = False,
) -> str:
    rows: list[str] = []
    for raw in props:
        if stat_filter and row_prop_key(raw) != stat_filter:
            continue
        if not raw.get("over") and not raw.get("under"):
            continue

        pkey = prop_projection_key(raw)
        proj = (projections or {}).get(pkey)
        if proj is None:
            proj = compute_prop_projection(raw, year=year, week=week)

        player = esc(raw.get("player"))
        away = esc(team_abbr(str(raw.get("away") or "")))
        home = esc(team_abbr(str(raw.get("home") or "")))
        matchup = f"{away} @ {home}"
        stat = esc(str(raw.get("stat") or "").replace("TOTAL ", ""))
        try:
            line = f"{float(raw.get('line')):g}"
        except (TypeError, ValueError):
            line = "—"
        proj_txt = f"{float(proj):g}"
        model_side = model_pick_side(float(proj), raw.get("line"))
        pick_quote = raw.get("over") if model_side == "over" else raw.get("under") if model_side == "under" else None

        grade_cols = ""
        pick_col = f'<td class="bo-pe-prop-pick">{_book_cell(pick_quote)}</td>' if completed else (
            f'<td class="bo-pe-prop-pick">{esc(str(model_side or "—").upper())}</td>'
        )

        if completed and game:
            graded = grade_prop_row(raw, float(proj), game=game)
            roi_txt = _fmt_roi_pct(graded.get("expected_roi_pct"))
            roi_cls = "bo-pe-edge-pos" if graded.get("expected_roi_pct") and float(graded["expected_roi_pct"]) > 0 else ""
            if graded.get("expected_roi_pct") and float(graded["expected_roi_pct"]) < 0:
                roi_cls = "bo-pe-edge-neg"
            grade_cols = (
                f'<td class="bo-pe-actual">{esc(graded.get("actual"))}</td>'
                f"<td>{_result_badge(graded.get('result'))}</td>"
                f'<td class="bo-pe-roi {roi_cls}">{esc(roi_txt)}</td>'
            )

        rows.append(
            f"<tr>"
            f'<td class="bo-pe-prop-player"><div class="bo-pe-prop-name">{player}</div>'
            f'<div class="bo-pe-prop-match">{matchup} · {stat}</div></td>'
            f'<td class="bo-pe-prop-line">{esc(line)}</td>'
            f'<td class="bo-pe-prop-proj">{esc(proj_txt)}</td>'
            f"{pick_col}"
            f'<td>{_book_cell(raw.get("over"))}</td>'
            f'<td>{_book_cell(raw.get("under"))}</td>'
            f"{grade_cols}"
            f"</tr>"
        )

    if not rows:
        return '<div class="bo-pe-empty-block">No player props with sportsbook lines for this game.</div>'

    hist_head = "<th>Actual</th><th>Result</th><th>Exp ROI</th>" if completed else ""
    pick_head = "Pick" if completed else "Model Pick"

    return (
        f'<div class="bo-props-table-wrap bo-pe-props">'
        f'<table class="bo-props-table bo-pe-table">'
        f"<thead><tr>"
        f"<th>Player</th><th>Line</th><th>Proj</th><th>{pick_head}</th>"
        f"<th>Over</th><th>Under</th>{hist_head}"
        f"</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f"</table></div>"
    )
