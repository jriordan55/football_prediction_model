"""Fast SP+ game pricing for Streamlit (mirrors puntandrally/sp_game_lines.js)."""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from .benter_blend import analyze_prop_benter, fill_market_metrics, ml_blend
from .config import DATA_DIR, PUBLIC_DIR
from .market_prior import MarketPriorContext, resolve_benter_prior
from .odds_math import american_to_implied

PROJECTION_VERSION = 16
CALIB_PATH = DATA_DIR / "historical" / "cfb_sp_margin_calibration.json"
DEFAULT_HFA = 2.81
DEFAULT_MARGIN_SIGMA = 16.09
LEAGUE_AVG_PTS = 28.5
DEFAULT_SIM_COUNT = 500
MIN_TEAM_SCORE = 3.0


def _finite_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round_half(n: float) -> float:
    return round(n * 2) / 2


def _round_prob(n: float | None) -> float | None:
    if n is None:
        return None
    return round(float(n), 3)


def _team_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _sp_name_keys(name: str) -> list[str]:
    """Exact normalized keys for SP+ lookup — never strip to parent prefixes."""
    from .team_registry import normalize_team_key, resolve_canonical

    canonical = resolve_canonical(str(name or "").strip()) or str(name or "").strip()
    keys: list[str] = []
    seen: set[str] = set()

    def add(val: str) -> None:
        k = normalize_team_key(val)
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    add(canonical)
    if canonical.endswith(" State"):
        add(canonical.replace(" State", " St"))
        add(canonical.replace(" State", " St."))
    if canonical.endswith(" College"):
        add(canonical.replace(" College", " Coll."))
    return keys


def _enforce_min_team_scores(home_pts: float, away_pts: float) -> tuple[float, float]:
    """FBS teams always project to score at least MIN_TEAM_SCORE."""
    home = max(MIN_TEAM_SCORE, float(home_pts))
    away = max(MIN_TEAM_SCORE, float(away_pts))
    return home, away


def scores_from_spread_total(spread: float, total: float) -> tuple[int, int]:
    """Derive realistic integer team scores from home spread + game total."""
    sp = _finite_float(spread)
    tot = _finite_float(total)
    if sp is None or tot is None:
        raise ValueError("non-finite spread or total")
    margin = -sp
    home, away = _enforce_min_team_scores((tot + margin) / 2, (tot - margin) / 2)
    return realistic_team_scores(home, away, spread=sp, total=tot)


@lru_cache(maxsize=1)
def _valid_football_scores(max_score: int = 75) -> frozenset[int]:
    """Scores reachable with TD (6/7) and FG (3) combinations."""
    ok = {0}
    for s in range(1, max_score + 1):
        if (s >= 3 and s - 3 in ok) or (s >= 6 and s - 6 in ok) or (s >= 7 and s - 7 in ok):
            ok.add(s)
    return frozenset(ok)


def _next_valid_score(valid: frozenset[int], score: int, *, min_score: int = 0) -> int:
    try:
        s = float(score)
        if not math.isfinite(s):
            s = float(min_score)
    except (TypeError, ValueError):
        s = float(min_score)
    floor = max(int(min_score), int(s))
    return min((v for v in valid if v >= floor), default=floor)


def realistic_team_scores(
    home_pts: float,
    away_pts: float,
    *,
    spread: float | None = None,
    total: float | None = None,
    min_home: int = 0,
    min_away: int = 0,
) -> tuple[int, int]:
    """
    Snap raw projections to realistic football scores (3/6/7 increments).
    Preserves spread/total, never returns a tie — slight favorite wins by 1.
    Optional min_home/min_away enforce live-game floors (current score).
    """
    valid = _valid_football_scores()
    min_score = int(MIN_TEAM_SCORE)

    if spread is not None and total is not None:
        sp = _finite_float(spread)
        tot = _finite_float(total)
        if sp is None or tot is None:
            raise ValueError("non-finite spread or total")
        margin = -sp
        target_total = tot
    else:
        hp = _finite_float(home_pts)
        ap = _finite_float(away_pts)
        if hp is None or ap is None:
            raise ValueError("non-finite team points")
        margin = hp - ap
        target_total = hp + ap

    target_home = (target_total + margin) / 2.0
    target_away = (target_total - margin) / 2.0
    home_candidates = sorted(
        s for s in valid if s >= max(int(min_home), 0) and (s == 0 or s >= min_score)
    )
    away_candidates = sorted(
        s for s in valid if s >= max(int(min_away), 0) and (s == 0 or s >= min_score)
    )
    if not home_candidates:
        home_candidates = [_next_valid_score(valid, max(min_home, min_score), min_score=min_home)]
    if not away_candidates:
        away_candidates = [_next_valid_score(valid, max(min_away, min_score), min_score=min_away)]

    best_home = best_away = None
    best_cost = float("inf")
    for home in home_candidates:
        for away in away_candidates:
            if home == away:
                continue
            cost = (
                abs(home - target_home)
                + abs(away - target_away)
                + 0.35 * abs(home + away - target_total)
                + 0.15 * abs((home - away) - margin)
            )
            if cost < best_cost:
                best_cost = cost
                best_home, best_away = home, away

    if best_home is None or best_away is None:
        home_i = int(round(target_home))
        away_i = int(round(target_away))
        if home_i not in valid or home_i < min_home:
            home_i = min(home_candidates, key=lambda s: abs(s - target_home))
        if away_i not in valid or away_i < min_away:
            away_i = min(away_candidates, key=lambda s: abs(s - target_away))
        best_home, best_away = home_i, away_i

    if best_home == best_away:
        if margin > 0:
            best_home = _next_valid_score(valid, best_home + 1, min_score=max(best_home + 1, min_home))
        elif margin < 0:
            best_away = _next_valid_score(valid, best_away + 1, min_score=max(best_away + 1, min_away))
        elif float(home_pts) >= float(away_pts):
            best_home = _next_valid_score(valid, best_home + 1, min_score=max(best_home + 1, min_home))
        else:
            best_away = _next_valid_score(valid, best_away + 1, min_score=max(best_away + 1, min_away))

    return int(best_away), int(best_home)


def team_scores_from_home_spread(spread: float, total: float) -> dict[str, float]:
    """Canonical away/home scores from home-team spread + total."""
    away, home = scores_from_spread_total(float(spread), float(total))
    return {
        "away_score": away,
        "home_score": home,
        "total": _round_half(float(total)),
        "spread": _round_half(float(spread)),
    }


def align_team_scores(
    home_score: float | None,
    away_score: float | None,
    *,
    spread: float | None,
    total: float | None,
    tol: float = 1.5,
) -> tuple[float | None, float | None]:
    """Correct away/home scores if they are swapped vs spread/total."""
    if home_score is None or away_score is None or spread is None or total is None:
        return home_score, away_score
    try:
        exp_away, exp_home = scores_from_spread_total(float(spread), float(total))
        hs, aw = float(home_score), float(away_score)
    except (TypeError, ValueError):
        return home_score, away_score
    if abs(hs - exp_home) <= tol and abs(aw - exp_away) <= tol:
        return hs, aw
    if abs(hs - exp_away) <= tol and abs(aw - exp_home) <= tol:
        return exp_home, exp_away
    return hs, aw


def _poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    fact = math.factorial(k)
    return math.exp(-lam) * (lam**k) / fact


def _poisson_over_prob(line: float, lam: float) -> float | None:
    if not math.isfinite(line) or not math.isfinite(lam):
        return None
    cdf = sum(_poisson_pmf(i, max(0.0, lam)) for i in range(int(math.floor(line)) + 1))
    return 1.0 - min(1.0, cdf)


def _skellam_home_cover(lambda_away: float, lambda_home: float, spread_abs: float, max_runs: int = 70) -> float:
    need = math.ceil(abs(spread_abs))
    p_cover = 0.0
    mass = 0.0
    la = max(0.0, lambda_away)
    lh = max(0.0, lambda_home)
    for a in range(max_runs + 1):
        pa = _poisson_pmf(a, la)
        for h in range(max_runs + 1):
            p = pa * _poisson_pmf(h, lh)
            mass += p
            if h - a >= need:
                p_cover += p
    return p_cover / mass if mass > 0 else 0.5


def _skellam_win_probs(lambda_away: float, lambda_home: float, max_runs: int = 80) -> tuple[float, float]:
    p_home = p_away = p_tie = 0.0
    mass = 0.0
    la = max(0.0, lambda_away)
    lh = max(0.0, lambda_home)
    for a in range(max_runs + 1):
        pa = _poisson_pmf(a, la)
        for h in range(max_runs + 1):
            p = pa * _poisson_pmf(h, lh)
            mass += p
            if h > a:
                p_home += p
            elif a > h:
                p_away += p
            else:
                p_tie += p
    if mass <= 0:
        return 0.5, 0.5
    home_win = (p_home + 0.5 * p_tie) / mass
    return home_win, 1.0 - home_win


def _home_win_from_margin(margin: float, sigma: float = DEFAULT_MARGIN_SIGMA) -> float:
    s = sigma if sigma > 0 else DEFAULT_MARGIN_SIGMA
    z = margin / s
    return min(1 - 1e-6, max(1e-6, 0.5 * (1.0 + math.erf(z / math.sqrt(2)))))


@lru_cache(maxsize=1)
def _load_calib() -> dict[str, Any]:
    if CALIB_PATH.exists():
        return json.loads(CALIB_PATH.read_text(encoding="utf-8"))
    return {
        "sp_to_margin": 0.8892,
        "blowout_k": 0,
        "blowout_threshold": 25,
        "total_base": LEAGUE_AVG_PTS * 2,
        "total_sp_sum_slope": -0.0495,
    }


@lru_cache(maxsize=8)
def _load_sp_index_cached(path_str: str, mtime_ns: int) -> dict[str, dict[str, Any]]:
    path = Path(path_str)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("teams") or {}
    return {}


def _resolve_sp_index_path(season: int | None, display_week: int | None) -> Path:
    from .config import DEFAULT_WEEK, DEFAULT_YEAR
    from .sp_refresh import SP_INDEX_PATH, sp_index_path

    yr = int(season if season is not None else DEFAULT_YEAR)
    wk = int(display_week if display_week is not None else DEFAULT_WEEK)
    path = sp_index_path(yr, wk)
    if path.exists():
        return path
    if SP_INDEX_PATH.exists():
        try:
            meta = json.loads(SP_INDEX_PATH.read_text(encoding="utf-8"))
            if int(meta.get("season") or yr) == yr and int(meta.get("display_week") or -1) == wk:
                return SP_INDEX_PATH
        except (json.JSONDecodeError, OSError):
            pass
    return path


def _load_sp_index(season: int | None = None, display_week: int | None = None) -> dict[str, dict[str, Any]]:
    path = _resolve_sp_index_path(season, display_week)
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    return _load_sp_index_cached(str(path), mtime_ns)


def clear_sp_cache() -> None:
    """Bust in-process SP+ caches after a disk refresh."""
    _load_sp_index_cached.cache_clear()
    _normalized_sp_index.cache_clear()
    _load_calib.cache_clear()


def _ensure_sp_loaded(
    season: int | None = None,
    display_week: int | None = None,
    *,
    refresh: bool = True,
) -> None:
    from .config import DEFAULT_WEEK, DEFAULT_YEAR

    yr = int(season if season is not None else DEFAULT_YEAR)
    wk = int(display_week if display_week is not None else DEFAULT_WEEK)
    if refresh and display_week is not None:
        try:
            from pricing_engine.pit import ratings_should_refresh
            from .sport_context import get_sport

            if not ratings_should_refresh(get_sport(), yr, wk):
                refresh = False
        except ImportError:
            pass

    if refresh:
        from .sp_refresh import ensure_fresh_sp_ratings

        ensure_fresh_sp_ratings(season=yr, display_week=wk)


def warm_sp_cache(season: int | None = None, display_week: int | None = None) -> None:
    """Load SP+ index into memory without triggering a network refresh."""
    _ensure_sp_loaded(season, display_week, refresh=False)
    path = _resolve_sp_index_path(season, display_week)
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    _normalized_sp_index(str(path), mtime_ns)


@lru_cache(maxsize=8)
def _normalized_sp_index(path_str: str, mtime_ns: int) -> dict[str, dict[str, Any]]:
    """Map normalized team key -> SP row (exact keys + registry canonical aliases)."""
    from .team_registry import normalize_team_key, resolve_canonical

    teams = _load_sp_index_cached(path_str, mtime_ns)
    out: dict[str, dict[str, Any]] = {}
    for k, row in teams.items():
        labels = {k, str(row.get("team") or ""), str(row.get("key") or "")}
        canon = resolve_canonical(str(row.get("team") or k) or "")
        if canon:
            labels.add(canon)
        for label in labels:
            nk = normalize_team_key(label)
            if nk and nk not in out:
                out[nk] = row
    return out


def lookup_sp_team(
    team_name: str,
    *,
    season: int | None = None,
    display_week: int | None = None,
    refresh: bool = True,
) -> dict[str, Any] | None:
    from .team_registry import normalize_team_key, resolve_canonical

    _ensure_sp_loaded(season, display_week, refresh=refresh)
    path = _resolve_sp_index_path(season, display_week)
    mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    index = _normalized_sp_index(str(path), mtime_ns)
    for cand in _sp_name_keys(team_name):
        hit = index.get(cand)
        if hit:
            canon = resolve_canonical(team_name) or resolve_canonical(str(hit.get("team") or ""))
            if canon:
                return {**hit, "team": canon, "key": normalize_team_key(canon)}
            return hit
    return None


def _blowout_scale(sp_diff: float, calib: dict[str, Any]) -> float:
    abs_diff = abs(sp_diff)
    threshold = calib.get("blowout_threshold", 25)
    k = calib.get("blowout_k", 0)
    if abs_diff <= threshold:
        return 1.0
    return 1 + k * ((abs_diff - threshold) / 12) ** 1.1


def price_game_from_sp(
    home_name: str,
    away_name: str,
    *,
    neutral: bool = False,
    season: int | None = None,
    display_week: int | None = None,
    refresh: bool = True,
) -> dict[str, Any] | None:
    from .team_registry import resolve_canonical

    _ensure_sp_loaded(season, display_week, refresh=refresh)
    calib = _load_calib()
    home_name = resolve_canonical(home_name) or home_name
    away_name = resolve_canonical(away_name) or away_name
    home = lookup_sp_team(home_name, season=season, display_week=display_week)
    away = lookup_sp_team(away_name, season=season, display_week=display_week)
    if not home or not away:
        return None

    sp_to_margin = calib.get("sp_to_margin", 0.87)
    total_sp_slope = calib.get("total_sp_sum_slope", -0.05)
    total_base = calib.get("total_base", LEAGUE_AVG_PTS * 2)
    margin_sigma = calib.get("margin_sigma", DEFAULT_MARGIN_SIGMA)

    home_sp = float(home.get("sp", 0))
    away_sp = float(away.get("sp", 0))
    home_off = float(home.get("spOff", home_sp * 0.4))
    away_off = float(away.get("spOff", away_sp * 0.4))
    home_def = float(home.get("spDef", -home_sp * 0.4))
    away_def = float(away.get("spDef", -away_sp * 0.4))
    venue_hfa = 0 if neutral else float(home.get("trueHfa", DEFAULT_HFA))

    sp_diff = home_sp - away_sp
    scale = _blowout_scale(sp_diff, calib)
    margin = sp_diff * sp_to_margin * scale + venue_hfa
    spread = _round_half(-margin)

    sp_sum = home_sp + away_sp
    off_sum = home_off + away_off
    def_sum = home_def + away_def
    total = total_base + sp_sum * total_sp_slope
    abs_margin = abs(margin)
    if abs_margin >= 24 and off_sum < 0 and def_sum > -4:
        total -= min(6.0, 0.12 * (abs_margin - 24) + 0.2 * max(0.0, -off_sum))
    home_pts, away_pts = _enforce_min_team_scores((total + margin) / 2, (total - margin) / 2)

    total_line = _round_half(total)
    away_score, home_score = realistic_team_scores(
        home_pts, away_pts, spread=spread, total=total_line
    )
    fair_spread = abs(spread)
    p_home_cover = _skellam_home_cover(away_pts, home_pts, fair_spread)
    p_over = _poisson_over_prob(total_line, total)
    home_win = _home_win_from_margin(margin, margin_sigma)
    away_win = 1.0 - home_win

    return {
        "spread": spread,
        "total": total_line,
        "home_score": home_score,
        "away_score": away_score,
        "margin": _round_half(margin),
        "home_lambda": home_pts,
        "away_lambda": away_pts,
        "spread_market": {
            "line": spread,
            "home_cover_prob": _round_prob(p_home_cover),
            "away_cover_prob": _round_prob(1 - p_home_cover if p_home_cover is not None else None),
        },
        "total_market": {
            "line": total_line,
            "over_prob": _round_prob(p_over),
            "under_prob": _round_prob(1 - p_over if p_over is not None else None),
        },
        "moneyline": {
            "home_win_prob": _round_prob(home_win),
            "away_win_prob": _round_prob(away_win),
        },
    }


def _pin_fair(row: dict[str, Any], implied: float | None, ctx: MarketPriorContext | None = None) -> float | None:
    from .market_prior import pin_fair_from_row

    pin = pin_fair_from_row(row, implied)
    if pin is not None:
        return pin
    if ctx is not None:
        return ctx.game_pin_side(row)
    sharp = row.get("sharpEdge")
    if sharp is None or implied is None:
        return None
    try:
        return implied + float(sharp)
    except (TypeError, ValueError):
        return None


def _prior_prob(
    row: dict[str, Any],
    implied: float | None,
    *,
    ctx: MarketPriorContext | None = None,
) -> tuple[float | None, int, str]:
    prior, _, book_count, source = resolve_benter_prior(row, implied, ctx=ctx)
    return prior, book_count, source


def _enrich_game_row(
    row: dict[str, Any],
    sp: dict[str, Any],
    *,
    sim_count: int = DEFAULT_SIM_COUNT,
    prior_ctx: MarketPriorContext | None = None,
) -> dict[str, Any]:
    next_row = dict(row)
    market = str(row.get("market") or "")
    side = str(row.get("side") or "")
    implied = american_to_implied(row.get("price"))
    if row.get("implied") is not None:
        try:
            implied = float(row.get("implied"))
        except (TypeError, ValueError):
            pass

    model_prob: float | None = None
    game_row = {**row, "category": "game"}
    prior, book_count, prior_source = _prior_prob(game_row, implied, ctx=prior_ctx)

    if market == "Spread":
        line = sp.get("spread")
        if line is not None:
            next_row["modelProj"] = -line if side == "away_cover" else line
        p_home = (sp.get("spread_market") or {}).get("home_cover_prob")
        if p_home is not None:
            model_prob = float(1 - p_home) if side == "away_cover" else float(p_home)
    elif market == "Total":
        if sp.get("total") is not None:
            next_row["modelProj"] = sp["total"]
        p_over = (sp.get("total_market") or {}).get("over_prob")
        if p_over is not None:
            model_prob = float(1 - p_over) if side == "under" else float(p_over)
    elif market == "Moneyline":
        ml = sp.get("moneyline") or {}
        home_ml = ml.get("home_win_prob")
        away_ml = ml.get("away_win_prob")
        if side == "home_ml" and home_ml is not None:
            model_prob = float(home_ml)
            next_row["modelProj"] = model_prob
        elif side == "away_ml" and away_ml is not None:
            model_prob = float(away_ml)
            next_row["modelProj"] = model_prob

    blended: float | None = None
    if prior is not None and model_prob is not None:
        if market == "Moneyline":
            blended = ml_blend(prior, model_prob, sim_count=sim_count, book_count=book_count)
        else:
            benter = analyze_prop_benter(prior, model_prob, book_count=book_count, sim_count=sim_count)
            blended = benter.get("blended") if benter else None
            if benter:
                next_row["prior"] = benter.get("prior")

    if model_prob is not None:
        model_prob = min(0.92, max(0.08, model_prob))
        next_row["winProb"] = _round_prob(model_prob)
        next_row["projection"] = _round_prob(model_prob)

    metrics = fill_market_metrics(
        prior=prior,
        model_prob=model_prob,
        blended=blended,
        implied=implied,
        price=row.get("price"),
        pin_fair=_pin_fair(row, implied, prior_ctx),
    )
    next_row.update(metrics)
    next_row["priorSource"] = prior_source
    return next_row


def hydrate_slate_rows(
    rows: list[dict[str, Any]],
    *,
    sim_count: int = DEFAULT_SIM_COUNT,
    prior_ctx: MarketPriorContext | None = None,
    season: int | None = None,
    display_week: int | None = None,
) -> list[dict[str, Any]]:
    """Re-price slate rows with SP+ model + Benter blend (mirrors sp_slate_enrich.js)."""
    if not rows:
        return rows

    from .prop_reprice import reprice_prop_row

    prop_rows = [r for r in rows if str(r.get("category") or "") == "prop"]
    ctx = prior_ctx or MarketPriorContext.build(prop_df=pd.DataFrame(prop_rows) if prop_rows else None)
    priced: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []

    for row in rows:
        category = str(row.get("category") or "")
        if category == "prop":
            out.append(reprice_prop_row(row, sim_count=sim_count, prior_ctx=ctx))
            continue
        if category != "game" or not row.get("home") or not row.get("away"):
            out.append(row)
            continue

        key = f"{row['away']}|{row['home']}"
        if key not in priced:
            priced[key] = (
                price_game_from_sp(
                    row["home"],
                    row["away"],
                    season=season,
                    display_week=display_week,
                )
                or {}
            )

        sp = priced[key]
        if not sp:
            out.append(row)
            continue
        out.append(_enrich_game_row(row, sp, sim_count=sim_count, prior_ctx=ctx))
    return out


def slate_json_paths(*, year: int | None = None, week: int | None = None) -> list[Path]:
    from .sport_context import SPORT_NFL, get_sport, slate_cache_paths

    paths: list[Path] = []
    if year is not None and week is not None:
        cache = DATA_DIR / "onyx_slate_cache"
        for name in slate_cache_paths(year=year, week=week):
            paths.append(cache / name)
    if get_sport() == SPORT_NFL:
        return paths
    # Generic archived slates only when no display week is requested.
    if year is None or week is None:
        paths.extend(
            [
                PUBLIC_DIR / "onyx-labs-slate.json",
                PUBLIC_DIR / "football-labs-slate.json",
                DATA_DIR / "onyx_slate_cache" / "onyx_slate_2026_w1.json",
            ]
        )
    return paths


def read_local_slate(*, year: int | None = None, week: int | None = None) -> dict[str, Any] | None:
    for path in slate_json_paths(year=year, week=week):
        if not path.exists():
            continue
        try:
            slate = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not slate.get("rows"):
            continue
        if year is not None and slate.get("year") not in (None, year):
            continue
        if week is not None and slate.get("week") not in (None, week):
            continue
        return slate
    return None


def ensure_fresh_projections(slate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not slate or not slate.get("rows"):
        return slate
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        return slate
    from .sp_refresh import ensure_fresh_sp_ratings

    season = slate.get("year") or slate.get("season")
    week = slate.get("week")
    if season is not None and week is not None:
        ensure_fresh_sp_ratings(season=int(season), display_week=int(week))
    version = slate.get("projectionVersion")
    if version is not None and version >= PROJECTION_VERSION:
        return slate
    sim_count = int(slate.get("simulations") or DEFAULT_SIM_COUNT)
    rows = hydrate_slate_rows(
        slate["rows"],
        sim_count=sim_count,
        season=int(season) if season is not None else None,
        display_week=int(week) if week is not None else None,
    )
    updated = {
        **slate,
        "rows": rows,
        "rowCount": len(rows),
        "playCount": sum(1 for r in rows if r.get("play")),
        "projectionVersion": PROJECTION_VERSION,
    }
    from .csv_log import log_slate_rows

    log_slate_rows(
        pd.DataFrame(rows),
        source="sp_plus_reprice",
        year=int(season) if season is not None else None,
        week=int(week) if week is not None else None,
        dedupe=False,
    )
    return updated
