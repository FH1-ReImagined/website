// The paint jobs and vinyl groups players shared from the game's storefront. The Pages workflow
// copies them from the Forza Live server (tools/build_designs.py) into designs/ and
// data/designs.json, newest first. Every list with data-designs gets them: designs.html has one
// per kind (data-designs-type), behind two tabs, with a sort and a filter by car or creator;
// online.html shows the newest (data-designs="3") and keeps its section hidden while there are
// none. Each card has the share code that finds the design in the storefront, and a copy button.
// Titles, gamertags and car names are the players' own text: they go in with textContent only.
(function () {
  "use strict";

  var lists = Array.prototype.slice.call(document.querySelectorAll("[data-designs]"));
  if (!lists.length) return;
  var KIND = { livery: "Paint job", vinylgroup: "Vinyl group" };
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"][aria-controls]'));
  var sortBy = document.getElementById("design-sort");
  var filter = document.getElementById("design-filter");
  var designs = [], failed = false;

  function text(v) { return typeof v === "string" ? v : ""; }
  function num(v, max) { return typeof v === "number" && isFinite(v) && v >= 0 && v <= max; }
  function size(v) { return num(v, 100000) && v > 0 ? Math.round(v) : 0; }
  function usable(d) {
    return d && /^[A-Za-z0-9]{6,32}$/.test(text(d.id)) && Object.prototype.hasOwnProperty.call(KIND, d.type) &&
      /^[A-Za-z0-9]{4,15}$/.test(text(d.code)) && d.src === "designs/" + d.id + ".png" && text(d.gamertag) &&
      size(d.w) && size(d.h) && num(d.downloads, 1e9) && num(d.rating, 5) && num(d.ratings, 1e9) &&
      num(d.createdAt, 1e12);
  }
  function el(tag, value, cls) {
    var e = document.createElement(tag);
    if (value) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }
  function count(n, one, many) { return n.toLocaleString("en") + " " + (n === 1 ? one : many); }

  // Top rated: the average pulled toward 3 stars by 3 made-up votes, so a single 5-star vote does
  // not beat forty votes averaging 4.9. Designs nobody rated come last.
  function score(d) { return (d.rating * d.ratings + 9) / (d.ratings + 3); }
  var SORTS = {
    newest: function (a, b) { return b.createdAt - a.createdAt; },
    downloads: function (a, b) { return b.downloads - a.downloads || b.createdAt - a.createdAt; },
    rating: function (a, b) {
      return (b.ratings > 0) - (a.ratings > 0) || score(b) - score(a) || b.ratings - a.ratings || b.createdAt - a.createdAt;
    }
  };

  function card(d, withKind) {
    var title = text(d.title), tag = text(d.gamertag), car = d.type === "livery" ? text(d.car) : "";
    var li = el("li", "", "design");
    li.id = "design-" + d.id;
    var img = el("img");
    img.src = d.src;
    img.width = size(d.w);
    img.height = size(d.h);
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = KIND[d.type] + (title ? " \u201c" + title + "\u201d" : "") + " by " + tag + (car ? ", for the " + car : "");
    var body = el("div", "", "design-body");
    if (withKind) body.append(el("span", KIND[d.type], "design-kind"));
    body.append(el("b", title || "Untitled", "design-title"));
    if (car) body.append(el("span", car, "design-car"));
    var by = el("span", "by ", "design-by");
    by.append(el("b", tag));
    body.append(by);

    var stats = el("p", "", "design-stats");
    if (d.ratings > 0) {
      var rating = el("span", "", "rating");
      rating.setAttribute("role", "img");
      rating.setAttribute("aria-label", "Rated " + d.rating.toFixed(1) + " out of 5 by " + count(d.ratings, "player", "players"));
      var stars = el("span", "\u2605\u2605\u2605\u2605\u2605", "rating-stars");  // five dim stars, the lit share on top
      stars.append(el("span", "\u2605\u2605\u2605\u2605\u2605", "on"));
      stars.style.setProperty("--r", String(d.rating));
      rating.append(stars, el("span", d.rating.toFixed(1)), el("small", "(" + d.ratings.toLocaleString("en") + ")"));
      stats.append(rating);
    } else {
      stats.append(el("span", "No ratings yet", "rating none"));
    }
    stats.append(el("span", count(d.downloads, "download", "downloads"), "downloads"));
    body.append(stats);

    var share = el("div", "", "design-code");
    var copy = el("button", "Copy", "copy");
    copy.type = "button";
    copy.dataset.code = d.code;
    copy.setAttribute("aria-label", "Copy the share code " + d.code);
    share.append(el("span", "Share code", "k"), el("code", d.code), copy);
    body.append(share);
    li.append(img, body);
    return li;
  }

  function fold(s) { return s.normalize ? s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase() : s.toLowerCase(); }
  function matches(d, q) { return !q || fold(text(d.car)).indexOf(q) >= 0 || fold(d.gamertag).indexOf(q) >= 0; }

  function render() {
    var order = SORTS[sortBy && sortBy.value] || SORTS.newest;
    var q = filter ? fold(filter.value.trim()) : "";
    lists.forEach(function (list) {
      var type = list.dataset.designsType, n = parseInt(list.dataset.designs, 10);
      var mine = designs.filter(function (d) { return !type || d.type === type; });
      var shown = type ? mine.filter(function (d) { return matches(d, q); }).sort(order)
        : mine.sort(SORTS.newest).slice(0, n > 0 ? n : mine.length);
      list.replaceChildren.apply(list, shown.map(function (d) { return card(d, !type); }));
      var section = list.closest("[data-designs-section]");
      if (section) {
        section.hidden = !shown.length;
        if (shown.length && location.hash === "#" + section.id) section.scrollIntoView({ behavior: "instant" });
      }
      var state = list.parentNode.querySelector("[data-designs-state]");
      if (state) {
        state.textContent = failed ? "The designs could not be loaded. Try again later."
          : !mine.length ? state.dataset.designsState : shown.length ? "" : state.dataset.designsNone;
        state.hidden = !failed && shown.length > 0;
        state.classList.toggle("error", failed);
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-designs-count]"), function (c) {
      var n = designs.filter(function (d) { return d.type === c.dataset.designsCount; }).length;
      c.textContent = n ? n.toLocaleString("en") : "";
    });
    document.dispatchEvent(new Event("designs:shown"));
  }

  // The tabs: one panel shown at a time; arrow keys, Home and End move between them, and the
  // address follows (#paint-jobs, #vinyl-groups). A link to #design-<id> opens that design's tab.
  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab, panel = document.getElementById(t.getAttribute("aria-controls"));
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
  }
  function tabFor(panelId) {
    return tabs.filter(function (t) { return t.getAttribute("aria-controls") === panelId; })[0] || null;
  }
  function fromAddress() {
    if (!tabs.length) return;
    var id = location.hash.slice(1), tab = tabFor(id), target = null;
    if (!tab && /^design-[A-Za-z0-9]{6,32}$/.test(id)) {
      target = document.getElementById(id);
      var panel = target && target.closest('[role="tabpanel"]');
      tab = panel ? tabFor(panel.id) : null;
    }
    select(tab || tabs[0]);
    if (target) target.scrollIntoView({ behavior: "instant", block: "center" });
  }
  tabs.forEach(function (t, i) {
    function go(tab, focus) {
      select(tab, focus);
      if (history.replaceState) history.replaceState(null, "", "#" + tab.getAttribute("aria-controls"));
    }
    t.addEventListener("click", function () { go(t, false); });
    t.addEventListener("keydown", function (e) {
      var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1
        : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
      if (j === null) return;
      e.preventDefault();
      go(tabs[(j + tabs.length) % tabs.length], true);
    });
  });
  window.addEventListener("hashchange", fromAddress);
  fromAddress();

  if (sortBy) sortBy.addEventListener("change", render);
  if (filter) filter.addEventListener("input", render);

  // Copy a share code. The Clipboard API needs https (or localhost); else the older
  // execCommand, and if that fails too the code is selected for the player to copy.
  var live = el("p", "", "sr-only");
  live.setAttribute("aria-live", "polite");
  document.body.append(live);
  function oldCopy(value) {
    var ta = document.createElement("textarea"), ok = false;
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.opacity = "0";
    document.body.append(ta);
    ta.select();
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
  function copyText(value) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value).catch(function () {
        if (!oldCopy(value)) throw new Error("copy failed");
      });
    }
    return oldCopy(value) ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }
  function flash(button, label, message) {
    button.textContent = label;
    button.classList.add("done");
    live.textContent = message;
    clearTimeout(button.designsTimer);
    button.designsTimer = setTimeout(function () {
      button.textContent = "Copy";
      button.classList.remove("done");
    }, 2000);
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("button.copy[data-code]") : null;
    if (!b) return;
    var code = b.dataset.code, shown = b.parentNode.querySelector("code");
    copyText(code).then(function () {
      flash(b, "Copied", "Share code " + code + " copied.");
    }, function () {
      if (shown && window.getSelection) window.getSelection().selectAllChildren(shown);
      flash(b, "Selected", "Share code " + code + " selected: copy it with your keyboard.");
    });
  });

  fetch("data/designs.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (doc) {
      designs = doc && Array.isArray(doc.designs) ? doc.designs.filter(usable) : [];
    })
    .catch(function () { failed = true; })
    .then(function () {
      render();
      if (/^#design-/.test(location.hash)) fromAddress();
    });
})();
