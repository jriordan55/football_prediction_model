"""Player props — synced to team simulation baselines."""
from __future__ import annotations

from typing import Any

from lib.prop_pricing import analyze_prop_line, prop_key_from_row


def _baseline_from_sim(sim: dict[str, Any], prop_key: str, side: str) -> float | None:
    home = sim.get("home") or ""
    markets = sim.get("markets") or []
    for m in markets:
        if m.get("market") == "Pass Yds" and prop_key == "pass_yds":
            if m.get("selection") == side:
                return m.get("line")
        if m.get("market") == "Rush Yds" and prop_key == "rush_yds":
            if m.get("selection") == side:
                return m.get("line")
    _ = home
    return None


def price_props_from_slate(
    slate_rows: list[dict[str, Any]],
    sim: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reprice slate props using simulation-adjusted team baselines."""
    out: list[dict[str, Any]] = []
    home = sim.get("home") or ""
    away = sim.get("away") or ""

    for row in slate_rows:
        player = str(row.get("player") or row.get("description") or "")
        market = str(row.get("market") or row.get("prop") or "")
        line = row.get("line")
        if line is None or not player:
            continue
        try:
            ln = float(line)
        except (TypeError, ValueError):
            continue

        team = str(row.get("team") or "")
        side = home if team and team.lower() in home.lower() else away
        pk = prop_key_from_row(row)
        if not pk:
            continue

        baseline = _baseline_from_sim(sim, pk, side)
        if baseline is None:
            baseline = row.get("projection") or row.get("median")
        if baseline is None:
            continue

        # Scale player baseline by team sim vs season
        try:
            proj = float(baseline) * (float(ln) / float(row.get("median") or ln) if row.get("median") else 1.0)
        except (TypeError, ValueError, ZeroDivisionError):
            proj = float(baseline)

        priced = analyze_prop_line(market, proj, ln)
        if not priced:
            continue
        out.append(
            {
                "player": player,
                "team": team or side,
                "market": market,
                "line": ln,
                "projection": round(proj, 1),
                "over_prob": priced.get("over_prob"),
                "under_prob": priced.get("under_prob"),
                "fair_over": priced.get("fair_over"),
                "fair_under": priced.get("fair_under"),
            }
        )
    return out[:48]


def render_prop_cards(props: list[dict[str, Any]], *, group_by_player: bool = False) -> str:
    from .ui.theme import esc

    if not props:
        return ""

    if group_by_player:
        by_player: dict[str, list[dict[str, Any]]] = {}
        for p in props:
            key = str(p.get("player") or "")
            by_player.setdefault(key, []).append(p)
        cards = []
        for player, rows in by_player.items():
            pos = rows[0].get("position") or ""
            inner = ""
            for p in rows[:6]:
                over = p.get("fair_over") or "—"
                line = p.get("line")
                inner += f'<div class="pe-prop-row"><span>{esc(str(p.get("market") or ""))} O {line}</span><span class="pe-price">{esc(str(over))}</span></div>'
            cards.append(
                f"""<div class="pe-prop-card">
                  <div class="pe-prop-name">{esc(player)} · {esc(str(pos))}</div>
                  {inner}
                </div>"""
            )
        return f'<div class="pe-props-grid">{"".join(cards)}</div>'

    cards = []
    for p in props:
        over = p.get("fair_over") or "—"
        under = p.get("fair_under") or "—"
        line = p.get("line")
        pos = p.get("position") or ""
        cards.append(
            f"""<div class="pe-prop-card">
              <div class="pe-prop-name">{esc(p.get("player"))} · {esc(str(p.get("market") or ""))} · {esc(str(pos))}</div>
              <div class="pe-prop-row"><span>O {line}</span><span class="pe-price">{esc(str(over))}</span></div>
              <div class="pe-prop-row"><span>U {line}</span><span class="pe-price">{esc(str(under))}</span></div>
            </div>"""
        )
    return f'<div class="pe-props-grid">{"".join(cards)}</div>'
