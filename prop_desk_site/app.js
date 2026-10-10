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
    if (points > 0.05) return "Over +" + points.toFixed(1);
    if (points < -0.05) return "Under +" + (-points).toFixed(1);
    return "0.0";
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

  function posted(quote, key, line) {
    let value = quote[key];
    if (value == null && key === "dkLine") value = quote.line;
    return value != null && Math.abs(value - line) < 0.05;
  }

  function dkFair(quote, line) {
    if (!posted(quote, "dkLine", line)) return null;
    if (quote.dkOver != null && quote.dkUnder != null) {
      const both = devig(quote.dkOver, quote.dkUnder);
      if (both != null) return both;
    }
    return quote.dkOver != null ? implied(quote.dkOver) : null;
  }

  function pinFair(quote, line) {
    if (!posted(quote, "pinLine", line)) return null;
    if (quote.pinOver != null && quote.pinUnder != null) {
      const both = devig(quote.pinOver, quote.pinUnder);
      if (both != null) return both;
    }
    return quote.pinOver != null ? implied(quote.pinOver) : null;
  }

  function fmtGap(gap) {
    if (gap == null || !Number.isFinite(gap)) return "—";
    const points = 100 * gap;
    if (points > 0.5) return "DK +" + points.toFixed(1);
    if (points < -0.5) return "Pin +" + (-points).toFixed(1);
    return "0.0";
  }

  function offerOf(sportId, player, stat, line) {
    const quote = player.props[stat] || {};
    const fitted = modelOf(sportData(sportId), player, stat);
    const raw = probability(fitted, line);
    const fair = dkFair(quote, line);
    const pin = pinFair(quote, line);
    const shown = raw;
    const allowUnder = sportId !== "cfb" && quote.dkUnder != null;
    let edge = null;
    if (shown != null && fair != null) {
      const gap = shown - fair;
      if (allowUnder || gap >= -0.005) edge = gap;
    }
    return { fitted, prob: shown, fair, edge, books: pin == null || fair == null ? null : pin - fair };
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

  const DECAY_HALF_LIFE = 3;

  function decayMean(values) {
    let total = 0;
    let weight = 0;
    values.forEach((value, index) => {
      const w = Math.exp(-(values.length - 1 - index) / DECAY_HALF_LIFE);
      total += w * value;
      weight += w;
    });
    return weight ? total / weight : 0;
  }

  function weightedSd(values) {
    if (values.length < 2) return 0;
    const mean = decayMean(values);
    let total = 0;
    let weight = 0;
    values.forEach((value, index) => {
      const w = Math.exp(-(values.length - 1 - index) / DECAY_HALF_LIFE);
      total += w * (value - mean) ** 2;
      weight += w;
    });
    return weight ? Math.sqrt(total / weight) : 0;
  }

  function modelOf(sport, player, stat) {
    const spec = sport.stats.find((item) => item.id === stat);
    const rows = logs(player, stat);
    const values = rows.map((row) => row[3]);
    const thisSeason = seasonRows(rows, sport.season);
    const role = roles[sport.id] && roles[sport.id][stat];
    if (!values.length && role == null) return null;
    const base = values.length ? decayMean(values) : role;
    let sd = values.length ? weightedSd(values) : 0;
    const defense = player.defense && player.defense[stat] != null ? Number(player.defense[stat]) : 1;
    const market = marketFactor(sport.id, player, stat);
    const extra = player.injury && player.injury[stat] != null ? Number(player.injury[stat]) : 0;
    const absent = (player.absent && player.absent[stat]) || [];
    const availability = player.availability == null ? 1 : Number(player.availability);
    let mean;
    let injury = extra;
    if (availability <= 0.25) {
      mean = 0;
      injury = 0;
    } else {
      mean = Math.max(0, base * defense * market + injury);
    }
    if (spec.kind === "yards") sd = Math.max(sd, 0.22 * Math.max(mean, 1));
    else sd = Math.max(sd, Math.sqrt(Math.max(mean, 0.05)));
    return {
      kind: spec.kind, mean, base, defense, market, factor: defense * market, sd,
      nSeason: thisSeason.length, halfLife: DECAY_HALF_LIFE, injury, absent, availability,
    };
  }

  function describeModel(fitted, spec, sport) {
    if (!fitted) return "The model needs games before it will give a probability.";
    const absent = (fitted.absent || []).join(", ");
    let role = "";
    if (fitted.availability <= 0.25) role = " He is out, so the mean is zero.";
    else if (fitted.injury > 0 && absent) role = ` Added ${fmtNum(fitted.injury)} because ${absent} is out.`;
    else if (absent) role = ` ${absent} is out.`;
    return `Per-game ${spec.kind}. Mean ${fmtNum(fitted.mean)}, from a recent rate of ${fmtNum(fitted.base)}. Games fade with a ${fmtNum(fitted.halfLife)}-game half-life, so the last few count the most. Opponent defense ${fmtNum(fitted.defense)} and the spread and total ${fmtNum(fitted.market)}.${role} ${fitted.nSeason} games in ${sport.season}.`;
  }

  function marketFactor(sportId, player, stat) {
    const spread = player.spread;
    const total = player.total;
    const side = player.side === "home" || player.side === "away" ? player.side : "";
    if (spread == null && total == null) return 1;
    const vol = volumeFactor(sportId, stat, side, spread, total);
    let margin = null;
    if (side === "home" && spread != null) margin = -spread;
    else if (side === "away" && spread != null) margin = spread;
    return vol * scriptFactor(stat, margin);
  }

  function volumeFactor(sportId, stat, side, spread, total) {
    const volume = ["pass_yds", "pass_attempts", "pass_completions", "pass_rush_yds", "rec_yds", "receptions", "rr_yds", "rush_yds", "rush_attempts"];
    const td = ["pass_tds", "tds"];
    if (!volume.includes(stat) && !td.includes(stat)) return 1;
    const baseline = sportId === "nfl" ? 45 : 54;
    let vol = 1;
    if (spread != null && total != null && side) {
      const teamPts = side === "home" ? (total - spread) / 2 : (total + spread) / 2;
      const raw = teamPts / Math.max(baseline / 2, 1);
      vol = Math.max(0.84, Math.min(1.16, 0.86 + (raw - 1) * 0.72));
    } else if (total != null) {
      vol = Math.max(0.88, Math.min(1.12, 1 + ((total - baseline) / baseline) * 0.48));
    }
    if (td.includes(stat)) return 0.55 + 0.45 * vol;
    return vol;
  }

  function scriptFactor(stat, margin) {
    if (margin == null) return 1;
    let key = stat;
    if (stat === "rr_yds") key = "rush_yds";
    else if (stat === "pass_rush_yds" || stat === "rec_yds" || stat === "receptions") key = "pass_yds";
    if (key === "pass_yds" || key === "pass_attempts" || key === "pass_completions") {
      if (margin > 7) return Math.max(0.78, 1 - (Math.min(28, margin) - 7) * 0.016);
      if (margin < -7) return Math.min(1.08, 1 + Math.min(21, Math.abs(margin)) * 0.006);
    }
    if (key === "pass_tds" && margin > 10) return Math.max(0.85, 1 - (Math.min(28, margin) - 10) * 0.012);
    if (key === "rush_yds" || key === "rush_attempts") {
      if (margin > 10) return Math.min(1.12, 1 + (Math.min(28, margin) - 10) * 0.008);
      if (margin < -10) return Math.max(0.82, 1 - (Math.min(28, Math.abs(margin)) - 10) * 0.012);
    }
    if (key === "tds" && margin > 10) return Math.max(0.88, 1 - (Math.min(28, margin) - 10) * 0.006);
    return 1;
  }

  function probability(model, line) {
    if (!model || !(model.sd > 0) || !Number.isFinite(model.mean)) return null;
    if (model.mean <= 1e-8) {
      if (model.kind === "yards") return 1 - normCdf(line, 0, Math.max(model.sd, 1));
      return line > 0 ? 0 : 1;
    }
    if (model.kind === "yards") return 1 - normCdf(line, model.mean, model.sd);
    const variance = model.sd * model.sd;
    const threshold = Math.ceil(line - 1e-9);
    if (variance <= model.mean + 1e-9) return 1 - poissonCdf(threshold - 1, Math.max(model.mean, 1e-6));
    const k = (model.mean * model.mean) / (variance - model.mean);
    if (!(k + model.mean > 0)) return line > 0 ? 0 : 1;
    const p = k / (k + model.mean);
    return 1 - nbinomCdf(threshold - 1, k, p);
  }

  function band80(model) {
    if (!model) return null;
    if (model.kind === "yards") {
      const z = 1.2815515655446004;
      return [Math.max(0, model.mean - z * model.sd), model.mean + z * model.sd];
    }
    if (model.mean <= 1e-8) return [0, 0];
    const variance = model.sd * model.sd;
    const distCdf = (k) => {
      if (variance <= model.mean + 1e-9) return poissonCdf(k, Math.max(model.mean, 1e-6));
      const n = (model.mean * model.mean) / (variance - model.mean);
      if (!(n + model.mean > 0)) return 1;
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
      const priced = offerOf(sportId, player, stat, check);
      rows.push({
        player, quote, check, hits, n, seasonN: seasonRows(played, sport.season).length,
        prob: priced.prob, fair: priced.fair, edge: priced.edge, books: priced.books,
      });
    }
    rows.sort((a, b) => {
      const ae = a.edge == null ? -9 : Math.abs(a.edge);
      const be = b.edge == null ? -9 : Math.abs(b.edge);
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
      return `<tr><td class="left"><a href="${href}">${esc(row.player.name)}</a></td><td class="left">${esc(row.player.opp || "—")}</td><td>${fmtNum(row.check)}</td><td>${rate}</td><td>${row.seasonN}</td><td>${american(row.quote.dkOver)} / ${american(row.quote.dkUnder)}</td><td>${american(row.quote.pinOver)} / ${american(row.quote.pinUnder)}</td><td>${fmtPct(row.fair)}</td><td>${fmtPct(row.prob)}</td><td class="${edgeCls}">${fmtEdge(row.edge)}</td><td>${fmtGap(row.books)}</td></tr>`;
    }).join("");
    const caption = mode === "book"
      ? "Each row is scored against that player's posted line. Side is the over or the under against DraftKings. College rows are overs only."
      : `Every player is scored against ${fmtNum(line)} ${spec.unit}. The side shows only when that number is the DraftKings line.`;
    const numberField = mode === "number" ? `<label>Number <input id="line" type="number" step="0.5" value="${line}"></label><button type="submit">Score</button>` : "";
    $("main").innerHTML = `
      <h1>Board</h1>
      <p class="lede">${esc(caption)} The 60/70/80 chips are the short list: last 10 at that rate, with at least five played games. Last 10 reaches into ${sport.season - 1} when this season is still short.</p>
      <div class="chips" style="margin-top:.8rem">${chips}</div>
      <form class="tools" id="board-form">${numberField}</form>
      <div class="chips">${modeChips}<span style="width:.4rem"></span>${floors}</div>
      <p class="note">${rows.length} players · ${esc(spec.label)}</p>
      <div class="table-wrap"><table><thead><tr><th class="left">Player</th><th class="left">Opp</th><th>Line</th><th>Last 10</th><th>${sport.season}</th><th>DK o/u</th><th>Pin o/u</th><th>DK</th><th>Model</th><th>Side</th><th>vs Pin</th></tr></thead><tbody>${body || '<tr><td class="left" colspan="11">Nobody cleared that floor.</td></tr>'}</tbody></table></div>`;
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
    const priced = offerOf(sportId, player, stat, line);
    const fitted = priced.fitted;
    const prob = priced.prob;
    const bookFair = priced.fair;
    const edge = priced.edge;
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
    const modelNote = describeModel(fitted, spec, sport);
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
          <dl class="facts" style="margin-top:.7rem"><div><dt>DK fair</dt><dd>${fmtPct(bookFair)}</dd></div><div><dt>DK price</dt><dd>${american(toAmerican(bookFair))}</dd></div><div><dt>Side</dt><dd class="${edgeCls}">${fmtEdge(edge)}</dd></div><div><dt>vs Pin</dt><dd>${fmtGap(priced.books)}</dd></div></dl>
          <p class="note">The side is the model against DraftKings only. A two-way DraftKings price is devigged. College sides are overs only, because DraftKings is not posting the under. vs Pin names the book with the cheaper over. It is blank when the number you typed is not the book's line.</p>
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
        <p>The mean is a time-decayed average of the log. Weight falls by half every three games, so last week counts twice what a game three back counts, and last season fades behind this season. A player with no log uses the median rate at that stat. Opponent defense and this game's spread and total scale that rate. If a teammate at the same position is out and we have his recent production, part of it is added. A player who is out is projected at zero. The DraftKings number is not an input.</p>
        <h2>Odds</h2>
        <p>The side is how far that model sits from the DraftKings price. Pinnacle is not part of the edge. vs Pin shows which book is cheaper on the over. College football has no under at DraftKings, so those rows never show an under. The side is blank when the number being checked is not the DraftKings line.</p>
        <p>Refresh NFL lines and Refresh college lines each reload that league's latest DraftKings and Pinnacle prices. A scheduled job pulls those prices off the board and republishes them. Hit rates stay on the saved game logs.</p>
      </div>`;
  }

  async function refreshSport(sportId) {
    const button = $("refresh-" + sportId);
    const status = $("refresh-status");
    const label = sportId === "nfl" ? "NFL" : "College";
    if (!button || !DATA) return;
    button.disabled = true;
    if (status) status.textContent = "Loading the latest " + label + " lines…";
    try {
      const response = await fetch("./desk.json?v=" + Date.now());
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      const next = payload.sports && payload.sports[sportId];
      if (!next || !Array.isArray(next.players)) throw new Error("missing slate");
      DATA.sports[sportId] = next;
      prepare();
      draw();
      const stamp = (next.updatedAt || "").slice(0, 16).replace("T", " ");
      if (status) status.textContent = label + " lines loaded · " + (stamp || "just now") + " UTC";
    } catch (error) {
      if (status) status.textContent = "Couldn't load the latest " + label + " lines.";
    } finally {
      button.disabled = false;
    }
  }

  function bindRefresh() {
    for (const sportId of ["nfl", "cfb"]) {
      const button = $("refresh-" + sportId);
      if (!button || button.dataset.bound) continue;
      button.dataset.bound = "1";
      button.disabled = false;
      button.addEventListener("click", () => refreshSport(sportId));
    }
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
      bindRefresh();
      draw();
    })
    .catch(() => {
      $("main").innerHTML = "<h1>Desk unavailable</h1><p class='lede'>The slate file did not load.</p>";
    });
  void STATS_FALLBACK;
})();
