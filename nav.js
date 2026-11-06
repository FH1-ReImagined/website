// The navbar, its look on scroll, and the signed-in hint (at the end). At 940px and below the Menu
// button opens the full list; Escape closes it and focus returns to the button. Wider than that,
// Online and Community open on hover, focus or click. The html.js class (set in the page) is what
// hides the narrow list: without this script the links stay on the page.
(function () {
  "use strict";
  var btn = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (!btn || !nav) return;

  var mq = window.matchMedia("(max-width: 940px)");
  var menus = nav.querySelectorAll("details.menu");

  function narrow() { return mq.matches; }

  function setExpanded(open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    nav.classList.toggle("is-open", open);
    solid();
    if (open && narrow()) {
      for (var i = 0; i < menus.length; i++) menus[i].open = true;
    }
  }

  function closeMenus(except) {
    for (var i = 0; i < menus.length; i++) {
      if (menus[i] !== except) menus[i].open = false;
    }
  }

  btn.addEventListener("click", function () {
    var open = btn.getAttribute("aria-expanded") === "true";
    setExpanded(!open);
    if (!open) {
      var first = nav.querySelector("a, summary");
      if (first) first.focus({ preventScroll: true });
    }
  });

  for (var m = 0; m < menus.length; m++) {
    (function (menu) {
      var summary = menu.querySelector("summary");
      var fromHover = false;
      menu.addEventListener("mouseenter", function () {
        if (narrow()) return;
        closeMenus(menu);
        if (!menu.open) fromHover = true;
        menu.open = true;
      });
      menu.addEventListener("mouseleave", function () {
        fromHover = false;
        if (narrow()) return;
        if (menu.contains(document.activeElement)) return;
        menu.open = false;
      });
      summary.addEventListener("click", function (e) {
        if (narrow()) {
          e.preventDefault();
          menu.open = true;
          return;
        }
        if (fromHover) {
          fromHover = false;
          e.preventDefault();
          menu.open = true;
        }
      });
    })(menus[m]);
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (btn.getAttribute("aria-expanded") === "true") {
      setExpanded(false);
      btn.focus();
      return;
    }
    var open = nav.querySelector("details.menu[open]");
    if (open && !narrow()) {
      open.open = false;
      var summary = open.querySelector("summary");
      if (summary) summary.focus();
    }
  });

  document.addEventListener("click", function (e) {
    if (btn.contains(e.target) || nav.contains(e.target)) return;
    if (btn.getAttribute("aria-expanded") === "true") setExpanded(false);
    if (!narrow()) closeMenus(null);
  });

  nav.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a") : null;
    if (a && narrow()) setExpanded(false);
  });

  // relatedTarget is null for some clicks, so check where focus landed.
  document.addEventListener("focusout", function () {
    if (!narrow() || btn.getAttribute("aria-expanded") !== "true") return;
    setTimeout(function () {
      if (btn.getAttribute("aria-expanded") !== "true") return;
      var active = document.activeElement;
      if (active && (nav.contains(active) || btn.contains(active))) return;
      setExpanded(false);
    }, 0);
  });

  // The bar is see-through over the landing page's sky until the page scrolls or the menu opens.
  var bar = document.querySelector(".topbar");
  function solid() {
    if (bar) bar.classList.toggle("solid", window.scrollY > 24 || btn.getAttribute("aria-expanded") === "true");
  }
  window.addEventListener("scroll", solid, { passive: true });
  solid();

  function onWidth() {
    setExpanded(false);
    if (!narrow()) closeMenus(null);
  }
  if (mq.addEventListener) mq.addEventListener("change", onWidth);
  else if (mq.addListener) mq.addListener(onWidth);
})();

// The signed-in hint: for a visitor who signed in on this browser (account.js sets the
// fh-signed-in hint when /api/me answers, and likes.js and account.js drop it when the service
// says signed out), the account link says "Account" instead of "Sign in" and Download leaves the
// navbar: they have the game already. The page's head sets html.signed-in from the same hint
// before the first paint. Only the account page calls the API.
(function () {
  "use strict";
  function set(on) {
    document.documentElement.classList.toggle("signed-in", !!on);
    var link = document.querySelector(".nav-account");
    if (link) link.textContent = on ? "Account" : "Sign in";
    var download = document.querySelector("#site-nav .nav-cta");
    if (download) download.hidden = !!on;
  }
  var on = false;
  try { on = localStorage.getItem("fh-signed-in") === "1"; } catch (e) { /* storage off: signed out */ }
  set(on);
  window.FHNav = { signedIn: set };
})();
