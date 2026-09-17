"""Customer-facing display helpers — mask sources, format feed rows."""
from __future__ import annotations

import html
import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from lib.config import book_label, normalize_book_id
from lib.odds_math import implied_to_american
from lib.nfl_projections import price_game
from lib.team_registry import teams_match

_HIDDEN_BOOKS = frozenset({"onyx", "onyx_odds", "onyx odds", ""})


def _query_href(**params: str) -> str:
    clean = {k: str(v) for k, v in params.items() if v is not None and str(v) != ""}
    return "?" + urlencode(clean) if clean else "?"


def customer_book(raw: Any) -> str:
    s = str(raw or "").strip()
    key = normalize_book_id(s)
    if not s:
        return "Best line"
    if key in _HIDDEN_BOOKS or "onyx" in s.lower():
        return "Onyx"
    return book_label(key) if key else "Best line"


def format_lean(side: Any) -> tuple[str, str]:
    s = str(side or "").upper()
    if "UNDER" in s:
        return "Under", "fl-tag-under"
    if "OVER" in s:
        return "Over", "fl-tag-over"
    return s.title() if s else "Play", ""


def fair_price(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    if row.get("fairPrice") and str(row.get("fairPrice")) != "—":
        from lib.prop_pricing import clamp_american

        return clamp_american(str(row.get("fairPrice")))
    wp = row.get("winProb")
    if wp is not None:
        try:
            p = float(wp)
            if 0.08 < p < 0.92:
                from lib.prop_pricing import clamp_american

                odds = implied_to_american(p)
                if odds:
                    return clamp_american(odds)
        except (TypeError, ValueError):
            pass
    return "—"


def price_labels(row: pd.Series | dict) -> tuple[str, str, str, str]:
    """Return (left_label, left_val, right_label, right_val) for feed price row."""
    if isinstance(row, dict):
        row = pd.Series(row)
    price = str(row.get("price") or "—")
    fair = fair_price(row)
    if fair != "—":
        return "Price", price, "Fair", fair
    line = row.get("line")
    proj = row.get("modelProj")
    if line is not None and proj is not None and str(proj).strip():
        return "Line", str(line), "Mine", str(proj)
    return "Price", price, "Fair", fair


def prop_market_label(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    market = str(row.get("market") or "Player prop").strip()
    matchup = str(row.get("matchup") or "").strip()
    if matchup:
        return f"{market} · {matchup}"
    return market


def render_prop_feed(rows: pd.DataFrame, *, limit: int = 40) -> None:
    """Bettor Odds-style +EV prop feed — no Streamlit dataframe."""
    import streamlit as st

    if rows.empty:
        return

    items: list[str] = []
    for _, row in rows.head(limit).iterrows():
        player = html.escape(str(row.get("player") or "Player"))
        lean, tag = format_lean(row.get("side"))
        ll, lv, rl, rv = price_labels(row)
        book = html.escape(customer_book(row.get("book")))
        ev = row.get("ev_pct")
        try:
            ev_str = f"+{float(ev):.1f}%"
        except (TypeError, ValueError):
            ev_str = "—"
        line = row.get("line")
        line_str = f" {line}" if line is not None and str(line).strip() else ""

        items.append(
            f"""
<div class="bo-feed-item">
  <div class="bo-feed-body">
    <div class="bo-feed-top">
      <span class="bo-feed-player">{player}</span>
      <span class="bo-feed-lean {tag}">{lean}</span>
    </div>
    <div class="bo-feed-market">College Football · {html.escape(str(row.get('market') or 'Prop'))}{line_str}</div>
    <div class="bo-feed-game">{html.escape(str(row.get('matchup') or ''))}</div>
    <div class="bo-feed-prices">
      <span>{html.escape(ll)} <strong>{html.escape(lv)}</strong></span>
      <span>{html.escape(rl)} <strong>{html.escape(rv)}</strong></span>
    </div>
    <div class="bo-feed-book">{book}</div>
  </div>
  <div class="bo-feed-edge">
    <div class="bo-feed-ev">{html.escape(ev_str)}</div>
    <div class="bo-feed-ev-label">EV</div>
  </div>
</div>
"""
        )

    st.markdown(f'<div class="bo-feed">{"".join(items)}</div>', unsafe_allow_html=True)


def _prop_short_market(market: str, line: Any) -> str:
    m = str(market or "Prop")
    short = (
        m.replace("Passing Touchdowns", "Pass TD")
        .replace("Passing", "Pass")
        .replace("Rushing", "Rush")
        .replace("Receiving", "Rec")
        .replace("Yards", "Yds")
    )
    if line is not None and str(line).strip():
        return f"{short} {line}"
    return short


def _position_category(position: str, props_df: pd.DataFrame) -> str:
    """Map roster position (or prop mix) to QB / RB / receiver bucket."""
    pos = (position or "").upper().strip()
    if pos == "QB":
        return "qb"
    if pos in ("RB", "FB", "HB"):
        return "rb"
    if pos in ("WR", "TE", "SE", "FL", "SL"):
        return "receiver"
    keys = {str(r.get("propKey") or "").lower() for _, r in props_df.iterrows()}
    if "pass_yds" in keys or "pass_tds" in keys:
        return "qb"
    if "rec_yds" in keys or "receptions" in keys:
        if "rush_yds" in keys and "rec_yds" not in keys and "receptions" not in keys:
            return "rb"
        return "receiver"
    if "rush_yds" in keys:
        return "rb"
    return "unknown"


def _prop_allowed_for_chart(prop_key: str, category: str) -> bool:
    pk = (prop_key or "").lower()
    if not pk:
        return False
    if category == "qb":
        return pk in ("pass_yds", "pass_tds", "rush_yds", "tds")
    if category == "rb":
        return pk in ("rush_yds", "rec_yds", "receptions", "tds")
    if category == "receiver":
        return pk in ("rec_yds", "receptions", "rush_yds", "tds")
    if pk == "pass_yds" or pk == "pass_tds":
        return category == "qb"
    if pk in ("rec_yds", "receptions"):
        return category != "qb"
    return True


def _chart_prop_choices(sub: pd.DataFrame, position: str) -> list[dict[str, Any]]:
    """Props on this week's board that are valid for the player's role."""
    from lib.prop_board_enrich import prop_display_name
    from lib.prop_pricing import prop_key_from_row

    category = _position_category(position, sub)
    seen: set[str] = set()
    choices: list[dict[str, Any]] = []
    sort_col = "ev_pct" if "ev_pct" in sub.columns else None
    ordered = sub.sort_values(sort_col, ascending=False, na_position="last") if sort_col else sub
    for _, row in ordered.iterrows():
        pk = prop_key_from_row(row.to_dict()) or str(row.get("propKey") or "")
        pk = pk.lower()
        if not pk or pk in seen or not _prop_allowed_for_chart(pk, category):
            continue
        seen.add(pk)
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            line = None
        choices.append(
            {
                "label": str(row.get("prop_label") or prop_display_name(row.get("market"))),
                "prop_key": pk,
                "line": line,
            }
        )
    return choices


def _format_game_time(start: Any) -> str:
    if not start:
        return ""
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        h = dt.hour % 12 or 12
        return f"{h}:{dt.minute:02d} {'PM' if dt.hour >= 12 else 'AM'}"
    except (TypeError, ValueError):
        return ""


def _book_pill_html(book: Any, book_id: Any = None) -> str:
    from lib.book_logos import book_logo_img

    name = customer_book(book)
    bid = str(book_id or book or "onyx_odds").lower()
    if "onyx" in bid:
        bid = "onyx_odds"
    return book_logo_img(bid, size=18, cls="bo-pp-book-logo")


def _player_avatar_html(row: pd.Series | dict, *, size: str = "board") -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    team_logo = str(row.get("teamLogo") or row.get("team_logo") or "").strip()
    if team_logo.lower() in ("nan", "none", ""):
        team_logo = ""
    espn_id = row.get("espn_id")
    headshot = None
    if espn_id:
        try:
            from lib.player_gamelog import athlete_headshot

            headshot = athlete_headshot(str(espn_id))
        except Exception:
            headshot = None
    cls = "bo-pp-headshot bo-pp-headshot-lg" if size == "detail" else "bo-pp-headshot"
    img_src = headshot or team_logo
    if img_src:
        avatar = f'<img class="{cls}" src="{html.escape(img_src)}" alt="" loading="lazy" />'
    elif team_logo:
        avatar = f'<img class="{cls}" src="{html.escape(team_logo)}" alt="" loading="lazy" />'
    else:
        avatar = f'<div class="{cls} bo-logo-fallback"></div>'
    badge = ""
    if team_logo:
        badge = (
            f'<img class="bo-pp-team-badge" src="{html.escape(team_logo)}" alt="" loading="lazy" />'
        )
    return f'<div class="bo-pp-avatar-wrap">{avatar}{badge}</div>'


def _player_matchup_time_html(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)

    team = str(row.get("team") or "")
    opponent = str(row.get("opponent") or "")
    team_logo = str(row.get("teamLogo") or "").strip()
    opp_logo = str(row.get("opponentLogo") or "").strip()
    home = str(row.get("home") or "")
    away = str(row.get("away") or "")
    home_logo = str(row.get("homeLogo") or "").strip()
    away_logo = str(row.get("awayLogo") or "").strip()

    if team and home and away:
        if teams_match(team, home):
            if not team_logo or team_logo == "nan":
                team_logo = home_logo
            if not opp_logo or opp_logo == "nan":
                opp_logo = away_logo
            if not opponent:
                opponent = away
        elif teams_match(team, away):
            if not team_logo or team_logo == "nan":
                team_logo = away_logo
            if not opp_logo or opp_logo == "nan":
                opp_logo = home_logo
            if not opponent:
                opponent = home

    team_img = (
        f'<img class="bo-pp-match-logo" src="{html.escape(team_logo)}" alt="" loading="lazy" />'
        if team_logo and team_logo != "nan"
        else ""
    )
    opp_img = (
        f'<img class="bo-pp-match-logo" src="{html.escape(opp_logo)}" alt="" loading="lazy" />'
        if opp_logo and opp_logo != "nan"
        else ""
    )

    sp_bits: list[str] = []
    rank = row.get("opp_sp_def_rank")
    adj_pct = row.get("opp_def_adj_pct")
    if rank is not None:
        try:
            sp_bits.append(f"Def #{int(rank)}")
        except (TypeError, ValueError):
            pass
    adj_cls = "bo-pp-opp-sp"
    if adj_pct is not None:
        try:
            pct = float(adj_pct)
            if pct > 0.5:
                adj_cls = "bo-pp-opp-sp bo-pp-opp-soft"
            elif pct < -0.5:
                adj_cls = "bo-pp-opp-sp bo-pp-opp-tough"
            sp_bits.append(f"{pct:+.0f}%")
        except (TypeError, ValueError):
            pass
    sp_html = (
        f'<span class="{adj_cls}">{html.escape(" · ".join(sp_bits))}</span>'
        if sp_bits
        else ""
    )

    kick = _format_game_time(row.get("startDate"))
    time_html = f'<span class="bo-pp-kick">{html.escape(kick)}</span>' if kick else ""
    return (
        f'<div class="bo-pp-matchup-row">{team_img}'
        f'<span class="bo-pp-at">@</span>{opp_img}{sp_html}{time_html}</div>'
    )


def _prop_grade_html(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    from lib.player_gamelog import prop_hit_grade
    from lib.prop_pricing import prop_key_from_row

    pk = prop_key_from_row(row.to_dict()) or str(row.get("propKey") or "")
    try:
        line = float(row.get("line"))
    except (TypeError, ValueError):
        return '<div class="bo-pp-grade bo-pp-grade-na">—</div>'
    espn_id = str(row.get("espn_id") or "").strip() or None
    hits, total = prop_hit_grade(
        espn_id,
        pk,
        line,
        str(row.get("side") or ""),
        season=int(row.get("year")) if row.get("year") is not None else None,
    )
    if hits is None or not total:
        return '<div class="bo-pp-grade bo-pp-grade-na">—</div>'
    return f'<div class="bo-pp-grade">{hits}/{total}</div>'


def _ours_projection(row: pd.Series | dict) -> str:
    import math

    if isinstance(row, dict):
        row = pd.Series(row)
    for key in ("modelProj", "projection"):
        try:
            val = float(row.get(key))
            if math.isfinite(val) and val >= 0:
                prop_key = str(row.get("propKey") or "").lower()
                market = str(row.get("market") or "").lower()
                if prop_key == "pass_tds" or "passing td" in market:
                    txt = f"{val:.1f}".rstrip("0").rstrip(".")
                    return txt or "0"
                return f"{val:.1f}"
        except (TypeError, ValueError):
            pass
    from lib.matchup_prop_enrich import format_prop_projection

    return format_prop_projection(row.to_dict())


def _matchup_cell(row: pd.Series) -> str:
    away = str(row.get("away") or "")
    home = str(row.get("home") or "")
    away_logo = str(row.get("awayLogo") or "")
    home_logo = str(row.get("homeLogo") or "")
    away_abbr = html.escape(team_abbr(away))
    home_abbr = html.escape(team_abbr(home))
    away_img = (
        f'<img class="bo-pp-match-logo" src="{html.escape(away_logo)}" alt="" loading="lazy" />'
        if away_logo and away_logo != "nan"
        else ""
    )
    home_img = (
        f'<img class="bo-pp-match-logo" src="{html.escape(home_logo)}" alt="" loading="lazy" />'
        if home_logo and home_logo != "nan"
        else ""
    )
    return (
        f'<div class="bo-pp-matchup-row">{away_img}{home_img}'
        f'<span>{away_abbr} @ {home_abbr}</span></div>'
    )


def _ours_cell(row: pd.Series) -> tuple[str, str]:
    from lib.prop_pricing import format_ours_american

    side = str(row.get("side") or "").lower()
    tag = "U" if "under" in side else "O" if "over" in side else ""
    over_pct = row.get("overProb")
    lean_prob = None
    if over_pct is not None:
        try:
            p = float(over_pct)
            lean_prob = (1.0 - p) if tag == "U" else p if tag == "O" else None
        except (TypeError, ValueError):
            lean_prob = None
    if lean_prob is None:
        wp = row.get("winProb")
        try:
            lean_prob = float(wp) if wp is not None else None
        except (TypeError, ValueError):
            lean_prob = None
    fair = format_ours_american(lean_prob) if lean_prob is not None else fair_price(row)
    odds_txt = f"{fair} {tag}".strip() if fair != "—" else "—"
    proj = row.get("modelProj")
    try:
        proj_txt = f"{float(proj):.1f}" if proj is not None else "—"
    except (TypeError, ValueError):
        proj_txt = "—"
    return odds_txt, proj_txt


def _prop_side_metrics(row: pd.Series | dict) -> tuple[str, str, str, str]:
    """Return (p_label, p_val, be_val, roi_val) for board cells."""
    if isinstance(row, dict):
        row = pd.Series(row)
    side = str(row.get("side") or "").lower()
    p_label = "P(Under)" if "under" in side else "P(Over)"

    try:
        wp = float(row.get("winProb"))
        p_val = f"{wp * 100:.1f}%" if 0 < wp < 1 else "—"
    except (TypeError, ValueError):
        p_val = "—"

    try:
        be = float(row.get("break_even"))
        be_val = f"{be * 100:.1f}%"
    except (TypeError, ValueError):
        from lib.odds_math import american_to_implied

        imp = american_to_implied(row.get("price"))
        be_val = f"{imp * 100:.1f}%" if imp is not None else "—"

    try:
        roi = float(row.get("expected_roi"))
        roi_val = f"{roi * 100:+.1f}%"
    except (TypeError, ValueError):
        try:
            roi = float(row.get("roi"))
            roi_val = f"{roi * 100:+.1f}%"
        except (TypeError, ValueError):
            roi_val = "—"

    return p_label, p_val, be_val, roi_val


def _roi_cell_class(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    try:
        roi = float(row.get("expected_roi") if row.get("expected_roi") is not None else row.get("roi"))
        if roi > 0.005:
            return "bo-pp-roi-pos"
        if roi < -0.005:
            return "bo-pp-roi-neg"
    except (TypeError, ValueError):
        pass
    return ""


def _prop_row_key(row: pd.Series | dict) -> tuple[str, str, str]:
    from lib.prop_pricing import prop_key_from_row

    if isinstance(row, dict):
        row = pd.Series(row)
    player = str(row.get("player") or "")
    pk = str(row.get("propKey") or prop_key_from_row(row.to_dict()) or "")
    try:
        line = f"{float(row.get('line')):g}"
    except (TypeError, ValueError):
        line = ""
    return player, pk, line


def _prop_row_is_active(
    row: pd.Series | dict,
    *,
    selected_player: str | None = None,
    selected_row: dict | None = None,
) -> bool:
    player, pk, line = _prop_row_key(row)
    if selected_row:
        try:
            sel_line = f"{float(selected_row.get('line')):g}" if selected_row.get("line") is not None else ""
        except (TypeError, ValueError):
            sel_line = str(selected_row.get("line") or "")
        return (
            player == str(selected_row.get("player") or "")
            and pk.lower() == str(selected_row.get("propKey") or "").lower()
            and line == sel_line
        )
    return bool(selected_player and player == selected_player)


def _result_badge(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    result = str(row.get("prop_result") or row.get("result") or "").lower()
    grade = str(row.get("grade") or "").upper()
    if result == "hit" or grade == "WIN":
        return '<span class="bo-result hit">HIT</span>'
    if result == "miss" or grade == "LOSS":
        return '<span class="bo-result miss">MISS</span>'
    if result == "push" or grade == "PUSH":
        return '<span class="bo-result push">PUSH</span>'
    return "—"


def _actual_stat_txt(row: pd.Series | dict) -> str:
    if isinstance(row, dict):
        row = pd.Series(row)
    val = row.get("actual_stat")
    if val is None:
        val = row.get("actual")
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    try:
        market = str(row.get("market") or "").lower()
        prop_key = str(row.get("propKey") or "").lower()
        if prop_key == "pass_tds" or "passing td" in market:
            txt = f"{float(val):.1f}".rstrip("0").rstrip(".")
            return txt or "0"
        return f"{float(val):g}"
    except (TypeError, ValueError):
        return str(val)


def render_props_board(
    df: pd.DataFrame,
    *,
    selected_player: str | None = None,
    selected_row: dict | None = None,
    show_results: bool = False,
) -> None:
    """Compact props table — Player / Prop / Line / Mine / Side / Price / P / BE% / Expected ROI."""
    import streamlit as st

    from lib.prop_board_enrich import prop_display_name

    if df.empty:
        return

    rows_html: list[str] = []
    for _, row in df.iterrows():
        player = html.escape(str(row.get("player") or "Player"))
        player_raw = str(row.get("player") or "")
        pos_raw = str(row.get("position") or "").strip()
        pos = html.escape(pos_raw.upper()) if pos_raw and pos_raw != "—" else ""
        pos_html = f'<span class="bo-pp-pos">{pos}</span>' if pos else ""
        prop_txt = html.escape(str(row.get("prop_label") or prop_display_name(row.get("market"))))
        mine = html.escape(_ours_projection(row))
        lean, _lean_cls = format_lean(row.get("side"))
        side_cls = "bo-pp-side-over" if lean.lower() == "over" else "bo-pp-side-under"
        price = html.escape(str(row.get("price") or "—"))
        try:
            line_txt = f"{float(row.get('line')):g}"
        except (TypeError, ValueError):
            line_txt = "—"
        p_label, p_val, be_val, roi_val = _prop_side_metrics(row)
        roi_cls = _roi_cell_class(row)
        actual_txt = html.escape(_actual_stat_txt(row)) if show_results else ""
        result_html = _result_badge(row) if show_results else ""
        _, pk_raw, line_raw = _prop_row_key(row)
        active = (
            " bo-pp-row-active"
            if _prop_row_is_active(row, selected_player=selected_player, selected_row=selected_row)
            else ""
        )
        result_cells = (
            f'<td class="bo-pp-actual">{actual_txt}</td><td class="bo-pp-result">{result_html}</td>'
            if show_results
            else ""
        )
        rows_html.append(
            f'<tr class="bo-pp-row{active}">'
            f'<td class="bo-pp-player"><div class="bo-pp-player-inner">'
            f'{_player_avatar_html(row)}'
            f'<div class="bo-pp-player-text">'
            f'<div class="bo-pp-name-row"><span class="bo-pp-name">{player}</span>{pos_html}</div>'
            f'{_player_matchup_time_html(row)}'
            f"</div></div></td>"
            f'<td class="bo-pp-prop"><div class="bo-pp-prop-line">{prop_txt}</div></td>'
            f'<td class="bo-pp-line">{html.escape(line_txt)}</td>'
            f'<td class="bo-pp-mine"><div class="bo-pp-mine-val">{mine}</div></td>'
            f'<td class="bo-pp-side"><span class="{side_cls}">{html.escape(lean.upper())}</span></td>'
            f'<td class="bo-pp-price-cell"><span class="bo-pp-price">{price}</span></td>'
            f'<td class="bo-pp-pprob"><span class="bo-pp-p-label">{html.escape(p_label)}</span>'
            f'<span class="bo-pp-p-val">{html.escape(p_val)}</span></td>'
            f'<td class="bo-pp-be">{html.escape(be_val)}</td>'
            f'<td class="bo-pp-roi {roi_cls}">{html.escape(roi_val)}</td>'
            f"{result_cells}"
            f"</tr>"
        )

    result_cols = (
        '<col class="actual-col" /><col class="result-col" />'
        if show_results
        else ""
    )
    result_heads = (
        "<th>Actual</th><th>Result</th>"
        if show_results
        else ""
    )

    st.markdown(
        f"""
<div class="bo-props-table-wrap bo-pp-board bo-pp-board-compact">
<table class="bo-props-table bo-pp-table">
<colgroup>
<col class="player-col" /><col class="prop-col" /><col class="line-col" /><col class="mine-col" />
<col class="side-col" /><col class="price-col" /><col class="p-col" /><col class="be-col" />
<col class="roi-col" />{result_cols}
</colgroup>
<thead><tr>
<th>Player</th><th>Prop</th><th>Line</th><th>Mine</th><th>Side</th><th>Price</th><th>P</th><th>BE%</th><th>Expected ROI</th>{result_heads}
</tr></thead>
<tbody>{"".join(rows_html)}</tbody>
</table></div>
""",
        unsafe_allow_html=True,
    )


def render_player_performance_panel(
    row: pd.Series | dict,
    *,
    season: int | None = None,
    compact: bool = False,
) -> None:
    """Recent performance for the prop row selected on the board."""
    import streamlit as st

    from lib.prop_board_enrich import prop_display_name
    from lib.prop_pricing import prop_key_from_row

    if isinstance(row, dict):
        row = pd.Series(row)

    player = str(row.get("player") or "")
    prop_key = str(row.get("propKey") or prop_key_from_row(row.to_dict()) or "rush_yds").lower()
    prop_label = str(row.get("prop_label") or prop_display_name(row.get("market")))
    espn_id = row.get("espn_id")
    if not espn_id:
        from lib.player_gamelog import athlete_id_for_player

        espn_id = athlete_id_for_player(
            player,
            str(row.get("team") or row.get("teamLabel") or ""),
        )
    try:
        line_f = float(row.get("line"))
    except (TypeError, ValueError):
        line_f = None
    side = str(row.get("side") or "over")

    if not compact:
        pos_raw = str(row.get("position") or "").strip()
        pos = html.escape(pos_raw.upper()) if pos_raw and pos_raw != "—" else ""
        pos_html = f'<span class="bo-pp-pos">{pos}</span>' if pos else ""
        team_short = html.escape(team_abbr(str(row.get("team") or "")))
        pname = html.escape(player)
        avatar = _player_avatar_html(row, size="detail")
        st.markdown(
            f"""
<div class="bo-pp-perf-panel">
  <div class="bo-pp-detail-head">{avatar}
    <div><div class="bo-pp-detail-name-row"><span class="bo-pp-detail-name">{pname}</span>{pos_html}</div>
    <div class="bo-pp-detail-meta">{team_short} · {html.escape(prop_label)}</div>
    {_player_matchup_time_html(row)}
    </div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
    try:
        season_i = int(season) if season is not None else int(row.get("year"))
    except (TypeError, ValueError):
        from lib.config import DEFAULT_YEAR

        season_i = DEFAULT_YEAR

    render_recent_performance(
        athlete_id=str(espn_id) if espn_id else None,
        prop_key=prop_key,
        line=line_f,
        side=side,
        season=season_i,
    )


def _bar_class(value: float, line: float | None, side: str = "over") -> str:
    if line is not None and line >= 0:
        under = "under" in str(side or "").lower()
        if under:
            return "bo-pp-bar-over" if value < line else "bo-pp-bar-under"
        return "bo-pp-bar-over" if value > line else "bo-pp-bar-under"
    return "bo-pp-bar-mid"


def render_recent_performance(
    *,
    athlete_id: str | None,
    prop_key: str,
    line: float | None = None,
    side: str = "over",
    season: int | None = None,
) -> None:
    """Bar chart — last 10 games for one prop vs its line."""
    import streamlit as st

    from lib.config import DEFAULT_YEAR
    from lib.player_gamelog import recent_stat_series

    prop_key = (prop_key or "rush_yds").lower()
    label_map = {
        "rush_yds": "Rush Yds",
        "rush_attempts": "Rush Att",
        "rec_yds": "Rec Yds",
        "pass_yds": "Pass Yds",
        "pass_yds_q1": "1Q Pass Yds",
        "pass_attempts": "Pass Att",
        "pass_completions": "Completions",
        "pass_tds": "Pass TD",
        "receptions": "Rec",
        "tds": "Anytime TD",
    }

    if not athlete_id:
        st.markdown(
            """
<div class="bo-pp-recent">
  <div class="bo-pp-recent-head">
    <div class="bo-pp-recent-title">RECENT PERFORMANCE · LAST 10 GAMES</div>
  </div>
  <div class="bo-pp-recent-empty">No game log available for this player.</div>
</div>""",
            unsafe_allow_html=True,
        )
        return

    season_i = int(season) if season is not None else DEFAULT_YEAR
    series = recent_stat_series(athlete_id, prop_key, season=season_i, limit=10)
    if not series:
        st.markdown(
            '<div class="bo-pp-recent-empty">No recent games for this stat.</div>',
            unsafe_allow_html=True,
        )
        return

    values = [g["value"] for g in series]
    max_v = max(max(values), line or 0, 1.0)
    bars: list[str] = []
    for g in series:
        h = max(12, int(110 * g["value"] / max_v))
        cls = _bar_class(g["value"], line, side)
        logo = str(g.get("opponent_logo") or "")
        abbr = html.escape(str(g.get("opponent_abbr") or "OPP")[:4])
        if logo:
            axis = f'<img class="bo-pp-bar-logo" src="{html.escape(logo)}" alt="{abbr}" title="{html.escape(str(g.get("opponent") or ""))}" loading="lazy" />'
        else:
            axis = f'<span class="bo-pp-bar-abbr">{abbr}</span>'
        bars.append(
            f'<div class="bo-pp-bar-wrap"><div class="bo-pp-bar-val">{g["value"]:.0f}</div>'
            f'<div class="bo-pp-bar {cls}" style="height:{h}px"></div>'
            f'<div class="bo-pp-bar-axis">{axis}</div></div>'
        )

    line_note = f"line {line:g}" if line is not None else ""
    stat_lbl = label_map.get(prop_key, "Stat")
    st.markdown(
        f"""
<div class="bo-pp-recent">
  <div class="bo-pp-recent-head">
    <div class="bo-pp-recent-title">RECENT PERFORMANCE · LAST 10 GAMES</div>
    <div class="bo-pp-recent-median">{html.escape(stat_lbl)}{(' · ' + html.escape(line_note)) if line_note else ''}</div>
  </div>
  <div class="bo-pp-bars">{"".join(bars)}</div>
</div>""",
        unsafe_allow_html=True,
    )


def render_prop_detail_panel(
    df: pd.DataFrame,
    player: str,
    *,
    chart_prop: str | None = None,
    chart_df: pd.DataFrame | None = None,
) -> None:
    """Right-rail player detail — props for one player."""
    import streamlit as st

    sub = df[df["player"] == player] if player else df.iloc[0:0]
    chart_src = chart_df[chart_df["player"] == player] if chart_df is not None else sub
    if sub.empty:
        st.markdown('<div class="bo-pp-detail-empty">Select a player from the board.</div>', unsafe_allow_html=True)
        return

    head = sub.iloc[0]
    pos_raw = str(head.get("position") or "").strip()
    pos = html.escape(pos_raw.upper()) if pos_raw and pos_raw != "—" else ""
    pos_html = f'<span class="bo-pp-pos">{pos}</span>' if pos else ""
    team_short = html.escape(team_abbr(str(head.get("team") or "")))
    pname = html.escape(str(player))
    espn_id = head.get("espn_id")
    if not espn_id:
        from lib.player_gamelog import athlete_id_for_player

        espn_id = athlete_id_for_player(
            str(player),
            str(head.get("team") or head.get("teamLabel") or ""),
        )
    avatar = _player_avatar_html(head, size="detail")

    mini_rows: list[str] = []
    for _, row in sub.iterrows():
        from lib.prop_board_enrich import prop_display_name

        prop_txt = html.escape(str(row.get("prop_label") or prop_display_name(row.get("market"))))
        mine = html.escape(_ours_projection(row))
        lean, _lean_cls = format_lean(row.get("side"))
        side_cls = "bo-pp-side-over" if lean.lower() == "over" else "bo-pp-side-under"
        price = html.escape(str(row.get("price") or "—"))
        try:
            line_txt = f"{float(row.get('line')):g}"
        except (TypeError, ValueError):
            line_txt = "—"
        p_label, p_val, be_val, roi_val = _prop_side_metrics(row)
        roi_cls = _roi_cell_class(row)
        try:
            ev_str = f"+{float(row.get('ev_pct')):.1f}%"
        except (TypeError, ValueError):
            ev_str = "—"
        mini_rows.append(
            f"<tr><td><div class=\"bo-pp-prop-line\">{prop_txt}</div></td>"
            f"<td class=\"bo-pp-line\">{html.escape(line_txt)}</td>"
            f"<td><div class=\"bo-pp-mine-val\">{mine}</div></td>"
            f"<td class=\"{side_cls}\">{html.escape(lean.upper())}</td>"
            f"<td><span class=\"bo-pp-price\">{price}</span></td>"
            f"<td><span class=\"bo-pp-p-label\">{html.escape(p_label)}</span> "
            f"<span class=\"bo-pp-p-val\">{html.escape(p_val)}</span></td>"
            f"<td>{html.escape(be_val)}</td>"
            f"<td class=\"{roi_cls}\">{html.escape(roi_val)}</td>"
            f"<td class=\"bo-pp-edge\">{html.escape(ev_str)}</td></tr>"
        )

    meta = team_short
    st.markdown(
        f"""
<div class="bo-pp-detail">
  <div class="bo-pp-detail-head">{avatar}
    <div><div class="bo-pp-detail-name-row"><span class="bo-pp-detail-name">{pname}</span>{pos_html}</div>
    <div class="bo-pp-detail-meta">{meta}</div>
    {_player_matchup_time_html(head)}
    </div>
  </div>
  <div class="bo-section-title">This week&apos;s props ({len(sub)})</div>
  <div class="bo-props-table-wrap">
    <table class="bo-props-table bo-pp-mini">
      <thead><tr><th>Prop</th><th>Line</th><th>Mine</th><th>Side</th><th>Price</th><th>P</th><th>BE%</th><th>ROI</th><th>Edge%</th></tr></thead>
      <tbody>{"".join(mini_rows)}</tbody>
    </table>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    chart_choices = _chart_prop_choices(chart_src, str(head.get("position") or ""))
    if not chart_choices:
        st.markdown(
            '<div class="bo-pp-recent-empty">No chartable props for this player&apos;s position.</div>',
            unsafe_allow_html=True,
        )
    else:
        default_pk = (chart_prop or str(head.get("propKey") or chart_choices[0]["prop_key"])).lower()
        labels = [c["label"] for c in chart_choices]
        default_idx = next(
            (i for i, c in enumerate(chart_choices) if c["prop_key"] == default_pk),
            0,
        )
        st.markdown('<div class="bo-pp-select-wrap">', unsafe_allow_html=True)
        picked_lbl = st.selectbox(
            "Prop chart",
            labels,
            index=default_idx,
            key=f"pp_chart_prop_{player}",
            label_visibility="collapsed",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        sel = chart_choices[labels.index(picked_lbl)]
        chart_side = "over"
        for _, row in chart_src.iterrows():
            from lib.prop_pricing import prop_key_from_row

            pk = prop_key_from_row(row.to_dict()) or str(row.get("propKey") or "")
            if pk.lower() == sel["prop_key"]:
                chart_side = str(row.get("side") or "over")
                break
        render_recent_performance(
            athlete_id=str(espn_id) if espn_id else None,
            prop_key=sel["prop_key"],
            line=sel["line"],
            side=chart_side,
        )
    st.markdown('<div class="bo-pp-detail-note">SP+ matchup · pace · game script · opponent pass defense</div>', unsafe_allow_html=True)


def team_abbr(name: str, lookup: dict[str, str] | None = None, *, year: int | None = None) -> str:
    n = str(name or "").strip()
    if not n:
        return "—"
    if lookup:
        if n in lookup:
            return lookup[n]
        low = n.lower()
        for k, v in lookup.items():
            if k.lower() == low:
                return v
    try:
        from lib.team_registry import official_team_abbr

        yr = year if year is not None else 2026
        official = official_team_abbr(n, year=int(yr))
        if official:
            return official
    except Exception:
        pass
    parts = [p for p in re.split(r"[\s.&'-]+", n) if p and p.lower() not in {"university", "the", "of"}]
    if len(parts) >= 2:
        if parts[0].lower() == "ohio" and parts[-1].lower() == "state":
            return "OSU"
        if parts[-1].lower() == "state" and len(parts[0]) >= 3:
            return (parts[0][:3] + "S").upper()
        abbr = "".join(p[0] for p in parts).upper()
        return abbr[:4] if len(abbr) >= 3 else (parts[0][:3] + parts[-1][0]).upper()
    return n[:4].upper()


def _logo_img(url: str, *, cls: str = "bo-proj-logo") -> str:
    if not url or str(url) == "nan":
        return f'<div class="{cls} bo-logo-fallback"></div>'
    return f'<img class="{cls}" src="{html.escape(str(url))}" alt="" loading="lazy" />'


def _format_kickoff(start: Any, week: int | None = None) -> tuple[str, str]:
    badge = f"W{week}" if week is not None else "—"
    sub = ""
    if start:
        try:
            dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            badge = f"{dt.month}/{dt.day}"
            wk = f"Week {week} · " if week is not None else ""
            sub = f"{wk}{dt.strftime('%b')} {dt.day}"
        except (TypeError, ValueError):
            sub = f"Week {week}" if week is not None else ""
    elif week is not None:
        sub = f"Week {week}"
    return badge, sub


def _injury_badge(status: str) -> tuple[str, str]:
    s = (status or "").upper()
    if any(x in s for x in ("OUT", "IR", "INJURED RESERVE", "SUSPEND")):
        return "OUT" if "OUT" in s else s[:18], "bo-inj-badge-out"
    if "DOUBTFUL" in s:
        return "DOUBTFUL", "bo-inj-badge-doubt"
    if "QUESTION" in s or "DAY-TO-DAY" in s or "DAY TO DAY" in s:
        return "QUESTIONABLE", "bo-inj-badge-question"
    if "EXPECTED" in s or "PROBABLE" in s or "ACTIVE" in s:
        return "EXPECTED TO PLAY", "bo-inj-badge-active"
    return s[:20] or "LISTED", "bo-inj-badge-listed"


def _hours_ago(ts: Any) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo or timezone.utc)
        secs = max(0, int((now - dt).total_seconds()))
        hrs = secs // 3600
        if hrs > 24 * 30:
            return dt.strftime("%b %d, %Y")
        if hrs >= 48:
            return dt.strftime("%b %d")
        if hrs >= 24:
            return f"{hrs // 24}d ago"
        return f"{hrs}h ago" if hrs else "now"
    except (TypeError, ValueError):
        return ""


def render_injury_feed(df: pd.DataFrame, *, limit: int = 80) -> None:
    """Reference-style injury room feed with avatars and status badges."""
    import streamlit as st

    if df.empty:
        return

    rows: list[str] = []
    for _, row in df.head(limit).iterrows():
        player = html.escape(str(row.get("player") or "Player"))
        pos = html.escape(str(row.get("position") or ""))
        team = html.escape(str(row.get("team_abbr") or team_abbr(str(row.get("team") or ""))))
        status_raw = str(row.get("status") or "")
        badge_txt, badge_cls = _injury_badge(status_raw)
        note = html.escape(str(row.get("note") or row.get("injury") or "")[:220])
        rel = html.escape(_hours_ago(row.get("updated")))
        head = str(row.get("headshot") or "")
        avatar = (
            f'<img class="bo-inj-avatar" src="{html.escape(head)}" alt="" loading="lazy" />'
            if head
            else '<div class="bo-inj-avatar bo-logo-fallback"></div>'
        )
        rows.append(
            f'<div class="bo-inj-row">'
            f'<div class="bo-inj-time">{rel}</div>'
            f'{avatar}'
            f'<div class="bo-inj-body">'
            f'<div class="bo-inj-top"><span class="bo-inj-player">{player}</span>'
            f'<span class="bo-inj-meta">{pos} · {team}</span></div>'
            f'<span class="bo-inj-badge {badge_cls}">{html.escape(badge_txt)}</span>'
            f'<div class="bo-inj-note">{note}</div></div></div>'
        )

    st.markdown(f'<div class="bo-inj-board">{"".join(rows)}</div>', unsafe_allow_html=True)


def _model_proj_from_row(row: pd.Series, *, kind: str, side: str) -> float | None:
    try:
        proj = float(row.get("modelProj"))
    except (TypeError, ValueError):
        return None
    if kind == "spread" and side == "away_cover":
        return -proj
    return proj


def _build_game_rows(
    df: pd.DataFrame,
    *,
    kind: str,
    week: int,
    abbr_lookup: dict[str, str],
    year: int | None = None,
    quick: bool = True,
    price_cache: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if df.empty:
        return []

    cache = price_cache if price_cache is not None else {}

    cols = ["matchup", "home", "away", "homeLogo", "awayLogo", "line", "modelProj", "side", "startDate"]
    show = df[[c for c in cols if c in df.columns]].copy()
    if kind == "total":
        show = show.drop_duplicates(subset=["matchup"], keep="first")
    else:
        show = show.drop_duplicates(subset=["matchup", "line"], keep="first")

    games: list[dict[str, Any]] = []
    for _, row in show.iterrows():
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        side = str(row.get("side") or "")
        slate_proj = _model_proj_from_row(row, kind=kind, side=side)
        cache_key = f"{away}|{home}"
        if cache_key not in cache:
            cache[cache_key] = price_game(
                home,
                away,
                season=year,
                display_week=week,
                refresh=not quick,
            ) or {}
        priced = cache[cache_key]
        try:
            raw_line = float(row.get("line"))
        except (TypeError, ValueError):
            raw_line = None

        if kind == "spread":
            line = -raw_line if side == "away_cover" and raw_line is not None else raw_line
            proj = priced.get("spread")
            if proj is None:
                proj = slate_proj
        else:
            line = raw_line
            proj = priced.get("total")
            if proj is None:
                proj = slate_proj

        if kind == "total" and line is not None and proj is not None:
            diff = round(proj - line, 1)
            lean = "OVER" if proj > line else "UNDER"
            pick_cls = "bo-pick-over" if lean == "OVER" else "bo-pick-under"
            pick_txt = f"{lean} {line:g}"
        elif kind == "spread" and line is not None and proj is not None:
            diff = round(proj - line, 1)
            if proj < line:
                pick_cls = "bo-pick-under"
                pick_txt = f"HOME {line:g}"
            else:
                pick_cls = "bo-pick-over"
                pick_txt = f"AWAY +{abs(line):g}"
        else:
            diff, pick_cls, pick_txt = None, "", "—"

        badge, sub = _format_kickoff(row.get("startDate"), week)
        games.append(
            {
                "away": away,
                "home": home,
                "away_abbr": team_abbr(away, abbr_lookup),
                "home_abbr": team_abbr(home, abbr_lookup),
                "away_logo": row.get("awayLogo"),
                "home_logo": row.get("homeLogo"),
                "away_score": priced.get("away_score"),
                "home_score": priced.get("home_score"),
                "line": line,
                "proj": proj,
                "diff": diff,
                "pick_cls": pick_cls,
                "pick_txt": pick_txt,
                "badge": badge,
                "sub": sub,
            }
        )
    return games


def render_projection_board(
    df: pd.DataFrame,
    *,
    kind: str = "total",
    week: int = 1,
    abbr_lookup: dict[str, str] | None = None,
    limit: int = 40,
) -> None:
    """Reference-style projections list — logos, scores, totals, colored picks."""
    import streamlit as st

    games = _build_game_rows(df, kind=kind, week=week, abbr_lookup=abbr_lookup or {})[:limit]
    if not games:
        return

    rows: list[str] = []
    for g in games:
        away_s = _format_proj_score(g.get("away_score")) or "—"
        home_s = _format_proj_score(g.get("home_score")) or "—"
        line_txt = f"{g['line']:g}" if g.get("line") is not None else "—"
        proj_txt = f"{g['proj']:g}" if g.get("proj") is not None else "—"

        if kind == "total":
            total_main = f'<span class="bo-proj-num">{proj_txt}</span> <span class="bo-proj-sep">/</span> <span class="bo-proj-line">{line_txt}</span>'
        else:
            total_main = f'<span class="bo-proj-num">{proj_txt}</span> <span class="bo-proj-sep">/</span> <span class="bo-proj-line">{line_txt}</span>'

        diff = g.get("diff")
        if diff is not None:
            diff_cls = "pos" if diff > 0 else "neg" if diff < 0 else "flat"
            diff_txt = f"{diff:+.1f}".replace("+0.0", "0").replace("-0.0", "0")
            if diff_txt in ("+0", "-0", "0.0"):
                diff_txt = "0"
            diff_html = f'<span class="bo-proj-diff {diff_cls}">{html.escape(diff_txt)}</span>'
        else:
            diff_html = ""

        rows.append(
            f'<div class="bo-proj-row">'
            f'<div class="bo-proj-date"><span class="bo-proj-date-badge">{html.escape(str(g["badge"]))}</span></div>'
            f'<div class="bo-proj-match">'
            f'<div class="bo-proj-team">'
            f'{_logo_img(g.get("away_logo") or "")}'
            f'<span class="bo-proj-abbr">{html.escape(g["away_abbr"])}</span>'
            f'<span class="bo-proj-score">{away_s}</span></div>'
            f'<span class="bo-proj-at">@</span>'
            f'<div class="bo-proj-team">'
            f'{_logo_img(g.get("home_logo") or "")}'
            f'<span class="bo-proj-abbr">{html.escape(g["home_abbr"])}</span>'
            f'<span class="bo-proj-score">{home_s}</span></div>'
            f'<div class="bo-proj-sub">{html.escape(g.get("sub") or "")}</div></div>'
            f'<div class="bo-proj-mid">{total_main}{diff_html}</div>'
            f'<div class="bo-proj-pick {g.get("pick_cls") or ""}">{html.escape(str(g.get("pick_txt") or "—"))}</div>'
            f'<button class="bo-proj-add" type="button" aria-label="Track">+</button>'
            f"</div>"
        )

    st.markdown(f'<div class="bo-proj-board">{"".join(rows)}</div>', unsafe_allow_html=True)


def render_projection_cards(df: pd.DataFrame, *, kind: str = "total") -> None:
    """Legacy card layout — prefer render_projection_board."""
    render_projection_board(df, kind=kind, week=1)


def _logo_for_team(team: str, lookup: dict[str, str]) -> str:
    t = str(team or "").strip().lower()
    if not t:
        return ""
    if t in lookup:
        return lookup[t]
    for key, url in lookup.items():
        if t in key or key in t:
            return url
    return ""


def render_market_move_chart(
    chart_rows: pd.DataFrame,
    logo_lookup: dict[str, str],
    *,
    hours: float = 22,
    y_min: float = -3.0,
    y_max: float = 1.0,
) -> None:
    """Pinnacle spread move chart — kickoff order, logos on top, green up / purple down."""
    import streamlit as st

    if chart_rows.empty:
        return

    steamed = int((chart_rows["move"] > 0).sum())
    came_down = int((chart_rows["move"] < 0).sum())
    chart_h = 220
    span = y_max - y_min
    zero_y = int((y_max - 0) / span * chart_h)
    tick_step = 0.5
    ticks: list[str] = []
    t = y_min
    while t <= y_max + 1e-9:
        label = f"{t:+.1f}" if t > 0 else f"{t:.1f}"
        top = 44 + int((y_max - t) / span * chart_h)
        ticks.append(
            f'<div class="bo-mm-ytick" style="top:{top}px"><span>{html.escape(label)}</span></div>'
        )
        t = round(t + tick_step, 1)

    up_room = zero_y
    down_room = chart_h - zero_y

    bars: list[str] = []
    for _, row in chart_rows.iterrows():
        move = float(row["move"])
        clamped = max(y_min, min(y_max, move))
        if move > 0:
            bar_px = max(3, int(clamped / y_max * up_room)) if y_max else 3
        elif move < 0:
            bar_px = max(3, int(abs(clamped) / abs(y_min) * down_room)) if y_min else 3
        else:
            bar_px = 0
        logo = _logo_for_team(str(row.get("logo_team") or ""), logo_lookup)
        logo_html = (
            f'<img class="bo-mm-logo" src="{html.escape(logo)}" alt="" />'
            if logo
            else '<div class="bo-mm-logo bo-mm-logo-fallback"></div>'
        )
        val = f"{move:+.1f}"
        if move > 0:
            bars.append(
                f"""
<div class="bo-mm-col">
  {logo_html}
  <div class="bo-mm-bar up" style="bottom:{chart_h - zero_y}px;height:{bar_px}px"><span>{html.escape(val)}</span></div>
</div>"""
            )
        elif move < 0:
            bars.append(
                f"""
<div class="bo-mm-col">
  {logo_html}
  <div class="bo-mm-bar down" style="top:{zero_y}px;height:{bar_px}px"><span>{html.escape(val)}</span></div>
</div>"""
            )
        else:
            bars.append(
                f"""
<div class="bo-mm-col">
  {logo_html}
  <div class="bo-mm-dot" style="top:{zero_y}px"></div>
</div>"""
            )

    idx = chart_rows["move"].abs().idxmax()
    hero = chart_rows.loc[idx]
    hero_move = float(hero["move"])
    hero_label = str(hero.get("event_short") or hero.get("event") or "")
    if hero_move > 0:
        hero_phrase = f"{html.escape(hero_label)} STEAMED {hero_move:+.1f}"
    elif hero_move < 0:
        hero_phrase = f"{html.escape(hero_label)} CAME DOWN {hero_move:+.1f}"
    else:
        hero_phrase = html.escape(hero_label)
    off_chart = abs(hero_move) > max(abs(y_min), abs(y_max))
    if off_chart:
        hero_phrase += ', <span class="bo-mm-off">OFF THE CHART</span>'

    st.markdown(
        f"""
<div class="bo-mm-panel">
  <div class="bo-mm-chart-wrap" style="--mm-axis-h:{chart_h}px">
    <div class="bo-mm-yaxis">
      <div class="bo-mm-ylabel">MARKET<br/>MOVE</div>
      {"".join(ticks)}
    </div>
    <div class="bo-mm-plot" style="--mm-h:{chart_h}px;--mm-zero:{zero_y}px">
      <div class="bo-mm-zero"></div>
      <div class="bo-mm-scroll">
        <div class="bo-mm-bars">{"".join(bars)}</div>
      </div>
    </div>
  </div>
  <div class="bo-mm-footer">
    <div class="bo-mm-callout">{hero_phrase}</div>
    <div class="bo-mm-meta">
      <span class="bo-mm-legend"><i class="bo-mm-swatch down"></i> CAME DOWN {came_down}</span>
      <span class="bo-mm-legend"><i class="bo-mm-swatch up"></i> STEAMED OVER {steamed}</span>
      <span class="bo-mm-order">KICKOFF ORDER · LAST {int(hours)}H</span>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_money_on_dogs_chart(chart_rows: pd.DataFrame, logo_lookup: dict[str, str]) -> None:
    """Diverging dog/fav spread move chart matching Football Labs reference."""
    import streamlit as st

    if chart_rows.empty:
        return

    dogs = int((chart_rows["move"] > 0).sum())
    favs = int((chart_rows["move"] < 0).sum())
    badge = f"{dogs} GAMES MOVED TO THE DOG · {favs} TO THE FAVOURITE"

    max_abs = max(float(chart_rows["move"].abs().max()), 0.5)
    max_px = 118

    bars: list[str] = []
    for _, row in chart_rows.iterrows():
        move = float(row["move"])
        is_dog = move > 0
        height = max(12, int(abs(move) / max_abs * max_px))
        team = str(row.get("team") or "")
        logo = _logo_for_team(team, logo_lookup)
        val = f"{move:+.1f}"
        logo_html = (
            f'<img class="bo-dog-logo" src="{html.escape(logo)}" alt="" />'
            if logo
            else '<div class="bo-dog-logo" style="background:#1a1a22;border-radius:50%"></div>'
        )
        cls = "pos" if is_dog else "neg"
        bars.append(
            f"""
<div class="bo-dog-col {cls}" style="--bar-h:{height}px">
  <div class="bo-dog-bar {"up" if is_dog else "down"}" style="height:{height}px">
    <span class="bo-dog-val">{html.escape(val)}</span>
  </div>
  {logo_html}
</div>"""
        )

    st.markdown(
        f"""
<div class="bo-dog-panel">
  <div class="bo-dog-head">
    <h2 class="bo-dog-title">THE MONEY IS ON THE DOGS</h2>
    <div class="bo-dog-badge">{html.escape(badge)}</div>
  </div>
  <div class="bo-dog-stage">
    <div class="bo-dog-axis"></div>
    <div class="bo-dog-bars">{"".join(bars)}</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


_BOOK_COLORS = {
    "novig": "#a78bfa",
    "draftkings": "#22c55e",
    "fanduel": "#38bdf8",
    "betmgm": "#fbbf24",
    "caesars": "#f59e0b",
    "pinnacle": "#c084fc",
    "thescore": "#38bdf8",
}


def _book_color(name: str) -> str:
    low = str(name or "").lower()
    for key, color in _BOOK_COLORS.items():
        if key in low:
            return color
    return "#71717a"


def render_clv_dashboard(report: dict[str, Any]) -> None:
    """Market Moves — line travel toward model, best price, travelled panel."""
    import streamlit as st

    top_l, top_r = st.columns([1.08, 1], gap="large")
    with top_l:
        render_clv_model_vs_close(report)
    with top_r:
        render_clv_best_price(report)
    st.markdown('<div class="bo-clv-section-gap"></div>', unsafe_allow_html=True)
    render_clv_travelled(report)


def render_clv_model_vs_close(report: dict[str, Any]) -> None:
    import streamlit as st

    pct = report.get("pct")
    raw_grid = report.get("grid") or []
    grid_items: list[dict[str, Any]] = []
    for item in raw_grid:
        if isinstance(item, dict):
            grid_items.append(item)
        else:
            grid_items.append(
                {
                    "toward": bool(item),
                    "matchup": "",
                    "market": "",
                    "line_display": "—",
                    "model_display": "—",
                }
            )
    with_us = report.get("with_us", 0)
    against = report.get("against", 0)
    spread_pct = report.get("spread_pct")
    total_pct = report.get("total_pct")
    spread_hits = report.get("spread_hits", 0)
    spread_graded = report.get("spread_graded", 0)
    total_hits = report.get("total_hits", 0)
    total_graded = report.get("total_graded", 0)
    graded = report.get("graded", 0)

    pct_txt = f"{pct}%" if pct is not None else "—"
    glow_cls = "bo-clv-glow-yes" if pct is not None and pct >= 50 else "bo-clv-glow-no"
    market_tag = {"Spread": "S", "Total": "T"}
    cells: list[str] = []
    for item in grid_items:
        toward = bool(item.get("toward"))
        cell_cls = "bo-clv-cell-yes" if toward else "bo-clv-cell-no"
        wrap_cls = "yes" if toward else "no-toward"
        matchup = html.escape(str(item.get("matchup") or "—"))
        market_raw = str(item.get("market") or "")
        market = html.escape(market_raw)
        tag = html.escape(market_tag.get(market_raw, "·"))
        line_txt = html.escape(str(item.get("line_display") or "—"))
        model_txt = html.escape(str(item.get("model_display") or "—"))
        meta = market if market else "Market"
        cells.append(
            f'<div class="bo-clv-cell-wrap {wrap_cls}">'
            f'<div class="bo-clv-cell {cell_cls}"><span class="bo-clv-cell-tag">{tag}</span></div>'
            f'<div class="bo-clv-tip">'
            f'<div class="bo-clv-tip-match">{matchup}</div>'
            f'<div class="bo-clv-tip-meta">{meta} · {line_txt}</div>'
            f'<div class="bo-clv-tip-meta">{model_txt}</div>'
            f"</div></div>"
        )

    spread_stat = f"{spread_pct}% SPREADS" if spread_pct is not None else "— SPREADS"
    spread_sub = f"{spread_hits}/{spread_graded}" if spread_graded else ""
    total_stat = f"{total_pct}% TOTALS" if total_pct is not None else "— TOTALS"
    total_sub = f"{total_hits}/{total_graded}" if total_graded else ""
    st.markdown(
        f"""
<div class="bo-clv-panel bo-clv-panel-hero">
  <div class="bo-clv-hero-pct {glow_cls}">{html.escape(pct_txt)}</div>
  <div class="bo-clv-hero-sub">when the line moved, it moved <strong>toward us</strong>.</div>
  <div class="bo-clv-grid">{"".join(cells) if cells else '<div class="bo-clv-empty">No line moves graded this week.</div>'}</div>
  <div class="bo-clv-legend">
    <span><i class="bo-clv-dot yes"></i> {with_us} MOVED WITH US</span>
    <span><i class="bo-clv-dot no"></i> {against} MOVED AGAINST</span>
  </div>
  <div class="bo-clv-foot-stats">
    <div class="bo-clv-stat-card"><strong>{html.escape(spread_stat)}</strong><span>{html.escape(spread_sub)}</span></div>
    <div class="bo-clv-stat-card"><strong>{html.escape(total_stat)}</strong><span>{html.escape(total_sub)}</span></div>
    <div class="bo-clv-stat-card"><strong>{graded}</strong><span>LINE MOVES GRADED</span></div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


def render_clv_best_price(report: dict[str, Any]) -> None:
    import streamlit as st

    from lib.book_logos import book_logo_img

    books: list[dict[str, Any]] = report.get("best_price") or []
    if not books:
        st.markdown(
            """
<div class="bo-clv-panel">
  <div class="bo-clv-eyebrow">Who gave you the best price</div>
  <div class="bo-clv-empty">Refresh lines once to compare books vs our number.</div>
</div>""",
            unsafe_allow_html=True,
        )
        return

    top = books[0]
    donut_stops: list[str] = []
    acc = 0.0
    for b in books:
        if b.get("book") == "Everyone else":
            continue
        acc += float(b.get("share_pct") or 0)
        donut_stops.append(f"{_book_color(b.get('book', ''))} {acc}%")
    donut_bg = f"conic-gradient({', '.join(donut_stops) if donut_stops else '#a78bfa 100%'})"

    top_bid = str(top.get("book_id") or "")
    top_logo = (
        book_logo_img(top_bid, size=26, cls="bo-clv-donut-logo")
        if top_bid
        else '<span class="bo-clv-donut-logo-fallback">—</span>'
    )

    rows: list[str] = []
    for b in books:
        bid = str(b.get("book_id") or "")
        logo = (
            book_logo_img(bid, size=22, cls="bo-clv-book-logo")
            if bid
            else '<span class="bo-clv-book-logo-fallback"></span>'
        )
        share_color = _book_color(str(b.get("book") or ""))
        share_html = (
            f'<span class="bo-clv-book-share-wrap">'
            f'<span class="bo-clv-book-share" style="color:{share_color}">{b.get("share_pct", 0)}%</span>'
            f"</span>"
        )
        rows.append(
            f'<div class="bo-clv-book-row">{logo}'
            f'<span class="bo-clv-book-name">{html.escape(str(b.get("book") or ""))}</span>'
            f'{share_html}</div>'
        )

    st.markdown(
        f"""
<div class="bo-clv-panel bo-clv-panel-books">
  <div class="bo-clv-eyebrow">Who gave you the best price</div>
  <div class="bo-clv-best-sub">Share of markets where each book posted the best American price.</div>
  <div class="bo-clv-donut-wrap">
    <div class="bo-clv-donut" style="background:{donut_bg}">
      <div class="bo-clv-donut-hole">
        {top_logo}
        <div class="bo-clv-donut-top">{top.get("share_pct", 0)}%</div>
        <div class="bo-clv-donut-label">{html.escape(str(top.get("book") or ""))}</div>
      </div>
    </div>
    <div class="bo-clv-book-list">{"".join(rows)}</div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )


def render_clv_travelled(report: dict[str, Any]) -> None:
    import streamlit as st

    week = report.get("week", 0)
    sides = report.get("travelled_sides") or []
    totals = report.get("travelled_totals") or []

    def _branch(items: list[dict[str, Any]], *, side: str) -> str:
        out: list[str] = []
        for item in items:
            toward = item.get("toward")
            cls = "toward" if toward else "against"
            delta = item.get("delta", 0)
            sign = f"+{delta}" if delta > 0 else str(delta)
            away_logo = str(item.get("awayLogo") or "")
            home_logo = str(item.get("homeLogo") or "")
            al = (
                f'<img class="bo-clv-travel-logo" src="{html.escape(away_logo)}" alt="" />'
                if away_logo and away_logo != "nan"
                else ""
            )
            hl = (
                f'<img class="bo-clv-travel-logo" src="{html.escape(home_logo)}" alt="" />'
                if home_logo and home_logo != "nan"
                else ""
            )
            line_txt = f"{item.get('open'):g} → {item.get('close'):g}"
            out.append(
                f'<div class="bo-clv-branch {cls} bo-clv-branch-{side}">'
                f'<div class="bo-clv-branch-line"></div>'
                f'<div class="bo-clv-branch-card">'
                f'<div class="bo-clv-travel-logos">{al}{hl}</div>'
                f'<div class="bo-clv-travel-match">{html.escape(str(item.get("matchup", "")))}: '
                f'{line_txt} <span class="bo-clv-travel-delta">({sign})</span></div>'
                f"</div></div>"
            )
        if not out:
            out.append('<div class="bo-clv-empty">No line travel this week.</div>')
        return "".join(out)

    st.markdown(
        f"""
<div class="bo-clv-panel bo-clv-panel-travel">
  <div class="bo-clv-eyebrow">How far the number travelled · Week {week}</div>
  <div class="bo-clv-travel-hub">
    <div class="bo-clv-travel-col bo-clv-travel-col-left">
      <div class="bo-clv-travel-head">Sides</div>
      <div class="bo-clv-branch-stack">{_branch(sides, side="left")}</div>
    </div>
    <div class="bo-clv-travel-spine"></div>
    <div class="bo-clv-travel-col bo-clv-travel-col-right">
      <div class="bo-clv-travel-head">Totals</div>
      <div class="bo-clv-branch-stack">{_branch(totals, side="right")}</div>
    </div>
  </div>
  <div class="bo-clv-travel-foot">Lines from cfbfastR / nflfastR · green = moved toward our number</div>
</div>""",
        unsafe_allow_html=True,
    )


def _rz_logo(url: str | None, *, size: int = 28) -> str:
    if url and str(url) != "nan":
        return f'<img class="bo-rz-logo" src="{html.escape(str(url))}" alt="" width="{size}" height="{size}" />'
    return '<span class="bo-rz-logo-fallback"></span>'


def render_weekly_brief_cards(
    *,
    headline: str,
    subtitle: str,
    bullets: list[dict[str, str]],
    weather_rows: list[dict],
    lean_rows: list[dict],
    meta: str,
    week: int,
) -> None:
    """REDZONE-style Weekly Brief stack — thread, weather, slate lean."""
    import streamlit as st

    bullet_html = "".join(
        f'<li><span class="bo-rz-dot"></span><div><strong>{html.escape(b["title"])}</strong>'
        f'<p>{html.escape(b["body"])}</p></div></li>'
        for b in bullets
    )

    st.markdown(
        f"""
<div class="bo-rz-card">
  <div class="bo-rz-card-top">
    <span class="bo-rz-eyebrow">THE WEEKLY BRIEF</span>
    <span class="bo-rz-meta">{html.escape(meta)}</span>
  </div>
  <h2 class="bo-rz-title">{html.escape(headline)}</h2>
  <p class="bo-rz-sub">{html.escape(subtitle)}</p>
  <ul class="bo-rz-bullets">{bullet_html}</ul>
  <div class="bo-rz-card-foot">
    <span class="bo-rz-copy-hint">Copy tweet</span>
    <span class="bo-rz-foot-note">Week {week} thread · model + market cross-check</span>
  </div>
  <div class="bo-rz-card-accent"></div>
</div>
""",
        unsafe_allow_html=True,
    )

    if weather_rows:
        wrows: list[str] = []
        max_adj = max(abs(float(r.get("total_adj") or 0)) for r in weather_rows) or 1.0
        for r in weather_rows:
            adj = float(r.get("total_adj") or 0)
            bar_w = min(100, int(abs(adj) / max_adj * 100))
            cls = "neg" if adj < 0 else "pos"
            rain = "🌧" if float(r.get("precip_pct") or 0) >= 35 else "☁"
            wrows.append(
                f"""
<div class="bo-rz-weather-row">
  <div class="bo-rz-weather-match">
    {_rz_logo(r.get("away_logo"))}{_rz_logo(r.get("home_logo"))}
    <div>
      <div class="bo-rz-match-label">{html.escape(str(r.get("away_abbr")))} @ {html.escape(str(r.get("home_abbr")))}</div>
      <div class="bo-rz-match-sub">{html.escape(str(r.get("venue") or "")[:42])} · {html.escape(str(r.get("kickoff") or ""))}</div>
    </div>
  </div>
  <div class="bo-rz-weather-stats">{rain} {int(r.get("temp_f") or 0)}°F · {int(r.get("wind_mph") or 0)} mph · {int(r.get("precip_pct") or 0)}% rain</div>
  <div class="bo-rz-total-bar-wrap">
    <span class="bo-rz-total-label">total</span>
    <div class="bo-rz-total-bar {cls}" style="width:{bar_w}px"></div>
    <span class="bo-rz-total-val">{adj:+.1f}</span>
  </div>
</div>"""
            )

        st.markdown(
            f"""
<div class="bo-rz-card">
  <div class="bo-rz-card-top">
    <span class="bo-rz-eyebrow">WEATHER REPORT</span>
    <span class="bo-rz-meta">{html.escape(meta)}</span>
  </div>
  <h2 class="bo-rz-title">{len(weather_rows)} games · what the sky does to the total</h2>
  <p class="bo-rz-sub">Outdoor kickoffs · wind, rain, and temperature adjustment on the total.</p>
  <div class="bo-rz-weather-list">{"".join(wrows)}</div>
  <div class="bo-rz-card-foot">
    <span class="bo-rz-copy-hint">Copy tweet</span>
    <span class="bo-rz-foot-note">Open-Meteo hourly from kickoff</span>
  </div>
  <div class="bo-rz-card-accent"></div>
</div>
""",
            unsafe_allow_html=True,
        )

    if lean_rows:
        lrows: list[str] = []
        max_diff = max(abs(float(r.get("diff") or 0)) for r in lean_rows) or 1.0
        for r in lean_rows:
            diff = float(r.get("diff") or 0)
            bar_w = min(120, int(abs(diff) / max_diff * 120))
            cls = "neg" if diff < 0 else "pos"
            lrows.append(
                f"""
<div class="bo-rz-lean-row">
  <div class="bo-rz-weather-match">
    {_rz_logo(r.get("away_logo"))}{_rz_logo(r.get("home_logo"))}
    <div>
      <div class="bo-rz-match-label">{html.escape(str(r.get("away_abbr")))} @ {html.escape(str(r.get("home_abbr")))}</div>
      <div class="bo-rz-match-sub">Posted {html.escape(str(r.get("line") or "—"))} · Model {html.escape(str(r.get("proj") or "—"))}</div>
    </div>
  </div>
  <div class="bo-rz-total-bar-wrap">
    <span class="bo-rz-total-label">total</span>
    <div class="bo-rz-total-bar {cls}" style="width:{bar_w}px"></div>
    <span class="bo-rz-total-val">{diff:+.1f}</span>
  </div>
</div>"""
            )

        st.markdown(
            f"""
<div class="bo-rz-card">
  <div class="bo-rz-card-top">
    <span class="bo-rz-eyebrow">WEEK {week} SLATE LEAN</span>
    <span class="bo-rz-meta">{html.escape(meta)}</span>
  </div>
  <h2 class="bo-rz-title">Where we disagree with the Week {week} market</h2>
  <p class="bo-rz-sub">Our total vs the posted number · {len(lean_rows)} games</p>
  <div class="bo-rz-weather-list">{"".join(lrows)}</div>
  <div class="bo-rz-card-foot">
    <span class="bo-rz-copy-hint">Copy tweet</span>
    <span class="bo-rz-foot-note">Model projections · market lines</span>
  </div>
  <div class="bo-rz-card-accent"></div>
</div>
""",
            unsafe_allow_html=True,
        )


def render_games_to_watch(rows: list[dict]) -> None:
    import streamlit as st

    if not rows:
        st.markdown('<div class="bo-rz-empty">No flagged plays on the slate yet.</div>', unsafe_allow_html=True)
        return

    cards = "".join(
        f"""
<div class="bo-rz-watch-row">
  {_rz_logo(r.get("away_logo"))}{_rz_logo(r.get("home_logo"))}
  <div class="bo-rz-watch-body">
    <div class="bo-rz-match-label">{html.escape(str(r.get("away_abbr")))} @ {html.escape(str(r.get("home_abbr")))}</div>
    <div class="bo-rz-match-sub">{html.escape(str(r.get("market") or ""))}</div>
  </div>
  <div class="bo-rz-watch-edge">{html.escape(str(r.get("edge") or "—"))}%</div>
</div>"""
        for r in rows
    )
    st.markdown(f'<div class="bo-rz-card"><div class="bo-rz-watch-list">{cards}</div><div class="bo-rz-card-accent"></div></div>', unsafe_allow_html=True)


def render_fantasy_chart(rows: list[dict]) -> None:
    import streamlit as st

    if not rows:
        st.markdown('<div class="bo-rz-empty">Player props populate when the slate is live.</div>', unsafe_allow_html=True)
        return

    cards = "".join(
        f"""
<div class="bo-rz-prop-row">
  <div class="bo-rz-prop-player">{html.escape(str(r.get("player") or ""))}</div>
  <div class="bo-rz-prop-meta">{html.escape(str(r.get("team") or ""))} · {html.escape(str(r.get("prop") or ""))}</div>
  <div class="bo-rz-prop-edge">{html.escape(str(r.get("edge") or "—"))}%</div>
  <div class="bo-rz-prop-price">{html.escape(str(r.get("price") or ""))}</div>
</div>"""
        for r in rows
    )
    st.markdown(f'<div class="bo-rz-card"><div class="bo-rz-prop-list">{cards}</div><div class="bo-rz-card-accent"></div></div>', unsafe_allow_html=True)


def _bar_width(diff: float | None, *, cap: float = 4.0) -> int:
    if diff is None:
        return 8
    return max(8, min(100, int(abs(float(diff)) / cap * 100)))


def _bar_width(diff: float | None, *, cap: float = 4.0) -> int:
    if diff is None:
        return 8
    return max(8, min(100, int(abs(float(diff)) / cap * 100)))


def _display_spread(home_line: float | None, home: str = "", away: str = "") -> str:
    if home_line is None:
        return "—"
    try:
        n = float(home_line)
    except (TypeError, ValueError):
        return "—"
    if home and away:
        if n < 0:
            return f"{home} {n:g}"
        if n > 0:
            return f"{away} {-n:g}"
        return "PK"
    away_val = -n
    return f"{away_val:+.1f}" if away_val > 0 else f"{away_val:.1f}"


def _display_total(val: float | None) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):.1f}"
    except (TypeError, ValueError):
        return "—"


def _format_proj_score(val: float | None) -> str | None:
    if val is None:
        return None
    try:
        from lib.sp_projections import MIN_TEAM_SCORE

        f = float(val)
        if not math.isfinite(f):
            return None
        n = int(round(f))
        if n < MIN_TEAM_SCORE:
            n = int(MIN_TEAM_SCORE)
        return str(n)
    except (TypeError, ValueError):
        return None


def _safe_score_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        f = float(val)
        if not math.isfinite(f):
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _card_score(g: dict) -> tuple[str, str, str]:
    """Return away score, home score, css class for final vs projected."""
    if g.get("completed") and g.get("home_score") is not None:
        a = _safe_score_int(g.get("away_score"))
        h = _safe_score_int(g.get("home_score"))
        if a is not None and h is not None:
            return str(a), str(h), "final"
    pa, ph = g.get("proj_away"), g.get("proj_home")
    if pa is None or ph is None:
        spread = g.get("spread_proj")
        total = g.get("total_proj")
        if spread is not None and total is not None:
            try:
                from lib.sp_projections import scores_from_spread_total

                pa, ph = scores_from_spread_total(float(spread), float(total))
            except (TypeError, ValueError):
                pa, ph = None, None
    away_s = _format_proj_score(pa)
    home_s = _format_proj_score(ph)
    if away_s is not None and home_s is not None:
        return away_s, home_s, "proj"
    return "—", "—", "empty"


def _card_meta_line(g: dict) -> str:
    spread_proj = g.get("spread_proj")
    spread_line = g.get("spread_line")
    total_line = g.get("total_line")
    total_proj = g.get("total_proj")
    date_str = str(g.get("date_str") or "")

    if g.get("completed") and (
        g.get("open_spread") is not None or g.get("close_spread") is not None
    ):
        home = str(g.get("home") or "")
        away = str(g.get("away") or "")
        open_sp = _display_spread(g.get("open_spread"), home, away)
        close_sp = _display_spread(g.get("close_spread"), home, away)
        open_tot = _display_total(g.get("open_total"))
        close_tot = _display_total(g.get("close_total"))
        return (
            f"{html.escape(date_str)} · "
            f"open {open_sp} → close {close_sp} · "
            f"tot {open_tot} → {close_tot} · "
            f"ours {_display_spread(spread_proj, home, away)}"
        )

    home = str(g.get("home") or "")
    away = str(g.get("away") or "")
    return (
        f"{html.escape(date_str)} · "
        f"ours {_display_spread(spread_proj, home, away)} | mkt {_display_spread(spread_line, home, away)} · "
        f"tot {_display_total(total_proj)} v {_display_total(total_line)}"
    )


def _edge_badge(diff: Any) -> str:
    if diff is None:
        return ""
    try:
        d = float(diff)
    except (TypeError, ValueError):
        return ""
    if abs(d) < 0.05:
        txt, cls = "0", "bo-mg-edge-neutral"
    elif d > 0:
        txt, cls = f"+{d:g}", "bo-mg-edge-pos"
    else:
        txt, cls = f"{d:g}", "bo-mg-edge-neg"
    return f'<span class="bo-mg-edge {cls}">{html.escape(txt)}</span>'


def render_game_projection_cards(cards: list[dict], *, selected: str | None = None) -> None:
    """Reference-style 4-column matchup grid — upcoming first, finals at bottom."""
    import streamlit as st

    from lib.book_logos import book_logo_img
    from lib.team_logos import logo_img_html

    if not cards:
        st.markdown('<div class="bo-rz-empty">No games on the board for this week.</div>', unsafe_allow_html=True)
        return None

    upcoming = [g for g in cards if not g.get("completed")]
    finished = [g for g in cards if g.get("completed")]

    def _spread_label(abbr: str, line: Any) -> str:
        if line is None:
            return f"{abbr} —"
        try:
            n = float(line)
        except (TypeError, ValueError):
            return f"{abbr} —"
        if n > 0:
            return f"{abbr} +{n:g}"
        if n < 0:
            return f"{abbr} {n:g}"
        return f"{abbr} PK"

    def _book_chip(quote: dict | None) -> str:
        if not quote or quote.get("price") in (None, "", "—"):
            return ""
        book = str(quote.get("book") or "Best")
        from lib.config import normalize_book_id

        bid = normalize_book_id(str(quote.get("book_id") or book))
        price = str(quote.get("price") or "—")
        return (
            f'<span class="bo-mg-q-book bo-mg-q-book-id" data-book="{html.escape(bid)}">'
            f'{book_logo_img(bid, size=11, cls="bo-mg-book-logo")}'
            f'<span class="bo-mg-q-price">{html.escape(price)}</span></span>'
        )

    def _quote_cell(label: str, quote: dict | None, *, fourc_key: str, abbr: str = "", side: str = "") -> str:
        chip = _book_chip(quote)
        books_html = chip if chip else '<span class="bo-mg-q-empty">—</span>'
        abbr_attr = html.escape(abbr)
        side_attr = html.escape(side)
        return (
            f'<div class="bo-mg-cell" data-fourc-key="{html.escape(fourc_key)}">'
            f'<div class="bo-mg-q-label" data-abbr="{abbr_attr}" data-side="{side_attr}">{html.escape(label)}</div>'
            f'<div class="bo-mg-q-meta">{books_html}</div>'
            f"</div>"
        )

    def _section_label(title: str, edge: Any = None) -> str:
        edge_html = _edge_badge(edge)
        return f'<div class="bo-mg-sec-label">{html.escape(title)}{edge_html}</div>'

    def _card_html(g: dict) -> str:
        away_abbr = str(g.get("away_abbr") or "")
        home_abbr = str(g.get("home_abbr") or "")
        away_logo = logo_img_html(str(g.get("away_logo") or ""), cls="bo-mg-team-logo")
        home_logo = logo_img_html(str(g.get("home_logo") or ""), cls="bo-mg-team-logo")
        kickoff = html.escape(str(g.get("kickoff") or g.get("date_str") or ""))
        broadcast = str(g.get("broadcast") or "")
        bc_logo = str(g.get("broadcast_logo") or "")
        if broadcast:
            from lib.broadcast_logos import broadcast_logo_img

            bc_html = broadcast_logo_img(broadcast, bc_logo, size=14)
        else:
            bc_html = ""

        away_s, home_s, _ = _card_score(g)
        if g.get("completed"):
            score_html = (
                f'<div class="bo-mg-score final">'
                f'<span>{html.escape(away_s)}</span>'
                f'<span class="bo-mg-score-sep">–</span>'
                f'<span>{html.escape(home_s)}</span></div>'
            )
        else:
            score_html = (
                f'<div class="bo-mg-score proj">'
                f'<span>{html.escape(away_s)}</span>'
                f'<span class="bo-mg-score-sep">–</span>'
                f'<span>{html.escape(home_s)}</span></div>'
            )

        away_sp = g.get("away_spread") or {}
        home_sp = g.get("home_spread") or {}
        over_q = g.get("over") or {}
        under_q = g.get("under") or {}
        fourc_id = html.escape(str(g.get("fourc_id") or g.get("event_id") or ""))

        away_line = away_sp.get("line")
        home_line = home_sp.get("line")
        if away_line is None and home_line is not None:
            try:
                away_line = -float(home_line)
            except (TypeError, ValueError):
                pass
        if home_line is None and away_line is not None:
            try:
                home_line = -float(away_line)
            except (TypeError, ValueError):
                pass

        away_spread_lbl = _spread_label(away_abbr, away_line)
        home_spread_lbl = _spread_label(home_abbr, home_line)
        total_line = over_q.get("line") or under_q.get("line") or g.get("total_line")
        try:
            tot_txt = f"{float(total_line):g}" if total_line is not None else "—"
        except (TypeError, ValueError):
            tot_txt = "—"

        away_tt = g.get("away_team_over") or {}
        home_tt = g.get("home_team_over") or {}
        away_tt_line = away_tt.get("line")
        home_tt_line = home_tt.get("line")
        try:
            away_tt_txt = f"{float(away_tt_line):g}" if away_tt_line is not None else "—"
        except (TypeError, ValueError):
            away_tt_txt = "—"
        try:
            home_tt_txt = f"{float(home_tt_line):g}" if home_tt_line is not None else "—"
        except (TypeError, ValueError):
            home_tt_txt = "—"

        matchup = str(g.get("matchup") or g.get("label") or "")
        active = selected == matchup
        cls = "bo-mg-card"
        if active:
            cls += " active"
        if g.get("completed"):
            cls += " bo-mg-card-final"
        spread_edge = g.get("spread_diff")
        total_edge = g.get("total_diff")
        away_tt_edge = g.get("away_tt_diff")
        home_tt_edge = g.get("home_tt_diff")
        fid = f' data-fourc-id="{fourc_id}"' if fourc_id else ""
        return (
            f'<div class="{cls}"{fid}>'
            f'<div class="bo-mg-head"><span class="bo-mg-time">{kickoff}</span>{bc_html}</div>'
            f'<div class="bo-mg-logos">{away_logo}{home_logo}</div>'
            f'<div class="bo-mg-match">{html.escape(away_abbr)} @ {html.escape(home_abbr)}</div>'
            f"{score_html}"
            f'<div class="bo-mg-body">'
            f'<div class="bo-mg-section">{_section_label("SPREAD", spread_edge)}'
            f'<div class="bo-mg-quotes">'
            f'{_quote_cell(away_spread_lbl, away_sp or None, fourc_key="spread_away", abbr=away_abbr)}'
            f'{_quote_cell(home_spread_lbl, home_sp or None, fourc_key="spread_home", abbr=home_abbr)}'
            f"</div></div>"
            f'<div class="bo-mg-section">{_section_label("TOTAL", total_edge)}'
            f'<div class="bo-mg-quotes">'
            f'{_quote_cell(f"Over {tot_txt}", over_q or None, fourc_key="total_over", side="Over")}'
            f'{_quote_cell(f"Under {tot_txt}", under_q or None, fourc_key="total_under", side="Under")}'
            f"</div></div>"
            f'<div class="bo-mg-section">{_section_label("TEAM TOTAL")}'
            f'<div class="bo-mg-quotes">'
            f'{_quote_cell(f"{away_abbr} O {away_tt_txt}", away_tt or None, fourc_key="away_tt", abbr=away_abbr)}'
            f'{_quote_cell(f"{home_abbr} O {home_tt_txt}", home_tt or None, fourc_key="home_tt", abbr=home_abbr)}'
            f"</div></div>"
            f"</div></div>"
        )

    def _pick_card(g: dict, *, col_idx: int, row_idx: int, pick_key: str) -> None:
        matchup = str(g.get("matchup") or g.get("label") or "")
        st.markdown(_card_html(g), unsafe_allow_html=True)
        if st.button(
            "Open",
            key=f"gp_pick_{pick_key}_{row_idx}_{col_idx}",
            use_container_width=True,
            type="secondary",
        ):
            st.session_state["gp_selected_matchup"] = matchup
            st.rerun()

    n_cols = 5

    if upcoming:
        for i in range(0, len(upcoming), n_cols):
            cols = st.columns(n_cols)
            for j, g in enumerate(upcoming[i : i + n_cols]):
                with cols[j]:
                    _pick_card(g, col_idx=j, row_idx=i, pick_key="up")

    if finished:
        st.markdown(
            f'<div class="bo-mg-finals-head"><span class="bo-mg-finals-label">Final scores</span>'
            f'<span class="bo-mg-finals-count">{len(finished)} games</span></div>',
            unsafe_allow_html=True,
        )
        for i in range(0, len(finished), n_cols):
            cols = st.columns(n_cols)
            for j, g in enumerate(finished[i : i + n_cols]):
                with cols[j]:
                    _pick_card(g, col_idx=j, row_idx=i, pick_key="fin")

    _mount_odds_live_sync(cards)


def _mount_odds_live_sync(cards: list[dict]) -> None:
    """Invisible client sync — patches card prices from Otter/4C mirror (no Streamlit rerun)."""
    import streamlit as st

    from lib.odds_live_feed import ensure_odds_live_feed, live_odds_enabled
    from lib.sport_context import SPORT_CFB, SPORT_NFL, get_sport

    sport = get_sport()
    if not live_odds_enabled() or sport not in (SPORT_CFB, SPORT_NFL):
        return
    if not any(c.get("event_id") or c.get("fourc_id") for c in cards):
        return
    ensure_odds_live_feed(sport)
    tag = "nfl" if sport == SPORT_NFL else "cfb"
    snippet = f'<script src="/app/static/fourc_live.js" data-sport="{tag}"></script>'
    if hasattr(st, "html"):
        st.html(snippet, unsafe_allow_javascript=True)
    else:
        import streamlit.components.v1 as components

        components.html(snippet, height=0, scrolling=False)

