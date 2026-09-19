"""In-play situation — down/distance, field, possession, score margin → pace adjustments."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

# Median form / sim-anchored derivative blend (pregame props track parent MC means).
PREGAME_PACE_WEIGHT = 0.35
PREGAME_SIM_WEIGHT = 0.65
YARDS_PER_COMPLETION = 11.0

from lib.live_projections import _garbage_multipliers


def _clock_sec(clock: str | None) -> int | None:
    if not clock:
        return None
    m = re.match(r"(\d+):(\d+)", str(clock).strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


@dataclass
class PlaySituation:
    period: int
    clock: str | None
    clock_seconds: int | None
    down: int | None
    distance: int | None
    yard_line: int | None
    possession_text: str | None
    offense_side: str | None  # "home" | "away" | None
    home_score: int
    away_score: int
    score_margin: int  # home - away
    in_red_zone: bool
    is_goal_to_go: bool


def _parse_yard_line(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    m = re.search(r"\d+", text)
    if not m:
        return None
    try:
        return int(m.group())
    except ValueError:
        return None


def _offense_side(possession: str | None, home: str, away: str) -> str | None:
    if not possession:
        return None
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    text = str(possession)
    for name, side in ((home, "home"), (away, "away")):
        if cfb_match(text, name) or nfl_match(text, name):
            return side
    # Abbreviation at start e.g. "BUF 35"
    token = text.split()[0] if text.split() else ""
    if token and len(token) <= 4:
        for name, side in ((home, "home"), (away, "away")):
            parts = str(name).split()
            abbr = "".join(p[0] for p in parts if p)[:3].upper()
            if token.upper() == abbr.upper():
                return side
    return None


def parse_play_situation(
    play: dict[str, Any],
    situation: dict[str, Any],
    *,
    home: str,
    away: str,
) -> PlaySituation:
    period = int(play.get("period") or situation.get("period") or 1)
    clock = play.get("clock") or situation.get("clock")
    clock_seconds = _clock_sec(clock) if clock else situation.get("clockSeconds")
    down = play.get("down") or _down_from_text(situation.get("downDistance"))
    distance = play.get("distance") or _distance_from_text(situation.get("downDistance"))
    yard_line = _parse_yard_line(play.get("yardLine") or situation.get("yardLine"))
    possession = play.get("possession") or situation.get("possession")
    offense = _offense_side(str(possession or ""), home, away)
    hp = int(play.get("homeScore") or play.get("home_score") or 0)
    ap = int(play.get("awayScore") or play.get("away_score") or 0)
    red = bool(situation.get("isRedZone")) or (yard_line is not None and yard_line <= 20)
    g2g = distance is not None and yard_line is not None and distance >= yard_line
    return PlaySituation(
        period=period,
        clock=str(clock) if clock else None,
        clock_seconds=int(clock_seconds) if clock_seconds is not None else None,
        down=int(down) if down is not None else None,
        distance=int(distance) if distance is not None else None,
        yard_line=yard_line,
        possession_text=str(possession) if possession else None,
        offense_side=offense,
        home_score=hp,
        away_score=ap,
        score_margin=hp - ap,
        in_red_zone=red,
        is_goal_to_go=g2g,
    )


def _down_from_text(text: object) -> int | None:
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return int(s.split()[0])
    except (TypeError, ValueError):
        return None


def _distance_from_text(text: object) -> int | None:
    s = str(text or "")
    if "&" not in s:
        return None
    part = s.split("&", 1)[1].strip().split()[0]
    try:
        return int(part)
    except (TypeError, ValueError):
        return None


def team_scoring_multipliers(sit: PlaySituation) -> tuple[float, float]:
    """Remaining scoring rate multipliers from margin, clock, and field."""
    gh, ga = _garbage_multipliers(
        sit.home_score, sit.away_score, sit.period, sit.clock_seconds,
    )
    h, a = float(gh), float(ga)

    # Red zone: offense more likely to finish drives for TDs
    if sit.in_red_zone and sit.offense_side == "home":
        h *= 1.05
    elif sit.in_red_zone and sit.offense_side == "away":
        a *= 1.05

    # Trailing team urgency (score margin + time)
    sec_left = (4 - min(4, sit.period)) * 900 + float(sit.clock_seconds or 450)
    late = sit.period >= 4 or (sit.period == 3 and sec_left < 600)
    if sit.score_margin <= -10 and late:
        a *= 1.04
    elif sit.score_margin >= 10 and late:
        h *= 1.02
        a *= 0.96

    # Short yardage / goal line: slightly higher TD chance for offense
    if sit.is_goal_to_go and sit.offense_side == "home":
        h *= 1.03
    elif sit.is_goal_to_go and sit.offense_side == "away":
        a *= 1.03

    return max(0.55, min(1.15, h)), max(0.55, min(1.15, a))


def pace_total_multiplier(sit: PlaySituation) -> float:
    """Small nudge to combined scoring pace from down/distance (clock effects)."""
    mult = 1.0
    d, dist = sit.down, sit.distance
    if d is not None and dist is not None:
        if d >= 3 and dist >= 7:
            mult += 0.015  # passing downs → more clock stoppages
        elif d == 1 and dist is not None and dist <= 10 and not sit.in_red_zone:
            mult -= 0.01  # early-down runs bleed clock
    if sit.period >= 4 and sit.clock_seconds is not None and sit.clock_seconds < 120:
        if abs(sit.score_margin) <= 8:
            mult += 0.02  # two-minute drill pace
    return max(0.94, min(1.08, mult))


def prop_script_multiplier(prop_key: str, *, player_team_side: str | None, sit: PlaySituation) -> float:
    """Tilt player stat types based on game script and situation."""
    pk = str(prop_key or "").lower()
    side = player_team_side
    if not side:
        return 1.0

    margin = sit.score_margin if side == "home" else -sit.score_margin
    trailing = margin < -3
    leading = margin > 7
    late = sit.period >= 4 or (sit.period == 3 and (sit.clock_seconds or 900) < 480)

    mult = 1.0
    if "pass" in pk and "yard" in pk:
        if trailing and late:
            mult = 1.08
        elif leading and late:
            mult = 0.92
        if sit.in_red_zone and sit.offense_side == side:
            mult *= 1.03
    elif "rush" in pk and "yard" in pk:
        if leading and late:
            mult = 1.06
        elif trailing and late:
            mult = 0.90
        if sit.down == 1 and sit.offense_side == side:
            mult *= 1.02
    elif "reception" in pk or pk == "rec_yds":
        if trailing and late:
            mult = 1.06
    elif "td" in pk:
        if sit.in_red_zone and sit.offense_side == side:
            mult = 1.08
        if sit.is_goal_to_go and sit.offense_side == side:
            mult = 1.10

    return max(0.82, min(1.18, mult))


def sim_team_stat_means(sim: dict[str, Any] | None, *, home: str, away: str) -> dict[str, float]:
    """Team-level projected means from the Monte Carlo sim (parent lines)."""
    if not sim:
        return {}
    sb = sim.get("scoreboard") or {}
    out: dict[str, float] = {}
    if sb.get("home_mean") is not None:
        out["home_points"] = float(sb["home_mean"])
    if sb.get("away_mean") is not None:
        out["away_points"] = float(sb["away_mean"])
    if sb.get("total_mean") is not None:
        out["total_points"] = float(sb["total_mean"])

    for m in sim.get("markets") or []:
        mk = str(m.get("market") or "")
        sel = str(m.get("selection") or "")
        proj = m.get("projection")
        if proj is None:
            continue
        try:
            val = float(proj)
        except (TypeError, ValueError):
            continue
        if mk == "Pass Yds":
            if sel == home:
                out["home_pass_yds"] = val
            elif sel == away:
                out["away_pass_yds"] = val
        elif mk == "Comp":
            if sel == home:
                out["home_completions"] = val
            elif sel == away:
                out["away_completions"] = val
        elif mk == "Rush Yds":
            if sel == home:
                out["home_rush_yds"] = val
            elif sel == away:
                out["away_rush_yds"] = val
        elif mk == "Team Total":
            if sel == home:
                out["home_points_alt"] = val
            elif sel == away:
                out["away_points_alt"] = val
        elif mk == "TDs":
            if sel == home:
                out["home_tds"] = val
            elif sel == away:
                out["away_tds"] = val
    return out


def _team_baseline_rate(sim: dict[str, Any], side: str, stat_key: str) -> float | None:
    """Season-pace team rate from score lambdas — denominator for player share."""
    from pricing_engine.ratings import default_team_rates

    try:
        hl = float(sim.get("home_lambda") or 22)
        al = float(sim.get("away_lambda") or 22)
    except (TypeError, ValueError):
        return None
    home_rates, away_rates = default_team_rates(hl, al)
    rates = home_rates if side == "home" else away_rates
    if stat_key == "pass_yds":
        return float(rates["pass_yds"])
    if stat_key == "rush_yds":
        return float(rates["rush_yds"])
    if stat_key == "completions":
        return float(rates["completions"])
    if stat_key == "points":
        return float(rates["tds"])
    return None


def _sim_team_stat(sim_team: dict[str, float], side: str, stat_key: str) -> float | None:
    prefix = f"{side}_"
    if stat_key == "pass_yds":
        val = sim_team.get(f"{prefix}pass_yds")
    elif stat_key == "rush_yds":
        val = sim_team.get(f"{prefix}rush_yds")
    elif stat_key == "completions":
        comp = sim_team.get(f"{prefix}completions")
        if comp is not None:
            return float(comp)
        val = sim_team.get(f"{prefix}pass_yds")
        if val is not None:
            return float(val) / YARDS_PER_COMPLETION
        return None
    elif stat_key == "points":
        val = sim_team.get(f"{prefix}tds") or sim_team.get(f"{prefix}points") or sim_team.get(f"{prefix}points_alt")
    else:
        return None
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def correlated_pregame_prop_projection(
    *,
    prop_key: str,
    team: str | None,
    home: str,
    away: str,
    ewma_proj: float | None,
    sim: dict[str, Any] | None,
    td_prob: bool = False,
) -> float | None:
    """
    Pregame player prop as a derivative of the Monte Carlo game sim.

    Player share = median rate / baseline team rate; anchor = sim team mean × share.
    Blended 35% median form / 65% sim-anchored (matches live prop correlation).
    """
    if ewma_proj is None:
        return None
    if not sim or sim.get("error"):
        return ewma_proj

    side = _team_side(str(team or ""), home, away) if team else None
    stat_key = _prop_team_stat_key(prop_key)
    if not side or not stat_key:
        return ewma_proj

    sim_team = sim_team_stat_means(sim, home=home, away=away)
    sim_final = _sim_team_stat(sim_team, side, stat_key)
    team_base = _team_baseline_rate(sim, side, stat_key)
    if sim_final is None or team_base is None or float(team_base) <= 0:
        return ewma_proj

    if td_prob:
        try:
            player_rate = -math.log(max(0.02, 1.0 - min(0.98, float(ewma_proj))))
        except (ValueError, ZeroDivisionError):
            player_rate = float(ewma_proj)
        share = player_rate / float(team_base)
    else:
        share = float(ewma_proj) / float(team_base)

    share = max(0.02, min(0.85, share))
    anchor_raw = float(sim_final) * share

    if td_prob:
        anchor = max(0.01, min(0.99, 1.0 - math.exp(-anchor_raw)))
        blended = PREGAME_PACE_WEIGHT * float(ewma_proj) + PREGAME_SIM_WEIGHT * anchor
        return round(max(0.01, min(0.99, blended)), 3)

    anchor = round(anchor_raw, 1)
    return round(PREGAME_PACE_WEIGHT * float(ewma_proj) + PREGAME_SIM_WEIGHT * anchor, 1)


def _team_side(team: str, home: str, away: str) -> str | None:
    from lib.nfl_team_registry import teams_match as nfl_match
    from lib.team_registry import teams_match as cfb_match

    if cfb_match(team, home) or nfl_match(team, home):
        return "home"
    if cfb_match(team, away) or nfl_match(team, away):
        return "away"
    return None


def _prop_team_stat_key(prop_key: str) -> str | None:
    pk = str(prop_key or "").lower()
    if "pass" in pk and "yard" in pk:
        return "pass_yds"
    if "rush" in pk and "yard" in pk:
        return "rush_yds"
    if "rec" in pk and "yard" in pk:
        return "pass_yds"  # team pass volume proxy for receiving corps
    if pk == "receptions" or (pk.startswith("rec") and "yard" not in pk and "yds" not in pk):
        return "completions"
    if "td" in pk:
        return "points"
    return None


def correlated_live_prop_projection(
    *,
    prop_key: str,
    team: str | None,
    home: str,
    away: str,
    pregame_proj: float | None,
    current_stat: float | None,
    elapsed_frac: float,
    live_sim: dict[str, Any] | None,
    pregame_sim: dict[str, Any] | None,
    team_box: dict[str, float],
    sit: PlaySituation,
) -> float | None:
    """
    Player prop anchored to live sim team totals so props track spread/total derivatives.
    """
    remain = max(0.02, 1.0 - float(elapsed_frac))
    cur = float(current_stat or 0)

    pace_proj: float | None = None
    if pregame_proj is not None:
        pace_proj = round(cur + float(pregame_proj) * remain, 1)
    elif current_stat is not None:
        pace_proj = round(cur, 1)

    side = _team_side(str(team or ""), home, away) if team else None
    stat_key = _prop_team_stat_key(prop_key)
    live_team = sim_team_stat_means(live_sim, home=home, away=away)
    pre_team = sim_team_stat_means(pregame_sim, home=home, away=away)

    anchor: float | None = None
    if side and stat_key and pregame_proj is not None:
        prefix = f"{side}_"
        if stat_key == "pass_yds":
            sim_final = live_team.get(f"{prefix}pass_yds")
            pre_team_val = pre_team.get(f"{prefix}pass_yds")
            box_cur = float(team_box.get("pass") or 0)
        elif stat_key == "completions":
            sim_pass = live_team.get(f"{prefix}pass_yds")
            pre_pass = pre_team.get(f"{prefix}pass_yds")
            sim_final = float(sim_pass) / 11.0 if sim_pass is not None else None
            pre_team_val = float(pre_pass) / 11.0 if pre_pass is not None else None
            box_cur = float(team_box.get("rec") or team_box.get("pass_comp") or 0)
        elif stat_key == "rush_yds":
            sim_final = live_team.get(f"{prefix}rush_yds")
            pre_team_val = pre_team.get(f"{prefix}rush_yds")
            box_cur = float(team_box.get("rush") or 0)
        elif stat_key == "points":
            sim_final = live_team.get(f"{prefix}points") or live_team.get(f"{prefix}points_alt")
            pre_team_val = pre_team.get(f"{prefix}points") or pre_team.get(f"{prefix}points_alt")
            box_cur = float(sit.home_score if side == "home" else sit.away_score)
        else:
            sim_final = pre_team_val = box_cur = None

        if sim_final is not None and pre_team_val is not None and float(pre_team_val) > 0.5:
            share = float(pregame_proj) / float(pre_team_val)
            share = max(0.02, min(0.85, share))
            anchor = round(cur + max(0.0, float(sim_final) - box_cur) * share, 1)

    if pace_proj is None and anchor is None:
        return None
    if anchor is None:
        blended = pace_proj
    elif pace_proj is None:
        blended = anchor
    else:
        blended = round(0.35 * float(pace_proj) + 0.65 * float(anchor), 1)

    script = prop_script_multiplier(prop_key, player_team_side=side, sit=sit)
    return round(float(blended) * script, 1)
