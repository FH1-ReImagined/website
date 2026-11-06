// The landing page's first screen: as it scrolls away the sun sinks behind the ridges (--hero-t,
// 0 at the top to 1 once the first screen is gone), and the "Scroll to know more" hint goes once
// the page has moved. With reduced motion the sun stays put; the hint still goes.
(function () {
  "use strict";
  var hero = document.querySelector(".hero");
  if (!hero) return;
  var hint = hero.querySelector(".scroll-hint");
  var doc = document.documentElement;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  var ticking = false;

  function update() {
    ticking = false;
    var y = window.scrollY || 0;
    if (hint) hint.classList.toggle("is-gone", y > 40);
    var t = reduce.matches ? 0 : Math.min(1, Math.max(0, y / Math.max(1, hero.offsetHeight)));
    doc.style.setProperty("--hero-t", t.toFixed(3));
  }
  function onScroll() {
    if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  update();
})();
