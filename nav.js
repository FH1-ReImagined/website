// The navbar. Below 700px the Menu button opens the full list; Escape closes it
// and focus returns to the button. Wider than that, Online and Community open on
// hover, focus or click. The html.js class (set in the page) is what hides the
// narrow list: without this script the links stay on the page.
(function () {
  "use strict";
  var btn = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (!btn || !nav) return;

  var mq = window.matchMedia("(max-width: 700px)");
  var menus = nav.querySelectorAll("details.menu");

  function narrow() { return mq.matches; }

  function setExpanded(open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    nav.classList.toggle("is-open", open);
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

  function onWidth() {
    setExpanded(false);
    if (!narrow()) closeMenus(null);
  }
  if (mq.addEventListener) mq.addEventListener("change", onWidth);
  else if (mq.addListener) mq.addListener(onWidth);
})();
