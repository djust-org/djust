/**
 * Responsive Image — blur-up placeholder transition.
 *
 * When the full image finishes loading, adds `.dj-loaded` to fade it in
 * and hides the placeholder.  Uses MutationObserver for LiveView compat.
 */
(function () {
  "use strict";

  function initImage(container) {
    if (container._djResponsiveImageInit) return;
    container._djResponsiveImageInit = true;

    var img = container.querySelector(".dj-responsive-image__img");
    if (!img) return;

    function reveal() {
      img.classList.add("dj-loaded");
      var ph = container.querySelector(".dj-responsive-image__placeholder");
      if (ph) ph.classList.add("dj-hidden");
    }

    if (img.complete && img.naturalWidth > 0) {
      reveal();
    } else {
      img.addEventListener("load", reveal);
    }
  }

  function initAll() {
    document
      .querySelectorAll(".dj-responsive-image--blur-up")
      .forEach(initImage);
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
