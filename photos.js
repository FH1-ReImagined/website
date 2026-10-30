// The screenshot lightbox (dialog#lightbox, for every .cards and .shots button) and the community
// photos: the photos players shared from the game's photo mode and a maintainer approved. The
// Pages workflow copies them from the Forza Live server (tools/build_gallery.py) into photos/
// and data/gallery.json, newest first. Every list with data-photos gets them: the front page
// shows the newest (data-photos="6") and keeps its section hidden while there are none;
// gallery.html shows them all. Titles and gamertags are the players' own text: they go in with
// textContent only.
(function () {
  "use strict";

  var box = document.getElementById("lightbox");
  if (box && box.showModal) {
    var boxImg = box.querySelector("img"), boxCap = box.querySelector("p");
    document.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest(".shots button, .cards button") : null;
      if (!b || !b.dataset.full) return;
      var w = parseInt(b.dataset.w, 10), h = parseInt(b.dataset.h, 10);
      if (w > 0 && h > 0) { boxImg.width = w; boxImg.height = h; }
      else { boxImg.removeAttribute("width"); boxImg.removeAttribute("height"); }
      boxImg.src = b.dataset.full;
      boxImg.alt = b.querySelector("img") ? b.querySelector("img").alt : "";
      boxCap.textContent = b.dataset.cap || "";
      box.showModal();
    });
    box.addEventListener("click", function (e) { if (e.target === box) box.close(); });
  }

  var lists = Array.prototype.slice.call(document.querySelectorAll("[data-photos]"));
  if (!lists.length) return;
  var states = Array.prototype.slice.call(document.querySelectorAll("[data-photos-state]"));

  function text(v) { return typeof v === "string" ? v : ""; }
  function size(v) { return typeof v === "number" && v > 0 && v < 100000 ? Math.round(v) : 0; }
  function usable(p) {
    return p && /^[A-Za-z2-7]{12}$/.test(text(p.id)) && p.src === "photos/" + p.id + ".jpg" &&
      p.thumb === "photos/" + p.id + "-thumb.jpg" && text(p.gamertag) &&
      size(p.w) && size(p.h) && size(p.tw) && size(p.th);
  }
  function el(tag, value, cls) {
    var e = document.createElement(tag);
    if (value) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }

  function item(p) {
    var title = text(p.title), tag = text(p.gamertag);
    var where = [text(p.car), text(p.location)].filter(Boolean).join(", ");
    var li = el("li");
    li.id = "photo-" + p.id;
    var b = el("button");
    b.type = "button";
    b.dataset.full = p.src;
    b.dataset.w = size(p.w);
    b.dataset.h = size(p.h);
    b.dataset.cap = title ? title + " \u2014 " + tag : tag;
    var img = el("img");
    img.src = p.thumb;
    img.width = size(p.tw);
    img.height = size(p.th);
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = (title ? title + ": a" : "A") + " photo by " + tag + (where ? ", " + where : "");
    var cap = el("span");
    cap.append(el("b", title || tag), el("small", title ? tag : where));
    b.append(img, cap);
    li.append(b);
    return li;
  }

  function show(photos, failed) {
    lists.forEach(function (list) {
      var n = parseInt(list.dataset.photos, 10);
      var shown = n > 0 ? photos.slice(0, n) : photos;
      list.replaceChildren.apply(list, shown.map(item));
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
    document.dispatchEvent(new Event("photos:shown"));
  }

  fetch("data/gallery.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (doc) {
      show(doc && Array.isArray(doc.photos) ? doc.photos.filter(usable) : [], false);
    })
    .catch(function () { show([], true); });
})();
