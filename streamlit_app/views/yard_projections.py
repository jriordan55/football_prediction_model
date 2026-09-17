"""Yard Projections — all DK team yard alt lines in one table."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from lib.app_filters import render_global_filters
from lib.bcftoys_ypp import lookup_ypp
from lib.cfbd_games import list_games_for_display_week
from lib.game_odds_quotes import load_board_odds_df
from lib.odds_client import market_label
from lib.player_props_board_cache import load_player_props_board
from lib.sport_context import SPORT_CFB, cache_sport, get_sport
from lib.styling import callout, section_header
from lib.team_registry import resolve_canonical, teams_match
from lib.team_yards_odds import (
    load_week_team_yards_odds,
    yard_odds_fetch_status,
    yards_cache_exists,
)
from lib.yard_projections import (
    build_matchup_yard_projections,
    enrich_alt_with_model_price,
    format_american_odds,
    format_dk_milestone_line,
    model_fair_american,
)

TAB = "Yard Projections"
TABLE_COLUMNS = ["Team", "Market", "Projection", "Sportsbook Line", "Odds", "Edge %"]


def _alt_at_line(alt_by_line: dict[float, dict], line: float) -> dict:
    if line in alt_by_line:
        return alt_by_line[line]
    for ln, row in alt_by_line.items():
        if abs(float(ln) - line) < 0.01:
            return row
    return {}


def _game_lines(home: str, away: str, odds_df: pd.DataFrame) -> tuple[float | None, float | None]:
    if odds_df.empty:
        return None, None
    home_c, away_c = resolve_canonical(home) or home, resolve_canonical(away) or away
    game = odds_df[
        odds_df.apply(
            lambda r: teams_match(str(r.get("home")), home_c) and teams_match(str(r.get("away")), away_c),
            axis=1,
        )
    ]
    if game.empty:
        return None, None
    spread = total = None
    dk = game[game["book_id"].astype(str).str.lower().eq("draftkings")]
    use = dk if not dk.empty else game
    spreads = use[use["market_key"].astype(str).str.lower().eq("spreads")]
    totals = use[use["market_key"].astype(str).str.lower().eq("totals")]
    for _, r in spreads.iterrows():
        if teams_match(str(r.get("description") or r.get("selection") or ""), home_c):
            spread = float(r.get("line"))
            break
    over = totals[totals["selection"].astype(str).str.lower().str.contains("over", na=False)]
    if not over.empty:
        total = float(over.iloc[0].get("line"))
    return spread, total


def _cfbd_meta_by_matchup(year: int, week: int) -> dict[tuple[str, str], dict]:
    meta: dict[tuple[str, str], dict] = {}
    for g in list_games_for_display_week(year, week):
        home = resolve_canonical(g.get("home") or "") or g.get("home")
        away = resolve_canonical(g.get("away") or "") or g.get("away")
        if home and away:
            meta[(str(away), str(home))] = g
    return meta


def _projection_lookup(proj: dict) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for side_key in ("home", "away"):
        side = proj.get(side_key) or {}
        team = resolve_canonical(side.get("team") or "") or side.get("team") or ""
        for mk, mproj in (side.get("markets") or {}).items():
            alt_by_line: dict[float, dict] = {}
            for alt in mproj.get("alt_lines") or []:
                try:
                    alt_by_line[float(alt["line"])] = alt
                except (TypeError, ValueError, KeyError):
                    continue
            out[(str(team), str(mk))] = {
                "median": mproj.get("median"),
                "sd": mproj.get("sd"),
                "label": mproj.get("market_label") or market_label(mk),
                "alts": alt_by_line,
            }
    return out


def _find_projection(
    lookup: dict[tuple[str, str], dict],
    team: str,
    market_key: str,
) -> dict | None:
    team_c = resolve_canonical(team) or team
    direct = lookup.get((team_c, market_key))
    if direct:
        return direct
    for (t, mk), val in lookup.items():
        if mk == market_key and teams_match(t, team):
            return val
    return None


def _rows_from_pkg(
    pkg: dict,
    *,
    year: int,
    cfbd_meta: dict[tuple[str, str], dict],
    odds_df: pd.DataFrame,
    props_df: pd.DataFrame,
) -> list[dict]:
    teams_data = pkg.get("teams") or {}
    if not teams_data:
        return []

    home = str(pkg.get("home") or "")
    away = str(pkg.get("away") or "")
    meta = None
    for (a, h), g in cfbd_meta.items():
        if teams_match(a, away) and teams_match(h, home):
            meta = g
            break
    if meta and meta.get("completed"):
        return []

    spread, total = _game_lines(home, away, odds_df)
    try:
        proj = build_matchup_yard_projections(
            home,
            away,
            spread=spread,
            total=total,
            odds_pkg=pkg,
            props_df=props_df,
            kickoff=(meta or {}).get("startDate"),
            venue=(meta or {}).get("venue"),
            year=year,
        )
    except Exception:
        return []

    lookup = _projection_lookup(proj)
    rows: list[dict] = []
    for _tname, tdata in teams_data.items():
        team = resolve_canonical(tdata.get("team") or _tname) or _tname
        for mk, mdata in (tdata.get("markets") or {}).items():
            pinfo = _find_projection(lookup, team, str(mk))
            market_label_str = (pinfo or {}).get("label") or market_label(mk)
            alt_by_line = (pinfo or {}).get("alts") or {}
            median = (pinfo or {}).get("median")
            sd = (pinfo or {}).get("sd")
            for alt in mdata.get("alt_lines") or []:
                try:
                    line = float(alt["line"])
                except (TypeError, ValueError, KeyError):
                    continue
                book_over = alt.get("over_price")
                modeled = _alt_at_line(alt_by_line, line)
                if not modeled and median is not None and sd is not None:
                    modeled = enrich_alt_with_model_price(
                        alt,
                        median=float(median),
                        sd=float(sd),
                    )
                model_price = modeled.get("model_price")
                if not model_price or (
                    isinstance(model_price, str)
                    and model_price.replace("+", "").replace("-", "").isdigit()
                    and abs(int(model_price.replace("+", ""))) > 800
                ):
                    model_price = model_fair_american(modeled.get("model_over_prob"))
                rows.append(
                    {
                        "Team": team,
                        "Market": market_label_str,
                        "Projection": format_american_odds(model_price),
                        "Sportsbook Line": format_dk_milestone_line(line),
                        "Odds": format_american_odds(book_over),
                        "Edge %": modeled.get("edge_ev"),
                    }
                )
    return rows


def _yards_table_from_odds(
    yards_odds: list[dict],
    *,
    year: int,
    week: int,
    sport: str,
) -> pd.DataFrame:
    if not yards_odds:
        return pd.DataFrame(columns=TABLE_COLUMNS)

    cfbd_meta = _cfbd_meta_by_matchup(year, week)
    odds_df = load_board_odds_df(tab=TAB)
    props_df = load_player_props_board(sport, year, week)

    rows: list[dict] = []
    for pkg in yards_odds:
        rows.extend(
            _rows_from_pkg(
                pkg,
                year=year,
                cfbd_meta=cfbd_meta,
                odds_df=odds_df,
                props_df=props_df,
            )
        )

    if not rows:
        return pd.DataFrame(columns=TABLE_COLUMNS)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["Team", "Market", "Sportsbook Line"], keep="first")
    return df.sort_values(
        ["Edge %", "Team", "Market", "Sportsbook Line"],
        ascending=[False, True, True, True],
        na_position="last",
    ).reset_index(drop=True)


def _load_yards_table(
    sport: str,
    year: int,
    week: int,
    *,
    refresh: bool = False,
) -> tuple[pd.DataFrame, list[dict], dict[str, str | None]]:
    if sport != SPORT_CFB:
        return pd.DataFrame(columns=TABLE_COLUMNS), [], {"mode": "n/a", "error": None}

    cfbd_games = list_games_for_display_week(year, week)
    yards_odds = load_week_team_yards_odds(
        year, week, matchups=cfbd_games, tab=TAB, refresh=refresh
    )
    status = yard_odds_fetch_status()
    table = _yards_table_from_odds(yards_odds, year=year, week=week, sport=sport)
    return table, yards_odds, status


def render() -> None:
    sport = get_sport()
    if sport != SPORT_CFB:
        callout("Yard Projections are available for College Football only.", "info")
        return

    year, week = render_global_filters(prefix="yp")
    section_header(
        "Yard Projections",
        "Model fair price at each DK milestone vs posted odds — edge is EV% on the over.",
    )

    refresh = False
    if st.button("Refresh yards odds", key="yp_refresh"):
        refresh = True

    spinner_msg = "Scraping DraftKings (~90s)…" if refresh else "Loading DK team yard markets…"
    with st.spinner(spinner_msg):
        table, yards_odds, status = _load_yards_table(
            cache_sport(), int(year), int(week), refresh=refresh
        )

    if table.empty:
        msg = (
            "No DraftKings team yard alt lines found for this week. "
            "Click **Refresh yards odds** to scrape DraftKings (~90 seconds)."
        )
        if yards_odds:
            msg += f"\n\nLoaded **{len(yards_odds)}** DK games from cache but built 0 table rows."
        elif yards_cache_exists(int(year), int(week)):
            msg += "\n\nCache file exists but returned no games — try **Refresh yards odds**."
        mode = status.get("mode") or "unknown"
        err = status.get("error")
        if mode == "failed" or err:
            msg += f"\n\nFetch failed: **{err or mode}**."
            msg += (
                "\nRequires Playwright + Edge/Chrome: "
                "`pip install playwright` then restart Streamlit."
            )
        callout(msg, "warn")
        if lookup_ypp("Ohio State"):
            st.caption("BCF Toys YPP is loaded — waiting on DK yard markets.")
        return

    n_teams = table["Team"].nunique()
    n_markets = table["Market"].nunique()
    src = status.get("mode") or "loaded"
    st.caption(
        f"{len(table)} lines · {n_teams} teams · {n_markets} markets · "
        f"Projection = model fair American odds at that line · Edge = EV% · source: {src}"
    )

    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Edge %": st.column_config.NumberColumn(format="%+.1f"),
        },
    )
