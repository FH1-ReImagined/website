// The community videos (videos.html): the clips players uploaded from the game, once the server
// converted them to MP4 and they passed the screening. The site's builds copy them from the Forza
// Live server (tools/build_videos.py) into videos/ and data/videos.json, newest first; the newest
// show before the next build has them (live.js, data-videos-live). Each card is the video's
// poster (a button that opens the player), its title (a link to the card itself,
// videos.html#video-<id>, the address to give when asking for a video to be removed), when it was
// shared and how long it is, who shared it (plain text: the player pages show no videos yet) and
// its likes (likes.js). Titles and gamertags are the players' own text: they go in with
// textContent only.
//
// The player (dialog#player) loads a video only when it opens, and drops it when it closes, so a
// visitor downloads only what they watch. On a phone it fills the screen; the video's own controls
// do the rest (play, seek, full screen).
(function () {
  "use strict";

  var lists = Array.prototype.slice.call(document.querySelectorAll("[data-videos]"));
  if (!lists.length) return;
  var states = Array.prototype.slice.call(document.querySelectorAll("[data-videos-state]"));
  var box = document.getElementById("player");

  function el(tag, value, cls) {
    var e = document.createElement(tag);
    if (value) e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }
  function text(v) { return typeof v === "string" ? v : ""; }
  function size(v) { return typeof v === "number" && v > 0 && v < 100000 ? Math.round(v) : 0; }
  function usable(v) {
    return v && /^[A-Z2-7]{12}$/.test(text(v.id)) && v.src === "videos/" + v.id + ".mp4" &&
      v.poster === "videos/" + v.id + ".jpg" && text(v.gamertag) && size(v.w) && size(v.h) &&
      typeof v.seconds === "number" && v.seconds > 0 && typeof v.date === "number";
  }

  // 0:15, 1:04
  function length(seconds) {
    var s = Math.max(1, Math.round(seconds));
    return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
  }
  function spoken(seconds) {
    var s = Math.max(1, Math.round(seconds)), m = Math.floor(s / 60);
    var secs = (s % 60) + (s % 60 === 1 ? " second" : " seconds");
    return m ? m + (m === 1 ? " minute" : " minutes") + (s % 60 ? " " + secs : "") : secs;
  }
  function day(unix) {
    try {
      return new Date(unix * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch (e) {
      return new Date(unix * 1000).toISOString().slice(0, 10);
    }
  }

  // ---- the player ----

  var video = box ? box.querySelector("video") : null;
  var capTitle = box ? box.querySelector(".viewer-cap b") : null;
  var capBy = box ? box.querySelector(".viewer-cap span") : null;
  var opener = null;

  function play(v, button) {
    if (!box || !box.showModal || !video) {  // no dialog support: the file itself
      location.href = v.src;
      return;
    }
    opener = button;
    video.poster = v.poster;
    video.src = v.src;
    video.style.setProperty("--ar", String(size(v.w) / size(v.h) || 16 / 9));
    video.setAttribute("aria-label", (text(v.title) || "A video") + " by " + text(v.gamertag));
    capTitle.textContent = text(v.title) || "Untitled clip";
    capBy.textContent = text(v.gamertag) + " · " + day(v.date) + " · " + length(v.seconds);
    document.documentElement.classList.add("viewer-open");
    box.showModal();
    var started = video.play();
    if (started && started.catch) started.catch(function () { /* the visitor presses play */ });
  }

  if (box && video) {
    box.addEventListener("close", function () {
      video.pause();
      video.removeAttribute("src");
      video.removeAttribute("poster");
      video.load();  // stops the download
      document.documentElement.classList.remove("viewer-open");
      if (opener && opener.isConnected) {
        var b = opener;
        setTimeout(function () { b.focus({ preventScroll: true }); }, 0);
      }
    });
    // a click beside the video closes the player, as a click on the backdrop does
    box.addEventListener("click", function (e) {
      if (e.target === box || e.target.classList.contains("player-stage")) box.close();
    });
    video.addEventListener("error", function () {
      if (video.getAttribute("src")) capBy.textContent = "This video could not be played. Try again later.";
    });
  }

  // ---- the cards ----

  function item(v) {
    var title = text(v.title), tag = text(v.gamertag);
    var li = el("li", "", "has-foot");
    li.id = "video-" + v.id;
    var b = el("button", "", "video-open");
    b.type = "button";
    b.setAttribute("aria-label", "Play " + (title || "the clip") + " by " + tag + ", " + spoken(v.seconds));
    var img = el("img");
    img.src = v.poster;
    img.width = size(v.w);
    img.height = size(v.h);
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    b.append(img, el("span", length(v.seconds), "video-len"));
    b.addEventListener("click", function () { play(v, b); });
    li.append(b);
    var body = el("div", "", "photo-body");
    var name = el("a", title || "Untitled clip", "photo-title video-link");
    name.href = "#video-" + v.id;
    name.setAttribute("aria-label", "Link to this video: " + (title || "Untitled clip"));
    body.append(name);
    var when = el("span", "", "photo-where");
    var t = el("time", day(v.date));
    t.dateTime = new Date(v.date * 1000).toISOString();
    when.append(document.createTextNode("Shared "), t);
    body.append(when);
    var foot = el("div", "", "photo-foot");
    foot.append(el("span", tag, "photo-by"));  // not a link: the player's page shows no videos yet
    if (window.FHLikes) foot.append(window.FHLikes.slot("video", v.id, v.likes));
    body.append(foot);
    li.append(body);
    return li;
  }

  function show(videos, failed) {
    lists.forEach(function (list) {
      var n = parseInt(list.dataset.videos, 10);
      var shown = n > 0 ? videos.slice(0, n) : videos;
      list.replaceChildren.apply(list, shown.map(item));
      list.setAttribute("aria-busy", "false");
    });
    states.forEach(function (s) {
      s.textContent = failed ? "The videos could not be loaded. Try again later."
        : videos.length ? "" : s.dataset.videosState;
      s.hidden = !failed && videos.length > 0;
      s.classList.toggle("error", !!failed);
    });
    if (window.FHLikes) window.FHLikes.fill(document);
    // a link to one video (videos.html#video-<id>) shows its card
    var target = /^#video-[A-Z2-7]{12}$/.test(location.hash) ? document.getElementById(location.hash.slice(1)) : null;
    if (target) target.scrollIntoView({ block: "center" });
    document.dispatchEvent(new Event("videos:shown"));
  }

  lists.forEach(function (list) { list.setAttribute("aria-busy", "true"); });
  var fromFeed = !!document.querySelector("[data-videos-live]") && window.FHLive;
  fetch("data/videos.json", { cache: "no-cache" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (doc) { return doc && Array.isArray(doc.videos) ? doc.videos.filter(usable) : []; },
          function () { return null; })
    .then(function (built) {
      if (!fromFeed) return built === null ? show([], true) : show(built, false);
      return window.FHLive.merge("video", built || []).then(function (videos) {
        if (built === null && !videos.length) show([], true);
        else show(videos, false);
      });
    });
})();
