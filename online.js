// "N players online now" on the landing page, under the download button. The game server
// publishes how many players are connected (counts only: no gamertags or ids) at
// /community/online.json, and the site's web server passes it on at /live/community/online.json,
// as it does the gallery and designs feeds (live.js). The page reads it on load and again every
// minute while the tab is visible. The badge's place is kept from the start, so it appears without
// moving the page, and it stays hidden with no feed (a copy of the site without the route, an
// error, a timeout), a feed that is not what the server sends, one older than five minutes, or
// nobody online. Screen readers hear it when the count changes, not on every refresh.
(function () {
  "use strict";

  var FEED = "/live/community/online.json";
  var MAX_BYTES = 4096;
  var TIMEOUT = 5000;
  var REFRESH = 60000;
  var MAX_AGE = 300;      // seconds: an older feed (or one from further in the future) is not shown
  var MAX_PLAYERS = 1e6;  // anything above is not a real count

  var badge = document.getElementById("online-now");
  if (!badge) return;
  var text = badge.querySelector(".live-text");
  var timer = 0, last = 0, busy = false;

  function isInt(v, max) { return typeof v === "number" && isFinite(v) && Math.floor(v) === v && v >= 0 && v <= max; }

  // The counts of a feed, or null when it is not one the badge can show.
  function parse(doc, now) {
    if (!doc || typeof doc !== "object" || Array.isArray(doc) || doc.version !== 1) return null;
    if (!isInt(doc.generated, 1e11) || !isInt(doc.players, MAX_PLAYERS)) return null;
    if (!isInt(doc.worlds, doc.players) || !isInt(doc.racing, doc.players)) return null;
    if (Math.abs(now - doc.generated) > MAX_AGE) return null;
    return { players: doc.players, worlds: doc.worlds, racing: doc.racing };
  }

  function label(c) {
    var n = c.players.toLocaleString("en-US");
    var s = n + (c.players === 1 ? " player" : " players") + " online now";
    if (c.racing > 0) s += " · " + c.racing.toLocaleString("en-US") + " racing";
    return s;
  }

  function show(c) {
    if (!c || c.players === 0) {
      badge.hidden = true;
      if (text.textContent) text.textContent = "";
      return;
    }
    var s = label(c);
    if (text.textContent !== s) text.textContent = s;  // the live region speaks only on a change
    badge.hidden = false;
  }

  function load() {
    var ctl = window.AbortController ? new AbortController() : null;
    var t = ctl ? setTimeout(function () { ctl.abort(); }, TIMEOUT) : 0;
    return fetch(FEED, { cache: "no-cache", credentials: "omit", headers: { "Accept": "application/json" },
                         signal: ctl ? ctl.signal : undefined })
      .then(function (r) {
        var type = r.headers.get("Content-Type") || "", len = Number(r.headers.get("Content-Length") || 0);
        if (!r.ok || type.indexOf("application/json") !== 0 || len > MAX_BYTES) return null;
        return r.text().then(function (body) {
          return body.length > MAX_BYTES ? null : parse(JSON.parse(body), Date.now() / 1000);
        });
      })
      .catch(function () { return null; })
      .then(function (c) { clearTimeout(t); return c; });
  }

  function refresh() {
    if (busy) return Promise.resolve();
    busy = true;
    last = Date.now();
    return load().then(function (c) { busy = false; show(c); });
  }

  function schedule() {
    clearTimeout(timer);
    timer = 0;
    if (document.hidden) return;
    timer = setTimeout(function () { refresh().then(schedule); }, Math.max(0, REFRESH - (Date.now() - last)));
  }

  document.addEventListener("visibilitychange", schedule);
  if (document.hidden) schedule();  // the first look waits until the tab is shown
  else refresh().then(schedule);

  window.FHOnline = { parse: parse, label: label, refresh: refresh };
})();
