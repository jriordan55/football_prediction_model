"""Live game state — fix completed score, simulate remainder from PBP context."""
from __future__ import annotations

import re
from typing import Any


def _parse_clock_seconds(clock: str | None) -> int | None:
    if not clock:
        return None
    m = re.match(r"(\d+):(\d+)", str(clock).strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def game_state_from_live(game: dict[str, Any] | None) -> dict[str, Any]:
    """Classify game: completed / live / unplayed."""
    if not game:
        return {"state": "unplayed"}
    status = str(game.get("status") or "").lower()
    period = game.get("period")
    clock = game.get("clock")
    home_score = int(game.get("home_score") or 0)
    away_score = int(game.get("away_score") or 0)

    if status in ("final", "completed", "post"):
        return {
            "state": "completed",
            "home_score": home_score,
            "away_score": away_score,
        }

    if status in ("in", "live", "in progress", "halftime") or (
        period and int(period or 0) > 0 and status not in ("scheduled", "pre", "pregame")
    ):
        secs = _parse_clock_seconds(clock)
        period_n = int(period or 1)
        # ~15 min quarters, 4 periods
        elapsed_frac = min(0.98, max(0.02, ((period_n - 1) * 900 + (900 - (secs or 450))) / 3600))
        remain = 1.0 - elapsed_frac
        return {
            "state": "live",
            "home_score": home_score,
            "away_score": away_score,
            "period": period_n,
            "clock": clock,
            "remain_frac": remain,
            "win_prob_home": game.get("win_prob_home"),
        }

    return {"state": "unplayed"}


def adjust_lambdas_for_live(
    home_lambda: float,
    away_lambda: float,
    live: dict[str, Any],
) -> tuple[float, float, dict[str, int] | None]:
    """
    For live games: scale remaining lambdas by time left and subtract fixed scores.
    Returns (home_rem_lambda, away_rem_lambda, fixed_scores or None).
    """
    state = live.get("state")
    if state == "completed":
        return 0.0, 0.0, {
            "home": int(live.get("home_score") or 0),
            "away": int(live.get("away_score") or 0),
        }
    if state != "live":
        return home_lambda, away_lambda, None

    remain = float(live.get("remain_frac") or 0.5)
    h_rem = home_lambda * remain
    a_rem = away_lambda * remain

    # Situation: margin, down/distance, field, possession
    sit = live.get("situation")
    if sit is not None:
        from pricing_engine.situation_model import pace_total_multiplier, team_scoring_multipliers

        hm, am = team_scoring_multipliers(sit)
        pace = pace_total_multiplier(sit)
        h_rem *= hm * pace
        a_rem *= am * pace

    # If in-play win prob available, tilt remaining lambdas
    wp = live.get("win_prob_home")
    if wp is not None:
        try:
            p = float(wp)
            if p > 1.0:
                p /= 100.0
            margin = (p - 0.5) * 4.0 * remain
            h_rem = max(0.5, h_rem + margin / 2)
            a_rem = max(0.5, a_rem - margin / 2)
        except (TypeError, ValueError):
            pass

    fixed = {
        "home": int(live.get("home_score") or 0),
        "away": int(live.get("away_score") or 0),
    }
    return h_rem, a_rem, fixed
