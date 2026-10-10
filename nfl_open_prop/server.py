"""Local NFL prop desk. Open Prop's screens, plus the posted odds."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from nfl_open_prop.desk import (
    CFB_CACHE,
    CFB_SNAPSHOT,
    STATS,
    STAT_BY_ID,
    Desk,
    american,
    fmt_books,
    fmt_edge,
    fmt_num,
    fmt_pct,
    fmt_when,
    implied_to_american,
    wilson,
)

DESKS = {
    "nfl": Desk(),
    "cfb": Desk(snapshot=CFB_SNAPSHOT, cache=CFB_CACHE),
}
HOST = "127.0.0.1"
PORT = 8520

CSS = """
:root {
  color-scheme: light;
  --bg: #f4f6f8; --panel: #ffffff; --ink: #12161b; --muted: #5d6872;
  --rule: #dce2e8; --chip: #eef2f5; --over: #0f8a4b; --under: #d23b48;
  --avg: #c48a12; --font: "Instrument Sans", "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0c0e12; --panel: #15191f; --ink: #f4f7fa; --muted: #93a0ac;
  --rule: #2a323b; --chip: #1c2229; --over: #3dce7a; --under: #ff6b78; --avg: #f0c14b;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body { background: var(--bg); color: var(--ink); font-family: var(--font); font-size: 15px; line-height: 1.4; }
a { color: inherit; }
button, input, select { font: inherit; color: inherit; }
.app { width: min(1120px, calc(100% - 2rem)); margin: 0 auto; min-height: 100vh; display: flex; flex-direction: column; }
.mast { display: flex; flex-wrap: wrap; gap: .75rem 1.25rem; align-items: center; padding: .85rem 0; border-bottom: 1px solid var(--rule); }
.brand { display: flex; gap: .7rem; align-items: baseline; margin-right: auto; text-decoration: none; }
.word { font-size: 1.05rem; font-weight: 650; letter-spacing: -.03em; }
.brand em { color: var(--muted); font-style: normal; font-family: var(--mono); font-size: .68rem; letter-spacing: .08em; text-transform: uppercase; }
nav { display: flex; gap: .2rem; }
nav a { padding: .35rem .65rem; border-radius: 999px; color: var(--muted); font-size: .86rem; font-weight: 550; text-decoration: none; }
nav a.sport { border: 1px solid var(--rule); }
nav a.sport.on { background: var(--ink); color: var(--bg); border-color: var(--ink); }
nav a[aria-current="page"] { background: var(--chip); color: var(--ink); }
button.ghost, a.ghost { height: 2.15rem; padding: 0 .75rem; background: transparent; color: var(--ink); border: 1px solid var(--rule); border-radius: 8px; text-decoration: none; display: inline-flex; align-items: center; }
main { flex: 1; padding: 1rem 0 2rem; }
h1 { margin: 0; font-size: clamp(1.8rem, 3vw, 2.4rem); font-weight: 650; letter-spacing: -.045em; line-height: 1; }
.lede { margin: .35rem 0 0; color: var(--muted); max-width: 40rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: .55rem; margin: 1rem 0; padding: 0; list-style: none; }
.cards a { display: grid; gap: .2rem; min-height: 5.6rem; padding: .75rem .8rem; background: var(--panel); border: 1px solid var(--rule); border-radius: 12px; text-decoration: none; }
.cards a:hover { border-color: var(--ink); }
.cards strong { font-size: 1rem; letter-spacing: -.02em; }
.muted, .proof { color: var(--muted); font-size: .8rem; }
.mono { font-family: var(--mono); }
.strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .4rem; margin: .9rem 0; }
.strip dt { color: var(--muted); font-family: var(--mono); font-size: .64rem; letter-spacing: .08em; text-transform: uppercase; }
.strip dd { margin: .15rem 0 0; font-size: 1.25rem; font-weight: 650; letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.search { display: flex; gap: .45rem; align-items: center; margin: .2rem 0 1rem; }
input[type="search"], input[type="number"], select {
  height: 2.15rem; padding: 0 .6rem; background: var(--panel); border: 1px solid var(--rule); border-radius: 8px;
}
button, .btn { height: 2.15rem; padding: 0 .75rem; background: var(--ink); color: var(--bg); border: 1px solid var(--ink); border-radius: 8px; font-size: .84rem; font-weight: 600; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }
.chips { display: flex; flex-wrap: wrap; gap: .35rem; }
a.chip, button.chip { height: 1.9rem; padding: 0 .7rem; background: transparent; color: var(--muted); border: 1px solid var(--rule); border-radius: 999px; font-size: .78rem; font-weight: 650; text-decoration: none; }
a.chip.on, button.chip.on { background: var(--ink); color: var(--bg); border-color: var(--ink); }
.tools { display: flex; flex-wrap: wrap; gap: .7rem; align-items: end; margin: .8rem 0; }
.tools label { display: grid; gap: .25rem; color: var(--muted); font-family: var(--mono); font-size: .64rem; letter-spacing: .08em; text-transform: uppercase; }
table { width: 100%; border-collapse: collapse; font-size: .84rem; font-variant-numeric: tabular-nums; }
th, td { padding: .42rem .45rem; border-bottom: 1px solid var(--rule); text-align: right; white-space: nowrap; }
th.left, td.left { text-align: left; }
th { color: var(--muted); font-size: .66rem; letter-spacing: .06em; text-transform: uppercase; }
td a { font-weight: 650; text-decoration: none; }
.table-wrap { overflow-x: auto; background: var(--panel); border: 1px solid var(--rule); border-radius: 12px; }
.pos { color: var(--over); font-weight: 700; }
.neg { color: var(--under); font-weight: 700; }
.identity { display: flex; flex-wrap: wrap; gap: .4rem 1.5rem; align-items: end; justify-content: space-between; }
.team { margin: 0 0 .28rem; color: var(--muted); font-family: var(--mono); font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; }
.asking { margin: 0; color: var(--muted); font-size: .95rem; font-weight: 650; }
.asking b { margin-left: .35rem; color: var(--ink); font-size: 1.7rem; letter-spacing: -.04em; }
.splits { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .45rem; margin: .8rem 0 .4rem; }
a.split { display: grid; gap: .12rem; padding: .6rem .75rem; background: var(--panel); border: 1px solid var(--rule); border-radius: 12px; text-decoration: none; }
a.split.on { border-color: var(--ink); box-shadow: inset 0 2px 0 var(--ink); }
a.split .k { color: var(--muted); font-family: var(--mono); font-size: .68rem; letter-spacing: .08em; text-transform: uppercase; }
a.split strong { font-size: clamp(1.45rem, 3vw, 2rem); font-weight: 650; letter-spacing: -.045em; line-height: 1; }
.band { color: var(--muted); font-size: .88rem; margin: .2rem 0 .8rem; }
.results { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(4.4rem, 1fr); gap: .4rem; margin: 0 0 .85rem; padding: 0; overflow-x: auto; list-style: none; }
.results li { padding: .45rem .3rem .5rem; border-radius: 10px; text-align: center; }
.results li.over { background: color-mix(in srgb, var(--over) 18%, var(--panel)); }
.results li.under { background: color-mix(in srgb, var(--under) 16%, var(--panel)); }
.results .opp { display: block; color: var(--muted); font-family: var(--mono); font-size: .64rem; }
.results strong { display: block; margin: .12rem 0; font-size: 1.2rem; }
.results li.over strong { color: var(--over); }
.results li.under strong { color: var(--under); }
.rooms { display: grid; grid-template-columns: 1fr 1fr; gap: .7rem; }
.room { margin: 0; padding: .8rem .85rem .75rem; background: var(--panel); border: 1px solid var(--rule); border-radius: 12px; }
.room.model { border-top: 3px solid var(--over); }
.room.odds { border-top: 3px solid var(--ink); }
.room.trend { border-top: 3px solid var(--avg); grid-column: 1 / -1; }
.kicker { margin: 0; color: var(--muted); font-family: var(--mono); font-size: .68rem; font-weight: 650; letter-spacing: .14em; text-transform: uppercase; }
.room.model .kicker { color: var(--over); }
.room h2 { margin: .15rem 0 .55rem; font-size: 1.05rem; letter-spacing: -.02em; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr)); gap: .7rem; margin: 0; }
.facts dt { color: var(--muted); font-size: .78rem; }
.facts dd { margin: .1rem 0 0; font-size: 1.15rem; font-weight: 650; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.chance { font-size: 2rem; font-weight: 650; letter-spacing: -.04em; margin: .2rem 0; }
.books { display: grid; grid-template-columns: 1fr 1fr; gap: .55rem; margin-top: .6rem; }
.book { padding: .55rem .6rem; background: var(--bg); border-radius: 10px; }
.book b { display: block; font-size: 1.05rem; }
.note { color: var(--muted); font-size: .82rem; }
.refresh { display: flex; flex-wrap: wrap; gap: .45rem; align-items: center; padding: .7rem 0 0; }
.refresh .status { color: var(--muted); font-size: .8rem; }
button:disabled { opacity: .55; cursor: progress; }
footer { display: flex; flex-wrap: wrap; gap: .35rem 1rem; padding: .8rem 0 1.2rem; border-top: 1px solid var(--rule); color: var(--muted); font-family: var(--mono); font-size: .68rem; }
.method { max-width: 42rem; }
.method h2 { margin: 1.3rem 0 .3rem; font-size: 1.15rem; letter-spacing: -.03em; }
.back { margin: 0 0 .6rem; }
.back a { color: var(--muted); font-size: .82rem; text-decoration: none; }
@media (max-width: 800px) {
  .splits, .strip, .rooms { grid-template-columns: 1fr 1fr; }
  .room.trend { grid-column: auto; }
  .app { width: min(1120px, calc(100% - 1rem)); }
  input, select, button { font-size: 16px; }
}
"""


def esc(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def sport_from(qs: dict[str, list[str]]) -> str:
    return "cfb" if (qs.get("sport") or ["nfl"])[0] == "cfb" else "nfl"


def href(path: str, sport: str, **params: object) -> str:
    query = {key: value for key, value in params.items() if value is not None and value != ""}
    query["sport"] = sport
    return f"{path}?{urlencode(query)}"


def page(title: str, body: str, active: str, sport: str) -> bytes:
    slate = DESKS[sport]
    brand = "CFB Prop Desk" if sport == "cfb" else "NFL Prop Desk"
    nav = []
    for key, label, target in (("nfl", "NFL", "/"), ("cfb", "College", "/")):
        on = " on" if key == sport else ""
        nav.append(f'<a class="sport{on}" href="{href(target, key)}">{label}</a>')
    for key, label, path in (("home", "Home", "/"), ("board", "Board", "/board"), ("method", "Method", "/method")):
        current = ' aria-current="page"' if key == active else ""
        nav.append(f'<a href="{href(path, sport)}"{current}>{label}</a>')
    stamp = slate.updated_at[:16].replace("T", " ") if slate.updated_at else "—"
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
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
</script>
</head>
<body>
<div class="app">
<header class="mast">
  <a class="brand" href="{href("/", sport)}"><span class="word">{brand}</span><em>with odds</em></a>
  <nav>{''.join(nav)}</nav>
  <button class="ghost" type="button" onclick="toggleTheme()">Theme</button>
</header>
<main>{body}</main>
<footer>
  <span>{slate.season} week {slate.week}</span>
  <span>Hit rates from ESPN game logs through week {max(slate.week - 1, 0)}</span>
  <span>Odds snapshot {esc(stamp)} UTC</span>
  <span>DraftKings and Pinnacle, pregame</span>
</footer>
</div>
</body>
</html>"""
    return html.encode("utf-8")


def _floor(raw: str | None) -> float | None:
    if raw in (None, "", "all"):
        return None
    try:
        return float(raw)
    except ValueError:
        return 0.7


def home(sport: str) -> bytes:
    slate = DESKS[sport]
    brand = "CFB Prop Desk" if sport == "cfb" else "NFL Prop Desk"
    cards = []
    for spec in slate.stat_summary():
        if not spec["posted"]:
            continue
        link = href("/board", sport, stat=spec["id"], mode="book")
        cards.append(
            f'<li><a href="{link}"><strong>{esc(spec["label"])}</strong>'
            f'<span class="proof">{spec["posted"]} posted · median {fmt_num(spec["median"])}</span></a></li>'
        )
    names = "".join(f'<option value="{esc(p["name"])}">' for p in sorted(slate.players.values(), key=lambda r: r["name"]))
    empty = ""
    if sport == "cfb" and not slate.players:
        empty = "<p class='lede'>The college slate has not been built yet.</p>"
    body = f"""
<h1>This week's slate</h1>
<p class="lede">Same desk as Open Prop: how often a player cleared a number, and a model for the chance he does it again. The difference is the price. Every posted prop carries DraftKings and Pinnacle, a devigged fair probability, and the gap between that fair number and the model.</p>
<dl class="strip">
  <div><dt>Players</dt><dd>{len(slate.players)}</dd></div>
  <div><dt>Games cached</dt><dd>{slate.game_rows}</dd></div>
  <div><dt>Games this week</dt><dd>{len(slate.matchups)}</dd></div>
  <div><dt>Season</dt><dd>{slate.season}</dd></div>
</dl>
<form class="search" action="/player" method="get">
  <input type="hidden" name="sport" value="{sport}">
  <label class="mono" for="q" style="color:var(--muted);font-size:.68rem;letter-spacing:.08em;text-transform:uppercase">Player</label>
  <input id="q" type="search" name="q" list="players" placeholder="Search the slate" required>
  <datalist id="players">{names}</datalist>
  <button type="submit">Open</button>
</form>
{empty}
<ul class="cards">{''.join(cards)}</ul>
"""
    return page(brand, body, "home", sport)


def board(qs: dict[str, list[str]]) -> bytes:
    sport = sport_from(qs)
    slate = DESKS[sport]
    stat = (qs.get("stat") or ["pass_yds"])[0]
    if stat not in STAT_BY_ID or not any(p["props"].get(stat) for p in slate.players.values()):
        posted = [item["id"] for item in STATS if any(p["props"].get(item["id"]) for p in slate.players.values())]
        if stat not in posted and posted:
            stat = posted[0]
    if stat not in STAT_BY_ID:
        stat = "pass_yds"
    spec = STAT_BY_ID[stat]
    mode = (qs.get("mode") or ["book"])[0]
    if mode not in ("book", "number"):
        mode = "book"
    floor_raw = (qs.get("floor") or ["all"])[0]
    floor = _floor(floor_raw)
    line_raw = (qs.get("line") or [""])[0]
    try:
        line = float(line_raw) if line_raw else float(spec["default"])
    except ValueError:
        line = float(spec["default"])
    rows = slate.board(stat, line=line, floor=floor, mode=mode)
    chips = []
    for item in STATS:
        if not any(p["props"].get(item["id"]) for p in slate.players.values()):
            continue
        on = " on" if item["id"] == stat else ""
        chips.append(f'<a class="chip{on}" href="{href("/board", sport, stat=item["id"], mode=mode, floor=floor_raw, line=line)}">{esc(item["label"])}</a>')
    mode_chips = []
    for key, label in (("book", "Each book's line"), ("number", "One number")):
        on = " on" if key == mode else ""
        mode_chips.append(f'<a class="chip{on}" href="{href("/board", sport, stat=stat, mode=key, floor=floor_raw, line=line)}">{label}</a>')
    floors = []
    for key, label in (("0.6", "60%"), ("0.7", "70%"), ("0.8", "80%"), ("all", "Everyone")):
        on = " on" if floor_raw == key or (floor_raw == "" and key == "all") else ""
        floors.append(f'<a class="chip{on}" href="{href("/board", sport, stat=stat, mode=mode, floor=key, line=line)}">{label}</a>')
    body_rows = []
    for row in rows:
        player = row["player"]
        quote = row["quote"]
        rate = f"{row['hits']}/{row['n']}" if row["n"] else "—"
        edge_cls = "pos" if (row["edge"] or 0) > 0.005 else "neg" if (row["edge"] or 0) < -0.005 else ""
        link = href("/player", sport, id=player["espn_id"], stat=stat, line=row["check"], window="l10")
        opp = player["away"] if player["team"] == player["home"] else player["home"]
        body_rows.append(
            "<tr>"
            f'<td class="left"><a href="{link}">{esc(player["name"])}</a></td>'
            f'<td class="left">{esc(_opp_abbr(player, opp))}</td>'
            f'<td>{fmt_num(row["check"])}</td>'
            f'<td>{rate}</td>'
            f'<td>{row["season_n"]}</td>'
            f'<td>{american(quote["dk_over"])} / {american(quote["dk_under"])}</td>'
            f'<td>{american(quote["pin_over"])} / {american(quote["pin_under"])}</td>'
            f'<td>{fmt_pct(row["fair"])}</td>'
            f'<td>{fmt_pct(row["prob"])}</td>'
            f'<td class="{edge_cls}">{fmt_edge(row["edge"])}</td>'
            f'<td>{fmt_books(row["books"])}</td>'
            "</tr>"
        )
    number_field = ""
    if mode == "number":
        number_field = f"""
<label>Number
  <input type="number" name="line" step="0.5" value="{line}">
</label>
<button type="submit">Score</button>"""
    caption = (
        "Each row is scored against that player's posted line. Side is the over or the under against DraftKings. College rows are overs only."
        if mode == "book"
        else f"Every player is scored against {fmt_num(line)} {spec['unit']}. The side shows only when that number is the DraftKings line."
    )
    body = f"""
<h1>Board</h1>
<p class="lede">{esc(caption)} The 60/70/80 chips are the short list: last 10 at that rate, with at least five played games. Last 10 reaches into {slate.season - 1} when this season is still short.</p>
<div class="chips" style="margin-top:.8rem">{''.join(chips)}</div>
<form class="tools" action="/board" method="get">
  <input type="hidden" name="sport" value="{sport}">
  <input type="hidden" name="stat" value="{esc(stat)}">
  <input type="hidden" name="mode" value="{esc(mode)}">
  <input type="hidden" name="floor" value="{esc(floor_raw)}">
  {number_field}
</form>
<div class="chips">{''.join(mode_chips)}<span style="width:.4rem"></span>{''.join(floors)}</div>
<p class="note">{len(rows)} players · {esc(spec["label"])}</p>
<div class="table-wrap">
<table>
<thead><tr>
<th class="left">Player</th><th class="left">Opp</th><th>Line</th><th>Last 10</th><th>{slate.season}</th>
<th>DK o/u</th><th>Pin o/u</th><th>DK</th><th>Model</th><th>Side</th><th>vs Pin</th>
</tr></thead>
<tbody>{''.join(body_rows) or '<tr><td class="left" colspan="11">Nobody cleared that floor.</td></tr>'}</tbody>
</table>
</div>
"""
    return page(f"{spec['label']} board", body, "board", sport)


_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WSH",
}


def _abbr_team(name: str) -> str:
    return _ABBR.get(name, name)


def _opp_abbr(player: dict, opp: str) -> str:
    if opp == player.get("home") and player.get("home_abbr"):
        return str(player["home_abbr"])
    if opp == player.get("away") and player.get("away_abbr"):
        return str(player["away_abbr"])
    return _abbr_team(opp)


def _model_note(fitted: dict | None, spec: dict, season: int) -> str:
    if not fitted:
        return "The model needs games before it will give a probability."
    absent = ", ".join(fitted.get("absent") or [])
    if float(fitted.get("availability") or 1) <= 0.25:
        role = "He is out, so the mean is zero instead of the posted line."
    elif fitted.get("snapped") and absent:
        role = (
            f"{absent} is out, so the mean uses the DraftKings line {fmt_num(fitted.get('line'))} "
            f"instead of the recent rate {fmt_num(fitted['base_mean'])}."
        )
    elif fitted.get("snapped"):
        role = (
            f"The recent rate {fmt_num(fitted['base_mean'])} is far from DraftKings at {fmt_num(fitted.get('line'))}, "
            "so the mean uses that line."
        )
    elif float(fitted.get("injury_add") or 0) > 0 and absent:
        role = (
            f"Added {fmt_num(fitted['injury_add'])} because {absent} is out, then kept the mean near "
            f"the DraftKings line {fmt_num(fitted.get('line'))}."
        )
    else:
        role = (
            f"Held near the DraftKings line {fmt_num(fitted.get('line'))}. "
            f"Opponent defense {fmt_num(fitted['defense'])} and the spread and total {fmt_num(fitted['market'])}."
        )
    return (
        f"Per-game {spec['kind']}. Mean {fmt_num(fitted['mean'])}, from a base of {fmt_num(fitted['base_mean'])}. "
        f"{role} Pulled toward {fitted['prior_from']} ({fmt_num(fitted['prior_games'])} pseudo-games). "
        f"{fitted['n_season']} games in {season}."
    )


def player_page(qs: dict[str, list[str]]) -> bytes:
    sport = sport_from(qs)
    slate = DESKS[sport]
    query = (qs.get("q") or [""])[0].strip()
    espn_id = (qs.get("id") or [""])[0].strip()
    if query and not espn_id:
        hits = slate.search(query)
        exact = [p for p in hits if p["name"].lower() == query.lower()]
        chosen = exact[0] if len(exact) == 1 else (hits[0] if len(hits) == 1 else None)
        if chosen is None:
            items = "".join(
                f'<li><a href="{href("/player", sport, id=p["espn_id"])}"><strong>{esc(p["name"])}</strong>'
                f'<span class="proof">{esc(p["team"])}</span></a></li>'
                for p in hits
            )
            empty = "<p class='lede'>Nobody on this slate matches that search.</p>" if not hits else ""
            return page("Search", f"<h1>Players</h1>{empty}<ul class='cards'>{items}</ul>", "home", sport)
        espn_id = chosen["espn_id"]
    player = slate.player(espn_id)
    if player is None:
        return page("Player", f"<h1>No player</h1><p class='lede'>That id is not on the week {slate.week} board.</p>", "home", sport)
    stat = (qs.get("stat") or [next(iter(player["props"]), "pass_yds")])[0]
    if stat not in player["props"]:
        stat = next(iter(player["props"]))
    spec = STAT_BY_ID[stat]
    quote = player["props"][stat]
    window = (qs.get("window") or ["l10"])[0]
    if window not in ("l5", "l10", "l20", "season"):
        window = "l10"
    try:
        line = float((qs.get("line") or [""])[0] or quote["line"] or spec["default"])
    except ValueError:
        line = float(quote["line"] or spec["default"])
    rows = slate.values(espn_id, stat)
    shown = slate.window_values(rows, window)
    priced = slate.offer(player, stat, line)
    fitted = priced["fitted"]
    prob = priced["prob"]
    fair = priced["fair"]
    edge = priced["edge"]
    band = slate.band80(fitted)
    chips = []
    for key in player["props"]:
        item = STAT_BY_ID[key]
        on = " on" if key == stat else ""
        chips.append(f'<a class="chip{on}" href="{href("/player", sport, id=espn_id, stat=key, window=window)}">{esc(item["label"])}</a>')
    splits = []
    for key, label in (("l5", "Last 5"), ("l10", "Last 10"), ("l20", "Last 20"), ("season", str(slate.season))):
        sample = slate.window_values(rows, key)
        hits, n = slate.hit_rate(sample, line)
        on = " on" if key == window else ""
        text = f"{hits}/{n}" if n else "—"
        splits.append(
            f'<a class="split{on}" href="{href("/player", sport, id=espn_id, stat=stat, line=line, window=key)}">'
            f'<span class="k">{label}</span><strong>{text}</strong></a>'
        )
    hits, n = slate.hit_rate(shown, line)
    interval = wilson(hits, n)
    if interval:
        band_text = f"95% Wilson interval on this window: {interval[0] * 100:.0f}% to {interval[1] * 100:.0f}%. {hits} of {n} cleared {fmt_num(line)}."
    else:
        band_text = "No played games in this window."
    tiles = []
    for game in shown:
        mark = "over" if game["value"] + 1e-9 >= line else "under"
        tiles.append(
            f'<li class="{mark}"><span class="opp">W{esc(game["week"])} {esc(game["opp"])}</span>'
            f'<strong>{fmt_num(game["value"], 0 if spec["kind"] == "count" else 1)}</strong></li>'
        )
    site = "home" if player["team"] == player["home"] else "away"
    opp = player["away"] if site == "home" else player["home"]
    edge_cls = "pos" if (edge or 0) > 0.005 else "neg" if (edge or 0) < -0.005 else ""
    fair_american = implied_to_american(fair) if fair is not None else None
    model_note = _model_note(fitted, spec, slate.season)
    range_text = "—" if not band else f"{fmt_num(band[0])}–{fmt_num(band[1])}"
    use_book = href("/player", sport, id=espn_id, stat=stat, line=quote["line"], window=window)
    csv_href = href("/player.csv", sport, id=espn_id, stat=stat, window=window)
    body = f"""
<p class="back"><a href="{href("/board", sport, stat=stat, mode="book")}">Board</a></p>
<div class="identity">
  <div>
    <p class="team">{esc(player["team"])}</p>
    <h1>{esc(player["name"])}</h1>
  </div>
  <p class="asking">{esc(spec["label"])}<b id="asking">{fmt_num(line)}</b></p>
</div>
<div class="chips" style="margin:.7rem 0">{''.join(chips)}</div>
<form class="tools" action="/player" method="get">
  <input type="hidden" name="sport" value="{sport}">
  <input type="hidden" name="id" value="{esc(espn_id)}">
  <input type="hidden" name="stat" value="{esc(stat)}">
  <input type="hidden" name="window" value="{esc(window)}">
  <label>Number
    <input id="line" type="number" name="line" step="0.5" value="{line}">
  </label>
  <button type="submit">Score</button>
  <a class="ghost" href="{use_book}">Use book line</a>
  <a class="ghost" href="{csv_href}">CSV</a>
</form>
<div class="splits">{''.join(splits)}</div>
<p class="band" id="wilson">{esc(band_text)}</p>
<ul class="results">{''.join(tiles) or '<li class="under"><span class="opp">—</span><strong>—</strong></li>'}</ul>
<div class="rooms">
  <section class="room odds">
    <p class="kicker">Odds</p>
    <h2>Posted this week</h2>
    <p class="note">{esc(player["away"])} at {esc(player["home"])} · {esc(player["name"])} is {site} vs {esc(opp)} · {esc(fmt_when(player["start"]))}</p>
    <div class="books">
      <div class="book"><span class="note">DraftKings · {fmt_num(dk_line)}</span><b>{american(quote["dk_over"])} / {american(quote["dk_under"])}</b></div>
      <div class="book"><span class="note">Pinnacle · {fmt_num(quote["pin_line"])}</span><b>{american(quote["pin_over"])} / {american(quote["pin_under"])}</b></div>
    </div>
    <dl class="facts" style="margin-top:.7rem">
      <div><dt>DK fair</dt><dd>{fmt_pct(fair)}</dd></div>
      <div><dt>DK price</dt><dd>{american(fair_american)}</dd></div>
      <div><dt>Side</dt><dd class="{edge_cls}">{fmt_edge(edge)}</dd></div>
      <div><dt>vs Pin</dt><dd>{fmt_books(priced["books"])}</dd></div>
    </dl>
    <p class="note">The side is the model against DraftKings only. A two-way DraftKings price is devigged. College sides are overs only, because DraftKings is not posting the under. vs Pin names the book with the cheaper over. It is blank when the number you typed is not the book's line.</p>
  </section>
  <section class="room model">
    <p class="kicker">Model</p>
    <h2>Chance of {fmt_num(line)} or more</h2>
    <p class="chance" id="chance">{fmt_pct(prob)}</p>
    <dl class="facts">
      <div><dt>Mean</dt><dd>{fmt_num(None if not fitted else fitted["mean"])}</dd></div>
      <div><dt>Usual range</dt><dd>{range_text}</dd></div>
      <div><dt>Kind</dt><dd>{esc(spec["kind"])}</dd></div>
    </dl>
    <p class="note">{esc(model_note)} The usual range is the middle 80%.</p>
  </section>
  <section class="room trend">
    <p class="kicker">Trend</p>
    <h2>{'Season' if window == 'season' else window.replace('l', 'Last ')} games</h2>
    {chart_svg(shown, line)}
    <p class="note">Green cleared the number, red missed. The gold path is the mean of that game and the two before it, inside this window only.</p>
  </section>
</div>
"""
    return page(player["name"], body, "board", sport)


def chart_svg(games: list[dict], line: float) -> str:
    if not games:
        return '<p class="note">No games in this window.</p>'
    width, height, pad = 640, 180, 28
    peak = max([line, *(g["value"] for g in games), 1])
    n = len(games)
    gap = (width - pad * 2) / max(n, 1)

    def y(value: float) -> float:
        return height - pad - (value / peak) * (height - pad * 2)

    bars = []
    path = []
    labels = []
    for i, game in enumerate(games):
        x = pad + i * gap + gap * 0.15
        bw = gap * 0.7
        yy = y(game["value"])
        color = "#0f8a4b" if game["value"] + 1e-9 >= line else "#d23b48"
        bars.append(f'<rect x="{x:.1f}" y="{yy:.1f}" width="{bw:.1f}" height="{max(1, y(0) - yy):.1f}" fill="{color}" rx="2"/>')
        window = games[max(0, i - 2) : i + 1]
        if i >= 2:
            avg = sum(g["value"] for g in window) / len(window)
            path.append(f"{x + bw / 2:.1f},{y(avg):.1f}")
        labels.append(f'<text x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle" font-size="10" fill="#5d6872">{esc(game["opp"])}</text>')
    line_y = y(line)
    poly = f'<polyline fill="none" stroke="#c48a12" stroke-width="2" points="{" ".join(path)}"/>' if path else ""
    return (
        f'<svg viewBox="0 0 {width} {height}" class="dist" role="img">'
        f'<line x1="{pad}" y1="{line_y:.1f}" x2="{width - pad}" y2="{line_y:.1f}" stroke="#12161b" stroke-dasharray="3 3"/>'
        + "".join(bars) + poly + "".join(labels) + "</svg>"
    )


def player_csv(qs: dict[str, list[str]]) -> bytes:
    sport = sport_from(qs)
    slate = DESKS[sport]
    espn_id = (qs.get("id") or [""])[0]
    stat = (qs.get("stat") or ["pass_yds"])[0]
    window = (qs.get("window") or ["l10"])[0]
    player = slate.player(espn_id)
    if player is None or stat not in STAT_BY_ID:
        return b"player,error\n"
    rows = slate.window_values(slate.values(espn_id, stat), window if window in ("l5", "l10", "l20", "season") else "l10")
    lines = ["player,season,week,opponent,stat,value"]
    for game in rows:
        lines.append(",".join([
            '"' + player["name"].replace('"', "") + '"',
            str(game["season"]),
            str(game["week"]),
            game["opp"],
            stat,
            fmt_num(game["value"]),
        ]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def method(qs: dict[str, list[str]]) -> bytes:
    sport = sport_from(qs)
    slate = DESKS[sport]
    league = "college football" if sport == "cfb" else "NFL"
    body = f"""
<div class="method">
<h1>Method</h1>
<p class="lede">The counting rules are the Open Prop rules, moved to a football week. The odds are the part that app leaves out.</p>
<h2>What a game counts as</h2>
<p>A game clears the number when the stat is greater than or equal to it. 26 against 26 counts. A line of 64.5 means 65 or more on a counting stat, because the predictive is a count. Yards stay continuous, so 64.5 yards means more than 64.5.</p>
<p>The log is the player's ESPN {league} game log. Games in week {slate.week}, and anything later, are left out. Last 5, last 10, and last 20 are the last that many played games with that stat, reaching into {slate.season - 1} when {slate.season} does not have enough. The season window is {slate.season} only.</p>
<p>The band under the hit rates is a 95% Wilson interval. Five of the last ten is about 24% to 76%.</p>
<h2>The model</h2>
<p>{league[0].upper() + league[1:]} props are per game, so this is a per-game model rather than a rate per minute. Yards use a normal. Counting stats use a negative binomial: wider when the expected total is higher, and never below zero. The percent at a line is that distribution from the line up. The usual range is the middle 80%.</p>
<p>Last season is the prior. It enters as at most eight pseudo-games, then decays as <span class="mono">exp(−n / 6)</span> once this season's games arrive. A player with no {slate.season - 1} log shrinks toward the median {slate.season} rate of players at the same stat. There is no claim that this beats the book. The holdout on the original app is not rerun here.</p>
<p>That rate is then held on the DraftKings line. When the recent rate and the line are far apart, the mean uses the line, because the market has already changed the role. Opponent defense and the spread and total only nudge it. If a teammate at the same position is out and the line has not moved, part of that player's recent production is added. A player who is out is projected at zero.</p>
<h2>Odds</h2>
<p>The side is the model against DraftKings, in percentage points, and it stays within 5 of that price unless an unpriced injury moves it. Pinnacle is not part of the edge. vs Pin shows which book is cheaper on the over. College football has no under at DraftKings, so those rows never show an under. The side is blank when the number being checked is not the DraftKings line.</p>
<p>Refresh NFL lines and Refresh college lines each reload that league's latest DraftKings and Pinnacle prices. A scheduled job pulls those prices off the board and republishes them. Hit rates stay on the saved game logs.</p>
</div>
"""
    return page("Method", body, "method", sport)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        sport = sport_from(qs)
        if path == "/":
            body, kind = home(sport), "text/html; charset=utf-8"
        elif path == "/board":
            body, kind = board(qs), "text/html; charset=utf-8"
        elif path == "/player":
            body, kind = player_page(qs), "text/html; charset=utf-8"
        elif path == "/player.csv":
            body, kind = player_csv(qs), "text/csv; charset=utf-8"
        elif path == "/method":
            body, kind = method(qs), "text/html; charset=utf-8"
        elif path == "/health":
            body, kind = json.dumps({
                "ok": True,
                "nfl": len(DESKS["nfl"].players),
                "cfb": len(DESKS["cfb"].players),
            }).encode(), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[nfl-desk] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"NFL Prop Desk -> {url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
