"""Market prop rows only — no synthetic lines, odds, or fabricated players."""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .prop_median_projection import median_matchup_projection
from .prop_pricing import PROP_KEYS, prop_key_from_row
from .prop_reprice import (
    _stored_projection,
    baseline_projection,
    reprice_prop_row,
    resolve_prop_team,
    team_logo_for,
)
from .team_registry import team_key


def _normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _finite_float(val: Any) -> float | None:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def has_real_market_price(row: dict[str, Any]) -> bool:
    price = row.get("price")
    if price is None:
        return False
    s = str(price).strip().lower()
    return s not in ("", "—", "nan", "none", "null")


def is_real_market_prop(row: dict[str, Any]) -> bool:
    """True when row is an posted sportsbook prop — not synthesized."""
    from .prop_pricing import is_combo_prop_market

    if is_combo_prop_market(row):
        return False
    if row.get("synthetic"):
        return False
    player = str(row.get("player") or "").strip()
    if not player:
        return False
    market = str(row.get("market") or row.get("prop") or "").strip()
    if not market:
        return False
    line = _finite_float(row.get("line"))
    if line is None:
        return False
    if not has_real_market_price(row):
        return False
    pk = prop_key_from_row(row)
    if pk and pk not in PROP_KEYS:
        return False
    book = str(row.get("book") or row.get("book_id") or "").strip().lower()
    if book in {"model", "synthetic", "baseline"}:
        return False
    return True


def format_prop_projection(row: dict[str, Any] | None) -> str:
    """Display-safe OURS value — never renders blank for listed props."""
    if not row:
        return "—"
    val = resolve_prop_projection(row)
    if val is None:
        for key in ("projection", "modelProj"):
            try:
                val = float(row.get(key))
                if math.isfinite(val):
                    break
            except (TypeError, ValueError):
                val = None
    if val is None:
        try:
            val = float(row.get("line"))
        except (TypeError, ValueError):
            return "—"
    prop_key = (prop_key_from_row(row) or "").lower()
    market = str(row.get("market") or row.get("prop") or "").lower()
    if prop_key == "pass_tds" or "passing td" in market:
        txt = f"{val:.1f}".rstrip("0").rstrip(".")
        return txt or "0"
    return f"{val:.1f}"


def resolve_prop_projection(row: dict[str, Any], *, historical: bool = False) -> float | None:
    """Model projection — median matchup rate with opponent/pace/script adjustments."""
    if not is_real_market_prop(row) and not historical:
        return None

    proj = median_matchup_projection(row)
    if proj is not None:
        return proj

    stored = _stored_projection(row)
    if stored is not None:
        return stored

    if not is_real_market_prop(row):
        return None

    player = str(row.get("player") or "")
    prop_key = prop_key_from_row(row)
    if not player or not prop_key or prop_key not in PROP_KEYS:
        return None

    team = resolve_prop_team(row)
    proj = baseline_projection(
        player,
        team,
        prop_key,
        home=row.get("home"),
        away=row.get("away"),
    )
    if proj is not None and proj >= 0:
        return round(float(proj), 1)

    if not historical:
        repriced = reprice_prop_row(row, skip_gamelog=False)
        for key in ("modelProj", "projection"):
            try:
                val = float(repriced.get(key))
                if math.isfinite(val) and val >= 0:
                    return round(val, 1)
            except (TypeError, ValueError):
                continue

    try:
        line_f = float(row.get("line"))
        if math.isfinite(line_f) and line_f >= 0:
            return round(line_f, 1)
    except (TypeError, ValueError):
        pass
    return None


def enrich_matchup_prop_rows(
    prop_rows: list[dict[str, Any]],
    *,
    home: str,
    away: str,
    board_row: dict[str, Any] | None = None,
    historical: bool = False,
) -> list[dict[str, Any]]:
    """Normalize real archived/live market props — never invent players or lines."""
    _ = board_row  # reserved for future logo enrichment from board
    out: list[dict[str, Any]] = []

    for raw in prop_rows:
        if raw.get("group") not in (None, "prop") and not raw.get("player"):
            continue
        row = dict(raw)
        if not is_real_market_prop(row):
            continue

        team = resolve_prop_team(row)
        if team:
            row["team"] = team
            logo = team_logo_for(row, team)
            if logo:
                row["teamLogo"] = logo
        elif home and away and not historical:
            continue
        if home and away and team:
            if not (team_key(team) == team_key(home) or team_key(team) == team_key(away)):
                continue

        if historical:
            proj = resolve_prop_projection(row, historical=True)
        else:
            row = reprice_prop_row(row)
            proj = resolve_prop_projection(row, historical=False)

        if proj is not None:
            row["modelProj"] = proj
            row["projection"] = proj
        elif is_real_market_prop(row):
            try:
                line_f = float(row.get("line"))
                if math.isfinite(line_f):
                    row["modelProj"] = round(line_f, 1)
                    row["projection"] = round(line_f, 1)
            except (TypeError, ValueError):
                pass
        out.append(row)

    out.sort(
        key=lambda row: (
            0 if team_key(str(row.get("team") or "")) == team_key(home) else 1,
            str(row.get("player") or ""),
            str(row.get("market") or ""),
        )
    )
    return out
