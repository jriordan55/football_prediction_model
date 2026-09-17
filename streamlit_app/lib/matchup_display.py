"""Reference-style matchup detail HTML for Streamlit."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any

import pandas as pd

from lib.matchup_prop_enrich import format_prop_projection


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _logo(url: str | None, *, size: str = "md") -> str:
    from lib.team_logos import logo_img_html

    cls = f"bo-mu-logo bo-mu-logo-{size}"
    return logo_img_html(url, cls=cls)


def _fmt_date(iso: Any) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(iso)[:10]


def _fmt_spread_pill(line: Any, home: str, away: str, *, year: int = 2026) -> str:
    from lib.display import team_abbr

    home_lbl = team_abbr(home, year=year)
    away_lbl = team_abbr(away, year=year)
    if line is None:
        return "—"
    try:
        n = float(line)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return f"{home_lbl} {n:g}"
    if n > 0:
        return f"{away_lbl} {-n:g}"
    return "PK"


def _fmt_open_close_spread_pill(open_line: Any, close_line: Any, home: str, away: str, *, year: int = 2026) -> str:
    open_txt = _fmt_spread_pill(open_line, home, away, year=year)
    close_txt = _fmt_spread_pill(close_line, home, away, year=year)
    if open_line is not None and close_line is not None:
        return f"Open {open_txt} → Close {close_txt}"
    if close_line is not None:
        return f"Close {close_txt}"
    if open_line is not None:
        return f"Open {open_txt}"
    return "—"


def _fmt_open_close_total_pill(open_total: Any, close_total: Any) -> str:
    def _one(val: Any) -> str:
        if val is None:
            return "—"
        try:
            return f"{float(val):g}"
        except (TypeError, ValueError):
            return str(val)

    open_txt = _one(open_total)
    close_txt = _one(close_total)
    if open_total is not None and close_total is not None:
        return f"O/U · Open {open_txt} → Close {close_txt}"
    if close_total is not None:
        return f"O/U · Close {close_txt}"
    if open_total is not None:
        return f"O/U · Open {open_txt}"
    return "—"


def _rank_class(rank_str: str) -> str:
    try:
        n = int(str(rank_str).lstrip("#"))
        if n <= 40:
            return "rank-good"
        if n >= 100:
            return "rank-bad"
    except (TypeError, ValueError):
        pass
    return "rank-mid"


def _injury_badge(status: str) -> tuple[str, str]:
    s = (status or "").upper()
    if any(x in s for x in ("OUT", "IR", "INJURED", "SUSPEND", "SEASON")):
        return "OUT SEASON" if "SEASON" in s else "OUT", "bo-inj-badge-out"
    if "DOUBT" in s:
        return "DOUBTFUL", "bo-inj-badge-doubt"
    if "QUESTION" in s or "DAY" in s:
        return "QUESTIONABLE", "bo-inj-badge-question"
    return s[:16], "bo-inj-badge-listed"


def _grade_class(grade: str) -> str:
    g = str(grade or "")
    if "/" in g:
        try:
            num = int(g.split("/")[0])
            if num >= 7:
                return "grade-high"
            if num >= 5:
                return "grade-mid"
        except (TypeError, ValueError, IndexError):
            pass
    if g.startswith("A"):
        return "grade-high"
    if g.startswith("B"):
        return "grade-mid"
    return "grade-low"


def _render_bar(label: str, pct: int | None, rank: int | None, *, accent: str) -> str:
    if pct is None:
        pct_txt = "—"
        width = 0
    else:
        pct_txt = f"{max(0, min(100, int(pct)))}%"
        width = max(0, min(100, int(pct)))
    rank_txt = f"#{rank}" if rank else "—"
    return (
        f'<div class="bo-mu-bar-row">'
        f'<div class="bo-mu-bar-head"><span>{_esc(label)}</span>'
        f'<span class="bo-mu-bar-pct">{_esc(pct_txt)}</span>'
        f'<span class="bo-mu-bar-rank">{_esc(rank_txt)}</span></div>'
        f'<div class="bo-mu-bar-track">'
        f'<div class="bo-mu-bar-fill" style="width:{width}%;background:linear-gradient(90deg,{accent}55,{accent})"></div>'
        f"</div></div>"
    )


def _render_profile(side: dict[str, Any], injuries: list[dict[str, Any]], *, accent: str) -> str:
    sp = side.get("sp") or {}
    conf = side.get("conference") or ""
    mascot = side.get("mascot") or ""
    rank_line = f"#{sp.get('rank')} of {sp.get('rankTotal')} on our board" if sp.get("rank") else ""
    sub = " · ".join(x for x in [conf, rank_line] if x)

    bars = ""
    if sp:
        overall_pct = max(
            0,
            min(
                100,
                int(
                    ((sp.get("rankTotal", 138) - (sp.get("rank") or 138) + 1) / max(sp.get("rankTotal", 138), 1)) * 100
                ),
            ),
        )
        bars = (
            _render_bar("Offense", sp.get("offPct"), sp.get("offRank"), accent=accent)
            + _render_bar("Defense", sp.get("defPct"), sp.get("defRank"), accent=accent)
            + _render_bar("Overall", overall_pct, sp.get("rank"), accent=accent)
        )

    coach = side.get("coach") or {}
    coach_html = ""
    if coach.get("name") or side.get("tempo"):
        tags = "".join(f'<span class="bo-mu-tag">{_esc(t)}</span>' for t in (coach.get("tags") or []))
        tempo = side.get("tempo") or {}
        tempo_txt = ""
        if tempo.get("secondsPerPlay"):
            tempo_txt = f'{tempo["secondsPerPlay"]:.1f}s / play'
        elif tempo.get("playsPerGame"):
            tempo_txt = f'{tempo["playsPerGame"]:.1f} plays/game · #{tempo.get("playsRank") or "—"} tempo'
        name_html = f"<strong>{_esc(coach.get('name'))}</strong>" if coach.get("name") else ""
        tags_html = f'<div class="bo-mu-tags">{tags}</div>' if tags else ""
        tempo_html = f'<div class="bo-mu-tempo">{_esc(tempo_txt)}</div>' if tempo_txt else ""
        coach_html = f'<div class="bo-mu-coach">{name_html}{tags_html}{tempo_html}</div>'

    starters = side.get("_starters") or []
    starter_cells = []
    for s in starters[:11]:
        pos = s.get("label") or s.get("position") or "—"
        starter_cells.append(
            f'<div class="bo-mu-starter-cell">'
            f'<span class="bo-mu-starter-pos">{_esc(pos)}</span> '
            f'<span class="bo-mu-starter-name">{_esc(s.get("name"))}</span></div>'
        )
    starter_grid = "".join(starter_cells) if starter_cells else '<span class="bo-mu-muted">Projected starters unavailable</span>'

    inj_html = ""
    for inj in injuries[:3]:
        badge, cls = _injury_badge(str(inj.get("status", "")))
        inj_html += (
            f'<div class="bo-mu-inj">'
            f'<span class="bo-inj-badge {cls}">{_esc(badge)}</span> '
            f'<span>{_esc(inj.get("player"))} ({_esc(inj.get("position"))})</span></div>'
        )

    return (
        f'<article class="bo-mu-profile" style="--team-accent:{accent}">'
        f'<div class="bo-mu-profile-head">{_logo(side.get("logo"), size="lg")}'
        f'<div><h3>{_esc(side.get("team"))}</h3>'
        f'<p>{_esc(sub)}</p>'
        f'{f"<p class=bo-mu-mascot>{_esc(mascot)}</p>" if mascot else ""}'
        f"</div></div>"
        f'{bars}{coach_html}'
        f'<div class="bo-mu-starters"><span class="bo-mu-label">Projected starters</span>'
        f'<div class="bo-mu-starter-grid">{starter_grid}</div></div>'
        f'{f"<div class=bo-mu-injuries><span class=bo-mu-label>Injuries</span>{inj_html}</div>" if inj_html else ""}'
        f"</article>"
    )


def _result_badge(result: str | None) -> str:
    r = str(result or "").lower()
    if r == "hit":
        return '<span class="bo-result hit">HIT</span>'
    if r == "miss":
        return '<span class="bo-result miss">MISS</span>'
    if r == "push":
        return '<span class="bo-result push">PUSH</span>'
    return "—"


def _format_expected_roi(row: dict[str, Any]) -> tuple[str, str]:
    try:
        roi = float(row.get("expected_roi") if row.get("expected_roi") is not None else row.get("roi"))
        txt = f"{roi * 100:+.1f}%"
        if roi > 0.005:
            cls = "edge-pos"
        elif roi < -0.005:
            cls = "edge-neg"
        else:
            cls = ""
        return txt, cls
    except (TypeError, ValueError):
        return "—", ""


def _render_props_table(
    props: list[dict[str, Any]], headshots: dict[str, str] | None = None, *, historical: bool = False
) -> str:
    if not props:
        empty = "No pre-game prop lines archived for this matchup." if historical else "No Expected ROI props for this game right now."
        return f'<div class="bo-mu-empty">{empty}</div>'

    seen: set[tuple] = set()
    unique_props: list[dict[str, Any]] = []
    for r in props:
        key = (r.get("player"), r.get("prop"), r.get("line"))
        if key in seen:
            continue
        seen.add(key)
        unique_props.append(r)

    shots = headshots or {}
    rows = []
    for r in unique_props[:25]:
        edge = r.get("edgePct")
        try:
            edge_f = float(edge)
            edge_txt = f"{edge_f:+.1f}%"
            edge_cls = "edge-pos" if edge_f >= 0 else "edge-neg"
        except (TypeError, ValueError):
            edge_txt, edge_cls = "—", ""

        side = str(r.get("side") or "").upper()
        if "OVER" in side:
            side_cls = "side-over"
        elif "UNDER" in side:
            side_cls = "side-under"
        elif "YES" in side:
            side_cls = "side-yes"
        else:
            side_cls = ""

        grade = str(r.get("grade") or "—")
        player = str(r.get("player") or "")
        avatar = shots.get(player.lower()) or r.get("teamLogo")
        avatar_html = _logo(avatar, size="prop") if avatar else '<div class="bo-mu-prop-avatar bo-logo-fallback"></div>'
        line_txt = f" {_esc(r.get('line'))}" if r.get("line") is not None else ""
        prop_txt = f"{_esc(r.get('prop'))}{line_txt}"

        row_cls = "play-row" if r.get("play") else ""
        if r.get("result") == "hit":
            row_cls += " result-hit"
        elif r.get("result") == "miss":
            row_cls += " result-miss"

        actual_col = f'<td class="mono">{_esc(r.get("actual"))}</td>' if historical else ""
        result_col = f"<td>{_result_badge(r.get('result'))}</td>" if historical else ""

        roi_txt, roi_cls = _format_expected_roi(r)

        rows.append(
            f'<tr class="{row_cls.strip()}">'
            + (
                f'<td class="grade"><span class="bo-grade-badge {_grade_class(grade)}">{_esc(grade)}</span></td>'
                if historical
                else ""
            )
            + f'<td class="player-cell">'
            f'<div class="bo-mu-prop-player">{avatar_html}'
            f'<div><div class="bo-mu-prop-name">{_esc(player or "—")}</div>'
            f'<div class="pos">{_esc(r.get("position"))} · {_esc(r.get("team"))}</div></div></div></td>'
            f'<td class="bo-mu-prop-market">{prop_txt}</td>'
            f'<td class="mono bo-mu-prop-mine">{_esc(format_prop_projection(r))}</td>'
            f'<td class="mono bo-mu-prop-roi {roi_cls}">{_esc(roi_txt)}</td>'
            f"{actual_col}{result_col}"
            + (
                f'<td class="{edge_cls}">{_esc(edge_txt)}</td>'
                f'<td class="{side_cls}">{_esc(side or "—")}</td>'
                if historical
                else ""
            )
            + f"</tr>"
        )

    if historical:
        hist_cols = "<th>Actual</th><th>Result</th>"
        head = "<th>Grade</th><th>Player</th><th>Prop</th><th>Mine</th><th>Expected ROI</th>" + hist_cols + "<th>Edge%</th><th>Pick</th>"
        table_open = (
            f'<div class="bo-props-table-wrap bo-mu-props-wrap bo-mu-props-hist">'
            f'<table class="bo-props-table bo-mu-props-simple">'
        )
    else:
        head = "<th>Player</th><th>Prop</th><th>Mine</th><th>Expected ROI</th>"
        table_open = (
            f'<div class="bo-props-table-wrap bo-mu-props-wrap">'
            f'<table class="bo-props-table bo-mu-props-simple">'
            f'<colgroup><col class="mu-col-player" /><col class="mu-col-prop" /><col class="mu-col-mine" /><col class="mu-col-roi" /></colgroup>'
        )

    return (
        f"{table_open}"
        f"<thead><tr>{head}</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _render_advanced(adv: dict[str, Any], away: str, home: str, away_logo: str | None, home_logo: str | None) -> str:
    off = adv.get("offense") or []
    deff = adv.get("defense") or []
    if not off:
        return ""

    def table(title: str, metrics: list[dict[str, Any]]) -> str:
        body = []
        for m in metrics:
            ar, hr = m.get("away", {}), m.get("home", {})
            body.append(
                f"<tr><td class='metric'>{_esc(m.get('label'))}</td>"
                f"<td class='val'><span class='{_rank_class(str(ar.get('rank')))}'>{_esc(ar.get('value'))}</span> "
                f"<span class='rank'>{_esc(ar.get('rank'))}</span></td>"
                f"<td class='val'><span class='{_rank_class(str(hr.get('rank')))}'>{_esc(hr.get('value'))}</span> "
                f"<span class='rank'>{_esc(hr.get('rank'))}</span></td></tr>"
            )
        return (
            f'<div class="bo-advanced-panel"><h3>{_esc(title)}</h3>'
            f'<table class="bo-advanced-table"><thead><tr><th>Stat</th>'
            f"<th>{_logo(away_logo, size='xs')}{_esc(away)}</th>"
            f"<th>{_logo(home_logo, size='xs')}{_esc(home)}</th></tr></thead>"
            f'<tbody>{"".join(body)}</tbody></table></div>'
        )

    yr = adv.get("sdvsYear") or ""
    wk = adv.get("sdvsWeek") or "—"
    return (
        f'<div class="bo-mu-advanced-head"><h2 class="bo-section-title">Advanced profile · side by side</h2>'
        f'<span class="bo-mu-muted">{yr} season · through week {wk}</span></div>'
        f'<div class="bo-advanced-grid">{table(f"Offense · {yr}", off)}{table(f"Defense (what they allow) · {yr}", deff)}</div>'
    )


def _injuries_for_team(inj_df: pd.DataFrame, team: str, abbr: str | None = None) -> list[dict]:
    if inj_df.empty or not team:
        return []
    from lib.sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        from lib.nfl_team_registry import resolve_canonical, teams_match
    else:
        from lib.team_registry import resolve_canonical, teams_match

    canon = resolve_canonical(team) or team
    ab = str(abbr or "").strip().upper()

    def _matches(row: pd.Series) -> bool:
        rteam = str(row.get("team") or "")
        rabbr = str(row.get("team_abbr") or "").strip().upper()
        if teams_match(rteam, canon) or teams_match(rteam, team):
            return True
        if ab and rabbr and ab == rabbr:
            return True
        return False

    matched = inj_df.loc[inj_df.apply(_matches, axis=1)]
    return matched.head(8).to_dict("records")


def render_matchup_detail(detail: dict[str, Any], injuries: pd.DataFrame | None = None) -> None:
    import streamlit as st

    from lib.game_status import game_is_final

    meta = detail.get("meta") or {}
    logos = detail.get("logos") or {}
    proj = detail.get("projectedScore") or {}
    lines = detail.get("marketLines") or {}
    home = str(meta.get("home") or "")
    away = str(meta.get("away") or "")
    season_year = int(meta.get("year") or 2026)

    actual = detail.get("actualScore") or {}
    is_final = game_is_final(
        completed=bool(meta.get("final")),
        home_pts=actual.get("homeScore"),
        away_pts=actual.get("awayScore"),
        start_date=meta.get("startDate"),
    )
    props_historical = bool(meta.get("historical")) or is_final

    away_score = proj.get("awayScore")
    home_score = proj.get("homeScore")
    try:
        from lib.sp_projections import align_team_scores

        spread_hint = proj.get("spread") if proj.get("spread") is not None else lines.get("spread")
        total_hint = proj.get("total") if proj.get("total") is not None else lines.get("total")
        home_score, away_score = align_team_scores(
            home_score,
            away_score,
            spread=spread_hint,
            total=total_hint,
        )
    except (TypeError, ValueError, ImportError):
        pass
    try:
        away_proj = f"{float(away_score):.1f}" if away_score is not None else "—"
        home_proj = f"{float(home_score):.1f}" if home_score is not None else "—"
    except (TypeError, ValueError):
        away_proj = home_proj = "—"

    if is_final:
        try:
            away_final = str(int(actual.get("awayScore"))) if actual.get("awayScore") is not None else "—"
            home_final = str(int(actual.get("homeScore"))) if actual.get("homeScore") is not None else "—"
        except (TypeError, ValueError):
            away_final = home_final = "—"
        score_block = (
            f'<div class="bo-mu-scores bo-mu-scores-final">'
            f'<div class="bo-mu-score-stack"><span class="bo-mu-score-label">Final</span>'
            f'<span class="bo-mu-score-box final">{away_final}</span>'
            f'<span class="bo-mu-proj-pill">Proj {away_proj}</span></div>'
            f'<span class="bo-mu-vs">vs</span>'
            f'<div class="bo-mu-score-stack"><span class="bo-mu-score-label">Final</span>'
            f'<span class="bo-mu-score-box final">{home_final}</span>'
            f'<span class="bo-mu-proj-pill">Proj {home_proj}</span></div></div>'
        )
        hero_title = "Scoreboard · Final"
        week_label = f"Week {meta.get('week')}" if meta.get("week") is not None else "Week 0"
    else:
        score_block = (
            f'<div class="bo-mu-scores">'
            f'<span class="bo-mu-score-box">{away_proj}</span>'
            f'<span class="bo-mu-vs">vs</span>'
            f'<span class="bo-mu-score-box">{home_proj}</span></div>'
        )
        hero_title = "Scoreboard"
        week_label = f"Week {meta.get('week')}"

    if is_final:
        spread_pill = _fmt_open_close_spread_pill(
            lines.get("openSpread"),
            lines.get("closeSpread") or lines.get("spread"),
            home,
            away,
            year=season_year,
        )
        total_pill = _fmt_open_close_total_pill(lines.get("openTotal"), lines.get("closeTotal") or lines.get("total"))
    else:
        spread_pill = _fmt_spread_pill(lines.get("spread"), home, away, year=season_year)
        total_pill = f"O/U · {lines.get('total')}" if lines.get("total") is not None else "—"

    inj_df = injuries if injuries is not None else pd.DataFrame()
    home_inj = _injuries_for_team(inj_df, home, meta.get("homeAbbr"))
    away_inj = _injuries_for_team(inj_df, away, meta.get("awayAbbr"))

    profiles = detail.get("teamProfiles") or {}
    home_p = dict(profiles.get("home") or {})
    away_p = dict(profiles.get("away") or {})
    starters = detail.get("starters") or {}
    away_p["_starters"] = (starters.get("away") or {}).get("starters") or []
    home_p["_starters"] = (starters.get("home") or {}).get("starters") or []

    away_color = (detail.get("teamColors") or {}).get("away") or "#56a0d3"
    from lib.sport_context import get_sport, get_sport_config

    _accent = str(get_sport_config(get_sport())["theme"]["accent_deep"])
    home_color = (detail.get("teamColors") or {}).get("home") or _accent

    away_mascot = away_p.get("mascot") or ""
    home_mascot = home_p.get("mascot") or ""

    headshots: dict[str, str] = {}
    if not inj_df.empty and "headshot" in inj_df.columns:
        for _, row in inj_df.iterrows():
            name = str(row.get("player") or "").strip().lower()
            shot = str(row.get("headshot") or "").strip()
            if name and shot:
                headshots[name] = shot

    hero = (
        f'<section class="bo-mu-hero{" bo-mu-hero-final" if is_final else ""}">'
        f'<div class="bo-mu-hero-title">{hero_title}</div>'
        f'<div class="bo-mu-scoreboard">'
        f'<div class="bo-mu-team-col away">{_logo(logos.get("away"), size="xl")}'
        f'<div class="bo-mu-team-name">{_esc(away)}</div>'
        f'{f"<div class=bo-mu-mascot>{_esc(away_mascot)}</div>" if away_mascot else ""}'
        f"</div>"
        f'<div class="bo-mu-score-center">'
        f"{score_block}"
        f'<div class="bo-mu-meta">{_esc(meta.get("venue") or "")} · {_esc(week_label)} · {_esc(_fmt_date(meta.get("startDate")))}</div>'
        f'<div class="bo-pill-row">'
        f'<span class="bo-pill">{_esc(spread_pill)}</span>'
        f'<span class="bo-pill">{_esc(total_pill)}</span></div></div>'
        f'<div class="bo-mu-team-col home">{_logo(logos.get("home"), size="xl")}'
        f'<div class="bo-mu-team-name">{_esc(home)}</div>'
        f'{f"<div class=bo-mu-mascot>{_esc(home_mascot)}</div>" if home_mascot else ""}'
        f"</div></div></section>"
    )

    from lib.weather_display import render_game_weather_html

    weather_html = render_game_weather_html(detail, season_year=season_year, tab="Game Detail")

    main = (
        f'<div class="bo-mu-layout">'
        f'<div class="bo-mu-main">'
        f'<div class="bo-profile-grid">'
        f'{_render_profile(away_p, away_inj, accent=away_color)}'
        f'{_render_profile(home_p, home_inj, accent=home_color)}'
        f"</div>"
        f"{weather_html}"
        f'{_render_advanced(detail.get("advancedProfile") or {}, away, home, logos.get("away"), logos.get("home"))}'
        f"</div>"
        f'<div class="bo-mu-aside">'
        f'<h2 class="bo-section-title">{"+Expected ROI props · pre-game vs actual" if props_historical else "+Expected ROI props"}</h2>'
        f'{_render_props_table(detail.get("plusEvProps") or [], headshots, historical=props_historical)}'
        f"</div></div>"
    )

    st.markdown(f'<div class="bo-mu-page">{hero}{main}</div>', unsafe_allow_html=True)
