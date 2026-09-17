(function () {
  var sport = document.body.dataset.peSport || "cfb";
  var feedUrl = "/app/static/odds_live_" + sport + ".json";
  var pollMs = Number(document.body.dataset.pePollMs || 15000);
  var lastGen = "";

  function norm(s) {
    return String(s || "")
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "");
  }

  function findGame(data, home, away) {
    var games = (data && data.games) || {};
    var nh = norm(home);
    var na = norm(away);
    for (var gid in games) {
      var g = games[gid];
      if (!g) continue;
      if (norm(g.home) === nh && norm(g.away) === na) return g;
      if (norm(g.home).indexOf(nh) >= 0 && norm(g.away).indexOf(na) >= 0) return g;
    }
    return null;
  }

  function tick() {
    fetch(feedUrl + "?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        if (data.updated_at === lastGen) return;
        lastGen = data.updated_at;
        var meta = document.querySelector(".pe-sync-ts");
        if (meta && data.updated_at) meta.textContent = meta.textContent.split("·")[0] + "· 4C · " + data.updated_at.slice(11, 19);
        var board = document.querySelector(".pe-board");
        if (!board) return;
        var gm = findGame(data, board.getAttribute("data-pe-home"), board.getAttribute("data-pe-away"));
        if (!gm || !gm.quotes) return;
        board.querySelectorAll("tr[data-fourc-key]").forEach(function (row) {
          var key = row.getAttribute("data-fourc-key");
          if (!key) return;
          var q = gm.quotes[key];
          if (!q) return;
          var cell = row.querySelector(".pe-book");
          if (cell && q.price) cell.textContent = q.price;
        });
      })
      .catch(function () {});
  }

  tick();
  setInterval(tick, pollMs);
})();
