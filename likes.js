// Likes on the community photos and designs (photos.js, designs.js, profile.js), and on the hub's
// mods and texture packs (hub.js, kinds "mod" and "pack", totals from data/hub.json). The Pages workflow
// copies the public totals into data/gallery.json and data/designs.json (tools/build_gallery.py,
// tools/build_designs.py: "likes"), so a visitor who never signed in sees them without any call
// to the accounts service. Only for a visitor who signed in on this browser (the fh-signed-in
// hint account.js sets) does a page ask /api/likes/mine and /api/likes/counts, once per kind,
// and draw a working thumbs-up:
// - linked to their game: pressed for what the game key liked, none on their own items; a click
//   sends POST /api/likes with the CSRF token. The total moves only when the key's likes count
//   in public (a key counts once its game has uploaded a speed trap to the public server);
// - not linked yet, the link on hold, or the game not allowed to like: the thumbs-up shows the
//   reason the service gave for /api/likes/mine when it is pressed (link the game in the
//   launcher, the link is on hold, upload a speed trap first, ...);
// - signed out since, or the service not answering: the thumbs-up leads to the account page, as
//   for everybody else.
// The totals and ids are numbers and ids from the site's own files or the API: nothing here is
// player text.
(function () {
  "use strict";

  var HINT = "fh-signed-in";
  var KINDS = { photo: "photo", design: "design", mod: "mod", pack: "pack" };
  var NOUN = { photo: "photo", design: "design", mod: "mod", pack: "texture pack" };
  var ERRORS = {
    "link your game first": "Link your game to like: in the launcher, with the main public server picked, Online > Online > Website account (before launcher 0.2.9: Settings > Online). Your game needs one speed trap uploaded to the public server first.",
    "not available": "Likes are not available for your game.",
    "this link needs the operator": "Your game link is on hold: your game's key changed. See your account page, or ask on Discord.",
    "play online first": "Drive through a speed trap in the game with the public server selected (single player is fine), then try again.",
    "your own": "That one is yours.",
    "not found": "That one is no longer shared.",
    "too many requests": "Too many likes for now. Try again later."
  };
  var states = {};
  var note = null, noteTimer = null;

  // A refusal's text: the service's own sentence (message) as it is, else ours for its error, else
  // its error as it is. Shown with textContent only.
  function serverText(doc) {
    if (doc && typeof doc.message === "string" && doc.message.trim()) return doc.message.trim().slice(0, 300);
    var code = doc && typeof doc.error === "string" ? doc.error.trim() : "";
    return ERRORS[code] || (code && code !== "refused" ? code.charAt(0).toUpperCase() + code.slice(1, 300) : "");
  }
  function signedIn() {
    try { return localStorage.getItem(HINT) === "1"; } catch (e) { return false; }
  }
  function forget() {
    try { localStorage.removeItem(HINT); } catch (e) { /* storage off */ }
    if (window.FHNav) window.FHNav.signedIn(false);  // the navbar: Sign in, and Download again
  }
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)__Host-fh_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function json(r) {
    var type = r.headers.get("Content-Type") || "";
    if (type.indexOf("application/json") !== 0) return Promise.resolve({ status: r.status, doc: null });
    return r.json().then(function (doc) { return { status: r.status, doc: doc }; },
                         function () { return { status: r.status, doc: null }; });
  }
  function get(path) {
    return fetch(path, { credentials: "same-origin", headers: { "Accept": "application/json" } }).then(json);
  }
  function token() {
    if (csrf()) return Promise.resolve(csrf());
    return get("/api/auth/browser/v1/config").then(csrf);  // sets the CSRF cookie
  }
  function post(path, body) {
    return token().then(function (t) {
      return fetch(path, {
        method: "POST", credentials: "same-origin", body: JSON.stringify(body),
        headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRFToken": t }
      }).then(json);
    });
  }
  function ids(list) {
    var out = {};
    (Array.isArray(list) ? list : []).forEach(function (i) { if (typeof i === "string") out[i] = true; });
    return out;
  }
  function count(v) { return typeof v === "number" && isFinite(v) && v >= 0 && v < 1e9 ? Math.round(v) : null; }

  // What this visitor may do with likes of a kind: {mode: "anon"|"linked"|"blocked", liked, own,
  // counted, counts, why}. "blocked": signed in, but the service said why this account cannot like.
  function state(kind) {
    if (states[kind]) return states[kind];
    if (!signedIn()) return (states[kind] = Promise.resolve({ mode: "anon" }));
    states[kind] = Promise.all([get("/api/likes/mine?kind=" + kind), get("/api/likes/counts?kind=" + kind)])
      .then(function (r) {
        var mine = r[0], totals = r[1];
        var counts = totals.status === 200 && totals.doc && totals.doc.counts && typeof totals.doc.counts === "object"
          ? totals.doc.counts : null;
        if (mine.status === 401) forget();
        if ((mine.status === 403 || mine.status === 409) && mine.doc) {
          var why = serverText(mine.doc);
          if (why) return { mode: "blocked", why: why, counts: counts };
        }
        if (mine.status !== 200 || !mine.doc) return { mode: "anon", counts: counts };
        return { mode: "linked", liked: ids(mine.doc.liked), own: ids(mine.doc.own), counted: !!mine.doc.counted,
                 counts: counts };
      }, function () { return { mode: "anon" }; });
    return states[kind];
  }

  function say(text, seconds) {
    if (!note) {
      note = document.createElement("p");
      note.className = "like-note";
      note.setAttribute("role", "status");
      note.hidden = true;
      document.body.append(note);
    }
    note.textContent = text;
    note.hidden = !text;
    clearTimeout(noteTimer);
    if (text) noteTimer = setTimeout(function () { note.hidden = true; }, (seconds || 5) * 1000);
  }

  function thumb() {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var path = document.createElementNS(NS, "path");
    path.setAttribute("d", "M2 10h4v12H2zM8 21V10l5.5-8 1.2.9c.6.5.9 1.3.7 2.1L14.6 9H21a2 2 0 0 1 2 2v2l-3 8z");
    svg.append(path);
    return svg;
  }

  function label(n, kind) {
    return n === null ? "Likes" : n.toLocaleString("en") + (n === 1 ? " like" : " likes");
  }

  // The thumbs-up's place in a card: filled in by fill(), once the visitor's state is known.
  function slot(kind, id, likes) {
    var s = document.createElement("span");
    s.className = "like-slot";
    s.dataset.kind = kind;
    s.dataset.id = id;
    var n = count(likes);
    if (n !== null) s.dataset.likes = String(n);
    return s;
  }

  function draw(s, st) {
    var kind = s.dataset.kind, id = s.dataset.id;
    var n = st.counts ? count(st.counts[id] || 0) : (s.dataset.likes !== undefined ? count(Number(s.dataset.likes)) : null);
    s.textContent = "";
    if (st.mode === "linked" && st.own[id]) {
      if (n === null) return;
      var own = document.createElement("span");
      own.className = "like own";
      own.title = "Your " + NOUN[kind];
      own.append(thumb(), document.createTextNode(n.toLocaleString("en")));
      own.setAttribute("aria-label", label(n, kind) + " on your " + NOUN[kind]);
      s.append(own);
      return;
    }
    if (st.mode === "linked") {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "like";
      var on = !!st.liked[id];
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.setAttribute("aria-label", (on ? "Liked. " : "Like this " + NOUN[kind] + ". ") + label(n, kind));
      b.append(thumb(), document.createTextNode(n === null ? "" : n.toLocaleString("en")));
      b.addEventListener("click", function () { toggle(b, s, st); });
      s.append(b);
      return;
    }
    if (st.mode === "blocked") {
      var why = document.createElement("button");
      why.type = "button";
      why.className = "like";
      why.title = st.why;
      why.setAttribute("aria-label", label(n, kind) + ". " + st.why);
      why.append(thumb(), document.createTextNode(n === null ? "" : n.toLocaleString("en")));
      why.addEventListener("click", function () { say(st.why, 12); });
      s.append(why);
      return;
    }
    if (n === null) return;  // no totals in this copy of the site
    var a = document.createElement("a");
    a.className = "like";
    a.href = "account.html";
    a.setAttribute("aria-label", label(n, kind) + ". Sign in and link your game to like");
    a.title = "Sign in and link your game to like";
    a.append(thumb(), document.createTextNode(n.toLocaleString("en")));
    s.append(a);
  }

  function toggle(b, s, st) {
    var kind = s.dataset.kind, id = s.dataset.id, on = !st.liked[id];
    b.disabled = true;
    post("/api/likes", { kind: kind, item: id, on: on }).then(function (r) {
      b.disabled = false;
      if (r.status === 200 && r.doc) {
        if (on) st.liked[id] = true; else delete st.liked[id];
        if (r.doc.counted && st.counts) st.counts[id] = Math.max(0, (count(st.counts[id]) || 0) + (on ? 1 : -1));
        else if (r.doc.counted && s.dataset.likes !== undefined) {
          s.dataset.likes = String(Math.max(0, Number(s.dataset.likes) + (on ? 1 : -1)));
        }
        draw(s, st);
        var again = s.querySelector("button");
        if (again) again.focus();
        if (on && !r.doc.counted) say("Liked. Your likes count in public once your game has uploaded a speed trap to the public server.");
        return;
      }
      if (r.status === 401) {
        forget();
        states = {};
        say("You are signed out. Sign in on the account page to like.");
        return;
      }
      say(serverText(r.doc) || (r.status === 429 ? ERRORS["too many requests"] : "That did not work. Try again."));
    }, function () {
      b.disabled = false;
      say("That did not work. Try again.");
    });
  }

  // Every slot under root, drawn for this visitor (one API call per kind and page view at most).
  function fill(root) {
    var slots = Array.prototype.slice.call((root || document).querySelectorAll(".like-slot"));
    var kinds = {};
    slots.forEach(function (s) { if (KINDS[s.dataset.kind]) kinds[s.dataset.kind] = true; });
    Object.keys(kinds).forEach(function (kind) {
      var mine = slots.filter(function (s) { return s.dataset.kind === kind; });
      mine.forEach(function (s) { draw(s, { mode: "anon" }); });  // the built totals at once
      state(kind).then(function (st) {
        if (st.mode !== "anon" || st.counts) mine.forEach(function (s) { if (s.isConnected) draw(s, st); });
      });
    });
  }

  window.FHLikes = { slot: slot, fill: fill };
})();
