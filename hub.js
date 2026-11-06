// The mods and texture packs hub (mods.html, and the item pages mods/<id>.html that
// tools/build_hub.py makes). The list comes from data/hub.json, which the Pages workflow copies
// every hour from the accounts service's public /api/hub/items; a visitor who did not sign in
// never calls the API. Downloads go to the hub's own download host (another host name), as the
// file's SHA-256 says.
// For a visitor who signed in on this browser (the fh-signed-in hint): the like buttons
// (likes.js, kinds "mod" and "pack"), a report button on each item page, and on mods.html the
// upload form and "Your uploads" (/api/hub/mine). An upload is two calls: POST /api/hub/items
// makes a draft, then the zip is sent as the body of PUT /api/hub/items/<id>/file; both carry the
// CSRF token. The accounts service checks the account is linked to its game.
// Titles, descriptions and gamertags are the uploaders' own text: they go in with textContent.
(function () {
  "use strict";

  var list = document.getElementById("hub-items");
  var itemPage = document.querySelector("[data-hub-item]");
  if (!list && !itemPage) return;
  var HINT = "fh-signed-in";
  var ID = /^[A-Z2-7]{12}$/, SHA = /^[0-9a-f]{64}$/;
  var KINDS = { mod: "Mod", pack: "Texture pack" };
  var STATES = {
    draft: "Not uploaded yet", uploading: "Uploading", queued: "Waiting for the checks",
    refused: "Refused", review: "Checked: waiting for a maintainer", live: "Listed", hidden: "Hidden"
  };
  var ERRORS = {
    "link your game first": "Link your account to your game first: in the launcher, Settings > Online > Website account.",
    "only your own work": "Only your own work can be shared.",
    "wait until your other uploads are checked": "Wait until your other uploads are checked.",
    "the queue is full: try again later": "Too many uploads are waiting. Try again later.",
    "too many requests": "Too many uploads for today. Try again tomorrow.",
    "send a .zip file": "Choose a .zip file.",
    "that is not a .zip file": "That file is not a zip.",
    "sign in again": "Sign in again on your account page, then try once more.",
    "you reported it already": "You reported it already.",
    "not available": "Not available."
  };
  var REASONS = [["broken", "It does not work"], ["malware", "Malware or a harmful file"],
                 ["not-own-work", "Not the uploader's own work"], ["game-files", "The game's original files"],
                 ["offensive", "Offensive"], ["other", "Something else"]];

  function text(v) { return typeof v === "string" ? v : ""; }
  function num(v) { return typeof v === "number" && isFinite(v) && v >= 0 ? Math.round(v) : 0; }
  function el(tag, value, cls) {
    var e = document.createElement(tag);
    if (value) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }
  function size(n) {
    var units = [["GB", 1073741824], ["MB", 1048576], ["KB", 1024]];
    for (var i = 0; i < units.length; i++) if (n >= units[i][1]) return (n / units[i][1]).toFixed(1) + " " + units[i][0];
    return n + " bytes";
  }
  function signedIn() { try { return localStorage.getItem(HINT) === "1"; } catch (e) { return false; } }
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)__Host-fh_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function json(r) {
    var type = r.headers.get("Content-Type") || "";
    if (type.indexOf("application/json") !== 0) return Promise.resolve({ status: r.status, doc: null });
    return r.json().then(function (doc) { return { status: r.status, doc: doc }; }, function () { return { status: r.status, doc: null }; });
  }
  function token() {
    if (csrf()) return Promise.resolve(csrf());
    return fetch("/api/auth/browser/v1/config", { credentials: "same-origin" }).then(csrf);
  }
  function call(method, path, body) {
    return token().then(function (t) {
      var opts = { method: method, credentials: "same-origin", headers: { "Accept": "application/json", "X-CSRFToken": t } };
      if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
      return fetch(path, opts).then(json);
    });
  }
  function say(where, msg, error) {
    if (!where) return;
    where.textContent = msg;
    where.classList.toggle("error", !!error);
  }
  function errorText(r) {
    var e = r.doc && typeof r.doc.error === "string" ? r.doc.error : "";
    if (r.status === 401) return "You are signed out. Sign in on your account page.";
    if (r.status === 413) return "The zip is too big.";
    return ERRORS[e] || (e ? "Not done: " + e + "." : "That did not work. Try again later.");
  }
  // An item from data/hub.json, checked: the download address must be the one the build made.
  function usable(i) {
    return i && ID.test(text(i.id)) && KINDS[i.kind] && text(i.title) && SHA.test(text(i.sha256)) &&
      /^https?:\/\/[^\/?#@]+\/[0-9a-f]{64}\/[A-Za-z0-9][A-Za-z0-9_-]{0,39}\.zip$/.test(text(i.download)) &&
      text(i.download).indexOf("/" + i.sha256 + "/") > 0;
  }

  // ---- The list ----

  function card(i) {
    var li = el("li", "", "hub-card");
    li.id = "item-" + i.id;
    var head = el("p", "", "hub-head");
    var a = el("a", i.title);
    a.href = "mods/" + i.id + ".html";
    head.append(el("span", KINDS[i.kind], "hub-kind"), document.createTextNode(" "), a);
    if (text(i.version)) head.append(document.createTextNode(" "), el("span", i.version, "hub-version"));
    li.append(head, el("p", "by " + (text(i.author) || "a player") + " · " + size(num(i.size)) + ", " + num(i.files) + " files", "hub-meta"));
    var first = text(i.description).split("\n")[0];
    if (first) li.append(el("p", first, "hub-desc"));
    var dl = el("a", "Download", "btn ghost hub-download");
    dl.href = i.download;
    dl.rel = "noopener noreferrer";
    var sha = el("p", "", "hub-sha");
    sha.append(document.createTextNode("SHA-256: "), el("code", i.sha256));
    var foot = el("div", "", "photo-foot");
    if (window.FHLikes) foot.append(window.FHLikes.slot(i.kind, i.id, i.likes));
    li.append(dl, sha, foot);
    return li;
  }

  function showList(doc) {
    var state = document.querySelector("[data-hub-state]"), filter = document.querySelector("[data-hub-filter]");
    if (!doc || doc.version !== 1 || !doc.on) {
      say(state, "The mods and texture packs hub is not open yet.");
      return false;
    }
    var items = (Array.isArray(doc.items) ? doc.items : []).filter(usable);
    var kind = "";
    function draw() {
      var shown = items.filter(function (i) { return !kind || i.kind === kind; });
      list.replaceChildren.apply(list, shown.map(card));
      say(state, shown.length ? "" : "Nothing shared yet. Share yours below.");
      state.hidden = shown.length > 0;
      if (window.FHLikes) window.FHLikes.fill(list);
    }
    if (filter) {
      filter.hidden = false;
      Array.prototype.forEach.call(filter.querySelectorAll("button"), function (b) {
        b.addEventListener("click", function () {
          kind = b.dataset.kind;
          Array.prototype.forEach.call(filter.querySelectorAll("button"), function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
          draw();
        });
      });
    }
    draw();
    return true;
  }

  // ---- Uploading, and the uploader's own items ----

  function mine() {
    var where = document.querySelector("[data-hub-mine]");
    if (!where) return;
    call("GET", "/api/hub/mine").then(function (r) {
      var note = document.querySelector("[data-hub-share-note]"), form = document.getElementById("hub-form");
      if (r.status !== 200 || !r.doc || !Array.isArray(r.doc.items)) {
        if (r.status === 401) try { localStorage.removeItem(HINT); } catch (e) { /* storage off */ }
        if (form) form.hidden = true;
        if (note) note.hidden = false;
        if (r.status === 403 && note) say(note, ERRORS["link your game first"]);
        return;
      }
      if (form) { form.hidden = false; form.dataset.max = String(num(r.doc.max_bytes)); }
      if (note) note.hidden = true;
      var box = document.querySelector("[data-hub-mine-box]");
      if (box) box.hidden = false;
      where.replaceChildren.apply(where, r.doc.items.filter(function (i) { return i && ID.test(text(i.id)); }).map(function (i) {
        var li = el("li");
        var body = el("div", "", "item-body");
        body.append(el("b", text(i.title) + (text(i.version) ? " " + i.version : "")),
                    el("span", (KINDS[i.kind] || "") + " · " + (STATES[i.state] || text(i.state)) + (text(i.reason) ? ": " + i.reason : "")));
        li.append(body);
        if (i.state === "live") {
          var link = el("a", "Its page");
          link.href = "mods/" + i.id + ".html";
          li.append(link);
        }
        var del = el("button", "Delete", "btn ghost");
        del.type = "button";
        del.addEventListener("click", function () {
          if (!window.confirm("Delete “" + text(i.title) + "”? Its download and its likes go.")) return;
          call("POST", "/api/hub/items/" + i.id + "/delete", {}).then(function (d) {
            if (d.status === 200) mine();
            else say(document.querySelector("[data-hub-message]"), errorText(d), true);
          });
        });
        if (i.state !== "uploading") li.append(del);
        return li;
      }));
    }, function () { /* no /api/ on this copy of the site */ });
  }

  function put(path, file, progress) {
    return token().then(function (t) {
      return new Promise(function (resolve) {
        var x = new XMLHttpRequest();
        x.open("PUT", path);
        x.withCredentials = true;
        x.setRequestHeader("Content-Type", "application/zip");
        x.setRequestHeader("Accept", "application/json");
        x.setRequestHeader("X-CSRFToken", t);
        x.upload.onprogress = function (e) { if (e.lengthComputable) progress(Math.round(100 * e.loaded / e.total)); };
        x.onload = function () {
          var doc = null;
          try { doc = JSON.parse(x.responseText); } catch (e) { doc = null; }
          resolve({ status: x.status, doc: doc });
        };
        x.onerror = function () { resolve({ status: 0, doc: null }); };
        x.send(file);
      });
    });
  }

  function startShare() {
    var share = document.getElementById("hub-share"), form = document.getElementById("hub-form");
    if (!share) return;
    share.hidden = false;
    if (!signedIn()) return;
    mine();
    if (!form) return;
    var msg = form.querySelector("[data-hub-message]"), bar = form.querySelector("[data-hub-progress]");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var file = form.elements.file.files[0], max = parseInt(form.dataset.max, 10) || 0;
      if (!file || !/\.zip$/i.test(file.name)) { say(msg, "Choose a .zip file.", true); return; }
      if (max && file.size > max) { say(msg, "The zip is too big: at most " + size(max) + ".", true); return; }
      if (!form.elements.own_work.checked) { say(msg, ERRORS["only your own work"], true); return; }
      var button = form.querySelector("button[type=submit]");
      button.disabled = true;
      say(msg, "Starting...");
      call("POST", "/api/hub/items", {
        kind: form.elements.kind.value, title: form.elements.title.value, version: form.elements.version.value,
        description: form.elements.description.value, own_work: true
      }).then(function (r) {
        if (r.status !== 200 || !r.doc || !ID.test(text(r.doc.id))) throw r;
        bar.hidden = false;
        return put("/api/hub/items/" + r.doc.id + "/file", file, function (p) { bar.value = p; say(msg, "Uploading: " + p + "%"); });
      }).then(function (r) {
        if (r.status !== 200) throw r;
        say(msg, "Uploaded. It is being checked; then a maintainer looks at it. Its state is below.");
        form.reset();
      }).catch(function (r) {
        say(msg, r && typeof r.status === "number" ? errorText(r) : "That did not work. Try again later.", true);
      }).then(function () {
        button.disabled = false;
        bar.hidden = true;
        mine();
      });
    });
  }

  // ---- An item's page: like and report ----

  function startItem() {
    var id = itemPage.dataset.hubItem, kind = itemPage.dataset.hubKind;
    var actions = itemPage.querySelector("[data-hub-actions]");
    if (!ID.test(id) || !KINDS[kind] || !actions) return;
    fetch("../data/hub.json", { cache: "no-cache" }).then(function (r) { return r.json(); }).then(function (doc) {
      var found = doc && Array.isArray(doc.items) ? doc.items.filter(function (i) { return i && i.id === id; })[0] : null;
      if (window.FHLikes) {
        actions.append(window.FHLikes.slot(kind, id, found ? found.likes : undefined));
        window.FHLikes.fill(actions);
      }
    }, function () { /* the page works without its totals */ });
    if (!signedIn()) return;
    var open = el("button", "Report", "potm-share-btn");
    open.type = "button";
    var form = el("form", "", "hub-report");
    form.hidden = true;
    var select = el("select");
    select.name = "reason";
    REASONS.forEach(function (r) { var o = el("option", r[1]); o.value = r[0]; select.append(o); });
    var note = el("input");
    note.name = "note";
    note.maxLength = 500;
    note.placeholder = "What is wrong (optional)";
    var send = el("button", "Send the report", "potm-share-btn");
    send.type = "submit";
    var status = el("span", "", "hub-report-status");
    status.setAttribute("role", "status");
    form.append(select, note, send, status);
    open.addEventListener("click", function () { form.hidden = !form.hidden; });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      call("POST", "/api/hub/reports", { item: id, reason: select.value, note: note.value }).then(function (r) {
        status.textContent = r.status === 200 ? "Thank you: a maintainer will look at it." : errorText(r);
        if (r.status === 200) send.disabled = true;
      });
    });
    actions.append(open, form);
  }

  if (list) {
    fetch("data/hub.json", { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (doc) {
      if (showList(doc)) startShare();
    }, function () { showList(null); });
  }
  if (itemPage) startItem();
})();
