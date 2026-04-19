/**
 * Scroll to Top — client-side behavior.
 *
 * Shows/hides a floating button based on scroll position and scrolls to
 * top on click.  Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  function parseThreshold(raw) {
    if (!raw) return 300;
    var n = parseInt(raw, 10);
    return isNaN(n) ? 300 : n;
  }

  function initButton(btn) {
    if (btn._djScrollToTopInit) return;
    btn._djScrollToTopInit = true;

    var threshold = parseThreshold(btn.getAttribute("data-threshold"));

    function onScroll() {
      if (window.scrollY > threshold) {
        btn.style.display = "";
        btn.classList.add("dj-scroll-to-top--visible");
      } else {
        btn.classList.remove("dj-scroll-to-top--visible");
      }
    }

    btn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });

    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  function initAll() {
    document.querySelectorAll(".dj-scroll-to-top").forEach(initButton);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  new MutationObserver(initAll).observe(document.body, {
    childList: true,
    subtree: true,
  });
})();
