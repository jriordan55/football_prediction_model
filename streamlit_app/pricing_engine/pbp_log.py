"""Play-by-play pricing log — team rows, frozen per-play odds, MC projections."""

from __future__ import annotations



from typing import Any



from lib.espn_live import (
    _plays_from_drives,
    fetch_game_plays_cached,
    fetch_game_summary_cached,
    live_poll_bucket,
)

from lib.odds_math import implied_to_american
from lib.prop_pricing import clamp_american

from lib.pbp_snapshots import (
    best_quotes_from_dict,
    capture_live_snapshots,
    ensure_total_quotes,
    load_play_snapshots,
    normalize_game_quotes,
    quotes_for_play,
)

from lib.team_logos import team_logo_url





def _fmt_spread(line: float | None) -> str | None:

    if line is None:

        return None

    try:

        return f"{float(line):+.1f}"

    except (TypeError, ValueError):

        return None





PBP_BOOK_ODDS_LO = -2000
PBP_BOOK_ODDS_HI = 2000
PBP_LIVE_WIN_CAP = 99.0
PBP_LIVE_WIN_FLOOR = 1.0


def _cap_live_win_pct(
    pct: float | None,
    *,
    live: bool,
    completed: bool,
) -> float | None:
    if pct is None:
        return None
    p = float(pct)
    if live and not completed:
        p = min(PBP_LIVE_WIN_CAP, max(PBP_LIVE_WIN_FLOOR, p))
    return p


def _calibrated_book_odds(val: object) -> str | None:
    raw = _fmt_price(val)
    if not raw:
        return None
    return clamp_american(raw, lo=PBP_BOOK_ODDS_LO, hi=PBP_BOOK_ODDS_HI)


def _expected_price(win_pct: float | None) -> str | None:
    if win_pct is None:
        return None
    p = float(win_pct) / 100.0
    if not (0 < p < 1):
        p = min(0.999, max(0.001, p))
    return implied_to_american(p)


def _fmt_price(val: object) -> str | None:

    if val is None or val == "":

        return None

    try:

        pi = int(float(val))

        return f"+{pi}" if pi > 0 else str(pi)

    except (TypeError, ValueError):

        s = str(val).strip()

        return s if s else None





def _fmt_yard_line(val: object) -> str | None:

    if val is None or val == "":

        return None

    if isinstance(val, dict):

        txt = val.get("text") or val.get("displayValue")

        if txt:

            return str(txt)

        val = val.get("number") or val.get("yardLine")

    try:

        return str(int(float(val)))

    except (TypeError, ValueError):

        s = str(val).strip()

        return s if s else None





def _pregame_lines(pregame: dict[str, Any] | None) -> tuple[float | None, float | None]:

    if not pregame:

        return None, None

    lines = pregame.get("lines") or {}

    spread = lines.get("spread")

    total = lines.get("total")

    if spread is None:

        spread = (pregame.get("quotes") or {}).get("spread_home", {}).get("line")

    if total is None:

        total = (pregame.get("quotes") or {}).get("total_over", {}).get("line")

    try:

        spread = float(spread) if spread is not None else None

    except (TypeError, ValueError):

        spread = None

    try:

        total = float(total) if total is not None else None

    except (TypeError, ValueError):

        total = None

    return spread, total





def _lines_from_quotes(quotes: dict[str, dict[str, Any]]) -> tuple[float | None, float | None, float | None]:

    q = normalize_game_quotes(quotes)

    spread_home_q = q.get("spread_home") or {}

    spread_away_q = q.get("spread_away") or {}

    total_q = q.get("total_over") or q.get("total_under") or {}

    spread_home = spread_home_q.get("line")

    spread_away = spread_away_q.get("line")

    if spread_away is None and spread_home is not None:

        try:

            spread_away = -float(spread_home)

        except (TypeError, ValueError):

            pass

    if spread_home is None and spread_away is not None:

        try:

            spread_home = -float(spread_away)

        except (TypeError, ValueError):

            pass

    total = total_q.get("line")

    try:

        sh = float(spread_home) if spread_home is not None else None

    except (TypeError, ValueError):

        sh = None

    try:

        sa = float(spread_away) if spread_away is not None else None

    except (TypeError, ValueError):

        sa = None

    try:

        tt = float(total) if total is not None else None

    except (TypeError, ValueError):

        tt = None

    return sh, sa, tt





def _team_row(

    *,

    side: str,

    name: str,

    logo: str,

    score: int,

    exp_spread: float | None,

    spread_line: float | None,

    spread_book: str | None,

    exp_total: float | None,

    total_line: float | None,

    total_book: str | None,

    total_over_odds: object,

    total_under_odds: object,

    ml_odds: object,

    ml_book: str | None,

    win_pct: float | None,

    total_over_book: str | None = None,

    total_under_book: str | None = None,

    live: bool = False,

    completed: bool = False,

) -> dict[str, Any]:

    win_p = _cap_live_win_pct(win_pct, live=live, completed=completed)

    ml_display = _calibrated_book_odds(ml_odds)

    return {

        "side": side,

        "name": name,

        "logo": logo,

        "score": score,

        "exp_spread": _fmt_spread(exp_spread),

        "spread": _fmt_spread(spread_line),

        "spread_book": spread_book,

        "exp_total": f"{exp_total:g}" if exp_total is not None else None,

        "total": f"{total_line:g}" if total_line is not None else None,

        "total_book": total_book,

        "total_over_odds": _calibrated_book_odds(total_over_odds),

        "total_under_odds": _calibrated_book_odds(total_under_odds),

        "total_over_book": total_over_book or total_book,

        "total_under_book": total_under_book or total_book,

        "ml_odds": ml_display,

        "ml_book": ml_book,

        "win_pct": f"{win_p:.1f}%" if win_p is not None else None,

        "exp_price": _expected_price(win_p),

    }





def build_team_pbp_plays(

    *,

    sport: str,

    year: int,

    week: int,

    event_id: str,

    home: str,

    away: str,

    quotes: dict[str, dict[str, Any]],

    pregame: dict[str, Any] | None,

    pregame_sim: dict[str, Any] | None,

    completed: bool = False,

    live: bool = False,

    max_plays: int = 120,

    home_logo: str | None = None,

    away_logo: str | None = None,

    capture_snapshots: bool = False,

    summary: dict[str, Any] | None = None,

    plays: list[dict[str, Any]] | None = None,

) -> list[dict[str, Any]]:

    """Two team rows per play; odds from background snapshots with carry-forward."""

    pre_spread, pre_total = _pregame_lines(pregame)

    fallback_quotes = ensure_total_quotes(
        best_quotes_from_dict(quotes),
        pregame_total=pre_total,
    )

    if not fallback_quotes and pregame:

        fallback_quotes = ensure_total_quotes(
            best_quotes_from_dict((pregame.get("quotes") or {})),
            pregame_total=pre_total,
        )



    if live and capture_snapshots:

        capture_live_snapshots(

            sport=sport,

            year=year,

            week=week,

            event_id=str(event_id),

            home=home,

            away=away,

            quotes=quotes or fallback_quotes,

            market_spread=pre_spread,

            market_total=pre_total,

        )



    snapshots = load_play_snapshots(sport, str(event_id))

    h_logo = team_logo_url(home, fallback=str(home_logo or ""))

    a_logo = team_logo_url(away, fallback=str(away_logo or ""))



    tick = live_poll_bucket()

    if summary is None:

        summary = fetch_game_summary_cached(str(event_id), sport, tick=tick)

    if plays is None:

        plays = fetch_game_plays_cached(str(event_id), sport, limit=400, tick=tick)

    if not plays:

        plays = _plays_from_drives(summary)

    if not plays:

        return []



    plays = plays[-max_plays:]

    running_quotes = ensure_total_quotes(
        normalize_game_quotes(fallback_quotes),
        pregame_total=pre_total,
    )

    cards: list[dict[str, Any]] = []



    for play in plays:

        pid = str(play.get("id") or "")

        snap = snapshots.get(pid) if pid else None

        play_quotes = quotes_for_play(
            pid,
            snapshots=snapshots,
            running_quotes=running_quotes,
            pregame_total=pre_total,
        )

        if play_quotes:

            running_quotes = play_quotes



        spread_home, spread_away, total_line = _lines_from_quotes(play_quotes)

        spread_home_q = play_quotes.get("spread_home") or {}

        spread_away_q = play_quotes.get("spread_away") or {}

        total_over_q = play_quotes.get("total_over") or {}

        total_under_q = play_quotes.get("total_under") or {}

        total_q = total_over_q or total_under_q

        ml_home_q = play_quotes.get("ml_home") or {}

        ml_away_q = play_quotes.get("ml_away") or {}



        hp = int(play.get("homeScore") or play.get("home_score") or 0)

        ap = int(play.get("awayScore") or play.get("away_score") or 0)



        sim_data = (snap or {}).get("sim") or {}

        exp_spread_home = sim_data.get("exp_spread_home")

        exp_spread_away = sim_data.get("exp_spread_away")

        exp_total = sim_data.get("total_mean")

        home_wp = sim_data.get("home_win_pct")

        away_wp = sim_data.get("away_win_pct")



        down = play.get("down")

        distance = play.get("distance")

        try:

            down = int(down) if down is not None else None

        except (TypeError, ValueError):

            down = None

        try:

            distance = int(distance) if distance is not None else None

        except (TypeError, ValueError):

            distance = None



        cards.append(

            {

                "play_id": play.get("id"),

                "quarter": int(play.get("period") or 1),

                "clock": play.get("clock"),

                "down": down,

                "distance": distance,

                "yard_line": _fmt_yard_line(play.get("yardLine")),

                "play_text": play.get("text") or "",

                "captured_at": (snap or {}).get("captured_at"),

                "away": _team_row(

                    side="away",

                    name=away,

                    logo=a_logo,

                    score=ap,

                    exp_spread=float(exp_spread_away) if exp_spread_away is not None else None,

                    spread_line=spread_away,

                    spread_book=str(spread_away_q.get("book_id") or ""),

                    exp_total=float(exp_total) if exp_total is not None else None,

                    total_line=total_line,

                    total_book=str(total_q.get("book_id") or ""),

                    total_over_odds=total_over_q.get("price"),

                    total_under_odds=total_under_q.get("price"),

                    total_over_book=str(total_over_q.get("book_id") or ""),

                    total_under_book=str(total_under_q.get("book_id") or ""),

                    ml_odds=ml_away_q.get("price"),

                    ml_book=str(ml_away_q.get("book_id") or ""),

                    win_pct=away_wp,

                    live=live,

                    completed=completed,

                ),

                "home": _team_row(

                    side="home",

                    name=home,

                    logo=h_logo,

                    score=hp,

                    exp_spread=float(exp_spread_home) if exp_spread_home is not None else None,

                    spread_line=spread_home,

                    spread_book=str(spread_home_q.get("book_id") or ""),

                    exp_total=float(exp_total) if exp_total is not None else None,

                    total_line=total_line,

                    total_book=str(total_q.get("book_id") or ""),

                    total_over_odds=total_over_q.get("price"),

                    total_under_odds=total_under_q.get("price"),

                    total_over_book=str(total_over_q.get("book_id") or ""),

                    total_under_book=str(total_under_q.get("book_id") or ""),

                    ml_odds=ml_home_q.get("price"),

                    ml_book=str(ml_home_q.get("book_id") or ""),

                    win_pct=home_wp,

                    live=live,

                    completed=completed,

                ),

            }

        )



    cards.reverse()

    return cards





def load_matchup_pbp_log(

    *,

    sport: str,

    year: int,

    week: int,

    event_id: str | None,

    home: str,

    away: str,

    quotes: dict[str, dict[str, Any]],

    props: list[dict[str, Any]],

    spread: float | None,

    total: float | None,

    pregame: dict[str, Any] | None,

    pregame_sim: dict[str, Any] | None,

    live: bool,

    completed: bool,

    home_logo: str | None = None,

    away_logo: str | None = None,

    capture_snapshots: bool = False,

    summary: dict[str, Any] | None = None,

    plays: list[dict[str, Any]] | None = None,

) -> list[dict[str, Any]]:

    if not event_id:

        return []



    return build_team_pbp_plays(

        sport=sport,

        year=year,

        week=week,

        event_id=str(event_id),

        home=home,

        away=away,

        quotes=quotes,

        pregame=pregame,

        pregame_sim=pregame_sim,

        completed=completed,

        live=live,

        home_logo=home_logo,

        away_logo=away_logo,

        capture_snapshots=capture_snapshots,

        summary=summary,

        plays=plays,

    )


