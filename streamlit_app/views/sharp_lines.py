"""Sharp Lines — multi-book game lines and player props."""
from __future__ import annotations

import html

import streamlit as st

from lib.app_filters import render_global_filters
from lib.book_logos import book_header_html, book_logo_img
from lib.games import display_week_complete, list_games_for_display_week
from lib.projection_archive import props_have_results
from lib.config import odds_api_key
from lib.devig_methods import DEVIG_BOOK_PRESETS, DEVIG_METHODS
from lib.display import team_abbr
from lib.game_sharp_board import DISPLAY_SHARP_BOOKS, build_game_sharp_rows, filter_game_rows
from lib.multi_book_game_lines import (
    MARKET_FILTER_OPTIONS,
    cache_age_minutes as game_cache_age_minutes,
    ensure_multi_book_game_lines,
    lines_cache_needs_refresh,
    load_multi_book_game_lines,
    lines_cache_path,
)
from lib.multi_book_props import (
    PROP_MARKET_FILTER_OPTIONS,
    cache_age_minutes as props_cache_age_minutes,
    ensure_multi_book_props,
    load_multi_book_props,
    props_cache_needs_refresh,
    props_cache_path,
)
from lib.prop_sharp_board import (
    DISPLAY_PROP_BOOKS,
    build_prop_sharp_rows,
    filter_prop_rows,
)
from lib.sharp_edges import _is_spread_key
from lib.sharp_line_history import (
    enrich_sharp_rows_with_grades,
    filter_lines_to_display_week,
    load_logged_sharp_rows,
)
from lib.fourc_odds_client import fourc_odds_enabled, fourc_refresh_sec
from lib.sport_context import SPORT_CFB, SPORT_NFL, cache_sport, get_sport
from lib.styling import callout
from lib.team_logos import team_logo_url

TAB = "Sharp Lines"
LINE_TYPE_OPTIONS = ("Game Lines", "Player Props")


def _init_devig_state() -> None:
    st.session_state.setdefault("sh_devig_method", "mpto")
    st.session_state.setdefault("sh_devig_preset", "pinnacle")


@st.dialog("Select Devig Books", width="large")
def _devig_dialog() -> None:
    _init_devig_state()
    st.markdown("##### Method")
    cols = st.columns(5)
    for i, key in enumerate(DEVIG_METHODS.keys()):
        with cols[i % 5]:
            if st.button(f"{key.upper()} — {DEVIG_METHODS[key]}", key=f"sh_m_{key}", use_container_width=True):
                st.session_state["sh_devig_method"] = key
    preset = st.radio(
        "Devig books",
        list(DEVIG_BOOK_PRESETS.keys()),
        format_func=lambda k: DEVIG_BOOK_PRESETS[k]["label"],
        index=list(DEVIG_BOOK_PRESETS.keys()).index(st.session_state["sh_devig_preset"]),
        label_visibility="collapsed",
    )
    if st.button("Apply", type="primary", use_container_width=True):
        st.session_state["sh_devig_preset"] = preset
        st.rerun()


@st.cache_data(ttl=600, show_spinner=False)
def _scoreboard_context(sport: str, year: int, week: int) -> tuple[dict, dict, list, tuple]:
    _ = sport
    games = list_games_for_display_week(int(year), int(week), fbs_only=False)
    abbrs: dict[str, str] = {}
    logos: dict[str, str] = {}
    matchups: list[dict] = []
    for g in games:
        home = str(g.get("home") or "")
        away = str(g.get("away") or "")
        away_abbr = team_abbr(away)[:4].upper() if away else "AWAY"
        home_abbr = team_abbr(home)[:4].upper() if home else "HOME"
        away_logo = team_logo_url(away)
        home_logo = team_logo_url(home)
        abbrs[home] = home_abbr
        abbrs[away] = away_abbr
        if home:
            logos[home.lower()] = home_logo or ""
        if away:
            logos[away.lower()] = away_logo or ""
        matchups.append(
            {
                "away": away,
                "home": home,
                "away_abbr": away_abbr,
                "home_abbr": home_abbr,
                "away_logo": away_logo or "",
                "home_logo": home_logo or "",
            }
        )
    return abbrs, logos, matchups, tuple(matchups)


@st.cache_data(ttl=600, show_spinner=False)
def _game_board(
    sport: str,
    year: int,
    week: int,
    devig_method: str,
    devig_preset: str,
    cache_sig: str,
) -> list[dict]:
    _ = sport
    lines = load_multi_book_game_lines()
    lines = filter_lines_to_display_week(lines, int(year), int(week))
    return build_game_sharp_rows(
        lines,
        devig_method=devig_method,
        devig_preset=devig_preset,
        tab=TAB,
        year=int(year),
        week=int(week),
    )


@st.cache_data(ttl=600, show_spinner=False)
def _prop_board(
    sport: str,
    year: int,
    week: int,
    devig_method: str,
    devig_preset: str,
    cache_sig: str,
) -> list[dict]:
    _ = sport
    props = load_multi_book_props()
    props = filter_lines_to_display_week(props, int(year), int(week))
    return build_prop_sharp_rows(
        props,
        devig_method=devig_method,
        devig_preset=devig_preset,
    )


def _fmt_price(val) -> str:
    if val is None:
        return "—"
    try:
        n = int(float(str(val).replace("+", "").replace("−", "-")))
        return f"+{n}" if n > 0 else str(n)
    except (TypeError, ValueError):
        return str(val)


def _fmt_line(val, market_key: str | None = None) -> str:
    if val is None:
        return ""
    mk = str(market_key or "").lower()
    try:
        f = float(val)
        if _is_spread_key(mk):
            return f"{f:+.1f}"
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(val)


def _title_label(text: str) -> str:
    return str(text or "").strip().title()


def _result_badge(result: str | None) -> str:
    r = str(result or "").lower()
    if r == "hit":
        return '<span class="bo-result hit">HIT</span>'
    if r == "miss":
        return '<span class="bo-result miss">MISS</span>'
    if r == "push":
        return '<span class="bo-result push">PUSH</span>'
    return "—"


def _render_toolbar(*, matchups: list[dict], devig_label: str) -> tuple[str, str, str, str, float, bool, bool]:
    st.markdown('<div class="bo-sl-toolbar">', unsafe_allow_html=True)
    r1c1, r1c2, r1c3, r1c4, r1c5, r1c6, r1c7, r1c8, r1c9 = st.columns(
        [1.0, 0.9, 0.8, 1.0, 1.1, 1.0, 1.2, 0.9, 0.45]
    )
    with r1c1:
        st.markdown('<span class="bo-sl-filter-label">Type</span>', unsafe_allow_html=True)
        line_type = st.selectbox("Type", LINE_TYPE_OPTIONS, key="sh_line_type", label_visibility="collapsed")
    book_options = DISPLAY_PROP_BOOKS if line_type == "Player Props" else DISPLAY_SHARP_BOOKS
    market_options = PROP_MARKET_FILTER_OPTIONS if line_type == "Player Props" else MARKET_FILTER_OPTIONS
    with r1c2:
        st.markdown('<span class="bo-sl-filter-label">Book</span>', unsafe_allow_html=True)
        book_filter = st.selectbox(
            "Book",
            ["All"] + [lbl for _, lbl in book_options],
            key=f"sh_book_{line_type.replace(' ', '_').lower()}",
            label_visibility="collapsed",
        )
    with r1c3:
        st.markdown('<span class="bo-sl-filter-label">&nbsp;</span>', unsafe_allow_html=True)
        st.button("Exclude", key="sh_exclude", disabled=True, use_container_width=True)
    with r1c4:
        st.markdown('<span class="bo-sl-filter-label">Devig</span>', unsafe_allow_html=True)
        if st.button(devig_label, key="sh_devig_btn", use_container_width=True):
            _devig_dialog()
    with r1c5:
        st.markdown('<span class="bo-sl-filter-label">Market</span>', unsafe_allow_html=True)
        market_filter = st.selectbox(
            "Market",
            market_options,
            key=f"sh_market_{line_type.replace(' ', '_').lower()}",
            label_visibility="collapsed",
        )
    with r1c6:
        st.markdown('<span class="bo-sl-filter-label">Game</span>', unsafe_allow_html=True)
        matchup_names = ["All Games"] + [f"{m['away_abbr']} @ {m['home_abbr']}" for m in matchups[:30]]
        matchup_pick = st.selectbox("Game", matchup_names, key="sh_matchup", label_visibility="collapsed")
    with r1c7:
        st.markdown('<span class="bo-sl-filter-label">Min EV %</span>', unsafe_allow_html=True)
        min_ev = st.slider("Min EV %", 0.0, 40.0, 0.0, 1.0, key="sh_min_ev", label_visibility="collapsed")
    with r1c8:
        st.markdown('<span class="bo-sl-filter-label">&nbsp;</span>', unsafe_allow_html=True)
        edges_only = st.checkbox("+EV only", value=False, key="sh_edges_only")
    with r1c9:
        st.markdown('<span class="bo-sl-filter-label">&nbsp;</span>', unsafe_allow_html=True)
        refresh = st.button("↻", key="sh_refresh", help="Refresh odds from The Odds API", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    return line_type, book_filter, market_filter, matchup_pick, float(min_ev), bool(edges_only), bool(refresh)


def _render_grid(
    rows: list[dict],
    *,
    abbrs: dict,
    logos: dict,
    book_filter: str,
    show_results: bool = False,
) -> None:
    if not rows:
        return

    books = DISPLAY_SHARP_BOOKS
    if book_filter and book_filter != "All":
        books = [(b, l) for b, l in DISPLAY_SHARP_BOOKS if l == book_filter]

    head = [
        '<th class="bo-sl-th ev">Expected Value</th>',
        '<th class="bo-sl-th best">Best Book</th>',
        '<th class="bo-sl-th player">Game</th>',
        '<th class="bo-sl-th prop">Market</th>',
        '<th class="bo-sl-th prop">Pick</th>',
        '<th class="bo-sl-th fair">Fair Value</th>',
        '<th class="bo-sl-th imp">Implied</th>',
        '<th class="bo-sl-th kelly">¼ Kelly<span class="bo-sl-u">u</span></th>',
    ]
    if show_results:
        head.extend(['<th class="bo-sl-th">Actual</th>', '<th class="bo-sl-th">Result</th>'])
    for bid, lbl in books:
        head.append(f'<th class="bo-sl-th book">{book_header_html(bid, lbl)}</th>')

    body: list[str] = []
    for r in rows[:200]:
        ev = float(r.get("ev_pct") or 0)
        edge_pts = r.get("edge_points")
        ev_cls = "pos" if ev >= 0 else "neg"
        if ev != 0:
            ev_txt = f"+{ev:.1f}%" if ev >= 0 else f"{ev:.1f}%"
        elif edge_pts is not None and float(edge_pts) > 0:
            ev_txt = f"+{float(edge_pts):.1f}pt"
            ev_cls = "pos"
        else:
            ev_txt = f"{ev:.1f}%"

        best = r.get("best") or {}
        bid = str(best.get("book_id") or "")
        best_price = _fmt_price(best.get("price"))
        mk = str(r.get("market_key") or "")
        line_txt = _fmt_line(best.get("line") or r.get("line"), mk)

        away = str(r.get("away") or "")
        home = str(r.get("home") or "")
        away_abbr = team_abbr(away, abbrs)
        home_abbr = team_abbr(home, abbrs)
        away_logo = logos.get(away.lower(), "")
        home_logo = logos.get(home.lower(), "")
        away_img = (
            f'<img class="bo-sl-ologo" src="{html.escape(away_logo)}" alt="" loading="lazy"/>'
            if away_logo and away_logo.lower() != "nan"
            else ""
        )
        home_img = (
            f'<img class="bo-sl-ologo" src="{html.escape(home_logo)}" alt="" loading="lazy"/>'
            if home_logo and home_logo.lower() != "nan"
            else ""
        )
        game_html = (
            f'<div class="bo-sl-player-inner">{away_img}<span class="bo-sl-opp-txt">{html.escape(away_abbr)}</span>'
            f'<span class="bo-sl-opp-txt"> @ </span>{home_img}<span class="bo-sl-opp-txt">{html.escape(home_abbr)}</span></div>'
        )

        market = html.escape(_title_label(str(r.get("market") or "")))
        pick = html.escape(_title_label(str(r.get("pick") or "")))
        kelly = r.get("quarter_kelly")
        kelly_txt = f"{kelly:.2f}u" if kelly is not None else "—"

        cells = [
            f'<td class="bo-sl-ev {ev_cls}">{ev_txt}</td>',
            f'<td class="bo-sl-best"><div class="bo-sl-best-inner">{book_logo_img(bid, size=22)}'
            f'<span class="bo-sl-best-odds">{html.escape(best_price)}</span>'
            f'<span class="bo-sl-opp-txt"> {html.escape(line_txt)}</span></div></td>',
            f'<td class="bo-sl-player">{game_html}</td>',
            f'<td class="bo-sl-prop">{market}</td>',
            f'<td class="bo-sl-prop">{pick}</td>',
            f'<td class="bo-sl-fair">{html.escape(str(r.get("fair_price") or "—"))}</td>',
            f'<td class="bo-sl-imp">{r.get("implied_pct", "—")}%</td>',
            f'<td class="bo-sl-kelly">{kelly_txt}</td>',
        ]
        if show_results:
            actual = r.get("actual")
            actual_txt = "—" if actual is None else html.escape(f"{float(actual):g}")
            cells.extend(
                [
                    f'<td class="bo-sl-fair">{actual_txt}</td>',
                    f'<td class="bo-sl-result">{_result_badge(r.get("pick_result"))}</td>',
                ]
            )

        flags = r.get("cell_flags") or {}
        book_map = r.get("books") or {}
        for book_id, _ in books:
            q = book_map.get(book_id) or {}
            price = q.get("price")
            line = q.get("line")
            if not price:
                cells.append('<td class="bo-sl-cell empty">—</td>')
                continue
            bline = _fmt_line(line, mk)
            price_txt = _fmt_price(price)
            edge = flags.get(book_id, False)
            cls = "bo-sl-cell edge" if edge else "bo-sl-cell"
            line_html = (
                f'<span class="bo-sl-book-line">{html.escape(bline)}</span> '
                if bline
                else ""
            )
            cells.append(
                f'<td class="{cls}">{line_html}<span class="bo-sl-book-price">{html.escape(price_txt)}</span></td>'
            )

        body.append(f'<tr class="bo-sl-row">{"".join(cells)}</tr>')

    table = (
        f'<div class="bo-sl-scroll"><table class="bo-sl-table">'
        f'<thead><tr>{"".join(head)}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )
    st.markdown(table, unsafe_allow_html=True)


def _render_prop_grid(
    rows: list[dict],
    *,
    book_filter: str,
) -> None:
    if not rows:
        return

    books = DISPLAY_PROP_BOOKS
    if book_filter and book_filter != "All":
        books = [(b, l) for b, l in DISPLAY_PROP_BOOKS if l == book_filter]

    head = [
        '<th class="bo-sl-th ev">Expected Value</th>',
        '<th class="bo-sl-th best">Best Book</th>',
        '<th class="bo-sl-th player">Player</th>',
        '<th class="bo-sl-th prop">Prop</th>',
        '<th class="bo-sl-th prop">Pick</th>',
        '<th class="bo-sl-th fair">Fair Value</th>',
        '<th class="bo-sl-th imp">Implied</th>',
        '<th class="bo-sl-th kelly">¼ Kelly<span class="bo-sl-u">u</span></th>',
    ]
    for bid, lbl in books:
        head.append(f'<th class="bo-sl-th book">{book_header_html(bid, lbl)}</th>')

    body: list[str] = []
    for r in rows[:200]:
        ev = float(r.get("ev_pct") or 0)
        ev_cls = "pos" if ev >= 0 else "neg"
        ev_txt = f"+{ev:.1f}%" if ev >= 0 else f"{ev:.1f}%"

        best = r.get("best") or {}
        bid = str(best.get("book_id") or "")
        best_price = _fmt_price(best.get("price"))
        side = str(r.get("side") or "over")
        line_txt = _fmt_line(best.get("line") or r.get("line"))

        player = html.escape(str(r.get("player") or ""))
        position = html.escape(str(r.get("position") or ""))
        team_logo = str(r.get("teamLogo") or "").strip()
        logo_html = (
            f'<img class="bo-sl-plogo" src="{html.escape(team_logo)}" alt="" loading="lazy"/>'
            if team_logo and team_logo.lower() not in ("nan", "none")
            else '<span class="bo-sl-plogo-ph"></span>'
        )
        pos_html = f'<div class="bo-sl-pos">{position}</div>' if position else ""
        opp = str(r.get("opp") or "")
        opp_prefix = str(r.get("opp_prefix") or "@")
        opp_txt = ""
        if opp:
            opp_txt = f'<div class="bo-sl-pos">{html.escape(opp_prefix)} {html.escape(opp)}</div>'
        player_html = (
            f'<div class="bo-sl-player-inner">{logo_html}'
            f'<div class="bo-sl-player-meta"><div class="bo-sl-name">{player}</div>{pos_html}{opp_txt}</div></div>'
        )

        market = html.escape(_title_label(str(r.get("market") or r.get("propAbbr") or "")))
        pick = html.escape(_title_label(str(r.get("pick") or f"{side.title()} {line_txt}".strip())))
        kelly = r.get("quarter_kelly")
        kelly_txt = f"{kelly:.2f}u" if kelly is not None else "—"

        cells = [
            f'<td class="bo-sl-ev {ev_cls}">{ev_txt}</td>',
            f'<td class="bo-sl-best"><div class="bo-sl-best-inner">{book_logo_img(bid, size=22)}'
            f'<span class="bo-sl-best-odds">{html.escape(best_price)}</span>'
            f'<span class="bo-sl-opp-txt"> {html.escape(line_txt)}</span></div></td>',
            f'<td class="bo-sl-player">{player_html}</td>',
            f'<td class="bo-sl-prop">{market}</td>',
            f'<td class="bo-sl-prop">{pick}</td>',
            f'<td class="bo-sl-fair">{html.escape(str(r.get("fair_price") or "—"))}</td>',
            f'<td class="bo-sl-imp">{r.get("implied_pct", "—")}%</td>',
            f'<td class="bo-sl-kelly">{kelly_txt}</td>',
        ]

        flags = r.get("cell_flags") or {}
        book_map = r.get("books") or {}
        for book_id, _ in books:
            q = book_map.get(book_id) or {}
            price = q.get(f"{side}_price") or q.get("price")
            line = q.get("line")
            if not price:
                cells.append('<td class="bo-sl-cell empty">—</td>')
                continue
            bline = _fmt_line(line)
            price_txt = _fmt_price(price)
            edge = flags.get(book_id, False)
            cls = "bo-sl-cell edge" if edge else "bo-sl-cell"
            line_html = (
                f'<span class="bo-sl-book-line">{html.escape(bline)}</span> '
                if bline
                else ""
            )
            cells.append(
                f'<td class="{cls}">{line_html}<span class="bo-sl-book-price">{html.escape(price_txt)}</span></td>'
            )

        body.append(f'<tr class="bo-sl-row">{"".join(cells)}</tr>')

    table = (
        f'<div class="bo-sl-scroll"><table class="bo-sl-table">'
        f'<thead><tr>{"".join(head)}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )
    st.markdown(table, unsafe_allow_html=True)


def render() -> None:
    _init_devig_state()
    year, week = render_global_filters(prefix="sh")
    historical = props_have_results(int(year), int(week)) or display_week_complete(int(year), int(week))

    abbrs, logos, matchups, matchup_tuple = _scoreboard_context(cache_sport(), int(year), int(week))
    preset_lbl = DEVIG_BOOK_PRESETS.get(st.session_state["sh_devig_preset"], {}).get("label", "Pinnacle")

    line_type, book_filter, market_filter, matchup_pick, min_ev, edges_only, refresh = _render_toolbar(
        matchups=matchups,
        devig_label=f"Devig · {preset_lbl}",
    )
    props_mode = line_type == "Player Props"

    if refresh:
        _game_board.clear()
        _prop_board.clear()
        _scoreboard_context.clear()

    use_fourc = get_sport() in (SPORT_CFB, SPORT_NFL) and fourc_odds_enabled() and not props_mode
    can_fetch = not historical and (odds_api_key() or use_fourc)
    if can_fetch:
        if props_mode and (refresh or props_cache_needs_refresh(matchup_tuple)):
            label = "Refreshing player props…" if refresh else "Loading player props…"
            with st.spinner(label):
                ensure_multi_book_props(
                    matchup_tuple,
                    force=refresh,
                    tab=TAB,
                    year=int(year),
                    week=int(week),
                )
            _prop_board.clear()
        elif not props_mode and (refresh or lines_cache_needs_refresh(matchup_tuple, int(week))):
            label = "Refreshing game lines…" if refresh else "Loading game lines…"
            with st.spinner(label):
                ensure_multi_book_game_lines(matchup_tuple, force=refresh, display_week=int(week))
            _game_board.clear()

    devig_method = st.session_state["sh_devig_method"]
    devig_preset = st.session_state["sh_devig_preset"]

    if props_mode:
        cache_sig = str(int(props_cache_path().stat().st_mtime)) if props_cache_path().exists() else "none"
        rows_all = _prop_board(cache_sport(), int(year), int(week), devig_method, devig_preset, cache_sig)
    elif historical:
        rows_all = load_logged_sharp_rows(int(year), int(week))
        if not rows_all:
            rows_all = _game_board(cache_sport(), int(year), int(week), devig_method, devig_preset, "historical")
        rows_all = enrich_sharp_rows_with_grades(rows_all, year=int(year), week=int(week))
    else:
        cache_sig = str(int(lines_cache_path().stat().st_mtime)) if lines_cache_path().exists() else "none"
        if use_fourc:
            import time

            cache_sig = f"{cache_sig}-{int(time.time()) // max(15, fourc_refresh_sec())}"
        rows_all = _game_board(cache_sport(), int(year), int(week), devig_method, devig_preset, cache_sig)

    matchup_filter = "ALL"
    if matchup_pick != "All Games":
        names = ["All Games"] + [f"{m['away_abbr']} @ {m['home_abbr']}" for m in matchups[:30]]
        idx = names.index(matchup_pick) - 1
        if 0 <= idx < len(matchups):
            matchup_filter = matchups[idx]["home"]

    if props_mode:
        rows = filter_prop_rows(
            rows_all,
            matchup=matchup_filter,
            market=market_filter,
            book=book_filter,
            min_ev=min_ev,
            edges_only=edges_only,
        )
    else:
        rows = filter_game_rows(
            rows_all,
            matchup=matchup_filter,
            market=market_filter,
            book=book_filter,
            min_ev=min_ev,
            edges_only=edges_only,
        )

    age_fn = props_cache_age_minutes if props_mode else game_cache_age_minutes
    age = age_fn()
    age_txt = f" · cached {age}m ago" if age is not None and not historical else ""
    if use_fourc and not historical:
        age_txt = f" · live via 4C Odds · refresh ~{fourc_refresh_sec()}s"
    hist_txt = " · FINAL" if historical and not props_mode else ""
    kind_txt = "player props" if props_mode else "spreads/totals/derivatives"
    source_txt = " · 4codds.com" if use_fourc and not historical else ""
    st.markdown(
        f'<div class="bo-sl-meta">{len(rows)} lines{age_txt}{hist_txt} · {kind_txt}{source_txt} · green = best price/line vs market</div>',
        unsafe_allow_html=True,
    )

    if not rows:
        if rows_all:
            callout("No lines match the current filters.", "info")
        elif props_mode:
            callout("No player props available for this week's slate.", "info")
        else:
            callout("No game lines available for this week's slate.", "info")
        return

    if props_mode:
        _render_prop_grid(rows, book_filter=book_filter)
    else:
        _render_grid(rows, abbrs=abbrs, logos=logos, book_filter=book_filter, show_results=historical)
