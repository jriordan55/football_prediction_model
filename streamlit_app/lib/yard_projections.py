"""CFB team total yards projections — market median + multi-factor model."""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import pandas as pd

from .bcftoys_ypp import lookup_ypp, matchup_ypp_edge
from .depth_chart import build_projected_starters
from .live_projections import CFBFASTR
from .odds_math import american_to_implied, devig_two_way, ev_pct, implied_to_american
from .prop_median_projection import median_matchup_projection
from .prop_pricing import norm_cdf, prop_key_from_row
from .sp_projections import scores_from_spread_total
from .team_registry import resolve_canonical, teams_match
from .odds_client import market_label
from .team_yards_odds import market_spec

PASS_SHARE = float(CFBFASTR["pass_share"])
PERIOD_SHARE = {"game": 1.0, "1h": 0.48, "2h": 0.52, "q1": 0.25, "q2": 0.23, "q3": 0.24, "q4": 0.28}
from .weather_client import fetch_hourly_forecast_cached, kickoff_hourly_slots
from .weather_historical import project_total_adjustment, summarize_kickoff_weather

YARDS_PER_POINT = float(CFBFASTR["yards_per_point"])
LEAGUE_YARDS = 55.2 * YARDS_PER_POINT
YARD_INCREMENT = 25


def round_projection_25(yards: float | None) -> int | None:
    """Round model yards to nearest 25 (DraftKings milestone grid)."""
    if yards is None:
        return None
    try:
        y = float(yards)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(y):
        return None
    return int(round(y / YARD_INCREMENT) * YARD_INCREMENT)


def format_dk_milestone_line(line: float | None) -> str:
    if line is None:
        return "—"
    return f"{int(round(float(line) + 0.5))}+"


def format_american_odds(price: int | float | None) -> str:
    if price is None:
        return "—"
    if isinstance(price, str) and price.strip() in ("—", ""):
        return "—"
    if isinstance(price, str) and (price.startswith("+") or price.startswith("-")):
        return price
    try:
        p = int(float(price))
    except (TypeError, ValueError):
        return "—"
    return f"+{p}" if p > 0 else str(p)


def model_over_at_line(median: float, sd: float, line: float) -> float:
    """P(over milestone) from normal yards distribution."""
    return 1.0 - norm_cdf(float(line), float(median), float(sd))


def model_fair_american(model_over: float | None) -> str:
    """Convert model P(over) to displayable American odds (clamped)."""
    if model_over is None:
        return "—"
    try:
        p = float(model_over)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(p):
        return "—"
    p = min(0.975, max(0.025, p))
    raw = implied_to_american(p)
    if not raw:
        return "—"
    try:
        n = int(str(raw).replace("+", ""))
    except ValueError:
        return raw
    n = max(-800, min(800, n))
    return f"+{n}" if n > 0 else str(n)


def _sd_floor(period: str, category: str) -> float:
    if period == "q1":
        return 10.0 if category != "total" else 14.0
    if period == "1h":
        return 18.0 if category != "total" else 24.0
    return 28.0


def _default_sd(median: float, period: str, category: str, ypp: dict[str, Any] | None) -> float:
    o7 = (ypp or {}).get("o7_plus")
    vol = 0.16 if period == "q1" else (0.21 if period == "1h" else 0.18)
    if o7 is not None:
        vol = 0.12 + float(o7) * 0.08
    return max(_sd_floor(period, category), float(median) * vol)


def _fair_over_at_line(alt: dict[str, Any]) -> float | None:
    fair = alt.get("fair_over")
    if fair is not None:
        try:
            return float(fair)
        except (TypeError, ValueError):
            pass
    fair = devig_two_way(alt.get("over_price"), alt.get("under_price"))
    if fair is not None:
        return float(fair)
    imp = american_to_implied(alt.get("over_price"))
    return float(imp) if imp is not None else None


def _main_alt_row(market_alts: list[dict[str, Any]], main_line: float | None) -> dict[str, Any] | None:
    if main_line is None:
        return None
    for alt in market_alts:
        try:
            if abs(float(alt["line"]) - float(main_line)) < 0.01:
                return alt
        except (TypeError, ValueError, KeyError):
            continue
    return None


def _calibrate_yards_distribution(
    *,
    median: float,
    market_alts: list[dict[str, Any]],
    main_line: float | None,
    period: str,
    category: str,
    ypp: dict[str, Any] | None,
) -> tuple[float, float]:
    """
    Anchor yards median to the book ladder and fit SD so the main line matches DK fair prob.
    """
    mm_median = median
    has_two_way = any(
        a.get("over_price") is not None and a.get("under_price") is not None for a in market_alts
    )
    main_row = _main_alt_row(market_alts, main_line)
    book_fair = _fair_over_at_line(main_row) if main_row else None

    # DK yard milestones are usually over-only — calibrating to juiced single-sided
    # prices collapses SD and blows up alt-line odds.
    if has_two_way and main_line is not None and book_fair is not None:
        target = min(0.88, max(0.12, float(book_fair)))
        lo, hi = 8.0, max(60.0, mm_median * 0.40)
        for _ in range(50):
            mid = (lo + hi) / 2.0
            p = model_over_at_line(mm_median, mid, float(main_line))
            if p > target:
                lo = mid
            else:
                hi = mid
        sd = max(_sd_floor(period, category), (lo + hi) / 2.0)
        sd = max(sd, mm_median * (0.11 if period == "1h" else 0.13))
        return mm_median, sd

    return mm_median, _default_sd(mm_median, period, category, ypp)


def enrich_alt_with_model_price(
    alt: dict[str, Any],
    *,
    median: float,
    sd: float,
) -> dict[str, Any]:
    """Attach model fair price + EV at the sportsbook milestone line."""
    try:
        ln = float(alt["line"])
    except (TypeError, ValueError, KeyError):
        return alt
    model_over = model_over_at_line(median, sd, ln)
    book_over = alt.get("over_price")
    book_fair = alt.get("fair_over")
    if book_fair is None:
        book_fair = devig_two_way(alt.get("over_price"), alt.get("under_price"))
    edge_pts = (model_over - book_fair) * 100 if book_fair is not None else None
    out = dict(alt)
    out.update(
        {
            "model_over_prob": round(model_over, 4),
            "model_over_pct": round(model_over * 100, 1),
            "model_price": model_fair_american(model_over),
            "market_over_pct": round(float(book_fair) * 100, 1) if book_fair is not None else alt.get("market_over_pct"),
            "edge_pts": round(edge_pts, 1) if edge_pts is not None else None,
            "edge_ev": ev_pct(model_over, book_over),
        }
    )
    return out


def _finite(val: Any, default: float | None = None) -> float | None:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _weather_yards_factor(
    home: str,
    away: str,
    kickoff: str | None,
    *,
    venue: str | None = None,
    tab: str | None = None,
) -> dict[str, Any]:
    from .venue_coords import coords_for_game

    coords = coords_for_game({"home": home, "away": away, "venue": venue or ""})
    if not coords or not kickoff:
        return {"factor": 1.0, "wind_mph": None, "temp_f": None, "precip_pct": None}
    lat, lon = coords
    matchup = f"{away} @ {home}"
    hourly = fetch_hourly_forecast_cached(lat, lon, str(kickoff), matchup, tab)
    slots = kickoff_hourly_slots(hourly)
    wx = summarize_kickoff_weather(slots)
    total_adj = project_total_adjustment(wx.get("wind_mph"), wx.get("temp_f"), wx.get("precip_pct"))
    # Map total pts adj to yards (~13.5 yds/pt); dampen to 60% for single-team variance.
    pts_delta = total_adj - 54.0
    factor = 1.0 + (pts_delta / 54.0) * 0.6
    return {
        "factor": round(max(0.88, min(1.12, factor)), 3),
        "wind_mph": wx.get("wind_mph"),
        "temp_f": wx.get("temp_f"),
        "precip_pct": wx.get("precip_pct"),
    }


def _game_script_factor(
    team: str, home: str, away: str, spread: float | None, *, category: str = "total"
) -> float:
    """Trailing teams throw more; favorites run more (rush category)."""
    if spread is None:
        return 1.0
    is_home = teams_match(team, home)
    margin = float(spread) if is_home else -float(spread)
    if category == "rush":
        if margin >= 10:
            return 1.05
        if margin >= 3:
            return 1.02
        if margin <= -10:
            return 0.94
        if margin <= -3:
            return 0.97
        return 1.0
    if category == "pass":
        if margin <= -10:
            return 1.08
        if margin <= -3:
            return 1.04
        if margin >= 10:
            return 0.94
        if margin >= 3:
            return 0.97
        return 1.0
    if margin <= -10:
        return 1.06
    if margin <= -3:
        return 1.03
    if margin >= 10:
        return 0.96
    if margin >= 3:
        return 0.98
    return 1.0


def _personnel_yards(team: str, opponent: str, home: str, away: str, props_df: pd.DataFrame | None) -> dict[str, Any]:
    """Sum projected QB + RB + WR/TE yards from player prop board."""
    starters = build_projected_starters(team)
    names = {s.get("name") for s in (starters.get("starters") or []) if s.get("name")}
    if not names or props_df is None or props_df.empty:
        return {"total": None, "breakdown": []}

    team_canon = resolve_canonical(team) or team
    if "team" in props_df.columns:
        rows = props_df[props_df["team"].astype(str).apply(lambda t: teams_match(t, team_canon))]
    else:
        rows = props_df.iloc[0:0]

    breakdown: list[dict[str, Any]] = []
    pass_yds = rush_yds = 0.0
    seen_props: set[str] = set()
    for _, r in rows.iterrows():
        pk = prop_key_from_row(r.to_dict()) or ""
        if pk not in ("pass_yds", "rush_yds", "rec_yds"):
            continue
        player = str(r.get("player") or r.get("description") or "")
        if player not in names:
            continue
        key = f"{player}|{pk}"
        if key in seen_props:
            continue
        seen_props.add(key)
        try:
            line = float(r.get("line"))
        except (TypeError, ValueError):
            line = None
        row_dict = dict(r)
        row_dict.setdefault("home", home)
        row_dict.setdefault("away", away)
        val = _finite(median_matchup_projection(row_dict))
        if val is None:
            val = line
        if val is None:
            continue
        breakdown.append({"player": player, "prop": pk, "yards": round(val, 1)})
        if pk == "pass_yds":
            pass_yds += val
        elif pk == "rush_yds":
            rush_yds += val

    team_total = pass_yds + rush_yds if (pass_yds + rush_yds) > 0 else None
    return {
        "total": round(team_total, 1) if team_total else None,
        "pass": round(pass_yds, 1) if pass_yds > 0 else None,
        "rush": round(rush_yds, 1) if rush_yds > 0 else None,
        "breakdown": breakdown,
    }


@lru_cache(maxsize=4)
def _historical_yards_index(year: int) -> list[dict[str, Any]]:
    """Prior-season games with spread/total buckets — yards estimated from points."""
    from .cfbd_games import load_cfbd_games

    rows: list[dict[str, Any]] = []
    for g in load_cfbd_games(year):
        if not g.get("completed"):
            continue
        hp, ap = g.get("homePoints"), g.get("awayPoints")
        if hp is None or ap is None:
            continue
        lines = g.get("lines") or []
        spread = total = None
        for ln in lines:
            if ln.get("spread") is not None:
                spread = float(ln["spread"])
            if ln.get("overUnder") is not None:
                total = float(ln["overUnder"])
        rows.append(
            {
                "home": g.get("homeTeam"),
                "away": g.get("awayTeam"),
                "home_pts": float(hp),
                "away_pts": float(ap),
                "home_yards": float(hp) * YARDS_PER_POINT,
                "away_yards": float(ap) * YARDS_PER_POINT,
                "spread": spread,
                "total": total,
            }
        )
    return rows


def _historical_bucket_yards(
    team: str,
    *,
    spread: float | None,
    total: float | None,
    is_home: bool,
    year: int = 2025,
) -> float | None:
    idx = _historical_yards_index(year)
    if not idx or spread is None or total is None:
        return None
    canon = resolve_canonical(team) or team
    vals: list[float] = []
    for g in idx:
        if g.get("spread") is None or g.get("total") is None:
            continue
        if abs(float(g["spread"]) - float(spread)) > 4.0:
            continue
        if abs(float(g["total"]) - float(total)) > 7.0:
            continue
        if teams_match(g.get("home") or "", canon):
            vals.append(float(g["home_yards"]))
        elif teams_match(g.get("away") or "", canon):
            vals.append(float(g["away_yards"]))
    if len(vals) < 3:
        return None
    vals.sort()
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _pace_yards(team_pts: float, opponent: str, team: str) -> float:
    edge = matchup_ypp_edge(team, opponent)
    base = max(150.0, team_pts * YARDS_PER_POINT)
    return base * float(edge.get("edge_factor") or 1.0)


def _distribution_sd(median: float, ypp: dict[str, Any] | None, *, period: str = "game", category: str = "total") -> float:
    return _default_sd(median, period, category, ypp)


def _category_pace(pace_total: float, category: str) -> float:
    if category == "pass":
        return pace_total * PASS_SHARE
    if category == "rush":
        return pace_total * (1.0 - PASS_SHARE)
    if category == "receiving":
        return pace_total * PASS_SHARE * 0.92
    return pace_total


def _category_personnel(personnel: dict[str, Any], category: str) -> float | None:
    if category == "pass":
        return personnel.get("pass")
    if category == "rush":
        return personnel.get("rush")
    if category == "receiving":
        rec = sum(
            b.get("yards", 0)
            for b in (personnel.get("breakdown") or [])
            if b.get("prop") == "rec_yds"
        )
        return round(rec, 1) if rec > 0 else None
    return personnel.get("total")


def _resolve_market_odds(
    odds_pkg: dict[str, Any] | None, team: str, market_key: str | None
) -> dict[str, Any] | None:
    if not odds_pkg or not odds_pkg.get("teams"):
        return None
    for tname, tdata in odds_pkg["teams"].items():
        if not (teams_match(tname, team) or teams_match(tdata.get("team") or "", team)):
            continue
        markets = tdata.get("markets") or {}
        if market_key and market_key in markets:
            return markets[market_key]
        pk = tdata.get("primary_market_key") or tdata.get("market_key")
        if pk and pk in markets:
            return markets[pk]
        if markets:
            return markets[sorted(markets.keys())[0]]
        return {
            "alt_lines": tdata.get("alt_lines") or [],
            "main_line": tdata.get("main_line"),
            "market_median": tdata.get("market_median"),
            "market_key": tdata.get("market_key"),
        }
    return None


def project_team_yards(
    *,
    team: str,
    opponent: str,
    home: str,
    away: str,
    is_home: bool,
    spread: float | None,
    total: float | None,
    team_pts: float | None,
    odds_pkg: dict[str, Any] | None,
    props_df: pd.DataFrame | None = None,
    kickoff: str | None = None,
    venue: str | None = None,
    year: int = 2026,
    market_key: str | None = None,
) -> dict[str, Any]:
    """Team yards projection for one DK market (total / rush / pass × FG / 1H / Q1)."""
    team = resolve_canonical(team) or team
    ypp = lookup_ypp(team)
    ypp_edge = matchup_ypp_edge(team, opponent)
    mkt = market_spec(market_key) if market_key else {"category": "total", "period": "game", "label": "Team Total Yards"}
    category = mkt.get("category") or "total"
    period = mkt.get("period") or "game"
    period_scale = PERIOD_SHARE.get(period, 1.0)

    if team_pts is None and spread is not None and total is not None:
        away_i, home_i = scores_from_spread_total(float(spread), float(total))
        team_pts = home_i if is_home else away_i
    team_pts = float(team_pts or LEAGUE_YARDS / YARDS_PER_POINT / 2)

    wx = _weather_yards_factor(home, away, kickoff, venue=venue)
    script = _game_script_factor(team, home, away, spread, category=category)
    pace_total = _pace_yards(team_pts, opponent, team) * wx["factor"] * script * period_scale
    pace = _category_pace(pace_total, category)
    hist_raw = _historical_bucket_yards(team, spread=spread, total=total, is_home=is_home, year=year - 1)
    hist = _category_pace(hist_raw * period_scale, category) if hist_raw else None
    personnel = _personnel_yards(team, opponent, home, away, props_df)
    pers_val = _category_personnel(personnel, category)
    if pers_val is not None:
        pers_val = round(float(pers_val) * period_scale, 1)

    market_odds = _resolve_market_odds(odds_pkg, team, market_key)
    market_alts: list[dict[str, Any]] = list((market_odds or {}).get("alt_lines") or [])
    main_line = (market_odds or {}).get("main_line")
    resolved_key = (market_odds or {}).get("market_key") or market_key

    components: dict[str, float | None] = {
        "pace_ypp": round(pace, 1),
        "historical": round(hist, 1) if hist else None,
        "personnel": pers_val,
        "market_median": (market_odds or {}).get("market_median"),
    }

    weights: list[tuple[float, float]] = [(0.35, pace)]
    if hist:
        weights.append((0.10, hist))
    if pers_val:
        weights.append((0.15, float(pers_val)))
    if components.get("market_median"):
        weights.append((0.40, float(components["market_median"])))

    wsum = sum(w for w, _ in weights)
    model_est = sum(w * v for w, v in weights) / wsum if wsum else pace

    market_median = components.get("market_median")
    if market_median is not None and market_alts:
        median = 0.72 * float(market_median) + 0.28 * model_est
    elif main_line is not None:
        median = 0.62 * float(main_line) + 0.38 * model_est
    else:
        median = model_est

    median, sd = _calibrate_yards_distribution(
        median=float(median),
        market_alts=market_alts,
        main_line=float(main_line) if main_line is not None else None,
        period=period,
        category=category,
        ypp=ypp,
    )
    alt_rows: list[dict[str, Any]] = []
    for alt in market_alts:
        ln = float(alt["line"])
        row = enrich_alt_with_model_price(alt, median=median, sd=sd)
        row["is_main"] = main_line is not None and abs(ln - float(main_line)) < 0.01
        alt_rows.append(row)

    starters = build_projected_starters(team)
    return {
        "team": team,
        "market_key": resolved_key,
        "market_label": mkt.get("label") or market_label(resolved_key or ""),
        "category": category,
        "period": period,
        "median": round(median, 1),
        "sd": round(sd, 1),
        "main_line": main_line,
        "components": components,
        "ypp": ypp,
        "ypp_edge": ypp_edge,
        "weather": wx,
        "game_script": script,
        "personnel": personnel,
        "starters": starters.get("starters") or [],
        "alt_lines": alt_rows,
        "alt_count": len(alt_rows),
        "spread": spread,
        "total": total,
        "team_pts": round(team_pts, 1),
    }


def project_team_all_markets(
    *,
    team: str,
    opponent: str,
    home: str,
    away: str,
    is_home: bool,
    spread: float | None,
    total: float | None,
    team_pts: float | None,
    odds_pkg: dict[str, Any] | None,
    props_df: pd.DataFrame | None = None,
    kickoff: str | None = None,
    venue: str | None = None,
    year: int = 2026,
) -> dict[str, Any]:
    """Project every posted DK yard market for one team."""
    market_keys: list[str] = []
    if odds_pkg and odds_pkg.get("teams"):
        for tname, tdata in odds_pkg["teams"].items():
            if teams_match(tname, team) or teams_match(tdata.get("team") or "", team):
                market_keys = sorted((tdata.get("markets") or {}).keys())
                break

    if not market_keys:
        primary = project_team_yards(
            team=team,
            opponent=opponent,
            home=home,
            away=away,
            is_home=is_home,
            spread=spread,
            total=total,
            team_pts=team_pts,
            odds_pkg=odds_pkg,
            props_df=props_df,
            kickoff=kickoff,
            venue=venue,
            year=year,
        )
        return {"team": team, "primary": primary, "markets": {}}

    markets: dict[str, dict[str, Any]] = {}
    for mk in market_keys:
        markets[mk] = project_team_yards(
            team=team,
            opponent=opponent,
            home=home,
            away=away,
            is_home=is_home,
            spread=spread,
            total=total,
            team_pts=team_pts,
            odds_pkg=odds_pkg,
            props_df=props_df,
            kickoff=kickoff,
            venue=venue,
            year=year,
            market_key=mk,
        )
    primary_key = None
    for pref in ("team_total_yds", "alternate_team_total_yds", "team_total_yards"):
        if pref in markets:
            primary_key = pref
            break
    if primary_key is None:
        primary_key = market_keys[0]
    return {"team": team, "primary": markets[primary_key], "markets": markets}


def build_matchup_yard_projections(
    home: str,
    away: str,
    *,
    spread: float | None,
    total: float | None,
    odds_pkg: dict[str, Any] | None,
    props_df: pd.DataFrame | None = None,
    kickoff: str | None = None,
    venue: str | None = None,
    year: int = 2026,
) -> dict[str, Any]:
    away_i, home_i = (
        scores_from_spread_total(float(spread), float(total))
        if spread is not None and total is not None
        else (LEAGUE_YARDS / YARDS_PER_POINT / 2, LEAGUE_YARDS / YARDS_PER_POINT / 2)
    )
    return {
        "home": project_team_all_markets(
            team=home,
            opponent=away,
            home=home,
            away=away,
            is_home=True,
            spread=spread,
            total=total,
            team_pts=home_i,
            odds_pkg=odds_pkg,
            props_df=props_df,
            kickoff=kickoff,
            venue=venue,
            year=year,
        ),
        "away": project_team_all_markets(
            team=away,
            opponent=home,
            home=home,
            away=away,
            is_home=False,
            spread=spread,
            total=total,
            team_pts=away_i,
            odds_pkg=odds_pkg,
            props_df=props_df,
            kickoff=kickoff,
            venue=venue,
            year=year,
        ),
    }
