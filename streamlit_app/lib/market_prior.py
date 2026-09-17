"""Benter market priors — Pinnacle fair first, then multi-book consensus."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from .odds_math import american_to_implied, devig_two_way
from .prop_pricing import normalize_onyx_player, prop_key_from_row
from .team_registry import resolve_canonical, teams_match


def _round_prob(p: float | None) -> float | None:
    if p is None or not math.isfinite(p):
        return None
    return round(float(p), 3)


def _round_half(n: float) -> float:
    return round(n * 2) / 2


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(np.median(values))


def matchup_key(home: str, away: str) -> str:
    return f"{away}|{home}"


def _matchup_keys(home: str, away: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for h, a in ((home, away), (resolve_canonical(home), resolve_canonical(away))):
        k = matchup_key(str(h or ""), str(a or ""))
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def pin_fair_from_row(row: dict[str, Any], implied: float | None) -> float | None:
    """Pinnacle fair prob for this side (stored sharpEdge or explicit pinFair)."""
    sharp = row.get("sharpEdge")
    if sharp is not None and implied is not None:
        try:
            return _round_prob(implied + float(sharp))
        except (TypeError, ValueError):
            pass
    for key in ("pinFair", "pin_fair", "pinnacleFair"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if 0 < val < 1:
                return _round_prob(val)
        except (TypeError, ValueError):
            continue
    return None


def _valid_stored_prior(prior: Any, implied: float | None) -> float | None:
    try:
        val = float(prior)
    except (TypeError, ValueError):
        return None
    if not (0 < val < 1):
        return None
    # Archived Onyx props often carry a 0.5 placeholder unrelated to the posted price.
    if abs(val - 0.5) < 1e-6 and implied is not None and abs(implied - 0.5) > 0.02:
        return None
    return _round_prob(val)


def _side_key(row: dict[str, Any]) -> str:
    return str(row.get("side") or "").lower()


def prop_pair_key(row: dict[str, Any]) -> str | None:
    player = normalize_onyx_player(str(row.get("player") or ""))
    prop_key = prop_key_from_row(row) or str(row.get("propKey") or "")
    if not player or not prop_key:
        return None
    try:
        line = float(row.get("line"))
    except (TypeError, ValueError):
        return None
    home = str(row.get("home") or "")
    away = str(row.get("away") or "")
    return f"{player}|{prop_key}|{line}|{away}|{home}"


def _game_side_prob(
    fair: dict[str, Any] | None,
    *,
    market: str,
    side: str,
) -> float | None:
    if not fair:
        return None
    market_l = market.lower()
    side_l = side.lower()
    if market_l == "spread":
        if side_l == "home_cover":
            return _round_prob(fair.get("spread", {}).get("homeCover"))
        if side_l == "away_cover":
            hc = fair.get("spread", {}).get("homeCover")
            return _round_prob(1 - hc) if hc is not None else None
    if market_l == "total":
        if side_l == "over":
            return _round_prob(fair.get("total", {}).get("over"))
        if side_l == "under":
            ov = fair.get("total", {}).get("over")
            return _round_prob(1 - ov) if ov is not None else None
    if market_l == "moneyline":
        if side_l == "home_ml":
            return _round_prob(fair.get("moneyline", {}).get("homeWin"))
        if side_l == "away_ml":
            hw = fair.get("moneyline", {}).get("homeWin")
            return _round_prob(1 - hw) if hw is not None else None
    return None


def build_pinnacle_game_fair(odds_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """De-vigged Pinnacle fair probs keyed by away|home."""
    if odds_df.empty:
        return {}
    pin = odds_df[odds_df["book_id"].astype(str).str.lower() == "pinnacle"]
    if pin.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for (home, away), gdf in pin.groupby(["home", "away"], dropna=False):
        fair = _book_fair_from_flat(gdf, str(home), str(away))
        if fair:
            out[matchup_key(str(home), str(away))] = fair
    return out


def build_matchup_consensus(odds_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Median de-vigged fair probs across all books (mirrors market_consensus.js)."""
    if odds_df.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for (home, away), gdf in odds_df.groupby(["home", "away"], dropna=False):
        spread_home_fair: list[float] = []
        spread_lines: list[float] = []
        total_over_fair: list[float] = []
        total_lines: list[float] = []
        ml_home_fair: list[float] = []

        for book_id, bdf in gdf.groupby("book_id", dropna=False):
            fair = _book_fair_from_flat(bdf, str(home), str(away))
            if not fair:
                continue
            if fair.get("spread", {}).get("homeCover") is not None:
                spread_home_fair.append(float(fair["spread"]["homeCover"]))
                if fair["spread"].get("line") is not None:
                    spread_lines.append(float(fair["spread"]["line"]))
            if fair.get("total", {}).get("over") is not None:
                total_over_fair.append(float(fair["total"]["over"]))
                if fair["total"].get("line") is not None:
                    total_lines.append(float(fair["total"]["line"]))
            if fair.get("moneyline", {}).get("homeWin") is not None:
                ml_home_fair.append(float(fair["moneyline"]["homeWin"]))

        consensus: dict[str, Any] = {"bookCount": int(gdf["book_id"].nunique())}
        if spread_home_fair:
            hc = _round_prob(_median(spread_home_fair))
            consensus["spread"] = {
                "line": _round_half(_median(spread_lines)) if spread_lines else None,
                "homeCover": hc,
                "awayCover": _round_prob(1 - hc) if hc is not None else None,
                "books": len(spread_home_fair),
            }
        if total_over_fair:
            ov = _round_prob(_median(total_over_fair))
            consensus["total"] = {
                "line": _round_half(_median(total_lines)) if total_lines else None,
                "over": ov,
                "under": _round_prob(1 - ov) if ov is not None else None,
                "books": len(total_over_fair),
            }
        if ml_home_fair:
            hw = _round_prob(_median(ml_home_fair))
            consensus["moneyline"] = {
                "homeWin": hw,
                "awayWin": _round_prob(1 - hw) if hw is not None else None,
                "books": len(ml_home_fair),
            }
        if len(consensus) > 1:
            out[matchup_key(str(home), str(away))] = consensus
    return out


def _book_fair_from_flat(bdf: pd.DataFrame, home: str, away: str) -> dict[str, Any]:
    fair: dict[str, Any] = {}
    mk = bdf["market_key"].astype(str).str.lower()
    spreads = bdf[mk.isin({"spreads", "spread", "point_spread"})]
    totals = bdf[mk.isin({"totals", "total", "total_points"})]
    h2h = bdf[mk.isin({"h2h", "moneyline"})]

    home_spread = spreads[spreads["selection"].astype(str).apply(lambda s: teams_match(s, home))]
    away_spread = spreads[spreads["selection"].astype(str).apply(lambda s: teams_match(s, away))]
    if not home_spread.empty and not away_spread.empty:
        hp = home_spread.iloc[0].get("price")
        ap = away_spread.iloc[0].get("price")
        hc = devig_two_way(hp, ap)
        if hc is not None:
            line = home_spread.iloc[0].get("line")
            try:
                line_f = float(line) if line is not None and not (isinstance(line, float) and math.isnan(line)) else None
            except (TypeError, ValueError):
                line_f = None
            fair["spread"] = {"line": line_f, "homeCover": _round_prob(hc), "awayCover": _round_prob(1 - hc)}

    over_rows = totals[totals["selection"].astype(str).str.lower().str.contains("over", na=False)]
    under_rows = totals[totals["selection"].astype(str).str.lower().str.contains("under", na=False)]
    if over_rows.empty:
        over_rows = totals[totals["side"].astype(str).str.lower().str.contains("over", na=False)]
    if under_rows.empty:
        under_rows = totals[totals["side"].astype(str).str.lower().str.contains("under", na=False)]
    if not over_rows.empty and not under_rows.empty:
        ov = devig_two_way(over_rows.iloc[0].get("price"), under_rows.iloc[0].get("price"))
        if ov is not None:
            line = over_rows.iloc[0].get("line")
            try:
                line_f = float(line) if line is not None and not (isinstance(line, float) and math.isnan(line)) else None
            except (TypeError, ValueError):
                line_f = None
            fair["total"] = {"line": line_f, "over": _round_prob(ov), "under": _round_prob(1 - ov)}

    home_ml = h2h[h2h["selection"].astype(str).apply(lambda s: teams_match(s, home))]
    away_ml = h2h[h2h["selection"].astype(str).apply(lambda s: teams_match(s, away))]
    if not home_ml.empty and not away_ml.empty:
        hw = devig_two_way(home_ml.iloc[0].get("price"), away_ml.iloc[0].get("price"))
        if hw is not None:
            fair["moneyline"] = {"homeWin": _round_prob(hw), "awayWin": _round_prob(1 - hw)}
    return fair


def build_prop_pair_fair(prop_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """De-vig over/under pairs from a prop batch (same player/line/matchup)."""
    if prop_df.empty:
        return {}
    sides: dict[str, dict[str, Any]] = {}
    for _, row in prop_df.iterrows():
        r = row.to_dict()
        key = prop_pair_key(r)
        if not key:
            continue
        side = _side_key(r)
        if side not in {"over", "under"}:
            continue
        sides.setdefault(key, {})[side] = r.get("price")

    out: dict[str, dict[str, float]] = {}
    for key, prices in sides.items():
        over_p, under_p = prices.get("over"), prices.get("under")
        if over_p is None or under_p is None:
            continue
        fair_over = devig_two_way(over_p, under_p)
        if fair_over is None:
            continue
        out[key] = {"over": fair_over, "under": 1.0 - fair_over}
    return out


@dataclass
class MarketPriorContext:
    game_consensus: dict[str, dict[str, Any]] = field(default_factory=dict)
    pin_game_fair: dict[str, dict[str, Any]] = field(default_factory=dict)
    prop_pair_fair: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        prop_df: pd.DataFrame | None = None,
        odds_df: pd.DataFrame | None = None,
    ) -> MarketPriorContext:
        odds = odds_df if odds_df is not None else load_cached_odds_lines()
        ctx = cls(
            game_consensus=build_matchup_consensus(odds),
            pin_game_fair=build_pinnacle_game_fair(odds),
            prop_pair_fair=build_prop_pair_fair(prop_df) if prop_df is not None else {},
        )
        return ctx

    def _lookup_game(self, row: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        for key in _matchup_keys(home, away):
            cons = self.game_consensus.get(key)
            pin = self.pin_game_fair.get(key)
            if cons or pin:
                return cons, pin
        for key, cons in self.game_consensus.items():
            parts = key.split("|", 1)
            if len(parts) != 2:
                continue
            a, h = parts
            if teams_match(h, home) and teams_match(a, away):
                return cons, self.pin_game_fair.get(key)
        return None, None

    def game_pin_side(self, row: dict[str, Any]) -> float | None:
        _, pin = self._lookup_game(row)
        return _game_side_prob(pin, market=str(row.get("market") or ""), side=_side_key(row))

    def game_consensus_side(self, row: dict[str, Any]) -> tuple[float | None, int]:
        cons, _ = self._lookup_game(row)
        prob = _game_side_prob(cons, market=str(row.get("market") or ""), side=_side_key(row))
        if prob is None:
            return None, 1
        books = 1
        if cons:
            market = str(row.get("market") or "").lower()
            if market == "spread":
                books = int((cons.get("spread") or {}).get("books") or cons.get("bookCount") or 1)
            elif market == "total":
                books = int((cons.get("total") or {}).get("books") or cons.get("bookCount") or 1)
            elif market == "moneyline":
                books = int((cons.get("moneyline") or {}).get("books") or cons.get("bookCount") or 1)
            else:
                books = int(cons.get("bookCount") or 1)
        return prob, max(1, books)

    def prop_pair_side(self, row: dict[str, Any]) -> float | None:
        key = prop_pair_key(row)
        if not key:
            return None
        pair = self.prop_pair_fair.get(key)
        if not pair:
            return None
        side = _side_key(row)
        if side == "over":
            return _round_prob(pair.get("over"))
        if side == "under":
            return _round_prob(pair.get("under"))
        return None

    def consensus_book_count(self, row: dict[str, Any]) -> int:
        _, books = self.game_consensus_side(row)
        return books


def resolve_benter_prior(
    row: dict[str, Any],
    implied: float | None,
    *,
    ctx: MarketPriorContext | None = None,
) -> tuple[float | None, float | None, int, str]:
    """
    Choose Benter prior: Pinnacle fair → multi-book consensus → two-way de-vig → stored → implied.
    Returns (prior, pin_fair_for_play, book_count, source).
    """
    category = str(row.get("category") or "prop")
    pin_play = pin_fair_from_row(row, implied)
    if pin_play is None and ctx is not None and category == "game":
        pin_play = ctx.game_pin_side(row)

    # 1. Pinnacle fair
    prior_pin = pin_play
    if prior_pin is None and ctx is not None and category == "game":
        prior_pin = ctx.game_pin_side(row)
    if prior_pin is not None:
        return prior_pin, prior_pin, 1, "pinnacle"

    # 2. Multi-book consensus (game lines)
    if ctx is not None and category == "game":
        cons, books = ctx.game_consensus_side(row)
        if cons is not None:
            return cons, pin_play, books, "consensus"

    # 3. Two-way de-vig on the same prop line (over + under in batch)
    if ctx is not None and category == "prop":
        pair = ctx.prop_pair_side(row)
        if pair is not None:
            return pair, pin_play, 2, "devig"

    # 4. Stored slate prior when it is not a single-book placeholder
    stored = _valid_stored_prior(row.get("prior"), implied)
    if stored is not None and (implied is None or abs(stored - implied) > 0.005):
        return stored, pin_play, 1, "stored"

    # 5. Posted book implied
    if implied is not None:
        return _round_prob(implied), pin_play, 1, "implied"

    return None, pin_play, 1, "none"


@lru_cache(maxsize=1)
def load_cached_odds_lines() -> pd.DataFrame:
    from .odds_cache import load_cached_lines

    return load_cached_lines()
