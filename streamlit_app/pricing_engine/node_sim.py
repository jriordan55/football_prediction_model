"""Node game_sim bridge — depth-chart player props via /api/sim/matchup."""
from __future__ import annotations

from typing import Any

from lib.config import NODE_API
from lib.node_bridge import node_available
from lib.odds_math import implied_to_american
from lib.sport_context import SPORT_CFB

_PROP_LABELS = {
    "pass_yds": "Pass Yds",
    "rush_yds": "Rush Yds",
    "rec_yds": "Rec Yds",
    "receptions": "Rec",
    "pass_tds": "Pass TDs",
    "tds": "Anytime TD",
    "pass_attempts": "Pass Att",
    "pass_completions": "Comp",
    "rush_attempts": "Carries",
}


def node_sim_available() -> bool:
    return node_available()


def fetch_node_matchup_sim(
    home: str,
    away: str,
    *,
    year: int = 2026,
    sdvs_year: int = 2025,
    simulations: int = 5000,
    neutral: bool = False,
    market_spread: float | None = None,
    timeout: int = 120,
) -> dict[str, Any] | None:
    """Call Node simulateFullMarkets via /api/sim/matchup (CFB P&R teams)."""
    import requests

    if not node_available():
        return None

    params: dict[str, Any] = {
        "home": home,
        "away": away,
        "year": year,
        "sdvsYear": sdvs_year,
        "sims": simulations,
    }
    if neutral:
        params["neutral"] = "1"
    if market_spread is not None:
        params["marketSpread"] = market_spread

    url = f"{NODE_API.rstrip('/')}/api/sim/matchup"
    try:
        r = requests.get(url, params=params, timeout=timeout)
        if not r.ok:
            return None
        data = r.json()
        return data if isinstance(data, dict) and not data.get("error") else None
    except Exception:
        return None


def _prop_rows_from_side(players: list[dict[str, Any]], team: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for player in players or []:
        name = str(player.get("name") or "")
        pos = str(player.get("position") or "")
        for prop_key, stats in (player.get("props") or {}).items():
            if not isinstance(stats, dict):
                continue
            line = stats.get("line")
            over_pct = stats.get("overPct")
            if line is None or over_pct is None:
                continue
            try:
                p_over = float(over_pct)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "player": name,
                    "position": pos,
                    "team": team,
                    "market": _PROP_LABELS.get(prop_key, prop_key),
                    "prop_key": prop_key,
                    "line": line,
                    "mean": stats.get("mean"),
                    "over_prob": round(p_over, 4),
                    "under_prob": round(1 - p_over, 4),
                    "fair_over": implied_to_american(p_over),
                    "fair_under": implied_to_american(1 - p_over),
                    "source": "node_sim",
                }
            )
    return rows


def extract_player_props(node_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten home/away playerProps from Node simulation."""
    markets = (node_result.get("simulation") or {}).get("markets") or {}
    pp = markets.get("playerProps") or {}
    home = str(node_result.get("home") or (node_result.get("projection") or {}).get("home") or "")
    away = str(node_result.get("away") or (node_result.get("projection") or {}).get("away") or "")
    out = _prop_rows_from_side(pp.get("home") or [], home)
    out.extend(_prop_rows_from_side(pp.get("away") or [], away))
    out.sort(key=lambda r: float(r.get("mean") or 0), reverse=True)
    return out


def merge_node_markets_into_sim(py_sim: dict[str, Any], node_result: dict[str, Any]) -> dict[str, Any]:
    """Overlay Node roster-based yard/TD markets and props onto Python sim shell."""
    if not node_result:
        return py_sim

    nm = (node_result.get("simulation") or {}).get("markets") or {}
    merged = dict(py_sim)
    extra: list[dict[str, Any]] = list(merged.get("markets") or [])

    def _add_from_node(period: str, market: str, selection: str, block: dict[str, Any] | None, line_key: str = "line"):
        if not block:
            return
        line = block.get(line_key)
        prob = block.get("overPct") or block.get("homeCoverPct")
        if prob is None:
            return
        extra.append(
            {
                "period": period,
                "market": market,
                "selection": selection,
                "line": line,
                "prob": round(float(prob), 4),
                "price": implied_to_american(float(prob)),
                "source": "node_sim",
            }
        )

    yards = nm.get("yards") or {}
    if yards:
        for key, label, sel in (
            ("homePass", "Pass Yds", merged.get("home")),
            ("awayPass", "Pass Yds", merged.get("away")),
            ("homeRush", "Rush Yds", merged.get("home")),
            ("awayRush", "Rush Yds", merged.get("away")),
        ):
            _add_from_node("FG", label, str(sel), yards.get(key))

    tds = nm.get("touchdowns") or {}
    if tds:
        _add_from_node("FG", "TDs", str(merged.get("home")), tds.get("home"))
        _add_from_node("FG", "TDs", str(merged.get("away")), tds.get("away"))

    merged["markets"] = extra
    merged["prop_mode"] = node_result.get("propMode") or "node"
    merged["node_sim"] = True
    return merged


def run_cfb_matchup_with_node(
    home: str,
    away: str,
    *,
    sport: str,
    year: int,
    week: int,
    market_spread: float | None,
    market_total: float | None,
    live_game: dict[str, Any] | None,
    n_sims: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Python base sim + Node depth-chart props when available (CFB)."""
    from .simulator import run_matchup_simulation

    py_sim = run_matchup_simulation(
        sport,
        home,
        away,
        season=year,
        week=week,
        market_spread=market_spread,
        market_total=market_total,
        live_game=live_game,
        n_sims=n_sims,
    )
    props: list[dict[str, Any]] = []

    if str(sport).lower() != SPORT_CFB or not node_sim_available():
        return py_sim, props

    node_result = fetch_node_matchup_sim(
        home,
        away,
        year=year,
        simulations=min(n_sims, 5000),
        market_spread=market_spread,
    )
    if not node_result:
        return py_sim, props

    py_sim = merge_node_markets_into_sim(py_sim, node_result)
    props = extract_player_props(node_result)
    return py_sim, props
