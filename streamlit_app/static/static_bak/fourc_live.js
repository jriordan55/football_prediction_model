(function () {
  var root = document;
  var sport = document.currentScript && document.currentScript.getAttribute("data-sport");
  if (!sport) sport = "cfb";
  var feedUrl = "/app/static/odds_live_" + sport + ".json";
  var lastGen = "";

  function relSync(iso) {
    if (!iso) return "just now";
    var t = Date.parse(iso);
    if (!t || isNaN(t)) return "just now";
    var sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (sec < 8) return "just now";
    if (sec < 60) return sec + "s ago";
    var min = Math.floor(sec / 60);
    if (min < 60) return min + " min ago";
    return Math.floor(min / 60) + "h ago";
  }

  function patchSyncLabel(iso) {
    var cap = root.querySelector("#gp-odds-sync");
    if (cap) cap.textContent = "4C Odds · synced " + relSync(iso);
  }

  function fmtSpread(abbr, line) {
    if (line === null || line === undefined || line === "") return abbr + " —";
    var n = Number(line);
    if (!isFinite(n)) return abbr + " —";
    if (n > 0) return abbr + " +" + n;
    if (n < 0) return abbr + " " + n;
    return abbr + " PK";
  }

  function fmtTotal(side, line) {
    if (line === null || line === undefined || line === "") return side + " —";
    return side + " " + Number(line);
  }

  function patchCell(card, key, quote, labelFn) {
    if (!quote) return;
    var cell = card.querySelector('[data-fourc-key="' + key + '"]');
    if (!cell) return;
    var labelEl = cell.querySelector(".bo-mg-q-label");
    var priceEl = cell.querySelector(".bo-mg-q-price");
    var bookEl = cell.querySelector(".bo-mg-q-book-id");
    if (labelFn && labelEl && quote.line !== undefined) {
      var abbr = labelEl.getAttribute("data-abbr") || "";
      var side = labelEl.getAttribute("data-side") || "";
      if (key.indexOf("spread") === 0) labelEl.textContent = fmtSpread(abbr, quote.line);
      else if (key.indexOf("total") === 0) labelEl.textContent = fmtTotal(side, quote.line);
    }
    if (priceEl && quote.price) priceEl.textContent = quote.price;
    if (bookEl && quote.book_id) {
      var prevBook = bookEl.getAttribute("data-book");
      bookEl.setAttribute("data-book", quote.book_id);
      if (prevBook !== quote.book_id) {
        var imgEl = bookEl.querySelector("img");
        if (imgEl && quote.logo_url) {
          imgEl.src = quote.logo_url;
          imgEl.alt = quote.book_id;
          if (quote.logo_fallback) {
            imgEl.onerror = function () {
              this.onerror = null;
              this.src = quote.logo_fallback;
            };
          }
        }
      }
    }
  }

  function tick() {
    fetch(feedUrl + "?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.games) return;
        patchSyncLabel(data.updated_at);
        if (data.updated_at === lastGen) return;
        lastGen = data.updated_at;
        root.querySelectorAll(".bo-mg-card[data-fourc-id]").forEach(function (card) {
          var gid = card.getAttribute("data-fourc-id");
          var g = data.games[gid];
          if (!g || !g.quotes) return;
          var q = g.quotes;
          patchCell(card, "spread_away", q.spread_away, true);
          patchCell(card, "spread_home", q.spread_home, true);
          patchCell(card, "total_over", q.total_over, true);
          patchCell(card, "total_under", q.total_under, true);
        });
      })
      .catch(function () {});
  }

  tick();
  setInterval(tick, 2000);
  setInterval(function () {
    fetch(feedUrl + "?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) { patchSyncLabel(data && data.updated_at); })
      .catch(function () {});
  }, 5000);
})();
