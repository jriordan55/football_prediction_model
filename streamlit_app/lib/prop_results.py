"""Grade player props from ESPN box score stats."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_num(s: Any) -> float | None:
    if s is None:
        return None
    txt = str(s).replace(",", "")
    m = re.search(r"-?\d+\.?\d*", txt)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def stat_from_box(name: str, market: str, box_stats: dict[str, dict[str, float]]) -> float | None:
    norm = _normalize_name(name)
    player_stats = box_stats.get(norm)
    if not player_stats:
        player_stats = box_stats.get(name.lower())
    if not player_stats:
        # exact normalized key
        for k, v in box_stats.items():
            if _normalize_name(k) == norm:
                player_stats = v
                break
    if not player_stats:
        # fuzzy: last name match (prefer unique)
        last = norm.split()[-1] if norm else ""
        if last:
            matches = [(k, v) for k, v in box_stats.items() if k.endswith(last) or last in k.split()]
            if len(matches) == 1:
                player_stats = matches[0][1]
            elif len(matches) > 1:
                first = norm.split()[0] if norm else ""
                for k, v in matches:
                    if first and first in k:
                        player_stats = v
                        break
    if not player_stats:
        return None

    mkt = str(market or "").lower()
    if "pass" in mkt and "yard" in mkt:
        return player_stats.get("pass_yds")
    if "pass" in mkt and ("touchdown" in mkt or " td" in mkt or mkt.endswith(" td") or mkt == "pass tds"):
        return player_stats.get("pass_tds")
    if "rush" in mkt and "yard" in mkt:
        return player_stats.get("rush_yds")
    if "rec" in mkt and "yard" in mkt:
        return player_stats.get("rec_yds")
    if "reception" in mkt:
        return player_stats.get("rec")
    if "anytime" in mkt and ("touchdown" in mkt or "td" in mkt):
        td = player_stats.get("td")
        return 1.0 if td is not None and td >= 1 else 0.0
    if "touchdown" in mkt or " td" in mkt or mkt.endswith(" td"):
        return player_stats.get("td")
    if "attempt" in mkt and "pass" in mkt:
        return player_stats.get("pass_att")
    if "completion" in mkt:
        return player_stats.get("pass_comp")
    return None


def model_pick_side(projection: float | None, line: float | None) -> str | None:
    """Over/under direction implied by OURS vs the market line."""
    if projection is None or line is None:
        return None
    try:
        proj = float(projection)
        ln = float(line)
    except (TypeError, ValueError):
        return None
    if abs(proj - ln) < 0.001:
        return None
    return "under" if proj < ln else "over"


def grade_player_prop(
    row: dict[str, Any],
    actual: float | None,
    *,
    grade_side: str | None = None,
) -> dict[str, Any]:
    out = dict(row)
    if actual is None:
        out["actual"] = "—"
        out["result"] = None
        return out

    line = row.get("line")
    side = str(grade_side or row.get("side") or row.get("sideLabel") or "").lower()
    out["actual"] = f"{actual:g}"
    if line is None:
        out["result"] = None
        return out

    try:
        ln = float(line)
    except (TypeError, ValueError):
        out["result"] = None
        return out

    if abs(actual - ln) < 0.001:
        out["result"] = "push"
    elif "under" in side:
        out["result"] = "hit" if actual < ln else "miss"
    elif "over" in side or "yes" in side:
        out["result"] = "hit" if actual > ln else "miss"
    else:
        out["result"] = None
    return out


def parse_espn_boxscore(summary: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Return {player_name_lower: {pass_yds, rush_yds, rec_yds, rec, td}}."""
    out: dict[str, dict[str, float]] = {}
    for team in summary.get("boxscore", {}).get("players") or []:
        for group in team.get("statistics") or []:
            gname = str(group.get("name") or "").lower()
            labels = [str(x).upper() for x in (group.get("labels") or [])]
            for ath in group.get("athletes") or []:
                name = (ath.get("athlete") or {}).get("displayName") or ""
                if not name:
                    continue
                key = _normalize_name(name)
                bucket = out.setdefault(key, {})
                stats = ath.get("stats") or []
                if gname == "passing":
                    if "YDS" in labels:
                        bucket["pass_yds"] = _parse_num(stats[labels.index("YDS")])
                    if "C/ATT" in labels:
                        comp_att = str(stats[labels.index("C/ATT")])
                        parts = comp_att.split("/")
                        if len(parts) == 2:
                            bucket["pass_comp"] = _parse_num(parts[0])
                            bucket["pass_att"] = _parse_num(parts[1])
                    if "TD" in labels:
                        td_val = _parse_num(stats[labels.index("TD")]) or 0
                        bucket["pass_tds"] = td_val
                        bucket.setdefault("td", 0)
                        bucket["td"] = (bucket.get("td") or 0) + td_val
                elif gname == "rushing":
                    if "YDS" in labels:
                        bucket["rush_yds"] = _parse_num(stats[labels.index("YDS")])
                    if "TD" in labels:
                        bucket.setdefault("td", 0)
                        bucket["td"] = (bucket.get("td") or 0) + (_parse_num(stats[labels.index("TD")]) or 0)
                elif gname == "receiving":
                    if "YDS" in labels:
                        bucket["rec_yds"] = _parse_num(stats[labels.index("YDS")])
                    if "REC" in labels:
                        bucket["rec"] = _parse_num(stats[labels.index("REC")])
                    if "TD" in labels:
                        bucket.setdefault("td", 0)
                        bucket["td"] = (bucket.get("td") or 0) + (_parse_num(stats[labels.index("TD")]) or 0)
    return out
