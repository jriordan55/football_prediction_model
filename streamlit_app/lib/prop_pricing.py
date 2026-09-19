"""Prop pricing — port of puntandrally/prop_pricing_model.js."""
from __future__ import annotations

import math
import re
from typing import Any

from .odds_math import american_to_implied, implied_to_american

SD_MULTIPLIERS = {
    "Pass Attempts": 0.25,
    "Completions": 0.28,
    "Passing Yards": 0.31,
    "Passing TDs": 0.45,
    "Anytime TD": 0.55,
    "Carries": 0.39,
    "Receptions": 0.53,
    "Rushing Yards": 0.56,
    "Receiving Yards": 0.64,
    "TE Receiving Yds": 0.69,
    "Rush + Rec Yards": 0.5,
}

RUSH_YPG_TIERS = [
    (80, 0.42),
    (60, 0.52),
    (0, 0.63),
]

PROP_KEYS = frozenset({
    "pass_yds",
    "pass_yds_q1",
    "rush_yds",
    "rec_yds",
    "receptions",
    "pass_tds",
    "pass_attempts",
    "pass_completions",
    "rush_attempts",
    "tds",
})

MARKET_TO_PROP = {
    "passing yards": "pass_yds",
    "1st quarter pass yards": "pass_yds_q1",
    "q1 pass yards": "pass_yds_q1",
    "rushing yards": "rush_yds",
    "receiving yards": "rec_yds",
    "receptions": "receptions",
    "pass attempts": "pass_attempts",
    "pass completions": "pass_completions",
    "pass att": "pass_attempts",
    "completions": "pass_completions",
    "rush attempts": "rush_attempts",
    "carries": "rush_attempts",
    "rush att": "rush_attempts",
    "passing tds": "pass_tds",
    "pass tds": "pass_tds",
    "passing touchdowns": "pass_tds",
    "anytime td": "tds",
    "anytime touchdown": "tds",
    "anytime touchdown scorer": "tds",
}

MARKET_KEY_TO_PROP: dict[str, str] = {
    "player_passing_yards": "pass_yds",
    "player_pass_yds": "pass_yds",
    "player_pass_yds_q1": "pass_yds_q1",
    "player_rushing_yards": "rush_yds",
    "player_receiving_yards": "rec_yds",
    "player_receptions": "receptions",
    "player_passing_touchdowns": "pass_tds",
    "player_pass_tds": "pass_tds",
    "player_pass_attempts": "pass_attempts",
    "player_pass_completions": "pass_completions",
    "player_rush_yds": "rush_yds",
    "player_rush_attempts": "rush_attempts",
    "player_reception_yds": "rec_yds",
}

MARKET_KEY_TO_LABEL: dict[str, str] = {
    "player_passing_yards": "passing yards",
    "player_pass_yds": "passing yards",
    "player_pass_yds_q1": "1st quarter pass yards",
    "player_rushing_yards": "rushing yards",
    "player_receiving_yards": "receiving yards",
    "player_receptions": "receptions",
    "player_passing_touchdowns": "passing tds",
    "player_pass_tds": "passing tds",
    "player_pass_attempts": "pass attempts",
    "player_pass_completions": "pass completions",
    "player_rush_yds": "rushing yards",
    "player_rush_attempts": "rush attempts",
    "player_reception_yds": "receiving yards",
}

PROP_KEY_TO_LABEL: dict[str, str] = {
    "pass_yds": "passing yards",
    "pass_yds_q1": "1st quarter pass yards",
    "rush_yds": "rushing yards",
    "rec_yds": "receiving yards",
    "receptions": "receptions",
    "pass_tds": "passing tds",
    "pass_attempts": "pass attempts",
    "pass_completions": "pass completions",
    "rush_attempts": "rush attempts",
    "tds": "anytime touchdown",
}


def prop_label_for_key(prop_key: str | None) -> str:
    """Canonical market label for internal prop keys (rush_yds → passing yards)."""
    pk = str(prop_key or "").lower().strip()
    if pk in PROP_KEY_TO_LABEL:
        return PROP_KEY_TO_LABEL[pk]
    return normalize_prop_market(pk.replace("_", " "))

# Plausible main-line ranges by prop key (reject cross-market bleed).
LINE_BOUNDS: dict[str, tuple[float, float]] = {
    "pass_yds": (80.0, 450.0),
    "pass_yds_q1": (15.0, 130.0),
    "rush_yds": (5.0, 180.0),
    "rec_yds": (5.0, 200.0),
    "receptions": (1.0, 15.0),
    "pass_attempts": (8.0, 55.0),
    "pass_completions": (5.0, 40.0),
    "rush_attempts": (2.0, 35.0),
    "pass_tds": (0.5, 5.0),
}

# Onyx combo / alt ladders — not single-stat props (mirrors puntandrally/onyx_player_props.js).
COMBO_MARKET_RE = re.compile(
    r"passing_\+_rushing|rushing_\+_receiving|pass_rush|pass_rush_reception|"
    r"passing \+|rushing \+|receiving \+|\+ rush|\+ rec|\bcombo\b",
    re.I,
)


def normalize_onyx_player(name: Any) -> str:
    """Strip side/line suffix Onyx embeds in selection labels."""
    s = str(name or "").strip()
    s = re.sub(r"\s+(over|under)\s+[\d.]+$", "", s, flags=re.IGNORECASE)
    return s.strip()


def is_combo_prop_market(row: dict[str, Any] | str) -> bool:
    """True for pass+rush, rush+rec, and other combo Onyx player markets."""
    if isinstance(row, str):
        mk = m = row.lower()
    else:
        mk = str(row.get("market_key") or "").lower()
        m = str(row.get("market") or row.get("prop") or "").lower()
    if COMBO_MARKET_RE.search(mk) or COMBO_MARKET_RE.search(m):
        return True
    blob = f"{mk} {m}".lower()
    if "+" in blob:
        if any(k in blob for k in ("pass", "rush", "rec")):
            return True
    if "passing" in blob and "rushing" in blob:
        return True
    if "rushing" in blob and "receiving" in blob:
        return True
    return False


def normalize_prop_market(market: Any) -> str:
    """Normalize Onyx/live market labels to canonical names."""
    raw = str(market or "").lower().strip()
    if is_combo_prop_market(raw):
        return raw
    m = re.sub(r"^player\s+", "", raw)
    m = re.sub(r"_", " ", m)
    m = re.sub(r"\s+", " ", m).strip()
    if is_combo_prop_market(m):
        return m
    if m in MARKET_TO_PROP:
        return m
    if "passing touchdown" in m or "pass td" in m:
        return "passing tds"
    if ("1st quarter" in m or "q1" in m) and "pass" in m:
        return "1st quarter pass yards"
    if "pass attempt" in m or m in {"pass att", "pass attempts"}:
        return "pass attempts"
    if "pass completion" in m or m in {"completions", "pass completions"}:
        return "pass completions"
    if "rush attempt" in m or m in {"carries", "rush att", "rush attempts"}:
        return "rush attempts"
    if m == "receptions" or ("reception" in m and "yard" not in m and "td" not in m):
        return "receptions"
    if "passing yard" in m:
        return "passing yards"
    if "rushing yard" in m:
        return "rushing yards"
    if "receiving yard" in m:
        return "receiving yards"
    if "anytime" in m and "touchdown" in m:
        return "anytime touchdown"
    return m


def _erf(x: float) -> float:
    sign = -1 if x < 0 else 1
    a = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (
        ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592)
        * t
        * math.exp(-a * a)
    )
    return sign * y


def norm_cdf(x: float, mean: float, sd: float) -> float:
    if not math.isfinite(sd) or sd <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / sd
    return 0.5 * (1.0 + _erf(z / math.sqrt(2)))


def _std_norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + _erf(z / math.sqrt(2)))


# Right-skewed counting stats — log-normal over prob (Layer 3), not symmetric normal.
LOGNORMAL_PROP_KEYS = frozenset({
    "pass_yds",
    "pass_yds_q1",
    "rush_yds",
    "rec_yds",
    "receptions",
    "pass_attempts",
    "pass_completions",
    "rush_attempts",
})


def lognormal_over_prob(mean: float, line: float, cv: float) -> float | None:
    """
    P(X > line) when X ~ LogNormal with E[X]=mean and CV=cv.
    Matches Layer 3 right-skew tab (mean > median → lower over prob than normal).
    """
    if not math.isfinite(mean) or mean <= 0 or not math.isfinite(line) or line <= 0:
        return None
    if not math.isfinite(cv) or cv <= 0:
        return None
    sigma2 = math.log(1.0 + cv * cv)
    mu = math.log(mean) - sigma2 / 2.0
    sigma = math.sqrt(sigma2)
    z = (math.log(line) - mu) / sigma
    return max(0.01, min(0.99, 1.0 - _std_norm_cdf(z)))


def _round_pct(n: float | None) -> float | None:
    if n is None:
        return None
    return round(n, 3)


def _rush_sd_multiplier(projection: float) -> float:
    for threshold, mult in RUSH_YPG_TIERS:
        if projection >= threshold:
            return mult
    return SD_MULTIPLIERS["Rushing Yards"]


def sd_type_for_prop(prop_key: str, position: str) -> str | None:
    key = (prop_key or "").lower()
    pos = (position or "").upper()
    if key == "pass_yds":
        return "Passing Yards"
    if key == "pass_yds_q1":
        return "Passing Yards"
    if key == "pass_attempts":
        return "Pass Attempts"
    if key == "pass_completions":
        return "Completions"
    if key == "rush_attempts":
        return "Carries"
    if key == "rush_yds":
        return "Rushing Yards"
    if key == "rec_yds":
        return "TE Receiving Yds" if pos == "TE" else "Receiving Yards"
    if key == "receptions":
        return "Receptions"
    if key == "pass_tds":
        return "Passing TDs"
    if key == "tds":
        return "Anytime TD"
    return None


def sd_multiplier(prop_key: str, position: str, projection: float) -> float | None:
    key = (prop_key or "").lower()
    if key == "rush_yds":
        return _rush_sd_multiplier(projection)
    sd_type = sd_type_for_prop(key, position)
    if not sd_type:
        return None
    return SD_MULTIPLIERS.get(sd_type)


def clamp_american(price: str | None, *, lo: int = -500, hi: int = 500) -> str:
    """Never show absurd fair prices like -1849."""
    if not price or price == "—":
        return "—"
    s = str(price).replace("−", "-").strip()
    try:
        n = int(float(s.replace("+", "")))
    except (TypeError, ValueError):
        return price
    if s.startswith("+"):
        n = abs(n)
    if n > 0:
        n = min(hi, max(100, n))
        return f"+{n}"
    n = max(lo, min(-100, n))
    return str(n)


def format_fair_decimal(prob: float | None, *, lo: int = -500, hi: int = 500) -> str:
    """Fair american with cents — e.g. -101.56 / +101.56."""
    if prob is None:
        return "—"
    p = min(0.99, max(0.01, float(prob)))
    if p >= 0.5:
        am = -100.0 * p / (1.0 - p)
    else:
        am = 100.0 * (1.0 - p) / p
    am = max(float(lo), min(float(hi), am))
    if am > 0:
        return f"+{am:.2f}"
    return f"{am:.2f}"


def format_ours_american(prob: float | None, *, lo: int = -500, hi: int = 500) -> str:
    """Integer fair for OURS column — e.g. -116."""
    if prob is None:
        return "—"
    p = min(0.99, max(0.01, float(prob)))
    if p >= 0.5:
        am = -100.0 * p / (1.0 - p)
    else:
        am = 100.0 * (1.0 - p) / p
    am = max(float(lo), min(float(hi), am))
    if am > 0:
        return f"+{int(round(am))}"
    return str(int(round(am)))


# Standard -110 / -110 moneyline hold (10/210 ≈ 4.76% overround).
DEFAULT_ML_HOLD = 10.0 / 210.0


def format_vigged_american(
    prob: float | None,
    *,
    hold: float = DEFAULT_ML_HOLD,
    lo: int = -5000,
    hi: int = 5000,
) -> str:
    """Book price to win — fair probability scaled by standard two-way vig."""
    if prob is None:
        return "—"
    p = min(0.99, max(0.01, float(prob)))
    imp = min(0.995, max(0.05, p * (1.0 + float(hold))))
    if imp >= 0.5:
        am = -100.0 * imp / (1.0 - imp)
    else:
        am = 100.0 * (1.0 - imp) / imp
    am = max(float(lo), min(float(hi), am))
    if am > 0:
        return f"+{int(round(am))}"
    return str(int(round(am)))


def fair_pair(over_prob: float | None) -> tuple[str, str]:
    if over_prob is None:
        return "—", "—"
    p = min(0.99, max(0.01, float(over_prob)))
    over = format_fair_decimal(p)
    under = format_fair_decimal(1.0 - p)
    return over, under


def analyze_prop_line(
    *,
    projection: float,
    line: float,
    prop_key: str,
    position: str = "",
    over_price: Any = None,
) -> dict[str, Any] | None:
    if not math.isfinite(projection) or projection < 0 or not math.isfinite(line):
        return None

    key = (prop_key or "").lower()
    if key == "tds" and line == 0.5:
        over_pct = _round_pct(min(0.95, max(0.05, projection)))
        implied = american_to_implied(over_price) if over_price else None
        return {
            "mean": round(projection, 1),
            "over_pct": over_pct,
            "implied_over": _round_pct(implied),
        }

    if projection <= 0:
        return None

    mult = sd_multiplier(key, position, projection)
    if mult is None:
        return None

    sd = max(0.5, projection * mult)
    cv = sd / max(projection, 0.01)
    if key in LOGNORMAL_PROP_KEYS:
        p_over = lognormal_over_prob(projection, line, cv)
    else:
        p_over = 1.0 - norm_cdf(line, projection, sd)
    over_pct = _round_pct(p_over)
    implied = american_to_implied(over_price) if over_price else None
    median = round(projection * math.exp(-math.log(1.0 + cv * cv) / 2.0), 1) if key in LOGNORMAL_PROP_KEYS else None
    return {
        "mean": round(projection, 1),
        "sd": round(sd, 1),
        "cv": round(cv, 4),
        "median": median,
        "over_pct": over_pct,
        "implied_over": _round_pct(implied),
        "distribution": "lognormal" if key in LOGNORMAL_PROP_KEYS else "normal",
    }


def sync_market_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Canonical market label + propKey from Onyx market_key when present."""
    out = dict(row)
    mk = str(out.get("market_key") or "").lower().strip()
    if mk in MARKET_KEY_TO_PROP:
        out["propKey"] = MARKET_KEY_TO_PROP[mk]
        out["market"] = MARKET_KEY_TO_LABEL.get(mk, mk.replace("_", " "))
    return out


def line_plausible_for_prop(prop_key: str, line: Any, *, position: str = "") -> bool:
    pk = str(prop_key or "").lower()
    bounds = LINE_BOUNDS.get(pk)
    if not bounds:
        return True
    try:
        ln = float(line)
    except (TypeError, ValueError):
        return False
    lo, hi = bounds
    if not (lo <= ln <= hi):
        return False
    pos = str(position or "").upper()
    if pos == "QB" and pk == "rush_yds" and ln > 90:
        return False
    if pos == "QB" and pk == "rec_yds" and ln > 60:
        return False
    return True


def prop_key_from_row(row: dict[str, Any]) -> str | None:
    pk = row.get("propKey") or row.get("prop_key")
    if pk:
        return str(pk).lower()
    mk = str(row.get("market_key") or "").lower().strip()
    if mk in MARKET_KEY_TO_PROP:
        return MARKET_KEY_TO_PROP[mk]
    market = normalize_prop_market(row.get("market"))
    if market in MARKET_TO_PROP:
        return MARKET_TO_PROP[market]
    for key, val in MARKET_TO_PROP.items():
        if key in market:
            return val
    return None


def side_win_prob(over_pct: float | None, side: str) -> float | None:
    if over_pct is None:
        return None
    side_l = str(side or "").lower()
    if "under" in side_l:
        return _round_pct(1.0 - over_pct)
    if "over" in side_l:
        return over_pct
    return None
