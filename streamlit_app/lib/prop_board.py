"""Player prop board filters — one primary market per player."""
from __future__ import annotations

import pandas as pd

from lib.prop_pricing import prop_key_from_row, line_plausible_for_prop, sync_market_fields

ALLOWED_PROP_KEYS = frozenset({"pass_yds", "rush_yds", "rec_yds", "pass_tds"})

NFL_PROP_KEYS = frozenset({
    "pass_yds",
    "pass_yds_q1",
    "rush_yds",
    "rec_yds",
    "receptions",
    "pass_tds",
    "pass_attempts",
    "pass_completions",
    "rush_attempts",
})

_MARKET_PRIORITY = {
    "pass_yds": 0,
    "rush_yds": 1,
    "rec_yds": 2,
    "pass_tds": 3,
}


def _row_prop_key(row: pd.Series) -> str:
    pk = prop_key_from_row(row.to_dict()) or str(row.get("propKey") or "")
    return pk.lower().strip()


def _parse_american(price: object) -> float | None:
    if price is None:
        return None
    try:
        s = str(price).strip().replace("−", "-").replace("+", "")
        if not s or s.lower() in {"nan", "none", ""}:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _juice_distance(price: object, *, target: float = -110.0) -> float:
    american = _parse_american(price)
    if american is None:
        return 9999.0
    return abs(american - target)


def _normalize_side(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in {"over", "o"} or s.startswith("over"):
        return "over"
    if s in {"under", "u"} or s.startswith("under"):
        return "under"
    return ""


def _main_line_score(*, over_price: object, under_price: object, is_main: bool) -> float:
    """Lower is better. Prefer paired -110/-110 lines; Onyx isMain is a tiebreaker only."""
    over_d = _juice_distance(over_price)
    under_d = _juice_distance(under_price)
    if over_price is not None and under_price is not None:
        score = over_d + under_d
    else:
        score = min(over_d, under_d) + 250.0
    if is_main:
        score -= 25.0
    return score


def filter_main_prop_lines(df: pd.DataFrame) -> pd.DataFrame:
    """One main posted line per player + market_key — juice-based, not alt ladder."""
    if df.empty:
        return df

    out = df.copy()
    out = pd.DataFrame([sync_market_fields(r.to_dict()) for _, r in out.iterrows()])
    out["_prop_key"] = out.apply(_row_prop_key, axis=1)
    out["_side_norm"] = out["side"].map(_normalize_side) if "side" in out.columns else ""

    group_cols = [c for c in ["player", "market_key", "home", "away"] if c in out.columns]
    if "market_key" not in group_cols:
        group_cols = [c for c in ["player", "_prop_key", "home", "away"] if c in out.columns]
    if not group_cols:
        return out.drop(columns=["_prop_key", "_side_norm"], errors="ignore")

    kept_indices: list[int] = []
    for _, grp in out.groupby(group_cols, dropna=False):
        books: dict[float, dict] = {}
        for idx, row in grp.iterrows():
            side = row.get("_side_norm") or _normalize_side(row.get("side"))
            if side not in {"over", "under"}:
                continue
            try:
                line = float(row.get("line"))
            except (TypeError, ValueError):
                continue
            pk = _row_prop_key(row)
            pos = str(row.get("position") or "")
            if not line_plausible_for_prop(pk, line, position=pos):
                continue
            book = books.setdefault(
                line,
                {"over": None, "under": None, "is_main": False, "indices": []},
            )
            book[side] = row.get("price")
            book["indices"].append(idx)
            if row.get("is_main") is True or row.get("isMain") is True:
                book["is_main"] = True

        if not books:
            continue

        best_line = min(
            books.keys(),
            key=lambda ln: _main_line_score(
                over_price=books[ln]["over"],
                under_price=books[ln]["under"],
                is_main=books[ln]["is_main"],
            ),
        )
        kept_indices.extend(books[best_line]["indices"])

    if not kept_indices:
        return out.drop(columns=["_prop_key", "_side_norm"], errors="ignore")

    result = out.loc[sorted(set(kept_indices))].drop(columns=["_prop_key", "_side_norm"], errors="ignore")
    keep_mask = []
    for _, row in result.iterrows():
        pk = prop_key_from_row(row.to_dict()) or _row_prop_key(row)
        keep_mask.append(line_plausible_for_prop(pk, row.get("line"), position=str(row.get("position") or "")))
    return result.loc[keep_mask].reset_index(drop=True)


def filter_primary_player_props(df: pd.DataFrame) -> pd.DataFrame:
    """Keep passing/rushing/receiving yards and passing TDs — one line per player."""
    if df.empty:
        return df

    rows: list[pd.Series] = []
    for _, row in df.iterrows():
        pk = _row_prop_key(row)
        if pk not in ALLOWED_PROP_KEYS:
            continue
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    filtered = pd.DataFrame(rows)
    filtered["_prop_key"] = filtered.apply(_row_prop_key, axis=1)
    filtered["_prio"] = filtered["_prop_key"].map(_MARKET_PRIORITY).fillna(99)

    if "ev_pct" in filtered.columns:
        filtered["_ev"] = pd.to_numeric(filtered["ev_pct"], errors="coerce").fillna(-999)
        filtered = filtered.sort_values(["player", "_ev", "_prio"], ascending=[True, False, True])
    else:
        filtered = filtered.sort_values(["player", "_prio"])

    out = filtered.drop_duplicates(subset=["player"], keep="first").drop(
        columns=["_prop_key", "_prio", "_ev"], errors="ignore"
    )
    if "ev_pct" in out.columns:
        out = out.sort_values("ev_pct", ascending=False, na_position="last")
    return out.reset_index(drop=True)


def filter_nfl_player_props(df: pd.DataFrame) -> pd.DataFrame:
    """Keep all core NFL markets — one main line per player + market (not one prop per player)."""
    if df.empty:
        return df

    scoped = filter_main_prop_lines(df)
    if scoped.empty:
        return scoped

    rows: list[pd.Series] = []
    for _, row in scoped.iterrows():
        pk = _row_prop_key(row)
        if pk in NFL_PROP_KEYS:
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    if "ev_pct" in out.columns:
        out = out.sort_values("ev_pct", ascending=False, na_position="last")
    elif "expected_roi" in out.columns:
        out = out.sort_values("expected_roi", ascending=False, na_position="last")
    elif "roi" in out.columns:
        out = out.sort_values("roi", ascending=False, na_position="last")
    return out.reset_index(drop=True)
