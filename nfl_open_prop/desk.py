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
            player["props"][stat] = quote
        self._fill_game_lines()
        self._attach_matchup()
        self._load_logs()
        self._attach_injuries()

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

    def _team_token(self, name: str) -> str:
        from lib.nfl_team_registry import team_key
        from lib.team_registry import normalize_team_key

        if self.sport == "nfl":
            return team_key(name)
        return normalize_team_key(name)

    def _same_role(self, member: dict[str, Any], stat: str) -> bool:
        """A quarterback's rush line is not the backup running back's work."""
        props = member.get("props") or {}
        if stat in {"rush_yds", "rush_attempts", "rr_yds"}:
            return "pass_yds" not in props and "pass_attempts" not in props
        if stat.startswith("pass"):
            return "pass_yds" in props or "pass_attempts" in props
        return True

    def _rate(self, espn_id: str, stat: str) -> float | None:
        """Plain per-game rate. This season when there are two games, otherwise the last eight."""
        rows = self.values(espn_id, stat)
        this = [row["value"] for row in rows if row["season"] == self.season]
        use = this if len(this) >= 2 else [row["value"] for row in rows[-8:]]
        if not use:
            return None
        return sum(use) / len(use)

    def _load_outside_log(self, espn_id: str) -> None:
        if not espn_id or espn_id in self._logs:
            return
        from nfl_open_prop.build_cfb import _fetch_log

        league = "nfl" if self.sport == "nfl" else "college-football"
        games: list[dict[str, Any]] = []
        for year in (self.season - 1, self.season):
            try:
                fetched = _fetch_log(str(espn_id), year, self.cache, league)
            except Exception:
                fetched = []
            for game in fetched:
                try:
                    game_season = int(game.get("season") or 0)
                    week = int(game.get("week") or 0)
                except (TypeError, ValueError):
                    continue
                if game_season > self.season or (game_season == self.season and week >= self.week):
                    continue
                games.append(game)
        games.sort(key=lambda game: (str(game.get("game_date") or ""), int(game.get("week") or 0)))
        self._logs[str(espn_id)] = games

    def _attach_injuries(self) -> None:
        from nfl_open_prop.injuries import USAGE, load_injuries, norm_name

        # One player inherits the starter's rate. Receiving is split across the rest.
        replace = {
            "pass_yds", "pass_tds", "pass_attempts", "pass_completions", "pass_rush_yds",
            "rush_yds", "rush_attempts", "rr_yds",
        }
        by_team: dict[str, list[dict[str, Any]]] = {}
        for row in load_injuries(self.sport):
            by_team.setdefault(self._team_token(row["team"]), []).append(row)
        groups: dict[str, list[dict[str, Any]]] = {}
        slate_names = {norm_name(player["name"]) for player in self.players.values()}
        needed: list[str] = []
        for player in self.players.values():
            token = self._team_token(player["team"])
            groups.setdefault(token, []).append(player)
            listings = by_team.get(token) or []
            own = next((row for row in listings if norm_name(row["name"]) == norm_name(player["name"])), None)
            player["availability"] = 1.0 if own is None else float(own["availability"])
            absent: dict[str, list[str]] = {}
            for stat, positions in USAGE.items():
                names = [
                    row["name"]
                    for row in listings
                    if float(row["availability"]) <= 0.25
                    and row["position"] in positions
                    and norm_name(row["name"]) != norm_name(player["name"])
                ]
                if names:
                    absent[stat] = names
            player["absent"] = absent
            player["injury_add"] = {}
            player["takeover"] = {}
        for listings in by_team.values():
            for row in listings:
                if float(row["availability"]) > 0.25 or not row.get("id"):
                    continue
                if norm_name(row["name"]) in slate_names:
                    continue
                if row["position"] not in {"QB", "RB", "FB", "WR", "TE"}:
                    continue
                needed.append(str(row["id"]))
        from concurrent.futures import ThreadPoolExecutor

        pending = sorted(set(needed))
        if pending:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(self._load_outside_log, pending))
        for token, members in groups.items():
            listings = [row for row in (by_team.get(token) or []) if float(row["availability"]) <= 0.25]
            for stat, positions in USAGE.items():
                outs = []
                for row in listings:
                    if row["position"] not in positions:
                        continue
                    holder = next((member for member in members if norm_name(member["name"]) == norm_name(row["name"])), None)
                    espn_id = holder["espn_id"] if holder else str(row.get("id") or "")
                    if not espn_id:
                        continue
                    rate = self._rate(espn_id, stat)
                    if rate is None or rate <= 0:
                        continue
                    outs.append((row["name"], rate, espn_id))
                if not outs:
                    continue
                starter_name, starter_rate, starter_id = max(outs, key=lambda item: item[1])
                actives = []
                for member in members:
                    if member["espn_id"] == starter_id or stat not in member["props"]:
                        continue
                    if float(member.get("availability") or 1) <= 0.25:
                        continue
                    if norm_name(member["name"]) == norm_name(starter_name):
                        continue
                    actives.append(member)
                if not actives:
                    continue
                if stat in replace:
                    same_role = [member for member in actives if self._same_role(member, stat)] or actives
                    lead = max(same_role, key=lambda member: self._rate(member["espn_id"], stat) or 0.0)
                    own = self._rate(lead["espn_id"], stat) or 0.0
                    if starter_rate > own * 1.15:
                        lead["takeover"][stat] = {"rate": round(starter_rate, 2), "from": starter_name}
                    continue
                vacated = sum(rate for _, rate, _ in outs)
                weights = [(member, self._rate(member["espn_id"], stat) or 0.0) for member in actives]
                weights = [(member, weight) for member, weight in weights if weight > 0]
                total = sum(weight for _, weight in weights)
                if total <= 0 or vacated <= 0:
                    continue
                for member, weight in weights:
                    member["injury_add"][stat] = float(member["injury_add"].get(stat) or 0) + vacated * 0.75 * weight / total

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
        this = [row["value"] for row in rows if row["season"] == self.season]
        last = [row["value"] for row in rows if row["season"] == self.season - 1]
        role = self._role.get(stat)
        own = self._rate(espn_id, stat)
        if own is None and role is None:
            return None
        pool = this if len(this) >= 2 else [row["value"] for row in rows[-8:]]
        sd = _sample_sd(pool) if len(pool) >= 2 else 0.0
        player = self.players.get(str(espn_id)) or {}
        takeover = (player.get("takeover") or {}).get(stat) or {}
        if takeover.get("rate") is not None:
            base_mean = float(takeover["rate"])
        elif own is not None:
            base_mean = own
        else:
            base_mean = float(role)
        from nfl_open_prop.environment import market_factor

        defense = float((player.get("defense") or {}).get(stat) or 1.0)
        market = market_factor(
            stat,
            side=player.get("side") or None,
            spread=player.get("spread"),
            total=player.get("total"),
            sport=self.sport,
        )
        extra = float((player.get("injury_add") or {}).get(stat) or 0.0)
        absent = list((player.get("absent") or {}).get(stat) or [])
        availability = float(player.get("availability") or 1.0)
        if availability <= 0.25:
            mean = 0.0
            extra = 0.0
        else:
            mean = max(0.0, base_mean * defense * market + extra)
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
            "injury_add": extra,
            "absent": absent,
            "availability": availability,
            "took_over": str(takeover.get("from") or ""),
        }

    def offer(self, player: dict[str, Any], stat: str, line: float) -> dict[str, Any]:
        """Model probability from the player's rate, or the starter's share when that starter is out."""
        fitted = self.model(player["espn_id"], stat)
        shown = self.probability(fitted, line)
        quote = player["props"].get(stat) or {}
        fair = dk_fair(quote, line)
        pin = pin_fair(quote, line)
        allow_under = self.sport != "cfb" and quote.get("dk_under") is not None
        edge = None
        if shown is not None and fair is not None:
            gap = shown - fair
            if allow_under or gap >= -0.005:
                edge = gap
        books = None if pin is None or fair is None else pin - fair
        return {"fitted": fitted, "prob": shown, "fair": fair, "edge": edge, "books": books}

    def probability(self, model: dict[str, Any] | None, line: float) -> float | None:
        if not model:
            return None
        mean = float(model["mean"])
        sd = float(model["sd"])
        if sd <= 0 or not math.isfinite(mean):
            return None
        if mean <= 1e-8:
            if model["kind"] == "yards":
                return float(1.0 - scipy.stats.norm.cdf(line, loc=0.0, scale=max(sd, 1.0)))
            return 0.0 if line > 0 else 1.0
        if model["kind"] == "yards":
            return float(1.0 - scipy.stats.norm.cdf(line, loc=mean, scale=sd))
        var = sd * sd
        threshold = math.ceil(line - 1e-9)
        if var <= mean + 1e-9:
            return float(1.0 - scipy.stats.poisson.cdf(threshold - 1, mu=max(mean, 1e-6)))
        k = (mean * mean) / (var - mean)
        if k + mean <= 0 or not math.isfinite(k):
            return 0.0 if line > 0 else 1.0
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
        if mean <= 1e-8:
            return 0.0, 0.0
        var = sd * sd
        if var <= mean + 1e-9:
            dist = scipy.stats.poisson(mu=max(mean, 1e-6))
        else:
            k = (mean * mean) / (var - mean)
            if k + mean <= 0 or not math.isfinite(k):
                return 0.0, 0.0
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
            priced = self.offer(player, stat, check)
            fitted = priced["fitted"]
            out.append(
                {
                    "player": player,
                    "quote": quote,
                    "check": check,
                    "hits": hits,
                    "n": n,
                    "season_n": len(season_rows),
                    "prob": priced["prob"],
                    "fair": priced["fair"],
                    "edge": priced["edge"],
                    "books": priced["books"],
                    "mean": None if not fitted else fitted["mean"],
                }
            )
        out.sort(key=lambda row: (-(abs(row["edge"]) if row["edge"] is not None else -9), -(row["hits"] / row["n"] if row["n"] else 0), row["player"]["name"]))
        return out


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _posted_line(quote: dict[str, Any], key: str, line: float) -> bool:
    posted = quote.get(key)
    if posted is None:
        posted = quote.get("line") if key == "dk_line" else None
    return posted is not None and abs(float(posted) - float(line)) < 0.05


def dk_fair(quote: dict[str, Any], line: float) -> float | None:
    """DraftKings only. Two-way markets are devigged. An over with no under uses that price."""
    if not _posted_line(quote, "dk_line", line):
        return None
    both = devig(quote.get("dk_over"), quote.get("dk_under"))
    if both is not None:
        return both
    over = quote.get("dk_over")
    if over is None:
        return None
    return american_to_implied(over)


def pin_fair(quote: dict[str, Any], line: float) -> float | None:
    if not _posted_line(quote, "pin_line", line):
        return None
    both = devig(quote.get("pin_over"), quote.get("pin_under"))
    if both is not None:
        return both
    over = quote.get("pin_over")
    if over is None:
        return None
    return american_to_implied(over)


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
    """Name the side that has the edge, and show that edge as a positive number."""
    if value is None or not math.isfinite(value):
        return "—"
    points = 100.0 * value
    if points > 0.05:
        return f"Over +{points:.1f}"
    if points < -0.05:
        return f"Under +{-points:.1f}"
    return "0.0"


def fmt_books(value: float | None) -> str:
    """Which book is cheaper on the over. Positive means DraftKings."""
    if value is None or not math.isfinite(value):
        return "—"
    points = 100.0 * value
    if points > 0.5:
        return f"DK +{points:.1f}"
    if points < -0.5:
        return f"Pin +{-points:.1f}"
    return "0.0"


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
