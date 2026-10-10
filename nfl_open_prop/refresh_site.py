"""Patch the published desk with fresh DraftKings and Pinnacle lines.

Game logs stay as they are. A player new to the board is added with an empty
log. Prices already on the desk are kept when this pull does not see that book
at the same number, so a rate limit does not wipe the slate.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from nfl_open_prop.fourc_lines import fetch_lines

ROOT = Path(__file__).resolve().parents[1]
DESK = ROOT / "prop_desk_site" / "desk.json"
INDEX = ROOT / "prop_desk_site" / "index.html"


def _norm(value: str) -> str:
    text = value.lower().replace(".", " ").replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z\s]", " ", text)
    return " ".join(text.split())


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _price(side) -> int | None:
    if not isinstance(side, dict):
        return None
    raw = side.get("price")
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).replace("+", ""))
    except ValueError:
        return None


def _quote(row: dict) -> dict:
    return {
        "line": _num(row.get("line")),
        "dkLine": _num(row.get("dk_line")),
        "dkOver": _price(row.get("over")),
        "dkUnder": _price(row.get("under")),
        "pinLine": _num(row.get("pin_line")),
        "pinOver": _price(row.get("pin_over")),
        "pinUnder": _price(row.get("pin_under")),
    }


def _same_number(left, right) -> bool:
    a, b = _num(left), _num(right)
    return a is not None and b is not None and abs(a - b) < 0.05


def _merge_quote(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    merged = dict(new)
    if merged.get("dkOver") is None and merged.get("dkUnder") is None and _same_number(old.get("dkLine"), new.get("line")):
        for key in ("dkLine", "dkOver", "dkUnder"):
            if old.get(key) is not None:
                merged[key] = old[key]
    if merged.get("pinOver") is None and merged.get("pinUnder") is None and _same_number(old.get("pinLine"), new.get("line")):
        for key in ("pinLine", "pinOver", "pinUnder"):
            if old.get(key) is not None:
                merged[key] = old[key]
    return merged


def _find(players: list[dict], name: str, home: str, away: str) -> dict | None:
    key = _norm(name)
    hits = [player for player in players if _norm(str(player.get("name") or "")) == key]
    same = [player for player in hits if player.get("home") == home and player.get("away") == away]
    if len(same) == 1:
        return same[0]
    if len(hits) == 1:
        return hits[0]
    return same[0] if same else None


def _players(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (_norm(row["player"]), row["home"], row["away"])
        player = grouped.get(key)
        if player is None:
            team = str(row.get("team") or "")
            opp_name = row["away"] if team == row["home"] else row["home"]
            if team == row["home"]:
                opp = str(row.get("away_abbr") or "")
            elif team == row["away"]:
                opp = str(row.get("home_abbr") or "")
            else:
                opp = str(row.get("away_abbr") or row.get("home_abbr") or "")
                opp_name = row["home"]
            player = {
                "id": "",
                "name": row["player"],
                "team": team,
                "home": row["home"],
                "away": row["away"],
                "opp": opp or opp_name,
                "start": row.get("startDate") or "",
                "props": {},
                "logs": {},
            }
            grouped[key] = player
        player["props"][row["prop_key"]] = _quote(row)
    return list(grouped.values())


def _changed(before: dict, after: dict) -> bool:
    if before.get("week") != after.get("week") or before.get("matchups") != after.get("matchups"):
        return True
    old = {(_norm(p["name"]), p.get("home"), p.get("away")): p.get("props") for p in before.get("players") or []}
    new = {(_norm(p["name"]), p.get("home"), p.get("away")): p.get("props") for p in after.get("players") or []}
    return old != new


def apply_sport(desk: dict, sport_id: str, fresh: dict) -> bool:
    sport = desk["sports"][sport_id]
    previous = json.loads(json.dumps(sport))
    incoming = _players(fresh["rows"])
    if not incoming:
        print(f"{sport_id}: no props returned, left the published slate alone", flush=True)
        return False
    merged = []
    for player in incoming:
        old = _find(sport.get("players") or [], player["name"], player["home"], player["away"])
        if old:
            player["id"] = old.get("id") or player["id"]
            player["logs"] = old.get("logs") or {}
            player["team"] = player["team"] or old.get("team") or ""
            if old.get("opp"):
                player["opp"] = old["opp"]
            props = {}
            for stat, quote in player["props"].items():
                props[stat] = _merge_quote((old.get("props") or {}).get(stat), quote)
            player["props"] = props
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", _norm(player["name"])).strip("-")
            player["id"] = f"{sport_id}-{slug}"
        merged.append(player)
    merged.sort(key=lambda row: row["name"])
    sport["players"] = merged
    sport["season"] = fresh["season"]
    sport["week"] = fresh["week"]
    sport["matchups"] = len(fresh["matchups"])
    sport["updatedAt"] = fresh["updatedAt"]
    if not _changed(previous, sport):
        sport.clear()
        sport.update(previous)
        print(f"{sport_id}: lines unchanged", flush=True)
        return False
    print(f"{sport_id}: {len(merged)} players, week {fresh['week']}", flush=True)
    return True


def bump_index(stamp: str) -> None:
    if not INDEX.exists():
        return
    html = INDEX.read_text(encoding="utf-8")
    html = re.sub(r"(desk\.json\?v=)\d+", rf"\g<1>{stamp}", html)
    html = re.sub(r"(app\.js\?v=)\d+", rf"\g<1>{stamp}", html)
    INDEX.write_text(html, encoding="utf-8")


def main(argv: list[str]) -> int:
    choice = (argv[1] if len(argv) > 1 else "both").strip().lower()
    sports = ["nfl", "cfb"] if choice in {"", "both"} else [choice]
    unknown = [sport for sport in sports if sport not in {"nfl", "cfb"}]
    if unknown or not DESK.exists():
        print("usage: python -m nfl_open_prop.refresh_site [nfl|cfb|both]", file=sys.stderr)
        return 1
    desk = json.loads(DESK.read_text(encoding="utf-8"))
    changed = False
    for sport in sports:
        fresh = fetch_lines(sport, use_cache=False)
        changed = apply_sport(desk, sport, fresh) or changed
    if not changed:
        print("desk.json left as published", flush=True)
        return 0
    desk["generatedAt"] = max(desk["sports"][sport]["updatedAt"] for sport in desk["sports"])
    DESK.write_text(json.dumps(desk, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    stamp = re.sub(r"\D", "", desk["generatedAt"])[:14]
    bump_index(stamp)
    print(f"wrote {DESK}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
