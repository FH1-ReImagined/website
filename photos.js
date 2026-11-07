// The photo viewer (dialog#lightbox, for every picture button with data-full) and the community
// photos: the photos players shared from the game's photo mode and a maintainer approved. The
// Pages workflow copies them from the Forza Live server (tools/build_gallery.py) into photos/
// and data/gallery.json, newest first. Every list with data-photos gets them: the front page
// shows the newest (data-photos="3") and keeps its section hidden while there are none;
// gallery.html shows them all. Titles and gamertags are the players' own text: they go in with
// textContent only. Each photo has its likes (likes.js) and a link to its owner's profile page
// (profile.html?p=<owner>, the player id the server publishes). A list with data-photos-owner (the
// profile page) shows only the photos of the player its address names. On gallery.html
// (data-photos-live) the newest approved photos show before the next build has them (live.js).
//
// The viewer shows one picture of its list at a time and slides to the next or the one before:
// the arrow buttons, the arrow keys (Home and End for the ends), or a swipe or drag. It holds
// three slides, the picture and its two neighbours, so the neighbours are already loading when
// they slide in, and the ones after them are fetched ahead. A slide shows the picture's thumbnail
// at once and the full picture fades in over it once it has loaded: a slide is emptied before it
// gets a new picture, so a picture seen before never shows in its place. With reduced motion the
// pictures change without sliding.
(function () {
  "use strict";

  function el(tag, value, cls) {
    var e = document.createElement(tag);
    if (value) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }

  var box = document.getElementById("lightbox");
  if (box && box.showModal) viewer(box);

  function viewer(box) {
    var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    Array.prototype.forEach.call(box.querySelectorAll(":scope > img, :scope > p"), function (n) { n.remove(); });
    box.setAttribute("aria-label", "Photo viewer");
    var count = el("p", "", "viewer-count");
    count.setAttribute("aria-live", "polite");
    var stage = el("div", "", "viewer-stage");
    var track = el("div", "", "viewer-track");
    stage.append(track);
    var slots = [0, 1, 2].map(function () {
      var s = el("div", "", "viewer-slide"), wrap = el("div", "", "viewer-box"), frame = el("div", "", "viewer-frame");
      var lo = el("img", "", "viewer-lo"), hi = el("img", "", "viewer-hi");
      lo.alt = "";
      lo.setAttribute("aria-hidden", "true");
      hi.decoding = "async";
      hi.draggable = false;
      lo.draggable = false;
      frame.append(lo, hi);
      wrap.append(frame);
      s.append(wrap);
      track.append(s);
      var slot = { el: s, frame: frame, lo: lo, hi: hi, i: -1 };
      hi.addEventListener("load", function () { if (hi.getAttribute("src")) loaded(slot); });
      return slot;
    });
    var prev = el("button", "", "viewer-nav viewer-prev"), next = el("button", "", "viewer-nav viewer-next");
    prev.type = next.type = "button";
    prev.setAttribute("aria-label", "Previous photo");
    next.setAttribute("aria-label", "Next photo");
    var cap = el("p", "", "viewer-cap"), capTitle = el("b"), capBy = el("span");
    cap.append(capTitle, capBy);
    box.append(count, stage, prev, next, cap);

    var items = [], index = 0, moving = null;

    function loaded(slot) {
      slot.hi.classList.add("is-loaded");
      slot.frame.classList.add("is-loaded");
    }
    function empty(slot) {
      slot.i = -1;
      slot.hi.classList.remove("is-loaded");
      slot.frame.classList.remove("is-loaded");
      slot.hi.removeAttribute("src");
      slot.lo.removeAttribute("src");
      slot.hi.alt = "";
    }
    function fill(slot, i) {
      empty(slot);
      var it = items[i];
      slot.el.hidden = !it;
      if (!it) return;
      slot.i = i;
      slot.frame.style.setProperty("--ar", String(it.ratio));
      if (it.thumb) slot.lo.src = it.thumb;
      slot.hi.alt = it.alt;
      slot.hi.src = it.full;
      if (slot.hi.complete && slot.hi.naturalWidth) loaded(slot);
    }
    function ahead(i) {
      if (items[i]) { var im = new Image(); im.decoding = "async"; im.src = items[i].full; }
    }
    function place() {
      slots.forEach(function (s, k) {
        s.el.style.setProperty("--pos", String(k - 1));
        s.el.setAttribute("aria-hidden", k === 1 ? "false" : "true");
      });
    }
    function shift(x, animate) {
      track.classList.toggle("moving", !!animate);
      track.style.transform = "translate3d(" + x + ",0,0)";
    }
    function caption() {
      var it = items[index], n = items.length;
      count.replaceChildren();
      if (n > 1) count.append(el("b", String(index + 1)), document.createTextNode(" / " + n));
      capTitle.textContent = it ? it.title : "";
      capBy.textContent = it ? it.by : "";
      prev.hidden = next.hidden = n < 2;
      prev.disabled = index <= 0;
      next.disabled = index >= n - 1;
    }
    function show(i) {
      finish();
      index = i;
      fill(slots[0], i - 1);
      fill(slots[1], i);
      fill(slots[2], i + 1);
      place();
      shift("0px", false);
      caption();
      ahead(i + 2);
      ahead(i - 2);
    }
    // Move the slides round once the slide has come in: the one that went out takes the next
    // neighbour, the others keep their pictures, already loaded.
    function rotate(dir) {
      moving = null;
      index += dir;
      var s;
      if (dir > 0) { s = slots.shift(); slots.push(s); fill(s, index + 1); }
      else { s = slots.pop(); slots.unshift(s); fill(s, index - 1); }
      place();
      shift("0px", false);
      caption();
      ahead(index + 2 * dir);
    }
    function finish() {
      if (moving) moving.done();
    }
    function go(dir, fromPx) {
      var j = index + dir;
      finish();
      if (j < 0 || j >= items.length) { shift("0px", !reduce.matches); return; }
      if (reduce.matches) { rotate(dir); return; }
      if (fromPx) { shift(fromPx + "px", false); void track.offsetWidth; }
      // (transitionend also bubbles up from a picture fading in: only the track's own counts)
      var m = { done: function (e) {
        if (e && e.target !== track) return;
        clearTimeout(m.timer);
        track.removeEventListener("transitionend", m.done);
        if (moving === m) rotate(dir);
      } };
      moving = m;
      track.addEventListener("transitionend", m.done);
      m.timer = setTimeout(m.done, 600);
      shift((-dir * 100) + "%", true);
    }

    prev.addEventListener("click", function () { go(-1); });
    next.addEventListener("click", function () { go(1); });
    box.addEventListener("keydown", function (e) {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "Home" && index > 0) show(0);
      else if (e.key === "End" && index < items.length - 1) show(items.length - 1);
      else return;
      e.preventDefault();
    });

    // Swipe on touch, drag with a mouse or pen: the slides follow the pointer, and a long or quick
    // enough move goes on to the next picture. Vertical moves are left to the page.
    var drag = null;
    stage.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || items.length < 2) return;
      finish();
      drag = { id: e.pointerId, x: e.clientX, y: e.clientY, t: Date.now(), dx: 0, on: false };
    });
    stage.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.on) {
        if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy)) return;
        drag.on = true;
        stage.classList.add("dragging");
        try { stage.setPointerCapture(e.pointerId); } catch (err) { /* already gone */ }
      }
      var atEnd = (dx > 0 && index === 0) || (dx < 0 && index === items.length - 1);
      drag.dx = atEnd ? dx / 3 : dx;
      shift(drag.dx + "px", false);
    });
    function release(e) {
      if (!drag || e.pointerId !== drag.id) return;
      var d = drag;
      drag = null;
      stage.classList.remove("dragging");
      if (!d.on) return;
      d.moved = true;
      stage.dataset.dragged = "1";
      setTimeout(function () { delete stage.dataset.dragged; }, 0);
      var w = stage.clientWidth || 1, speed = d.dx / Math.max(1, Date.now() - d.t);
      if (d.dx < -w * 0.18 || (speed < -0.45 && d.dx < -30)) go(1, d.dx);
      else if (d.dx > w * 0.18 || (speed > 0.45 && d.dx > 30)) go(-1, d.dx);
      else shift("0px", !reduce.matches);
    }
    stage.addEventListener("pointerup", release);
    stage.addEventListener("pointercancel", release);

    // A click beside the picture closes the viewer, as a click on the backdrop does.
    box.addEventListener("click", function (e) {
      if (stage.dataset.dragged) return;
      if (e.target === box || e.target === stage || e.target.classList.contains("viewer-slide") ||
          e.target.classList.contains("viewer-box")) box.close();
    });
    box.addEventListener("close", function () {
      finish();
      document.documentElement.classList.remove("viewer-open");
      var it = items[index];
      slots.forEach(empty);
      // after the browser has put focus back on the button that opened the viewer
      if (it && it.button.isConnected) setTimeout(function () {
        it.button.focus({ preventScroll: true });
        it.button.scrollIntoView({ block: "nearest" });
      }, 0);
    });

    function itemOf(b) {
      var img = b.querySelector("img");
      var w = parseInt(b.dataset.w, 10), h = parseInt(b.dataset.h, 10);
      if (!(w > 0 && h > 0) && img) { w = img.naturalWidth || img.width; h = img.naturalHeight || img.height; }
      var parts = (b.dataset.cap || "").split(" — ");
      return {
        button: b, full: b.dataset.full, thumb: img ? (img.currentSrc || img.src) : "",
        ratio: w > 0 && h > 0 ? w / h : 16 / 9, alt: img ? img.alt : "",
        title: parts[0] || "", by: parts.slice(1).join(" — ")
      };
    }

    document.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-full]") : null;
      if (!b || box.contains(b)) return;
      var list = b.closest("ul, ol") || document.body;
      var all = Array.prototype.filter.call(list.querySelectorAll("button[data-full]"), function (x) { return !box.contains(x); });
      items = all.map(itemOf);
      show(Math.max(0, all.indexOf(b)));
      document.documentElement.classList.add("viewer-open");
      box.showModal();
    });
  }

  var lists = Array.prototype.slice.call(document.querySelectorAll("[data-photos]"));
  if (!lists.length) return;
  var states = Array.prototype.slice.call(document.querySelectorAll("[data-photos-state]"));

  function text(v) { return typeof v === "string" ? v : ""; }
  function size(v) { return typeof v === "number" && v > 0 && v < 100000 ? Math.round(v) : 0; }
  function owner(p) { return /^[0-9a-f]{12}$/.test(text(p.owner)) ? p.owner : ""; }
  var wanted = owner({ owner: new URLSearchParams(location.search).get("p") });
  function usable(p) {
    return p && /^[A-Za-z2-7]{12}$/.test(text(p.id)) && p.src === "photos/" + p.id + ".jpg" &&
      p.thumb === "photos/" + p.id + "-thumb.jpg" && text(p.gamertag) &&
      size(p.w) && size(p.h) && size(p.tw) && size(p.th);
  }

  // A photo's card: the picture (a button for the viewer), its title and place, then who took it
  // (a link to their page) and its likes.
  function item(p, onProfile) {
    var title = text(p.title), tag = text(p.gamertag);
    var where = [text(p.car), text(p.location)].filter(Boolean).join(", ");
    var li = el("li", "", "has-foot");
    li.id = "photo-" + p.id;
    var b = el("button");
    b.type = "button";
    b.dataset.full = p.src;
    b.dataset.w = size(p.w);
    b.dataset.h = size(p.h);
    b.dataset.cap = title ? title + " — " + tag : tag;
    var img = el("img");
    img.src = p.thumb;
    img.width = size(p.tw);
    img.height = size(p.th);
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = (title ? title + ": a" : "A") + " photo by " + tag + (where ? ", " + where : "");
    b.append(img);
    li.append(b);
    var body = el("div", "", "photo-body");
    if (title || where) body.append(el("b", title || where, "photo-title"));
    if (title && where) body.append(el("span", where, "photo-where"));
    var foot = el("div", "", "photo-foot");
    if (owner(p) && !onProfile) {
      var by = el("a", tag, "photo-by");
      by.href = "profile.html?p=" + owner(p);
      by.setAttribute("aria-label", "More from " + tag);
      foot.append(by);
    } else if (!onProfile) {
      foot.append(el("span", tag, "photo-by"));
    }
    if (window.FHLikes) foot.append(window.FHLikes.slot("photo", p.id, p.likes));
    body.append(foot);
    li.append(body);
    return li;
  }

  function show(photos, failed) {
    lists.forEach(function (list) {
      var n = parseInt(list.dataset.photos, 10), onProfile = list.hasAttribute("data-photos-owner");
      var mine = onProfile ? photos.filter(function (p) { return wanted && owner(p) === wanted; }) : photos;
      var shown = n > 0 ? mine.slice(0, n) : mine;
      list.replaceChildren.apply(list, shown.map(function (p) { return item(p, onProfile); }));
      var section = list.closest("[data-photos-section]");
      if (section) {
        section.hidden = !shown.length;
        if (shown.length && location.hash === "#" + section.id) section.scrollIntoView({ behavior: "instant" });
      }
    });
    states.forEach(function (s) {
      s.textContent = failed ? "The photos could not be loaded. Try again later."
        : photos.length ? "" : s.dataset.photosState;
      s.hidden = !failed && photos.length > 0;
      s.classList.toggle("error", !!failed);
    });
    if (window.FHLikes) window.FHLikes.fill(document);
    document.dispatchEvent(new Event("photos:shown"));
  }

  // A page that lists the photos as soon as they are approved (data-photos-live, gallery.html)
  // merges the server's live feed into the build's (live.js); without one it shows the build's.
  var fromFeed = !!document.querySelector("[data-photos-live]") && window.FHLive;
  fetch("data/gallery.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (doc) { return doc && Array.isArray(doc.photos) ? doc.photos.filter(usable) : []; },
          function () { return null; })
    .then(function (built) {
      if (!fromFeed) return built === null ? show([], true) : show(built, false);
      return window.FHLive.merge("photo", built || []).then(function (photos) {
        if (built === null && !photos.length) show([], true);
        else show(photos, false);
      });
    });
})();
