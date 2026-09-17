"""In-play player prop projections from live box stats + pregame rate."""
from __future__ import annotations

from typing import Any

from lib.prop_pricing import analyze_prop_line, prop_key_from_row, side_win_prob
from lib.prop_results import model_pick_side, stat_from_box
from lib.odds_math import ev_pct, implied_to_american


def _elapsed_frac(period: int, clock_seconds: int | None) -> float:
    p = max(1, min(4, int(period or 1)))
    sec_left_q = float(clock_seconds if clock_seconds is not None else 450)
    sec_left_game = (4 - p) * 900.0 + sec_left_q
    return max(0.04, min(0.98, 1.0 - sec_left_game / 3600.0))


def live_prop_projection(
    *,
    pregame_proj: float | None,
    current_stat: float | None,
    elapsed_frac: float,
) -> float | None:
    """Project final stat: current box + pregame pace × time left."""
    if current_stat is None and pregame_proj is None:
        return None
    cur = float(current_stat or 0)
    remain = max(0.02, 1.0 - float(elapsed_frac))
    if pregame_proj is not None:
        pre = float(pregame_proj)
        return round(cur + pre * remain, 1)
    return round(cur, 1)


def enrich_live_prop_row(
    row: dict[str, Any],
    *,
    box_stats: dict[str, dict[str, float]],
    pregame_proj: float | None,
    period: int,
    clock_seconds: int | None,
) -> dict[str, Any]:
    from lib.prop_board_enrich import prop_display_name

    prop_key = str(row.get("prop_key") or prop_key_from_row(row) or "")
    market = prop_display_name(prop_key)
    player = str(row.get("player") or "")
    elapsed = _elapsed_frac(period, clock_seconds)
    actual = stat_from_box(player, market, box_stats) if player and box_stats else None
    proj = live_prop_projection(
        pregame_proj=pregame_proj,
        current_stat=actual,
        elapsed_frac=elapsed,
    )
    out = dict(row)
    out["actual_stat"] = actual
    out["live_projection"] = proj
    out["pregame_projection"] = pregame_proj
    if proj is not None and row.get("line") is not None:
        side = model_pick_side(proj, row.get("line"))
        out["model_side"] = side
        if side:
            quote = row.get("over") if side == "over" else row.get("under")
            price = (quote or {}).get("price") if isinstance(quote, dict) else None
            analyzed = analyze_prop_line(
                projection=float(proj),
                line=float(row["line"]),
                prop_key=prop_key,
                position=str(row.get("position") or ""),
                over_price=(row.get("over") or {}).get("price") if isinstance(row.get("over"), dict) else None,
            )
            wp = side_win_prob((analyzed or {}).get("over_pct"), side)
            if wp is not None and price is not None:
                ev = ev_pct(float(wp), price)
                out["expected_roi_pct"] = ev
                out["model_price"] = implied_to_american(float(wp))
    return out
