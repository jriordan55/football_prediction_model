"""NFL prop desk: game-log hit rates, a per-game model, and book odds.

The screens follow Open Prop (WalrusQuant/open-prop). That app scores a number
you type and does not carry a price. This desk keeps that count, and it also
reads the posted DraftKings and Pinnacle lines for the current week.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import scipy.stats

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "streamlit_app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from lib.nfl_l10_stats import game_stat_value  # noqa: E402

DATA = ROOT / "data"
SNAPSHOT = DATA / "odds_api_cache" / "nfl_props_table" / "nfl_props_table_current.json"
SNAPSHOT_PREVIOUS = DATA / "odds_api_cache" / "nfl_props_table" / "nfl_props_table_2026_w5_v1.json"
CACHE = DATA / "espn_cache"
CFB_SNAPSHOT = DATA / "odds_api_cache" / "cfb_props_table" / "cfb_props_table_current.json"
CFB_CACHE = DATA / "espn_cache" / "cfb"
SEASON = 2026
WEEK = 5

# Counting stats the desk will score. Yards use a normal; counts use a
# negative binomial so the predictive stays on the integers.
STATS: list[dict[str, Any]] = [
    {"id": "pass_yds", "label": "Passing yards", "unit": "yards", "default": 249.5, "kind": "yards"},
    {"id": "pass_tds", "label": "Passing TDs", "unit": "TDs", "default": 1.5, "kind": "count"},
    {"id": "pass_attempts", "label": "Pass attempts", "unit": "attempts", "default": 33.5, "kind": "count"},
    {"id": "pass_completions", "label": "Completions", "unit": "completions", "default": 22.5, "kind": "count"},
    {"id": "rush_yds", "label": "Rushing yards", "unit": "yards", "default": 49.5, "kind": "yards"},
    {"id": "rush_attempts", "label": "Rush attempts", "unit": "attempts", "default": 14.5, "kind": "count"},
    {"id": "rec_yds", "label": "Receiving yards", "unit": "yards", "default": 49.5, "kind": "yards"},
    {"id": "receptions", "label": "Receptions", "unit": "receptions", "default": 4.5, "kind": "count"},
    {"id": "rr_yds", "label": "Rush + rec yards", "unit": "yards", "default": 64.5, "kind": "yards"},
    {"id": "pass_rush_yds", "label": "Pass + rush yards", "unit": "yards", "default": 269.5, "kind": "yards"},
    {"id": "tds", "label": "Anytime TD", "unit": "TDs", "default": 0.5, "kind": "count"},
]
STAT_BY_ID = {s["id"]: s for s in STATS}
WINDOWS = (("l5", 5), ("l10", 10), ("l20", 20), ("season", None))


def american_to_implied(price: int | float) -> float:
    price = float(price)
    if price < 0:
        return (-price) / ((-price) + 100.0)
    return 100.0 / (price + 100.0)


def implied_to_american(prob: float) -> int | None:
    if prob <= 0.0 or prob >= 1.0:
        return None
    if prob >= 0.5:
        return int(round(-100.0 * prob / (1.0 - prob)))
    return int(round(100.0 * (1.0 - prob) / prob))


def devig(over_price: int | float | None, under_price: int | float | None) -> float | None:
    """Multiplicative devig of a two-way market. Returns the fair over probability."""
    if over_price is None or under_price is None:
        return None
    over_p = american_to_implied(over_price)
    under_p = american_to_implied(under_price)
    total = over_p + under_p
    if total <= 0:
        return None
    return over_p / total


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = hits / n
    den = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (centre - margin) / den, (centre + margin) / den


def _price(side: dict[str, Any] | None) -> int | None:
    if not isinstance(side, dict):
        return None
    raw = side.get("price")
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).replace("+", ""))
    except ValueError:
        return None


def _fmt_american(price: int | None) -> str:
    if price is None:
        return "—"
    return f"+{price}" if price > 0 else str(price)


def _sample_sd(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def _clears(value: float, line: float) -> bool:
    return value + 1e-9 >= line


def _read_games(cache: Path, espn_id: str, year: int) -> list[dict[str, Any]]:
    path = cache / f"gamelog_{espn_id}_{year}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


class Desk:
    def __init__(
        self,
        snapshot: Path | None = None,
        cache: Path | None = None,
        season: int | None = None,
        week: int | None = None,
    ) -> None:
        self.cache = cache or CACHE
        self.season = SEASON if season is None else int(season)
        self.week = WEEK if week is None else int(week)
        self.sport = "nfl"
        self.updated_at = ""
        self.matchups: list[Any] = []
        self.players: dict[str, dict[str, Any]] = {}
        self.game_rows = 0
        self._logs: dict[str, list[dict[str, Any]]] = {}
        self._role: dict[str, float] = {}
        path = snapshot or SNAPSHOT
        if not path.exists() and snapshot is None and SNAPSHOT_PREVIOUS.exists():
            path = SNAPSHOT_PREVIOUS
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        if season is None and payload.get("year"):
            self.season = int(payload["year"])
        if week is None and payload.get("week"):
            self.week = int(payload["week"])
        self.sport = "cfb" if str(payload.get("sport") or "") == "cfb" else "nfl"
        self.updated_at = str(payload.get("updatedAt") or "")
        self.matchups = list(payload.get("matchups") or [])
        for raw in payload.get("props") or []:
            stat = str(raw.get("prop_key") or "")
            if stat not in STAT_BY_ID:
                continue
            espn_id = str(raw.get("espn_id") or "").strip()
            if not espn_id:
                continue
            player = self.players.setdefault(
                espn_id,
                {
                    "espn_id": espn_id,
                    "name": str(raw.get("player") or ""),
                    "team": str(raw.get("team") or ""),
                    "home": str(raw.get("home") or ""),
                    "away": str(raw.get("away") or ""),
                    "event": str(raw.get("event") or ""),
                    "start": str(raw.get("startDate") or ""),
                    "home_abbr": str(raw.get("home_abbr") or ""),
                    "away_abbr": str(raw.get("away_abbr") or ""),
                    "spread": _num(raw.get("spread")),
                    "total": _num(raw.get("total")),
                    "side": "",
                    "opponent": "",
                    "defense": {},
                    "props": {},
                },
            )
            spread = _num(raw.get("spread"))
            total = _num(raw.get("total"))
            if spread is not None:
                player["spread"] = spread
            if total is not None:
                player["total"] = total
            quote = {
                "line": _num(raw.get("line")),
                "dk_line": _num(raw.get("dk_line")) if raw.get("dk_line") is not None else _num(raw.get("line")),
                "dk_over": _price(raw.get("over")),
                "dk_under": _price(raw.get("under")),
                "pin_line": _num(raw.get("pin_line")),
                "pin_over": _price(raw.get("pin_over")),
                "pin_under": _price(raw.get("pin_under")),
            }
            quote["fair"] = _fair(quote)
            player["props"][stat] = quote
        self._fill_game_lines()
        self._attach_matchup()
        self._load_logs()

    def _fill_game_lines(self) -> None:
        if not any(player.get("spread") is None or player.get("total") is None for player in self.players.values()):
            return
        try:
            from nfl_open_prop.fourc_lines import game_lines

            lines = game_lines(self.sport)
        except Exception:
            return
        for player in self.players.values():
            found = lines.get((player["home"], player["away"]))
            if not found:
                continue
            spread, total = found
            if player.get("spread") is None:
                player["spread"] = spread
            if player.get("total") is None:
                player["total"] = total

    def _attach_matchup(self) -> None:
        from lib.nfl_team_registry import teams_match as nfl_match
        from lib.team_registry import normalize_team_key

        from nfl_open_prop.environment import defense_multiplier

        def same(left: str, right: str) -> bool:
            if not left or not right:
                return False
            if left == right or nfl_match(left, right):
                return True
            a, b = normalize_team_key(left), normalize_team_key(right)
            return bool(a and b and a == b)

        cached: dict[str, dict[str, float]] = {}
        for player in self.players.values():
            team, home, away = player["team"], player["home"], player["away"]
            if same(team, home):
                player["side"] = "home"
                player["opponent"] = away
            elif same(team, away):
                player["side"] = "away"
                player["opponent"] = home
            opponent = str(player.get("opponent") or "")
            factors = cached.get(opponent)
            if factors is None:
                factors = {
                    spec["id"]: defense_multiplier(self.sport, opponent, spec["id"], self.season, self.week)
                    for spec in STATS
                }
                cached[opponent] = factors
            player["defense"] = factors

    def _load_logs(self) -> None:
        series: dict[str, list[float]] = {s["id"]: [] for s in STATS}
        for espn_id, player in self.players.items():
            games: list[dict[str, Any]] = []
            for year in (self.season - 1, self.season):
                for game in _read_games(self.cache, espn_id, year):
                    try:
                        game_season = int(game.get("season") or 0)
                        week = int(game.get("week") or 0)
                    except (TypeError, ValueError):
                        continue
                    if game_season > self.season or (game_season == self.season and week >= self.week):
                        continue
                    games.append(game)
            games.sort(key=lambda g: (str(g.get("game_date") or ""), int(g.get("week") or 0)))
            self._logs[espn_id] = games
            self.game_rows += len(games)
            for stat in player["props"]:
                values = []
                season_vals = []
                for game in games:
                    value = self._stat(game, stat)
                    if value is None or not _played(game, stat, value):
                        continue
                    values.append(value)
                    if int(game.get("season") or 0) == self.season:
                        season_vals.append(value)
                if len(season_vals) >= 3:
                    series[stat].append(sum(season_vals) / len(season_vals))
                elif len(values) >= 5:
                    series[stat].append(sum(values) / len(values))
        for stat, means in series.items():
            if means:
                ordered = sorted(means)
                self._role[stat] = ordered[len(ordered) // 2]

    def _stat(self, game: dict[str, Any], stat: str) -> float | None:
        return game_stat_value(game, stat)

    def values(self, espn_id: str, stat: str) -> list[dict[str, Any]]:
        rows = []
        for game in self._logs.get(espn_id) or []:
            value = self._stat(game, stat)
            if value is None or not _played(game, stat, value):
                continue
            rows.append(
                {
                    "value": float(value),
                    "season": int(game.get("season") or 0),
                    "week": game.get("week"),
                    "date": str(game.get("game_date") or ""),
                    "opp": str(game.get("opponent_abbr") or game.get("opponent") or ""),
                }
            )
        return rows

    def player(self, espn_id: str) -> dict[str, Any] | None:
        return self.players.get(str(espn_id))

    def search(self, query: str) -> list[dict[str, Any]]:
        needle = " ".join(query.lower().split())
        if not needle:
            return []
        hits = [p for p in self.players.values() if needle in p["name"].lower()]
        hits.sort(key=lambda p: p["name"])
        return hits

    def stat_summary(self) -> list[dict[str, Any]]:
        out = []
        for spec in STATS:
            lines = []
            for player in self.players.values():
                quote = player["props"].get(spec["id"])
                if quote and quote["line"] is not None:
                    lines.append(quote["line"])
            lines.sort()
            median = lines[len(lines) // 2] if lines else spec["default"]
            out.append({**spec, "posted": len(lines), "median": median})
        return out

    def quote_for(self, player: dict[str, Any], stat: str) -> dict[str, Any] | None:
        return player["props"].get(stat)

    def model(self, espn_id: str, stat: str) -> dict[str, Any] | None:
        spec = STAT_BY_ID[stat]
        rows = self.values(espn_id, stat)
        this = [r["value"] for r in rows if r["season"] == self.season]
        last = [r["value"] for r in rows if r["season"] == self.season - 1]
        role = self._role.get(stat)
        if not this and not last and role is None:
            return None
        decay = math.exp(-len(this) / 6.0)
        prior_n = (min(8, len(last)) * decay) if last else (3.0 if role is not None else 0.0)
        if last:
            prior_mean = sum(last) / len(last)
            prior_from = f"{self.season - 1}"
        else:
            prior_mean = role if role is not None else (sum(this) / len(this))
            prior_from = "role"
        n = float(len(this))
        if n == 0:
            mean = prior_mean
        else:
            mean = (sum(this) + prior_mean * prior_n) / (n + prior_n)
        pool = this if len(this) >= 4 else (this + last)
        sd = _sample_sd(pool) if len(pool) >= 2 else 0.0
        player = self.players.get(str(espn_id)) or {}
        from nfl_open_prop.environment import market_factor

        defense = float((player.get("defense") or {}).get(stat) or 1.0)
        market = market_factor(
            stat,
            side=player.get("side") or None,
            spread=player.get("spread"),
            total=player.get("total"),
            sport=self.sport,
        )
        base_mean = mean
        mean = base_mean * defense * market
        if spec["kind"] == "yards":
            sd = max(sd, 0.22 * max(mean, 1.0))
        else:
            sd = max(sd, math.sqrt(max(mean, 0.05)))
        return {
            "kind": spec["kind"],
            "mean": mean,
            "base_mean": base_mean,
            "defense": defense,
            "market": market,
            "factor": defense * market,
            "sd": sd,
            "n_season": len(this),
            "n_last": len(last),
            "prior_from": prior_from,
            "prior_games": prior_n,
        }

    def probability(self, model: dict[str, Any] | None, line: float) -> float | None:
        if not model:
            return None
        mean = float(model["mean"])
        sd = float(model["sd"])
        if sd <= 0 or not math.isfinite(mean):
            return None
        if model["kind"] == "yards":
            return float(1.0 - scipy.stats.norm.cdf(line, loc=mean, scale=sd))
        var = sd * sd
        threshold = math.ceil(line - 1e-9)
        if var <= mean + 1e-9:
            return float(1.0 - scipy.stats.poisson.cdf(threshold - 1, mu=max(mean, 1e-6)))
        k = (mean * mean) / (var - mean)
        p = k / (k + mean)
        return float(1.0 - scipy.stats.nbinom.cdf(threshold - 1, k, p))

    def band80(self, model: dict[str, Any] | None) -> tuple[float, float] | None:
        if not model:
            return None
        mean = float(model["mean"])
        sd = float(model["sd"])
        if model["kind"] == "yards":
            z = float(scipy.stats.norm.ppf(0.9))
            return max(0.0, mean - z * sd), mean + z * sd
        var = sd * sd
        if var <= mean + 1e-9:
            dist = scipy.stats.poisson(mu=max(mean, 1e-6))
        else:
            k = (mean * mean) / (var - mean)
            p = k / (k + mean)
            dist = scipy.stats.nbinom(k, p)
        return float(dist.ppf(0.1)), float(dist.ppf(0.9))

    def window_values(self, rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        if key == "season":
            return [r for r in rows if r["season"] == self.season]
        size = {"l5": 5, "l10": 10, "l20": 20}[key]
        return rows[-size:]

    def hit_rate(self, rows: list[dict[str, Any]], line: float) -> tuple[int, int]:
        hits = sum(1 for r in rows if _clears(r["value"], line))
        return hits, len(rows)

    def board(self, stat: str, *, line: float | None, floor: float | None, mode: str) -> list[dict[str, Any]]:
        spec = STAT_BY_ID[stat]
        number = spec["default"] if line is None else line
        out = []
        for player in self.players.values():
            quote = player["props"].get(stat)
            if not quote or quote["line"] is None:
                continue
            rows = self.values(player["espn_id"], stat)
            check = float(quote["line"]) if mode == "book" else float(number)
            last10 = rows[-10:]
            season_rows = [r for r in rows if r["season"] == self.season]
            hits, n = self.hit_rate(last10, check)
            if floor is not None:
                if n < 5:
                    continue
                if n == 0 or hits / n < floor:
                    continue
            fitted = self.model(player["espn_id"], stat)
            prob = self.probability(fitted, check)
            book_line = quote.get("line")
            fair = quote["fair"] if book_line is not None and abs(float(book_line) - check) < 0.05 else None
            edge = (prob - fair) if prob is not None and fair is not None else None
            out.append(
                {
                    "player": player,
                    "quote": quote,
                    "check": check,
                    "hits": hits,
                    "n": n,
                    "season_n": len(season_rows),
                    "prob": prob,
                    "fair": fair,
                    "edge": edge,
                    "mean": None if not fitted else fitted["mean"],
                }
            )
        out.sort(key=lambda row: (-(row["edge"] if row["edge"] is not None else -9), -(row["hits"] / row["n"] if row["n"] else 0), row["player"]["name"]))
        return out


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fair(quote: dict[str, Any]) -> float | None:
    """Pinnacle when it is posted at the same number as the row, else DraftKings."""
    pin = devig(quote.get("pin_over"), quote.get("pin_under"))
    pin_line = quote.get("pin_line")
    line = quote.get("line")
    if pin is not None and pin_line is not None and line is not None and abs(float(pin_line) - float(line)) < 0.05:
        return pin
    dk_line = quote.get("dk_line")
    if dk_line is None:
        dk_line = line
    if dk_line is not None and line is not None and abs(float(dk_line) - float(line)) < 0.05:
        return devig(quote.get("dk_over"), quote.get("dk_under"))
    return None


def _played(game: dict[str, Any], stat: str, value: float) -> bool:
    """A game already in the log counts. A missing stat is left out upstream."""
    del game, stat, value
    return True


def fmt_pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{100.0 * value:.0f}%"


def fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def fmt_edge(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    points = 100.0 * value
    sign = "+" if points > 0.05 else ""
    return f"{sign}{points:.1f}"


def fmt_when(iso: str) -> str:
    if not iso:
        return ""
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    return f"{stamp.strftime('%b')} {stamp.day}"


def american(price: int | None) -> str:
    return _fmt_american(price)
