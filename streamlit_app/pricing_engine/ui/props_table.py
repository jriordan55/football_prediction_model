"""Player props table — live book odds, stable cached projections."""
from __future__ import annotations

import html
import math
from typing import Any

import pandas as pd

from lib.book_logos import book_logo_img
from lib.display import _player_avatar_html, _player_matchup_time_html
from lib.odds_math import american_to_implied
from lib.prop_median_projection import RECENT_LIMIT, _ewma, _position_prior, median_matchup_projection
from lib.prop_pricing import PROP_KEYS, prop_key_from_row
from lib.prop_results import model_pick_side
from lib.prop_reprice import (
    baseline_projection,
    find_baseline_by_name,
    reprice_prop_row,
    resolve_prop_team,
    team_logo_for,
    _baseline_on_team,
    _stored_projection as reprice_stored_projection,
)
from lib.team_logos import enrich_row_logos
from lib.team_registry import teams_match
from pricing_engine.constants import PRICING_UI_BOOKS
from pricing_engine.grading import grade_prop_row, prop_expected_roi_pct
from pricing_engine.situation_model import correlated_pregame_prop_projection

_PROP_META_CACHE: dict[tuple[str, str, str, str, str, str, int | None, int | None], dict[str, Any]] = {}


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


def _enrich_prop_meta(
    row: dict[str, Any],
    *,
    year: int | None,
    week: int | None,
    game: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prop_key = str(row.get("prop_key") or prop_key_from_row(row) or "")
    player = str(row.get("player") or "")
    line_s = str(row.get("line") or "")
    home_pre = str((game or {}).get("home") or row.get("home") or "")
    away_pre = str((game or {}).get("away") or row.get("away") or "")
    team_pre = str(row.get("team") or "")
    cache_key = (player, prop_key, line_s, home_pre, away_pre, team_pre, year, week)
    cached = _PROP_META_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    meta = dict(row)
    meta["propKey"] = prop_key
    meta["prop_key"] = prop_key
    if year is not None:
        meta["year"] = year
    if week is not None:
        meta["week"] = week

    if game:
        meta["home"] = game.get("home") or meta.get("home")
        meta["away"] = game.get("away") or meta.get("away")
        meta["startDate"] = (
            game.get("kickoff") or game.get("startDate") or game.get("date_str") or meta.get("startDate")
        )
        logos = enrich_row_logos(
            {
                "home": meta.get("home"),
                "away": meta.get("away"),
                "home_logo": game.get("home_logo"),
                "away_logo": game.get("away_logo"),
            }
        )
        meta["homeLogo"] = logos.get("home_logo") or game.get("home_logo")
        meta["awayLogo"] = logos.get("away_logo") or game.get("away_logo")

    team_s = str(meta.get("team") or resolve_prop_team(meta, skip_starters=True) or "")
    if team_s:
        meta["team"] = team_s
        if not meta.get("position"):
            try:
                bl = _baseline_on_team(str(row.get("player") or ""), team_s)
                if bl and bl.get("position"):
                    meta["position"] = bl.get("position")
            except Exception:
                pass

    home = str(meta.get("home") or "")
    away = str(meta.get("away") or "")
    if team_s and home and away:
        if teams_match(team_s, home):
            meta["opponent"] = away
        elif teams_match(team_s, away):
            meta["opponent"] = home
        logo = team_logo_for(meta, team_s)
        if logo:
            meta["teamLogo"] = logo
        opp = str(meta.get("opponent") or "")
        if opp:
            opp_logo = team_logo_for(meta, opp)
            if opp_logo:
                meta["opponentLogo"] = opp_logo
    else:
        meta.pop("teamLogo", None)

    _PROP_META_CACHE[cache_key] = dict(meta)
    if len(_PROP_META_CACHE) > 1024:
        _PROP_META_CACHE.clear()
    return meta


def _is_anytime_td(prop_key: str) -> bool:
    return str(prop_key or "").lower() == "tds"


def _rate_to_td_prob(rate: float) -> float:
    if not math.isfinite(rate) or rate < 0:
        return 0.01
    if rate <= 1.0:
        prob = float(rate)
    else:
        prob = 1.0 - math.exp(-float(rate))
    return min(0.99, max(0.01, prob))


def _anytime_td_median_prob(
    player: str,
    team: str | None,
    *,
    season: int | None,
    as_of_week: int | None,
) -> float | None:
    """Median-leaning recent TD hit rate from last 10 games."""
    from lib.player_gamelog import anytime_td_hit_series, athlete_id_for_player, fetch_gamelog_df_combined

    aid = athlete_id_for_player(player, team or None)
    if not aid:
        return None
    yr = int(season or 2026)
    df = fetch_gamelog_df_combined(aid, yr)
    if as_of_week is not None:
        from pricing_engine.pit import filter_gamelog_as_of

        df = filter_gamelog_as_of(df, yr, int(as_of_week))
    hits = anytime_td_hit_series(df)
    if hits is None or hits.empty:
        return None
    vals = [float(v) for v in hits.tail(RECENT_LIMIT) if math.isfinite(float(v))]
    if not vals:
        return None
    rate = _ewma(vals)
    if rate is None:
        return None
    return _rate_to_td_prob(float(rate))


def _median_prop_projection(
    row: dict[str, Any],
    *,
    year: int | None,
    week: int | None,
    backtest: bool = False,
) -> float:
    """Robust median matchup projection — line-anchored so role players stay near market."""
    meta = _enrich_prop_meta(row, year=year, week=week)
    prop_key = str(meta.get("prop_key") or "")
    player = str(meta.get("player") or "")
    team = meta.get("team")

    if prop_key in PROP_KEYS and player:
        proj = median_matchup_projection(
            meta,
            skip_gamelog=False,
            skip_starters=True,
            line_as_prior=not backtest,
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


def _compute_anytime_td_projection(
    row: dict[str, Any],
    *,
    year: int | None,
    week: int | None,
    sim: dict[str, Any] | None = None,
    home: str | None = None,
    away: str | None = None,
) -> float:
    meta = _enrich_prop_meta(row, year=year, week=week)
    player = str(meta.get("player") or "")
    team = meta.get("team")
    home_s = str(home or meta.get("home") or "")
    away_s = str(away or meta.get("away") or "")

    median_prob = _anytime_td_median_prob(player, team, season=year, as_of_week=week)
    if median_prob is None:
        bl = _baseline_on_team(player, str(team or "")) or find_baseline_by_name(player)
        if bl and bl.get("tds_pg") is not None:
            try:
                median_prob = _rate_to_td_prob(float(bl["tds_pg"]))
            except (TypeError, ValueError):
                median_prob = None
    if median_prob is None:
        median_prob = 0.01

    correlated = correlated_pregame_prop_projection(
        prop_key="tds",
        team=str(team or "") or None,
        home=home_s,
        away=away_s,
        ewma_proj=median_prob,
        sim=sim,
        td_prob=True,
    )
    return round(float(correlated if correlated is not None else median_prob), 3)


def compute_prop_projection(
    row: dict[str, Any],
    *,
    year: int | None,
    week: int | None,
    sim: dict[str, Any] | None = None,
    home: str | None = None,
    away: str | None = None,
    backtest: bool = False,
) -> float:
    """
    Median player projection blended with Monte Carlo parent-market derivative.

    Form = median_matchup (line-anchored). Sim share = player median / team baseline
    applied to sim team mean — keeps stars high, role players near their lines.
    """
    meta = _enrich_prop_meta(row, year=year, week=week)
    prop_key = str(meta.get("prop_key") or "")
    home_s = str(home or meta.get("home") or "")
    away_s = str(away or meta.get("away") or "")

    if _is_anytime_td(prop_key):
        return _compute_anytime_td_projection(
            row, year=year, week=week, sim=sim, home=home_s, away=away_s,
        )

    median = _median_prop_projection(row, year=year, week=week, backtest=backtest)
    team = meta.get("team")
    correlated = correlated_pregame_prop_projection(
        prop_key=prop_key,
        team=str(team or "") or None,
        home=home_s,
        away=away_s,
        ewma_proj=median,
        sim=sim,
        td_prob=False,
    )
    if correlated is not None:
        return correlated
    return median


def build_prop_projection_map(
    props: list[dict[str, Any]],
    *,
    year: int | None,
    week: int | None,
    sim: dict[str, Any] | None = None,
    home: str | None = None,
    away: str | None = None,
    backtest: bool = False,
) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    for raw in props:
        out[prop_projection_key(raw)] = compute_prop_projection(
            raw,
            year=year,
            week=week,
            sim=sim,
            home=home or (raw.get("home")),
            away=away or (raw.get("away")),
            backtest=backtest,
        )
    return out


def _allowed_quote(quote: dict[str, Any] | None) -> dict[str, Any] | None:
    if not quote:
        return None
    bid = str(quote.get("book_id") or "").lower()
    if bid and bid not in PRICING_UI_BOOKS:
        return None
    return quote


def _fmt_liquidity(val: object) -> str:
    try:
        liq = float(val)
        if not math.isfinite(liq) or liq <= 0:
            return ""
        if liq >= 1000:
            txt = f"${liq / 1000:.1f}k".replace(".0k", "k")
            return txt
        return f"${liq:.0f}"
    except (TypeError, ValueError):
        return ""


def _fmt_prob_pct(prob: float | None) -> str:
    if prob is None:
        return "—"
    pct = min(99.0, max(1.0, round(float(prob) * 100.0, 1)))
    txt = f"{pct:.1f}".rstrip("0").rstrip(".")
    return f"{txt}%"


def _price_to_prob_pct(price: object) -> str:
    imp = american_to_implied(price)
    if imp is None:
        return "—"
    return _fmt_prob_pct(imp)


def _book_cell(quote: dict[str, Any] | None, *, prob_mode: bool = False) -> str:
    quote = _allowed_quote(quote)
    if not quote or quote.get("price") is None:
        return '<span class="bo-pe-empty">—</span>'
    bid = str(quote.get("book_id") or "")
    if prob_mode:
        price = _price_to_prob_pct(quote.get("price"))
    else:
        try:
            pi = int(float(quote.get("price")))
            price = f"+{pi}" if pi > 0 else str(pi)
        except (TypeError, ValueError):
            price = str(quote.get("price") or "—")
    logo = book_logo_img(bid, size=18, cls="bo-pe-book-logo")
    liq = _fmt_liquidity(quote.get("liquidity"))
    liq_html = f'<span class="bo-pe-liq">{esc(liq)}</span>' if liq else ""
    return (
        f'<div class="bo-pe-odds-cell">{logo}'
        f'<span class="bo-pe-odds">{esc(price)}</span>{liq_html}</div>'
    )


def _is_neutral_pick(projection: float, line: float | None) -> bool:
    if line is None:
        return False
    try:
        return abs(float(projection) - float(line)) < 0.001
    except (TypeError, ValueError):
        return False


def _effective_line(row: dict[str, Any], *, prob_mode: bool) -> float | None:
    if prob_mode:
        return 0.5
    try:
        return float(row.get("line"))
    except (TypeError, ValueError):
        return None


def _roi_cell(roi: float | None) -> str:
    if roi is None:
        return '<span class="bo-pe-empty">—</span>'
    if abs(roi) < 0.05:
        return f'<span class="bo-pe-edge bo-pe-edge-neutral">{esc("0.0%")}</span>'
    sign = "+" if roi > 0 else ""
    cls = "bo-pe-edge-pos" if roi > 0 else "bo-pe-edge-neg"
    return f'<span class="bo-pe-edge {cls}">{esc(f"{sign}{roi:.1f}%")}</span>'


def _pick_label(model_side: str | None, *, neutral: bool) -> str:
    if neutral:
        return "NEUTRAL"
    return str(model_side or "—").upper()


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


def _player_cell(meta: dict[str, Any]) -> str:
    row = pd.Series(meta)
    player = esc(meta.get("player"))
    pos_raw = str(meta.get("position") or "").strip()
    pos = esc(pos_raw.upper()) if pos_raw and pos_raw != "—" else ""
    pos_html = f'<span class="bo-pp-pos bo-pe-pos">{pos}</span>' if pos else ""
    return (
        f'<td class="bo-pp-player bo-pe-prop-player">'
        f'<div class="bo-pp-player-inner bo-pe-player-centered">'
        f"{_player_avatar_html(row)}"
        f'<div class="bo-pp-player-text">'
        f'<div class="bo-pp-name-row"><span class="bo-pp-name">{player}</span>{pos_html}</div>'
        f"{_player_matchup_time_html(row)}"
        f"</div></div></td>"
    )


def render_player_props_table(
    props: list[dict[str, Any]],
    *,
    projections: dict[tuple[str, str, str], float] | None = None,
    year: int | None = None,
    week: int | None = None,
    stat_filter: str | None = None,
    game: dict[str, Any] | None = None,
    sim: dict[str, Any] | None = None,
    home: str | None = None,
    away: str | None = None,
    completed: bool = False,
    sport: str | None = None,
) -> str:
    prob_mode = _is_anytime_td(str(stat_filter or ""))
    row_items: list[tuple[float, str]] = []
    box_stats = None
    if completed and game:
        from pricing_engine.grading import _box_stats_for_event, _resolve_event_id

        eid = _resolve_event_id(
            game,
            home=str(home or game.get("home") or ""),
            away=str(away or game.get("away") or ""),
            sport=sport,
            year=year,
            week=week,
        )
        if eid:
            box_stats = _box_stats_for_event(eid, str(sport or ""))

    for raw in props:
        if stat_filter and row_prop_key(raw) != stat_filter:
            continue
        over_q = _allowed_quote(raw.get("over") if isinstance(raw.get("over"), dict) else None)
        under_q = _allowed_quote(raw.get("under") if isinstance(raw.get("under"), dict) else None)
        if not over_q and not under_q:
            continue

        pkey = prop_projection_key(raw)
        proj = (projections or {}).get(pkey)
        if proj is None:
            for field in ("projection", "modelProj"):
                try:
                    val = float(raw.get(field))
                    if math.isfinite(val):
                        proj = val
                        break
                except (TypeError, ValueError):
                    continue
        if proj is None and not completed:
            proj = compute_prop_projection(
                raw,
                year=year,
                week=week,
                sim=sim,
                home=home or (game or {}).get("home"),
                away=away or (game or {}).get("away"),
            )
        elif proj is None and completed:
            proj = compute_prop_projection(
                raw,
                year=year,
                week=week,
                sim=sim,
                home=home or (game or {}).get("home"),
                away=away or (game or {}).get("away"),
                backtest=True,
            )

        meta = _enrich_prop_meta(raw, year=year, week=week, game=game)
        meta["over"] = over_q
        meta["under"] = under_q

        line_f = _effective_line(raw, prob_mode=prob_mode)
        if prob_mode:
            line = "0.5"
            proj_txt = _fmt_prob_pct(float(proj))
        else:
            try:
                line = f"{float(raw.get('line')):g}"
            except (TypeError, ValueError):
                line = "—"
            proj_txt = f"{float(proj):g}"
        neutral = _is_neutral_pick(float(proj), line_f)
        model_side = model_pick_side(float(proj), line_f)
        exp_roi = prop_expected_roi_pct(meta, float(proj), model_side=model_side)

        pick_col = (
            f'<td class="bo-pe-prop-pick">{esc(_pick_label(model_side, neutral=neutral))}</td>'
            if not completed
            else f'<td class="bo-pe-prop-pick">{_book_cell(over_q if model_side == "over" else under_q)}</td>'
        )

        grade_cols = ""
        if completed and game:
            graded = grade_prop_row(
                meta,
                float(proj),
                game=game,
                box_stats=box_stats,
                sport=sport,
                year=year,
                week=week,
            )
            grade_cols = (
                f'<td class="bo-pe-actual">{esc(graded.get("actual"))}</td>'
                f"<td>{_result_badge(graded.get('result'))}</td>"
            )

        row_items.append(
            (
                exp_roi if exp_roi is not None else float("-inf"),
                f"<tr>"
                f"{_player_cell(meta)}"
                f'<td class="bo-pe-prop-line">{esc(line)}</td>'
                f'<td class="bo-pe-prop-proj">{esc(proj_txt)}</td>'
                f"{pick_col}"
                f'<td>{_book_cell(over_q)}</td>'
                f'<td>{_book_cell(under_q)}</td>'
                f"{grade_cols}"
                f'<td class="bo-pe-roi-cell">{_roi_cell(exp_roi)}</td>'
                f"</tr>",
            )
        )

    row_items.sort(key=lambda item: item[0], reverse=True)
    rows = [html for _, html in row_items]

    if not rows:
        return '<div class="bo-pe-empty-block">No player props with sportsbook lines for this game.</div>'

    hist_head = "<th>Actual</th><th>Result</th>" if completed else ""
    pick_head = "Pick" if completed else "Model Pick"
    if prob_mode:
        line_head, proj_head, over_head, under_head = "Line", "Model %", "Yes", "No"
    else:
        line_head, proj_head, over_head, under_head = "Line", "Proj", "Over", "Under"

    return (
        f'<div class="bo-props-table-wrap bo-pp-board bo-pp-board-compact bo-pe-props">'
        f'<table class="bo-props-table bo-pp-table bo-pe-table">'
        f"<colgroup>"
        f'<col class="player-col" /><col class="line-col" /><col class="proj-col" />'
        f'<col class="pick-col" /><col class="over-col" /><col class="under-col" />'
        f'<col class="edge-col" />'
        f"</colgroup>"
        f"<thead><tr>"
        f"<th>Player</th><th>{line_head}</th><th>{proj_head}</th><th>{pick_head}</th>"
        f"<th>{over_head}</th><th>{under_head}</th>{hist_head}<th>Exp ROI</th>"
        f"</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f"</table></div>"
    )
