"""Live games board + detail — screenshot-style cards, no market data."""
from __future__ import annotations

import html
import math
from typing import Any

from .display import team_abbr
from .prop_pricing import format_vigged_american
from .team_logos import logo_img_html


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def _format_win(val: float | None, *, mode: str = "pct") -> str:
    if val is None:
        return "—"
    if mode == "american":
        return format_vigged_american(float(val) / 100.0)
    return f"{float(val):.1f}%"


def _format_spread(val: Any) -> str:
    if val is None:
        return "—"
    try:
        f = float(val)
        if not math.isfinite(f):
            return "—"
        return f"{int(round(f)):+d}"
    except (TypeError, ValueError):
        return "—"


def _format_int(val: Any) -> str:
    if val is None:
        return "—"
    try:
        f = float(val)
        if not math.isfinite(f):
            return "—"
        return str(int(round(f)))
    except (TypeError, ValueError):
        return "—"


def _safe_score(val: Any) -> int:
    try:
        f = float(val if val is not None else 0)
        return int(f) if math.isfinite(f) else 0
    except (TypeError, ValueError):
        return 0


def _status_line(game: dict[str, Any], situation: dict[str, Any] | None) -> str:
    status = game.get("status") or {}
    state = status.get("state") or "pre"
    if state == "post" or status.get("completed"):
        return status.get("detail") or "Final"
    if state != "in":
        return status.get("detail") or "Scheduled"
    sit = situation or {}
    period = sit.get("period") or 1
    clock = sit.get("clock") or status.get("displayClock") or ""
    q = f"Q{period} {clock}".strip()
    dd = sit.get("downDistance") or ""
    poss = sit.get("possession") or ""
    tail = dd or poss
    return f"{q} · {tail}" if tail else q


def render_live_card(game: dict[str, Any], *, selected: bool = False, win_display: str = "pct") -> str:
    proj = game.get("projection") or {}
    away = game.get("away") or {}
    home = game.get("home") or {}
    status = game.get("status") or {}
    state = status.get("state") or "pre"
    is_live = state == "in"
    is_final = state == "post" or status.get("completed")

    cls = "bo-live-card"
    if is_live:
        cls += " bo-live-card-active"
    if is_final:
        cls += " bo-live-card-final"
    if selected:
        cls += " active"

    event_id = game.get("event_id") or ""

    away_logo = away.get("logo") or ""
    home_logo = home.get("logo") or ""
    away_abbr = _esc(away.get("abbr") or away.get("name", "")[:4])
    home_abbr = _esc(home.get("abbr") or home.get("name", "")[:4])

    ph = proj.get("projHome")
    pa = proj.get("projAway")
    hwp = proj.get("homeWinPct")
    awp = proj.get("awayWinPct")
    spread = proj.get("projSpread")
    total = proj.get("projTotal")
    lucky = proj.get("luckyTag")

    awp_html = (
        f'<div class="bo-live-wp">{_esc(_format_win(awp, mode=win_display))}</div>'
        if awp is not None
        else ""
    )
    hwp_html = (
        f'<div class="bo-live-wp">{_esc(_format_win(hwp, mode=win_display))}</div>'
        if hwp is not None
        else ""
    )
    lucky_html = f'<span class="bo-live-lucky">{_esc(lucky)}</span>' if lucky else ""

    status_txt = _esc(_status_line(game, game.get("situation")))
    live_badge = (
        '<span class="bo-live-dot"></span><span class="bo-live-badge">LIVE</span>'
        if is_live
        else ('<span class="bo-live-badge bo-live-badge-final">FINAL</span>' if is_final else f'<span class="bo-live-badge bo-live-badge-pre">{status_txt}</span>')
    )

    sub_status = f'<span class="bo-live-situation">{status_txt}</span>' if is_live else ""

    projected_final = ""
    if ph is not None and pa is not None:
        spread_txt = _format_spread(spread)
        total_txt = _format_int(total)
        projected_final = (
            f'<div class="bo-live-xfinal">'
            f'<span class="bo-live-xf-label">Projected Final</span>'
            f'<span class="bo-live-xf-scores">{away_abbr} {int(pa)} – {home_abbr} {int(ph)}</span>'
            f'<span class="bo-live-xf-meta">({spread_txt}, {total_txt})</span>'
            f"{lucky_html}"
            f"</div>"
        )

    return (
        f'<div class="{cls}">'
        f'<div class="bo-live-head">{live_badge}{sub_status}</div>'
        f'<div class="bo-live-scoreboard">'
        f'<div class="bo-live-team">'
        f'<img class="bo-live-logo" src="{_esc(away_logo)}" alt="" onerror="this.style.display=\'none\'"/>'
        f'<div class="bo-live-abbr">{away_abbr}</div>'
        f'<div class="bo-live-score">{_safe_score(away.get("score"))}</div>'
        f"{awp_html}"
        f"</div>"
        f'<div class="bo-live-team">'
        f'<img class="bo-live-logo" src="{_esc(home_logo)}" alt="" onerror="this.style.display=\'none\'"/>'
        f'<div class="bo-live-abbr">{home_abbr}</div>'
        f'<div class="bo-live-score">{_safe_score(home.get("score"))}</div>'
        f"{hwp_html}"
        f"</div>"
        f"</div>"
        f"{projected_final}"
        f"</div>"
    )


def render_live_board(
    games: list[dict[str, Any]], *, selected_id: str | None = None, win_display: str = "pct"
) -> str:
    if not games:
        return '<div class="bo-live-empty">No games on the board for this week.</div>'
    live = [g for g in games if (g.get("status") or {}).get("state") == "in"]
    upcoming = [g for g in games if (g.get("status") or {}).get("state") == "pre"]
    final = [g for g in games if (g.get("status") or {}).get("state") == "post" or (g.get("status") or {}).get("completed")]
    other = [g for g in games if g not in live and g not in upcoming and g not in final]
    ordered = live + upcoming + other + final
    cards = "".join(
        render_live_card(g, selected=(g.get("event_id") == selected_id), win_display=win_display)
        for g in ordered
    )
    return f'<div class="bo-live-grid">{cards}</div>'


def _metric_row(label: str, away_val: Any, home_val: Any) -> str:
    return (
        f'<tr><td class="bo-live-metric-label">{_esc(label)}</td>'
        f'<td class="bo-live-metric-away">{_esc(away_val)}</td>'
        f'<td class="bo-live-metric-home">{_esc(home_val)}</td></tr>'
    )


def _has_possession(team: dict[str, Any], situation: dict[str, Any]) -> bool:
    poss = str(situation.get("possession") or "").lower()
    if not poss:
        return False
    for key in ("abbr", "name"):
        token = str(team.get(key) or "").lower()
        if token and token in poss:
            return True
    return False


def _espn_scoreboard_hero(
    game: dict[str, Any],
    situation: dict[str, Any],
    proj: dict[str, Any],
    *,
    win_display: str,
) -> str:
    away = game.get("away") or {}
    home = game.get("home") or {}
    status = game.get("status") or {}
    state = status.get("state") or "pre"
    is_live = state == "in"
    is_final = state == "post" or status.get("completed")

    away_abbr = _esc(away.get("abbr") or away.get("name", "")[:4])
    home_abbr = _esc(home.get("abbr") or home.get("name", "")[:4])
    away_name = _esc(away.get("name") or away_abbr)
    home_name = _esc(home.get("name") or home_abbr)
    away_color = _esc(away.get("color") or "#1a1a22")
    home_color = _esc(home.get("color") or "#1a1a22")

    away_logo = logo_img_html(away.get("logo"), cls="bo-live-espn-logo", alt=away_abbr)
    home_logo = logo_img_html(home.get("logo"), cls="bo-live-espn-logo", alt=home_abbr)

    period = situation.get("period") or 1
    clock = situation.get("clock") or status.get("displayClock") or ""
    down_dist = situation.get("downDistance") or ""
    yard_line = situation.get("yardLine") or ""
    venue = _esc(game.get("venue") or "")

    if is_final:
        badge = '<span class="bo-live-espn-badge bo-live-espn-badge-final">FINAL</span>'
        clock_line = _esc(status.get("detail") or "Final")
        sit_line = ""
    elif is_live:
        badge = '<span class="bo-live-espn-badge bo-live-espn-badge-live"><span class="bo-live-dot"></span>LIVE</span>'
        clock_line = _esc(f"Q{period} {clock}".strip())
        sit_bits = [down_dist, str(yard_line) if yard_line else ""]
        sit_line = _esc(" · ".join(x for x in sit_bits if x))
    else:
        badge = '<span class="bo-live-espn-badge bo-live-espn-badge-pre">SCHEDULED</span>'
        clock_line = _esc(status.get("detail") or "Scheduled")
        sit_line = ""

    away_wp = proj.get("awayWinPct")
    home_wp = proj.get("homeWinPct")
    away_win = _esc(_format_win(away_wp, mode=win_display))
    home_win = _esc(_format_win(home_wp, mode=win_display))

    away_ball = " bo-live-espn-has-ball" if _has_possession(away, situation) else ""
    home_ball = " bo-live-espn-has-ball" if _has_possession(home, situation) else ""

    wp_bar = ""
    if away_wp is not None and home_wp is not None and win_display == "pct":
        aw = max(0.0, min(100.0, float(away_wp)))
        hw = max(0.0, min(100.0, float(home_wp)))
        wp_bar = (
            f'<div class="bo-live-espn-wpbar">'
            f'<div class="bo-live-espn-wpaway" style="width:{aw:.1f}%;background:{away_color}"></div>'
            f'<div class="bo-live-espn-wphome" style="width:{hw:.1f}%;background:{home_color}"></div>'
            f"</div>"
        )

    sit_html = f'<span class="bo-live-espn-sit">{sit_line}</span>' if sit_line else ""
    venue_html = f'<span class="bo-live-espn-venue">{venue}</span>' if venue else ""

    return (
        f'<div class="bo-live-espn-bar">'
        f'<div class="bo-live-espn-stripe bo-live-espn-stripe-away" style="background:{away_color}"></div>'
        f'<div class="bo-live-espn-body">'
        f'<div class="bo-live-espn-top">'
        f"{badge}"
        f'<span class="bo-live-espn-clock">{clock_line}</span>'
        f"{sit_html}"
        f"{venue_html}"
        f"</div>"
        f'<div class="bo-live-espn-matchup">'
        f'<div class="bo-live-espn-side bo-live-espn-away{away_ball}">'
        f'<div class="bo-live-espn-side-main">'
        f"{away_logo}"
        f'<div class="bo-live-espn-teammeta">'
        f'<div class="bo-live-espn-abbr">{away_abbr}</div>'
        f'<div class="bo-live-espn-full">{away_name}</div>'
        f"</div>"
        f'<div class="bo-live-espn-score">{_safe_score(away.get("score"))}</div>'
        f"</div>"
        f'<div class="bo-live-espn-win">{away_win}</div>'
        f"</div>"
        f'<div class="bo-live-espn-mid">'
        f'<div class="bo-live-espn-at">@</div>'
        f"{wp_bar}"
        f"</div>"
        f'<div class="bo-live-espn-side bo-live-espn-home{home_ball}">'
        f'<div class="bo-live-espn-side-main">'
        f'<div class="bo-live-espn-score">{_safe_score(home.get("score"))}</div>'
        f'<div class="bo-live-espn-teammeta">'
        f'<div class="bo-live-espn-abbr">{home_abbr}</div>'
        f'<div class="bo-live-espn-full">{home_name}</div>'
        f"</div>"
        f"{home_logo}"
        f"</div>"
        f'<div class="bo-live-espn-win">{home_win}</div>'
        f"</div>"
        f"</div>"
        f"</div>"
        f'<div class="bo-live-espn-stripe bo-live-espn-stripe-home" style="background:{home_color}"></div>'
        f"</div>"
    )


def render_live_detail(detail: dict[str, Any], *, win_display: str = "pct") -> str:
    game = detail.get("game") or {}
    proj = detail.get("projection") or {}
    away = game.get("away") or {}
    home = game.get("home") or {}
    situation = detail.get("situation") or {}
    plays = detail.get("plays") or []
    pre = detail.get("pregame") or {}

    away_abbr = _esc(away.get("abbr") or "AWY")
    home_abbr = _esc(home.get("abbr") or "HOM")
    away_logo = logo_img_html(away.get("logo"), cls="bo-live-metric-logo", alt=away_abbr)
    home_logo = logo_img_html(home.get("logo"), cls="bo-live-metric-logo", alt=home_abbr)

    hero = _espn_scoreboard_hero(game, situation, proj, win_display=win_display)

    win_label = "Price to win" if win_display == "american" else "Win %"
    metrics = (
        f'<table class="bo-live-metrics">'
        f"<thead><tr><th></th>"
        f'<th class="bo-live-metric-head"><span class="bo-live-metric-head-inner">{away_logo}{away_abbr}</span></th>'
        f'<th class="bo-live-metric-head"><span class="bo-live-metric-head-inner">{home_logo}{home_abbr}</span></th>'
        f"</tr></thead><tbody>"
        + _metric_row(
            win_label,
            _format_win(proj.get("awayWinPct"), mode=win_display),
            _format_win(proj.get("homeWinPct"), mode=win_display),
        )
        + _metric_row("Projected Final", _format_int(proj.get("projAway")), _format_int(proj.get("projHome")))
        + _metric_row(
            "Proj spread (home)",
            "—",
            _format_spread(proj.get("projSpread")) if proj.get("projSpread") is not None else "—",
        )
        + _metric_row(
            "Projected Total",
            _format_int(proj.get("projTotal")),
            _format_int(proj.get("projTotal")),
        )
        + _metric_row("Team total (proj)", _format_int(proj.get("projAway")), _format_int(proj.get("projHome")))
        + _metric_row("Current Yards", _format_int(proj.get("curAwayYards")), _format_int(proj.get("curHomeYards")))
        + _metric_row(
            "Projected Yards",
            _format_int(proj.get("projAwayYards")),
            _format_int(proj.get("projHomeYards")),
        )
        + _metric_row(
            "Current Passing Yards",
            _format_int(proj.get("curAwayPassYards")),
            _format_int(proj.get("curHomePassYards")),
        )
        + _metric_row(
            "Projected Passing Yards",
            _format_int(proj.get("projAwayPassYards")),
            _format_int(proj.get("projHomePassYards")),
        )
        + _metric_row(
            "Current Rushing Yards",
            _format_int(proj.get("curAwayRushYards")),
            _format_int(proj.get("curHomeRushYards")),
        )
        + _metric_row(
            "Projected Rushing Yards",
            _format_int(proj.get("projAwayRushYards")),
            _format_int(proj.get("projHomeRushYards")),
        )
        + _metric_row("Pregame spread (home)", "—", pre.get("spread", "—"))
        + _metric_row("Pregame total", pre.get("total", "—"), pre.get("total", "—"))
        + "</tbody></table>"
    )

    play_rows = []
    for p in reversed(plays[-40:]):
        period = p.get("period")
        clock = p.get("clock") or ""
        text = p.get("text") or ""
        score_badge = ""
        if p.get("scoringPlay"):
            score_badge = '<span class="bo-live-pbp-score">SCORE</span>'
        play_rows.append(
            f'<div class="bo-live-pbp-row">'
            f'<span class="bo-live-pbp-time">Q{period} { _esc(clock)}</span>'
            f'<span class="bo-live-pbp-text">{_esc(text)}</span>{score_badge}'
            f"</div>"
        )
    empty_pbp = "<div class='bo-live-empty'>Plays will appear once the game starts.</div>"
    pbp = (
        f'<div class="bo-live-pbp-wrap"><div class="bo-live-pbp-head">Play-by-play</div>'
        f'{"".join(play_rows) if play_rows else empty_pbp}'
        f"</div>"
    )

    return (
        f'<div class="bo-live-detail">'
        f"{hero}"
        f'<div class="bo-live-detail-grid">'
        f'<div class="bo-live-detail-panel">{metrics}</div>'
        f'<div class="bo-live-detail-panel bo-live-pbp-panel">{pbp}</div>'
        f"</div></div>"
    )
