"""Grade pre-game lines against final scores — mirrors projection_results.js."""
from __future__ import annotations

from typing import Any


def _quarter_total(line_scores: list | None, quarters: int = 1) -> float | None:
    if not line_scores:
        return None
    try:
        return sum(float(x or 0) for x in line_scores[:quarters])
    except (TypeError, ValueError):
        return None


def grade_side(
    *,
    side: str,
    line: float | None,
    home_points: int,
    away_points: int,
    home_line_scores: list | None = None,
    away_line_scores: list | None = None,
    market: str = "",
) -> dict[str, Any]:
    margin = home_points - away_points
    total = home_points + away_points
    side_l = str(side or "").lower()
    mkt = str(market or "").lower()

    if side_l in ("home_cover", "away_cover"):
        if line is None:
            return {"actual": margin, "result": None}
        spread = float(line)
        adjusted = margin + spread
        if abs(adjusted) < 0.001:
            return {"actual": margin, "result": "push"}
        home_covers = adjusted > 0
        wins = home_covers if side_l == "home_cover" else not home_covers
        return {"actual": margin, "result": "hit" if wins else "miss"}

    if side_l in ("over", "under"):
        if line is None:
            return {"actual": total, "result": None}
        ln = float(line)
        if abs(total - ln) < 0.001:
            return {"actual": total, "result": "push"}
        over_wins = total > ln
        wins = over_wins if side_l == "over" else not over_wins
        return {"actual": total, "result": "hit" if wins else "miss"}

    if side_l == "home_ml":
        home_wins = margin > 0
        return {"actual": 1 if home_wins else 0, "result": "hit" if home_wins else "miss"}
    if side_l == "away_ml":
        away_wins = margin < 0
        return {"actual": 1 if away_wins else 0, "result": "hit" if away_wins else "miss"}

    if "1st quarter" in mkt or "q1" in mkt:
        hq = _quarter_total(home_line_scores, 1)
        aq = _quarter_total(away_line_scores, 1)
        if hq is None or aq is None:
            return {"actual": None, "result": None}
        q_margin, q_total = hq - aq, hq + aq
        if "total" in mkt:
            if line is None:
                return {"actual": q_total, "result": None}
            over_wins = q_total > float(line)
            wins = over_wins if side_l == "over" else not over_wins
            return {"actual": q_total, "result": "hit" if wins else "miss"}
        if line is None:
            return {"actual": q_margin, "result": None}
        adjusted = q_margin + float(line)
        if abs(adjusted) < 0.001:
            return {"actual": q_margin, "result": "push"}
        home_covers = adjusted > 0
        wins = home_covers if side_l == "home_cover" else not home_covers
        return {"actual": q_margin, "result": "hit" if wins else "miss"}

    return {"actual": None, "result": None}


def fmt_actual(side: str, actual: Any, group: str) -> str:
    if actual is None:
        return "—"
    if group == "moneyline":
        return "Win" if actual else "Loss"
    if group in ("spread",) or (group == "derivative" and "spread" in str(side).lower()):
        n = float(actual)
        return f"+{n:g}" if n > 0 else f"{n:g}"
    try:
        return f"{float(actual):.1f}"
    except (TypeError, ValueError):
        return str(actual)


def grade_slate_rows(rows: list[dict[str, Any]], game: dict[str, Any]) -> list[dict[str, Any]]:
    graded = []
    for row in rows:
        grade = grade_side(
            side=str(row.get("side") or ""),
            line=row.get("line"),
            home_points=int(game["homePoints"]),
            away_points=int(game["awayPoints"]),
            home_line_scores=game.get("homeLineScores"),
            away_line_scores=game.get("awayLineScores"),
            market=str(row.get("market") or ""),
        )
        if grade.get("result") is None and grade.get("actual") is None:
            continue
        out = dict(row)
        out["actual"] = fmt_actual(str(row.get("side") or ""), grade.get("actual"), str(row.get("group") or ""))
        out["actualRaw"] = grade.get("actual")
        out["result"] = grade.get("result")
        graded.append(out)
    return graded
