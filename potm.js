// Photo of the Month. The Pages workflow writes data/photo-of-the-month.json every hour from the
// accounts service (tools/build_potm.py): the newest winner ("featured"), every winner, and the
// standings of this month and the month before, each photo with the files the gallery published.
// - The home page (an element with data-potm-featured) shows the featured winner from that file
//   only: it never calls the accounts service.
// - photo-of-the-month.html (#potm-board) shows this month's standings, live: it asks the public
//   /api/potm/standings (no cookie is sent) when the page opens and every minute while it is
//   visible, and keeps the built copy when that fails (a copy of the site without /api/). Then
//   the month before (being checked, or its winner) and the past winners. Each entry has its
//   like button (likes.js) and share buttons.
// - Every page fills [data-potm-days] (how old an account must be for its likes to count) and
//   [data-potm-from] (the first contest month) from the same file.
// Titles and gamertags are the players' own text: they go in with textContent only.
(function () {
  "use strict";

  var board = document.getElementById("potm-board");
  var featured = document.querySelectorAll("[data-potm-featured]");
  var days = document.querySelectorAll("[data-potm-days]");
  var froms = document.querySelectorAll("[data-potm-from]");
  if (!board && !featured.length && !days.length && !froms.length) return;

  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"];
  var ID = /^[A-Za-z2-7]{12}$/, MONTH = /^[0-9]{4}-(0[1-9]|1[0-2])$/;
  var LIVE_SECONDS = 60;

  function text(v) { return typeof v === "string" ? v : ""; }
  function num(v) { return typeof v === "number" && isFinite(v) && v >= 0 && v < 1e12 ? Math.round(v) : null; }
  function size(v) { var n = num(v); return n && n < 100000 ? n : 0; }
  function el(tag, value, cls) {
    var e = document.createElement(tag);
    if (value) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }
  function monthName(m) {
    return MONTH.test(text(m)) ? MONTHS[parseInt(m.slice(5), 10) - 1] + " " + m.slice(0, 4) : "";
  }
  function when(unix) {
    var n = num(unix);
    if (n === null) return "";
    var d = new Date(n * 1000);
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear() + ", " +
      ("0" + d.getUTCHours()).slice(-2) + ":" + ("0" + d.getUTCMinutes()).slice(-2) + " UTC";
  }
  function plural(n, one, many) { return n.toLocaleString("en") + " " + (n === 1 ? one : many); }
  function hasPhoto(p) {
    return p && ID.test(text(p.id)) && p.src === "photos/" + p.id + ".jpg" && p.thumb === "photos/" + p.id + "-thumb.jpg" &&
      size(p.w) && size(p.h) && size(p.tw) && size(p.th);
  }
  function get(path, opts) {
    return fetch(path, opts || { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  // The picture of an entry or winner: a button for the lightbox (photos.js), or a placeholder
  // when the gallery has not published the photo (yet, or any more).
  function picture(p, big) {
    if (!hasPhoto(p)) {
      return el("div", "This photo is not in the gallery right now.", "potm-nophoto");
    }
    var b = el("button");
    b.type = "button";
    b.dataset.full = p.src;
    b.dataset.w = size(p.w);
    b.dataset.h = size(p.h);
    b.dataset.cap = text(p.title) ? text(p.title) + " — " + text(p.gamertag) : text(p.gamertag);
    var img = el("img");
    img.src = big ? p.src : p.thumb;
    img.width = big ? size(p.w) : size(p.tw);
    img.height = big ? size(p.h) : size(p.th);
    img.loading = big ? "eager" : "lazy";
    img.decoding = "async";
    img.alt = (text(p.title) ? text(p.title) + ": a" : "A") + " photo by " + text(p.gamertag);
    b.append(img);
    return b;
  }

  function byline(p) {
    var cap = el("p", "", "potm-by");
    cap.append(el("b", text(p.title) || "Untitled"), document.createTextNode(" by "));
    if (/^[0-9a-f]{12}$/.test(text(p.owner))) {
      var a = el("a", text(p.gamertag));
      a.href = "profile.html?p=" + p.owner;
      cap.append(a);
    } else {
      cap.append(el("span", text(p.gamertag)));
    }
    return cap;
  }

  // ---- The home page ----

  function showFeatured(doc) {
    var w = doc && doc.featured;
    featured.forEach(function (section) {
      var slot = section.querySelector("[data-potm-photo]");
      if (!w || !hasPhoto(w) || !slot) { section.hidden = true; return; }
      var name = section.querySelector("[data-potm-month]");
      if (name) name.textContent = monthName(w.month);
      slot.replaceChildren(picture(w, true), byline(w));
      section.hidden = false;
    });
    if (featured.length) document.dispatchEvent(new Event("photos:shown"));  // script.js lays the road out again
  }

  // ---- Sharing ----

  function pageUrl(hash) {
    return location.origin + location.pathname + (hash ? "#" + hash : "");
  }

  function shareLinks(url, message) {
    var u = encodeURIComponent(url), t = encodeURIComponent(message);
    return [
      ["X", "https://twitter.com/intent/tweet?text=" + t + "&url=" + u],
      ["Bluesky", "https://bsky.app/intent/compose?text=" + encodeURIComponent(message + " " + url)],
      ["Reddit", "https://www.reddit.com/submit?url=" + u + "&title=" + t],
      ["Facebook", "https://www.facebook.com/sharer/sharer.php?u=" + u]
    ];
  }

  function shareBox(url, message, label) {
    var box = el("div", "", "potm-share");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", label);
    if (navigator.share) {
      var native = el("button", "Share", "potm-share-btn");
      native.type = "button";
      native.addEventListener("click", function () {
        navigator.share({ title: document.title, text: message, url: url }).catch(function () {});
      });
      box.append(native);
    }
    var copy = el("button", "Copy link", "potm-share-btn");
    copy.type = "button";
    copy.addEventListener("click", function () {
      var done = function () { copy.textContent = "Link copied"; setTimeout(function () { copy.textContent = "Copy link"; }, 3000); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, function () { prompt("Copy this link", url); });
      else prompt("Copy this link", url);
    });
    box.append(copy);
    shareLinks(url, message).forEach(function (s) {
      var a = el("a", s[0], "potm-share-btn");
      a.href = s[1];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.setAttribute("aria-label", "Share on " + s[0]);
      box.append(a);
    });
    return box;
  }

  // ---- The leaderboard page ----

  var built = null, gallery = {};

  function withPhoto(e) {
    var g = gallery[e.id];
    if (!g || hasPhoto(e)) return e;
    var out = {};
    Object.keys(e).forEach(function (k) { out[k] = e[k]; });
    ["src", "w", "h", "thumb", "tw", "th", "owner"].forEach(function (k) { if (g[k] !== undefined) out[k] = g[k]; });
    if (!text(out.gamertag)) out.gamertag = text(g.gamertag);
    return out;
  }

  function entryItem(e, month, open) {
    var li = el("li", "", "potm-entry");
    li.id = "photo-" + e.id;
    var rank = el("span", "#" + e.rank, "potm-rank");
    rank.setAttribute("aria-label", "Place " + e.rank);
    li.append(rank, picture(e, false));
    var info = el("div", "", "potm-info");
    info.append(byline(e));
    var votes = num(e.votes) || 0, likes = num(e.likes);
    var counts = el("p", "", "potm-counts");
    counts.append(el("b", plural(votes, "vote", "votes")));
    if (likes !== null && likes !== votes) counts.append(document.createTextNode(" · " + plural(likes, "like", "likes") + " in all"));
    info.append(counts);
    var foot = el("div", "", "photo-foot");
    if (open && window.FHLikes) foot.append(window.FHLikes.slot("photo", e.id, e.likes));
    info.append(foot);
    if (open) {
      var msg = "Vote for " + (text(e.title) ? "“" + text(e.title) + "”" : "this shot") + " by " + text(e.gamertag) +
        " in the Forza Horizon 1: ReImagined Photo of the Month (" + monthName(month) + ")";
      info.append(shareBox(pageUrl("photo-" + e.id), msg, "Share this photo"));
    }
    li.append(info);
    return li;
  }

  function showMonth(m, where) {
    var head = where.querySelector("[data-potm-head]"), list = where.querySelector("[data-potm-list]");
    var note = where.querySelector("[data-potm-note]");
    if (!m || !MONTH.test(text(m.month))) {
      where.hidden = where.hasAttribute("data-potm-optional");
      if (head) head.textContent = "";
      if (list) list.replaceChildren();
      if (note) note.textContent = "Photo of the Month has not started yet.";
      return;
    }
    where.hidden = false;
    var open = m.state === "open";
    var top = (Array.isArray(m.top) ? m.top : []).filter(function (e) { return e && ID.test(text(e.id)); }).map(withPhoto);
    if (head) head.textContent = monthName(m.month);
    var entries = num(m.entries) || 0, says;
    if (m.state === "upcoming") says = "The first Photo of the Month starts " + when(m.start) + ".";
    else if (open) says = plural(entries, "photo has", "photos have") + " entered. Voting closes " + when(m.end) + ".";
    else if (m.state === "verifying") says = "Voting closed " + when(m.end) + ". The maintainers are checking the votes before naming the winner; these are the standings as they are now.";
    else if (m.state === "confirmed") says = "Voting closed " + when(m.end) + ". The winner is confirmed.";
    else says = "Voting closed " + when(m.end) + " with no counted votes.";
    if (note) note.textContent = says + (open && !top.length && entries ? " No photo has a counted vote yet." : "");
    if (list) {
      // The built file names the winner by its id, the live answer with an object.
      var won = m.winner && typeof m.winner === "object" ? text(m.winner.id) : text(m.winner);
      var shown = m.state === "confirmed" ? top.filter(function (e) { return e.id === won; }) : top;
      list.replaceChildren.apply(list, shown.map(function (e) { return entryItem(e, m.month, open); }));
    }
  }

  function showWinners(doc) {
    var list = document.querySelector("[data-potm-winners]"), state = document.querySelector("[data-potm-winners-state]");
    if (!list) return;
    var winners = (doc && Array.isArray(doc.winners) ? doc.winners : []).filter(function (w) {
      return w && ID.test(text(w.id)) && MONTH.test(text(w.month));
    });
    list.replaceChildren.apply(list, winners.map(function (w) {
      var li = el("li", "", "potm-winner");
      li.id = w.month;
      li.append(el("p", monthName(w.month), "potm-when"), picture(w, false), byline(w),
                el("p", plural(num(w.votes) || 0, "vote", "votes"), "potm-counts"));
      return li;
    }));
    if (state) {
      state.hidden = winners.length > 0;
      state.textContent = "No winner yet: the first one is named after the first month ends.";
    }
  }

  function render(months) {
    showMonth(months[0], document.getElementById("potm-now"));
    showMonth(months[1], document.getElementById("potm-before"));
    if (window.FHLikes) window.FHLikes.fill(board);
    var target = location.hash && document.getElementById(location.hash.slice(1));
    if (target && !target.dataset.seen) {
      target.dataset.seen = "1";
      target.classList.add("potm-target");
      target.scrollIntoView({ behavior: "instant" });
    }
  }

  function previous(m) {
    var y = parseInt(m.slice(0, 4), 10), n = parseInt(m.slice(5), 10);
    if (n === 1) { y -= 1; n = 13; }
    return y + "-" + ("0" + (n - 1)).slice(-2);
  }

  // The live standings: this month, then the month before when the contest ran in it.
  function live() {
    var opts = { cache: "no-cache", credentials: "omit", headers: { "Accept": "application/json" } };
    return get("/api/potm/standings", opts).then(function (now) {
      if (!now || now.version !== 1 || !MONTH.test(text(now.month))) throw new Error("not standings");
      var before = previous(now.month), first = built && MONTH.test(text(built.from)) ? built.from : null;
      if (!first || before < first) return [now];
      return get("/api/potm/standings?month=" + before, opts).then(function (b) { return [now, b]; },
        function () { return [now, built.months && built.months[1]]; });
    });
  }

  function fillTexts(doc) {
    var m = doc && Array.isArray(doc.months) && doc.months[0];
    var n = m ? num(m.accountDays) : null;
    if (n !== null) days.forEach(function (d) { d.textContent = String(n); });
    if (doc && MONTH.test(text(doc.from))) froms.forEach(function (f) { f.textContent = monthName(doc.from); });
  }

  function startBoard() {
    var share = document.querySelector("[data-potm-share-page]");
    if (share) share.replaceChildren(shareBox(pageUrl(""), "Vote for your favourite shots in the Forza Horizon 1: ReImagined Photo of the Month",
                                              "Share this page"));
    var refresh = function () {
      if (document.visibilityState === "hidden") return;
      live().then(function (months) { render(months); }, function () { /* keep what is shown */ });
    };
    render(built && Array.isArray(built.months) ? built.months : []);
    showWinners(built);
    refresh();
    setInterval(refresh, LIVE_SECONDS * 1000);
  }

  var wanted = [get("data/photo-of-the-month.json").catch(function () { return null; })];
  if (board) wanted.push(get("data/gallery.json").catch(function () { return null; }));
  Promise.all(wanted).then(function (r) {
    built = r[0] && r[0].version === 1 ? r[0] : null;
    if (r[1] && Array.isArray(r[1].photos)) r[1].photos.forEach(function (p) { if (p && ID.test(text(p.id))) gallery[p.id] = p; });
    fillTexts(built);
    showFeatured(built);
    if (board) startBoard();
  });
})();
