"""Re-price player props from season baselines (mirrors slate_rows.repricePropRow)."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from typing import Any

import pandas as pd

from .benter_blend import analyze_prop_benter, fill_market_metrics
from .config import DATA_DIR
from .market_prior import MarketPriorContext, pin_fair_from_row, resolve_benter_prior
from .odds_math import american_to_implied, ev_pct, implied_to_american
from .prop_pricing import (
    PROP_KEYS,
    analyze_prop_line,
    clamp_american,
    fair_pair,
    prop_key_from_row,
    side_win_prob,
)
from .prop_matchup import apply_prop_matchup_to_baseline
from .team_registry import teams_match

BASELINE_PATH = DATA_DIR / "cfbd_player_season_baselines.json"

BASELINE_FIELDS = {
    "pass_yds": "pass_yds_pg",
    "rush_yds": "rush_yds_pg",
    "rec_yds": "rec_yds_pg",
    "receptions": "receptions_pg",
    "pass_tds": "pass_tds_pg",
    "tds": "tds_pg",
}


def _normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _team_key(team: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(team or "").lower())


def _name_team_key(name: str, team: str) -> str:
    return f"{_normalize_name(name)}|{_team_key(team)}"


@lru_cache(maxsize=1)
def _load_baselines() -> dict[str, dict[str, Any]]:
    if not BASELINE_PATH.exists():
        return {}
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("players") or {}


def _sp_matchup_factor(
    team: str,
    opponent: str,
    prop: str,
    *,
    home: str | None = None,
    away: str | None = None,
) -> float:
    val = apply_prop_matchup_to_baseline(1.0, prop, team, opponent, home=home, away=away)
    return float(val) if val is not None else 1.0


def find_baseline_by_name(name: str) -> dict[str, Any] | None:
    players = _load_baselines()
    norm = _normalize_name(name)
    if not norm:
        return None
    best: dict[str, Any] | None = None
    best_score = -1.0
    for key, row in players.items():
        if not key.startswith(f"{norm}|"):
            continue
        gp = float(row.get("games") or 0)
        vol = sum(float(row.get(f) or 0) for f in BASELINE_FIELDS.values())
        score = gp * 10 + vol
        if score > best_score:
            best_score = score
            best = row
    return best


def _baseline_on_team(player: str, team: str | None) -> dict[str, Any] | None:
    if not team:
        return None
    return _load_baselines().get(_name_team_key(player, team))


def resolve_prop_team(row: dict[str, Any], *, skip_starters: bool = False) -> str | None:
    """Correct slate team tags using baselines, starters, and ESPN."""
    from .sport_context import SPORT_NFL, get_sport

    home = row.get("home")
    away = row.get("away")
    stored = row.get("team")
    player = str(row.get("player") or "")

    if get_sport() == SPORT_NFL:
        if not skip_starters:
            try:
                from .prop_starters import starter_for_player

                info = starter_for_player(
                    player,
                    home=str(home or ""),
                    away=str(away or ""),
                    team=str(stored or "") if stored else None,
                )
                if info and info.get("team"):
                    tm = str(info["team"])
                    if home and away:
                        if teams_match(tm, home) or teams_match(tm, away):
                            return tm
                    else:
                        return tm
            except Exception:
                pass

        try:
            from .player_gamelog import espn_team_for_player

            espn_team = espn_team_for_player(player, home, away)
            if espn_team:
                return espn_team
        except Exception:
            pass

        if stored and home and away and (teams_match(stored, home) or teams_match(stored, away)):
            return str(stored)
        return None

    prop_key = (prop_key_from_row(row) or "").lower()

    home_bl = _baseline_on_team(player, home)
    away_bl = _baseline_on_team(player, away)
    if home_bl and not away_bl:
        return str(home)
    if away_bl and not home_bl:
        return str(away)

    if not skip_starters:
        try:
            from .prop_starters import starter_for_player

            info = starter_for_player(
                player,
                home=str(home or ""),
                away=str(away or ""),
                team=str(stored or "") if stored else None,
            )
            if info and info.get("team"):
                tm = str(info["team"])
                if home and away:
                    if teams_match(tm, home) or teams_match(tm, away):
                        return tm
                else:
                    return tm
        except Exception:
            pass

        try:
            from .player_gamelog import espn_team_for_player

            espn_team = espn_team_for_player(player, home, away)
            if espn_team:
                return espn_team
        except Exception:
            pass

    try:
        line_f = float(row.get("line"))
    except (TypeError, ValueError):
        line_f = None

    field = BASELINE_FIELDS.get(prop_key)

    if home_bl and away_bl and field:
        h_val = float(home_bl.get(field) or 0)
        a_val = float(away_bl.get(field) or 0)
        return str(home if h_val >= a_val else away)

    prior = find_baseline_by_name(player)
    if prior and field:
        prior_team = str(prior.get("team") or "")
        if home and _team_key(prior_team) == _team_key(home):
            return str(home)
        if away and _team_key(prior_team) == _team_key(away):
            return str(away)

    if stored and home and away and line_f and field and prior:
        pg = float(prior.get(field) or 0)
        if pg < line_f * 0.45:
            if _team_key(stored) == _team_key(home):
                return str(away)
            if _team_key(stored) == _team_key(away):
                return str(home)

    if stored in (home, away):
        return str(stored)
    return str(away or home or stored or "")


def team_logo_for(row: dict[str, Any], team: str | None) -> str | None:
    from .team_logos import team_logo_url

    if not team:
        fb = row.get("teamLogo")
        return team_logo_url(None, fallback=str(fb or "")) or None
    home, away = row.get("home"), row.get("away")
    fallback = ""
    if home and _team_key(team) == _team_key(home):
        fallback = str(row.get("homeLogo") or row.get("home_logo") or "")
    elif away and _team_key(team) == _team_key(away):
        fallback = str(row.get("awayLogo") or row.get("away_logo") or "")
    url = team_logo_url(str(team), fallback=fallback)
    return url or None


def _gamelog_projection(player: str, team: str, prop_key: str) -> float | None:
    try:
        from .player_gamelog import athlete_id_for_player, fetch_gamelog_df_combined
        from .prop_median_projection import _robust_recent_rate

        aid = athlete_id_for_player(player, team)
        if not aid:
            return None
        df = fetch_gamelog_df_combined(aid)
        if df.empty:
            return None
        from .player_gamelog import gamelog_prop_series

        series = gamelog_prop_series(df, prop_key)
        if series is None:
            return None
        vals = [float(v) for v in series.astype(float).tail(10) if math.isfinite(float(v))]
        vals = [v for v in vals if v >= 0]
        if not vals:
            return None
        rate, _ = _robust_recent_rate(vals, prop_key)
        return round(rate, 1) if rate is not None else None
    except Exception:
        return None


def baseline_projection(
    player: str,
    team: str | None,
    prop_key: str,
    *,
    home: str | None = None,
    away: str | None = None,
    skip_gamelog: bool = False,
) -> float | None:
    from .sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        if not skip_gamelog:
            tm = team or home or away
            gl = _gamelog_projection(player, str(tm or ""), prop_key)
            if gl is not None:
                return gl
        return None

    players = _load_baselines()
    if not players:
        return None

    teams: list[str] = []
    if team:
        teams.append(team)
    else:
        if home:
            teams.append(home)
        if away and away not in teams:
            teams.append(away)

    field = BASELINE_FIELDS.get((prop_key or "").lower())
    if not field:
        return None

    for tm in teams:
        row = players.get(_name_team_key(player, tm))
        if not row or row.get(field) is None:
            continue
        val = float(row[field])
        if val <= 0:
            continue
        opp = away if _team_key(tm) == _team_key(home or "") else home
        factor = (
            _sp_matchup_factor(tm, opp or "", prop_key, home=home, away=away) if opp else 1.0
        )
        return round(val * factor, 1)

    # Name-only fallback — only when team unknown
    if not team:
        nkey = _normalize_name(player)
        for key, row in players.items():
            if key.startswith(f"{nkey}|") and row.get(field) is not None:
                val = float(row[field])
                if val > 0:
                    return round(val, 1)

    if team and not skip_gamelog:
        gl = _gamelog_projection(player, team, prop_key)
        if gl is not None and gl > 0:
            opp = away if _team_key(team) == _team_key(home or "") else home
            factor = _sp_matchup_factor(team, opp or "", prop_key, home=home, away=away) if opp else 1.0
            return round(gl * factor, 1)
    return None


def _line_tolerance(line_f: float) -> float:
    return max(35.0, 0.35 * abs(line_f))


def _projection_plausible(
    stored: float,
    *,
    line_f: float | None,
    prop_key: str,
    baseline: float | None,
    position: str,
) -> bool:
    """Reject corrupted or mis-keyed archived projections."""
    pk = (prop_key or "").lower()
    pos = (position or "").upper()

    if line_f is not None and math.isfinite(line_f):
        if abs(stored - line_f) > _line_tolerance(line_f):
            return False
        # Over side on a high line with tiny projection is almost always bad data.
        if line_f >= 40 and stored < line_f * 0.35:
            return False

    if baseline is not None and baseline > 0:
        # Reject only when stored is far below season baseline (corrupted / wrong prop).
        if stored < baseline * 0.45:
            return False

    if pk == "rush_yds" and pos in {"RB", "FB", "HB"}:
        floor = 20.0
        if baseline and baseline >= 30:
            floor = min(baseline * 0.5, baseline - 10)
        if stored < floor:
            return False

    if pk == "pass_yds" and pos == "QB":
        floor = 120.0
        if baseline and baseline >= 150:
            floor = baseline * 0.55
        if stored < floor:
            return False

    if pk == "rec_yds" and pos in {"WR", "TE", "RB"}:
        if line_f and line_f >= 20 and stored < line_f * 0.35:
            return False

    return True


def _stored_projection(row: dict[str, Any]) -> float | None:
    """Archived pre-game model projection when plausible vs line and role."""
    raw: float | None = None
    for key in ("modelProj", "projection"):
        try:
            val = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(val) and val >= 0:
            raw = round(val, 1)
            break
    if raw is None:
        return None

    prop_key = prop_key_from_row(row) or ""
    line_f: float | None = None
    try:
        line_f = float(row.get("line"))
        if not math.isfinite(line_f):
            line_f = None
    except (TypeError, ValueError):
        line_f = None

    team = resolve_prop_team(row)
    baseline = None
    if prop_key:
        baseline = baseline_projection(
            str(row.get("player") or ""),
            team,
            prop_key,
            home=row.get("home"),
            away=row.get("away"),
        )

    position = str(row.get("position") or "")
    if not _projection_plausible(
        raw,
        line_f=line_f,
        prop_key=prop_key,
        baseline=baseline,
        position=position,
    ):
        return None
    return raw


def reprice_prop_row(
    row: dict[str, Any],
    *,
    sim_count: int = 500,
    prior_ctx: MarketPriorContext | None = None,
    skip_gamelog: bool = False,
    skip_starters: bool = False,
) -> dict[str, Any]:
    out = dict(row)
    prop_key = prop_key_from_row(row)
    if not prop_key or prop_key not in PROP_KEYS:
        return out

    line = row.get("line")
    try:
        line_f = float(line)
    except (TypeError, ValueError):
        return out

    team = resolve_prop_team(row, skip_starters=skip_starters)
    if team:
        out["team"] = team
        logo = team_logo_for(row, team)
        if logo:
            out["teamLogo"] = logo
        bl = _baseline_on_team(str(row.get("player") or ""), team)
        if bl and bl.get("position"):
            out["position"] = bl.get("position")

    if not skip_starters:
        try:
            from .player_gamelog import athlete_id_for_player

            aid = athlete_id_for_player(str(row.get("player") or ""), team)
            if aid:
                out["espn_id"] = aid
        except Exception:
            pass

    projection = _stored_projection(row)
    if projection is None:
        from .prop_median_projection import median_matchup_projection

        projection = median_matchup_projection(
            row,
            skip_gamelog=skip_gamelog,
            skip_starters=skip_starters,
        )
    if projection is None:
        projection = baseline_projection(
            str(row.get("player") or ""),
            team,
            prop_key,
            home=row.get("home"),
            away=row.get("away"),
            skip_gamelog=skip_gamelog,
        )
    if projection is None:
        return out

    side = str(row.get("side") or "")
    over_price = row.get("price") if "over" in side.lower() else None
    priced = analyze_prop_line(
        projection=projection,
        line=line_f,
        prop_key=prop_key,
        position=str(row.get("position") or ""),
        over_price=over_price,
    )
    if not priced:
        return out

    over_pct = priced.get("over_pct")
    win_prob = side_win_prob(over_pct, side)
    if win_prob is None:
        return out

    win_prob = min(0.92, max(0.08, float(win_prob)))

    implied = american_to_implied(row.get("price"))
    if row.get("implied") is not None:
        try:
            implied = float(row.get("implied"))
        except (TypeError, ValueError):
            pass

    prior, pin_fair, book_count, prior_source = resolve_benter_prior(
        {**row, "category": "prop"},
        implied,
        ctx=prior_ctx,
    )
    if pin_fair is None:
        pin_fair = pin_fair_from_row(row, implied)

    benter = (
        analyze_prop_benter(prior, win_prob, book_count=book_count, sim_count=sim_count)
        if prior is not None
        else None
    )
    blended = benter.get("blended") if benter else win_prob
    fair_prob = blended if blended is not None else win_prob
    fair = clamp_american(implied_to_american(fair_prob))

    sharp = row.get("sharpEdge")
    if pin_fair is None and sharp is not None and implied is not None:
        try:
            pin_fair = implied + float(sharp)
        except (TypeError, ValueError):
            pin_fair = None

    metrics = fill_market_metrics(
        prior=prior,
        model_prob=win_prob,
        blended=blended,
        implied=implied,
        price=row.get("price"),
        pin_fair=pin_fair,
    )

    out.update(
        {
            "propKey": prop_key,
            "modelProj": projection,
            "projection": projection,
            "winProb": win_prob,
            "overProb": over_pct,
            "fairPrice": fair,
            "ev_pct": ev_pct(blended, row.get("price")),
            "priorSource": prior_source,
            **metrics,
        }
    )
    fair_over, fair_under = fair_pair(over_pct)
    out["fairOver"] = fair_over
    out["fairUnder"] = fair_under
    return out


def reprice_props_df(
    df: pd.DataFrame,
    *,
    sim_count: int = 500,
    prior_ctx: MarketPriorContext | None = None,
    skip_gamelog: bool = False,
    skip_starters: bool = False,
) -> pd.DataFrame:
    if df.empty:
        return df
    ctx = prior_ctx or MarketPriorContext.build(prop_df=df)
    rows = [
        reprice_prop_row(
            r.to_dict(),
            sim_count=sim_count,
            prior_ctx=ctx,
            skip_gamelog=skip_gamelog,
            skip_starters=skip_starters,
        )
        for _, r in df.iterrows()
    ]
    return pd.DataFrame(rows)
