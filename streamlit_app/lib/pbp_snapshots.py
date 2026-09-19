"""Per-play frozen odds + model projections for the PBP log."""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import pandas as pd
from lib.espn_client import fetch_game_summary
from lib.espn_live import _plays_from_drives, derive_situation, fetch_game_plays
from lib.inplay_storage import BY_GAME_DIR, load_seen_plays, save_seen_plays
from lib.sharp_edges import _better_american
from pricing_engine.constants import DEFAULT_SIMS
from pricing_engine.simulator import run_matchup_simulation
from pricing_engine.situation_model import parse_play_situation
SNAPSHOT_DIR = BY_GAME_DIR.parent / "play_snapshots"
PBP_BACKFILL_SIMS = 800
BACKFILL_SNAPSHOTS_PER_RUN = 12
_GAME_KEYS = (
    "spread_home",
    "spread_away",
    "total_over",
    "total_under",
    "ml_home",
    "ml_away",
)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _snapshot_path(sport: str, event_id: str) -> Path:
    return SNAPSHOT_DIR / f"{sport}_{event_id}.json"

def _quote_usable(q: dict[str, Any] | None) -> bool:
    return bool(q and (q.get("price") is not None or q.get("line") is not None))

def best_quotes_from_dict(quotes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in _GAME_KEYS:
        q = quotes.get(key)
        if _quote_usable(q):
            out[key] = deepcopy(q)
    return out

def normalize_game_quotes(quotes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fill companion spread/total sides when only one side was captured."""
    q = best_quotes_from_dict(quotes)
    sh_q = q.get("spread_home") or {}
    sa_q = q.get("spread_away") or {}
    sh_line = sh_q.get("line")
    sa_line = sa_q.get("line")
    try:
        if sh_line is not None and sa_line is None:
            q["spread_away"] = {**sa_q, **sh_q, "line": -float(sh_line)}
        elif sa_line is not None and sh_line is None:
            q["spread_home"] = {**sh_q, **sa_q, "line": -float(sa_line)}
    except (TypeError, ValueError):
        pass
    over_q = q.get("total_over") or {}
    under_q = q.get("total_under") or {}
    tot_line = over_q.get("line") if over_q.get("line") is not None else under_q.get("line")
    if tot_line is not None:
        if not _quote_usable(over_q):
            q["total_over"] = {**over_q, "line": tot_line}
        if not _quote_usable(under_q):
            q["total_under"] = {**under_q, "line": tot_line}
    return q


def ensure_total_quotes(
    quotes: dict[str, dict[str, Any]] | None,
    *,
    game: dict[str, Any] | None = None,
    pregame_total: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Fill total line + over/under prices from board cells, main.tot, or pregame."""
    from lib.fourc_odds_client import _better_american, _quote_from_cell

    q = normalize_game_quotes(quotes or {})
    main = (game or {}).get("main") or {}
    tot_line: float | None = None
    for src in (main.get("tot"), pregame_total):
        if src is None:
            continue
        try:
            tot_line = float(src)
            break
        except (TypeError, ValueError):
            continue

    for cell in (game or {}).get("cells") or []:
        if not cell or len(cell) < 5 or str(cell[0] or "") != "tot":
            continue
        side = str(cell[1] or "").lower()
        if side not in ("over", "under"):
            continue
        cq = _quote_from_cell(cell)
        if not cq:
            continue
        try:
            ln = float(
                cq.get("line")
                if cq.get("line") is not None
                else (tot_line if tot_line is not None else main.get("tot"))
            )
        except (TypeError, ValueError):
            continue
        cq["line"] = ln
        cq["market"] = "Total"
        cq["selection"] = "Over" if side == "over" else "Under"
        key = "total_over" if side == "over" else "total_under"
        if tot_line is None:
            tot_line = ln
        existing = q.get(key)
        if not existing:
            q[key] = cq
            continue
        try:
            if _better_american(int(cq["price"]), int(existing["price"])) == int(cq["price"]):
                q[key] = cq
        except (TypeError, ValueError):
            pass

    if tot_line is not None:
        for key, sel in (("total_over", "Over"), ("total_under", "Under")):
            cur = q.get(key) or {}
            if not _quote_usable(cur):
                q[key] = {"line": tot_line, "market": "Total", "selection": sel}
            elif cur.get("line") is None:
                cur["line"] = tot_line
                q[key] = cur
    return normalize_game_quotes(q)


def _sim_summary(sim: dict[str, Any] | None, *, home: str, away: str) -> dict[str, Any] | None:
    if not sim or sim.get("error"):
        return None
    sb = sim.get("scoreboard") or {}
    hm, am = sb.get("home_mean"), sb.get("away_mean")
    if hm is None or am is None:
        return None
    margin = float(hm) - float(am)
    home_wp = away_wp = None
    for m in sim.get("markets") or []:
        if m.get("market") != "ML":
            continue
        sel = str(m.get("selection") or "")
        prob = m.get("prob")
        if prob is None:
            continue
        pct = float(prob) * 100.0
        if sel == home:
            home_wp = pct
        elif sel == away:
            away_wp = pct
    total_mean = sb.get("total_mean")
    if total_mean is None:
        total_mean = float(hm) + float(am)
    return {
        "home_mean": float(hm),
        "away_mean": float(am),
        "total_mean": float(total_mean),
        "exp_spread_home": -margin,
        "exp_spread_away": margin,
        "home_win_pct": home_wp,
        "away_win_pct": away_wp,
    }

def load_play_snapshots(sport: str, event_id: str) -> dict[str, dict[str, Any]]:
    path = _snapshot_path(sport, str(event_id))
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            plays = raw.get("plays") or {}
            if isinstance(plays, dict):
                return {str(k): v for k, v in plays.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    imported = _import_snapshots_from_csv(sport, str(event_id))
    if imported:
        _save_snapshot_file(sport, str(event_id), imported)
    return imported

def _save_snapshot_file(sport: str, event_id: str, plays: dict[str, dict[str, Any]]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(sport, event_id)
    payload = {
        "sport": sport,
        "event_id": event_id,
        "updated_at": _utc_now_iso(),
        "plays": plays,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

def upsert_play_snapshots(
    sport: str,
    event_id: str,
    new_entries: dict[str, dict[str, Any]],
) -> None:
    if not new_entries:
        return
    existing = load_play_snapshots(sport, event_id)
    for pid, entry in new_entries.items():
        if pid in existing and existing[pid].get("quotes") and not entry.get("quotes"):
            entry = {**entry, "quotes": existing[pid]["quotes"]}
        if pid in existing and existing[pid].get("sim") and not entry.get("sim"):
            entry = {**entry, "sim": existing[pid]["sim"]}
        existing[pid] = entry
    _save_snapshot_file(sport, event_id, existing)

def record_play_snapshot(
    *,
    sport: str,
    event_id: str,
    play: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    sim: dict[str, Any] | None,
    home: str,
    away: str,
    source: str = "daemon",
    captured_at: str | None = None,
    game: dict[str, Any] | None = None,
    pregame_total: float | None = None,
) -> None:
    pid = str(play.get("id") or "")
    if not pid:
        return
    frozen = ensure_total_quotes(quotes, game=game, pregame_total=pregame_total)
    if not frozen:
        return
    upsert_play_snapshots(
        sport,
        str(event_id),
        {
            pid: {
                "captured_at": captured_at or _utc_now_iso(),
                "quotes": frozen,
                "sim": _sim_summary(sim, home=home, away=away),
                "source": source,
                "period": play.get("period"),
                "clock": play.get("clock"),
            }
        },
    )

def _import_snapshots_from_csv(sport: str, event_id: str) -> dict[str, dict[str, Any]]:
    path = BY_GAME_DIR / f"{sport}_{event_id}.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return {}
    if df.empty or "play_id" not in df.columns:
        return {}
    game = df[df.get("market_type", pd.Series()).astype(str) == "game"] if "market_type" in df.columns else df
    if game.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for play_id, chunk in game.groupby("play_id"):
        pid = str(play_id)
        if not pid or pid.lower() == "nan":
            continue
        quotes: dict[str, dict[str, Any]] = {}
        sim_summary: dict[str, Any] | None = None
        captured_at = None
        home_name = str(chunk.iloc[0].get("home") or "")
        away_name = str(chunk.iloc[0].get("away") or "")
        for _, row in chunk.iterrows():
            market = str(row.get("market") or "")
            sel = str(row.get("selection") or "")
            sel_l = sel.lower()
            if market == "Spread":
                if home_name and sel == home_name:
                    key = "spread_home"
                elif away_name and sel == away_name:
                    key = "spread_away"
                elif "home" in sel_l:
                    key = "spread_home"
                else:
                    key = "spread_away"
            elif market == "Total":
                key = "total_over" if "over" in sel_l else "total_under"
            elif market == "ML":
                if home_name and sel == home_name:
                    key = "ml_home"
                elif away_name and sel == away_name:
                    key = "ml_away"
                else:
                    key = "ml_home" if "home" in sel_l else "ml_away"
            else:
                continue
            quotes[key] = {
                "book_id": row.get("book_id"),
                "price": row.get("book_price"),
                "line": row.get("line"),
                "selection": sel,
                "market": market,
            }
            captured_at = captured_at or row.get("captured_at")
            proj = row.get("model_projection")
            if proj is not None and str(proj).strip() not in ("", "nan", "—", "-"):
                if market == "Spread" and sim_summary is None:
                    try:
                        spread_val = float(str(proj).replace("+", ""))
                        sim_summary = {
                            "exp_spread_home": spread_val if key == "spread_home" else -spread_val,
                            "exp_spread_away": -spread_val if key == "spread_home" else spread_val,
                        }
                    except (TypeError, ValueError):
                        pass
                if market == "Total" and sim_summary is not None:
                    try:
                        sim_summary["total_mean"] = float(str(proj).replace("+", ""))
                    except (TypeError, ValueError):
                        pass
                if market == "ML" and sim_summary is not None:
                    try:
                        pct = float(str(proj).replace("%", ""))
                        if key == "ml_home":
                            sim_summary["home_win_pct"] = pct
                        else:
                            sim_summary["away_win_pct"] = pct
                    except (TypeError, ValueError):
                        pass
        if quotes:
            out[pid] = {
                "captured_at": captured_at,
                "quotes": normalize_game_quotes(quotes),
                "sim": sim_summary,
                "source": "csv",
            }
    return out

def _live_game_for_play(
    play: dict[str, Any],
    *,
    home: str,
    away: str,
    situation: dict[str, Any],
    win_prob_home: float | None,
) -> dict[str, Any]:
    play_sit = parse_play_situation(play, situation, home=home, away=away)
    return {
        "status": "in",
        "period": int(play.get("period") or situation.get("period") or 1),
        "clock": play.get("clock") or situation.get("clock"),
        "home_score": play_sit.home_score,
        "away_score": play_sit.away_score,
        "win_prob_home": win_prob_home,
        "situation": play_sit,
        "down": play_sit.down,
        "distance": play_sit.distance,
        "yard_line": play_sit.yard_line,
        "possession": play_sit.possession_text,
    }

def _run_play_sim(
    *,
    sport: str,
    home: str,
    away: str,
    year: int,
    week: int,
    play: dict[str, Any],
    situation: dict[str, Any],
    win_prob_home: float | None,
    market_spread: float | None,
    market_total: float | None,
    n_sims: int = DEFAULT_SIMS,
) -> dict[str, Any] | None:
    live_game = _live_game_for_play(
        play, home=home, away=away, situation=situation, win_prob_home=win_prob_home,
    )
    sim = run_matchup_simulation(
        sport,
        home,
        away,
        season=year,
        week=week,
        market_spread=market_spread,
        market_total=market_total,
        live_game=live_game,
        n_sims=n_sims,
    )
    return _sim_summary(sim, home=home, away=away)

def backfill_missing_play_snapshots(
    *,
    sport: str,
    year: int,
    week: int,
    event_id: str,
    home: str,
    away: str,
    quotes: dict[str, dict[str, Any]],
    market_spread: float | None,
    market_total: float | None,
    max_plays: int = BACKFILL_SNAPSHOTS_PER_RUN,
) -> int:
    """Create snapshots for plays that were missed — sim per play, quotes carry-forward."""
    existing = load_play_snapshots(sport, str(event_id))
    summary = fetch_game_summary(str(event_id), sport=sport)
    plays = fetch_game_plays(str(event_id), sport=sport, limit=400)
    if not plays:
        plays = _plays_from_drives(summary)
    if not plays:
        return 0
    situation = derive_situation(summary, {"status": {"state": "in"}}, plays)
    wp = (summary.get("winprobability") or [{}])[-1] if summary.get("winprobability") else {}
    win_prob_home = wp.get("homeWinPercentage")
    if win_prob_home is not None:
        try:
            win_prob_home = float(win_prob_home)
            if win_prob_home <= 1.0:
                win_prob_home *= 100.0
        except (TypeError, ValueError):
            win_prob_home = None
    spread_line = market_spread
    total_line = market_total
    seed_quotes = ensure_total_quotes(quotes, pregame_total=market_total)
    if spread_line is None:
        spread_line = (seed_quotes.get("spread_home") or {}).get("line")
    if total_line is None:
        total_line = (seed_quotes.get("total_over") or seed_quotes.get("total_under") or {}).get("line")
    try:
        spread_line = float(spread_line) if spread_line is not None else None
    except (TypeError, ValueError):
        spread_line = None
    try:
        total_line = float(total_line) if total_line is not None else None
    except (TypeError, ValueError):
        total_line = None
    running_quotes = seed_quotes
    new_entries: dict[str, dict[str, Any]] = {}
    captured_at = _utc_now_iso()
    filled = 0
    for play in plays:
        pid = str(play.get("id") or "")
        if not pid:
            continue
        prior = {**(existing.get(pid) or {}), **(new_entries.get(pid) or {})}
        if prior.get("quotes") and prior.get("sim"):
            running_quotes = ensure_total_quotes(prior["quotes"], pregame_total=total_line)
            continue
        if filled >= max_plays:
            break
        sim_summary = prior.get("sim")
        if not sim_summary:
            sim_summary = _run_play_sim(
                sport=sport,
                home=home,
                away=away,
                year=year,
                week=week,
                play=play,
                situation=situation,
                win_prob_home=win_prob_home,
                market_spread=spread_line,
                market_total=total_line,
                n_sims=PBP_BACKFILL_SIMS,
            )
        play_quotes = prior.get("quotes") or running_quotes or seed_quotes
        if not play_quotes and not sim_summary:
            continue
        if play_quotes:
            running_quotes = ensure_total_quotes(play_quotes, pregame_total=total_line)
        frozen = ensure_total_quotes(running_quotes or seed_quotes, pregame_total=total_line)
        new_entries[pid] = {
            "captured_at": prior.get("captured_at") or captured_at,
            "quotes": deepcopy(frozen) if frozen else prior.get("quotes"),
            "sim": sim_summary,
            "source": "backfill",
            "period": play.get("period"),
            "clock": play.get("clock"),
        }
        filled += 1
    if new_entries:
        upsert_play_snapshots(sport, str(event_id), new_entries)
    return len(new_entries)

def capture_live_snapshots(
    *,
    sport: str,
    year: int,
    week: int,
    event_id: str,
    home: str,
    away: str,
    quotes: dict[str, dict[str, Any]],
    market_spread: float | None,
    market_total: float | None,
) -> int:
    """Capture new plays + backfill gaps (used by UI poll and background daemon)."""
    frozen = ensure_total_quotes(quotes, pregame_total=market_total)
    if not frozen:
        return backfill_missing_play_snapshots(
            sport=sport,
            year=year,
            week=week,
            event_id=event_id,
            home=home,
            away=away,
            quotes=quotes,
            market_spread=market_spread,
            market_total=market_total,
        )
    summary = fetch_game_summary(str(event_id), sport=sport)
    plays = fetch_game_plays(str(event_id), sport=sport, limit=400)
    if not plays:
        plays = _plays_from_drives(summary)
    if not plays:
        return 0
    situation = derive_situation(summary, {"status": {"state": "in"}}, plays)
    wp = (summary.get("winprobability") or [{}])[-1] if summary.get("winprobability") else {}
    win_prob_home = wp.get("homeWinPercentage")
    if win_prob_home is not None:
        try:
            win_prob_home = float(win_prob_home)
            if win_prob_home <= 1.0:
                win_prob_home *= 100.0
        except (TypeError, ValueError):
            win_prob_home = None
    existing = load_play_snapshots(sport, event_id)
    seen = load_seen_plays(event_id)
    new_entries: dict[str, dict[str, Any]] = {}
    captured_at = _utc_now_iso()
    spread_line = market_spread
    total_line = market_total
    if spread_line is None:
        spread_line = frozen.get("spread_home", {}).get("line")
    if total_line is None:
        total_line = (frozen.get("total_over") or frozen.get("total_under") or {}).get("line")
    try:
        spread_line = float(spread_line) if spread_line is not None else None
    except (TypeError, ValueError):
        spread_line = None
    try:
        total_line = float(total_line) if total_line is not None else None
    except (TypeError, ValueError):
        total_line = None
    candidates = [p for p in plays if str(p.get("id") or "") not in seen]
    if not candidates and plays:
        candidates = [plays[-1]]
    for play in candidates:
        pid = str(play.get("id") or "")
        if not pid:
            continue
        sim_summary = _run_play_sim(
            sport=sport,
            home=home,
            away=away,
            year=year,
            week=week,
            play=play,
            situation=situation,
            win_prob_home=win_prob_home,
            market_spread=spread_line,
            market_total=total_line,
            n_sims=DEFAULT_SIMS,
        )
        new_entries[pid] = {
            "captured_at": captured_at,
            "quotes": deepcopy(frozen),
            "sim": sim_summary,
            "source": "live",
            "period": play.get("period"),
            "clock": play.get("clock"),
        }
        seen.add(pid)
    written = 0
    if new_entries:
        upsert_play_snapshots(sport, event_id, new_entries)
        written = len(new_entries)
        save_seen_plays(
            event_id,
            seen,
            meta={"home": home, "away": away, "sport": sport, "last_captured_at": captured_at},
        )
    written += backfill_missing_play_snapshots(
        sport=sport,
        year=year,
        week=week,
        event_id=event_id,
        home=home,
        away=away,
        quotes=quotes,
        market_spread=market_spread,
        market_total=market_total,
        max_plays=BACKFILL_SNAPSHOTS_PER_RUN,
    )
    return written

def quotes_for_play(
    play_id: str,
    *,
    snapshots: dict[str, dict[str, Any]],
    running_quotes: dict[str, dict[str, Any]],
    pregame_total: float | None = None,
) -> dict[str, dict[str, Any]]:
    snap = snapshots.get(play_id) or {}
    if snap.get("quotes"):
        return ensure_total_quotes(snap["quotes"], pregame_total=pregame_total)
    return ensure_total_quotes(running_quotes, pregame_total=pregame_total)
