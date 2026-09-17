"""Game projection card rows — model vs market for matchup board."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from lib.config import DATA_DIR
from lib.display import _build_game_rows, team_abbr
from lib.espn_client import fetch_scoreboard_cached
from lib.game_status import game_is_final, game_past_kickoff
from lib.game_line_history import opening_closing_lines
from lib.matchup_board import load_matchup_board
from lib.matchup_builder import _slate_rows_for_game
from lib.slate_loader import load_slate_df
from lib.nfl_projections import price_game
from lib.sp_projections import scores_from_spread_total, warm_sp_cache
from lib.sp_refresh import sp_index_path, sp_index_summary, ensure_fresh_sp_ratings
from lib.team_logos import team_logo_url
from lib.team_registry import resolve_canonical, teams_match
from lib.game_odds_quotes import draftkings_game_quotes
from lib.odds_cache import load_cached_lines
from lib.sport_context import SPORT_CFB, SPORT_NFL, resolve_sport, sport_disk_tag

GAME_CARDS_CACHE_DIR = DATA_DIR / "game_board_cache"
GAME_CARDS_CACHE_TTL_SEC = 3600
GAME_CARDS_FAST_CACHE_SEC = 900  # skip live ESPN bust for 15 min after save
GAME_CARDS_CACHE_VERSION = 8


def merge_odds_into_cards(
    cards: list[dict[str, Any]],
    *,
    sport: str | None = None,
    tab: str | None = None,
) -> list[dict[str, Any]]:
    """Refresh spread/total/team-total quotes from latest odds cache — no full rebuild."""
    from lib.odds_cache import ensure_fourc_lines, load_cached_lines
    from lib.odds_live_feed import live_odds_enabled
    from lib.sport_context import get_sport

    if not cards:
        return cards
    sport = resolve_sport(sport or get_sport())
    if not live_odds_enabled() or sport not in (SPORT_CFB, SPORT_NFL):
        return cards

    odds_df = ensure_fourc_lines(tab=tab)
    if odds_df.empty:
        odds_df = load_cached_lines()
    if odds_df.empty:
        return cards

    odds_index = _odds_by_matchup(odds_df)
    out: list[dict[str, Any]] = []
    for card in cards:
        c = dict(card)
        home = str(c.get("home") or "")
        away = str(c.get("away") or "")
        home_canon = resolve_canonical(home) or home
        away_canon = resolve_canonical(away) or away
        game_odds = odds_index.get(f"{away_canon}|{home_canon}")
        if game_odds is None:
            for k, frame in odds_index.items():
                a, h = k.split("|", 1)
                if teams_match(a, away_canon) and teams_match(h, home_canon):
                    game_odds = frame
                    break
        if game_odds is None:
            game_odds = pd.DataFrame()

        quotes = draftkings_game_quotes(
            home_canon,
            away_canon,
            odds_df,
            home_spread_line=c.get("spread_line"),
            total_line=c.get("total_line"),
            game_odds=game_odds,
            completed=bool(c.get("completed")),
            kickoff=c.get("start"),
        )
        if game_odds is not None and not game_odds.empty and "event_id" in game_odds.columns:
            eids = game_odds["event_id"].astype(str).dropna().unique()
            if len(eids) == 1 and eids[0]:
                c["event_id"] = eids[0]
                c["fourc_id"] = eids[0]
        _enrich_card(c, {}, quotes)
        if c.get("spread_line") is None:
            hs = c.get("home_spread") or {}
            if hs.get("line") is not None:
                try:
                    c["spread_line"] = float(hs["line"])
                except (TypeError, ValueError):
                    pass
        if c.get("total_line") is None:
            ov = c.get("over") or {}
            if ov.get("line") is not None:
                try:
                    c["total_line"] = float(ov["line"])
                except (TypeError, ValueError):
                    pass
        if c.get("spread_diff") is None and c.get("spread_line") is not None and c.get("spread_proj") is not None:
            try:
                c["spread_diff"] = round(float(c["spread_proj"]) - float(c["spread_line"]), 1)
            except (TypeError, ValueError):
                pass
        if c.get("total_diff") is None and c.get("total_line") is not None and c.get("total_proj") is not None:
            try:
                c["total_diff"] = round(float(c["total_proj"]) - float(c["total_line"]), 1)
            except (TypeError, ValueError):
                pass
        _finalize_card_projections(c)
        out.append(c)
    return out


def cards_missing_odds(cards: list[dict[str, Any]]) -> bool:
    if not cards:
        return True
    for c in cards:
        away_sp = c.get("away_spread") or {}
        over = c.get("over") or {}
        if away_sp.get("line") is not None or over.get("line") is not None:
            return False
        if c.get("spread_line") is not None or c.get("total_line") is not None:
            return False
    return True


def _cards_cache_path(sport: str, year: int, week: int) -> Path:
    tag = sport_disk_tag(sport)
    return GAME_CARDS_CACHE_DIR / f"game_cards_{tag}_{int(year)}_w{int(week)}.json"


def _legacy_cards_cache_path(year: int, week: int) -> Path:
    """Pre-sport-isolation path — may contain wrong league; validate before use."""
    return GAME_CARDS_CACHE_DIR / f"game_cards_{int(year)}_w{int(week)}.json"


def _cards_match_sport(cards: list[dict[str, Any]], sport: str) -> bool:
    if not cards:
        return False
    sid = resolve_sport(sport)
    ncaa = nfl = 0
    for card in cards[:12]:
        for key in ("home_logo", "away_logo"):
            logo = str(card.get(key) or "")
            if "/ncaa/" in logo:
                ncaa += 1
            elif "/nfl/" in logo:
                nfl += 1
    if sid == SPORT_NFL:
        return nfl > ncaa
    return ncaa >= nfl and ncaa > 0


def _read_cards_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _cards_from_payload(payload: dict[str, Any], sport: str) -> list[dict[str, Any]]:
    if not _payload_sport_ok(payload, sport):
        return []
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        return []
    tagged = payload.get("sport")
    if tagged and str(tagged) == sport_disk_tag(sport):
        return cards
    if not _cards_match_sport(cards, sport):
        return []
    return cards


def _hydrate_cards_from_board(
    cards: list[dict[str, Any]],
    sport: str,
    year: int,
    week: int,
) -> list[dict[str, Any]]:
    """Fill logos, abbrs, and final scores from ESPN board without rebuilding projections."""
    if not cards:
        return cards
    board = load_matchup_board(sport, int(year), int(week))
    if board.empty:
        return cards
    abbrs = _abbr_lookup(sport, int(year), int(week), board=board)
    board_by_label: dict[str, dict[str, Any]] = {}
    for _, row in board.iterrows():
        board_by_label[str(row.get("label") or "")] = row.to_dict()
        hk = resolve_canonical(str(row.get("home") or "")) or str(row.get("home") or "")
        ak = resolve_canonical(str(row.get("away") or "")) or str(row.get("away") or "")
        if hk and ak:
            board_by_label[f"{ak}|{hk}"] = row.to_dict()

    out: list[dict[str, Any]] = []
    for card in cards:
        merged = dict(card)
        home = str(merged.get("home") or "")
        away = str(merged.get("away") or "")
        home_canon = resolve_canonical(home) or home
        away_canon = resolve_canonical(away) or away
        br = board_by_label.get(str(merged.get("matchup") or merged.get("label") or ""))
        if br is None:
            br = board_by_label.get(f"{away_canon}|{home_canon}")
        if br:
            merged.setdefault("start", br.get("date"))
            merged.setdefault("event_id", br.get("event_id"))
            merged.setdefault("broadcast", br.get("broadcast"))
            merged.setdefault("broadcast_logo", br.get("broadcast_logo"))
            merged.setdefault("home_logo", br.get("home_logo"))
            merged.setdefault("away_logo", br.get("away_logo"))
            completed = game_is_final(
                completed=bool(br.get("completed")),
                home_pts=br.get("home_score"),
                away_pts=br.get("away_score"),
                start_date=merged.get("start") or br.get("date"),
            )
            if completed:
                merged["completed"] = True
                merged["home_score"] = br.get("home_score")
                merged["away_score"] = br.get("away_score")
        merged["home_abbr"] = merged.get("home_abbr") or team_abbr(home, abbrs)
        merged["away_abbr"] = merged.get("away_abbr") or team_abbr(away, abbrs)
        out.append(merged)
    out.sort(key=_card_sort_key)
    return out


def _load_archived_game_cards(sport: str, year: int, week: int) -> list[dict[str, Any]]:
    from lib.csv_log import load_game_cards_from_csv_log
    from lib.games import display_week_complete

    if not display_week_complete(int(year), int(week), sport=sport):
        return []
    cards = load_game_cards_from_csv_log(int(year), int(week))
    if not cards:
        return []
    return _hydrate_cards_from_board(cards, sport, int(year), int(week))


def _sp_cache_token(sport: str, year: int, week: int) -> str:
    if resolve_sport(sport) == SPORT_NFL:
        return "nfl"
    meta = sp_index_summary(season=int(year), display_week=int(week))
    path = sp_index_path(int(year), int(week))
    mtime = path.stat().st_mtime_ns if path.exists() else 0
    return (
        f"v{GAME_CARDS_CACHE_VERSION}|w{int(week)}|sw{meta.get('sourceWeek')}|"
        f"{meta.get('source_tab')}|{meta.get('content_signature')}|{meta.get('generated_at')}|{mtime}"
    )


def _odds_cache_token(sport: str) -> str:
    from lib.odds_cache import CACHE_DIR
    from lib.sport_context import odds_cache_paths

    name, _ = odds_cache_paths(sport)
    path = CACHE_DIR / name
    if path.exists():
        return str(path.stat().st_mtime_ns)
    return "none"


def _scoreboard_cache_token(sport: str, year: int, week: int) -> str:
    """Bust card cache when ESPN scores or completion status change."""
    import hashlib

    from lib.espn_client import fetch_scoreboard_cached

    sb = fetch_scoreboard_cached(week=max(int(week), 1), year=int(year), sport=sport)
    if sb.empty:
        return "empty"
    bits: list[str] = []
    for _, r in sb.sort_values("date").iterrows():
        bits.append(
            f"{r.get('event_id')}|{bool(r.get('completed'))}|{r.get('home_score')}|{r.get('away_score')}"
        )
    return hashlib.sha256("\n".join(bits).encode()).hexdigest()[:20]


@lru_cache(maxsize=32)
def _scoreboard_cache_token_cached(sport: str, year: int, week: int) -> str:
    return _scoreboard_cache_token(sport, year, week)


scoreboard_cache_token = _scoreboard_cache_token_cached


def _payload_sport_ok(payload: dict[str, Any], sport: str) -> bool:
    saved = payload.get("sport")
    return not saved or str(saved) == sport_disk_tag(sport)


def _load_cards_disk_cache(sport: str, year: int, week: int) -> list[dict[str, Any]] | None:
    path = _cards_cache_path(sport, year, week)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not _payload_sport_ok(payload, sport):
            return None
        saved_at = datetime.fromisoformat(str(payload.get("saved_at")).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - saved_at).total_seconds()
        if age > GAME_CARDS_CACHE_TTL_SEC:
            return None
        if payload.get("cache_version") != GAME_CARDS_CACHE_VERSION:
            return None
        if payload.get("sp_token") != _sp_cache_token(sport, int(year), int(week)):
            return None
        if payload.get("odds_token") != _odds_cache_token(sport):
            return None
        if age > GAME_CARDS_FAST_CACHE_SEC:
            if payload.get("scoreboard_token") != _scoreboard_cache_token_cached(
                sport, int(year), int(week)
            ):
                return None
        cards = payload.get("cards")
        return cards if isinstance(cards, list) and cards else None
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def load_cached_game_cards(sport: str, year: int, week: int) -> list[dict[str, Any]]:
    """Instant load — ignore TTL/tokens until explicit refresh."""
    from lib.odds_live_feed import live_odds_enabled

    sport = resolve_sport(sport)
    use_fourc = live_odds_enabled() and sport in (SPORT_CFB, SPORT_NFL)
    path = _cards_cache_path(sport, year, week)
    payload = _read_cards_payload(path)
    if payload:
        if int(payload.get("cache_version") or 0) != GAME_CARDS_CACHE_VERSION:
            payload = None
        else:
            cards = _cards_from_payload(payload, sport)
            if cards and (not use_fourc or not cards_missing_odds(cards)):
                return cards

    legacy = _read_cards_payload(_legacy_cards_cache_path(int(year), int(week)))
    if legacy:
        cards = _cards_from_payload(legacy, sport)
        if cards:
            _save_cards_disk_cache(cards, sport, int(year), int(week))
            return cards

    archived = _load_archived_game_cards(sport, int(year), int(week))
    if archived:
        _save_cards_disk_cache(archived, sport, int(year), int(week))
        return archived
    return []


def game_cards_cache_updated_at(sport: str, year: int, week: int) -> datetime | None:
    from lib.view_disk_cache import parse_cache_timestamp

    sport = resolve_sport(sport)
    for path in (_cards_cache_path(sport, year, week), _legacy_cards_cache_path(int(year), int(week))):
        payload = _read_cards_payload(path)
        if not payload:
            continue
        cards = _cards_from_payload(payload, sport)
        if cards:
            return parse_cache_timestamp(payload.get("saved_at"))
    from lib.config import CSV_LOG_DIR

    if CSV_LOG_DIR.exists():
        for fp in sorted(CSV_LOG_DIR.glob("streamlit_feed_*.csv"), reverse=True):
            try:
                if fp.stat().st_size == 0:
                    continue
                head = fp.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
                if not head or "game_projection" not in head[0]:
                    continue
                return datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
    return None


def clear_game_cards_cache(sport: str, year: int, week: int) -> None:
    sport = resolve_sport(sport)
    for path in (_cards_cache_path(sport, year, week), _legacy_cards_cache_path(int(year), int(week))):
        try:
            if path.exists():
                payload = _read_cards_payload(path)
                if payload and _cards_from_payload(payload, sport):
                    path.unlink(missing_ok=True)
        except OSError:
            pass


def _save_cards_disk_cache(
    cards: list[dict[str, Any]],
    sport: str,
    year: int,
    week: int,
) -> None:
    sport = resolve_sport(sport)
    try:
        GAME_CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "cache_version": GAME_CARDS_CACHE_VERSION,
            "sport": sport_disk_tag(sport),
            "year": int(year),
            "week": int(week),
            "sp_token": _sp_cache_token(sport, int(year), int(week)),
            "odds_token": _odds_cache_token(sport),
            "scoreboard_token": _scoreboard_cache_token_cached(sport, int(year), int(week)),
            "cards": cards,
        }
        _cards_cache_path(sport, year, week).write_text(
            json.dumps(payload, default=str), encoding="utf-8"
        )
    except OSError:
        pass


def _slate_by_matchup(slate: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if slate.empty:
        return {}
    from lib.team_registry import team_key

    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in slate.to_dict(orient="records"):
        hk = team_key(str(row.get("home") or ""))
        ak = team_key(str(row.get("away") or ""))
        if not hk or not ak:
            continue
        buckets.setdefault(f"{ak}|{hk}", []).append(row)
    return {k: pd.DataFrame(v) for k, v in buckets.items()}


def _lookup_slate_game(
    games: dict[str, dict[str, Any]],
    home: str,
    away: str,
    *,
    label: str = "",
) -> dict[str, Any]:
    """Match slate spread/total row by label or canonical team names."""
    if label and label in games:
        return games[label]
    key = f"{away} @ {home}"
    if key in games:
        return games[key]
    for g in games.values():
        if teams_match(g.get("home"), home) and teams_match(g.get("away"), away):
            return g
    return {}


def _slate_for_game(slate_index: dict[str, pd.DataFrame], home: str, away: str) -> pd.DataFrame:
    from lib.team_registry import team_key, teams_match

    key = f"{team_key(away)}|{team_key(home)}"
    hit = slate_index.get(key)
    if hit is not None:
        return hit
    for k, frame in slate_index.items():
        a, h = k.split("|", 1)
        if teams_match(a, away) and teams_match(h, home):
            return frame
    return pd.DataFrame()


def _odds_by_matchup(odds_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if odds_df.empty:
        return {}
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in odds_df.to_dict(orient="records"):
        home = resolve_canonical(str(row.get("home") or "")) or str(row.get("home") or "")
        away = resolve_canonical(str(row.get("away") or "")) or str(row.get("away") or "")
        if not home or not away:
            continue
        key = f"{away}|{home}"
        buckets.setdefault(key, []).append(row)
    return {k: pd.DataFrame(v) for k, v in buckets.items()}


def _parse_kickoff(start: Any) -> str:
    if not start:
        return ""
    return str(start)


def _card_sort_key(card: dict[str, Any]) -> tuple:
    """Upcoming first, finished last; chronological within each tier."""
    if card.get("completed"):
        tier = 2
    elif game_past_kickoff(card.get("start")):
        tier = 1
    else:
        tier = 0
    return (tier, _parse_kickoff(card.get("start")), str(card.get("matchup") or ""))


def _scoreboard_context(
    sport: str,
    year: int,
    week: int,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """ESPN season + week scoreboard for abbrs, broadcast, logos, kickoff."""
    from lib.broadcast_logos import broadcast_logo_url
    from lib.espn_client import fetch_scoreboard_cached, fetch_scoreboard_season_cached

    espn_season = fetch_scoreboard_season_cached(int(year), sport=sport)
    espn_week = (
        fetch_scoreboard_cached(week=max(int(week), 1), year=int(year), sport=sport)
        if int(week) >= 1
        else pd.DataFrame()
    )

    lookup: dict[str, str] = {}
    meta: dict[str, dict[str, Any]] = {}

    def _ingest(row: pd.Series, *, overwrite: bool) -> None:
        for side in ("home", "away"):
            name = str(row.get(side) or "")
            abbr = str(row.get(f"{side}_abbr") or "")
            if name and abbr:
                lookup[name] = abbr
        home = resolve_canonical(str(row.get("home") or "")) or str(row.get("home") or "")
        away = resolve_canonical(str(row.get("away") or "")) or str(row.get("away") or "")
        if not home or not away:
            return
        key = f"{away}|{home}"
        broadcast = str(row.get("broadcast") or "").strip()
        raw_logo = str(row.get("broadcast_logo") or "").strip()
        logo = broadcast_logo_url(broadcast, raw_logo) if broadcast else raw_logo
        payload = {
            "start": row.get("date"),
            "broadcast": broadcast,
            "broadcast_logo": logo,
            "home_logo": team_logo_url(home, fallback=str(row.get("home_logo") or "")),
            "away_logo": team_logo_url(away, fallback=str(row.get("away_logo") or "")),
            "home_abbr": row.get("home_abbr"),
            "away_abbr": row.get("away_abbr"),
            "event_id": row.get("event_id"),
        }
        if overwrite or key not in meta:
            meta[key] = payload
            return
        prev = meta[key]
        if not prev.get("broadcast") and broadcast:
            prev["broadcast"] = broadcast
            prev["broadcast_logo"] = logo
        if not prev.get("start") and payload.get("start"):
            prev["start"] = payload.get("start")
        if not prev.get("event_id") and payload.get("event_id"):
            prev["event_id"] = payload.get("event_id")

    if not espn_season.empty:
        for _, row in espn_season.iterrows():
            _ingest(row, overwrite=False)
    if not espn_week.empty:
        for _, row in espn_week.iterrows():
            _ingest(row, overwrite=True)

    return lookup, meta


def _abbr_lookup(
    sport: str,
    year: int,
    week: int,
    board: pd.DataFrame | None = None,
    *,
    week_lookup: dict[str, str] | None = None,
) -> dict[str, str]:
    from lib.team_registry import cfbd_abbr_lookup

    lookup = dict(cfbd_abbr_lookup())
    if week_lookup:
        lookup.update(week_lookup)
    elif week is not None:
        lookup.update(_scoreboard_context(sport, year, week)[0])
    if board is not None and not board.empty:
        for _, row in board.iterrows():
            for side in ("home", "away"):
                name = str(row.get(side) or "")
                abbr = str(row.get(f"{side}_abbr") or "").strip().upper()
                if name and abbr:
                    lookup[name] = abbr
                    canon = resolve_canonical(name)
                    if canon:
                        lookup[canon] = abbr
    return lookup


def _scoreboard_meta(sport: str, year: int, week: int) -> dict[str, dict[str, Any]]:
    return _scoreboard_context(sport, year, week)[1]


def _format_kickoff_et(start: Any) -> str:
    if not start:
        return ""
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        et = dt.astimezone(ZoneInfo("America/New_York"))
        txt = et.strftime("%I:%M %p ET")
        if txt.startswith("0"):
            txt = txt[1:]
        return txt
    except (TypeError, ValueError):
        return ""



def _enrich_card(card: dict[str, Any], meta: dict[str, Any], quotes: dict[str, Any]) -> None:
    from lib.broadcast_logos import broadcast_logo_url

    if meta.get("start") and not card.get("start"):
        card["start"] = meta["start"]
    card["kickoff"] = _format_kickoff_et(card.get("start") or meta.get("start"))
    broadcast = str(meta.get("broadcast") or card.get("broadcast") or "").strip()
    if broadcast:
        card["broadcast"] = broadcast
        raw_logo = str(meta.get("broadcast_logo") or card.get("broadcast_logo") or "").strip()
        card["broadcast_logo"] = broadcast_logo_url(broadcast, raw_logo)
    card["home_logo"] = team_logo_url(card.get("home"), fallback=str(card.get("home_logo") or meta.get("home_logo") or ""))
    card["away_logo"] = team_logo_url(card.get("away"), fallback=str(card.get("away_logo") or meta.get("away_logo") or ""))
    if meta.get("home_abbr") and not card.get("home_abbr"):
        card["home_abbr"] = meta["home_abbr"]
    if meta.get("away_abbr") and not card.get("away_abbr"):
        card["away_abbr"] = meta["away_abbr"]
    if meta.get("event_id") and not card.get("event_id"):
        card["event_id"] = meta["event_id"]
    card.update({k: v for k, v in quotes.items() if v is not None})


def _finalize_card_projections(card: dict[str, Any]) -> None:
    """Realistic integer scores + team-total edges for display."""
    from lib.sp_projections import realistic_team_scores, scores_from_spread_total

    spread = card.get("spread_proj")
    total = card.get("total_proj")
    ph, pa = card.get("proj_home"), card.get("proj_away")

    try:
        if ph is not None and pa is not None:
            pa_i, ph_i = realistic_team_scores(float(ph), float(pa), spread=spread, total=total)
        elif spread is not None and total is not None:
            pa_i, ph_i = scores_from_spread_total(float(spread), float(total))
        else:
            return
    except (TypeError, ValueError):
        return

    card["proj_away"] = pa_i
    card["proj_home"] = ph_i

    away_tt = card.get("away_team_over") or {}
    home_tt = card.get("home_team_over") or {}
    try:
        if away_tt.get("line") is not None:
            card["away_tt_diff"] = round(float(pa_i) - float(away_tt["line"]), 1)
    except (TypeError, ValueError):
        pass
    try:
        if home_tt.get("line") is not None:
            card["home_tt_diff"] = round(float(ph_i) - float(home_tt["line"]), 1)
    except (TypeError, ValueError):
        pass


@lru_cache(maxsize=16)
def _board_inputs(sport: str, year: int, week: int, historical: bool) -> tuple[Any, Any, Any, Any]:
    """Cached slate + board + scoreboard context for card builds."""
    slate_week = 1 if int(week) == 0 else int(week)
    slate = load_slate_df(sport, int(year), slate_week, historical=historical, log=False)
    board = load_matchup_board(sport, int(year), int(week), tab=None)
    week_lookup, sb_meta = _scoreboard_context(sport, int(year), int(week))
    abbrs = _abbr_lookup(sport, int(year), int(week), board, week_lookup=week_lookup)
    return slate, board, abbrs, sb_meta


def _price_for_matchup(
    price_cache: dict[str, dict[str, Any]],
    home: str,
    away: str,
    *,
    year: int,
    week: int,
    refresh: bool,
) -> dict[str, Any]:
    key = f"{away}|{home}"
    if key not in price_cache:
        priced = price_game(home, away, season=int(year), display_week=int(week), refresh=refresh) or {}
        price_cache[key] = priced
    return price_cache[key]


def build_game_card_rows(
    sport: str,
    year: int,
    week: int,
    *,
    tab: str | None = None,
    quick: bool = True,
    save_cache: bool = True,
) -> list[dict[str, Any]]:
    from lib.games import display_week_complete

    sport = resolve_sport(sport)
    week_done = display_week_complete(int(year), int(week), sport=sport)

    from lib.odds_live_feed import live_odds_enabled

    use_fourc = live_odds_enabled() and sport in (SPORT_CFB, SPORT_NFL)
    if week_done and quick and not use_fourc:
        cached = load_cached_game_cards(sport, int(year), int(week))
        if cached and not cards_missing_odds(cached):
            return cached

    if sport != SPORT_NFL and not week_done:
        ensure_fresh_sp_ratings(season=int(year), display_week=int(week))
        warm_sp_cache(season=int(year), display_week=int(week))

    historical = week_done or int(week) == 0 or int(week) < 1
    slate, board, abbrs, sb_meta = _board_inputs(sport, int(year), int(week), historical)

    if slate.empty and board.empty:
        return []

    from lib.odds_cache import ensure_fourc_lines, has_extended_markets
    from lib.game_odds_quotes import load_board_odds_df

    odds_df = load_cached_lines()
    if use_fourc:
        odds_df = ensure_fourc_lines(tab=tab, force=quick is False)
    elif odds_df.empty and not quick:
        odds_df = load_board_odds_df(tab=tab)
    elif not odds_df.empty and not quick and not has_extended_markets(odds_df):
        odds_df = load_board_odds_df(tab=tab)
    elif odds_df.empty:
        odds_df = load_board_odds_df(tab=tab)
    odds_index = _odds_by_matchup(odds_df)
    slate_index = _slate_by_matchup(slate)
    price_cache: dict[str, dict[str, Any]] = {}
    refresh_prices = True
    cards: list[dict[str, Any]] = []
    spread_games: dict[str, dict[str, Any]] = {}
    total_games: dict[str, dict[str, Any]] = {}
    if not slate.empty:
        spreads = slate[slate["group"].eq("spread") & slate["market"].eq("Spread")]
        totals = slate[slate["group"].eq("total") & slate["market"].eq("Total")]
        spread_games = {
            f"{g['away']} @ {g['home']}": g
            for g in _build_game_rows(
                spreads,
                kind="spread",
                week=week,
                abbr_lookup=abbrs,
                year=int(year),
                quick=False,
                price_cache=price_cache,
            )
        }
        total_games = {
            f"{g['away']} @ {g['home']}": g
            for g in _build_game_rows(
                totals,
                kind="total",
                week=week,
                abbr_lookup=abbrs,
                year=int(year),
                quick=False,
                price_cache=price_cache,
            )
        }

    board_by_label = {str(r["label"]): r for _, r in board.iterrows()} if not board.empty else {}

    # CFBD/ESPN schedule is authoritative — Onyx slate may only cover a handful of games.
    if board_by_label:
        matchups = board["label"].tolist()
    elif spread_games or total_games:
        matchups = sorted(set(spread_games) | set(total_games))
    else:
        matchups = []

    for matchup in matchups:
        br = board_by_label.get(str(matchup), {})
        home = str(br.get("home") or "")
        away = str(br.get("away") or "")
        sg = _lookup_slate_game(spread_games, home, away, label=str(matchup))
        tg = _lookup_slate_game(total_games, home, away, label=str(matchup))
        if not home:
            home = str(sg.get("home") or tg.get("home") or "")
        if not away:
            away = str(sg.get("away") or tg.get("away") or "")

        home_canon = resolve_canonical(home) or home
        away_canon = resolve_canonical(away) or away
        priced = (
            _price_for_matchup(
                price_cache,
                home_canon,
                away_canon,
                year=int(year),
                week=int(week),
                refresh=refresh_prices,
            )
            if home_canon and away_canon
            else {}
        )
        spread_proj = priced.get("spread")
        if spread_proj is None:
            spread_proj = sg.get("proj")
        total_proj = priced.get("total")
        if total_proj is None:
            total_proj = tg.get("proj")
        start = br.get("date")
        completed = game_is_final(
            completed=bool(br.get("completed")),
            home_pts=br.get("home_score"),
            away_pts=br.get("away_score"),
            start_date=start,
        )
        home_score = br.get("home_score") if completed else None
        away_score = br.get("away_score") if completed else None
        spread_line = sg.get("line")
        spread_diff = sg.get("diff")
        if spread_diff is None and spread_line is not None and spread_proj is not None:
            spread_diff = round(float(spread_proj) - float(spread_line), 1)

        total_line = tg.get("line")
        total_diff = tg.get("diff")
        if total_diff is None and total_line is not None and total_proj is not None:
            total_diff = round(float(total_proj) - float(total_line), 1)

        proj_home = priced.get("home_score")
        proj_away = priced.get("away_score")
        if (proj_home is None or proj_away is None) and spread_proj is not None and total_proj is not None:
            try:
                proj_away, proj_home = scores_from_spread_total(float(spread_proj), float(total_proj))
            except (TypeError, ValueError):
                pass

        date_str = ""
        if sg.get("badge"):
            date_str = str(sg.get("badge") or "")
        elif sg.get("sub"):
            date_str = str(sg.get("sub") or "")[:5]
        elif br.get("date"):
            date_str = str(br.get("date"))[5:10].replace("-", "/")

        card: dict[str, Any] = {
                "matchup": matchup,
                "home": home,
                "away": away,
                "start": start,
                "home_abbr": sg.get("home_abbr") or team_abbr(home, abbrs),
                "away_abbr": sg.get("away_abbr") or team_abbr(away, abbrs),
                "home_logo": sg.get("home_logo") or br.get("home_logo"),
                "away_logo": sg.get("away_logo") or br.get("away_logo"),
                "broadcast": br.get("broadcast") or sg.get("broadcast"),
                "broadcast_logo": br.get("broadcast_logo") or sg.get("broadcast_logo"),
                "spread_line": spread_line,
                "spread_proj": spread_proj,
                "spread_diff": spread_diff,
                "total_line": total_line,
                "total_proj": total_proj,
                "total_diff": total_diff,
                "completed": completed,
                "home_score": home_score,
                "away_score": away_score,
                "proj_home": proj_home,
                "proj_away": proj_away,
                "date_str": date_str,
                "label": f"{away} @ {home}" if home else matchup,
                "event_id": br.get("event_id"),
            }

        meta_key = f"{away_canon}|{home_canon}"
        meta = sb_meta.get(meta_key, {})
        if not meta:
            for k, v in sb_meta.items():
                a, h = k.split("|", 1)
                if teams_match(a, away_canon) and teams_match(h, home_canon):
                    meta = v
                    break
        game_slate = _slate_for_game(slate_index, home_canon, away_canon)
        home_spread_for_tt = spread_line
        game_odds = odds_index.get(f"{away_canon}|{home_canon}")
        if game_odds is None:
            for k, frame in odds_index.items():
                a, h = k.split("|", 1)
                if teams_match(a, away_canon) and teams_match(h, home_canon):
                    game_odds = frame
                    break
        if game_odds is None:
            game_odds = pd.DataFrame()
        quotes = draftkings_game_quotes(
            home_canon,
            away_canon,
            odds_df,
            slate=game_slate,
            home_spread_line=home_spread_for_tt,
            total_line=total_line,
            game_odds=game_odds,
            completed=completed,
            kickoff=card.get("start") or meta.get("start"),
        )
        _enrich_card(card, meta, quotes)
        if card.get("spread_line") is None:
            hs = card.get("home_spread") or {}
            if hs.get("line") is not None:
                try:
                    card["spread_line"] = float(hs["line"])
                except (TypeError, ValueError):
                    pass
        if card.get("total_line") is None:
            ov = card.get("over") or {}
            if ov.get("line") is not None:
                try:
                    card["total_line"] = float(ov["line"])
                except (TypeError, ValueError):
                    pass
        if card.get("spread_diff") is None and card.get("spread_line") is not None and spread_proj is not None:
            try:
                card["spread_diff"] = round(float(spread_proj) - float(card["spread_line"]), 1)
            except (TypeError, ValueError):
                pass
        if card.get("total_diff") is None and card.get("total_line") is not None and total_proj is not None:
            try:
                card["total_diff"] = round(float(total_proj) - float(card["total_line"]), 1)
            except (TypeError, ValueError):
                pass
        _finalize_card_projections(card)

        if completed and not quick:
            game_rows = _slate_rows_for_game(slate, home_canon, away_canon)
            oc = opening_closing_lines(
                home_canon,
                away_canon,
                event_id=br.get("event_id"),
                slate_rows=game_rows,
                year=int(year),
                skip_espn=True,
                kickoff=card.get("start") or meta.get("start"),
            )
            card["open_spread"] = oc.get("openSpread")
            card["close_spread"] = oc.get("closeSpread")
            card["open_total"] = oc.get("openTotal")
            card["close_total"] = oc.get("closeTotal")
            if card["close_spread"] is not None:
                card["spread_line"] = card["close_spread"]
                if spread_proj is not None:
                    card["spread_diff"] = round(float(spread_proj) - float(card["close_spread"]), 1)
            if card["close_total"] is not None:
                card["total_line"] = card["close_total"]
                if total_proj is not None:
                    card["total_diff"] = round(float(total_proj) - float(card["close_total"]), 1)

        cards.append(card)

    if not cards and not board.empty:
        for _, br in board.iterrows():
            home, away = str(br.get("home") or ""), str(br.get("away") or "")
            home_canon = resolve_canonical(home) or home
            away_canon = resolve_canonical(away) or away
            priced = _price_for_matchup(
                price_cache,
                home_canon,
                away_canon,
                year=int(year),
                week=int(week),
                refresh=refresh_prices,
            )
            spread_p = priced.get("spread")
            total_p = priced.get("total")
            proj_home = priced.get("home_score")
            proj_away = priced.get("away_score")
            if (proj_home is None or proj_away is None) and spread_p is not None and total_p is not None:
                try:
                    proj_away, proj_home = scores_from_spread_total(float(spread_p), float(total_p))
                except (TypeError, ValueError):
                    pass
            start = br.get("date")
            completed = game_is_final(
                completed=bool(br.get("completed")),
                home_pts=br.get("home_score"),
                away_pts=br.get("away_score"),
                start_date=start,
            )
            card_fb: dict[str, Any] = {
                    "matchup": br.get("label"),
                    "home": home,
                    "away": away,
                    "start": start,
                    "home_abbr": team_abbr(home, abbrs),
                    "away_abbr": team_abbr(away, abbrs),
                    "home_logo": br.get("home_logo"),
                    "away_logo": br.get("away_logo"),
                    "broadcast": br.get("broadcast"),
                    "broadcast_logo": br.get("broadcast_logo"),
                    "spread_line": priced.get("spread"),
                    "spread_proj": spread_p,
                    "spread_diff": 0,
                    "total_line": total_p,
                    "total_proj": total_p,
                    "total_diff": 0,
                    "completed": completed,
                    "home_score": br.get("home_score") if completed else None,
                    "away_score": br.get("away_score") if completed else None,
                    "proj_home": proj_home,
                    "proj_away": proj_away,
                    "date_str": str(br.get("date") or "")[5:10].replace("-", "/"),
                    "label": str(br.get("label") or ""),
                    "event_id": br.get("event_id"),
                }
            meta_key = f"{away_canon}|{home_canon}"
            meta = sb_meta.get(meta_key, {})
            game_slate = _slate_for_game(slate_index, home_canon, away_canon)
            quotes_fb = draftkings_game_quotes(
                home_canon,
                away_canon,
                odds_df,
                slate=game_slate,
                home_spread_line=priced.get("spread"),
                total_line=total_p,
                completed=completed,
                kickoff=card_fb.get("start"),
            )
            _enrich_card(card_fb, meta, quotes_fb)
            _finalize_card_projections(card_fb)
            if completed and not quick:
                game_rows = _slate_rows_for_game(slate, home_canon, away_canon)
                oc = opening_closing_lines(
                    home_canon,
                    away_canon,
                    event_id=br.get("event_id"),
                    slate_rows=game_rows,
                    year=int(year),
                    skip_espn=True,
                    kickoff=card_fb.get("start"),
                )
                card_fb["open_spread"] = oc.get("openSpread")
                card_fb["close_spread"] = oc.get("closeSpread")
                card_fb["open_total"] = oc.get("openTotal")
                card_fb["close_total"] = oc.get("closeTotal")
                if card_fb["close_spread"] is not None:
                    card_fb["spread_line"] = card_fb["close_spread"]
                if card_fb["close_total"] is not None:
                    card_fb["total_line"] = card_fb["close_total"]
            cards.append(card_fb)

    cards.sort(key=_card_sort_key)
    from .projection_archive import persist_game_cards

    cards = persist_game_cards(cards, year=int(year), week=int(week), tab=tab)
    if save_cache:
        _save_cards_disk_cache(cards, sport, int(year), int(week))
    return cards
