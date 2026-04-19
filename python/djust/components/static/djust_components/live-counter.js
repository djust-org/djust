/**
 * Live Counter — client-side JS for animated counter updates via WebSocket.
 *
 * Listens for djust server events matching the configured stream_event
 * and animates the counter value on change with a roll animation.
 */
(function () {
  "use strict";

  function initLiveCounter(el) {
    const streamEvent = el.dataset.streamEvent;
    const valueEl = el.querySelector(".dj-live-counter__value");
    if (!valueEl || !streamEvent) return;

    document.addEventListener("djust:" + streamEvent, function (e) {
      const detail = e.detail || {};
      const newValue = detail.value !== undefined ? detail.value : detail;
      if (newValue === undefined || newValue === null) return;

      valueEl.dataset.value = newValue;
      valueEl.textContent = String(newValue);

      // Trigger roll animation
      valueEl.classList.remove("dj-live-counter--animating");
      // Force reflow to restart animation
      void valueEl.offsetWidth;
      valueEl.classList.add("dj-live-counter--animating");
    });
  }

  function initAll() {
    document.querySelectorAll(".dj-live-counter").forEach(initLiveCounter);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-initialize after LiveView patches
  new MutationObserver(function (mutations) {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType === 1) {
          if (node.classList && node.classList.contains("dj-live-counter")) {
            initLiveCounter(node);
          }
          node.querySelectorAll && node.querySelectorAll(".dj-live-counter").forEach(initLiveCounter);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
