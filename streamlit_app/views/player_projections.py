"""Player Projections — full Onyx prop board with Benter blend."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import pandas as pd

from lib.app_filters import render_global_filters
from lib.config import DATA_DIR
from lib.display import render_player_performance_panel, render_props_board
from lib.espn_client import fetch_scoreboard_cached
from lib.odds_client import fetch_onyx_df, props_df
from lib.matchup_prop_enrich import is_real_market_prop, resolve_prop_projection
from lib.prop_board import filter_main_prop_lines, filter_nfl_player_props, filter_primary_player_props
from lib.prop_board_enrich import enrich_prop_board, ensure_team_column
from lib.prop_pricing import (
    is_combo_prop_market,
    normalize_prop_market,
    normalize_onyx_player,
    prop_key_from_row,
    sync_market_fields,
)
from lib.prop_reprice import reprice_props_df
from lib.prop_starters import enrich_starter_metadata, is_projected_starter
from lib.slate_loader import load_slate_df
from lib.sport_context import cache_sport
from lib.styling import callout, section_header
from lib.team_registry import teams_match

TAB = "Player Projections"
SEL_ROW_KEY = "pp_selected_row"
BOARD_CACHE_DIR = DATA_DIR / "odds_api_cache"
BOARD_CACHE_VERSION = 14


def _board_cache_path(sport: str, year: int, week: int) -> Path:
    from lib.sport_context import get_sport_config

    tag = get_sport_config(sport)["odds_cache_tag"]
    return BOARD_CACHE_DIR / f"player_props_board_{tag}_{year}_w{week}_v{BOARD_CACHE_VERSION}.json"


def _load_board_disk_cache(sport: str, year: int, week: int) -> pd.DataFrame:
    path = _board_cache_path(sport, year, week)
    if not path.exists():
        return pd.DataFrame()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return pd.DataFrame()


def _board_cache_updated_at(sport: str, year: int, week: int) -> datetime | None:
    path = _board_cache_path(sport, year, week)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated = payload.get("updatedAt")
        if not updated:
            return None
        ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return None


def _format_cache_age(ts: datetime | None) -> str:
    if ts is None:
        return "not cached yet"
    age_min = max(0, int((datetime.now(timezone.utc) - ts).total_seconds() // 60))
    if age_min < 1:
        return "just now"
    if age_min < 60:
        return f"{age_min} min ago"
    hours = age_min // 60
    return f"{hours}h ago" if hours < 48 else ts.astimezone().strftime("%b %d · %I:%M %p")


def _save_board_disk_cache(df: pd.DataFrame, sport: str, year: int, week: int) -> None:
    if df.empty:
        return
    path = _board_cache_path(sport, year, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rows": df.to_dict(orient="records"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(df),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _row_pick_label(row: dict) -> str:
    player = str(row.get("player") or "Player")
    prop = str(row.get("prop_label") or row.get("market") or "Prop")
    try:
        line = f"{float(row.get('line')):g}"
    except (TypeError, ValueError):
        line = "—"
    return f"{player} · {prop} · {line}"


def _resolve_selected_row(view: pd.DataFrame) -> dict | None:
    """Match session selection to a visible board row."""
    from lib.display import _prop_row_is_active

    sel = st.session_state.get(SEL_ROW_KEY)
    if not isinstance(sel, dict) or not sel.get("player"):
        pick = st.session_state.get("pp_row_select")
        if pick:
            for _, row in view.iterrows():
                if _row_pick_label(row.to_dict()) == pick:
                    sel = {
                        "player": row.get("player"),
                        "propKey": prop_key_from_row(row.to_dict()) or "",
                        "line": row.get("line"),
                    }
                    st.session_state[SEL_ROW_KEY] = sel
                    break
    if not isinstance(sel, dict) or not sel.get("player"):
        return None

    for _, row in view.iterrows():
        if _prop_row_is_active(row, selected_row=sel):
            return row.to_dict()
    return None


def _enrich_team_logos(df: pd.DataFrame, year: int, week: int) -> pd.DataFrame:
    if df.empty:
        return df
    sb = fetch_scoreboard_cached(week=max(int(week), 1), year=int(year), tab=TAB)
    if sb.empty:
        return df

    logo_map: dict[str, str] = {}
    for _, g in sb.iterrows():
        for side in ("home", "away"):
            name = str(g.get(side) or "")
            logo = str(g.get(f"{side}_logo") or "")
            if name and logo and logo.lower() != "nan":
                logo_map[name.lower()] = logo

    out = df.copy()
    home_logos: list[str | None] = []
    away_logos: list[str | None] = []
    logos: list[str | None] = []
    for _, row in out.iterrows():
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        hl = None
        al = None
        for candidate, side in ((home, "home"), (away, "away")):
            if candidate.lower() in logo_map:
                if side == "home":
                    hl = logo_map[candidate.lower()]
                else:
                    al = logo_map[candidate.lower()]
        if not hl and home:
            for gname, glogo in logo_map.items():
                if teams_match(home, gname):
                    hl = glogo
                    break
        if not al and away:
            for gname, glogo in logo_map.items():
                if teams_match(away, gname):
                    al = glogo
                    break
        home_logos.append(hl)
        away_logos.append(al)

        team = str(row.get("team") or row.get("teamLabel") or "").strip()
        found = None
        if team:
            existing = str(row.get("teamLogo") or "").strip()
            if existing and existing.lower() not in ("nan", "none"):
                found = existing
            elif teams_match(team, home) and hl:
                found = hl
            elif teams_match(team, away) and al:
                found = al
            else:
                for gname, glogo in logo_map.items():
                    if teams_match(team, gname):
                        found = glogo
                        break
        logos.append(found)
    out["homeLogo"] = home_logos
    out["awayLogo"] = away_logos
    out["teamLogo"] = logos

    opp_logos: list[str | None] = []
    opponents: list[str] = []
    for _, row in out.iterrows():
        team = str(row.get("team") or "").strip()
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        hl = row.get("homeLogo")
        al = row.get("awayLogo")
        opp = str(row.get("opponent") or "").strip()
        opp_logo = str(row.get("opponentLogo") or "").strip() or None
        if team and home and away:
            if teams_match(team, home):
                opp = opp or away
                opp_logo = opp_logo or al
            elif teams_match(team, away):
                opp = opp or home
                opp_logo = opp_logo or hl
        opponents.append(opp)
        opp_logos.append(opp_logo if opp_logo and str(opp_logo).lower() not in ("nan", "none") else None)
    out["opponent"] = opponents
    out["opponentLogo"] = opp_logos
    return out


def _prop_row_priority(row: dict) -> int:
    score = 0
    if row.get("group") == "prop":
        score += 20
    if row.get("modelProj") is not None:
        score += 10
    if row.get("edgeDisplay") is not None or row.get("ev_pct") is not None:
        score += 2
    if prop_key_from_row(row):
        score += 5
    return score


def _prepare_prop_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    from lib.prop_pricing import sync_market_fields

    out = df.copy()
    if "player" in out.columns:
        out["player"] = out["player"].map(normalize_onyx_player)
    synced = [sync_market_fields(r.to_dict()) for _, r in out.iterrows()]
    out = pd.DataFrame(synced)
    if "market" in out.columns:
        out["market"] = out["market"].map(normalize_prop_market)
    out["_prop_key"] = out.apply(lambda r: prop_key_from_row(r.to_dict()), axis=1)
    out = out[out.apply(lambda r: is_real_market_prop(r.to_dict()), axis=1)].copy()
    return filter_main_prop_lines(out)


def _dedupe_prop_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    dedupe_cols = [c for c in ["player", "market", "line", "side", "home", "away"] if c in df.columns]
    if not dedupe_cols:
        return df
    out = df.copy()
    out["_prio"] = out.apply(lambda r: -_prop_row_priority(r.to_dict()), axis=1)
    out = out.sort_values("_prio").drop_duplicates(subset=dedupe_cols, keep="first")
    return out.drop(columns=["_prio", "_prop_key"], errors="ignore")


def _enrich_espn_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Attach ESPN athlete ids from baselines only — panel resolves missing ids on click."""
    if df.empty:
        return df
    from lib.prop_reprice import _load_baselines, _name_team_key

    baselines = _load_baselines()
    out = df.copy()
    ids: list[str | None] = []
    for _, row in out.iterrows():
        player = str(row.get("player") or "")
        team = str(row.get("team") or row.get("teamLabel") or "")
        bl = baselines.get(_name_team_key(player, team))
        aid = str(bl.get("espn_id")) if bl and bl.get("espn_id") else None
        ids.append(aid)
    out["espn_id"] = ids
    return out


def _refresh_matchup_projections(
    df: pd.DataFrame,
    *,
    skip_gamelog: bool = False,
    skip_starters: bool = True,
) -> pd.DataFrame:
    """Recompute Mine from current opponent/script logic instead of archived slate values."""
    if df.empty:
        return df
    from concurrent.futures import ThreadPoolExecutor
    from lib.prop_median_projection import median_matchup_projection

    def _apply(row_dict: dict) -> dict:
        d = dict(row_dict)
        proj = median_matchup_projection(
            d,
            skip_gamelog=skip_gamelog,
            skip_starters=skip_starters,
        )
        if proj is not None:
            d["modelProj"] = proj
            d["projection"] = proj
        return d

    row_dicts = [row.to_dict() for _, row in df.iterrows()]
    workers = min(12, max(4, len(row_dicts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        updated = list(pool.map(_apply, row_dicts))
    return pd.DataFrame(updated).reset_index(drop=True)


def _prop_rows_from_slate(slate: pd.DataFrame) -> pd.DataFrame:
    if slate.empty:
        return slate
    prop_mask = slate["player"].notna() & (slate["player"].astype(str).str.len() > 1)
    prop_mask |= slate["group"].astype(str).eq("prop")
    return slate[prop_mask].copy()


def _live_onyx_props_for_week(year: int, week: int) -> pd.DataFrame:
    """Pull live Onyx player props and restrict to the display week's matchups."""
    from lib.games import list_games_for_display_week
    from lib.slate_loader import _filter_slate_to_games

    live = props_df(fetch_onyx_df(tab=TAB))
    if live.empty:
        return live
    live = _prepare_prop_frame(live)
    if live.empty:
        return live
    games = list_games_for_display_week(int(year), int(week))
    if games:
        live = _filter_slate_to_games(live, games)
    return live


def _drop_unmapped_nfl_props(df: pd.DataFrame) -> pd.DataFrame:
    """Remove NFL rows where the player cannot be tied to home or away."""
    from lib.sport_context import SPORT_NFL, get_sport

    if df.empty or get_sport() != SPORT_NFL:
        return df
    keep: list[pd.Series] = []
    for _, row in df.iterrows():
        team = str(row.get("team") or "").strip()
        home = str(row.get("home") or "")
        away = str(row.get("away") or "")
        if team and home and away and (teams_match(team, home) or teams_match(team, away)):
            keep.append(row)
    if not keep:
        return pd.DataFrame()
    return pd.DataFrame(keep).reset_index(drop=True)


def _finalize_prop_board(
    df: pd.DataFrame,
    *,
    sport: str,
    year: int,
    week: int,
    skip_gamelog: bool = True,
) -> pd.DataFrame:
    """Reprice, enrich, and cache a prop frame for display."""
    from lib.projection_archive import persist_prop_board

    if df.empty:
        return df
    df = _dedupe_prop_rows(df)
    repriced = reprice_props_df(
        df, sim_count=150, skip_gamelog=skip_gamelog, skip_starters=True,
    )
    repriced = enrich_starter_metadata(repriced)
    repriced = repriced[
        repriced.apply(lambda r: resolve_prop_projection(r.to_dict()) is not None, axis=1)
    ].copy()
    if repriced.empty:
        return pd.DataFrame()

    repriced["is_starter"] = repriced.apply(
        lambda r: is_projected_starter(
            str(r.get("player") or ""),
            home=str(r.get("home") or ""),
            away=str(r.get("away") or ""),
            team=str(r.get("team") or ""),
        ),
        axis=1,
    )
    if "ev_pct" not in repriced.columns or repriced["ev_pct"].isna().all():
        if "roi" in repriced.columns:
            repriced["ev_pct"] = (pd.to_numeric(repriced["roi"], errors="coerce") * 100).round(1)
        else:
            repriced["ev_pct"] = pd.to_numeric(repriced.get("edgeDisplay"), errors="coerce")
    repriced = repriced.sort_values(["is_starter", "ev_pct"], ascending=[False, False])

    enriched = _enrich_team_logos(repriced, year, week)
    out = filter_primary_player_props(enriched)
    out = _stamp_slate_context(out, year, week)
    out = _refresh_matchup_projections(out, skip_gamelog=False, skip_starters=True)
    out = _sanitize_prop_board(out, year, week)
    out = _enrich_espn_ids(out)
    out = out.drop(columns=["is_starter"], errors="ignore")
    out = persist_prop_board(out, year=int(year), week=int(week), tab=TAB)
    _save_board_disk_cache(out, sport, int(year), int(week))
    return out


def _stamp_slate_context(df: pd.DataFrame, year: int, week: int) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["year"] = int(year)
    out["week"] = int(week)
    return out


def _light_prepare_board(df: pd.DataFrame) -> pd.DataFrame:
    """Fast path for cached boards — no API calls or repricing."""
    if df.empty:
        return df
    out = ensure_team_column(df.copy())
    if "propKey" not in out.columns:
        out["propKey"] = out.apply(lambda r: prop_key_from_row(r.to_dict()) or "", axis=1)
    if "ev_pct" not in out.columns or out["ev_pct"].isna().all():
        if "roi" in out.columns:
            out["ev_pct"] = (pd.to_numeric(out["roi"], errors="coerce") * 100).round(1)
        else:
            out["ev_pct"] = pd.to_numeric(out.get("edgeDisplay"), errors="coerce")
    return out


def _sanitize_prop_board(df: pd.DataFrame, year: int, week: int) -> pd.DataFrame:
    """Drop combo markets, sync labels from market_key, refresh logos + opponent SP+."""
    if df.empty:
        return df

    rows = [sync_market_fields(r.to_dict()) for _, r in df.iterrows()]
    out = pd.DataFrame(rows)
    out = out[~out.apply(lambda r: is_combo_prop_market(r.to_dict()), axis=1)].copy()
    if out.empty:
        return out

    if "market" in out.columns:
        out["market"] = out["market"].map(normalize_prop_market)
    out = out[out.apply(lambda r: is_real_market_prop(r.to_dict()), axis=1)].copy()
    if out.empty:
        return out

    out = _stamp_slate_context(out, year, week)
    out = enrich_prop_board(out)
    out = ensure_team_column(out)
    out = _drop_unmapped_nfl_props(out)
    out = _enrich_team_logos(out, year, week)
    if "propKey" not in out.columns:
        out["propKey"] = out.apply(lambda r: prop_key_from_row(r.to_dict()) or "", axis=1)
    return ensure_team_column(out.reset_index(drop=True))


def load_cached_player_props(sport: str, year: int, week: int) -> pd.DataFrame:
    """Instant load from disk — no repricing or live API calls."""
    cached = _load_board_disk_cache(sport, int(year), int(week))
    if not cached.empty:
        return _light_prepare_board(cached)

    from lib.games import display_week_complete
    from lib.projection_archive import load_graded_props_df, props_have_results

    if display_week_complete(int(year), int(week), sport=sport) or props_have_results(int(year), int(week)):
        graded = load_graded_props_df(int(year), int(week))
        if not graded.empty:
            out = _light_prepare_board(graded)
            _save_board_disk_cache(out, sport, int(year), int(week))
            return out
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def build_player_props_board(
    sport: str,
    year: int,
    week: int,
    *,
    board_version: int = BOARD_CACHE_VERSION,
) -> pd.DataFrame:
    """Full rebuild — live odds, repricing, enrichment. Use only on refresh or first load."""
    _ = board_version
    from lib.projection_archive import persist_prop_board, load_graded_props_df
    from lib.sport_context import SPORT_NFL

    if sport == SPORT_NFL:
        graded = load_graded_props_df(int(year), int(week))
        if not graded.empty:
            out = _sanitize_prop_board(graded, int(year), int(week))
            out = _enrich_espn_ids(out)
            return out.sort_values("expected_roi", ascending=False, na_position="last")

        from lib.nfl_draftkings_props import load_nfl_draftkings_props

        dk = load_nfl_draftkings_props(sport, int(year), int(week))
        if dk.empty:
            return pd.DataFrame()
        df = _prepare_prop_frame(dk)
        if df.empty:
            return df
        df = _dedupe_prop_rows(df)
        repriced = reprice_props_df(
            df, sim_count=150, skip_gamelog=True, skip_starters=True,
        )
        if repriced.empty:
            return pd.DataFrame()
        out = filter_nfl_player_props(repriced)
        out = _stamp_slate_context(out, year, week)
        out = enrich_prop_board(out)
        out = ensure_team_column(out)
        out = _drop_unmapped_nfl_props(out)
        out = _enrich_team_logos(out, year, week)
        out = _refresh_matchup_projections(out, skip_gamelog=False, skip_starters=True)
        out = _enrich_espn_ids(out)
        if "propKey" not in out.columns:
            out["propKey"] = out.apply(lambda r: prop_key_from_row(r.to_dict()) or "", axis=1)
        out = persist_prop_board(out, year=int(year), week=int(week), tab=TAB)
        _save_board_disk_cache(out, sport, int(year), int(week))
        return ensure_team_column(out)

    graded = load_graded_props_df(int(year), int(week))
    if not graded.empty:
        out = _sanitize_prop_board(graded, int(year), int(week))
        out = _enrich_espn_ids(out)
        return out.sort_values("expected_roi", ascending=False, na_position="last")

    slate_week = 1 if int(week) == 0 else int(week)
    slate = load_slate_df(cache_sport(), int(year), slate_week)
    df = _prop_rows_from_slate(slate)
    if not df.empty:
        df = _prepare_prop_frame(df)
    if df.empty:
        live = _live_onyx_props_for_week(int(year), int(week))
        return _finalize_prop_board(live, sport=sport, year=int(year), week=int(week))

    df = _dedupe_prop_rows(df)

    # Fast path: archived model + edge columns already on slate rows.
    has_model = "modelProj" in df.columns and df["modelProj"].notna().any()
    if has_model:
        df = df[df["modelProj"].notna()].copy()
        if "ev_pct" not in df.columns or df["ev_pct"].isna().all():
            if "edgeDisplay" in df.columns:
                df["ev_pct"] = pd.to_numeric(df["edgeDisplay"], errors="coerce")
            elif "roi" in df.columns:
                df["ev_pct"] = (pd.to_numeric(df["roi"], errors="coerce") * 100).round(1)
        out = filter_primary_player_props(df)
        out = _refresh_matchup_projections(out, skip_gamelog=True, skip_starters=True)
        out = _sanitize_prop_board(out, year, week)
        out = _enrich_espn_ids(out)
        out = out.sort_values("ev_pct", ascending=False, na_position="last")
        out = persist_prop_board(out, year=int(year), week=int(week), tab=TAB)
        _save_board_disk_cache(out, sport, int(year), int(week))
        return out

    frames = [df]
    live = _live_onyx_props_for_week(int(year), int(week))
    if not live.empty:
        frames.append(live)

    combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else df
    return _finalize_prop_board(combined, sport=sport, year=int(year), week=int(week))


def _clear_player_props_cache(sport: str, year: int, week: int) -> None:
    path = _board_cache_path(sport, year, week)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    build_player_props_board.clear()


def render() -> None:
    year, week = render_global_filters(prefix="pp")
    sport = cache_sport()

    from lib.projection_archive import props_have_results

    historical = props_have_results(int(year), int(week))
    section_header("Player Projections", eyebrow=f"WEEK {week}" + (" · FINAL" if historical else ""))

    cache_ts = _board_cache_updated_at(sport, int(year), int(week))
    hdr_l, hdr_r = st.columns([3, 1])
    with hdr_r:
        refresh = st.button("Refresh odds & lines", key="pp_refresh_board", use_container_width=True)
    if refresh:
        _clear_player_props_cache(sport, int(year), int(week))
        st.session_state["pp_force_build"] = True
        st.rerun()

    force_build = bool(st.session_state.pop("pp_force_build", False))
    from lib.games import display_week_complete

    week_done = display_week_complete(int(year), int(week), sport=sport) or historical
    df = load_cached_player_props(sport, int(year), int(week))
    if df.empty or force_build:
        if force_build:
            label = "Refreshing player props…"
        elif week_done:
            label = "Loading archived player props…"
        else:
            label = "Building player props…"
        with st.spinner(label):
            df = build_player_props_board(
                sport, int(year), int(week), board_version=BOARD_CACHE_VERSION,
            )
        cache_ts = _board_cache_updated_at(sport, int(year), int(week))

    with hdr_l:
        st.caption(f"Board cached · updated {_format_cache_age(cache_ts)}")

    if df.empty:
        callout(
            "No player props cached for this week yet. Tap <strong>Refresh odds & lines</strong> "
            "once to pull live lines and build projections.",
            "info",
        )
        return

    df = ensure_team_column(df)
    from lib.sport_context import SPORT_NFL, get_sport

    if get_sport() == SPORT_NFL:
        df = _drop_unmapped_nfl_props(df)
        if df.empty:
            callout("Player props will populate when the board is live.", "info")
            return
    if "player" not in df.columns:
        df["player"] = ""

    if "ev_pct" not in df.columns or df["ev_pct"].isna().all():
        if "roi" in df.columns:
            df["ev_pct"] = (pd.to_numeric(df["roi"], errors="coerce") * 100).round(1)
        else:
            df["ev_pct"] = pd.to_numeric(df.get("edgeDisplay"), errors="coerce")

    prop_opts = sorted(df["prop_label"].dropna().unique().tolist()) if "prop_label" in df.columns else sorted(
        df["market"].dropna().unique().tolist()
    ) if "market" in df.columns else []
    team_opts = sorted(t for t in df["team"].dropna().astype(str).unique().tolist() if t)
    player_opts = sorted(df["player"].dropna().astype(str).unique().tolist()) if "player" in df.columns else []

    st.markdown('<div class="bo-filter-bar bo-pp-filters">', unsafe_allow_html=True)
    f1, f2, f3, f4 = st.columns([1.1, 1.1, 1.2, 1.0])
    with f1:
        prop_filter = st.multiselect("Prop", prop_opts, key="pp_filter_prop", placeholder="All props")
    with f2:
        team_filter = st.multiselect("Team", team_opts, key="pp_filter_team", placeholder="All teams")
    with f3:
        player_filter = st.multiselect("Player", player_opts, key="pp_filter_player", placeholder="All players")
    with f4:
        sort_map = {
            "Expected ROI ↓": ("expected_roi", False),
            "Mine ↓": ("modelProj", False),
            "Mine ↑": ("modelProj", True),
            "Player A→Z": ("player", True),
            "Player Z→A": ("player", False),
        }
        sort_label = st.selectbox("Sort", list(sort_map.keys()), index=0, key="pp_sort")
        sort_col, sort_asc = sort_map[sort_label]
    st.markdown("</div>", unsafe_allow_html=True)

    view = df.copy()
    if prop_filter and "prop_label" in view.columns:
        view = view[view["prop_label"].isin(prop_filter)]
    elif prop_filter:
        view = view[view["market"].isin(prop_filter)]
    if team_filter and "team" in view.columns:
        view = view[view["team"].astype(str).isin(team_filter)]
    if player_filter:
        view = view[view["player"].astype(str).isin(player_filter)]

    if sort_col in view.columns:
        view = view.sort_values(sort_col, ascending=sort_asc, na_position="last")
    else:
        view = view.sort_values("ev_pct", ascending=False, na_position="last")

    if view.empty:
        callout("No props match your filters.", "info")
        return

    row_labels = [_row_pick_label(r) for r in view.to_dict("records")]
    prev = st.session_state.get("pp_row_select")
    default_idx = row_labels.index(prev) if prev in row_labels else 0
    st.markdown('<div class="bo-pp-select-wrap">', unsafe_allow_html=True)
    st.selectbox(
        "Select prop for details",
        row_labels,
        index=default_idx,
        key="pp_row_select",
        label_visibility="collapsed",
    )
    st.markdown("</div>", unsafe_allow_html=True)
    picked = st.session_state.get("pp_row_select")
    if picked:
        for _, row in view.iterrows():
            if _row_pick_label(row.to_dict()) == picked:
                st.session_state[SEL_ROW_KEY] = {
                    "player": row.get("player"),
                    "propKey": prop_key_from_row(row.to_dict()) or "",
                    "line": row.get("line"),
                }
                break

    selected_row = _resolve_selected_row(view)
    if selected_row:
        st.markdown('<div class="bo-pp-perf-wrap bo-pp-perf-inline">', unsafe_allow_html=True)
        render_player_performance_panel(selected_row, season=int(year), compact=True)
        st.markdown("</div>", unsafe_allow_html=True)

    show_results = historical or bool(view["prop_result"].notna().any() if "prop_result" in view.columns else False)
    render_props_board(view, selected_row=selected_row, show_results=show_results)
