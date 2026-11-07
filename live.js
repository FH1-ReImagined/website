// The newest community photos and designs, before the next build bakes them into the site.
// gallery.html and designs.html (a list or page marked data-photos-live or data-designs-live)
// read the game server's public feeds through this site, at /live/community/gallery.json and
// /live/community/designs.json: the same feeds tools/build_gallery.py and build_designs.py read,
// passed on read-only, and cached for a few seconds, by the site's web server. The feed decides
// what is shown and in which order: an item the build already holds keeps the build's copy (its
// re-encoded picture and thumbnail, its likes), an item only the feed lists yet is shown from the
// feed's own picture route (/live/community/img/<id>, /live/community/design-img/<id>.png), and
// an item the feed no longer lists (hidden or deleted on the server) is left out at once.
// Each entry is checked as the builds check it (the same ids, codes, hashes and text limits;
// anything else in it is ignored), and player text goes into the pages as text only. With no
// feed (a copy of the site without the route, an error, a timeout) the pages show the build's
// data as before.
(function () {
  "use strict";

  var FEEDS = { photo: "/live/community/gallery.json", design: "/live/community/designs.json" };
  var MAX_ITEMS = 500;
  var MAX_BYTES = 8 << 20;
  var TIMEOUT = 5000;
  var LIMITS = { gamertag: 32, title: 80, car: 80, location: 80 };

  function isInt(v, max) { return typeof v === "number" && isFinite(v) && Math.floor(v) === v && v >= 0 && v <= max; }

  // Player text as the builds keep it (clean_text): no control or format characters (but the
  // zero-width joiner of emoji), whitespace collapsed, at most limit characters.
  var DROP = /[\u0000-\u001f\u007f-\u009f­؀-؅؜۝܏᠎​‌‎‏‪-‮⁠-⁤⁦-⁯﻿￹-￻\ud800-\udfff]/g;
  function clean(v, limit) {
    if (typeof v !== "string") return "";
    var s = v.replace(/\s/g, " ");
    try { s = s.replace(/[\p{Cc}\p{Cf}\p{Cs}]/gu, function (c) { return c === "‍" ? c : ""; }); }
    catch (e) { s = s.replace(DROP, ""); }  // a browser without Unicode property escapes
    return Array.from(s.split(/ +/).join(" ").trim()).slice(0, limit).join("").trim();
  }
  function owner(raw) { return typeof raw.owner === "string" && /^[0-9a-f]{12}$/.test(raw.owner) ? raw.owner : ""; }

  function photoOf(raw) {
    if (!raw || typeof raw !== "object") return null;
    var id = raw.id, m = typeof raw.image === "string" ? /^\/community\/img\/([A-Z2-7]{12})\.(png|jpg)$/.exec(raw.image) : null;
    if (typeof id !== "string" || !/^[A-Z2-7]{12}$/.test(id) || !m || m[1] !== id) return null;
    if (typeof raw.sha256 !== "string" || !/^[0-9a-fA-F]{64}$/.test(raw.sha256)) return null;
    if (!isInt(raw.approvedAt, 1e11) || !isInt(raw.w, 7680) || !isInt(raw.h, 7680) || raw.w < 180 || raw.h < 180) return null;
    var gamertag = clean(raw.gamertag, LIMITS.gamertag);
    if (!gamertag) return null;
    var src = "/live" + raw.image;
    var p = { id: id, title: clean(raw.title, LIMITS.title), gamertag: gamertag,
              src: src, w: raw.w, h: raw.h, thumb: src, tw: raw.w, th: raw.h, live: true };
    if (owner(raw)) p.owner = raw.owner;
    ["car", "location"].forEach(function (k) { var t = clean(raw[k], LIMITS[k]); if (t) p[k] = t; });
    return p;
  }

  function designOf(raw) {
    if (!raw || typeof raw !== "object") return null;
    var id = raw.id;
    if (typeof id !== "string" || !/^[A-Za-z0-9]{6,32}$/.test(id)) return null;
    if (raw.type !== "livery" && raw.type !== "vinylgroup") return null;
    if (typeof raw.code !== "string" || !/^[A-Za-z0-9]{4,15}$/.test(raw.code)) return null;
    if (typeof raw.sha256 !== "string" || !/^[0-9a-fA-F]{64}$/.test(raw.sha256)) return null;
    if (raw.preview !== "/community/design-img/" + id + ".png") return null;
    if (!isInt(raw.createdAt, 1e11) || !isInt(raw.w, 1024) || !isInt(raw.h, 1024) || !raw.w || !raw.h) return null;
    var gamertag = clean(raw.gamertag, LIMITS.gamertag);
    if (!gamertag) return null;
    var downloads = raw.downloads === undefined ? 0 : raw.downloads;
    if (!isInt(downloads, 1e9)) return null;
    var rating = 0, ratings = 0, r = raw.rating;
    if (r && typeof r === "object" && isInt(r.count, 1e9) && r.count > 0 && typeof r.avg === "number" && r.avg >= 0 && r.avg <= 5) {
      rating = Math.round(r.avg * 100) / 100;
      ratings = r.count;
    }
    var d = { id: id, type: raw.type, code: raw.code, title: clean(raw.title, LIMITS.title), gamertag: gamertag,
              downloads: downloads, rating: rating, ratings: ratings, createdAt: raw.createdAt,
              src: "/live" + raw.preview, w: raw.w, h: raw.h, live: true };
    if (owner(raw)) d.owner = raw.owner;
    if (raw.type === "livery" && raw.car && typeof raw.car === "object") {
      var car = clean(raw.car.name, LIMITS.car);
      if (car) d.car = car;
    }
    return d;
  }

  // The feed's entries, or null when there is no usable feed.
  function load(kind) {
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, TIMEOUT) : 0;
    return fetch(FEEDS[kind], { cache: "no-cache", credentials: "omit", headers: { "Accept": "application/json" },
                                signal: ctl ? ctl.signal : undefined })
      .then(function (r) {
        var type = r.headers.get("Content-Type") || "", len = Number(r.headers.get("Content-Length") || 0);
        if (!r.ok || type.indexOf("application/json") !== 0 || len > MAX_BYTES) return null;
        return r.text().then(function (t) {
          if (t.length > MAX_BYTES) return null;
          var doc = JSON.parse(t), list = doc && doc.version === 1 ? doc[kind === "photo" ? "photos" : "designs"] : null;
          return Array.isArray(list) ? list : null;
        });
      })
      .catch(function () { return null; })
      .then(function (list) { clearTimeout(timer); return list; });
  }

  // The items to show: the feed's, in its order, each the build's copy when the build has it.
  // Without a feed, the build's items as they are.
  function merge(kind, baked) {
    return load(kind).then(function (feed) {
      if (!feed) return baked;
      var built = {}, seen = {}, out = [];
      baked.forEach(function (b) { built[b.id] = b; });
      for (var i = 0; i < feed.length && out.length < MAX_ITEMS; i++) {
        var item = kind === "photo" ? photoOf(feed[i]) : designOf(feed[i]);
        if (!item || seen[item.id]) continue;
        seen[item.id] = true;
        out.push(built[item.id] || item);
      }
      return out;
    });
  }

  window.FHLive = { merge: merge, photoOf: photoOf, designOf: designOf, clean: clean };
})();
