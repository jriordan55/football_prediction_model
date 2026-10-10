"""Write the phone site: one folder GitHub Pages can host."""
from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

from nfl_open_prop.desk import STATS
from nfl_open_prop.server import CSS, DESKS, _opp_abbr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "prop_desk_site"
WEB = Path(__file__).resolve().parent / "web" / "app.js"


def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _pack(sport_id: str, brand: str) -> dict:
    slate = DESKS[sport_id]
    players = []
    for player in slate.players.values():
        logs = {}
        for stat in player["props"]:
            rows = slate.values(player["espn_id"], stat)
            logs[stat] = [
                [row["season"], row["week"], row["opp"], _clean(round(float(row["value"]), 2))]
                for row in rows
            ]
        quotes = {}
        for stat, quote in player["props"].items():
            quotes[stat] = {
                "line": _clean(quote["line"]),
                "dkLine": _clean(quote.get("dk_line")),
                "dkOver": _clean(quote["dk_over"]),
                "dkUnder": _clean(quote["dk_under"]),
                "pinLine": _clean(quote["pin_line"]),
                "pinOver": _clean(quote["pin_over"]),
                "pinUnder": _clean(quote["pin_under"]),
            }
        opp_name = player["away"] if player["team"] == player["home"] else player["home"]
        players.append(
            {
                "id": player["espn_id"],
                "name": player["name"],
                "team": player["team"],
                "home": player["home"],
                "away": player["away"],
                "opp": _opp_abbr(player, opp_name),
                "start": player["start"],
                "props": quotes,
                "logs": logs,
            }
        )
    players.sort(key=lambda row: row["name"])
    return {
        "id": sport_id,
        "brand": brand,
        "season": slate.season,
        "week": slate.week,
        "updatedAt": slate.updated_at,
        "matchups": len(slate.matchups),
        "gameRows": slate.game_rows,
        "stats": [
            {"id": spec["id"], "label": spec["label"], "unit": spec["unit"], "default": spec["default"], "kind": spec["kind"]}
            for spec in STATS
        ],
        "players": players,
    }


def export() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sports": {
            "nfl": _pack("nfl", "NFL Prop Desk"),
            "cfb": _pack("cfb", "CFB Prop Desk"),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "desk.json").write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    shutil.copyfile(WEB, OUT / "app.js")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#f4f6f8">
<title>Prop Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&family=Instrument+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script>
document.documentElement.dataset.theme = localStorage.getItem("nfl-desk-theme") || "light";
function toggleTheme() {{
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("nfl-desk-theme", next);
}}
window.DESK_URL = "./desk.json?v={stamp}";
</script>
</head>
<body>
<div class="app">
<header class="mast" id="mast"></header>
<div class="refresh" id="refresh">
  <button type="button" class="ghost" id="refresh-nfl" disabled>Refresh NFL lines</button>
  <button type="button" class="ghost" id="refresh-cfb" disabled>Refresh college lines</button>
  <span class="status" id="refresh-status"></span>
</div>
<main id="main"><p class="lede">Loading the slate…</p></main>
<footer id="foot"></footer>
</div>
<script src="./app.js?v={stamp}"></script>
</body>
</html>
"""
    (OUT / "index.html").write_text(html, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return OUT


if __name__ == "__main__":
    export()
