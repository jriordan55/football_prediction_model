/* Prop desk in the browser. The math matches nfl_open_prop/desk.py. */
(function () {
  const STATS_FALLBACK = [];
  let DATA = null;
  const roles = {};

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtNum(value, digits) {
    if (value == null || !Number.isFinite(value)) return "—";
    let text = Number(value).toFixed(digits == null ? 1 : digits);
    if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
    return text;
  }

  function fmtPct(value) {
    if (value == null || !Number.isFinite(value)) return "—";
    return Math.round(100 * value) + "%";
  }

  function fmtEdge(value) {
    if (value == null || !Number.isFinite(value)) return "—";
    const points = 100 * value;
    const sign = points > 0.05 ? "+" : "";
    return sign + points.toFixed(1);
  }

  function american(price) {
    if (price == null || !Number.isFinite(price)) return "—";
    return price > 0 ? "+" + price : String(price);
  }

  function implied(price) {
    if (price == null) return null;
    if (price < 0) return (-price) / ((-price) + 100);
    return 100 / (price + 100);
  }

  function toAmerican(prob) {
    if (prob == null || prob <= 0 || prob >= 1) return null;
    if (prob >= 0.5) return Math.round(-100 * prob / (1 - prob));
    return Math.round(100 * (1 - prob) / prob);
  }

  function devig(over, under) {
    if (over == null || under == null) return null;
    const a = implied(over);
    const b = implied(under);
    if (!a || !b || a + b <= 0) return null;
    return a / (a + b);
  }

  function fairOf(quote, line) {
    if (quote.pinLine != null && Math.abs(quote.pinLine - line) < 0.05) {
      const pin = devig(quote.pinOver, quote.pinUnder);
      if (pin != null) return pin;
    }
    const dkLine = quote.dkLine != null ? quote.dkLine : quote.line;
    if (dkLine != null && Math.abs(dkLine - line) < 0.05) return devig(quote.dkOver, quote.dkUnder);
    return null;
  }

  function fmtWhen(iso) {
    if (!iso) return "";
    const stamp = new Date(iso);
    if (Number.isNaN(stamp.getTime())) return iso.slice(0, 10);
    return stamp.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  }

  function logGamma(z) {
    const p = [
      676.5203681218851, -1259.1392167224028, 771.32342877765313,
      -176.61502916214059, 12.507343278686905, -0.13857109526572012,
      9.984369654078499e-6, 1.5056327351493116e-7,
    ];
    if (z < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * z)) - logGamma(1 - z);
    z -= 1;
    let x = 0.99999999999980993;
    for (let i = 0; i < p.length; i++) x += p[i] / (z + i + 1);
    const t = z + p.length - 0.5;
    return 0.5 * Math.log(2 * Math.PI) + (z + 0.5) * Math.log(t) - t + Math.log(x);
  }

  function betacf(a, b, x) {
    const qab = a + b;
    const qap = a + 1;
    const qam = a - 1;
    let c = 1;
    let d = 1 - (qab * x) / qap;
    if (Math.abs(d) < 1e-30) d = 1e-30;
    d = 1 / d;
    let h = d;
    for (let m = 1; m <= 200; m++) {
      const m2 = 2 * m;
      let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
      d = 1 + aa * d;
      if (Math.abs(d) < 1e-30) d = 1e-30;
      c = 1 + aa / c;
      if (Math.abs(c) < 1e-30) c = 1e-30;
      d = 1 / d;
      h *= d * c;
      aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
      d = 1 + aa * d;
      if (Math.abs(d) < 1e-30) d = 1e-30;
      c = 1 + aa / c;
      if (Math.abs(c) < 1e-30) c = 1e-30;
      d = 1 / d;
      const del = d * c;
      h *= del;
      if (Math.abs(del - 1) < 3e-7) break;
    }
    return h;
  }

  function betai(a, b, x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    const bt = Math.exp(logGamma(a + b) - logGamma(a) - logGamma(b) + a * Math.log(x) + b * Math.log(1 - x));
    if (x < (a + 1) / (a + b + 2)) return (bt * betacf(a, b, x)) / a;
    return 1 - (bt * betacf(b, a, 1 - x)) / b;
  }

  function nbinomCdf(k, n, p) {
    if (k < 0) return 0;
    return betai(n, Math.floor(k) + 1, p);
  }

  function poissonCdf(k, mu) {
    if (k < 0) return 0;
    let term = Math.exp(-mu);
    let sum = term;
    for (let i = 1; i <= k; i++) {
      term *= mu / i;
      sum += term;
    }
    return Math.min(1, sum);
  }

  function erf(x) {
    const sign = x < 0 ? -1 : 1;
    const a = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * a);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t) * Math.exp(-a * a);
    return sign * y;
  }

  function normCdf(x, mean, sd) {
    return 0.5 * (1 + erf((x - mean) / (sd * Math.SQRT2)));
  }

  function sampleSd(values) {
    if (values.length < 2) return 0;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    return Math.sqrt(values.reduce((a, v) => a + (v - mean) ** 2, 0) / (values.length - 1));
  }

  function median(values) {
    const ordered = values.slice().sort((a, b) => a - b);
    return ordered[Math.floor(ordered.length / 2)];
  }

  function logs(player, stat) {
    return (player.logs && player.logs[stat]) || [];
  }

  function seasonRows(rows, season) {
    return rows.filter((row) => row[0] === season);
  }

  function windowRows(rows, key, season) {
    if (key === "season") return seasonRows(rows, season);
    const size = { l5: 5, l10: 10, l20: 20 }[key] || 10;
    return rows.slice(-size);
  }

  function hitRate(rows, line) {
    let hits = 0;
    for (const row of rows) if (row[3] + 1e-9 >= line) hits += 1;
    return [hits, rows.length];
  }

  function modelOf(sport, player, stat) {
    const spec = sport.stats.find((item) => item.id === stat);
    const rows = logs(player, stat);
    const thisSeason = seasonRows(rows, sport.season).map((row) => row[3]);
    const last = rows.filter((row) => row[0] === sport.season - 1).map((row) => row[3]);
    const role = roles[sport.id] && roles[sport.id][stat];
    if (!thisSeason.length && !last.length && role == null) return null;
    const decay = Math.exp(-thisSeason.length / 6);
    const priorN = last.length ? Math.min(8, last.length) * decay : role != null ? 3 : 0;
    let priorMean;
    let priorFrom;
    if (last.length) {
      priorMean = last.reduce((a, b) => a + b, 0) / last.length;
      priorFrom = String(sport.season - 1);
    } else {
      priorMean = role != null ? role : thisSeason.reduce((a, b) => a + b, 0) / thisSeason.length;
      priorFrom = "role";
    }
    const n = thisSeason.length;
    const mean = n === 0 ? priorMean : (thisSeason.reduce((a, b) => a + b, 0) + priorMean * priorN) / (n + priorN);
    const pool = thisSeason.length >= 4 ? thisSeason : thisSeason.concat(last);
    let sd = sampleSd(pool);
    if (spec.kind === "yards") sd = Math.max(sd, 0.22 * Math.max(mean, 1));
    else sd = Math.max(sd, Math.sqrt(Math.max(mean, 0.05)));
    return { kind: spec.kind, mean, sd, nSeason: n, priorFrom, priorGames: priorN };
  }

  function probability(model, line) {
    if (!model || !(model.sd > 0) || !Number.isFinite(model.mean)) return null;
    if (model.kind === "yards") return 1 - normCdf(line, model.mean, model.sd);
    const variance = model.sd * model.sd;
    const threshold = Math.ceil(line - 1e-9);
    if (variance <= model.mean + 1e-9) return 1 - poissonCdf(threshold - 1, Math.max(model.mean, 1e-6));
    const k = (model.mean * model.mean) / (variance - model.mean);
    const p = k / (k + model.mean);
    return 1 - nbinomCdf(threshold - 1, k, p);
  }

  function band80(model) {
    if (!model) return null;
    if (model.kind === "yards") {
      const z = 1.2815515655446004;
      return [Math.max(0, model.mean - z * model.sd), model.mean + z * model.sd];
    }
    const variance = model.sd * model.sd;
    const distCdf = (k) => {
      if (variance <= model.mean + 1e-9) return poissonCdf(k, Math.max(model.mean, 1e-6));
      const n = (model.mean * model.mean) / (variance - model.mean);
      const p = n / (n + model.mean);
      return nbinomCdf(k, n, p);
    };
    const quantile = (target) => {
      let k = 0;
      const cap = Math.max(40, Math.ceil(model.mean + 12 * model.sd));
      while (k < cap && distCdf(k) < target) k += 1;
      return k;
    };
    return [quantile(0.1), quantile(0.9)];
  }

  function wilson(hits, n) {
    if (n <= 0) return null;
    const z = 1.96;
    const p = hits / n;
    const den = 1 + (z * z) / n;
    const centre = p + (z * z) / (2 * n);
    const margin = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
    return [(centre - margin) / den, (centre + margin) / den];
  }

  function numParam(qs, key) {
    const raw = qs.get(key);
    if (raw == null || raw === "") return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  function route() {
    const raw = (location.hash || "#/nfl").replace(/^#/, "");
    const [path, query] = raw.split("?");
    const parts = path.split("/").filter(Boolean);
    const sport = parts[0] === "cfb" ? "cfb" : "nfl";
    const page = parts[1] || "home";
    return { sport, page, qs: new URLSearchParams(query || "") };
  }

  function link(sport, page, params) {
    const qs = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value != null && value !== "") qs.set(key, value);
    });
    const tail = qs.toString();
    return "#/" + sport + (page && page !== "home" ? "/" + page : "") + (tail ? "?" + tail : "");
  }

  function sportData(id) {
    return DATA.sports[id];
  }

  function shell(sportId, active) {
    const sport = sportData(sportId);
    const nav = [
      ["nfl", "NFL", link("nfl", "home")],
      ["cfb", "College", link("cfb", "home")],
    ].map(([id, label, href]) => `<a class="sport${id === sportId ? " on" : ""}" href="${href}">${label}</a>`).join("");
    const pages = [
      ["home", "Home", link(sportId, "home")],
      ["board", "Board", link(sportId, "board")],
      ["method", "Method", link(sportId, "method")],
    ].map(([id, label, href]) => `<a href="${href}"${id === active ? ' aria-current="page"' : ""}>${label}</a>`).join("");
    $("mast").innerHTML = `<a class="brand" href="${link(sportId, "home")}"><span class="word">${esc(sport.brand)}</span><em>with odds</em></a><nav>${nav}${pages}</nav><button class="ghost" type="button" onclick="toggleTheme()">Theme</button>`;
    const stamp = (sport.updatedAt || "").slice(0, 16).replace("T", " ") || "—";
    $("foot").innerHTML = `<span>${sport.season} week ${sport.week}</span><span>Hit rates from ESPN game logs through week ${Math.max(sport.week - 1, 0)}</span><span>Odds snapshot ${esc(stamp)} UTC</span><span>DraftKings and Pinnacle, pregame</span>`;
  }

  function postedStats(sport) {
    return sport.stats.filter((spec) => sport.players.some((player) => player.props[spec.id]));
  }

  function renderHome(sportId) {
    const sport = sportData(sportId);
    shell(sportId, "home");
    document.title = sport.brand;
    const cards = postedStats(sport).map((spec) => {
      const lines = sport.players.map((player) => player.props[spec.id] && player.props[spec.id].line).filter((line) => line != null).sort((a, b) => a - b);
      const medianLine = lines.length ? lines[Math.floor(lines.length / 2)] : spec.default;
      return `<li><a href="${link(sportId, "board", { stat: spec.id, mode: "book" })}"><strong>${esc(spec.label)}</strong><span class="proof">${lines.length} posted · median ${fmtNum(medianLine)}</span></a></li>`;
    }).join("");
    const names = sport.players.slice().sort((a, b) => a.name.localeCompare(b.name)).map((player) => `<option value="${esc(player.name)}">`).join("");
    const empty = sportId === "cfb" && !sport.players.length ? "<p class='lede'>The college slate has not been built yet.</p>" : "";
    $("main").innerHTML = `
      <h1>This week's slate</h1>
      <p class="lede">Same desk as Open Prop: how often a player cleared a number, and a model for the chance he does it again. The difference is the price. Every posted prop carries DraftKings and Pinnacle, a devigged fair probability, and the gap between that fair number and the model.</p>
      <dl class="strip">
        <div><dt>Players</dt><dd>${sport.players.length}</dd></div>
        <div><dt>Games cached</dt><dd>${sport.gameRows}</dd></div>
        <div><dt>Games this week</dt><dd>${sport.matchups}</dd></div>
        <div><dt>Season</dt><dd>${sport.season}</dd></div>
      </dl>
      <form class="search" id="search">
        <label class="mono" for="q" style="color:var(--muted);font-size:.68rem;letter-spacing:.08em;text-transform:uppercase">Player</label>
        <input id="q" type="search" name="q" list="players" placeholder="Search the slate" required>
        <datalist id="players">${names}</datalist>
        <button type="submit">Open</button>
      </form>
      ${empty}
      <ul class="cards">${cards}</ul>`;
    $("search").addEventListener("submit", (event) => {
      event.preventDefault();
      location.hash = link(sportId, "player", { q: $("q").value });
    });
  }

  function renderBoard(sportId, qs) {
    const sport = sportData(sportId);
    const available = postedStats(sport);
    let stat = qs.get("stat") || (available[0] && available[0].id) || "pass_yds";
    if (!available.some((item) => item.id === stat) && available.length) stat = available[0].id;
    const spec = sport.stats.find((item) => item.id === stat) || sport.stats[0];
    let mode = qs.get("mode") === "number" ? "number" : "book";
    const floorRaw = qs.get("floor") || "all";
    const floor = floorRaw === "all" ? null : Number(floorRaw);
    let line = numParam(qs, "line");
    if (line == null) line = spec.default;
    const rows = [];
    for (const player of sport.players) {
      const quote = player.props[stat];
      if (!quote || quote.line == null) continue;
      const played = logs(player, stat);
      const check = mode === "book" ? Number(quote.line) : line;
      const last10 = played.slice(-10);
      const [hits, n] = hitRate(last10, check);
      if (floor != null && (n < 5 || hits / n < floor)) continue;
      const fitted = modelOf(sport, player, stat);
      const prob = probability(fitted, check);
      const bookFair = fairOf(quote, check);
      const edge = prob != null && bookFair != null ? prob - bookFair : null;
      rows.push({ player, quote, check, hits, n, seasonN: seasonRows(played, sport.season).length, prob, fair: bookFair, edge });
    }
    rows.sort((a, b) => {
      const ae = a.edge == null ? -9 : a.edge;
      const be = b.edge == null ? -9 : b.edge;
      if (be !== ae) return be - ae;
      const ar = a.n ? a.hits / a.n : 0;
      const br = b.n ? b.hits / b.n : 0;
      if (br !== ar) return br - ar;
      return a.player.name.localeCompare(b.player.name);
    });
    shell(sportId, "board");
    document.title = spec.label + " board";
    const chips = available.map((item) => `<a class="chip${item.id === stat ? " on" : ""}" href="${link(sportId, "board", { stat: item.id, mode, floor: floorRaw, line })}">${esc(item.label)}</a>`).join("");
    const modeChips = [["book", "Each book's line"], ["number", "One number"]].map(([key, label]) => `<a class="chip${key === mode ? " on" : ""}" href="${link(sportId, "board", { stat, mode: key, floor: floorRaw, line })}">${label}</a>`).join("");
    const floors = [["0.6", "60%"], ["0.7", "70%"], ["0.8", "80%"], ["all", "Everyone"]].map(([key, label]) => `<a class="chip${floorRaw === key ? " on" : ""}" href="${link(sportId, "board", { stat, mode, floor: key, line })}">${label}</a>`).join("");
    const body = rows.map((row) => {
      const rate = row.n ? `${row.hits}/${row.n}` : "—";
      const edgeCls = (row.edge || 0) > 0.005 ? "pos" : (row.edge || 0) < -0.005 ? "neg" : "";
      const href = link(sportId, "player", { id: row.player.id, stat, line: row.check, window: "l10" });
      return `<tr><td class="left"><a href="${href}">${esc(row.player.name)}</a></td><td class="left">${esc(row.player.opp || "—")}</td><td>${fmtNum(row.check)}</td><td>${rate}</td><td>${row.seasonN}</td><td>${american(row.quote.dkOver)} / ${american(row.quote.dkUnder)}</td><td>${american(row.quote.pinOver)} / ${american(row.quote.pinUnder)}</td><td>${fmtPct(row.fair)}</td><td>${fmtPct(row.prob)}</td><td class="${edgeCls}">${fmtEdge(row.edge)}</td></tr>`;
    }).join("");
    const caption = mode === "book"
      ? "Each row is scored against that player's posted line. Edge is the model minus the devigged fair over."
      : `Every player is scored against ${fmtNum(line)} ${spec.unit}. Edge shows only when that number is the book's line.`;
    const numberField = mode === "number" ? `<label>Number <input id="line" type="number" step="0.5" value="${line}"></label><button type="submit">Score</button>` : "";
    $("main").innerHTML = `
      <h1>Board</h1>
      <p class="lede">${esc(caption)} The 60/70/80 chips are the short list: last 10 at that rate, with at least five played games. Last 10 reaches into ${sport.season - 1} when this season is still short.</p>
      <div class="chips" style="margin-top:.8rem">${chips}</div>
      <form class="tools" id="board-form">${numberField}</form>
      <div class="chips">${modeChips}<span style="width:.4rem"></span>${floors}</div>
      <p class="note">${rows.length} players · ${esc(spec.label)}</p>
      <div class="table-wrap"><table><thead><tr><th class="left">Player</th><th class="left">Opp</th><th>Line</th><th>Last 10</th><th>${sport.season}</th><th>DK o/u</th><th>Pin o/u</th><th>Fair</th><th>Model</th><th>Edge</th></tr></thead><tbody>${body || '<tr><td class="left" colspan="10">Nobody cleared that floor.</td></tr>'}</tbody></table></div>`;
    const form = $("board-form");
    if (form) form.addEventListener("submit", (event) => {
      event.preventDefault();
      const next = Number($("line").value);
      location.hash = link(sportId, "board", { stat, mode, floor: floorRaw, line: Number.isFinite(next) ? next : line });
    });
  }

  function findPlayers(sport, query) {
    const needle = query.toLowerCase().replace(/\s+/g, " ").trim();
    if (!needle) return [];
    return sport.players.filter((player) => player.name.toLowerCase().includes(needle)).sort((a, b) => a.name.localeCompare(b.name));
  }

  function chart(games, line) {
    if (!games.length) return '<p class="note">No games in this window.</p>';
    const width = 640;
    const height = 180;
    const pad = 28;
    const peak = Math.max(line, ...games.map((game) => game[3]), 1);
    const gap = (width - pad * 2) / Math.max(games.length, 1);
    const y = (value) => height - pad - (value / peak) * (height - pad * 2);
    const bars = [];
    const path = [];
    const labels = [];
    games.forEach((game, i) => {
      const x = pad + i * gap + gap * 0.15;
      const bw = gap * 0.7;
      const yy = y(game[3]);
      const color = game[3] + 1e-9 >= line ? "#0f8a4b" : "#d23b48";
      bars.push(`<rect x="${x.toFixed(1)}" y="${yy.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, y(0) - yy).toFixed(1)}" fill="${color}" rx="2"/>`);
      if (i >= 2) {
        const window = games.slice(Math.max(0, i - 2), i + 1);
        const avg = window.reduce((sum, item) => sum + item[3], 0) / window.length;
        path.push(`${(x + bw / 2).toFixed(1)},${y(avg).toFixed(1)}`);
      }
      labels.push(`<text x="${(x + bw / 2).toFixed(1)}" y="${height - 8}" text-anchor="middle" font-size="10" fill="#5d6872">${esc(game[2])}</text>`);
    });
    const lineY = y(line);
    const poly = path.length ? `<polyline fill="none" stroke="#c48a12" stroke-width="2" points="${path.join(" ")}"/>` : "";
    return `<svg viewBox="0 0 ${width} ${height}" role="img"><line x1="${pad}" y1="${lineY.toFixed(1)}" x2="${width - pad}" y2="${lineY.toFixed(1)}" stroke="#12161b" stroke-dasharray="3 3"/>${bars.join("")}${poly}${labels.join("")}</svg>`;
  }

  function renderPlayer(sportId, qs) {
    const sport = sportData(sportId);
    const query = (qs.get("q") || "").trim();
    let id = qs.get("id") || "";
    if (query && !id) {
      const hits = findPlayers(sport, query);
      const exact = hits.filter((player) => player.name.toLowerCase() === query.toLowerCase());
      const chosen = exact.length === 1 ? exact[0] : hits.length === 1 ? hits[0] : null;
      if (!chosen) {
        shell(sportId, "home");
        document.title = "Search";
        const items = hits.map((player) => `<li><a href="${link(sportId, "player", { id: player.id })}"><strong>${esc(player.name)}</strong><span class="proof">${esc(player.team)}</span></a></li>`).join("");
        const empty = hits.length ? "" : "<p class='lede'>Nobody on this slate matches that search.</p>";
        $("main").innerHTML = `<h1>Players</h1>${empty}<ul class="cards">${items}</ul>`;
        return;
      }
      id = chosen.id;
    }
    const player = sport.players.find((item) => item.id === id);
    shell(sportId, "board");
    if (!player) {
      document.title = "Player";
      $("main").innerHTML = `<h1>No player</h1><p class="lede">That id is not on the week ${sport.week} board.</p>`;
      return;
    }
    const propKeys = Object.keys(player.props);
    let stat = qs.get("stat") || propKeys[0];
    if (!player.props[stat]) stat = propKeys[0];
    const spec = sport.stats.find((item) => item.id === stat);
    const quote = player.props[stat];
    let windowKey = qs.get("window") || "l10";
    if (!["l5", "l10", "l20", "season"].includes(windowKey)) windowKey = "l10";
    let line = numParam(qs, "line");
    if (line == null) line = quote.line != null ? Number(quote.line) : spec.default;
    const rows = logs(player, stat);
    const shown = windowRows(rows, windowKey, sport.season);
    const fitted = modelOf(sport, player, stat);
    const prob = probability(fitted, line);
    const bookFair = fairOf(quote, line);
    const edge = prob != null && bookFair != null ? prob - bookFair : null;
    const [hits, n] = hitRate(shown, line);
    const interval = wilson(hits, n);
    const bandText = interval
      ? `95% Wilson interval on this window: ${Math.round(interval[0] * 100)}% to ${Math.round(interval[1] * 100)}%. ${hits} of ${n} cleared ${fmtNum(line)}.`
      : "No played games in this window.";
    const chips = propKeys.map((key) => {
      const item = sport.stats.find((specItem) => specItem.id === key);
      return `<a class="chip${key === stat ? " on" : ""}" href="${link(sportId, "player", { id, stat: key, window: windowKey })}">${esc(item.label)}</a>`;
    }).join("");
    const splits = [["l5", "Last 5"], ["l10", "Last 10"], ["l20", "Last 20"], ["season", String(sport.season)]].map(([key, label]) => {
      const sample = windowRows(rows, key, sport.season);
      const [h, count] = hitRate(sample, line);
      return `<a class="split${key === windowKey ? " on" : ""}" href="${link(sportId, "player", { id, stat, line, window: key })}"><span class="k">${label}</span><strong>${count ? h + "/" + count : "—"}</strong></a>`;
    }).join("");
    const tiles = shown.map((game) => `<li class="${game[3] + 1e-9 >= line ? "over" : "under"}"><span class="opp">W${esc(game[1])} ${esc(game[2])}</span><strong>${fmtNum(game[3], spec.kind === "count" ? 0 : 1)}</strong></li>`).join("");
    const site = player.team && player.team === player.home ? "home" : "away";
    const opp = site === "home" ? player.away : player.home;
    const edgeCls = (edge || 0) > 0.005 ? "pos" : (edge || 0) < -0.005 ? "neg" : "";
    const range = band80(fitted);
    const modelNote = fitted
      ? `Per-game ${spec.kind}. Mean ${fmtNum(fitted.mean)}, pulled toward ${fitted.priorFrom} (${fmtNum(fitted.priorGames)} pseudo-games). ${fitted.nSeason} games in ${sport.season}.`
      : "The model needs games before it will give a probability.";
    document.title = player.name;
    $("main").innerHTML = `
      <p class="back"><a href="${link(sportId, "board", { stat, mode: "book" })}">Board</a></p>
      <div class="identity"><div><p class="team">${esc(player.team)}</p><h1>${esc(player.name)}</h1></div><p class="asking">${esc(spec.label)}<b>${fmtNum(line)}</b></p></div>
      <div class="chips" style="margin:.7rem 0">${chips}</div>
      <form class="tools" id="player-form">
        <label>Number <input id="line" type="number" step="0.5" value="${line}"></label>
        <button type="submit">Score</button>
        <a class="ghost" href="${link(sportId, "player", { id, stat, line: quote.line, window: windowKey })}">Use book line</a>
        <a class="ghost" id="csv" href="#">CSV</a>
      </form>
      <div class="splits">${splits}</div>
      <p class="band">${esc(bandText)}</p>
      <ul class="results">${tiles || '<li class="under"><span class="opp">—</span><strong>—</strong></li>'}</ul>
      <div class="rooms">
        <section class="room odds"><p class="kicker">Odds</p><h2>Posted this week</h2>
          <p class="note">${esc(player.away)} at ${esc(player.home)} · ${esc(player.name)} is ${site} vs ${esc(opp)} · ${esc(fmtWhen(player.start))}</p>
          <div class="books">
            <div class="book"><span class="note">DraftKings · ${fmtNum(quote.dkLine != null ? quote.dkLine : quote.line)}</span><b>${american(quote.dkOver)} / ${american(quote.dkUnder)}</b></div>
            <div class="book"><span class="note">Pinnacle · ${fmtNum(quote.pinLine)}</span><b>${american(quote.pinOver)} / ${american(quote.pinUnder)}</b></div>
          </div>
          <dl class="facts" style="margin-top:.7rem"><div><dt>Fair over</dt><dd>${fmtPct(bookFair)}</dd></div><div><dt>Fair price</dt><dd>${american(toAmerican(bookFair))}</dd></div><div><dt>Edge</dt><dd class="${edgeCls}">${fmtEdge(edge)}</dd></div></dl>
          <p class="note">Fair is the two-way price with the vig divided out. Pinnacle is used when both sides are up at this number, otherwise DraftKings. Edge is model minus fair, in percentage points. It is blank when the number you typed is not the book's line.</p>
        </section>
        <section class="room model"><p class="kicker">Model</p><h2>Chance of ${fmtNum(line)} or more</h2>
          <p class="chance">${fmtPct(prob)}</p>
          <dl class="facts"><div><dt>Mean</dt><dd>${fmtNum(fitted && fitted.mean)}</dd></div><div><dt>Usual range</dt><dd>${range ? fmtNum(range[0]) + "–" + fmtNum(range[1]) : "—"}</dd></div><div><dt>Kind</dt><dd>${esc(spec.kind)}</dd></div></dl>
          <p class="note">${esc(modelNote)} The usual range is the middle 80%.</p>
        </section>
        <section class="room trend"><p class="kicker">Trend</p><h2>${windowKey === "season" ? "Season" : windowKey.replace("l", "Last ")} games</h2>
          ${chart(shown, line)}
          <p class="note">Green cleared the number, red missed. The gold path is the mean of that game and the two before it, inside this window only.</p>
        </section>
      </div>`;
    $("player-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const next = Number($("line").value);
      location.hash = link(sportId, "player", { id, stat, line: Number.isFinite(next) ? next : line, window: windowKey });
    });
    $("csv").addEventListener("click", (event) => {
      event.preventDefault();
      const lines = ["player,season,week,opponent,stat,value"];
      shown.forEach((game) => lines.push([`"${player.name.replace(/"/g, "")}"`, game[0], game[1], game[2], stat, fmtNum(game[3])].join(",")));
      const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = player.name.replace(/\s+/g, "_") + ".csv";
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  function renderMethod(sportId) {
    const sport = sportData(sportId);
    const league = sportId === "cfb" ? "college football" : "NFL";
    shell(sportId, "method");
    document.title = "Method";
    $("main").innerHTML = `
      <div class="method">
        <h1>Method</h1>
        <p class="lede">The counting rules are the Open Prop rules, moved to a football week. The odds are the part that app leaves out.</p>
        <h2>What a game counts as</h2>
        <p>A game clears the number when the stat is greater than or equal to it. 26 against 26 counts. A line of 64.5 means 65 or more on a counting stat, because the predictive is a count. Yards stay continuous, so 64.5 yards means more than 64.5.</p>
        <p>The log is the player's ESPN ${league} game log. Games in week ${sport.week}, and anything later, are left out. Last 5, last 10, and last 20 are the last that many played games with that stat, reaching into ${sport.season - 1} when ${sport.season} does not have enough. The season window is ${sport.season} only.</p>
        <p>The band under the hit rates is a 95% Wilson interval. Five of the last ten is about 24% to 76%.</p>
        <h2>The model</h2>
        <p>${league[0].toUpperCase() + league.slice(1)} props are per game, so this is a per-game model rather than a rate per minute. Yards use a normal. Counting stats use a negative binomial: wider when the expected total is higher, and never below zero. The percent at a line is that distribution from the line up. The usual range is the middle 80%.</p>
        <p>Last season is the prior. It enters as at most eight pseudo-games, then decays as <span class="mono">exp(−n / 6)</span> once this season's games arrive. A player with no ${sport.season - 1} log shrinks toward the median ${sport.season} rate of players at the same stat. There is no claim that this beats the book.</p>
        <h2>Odds</h2>
        <p>Prices are the week-${sport.week} pregame snapshot: DraftKings and Pinnacle, American odds. A two-way market is devigged by dividing each raw implied probability by the sum of the two. Pinnacle is the fair price when both sides are posted. DraftKings is the fallback. Edge is the model probability minus that fair over, in percentage points. It is only shown when the number being checked is the book's line.</p>
        <p>The snapshot does not move while this page is open.</p>
      </div>`;
  }

  function draw() {
    const { sport, page, qs } = route();
    if (page === "board") renderBoard(sport, qs);
    else if (page === "player") renderPlayer(sport, qs);
    else if (page === "method") renderMethod(sport);
    else renderHome(sport);
  }

  function prepare() {
    for (const sport of Object.values(DATA.sports)) {
      roles[sport.id] = {};
      for (const spec of sport.stats) {
        const means = [];
        for (const player of sport.players) {
          const rows = logs(player, spec.id);
          const season = seasonRows(rows, sport.season).map((row) => row[3]);
          const all = rows.map((row) => row[3]);
          if (season.length >= 3) means.push(season.reduce((a, b) => a + b, 0) / season.length);
          else if (all.length >= 5) means.push(all.reduce((a, b) => a + b, 0) / all.length);
        }
        if (means.length) roles[sport.id][spec.id] = median(means);
      }
    }
  }

  window.addEventListener("hashchange", draw);
  fetch(window.DESK_URL)
    .then((response) => response.json())
    .then((payload) => {
      DATA = payload;
      prepare();
      draw();
    })
    .catch(() => {
      $("main").innerHTML = "<h1>Desk unavailable</h1><p class='lede'>The slate file did not load.</p>";
    });
  void STATS_FALLBACK;
})();
