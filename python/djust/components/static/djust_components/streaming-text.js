/**
 * Streaming Text — client-side JS for incremental text rendering.
 *
 * Listens for djust server events matching the configured stream_event
 * and appends text chunks to the content area. Supports auto-scroll.
 *
 * Works with djust's pushEvent / server-sent events.
 */
(function () {
  "use strict";

  function initStreamingText(el) {
    const streamEvent = el.dataset.streamEvent;
    const autoScroll = el.dataset.autoScroll === "true";
    const contentEl = el.querySelector(".dj-streaming-text__content");
    if (!contentEl || !streamEvent) return;

    // Listen for djust server-pushed events
    document.addEventListener("djust:" + streamEvent, function (e) {
      const detail = e.detail || {};
      const chunk = detail.chunk || detail.text || "";
      if (chunk) {
        contentEl.textContent += chunk;
      }
      // Replace mode (full text update)
      if (detail.replace !== undefined) {
        contentEl.textContent = detail.replace;
      }
      // Auto-scroll to bottom
      if (autoScroll) {
        el.scrollTop = el.scrollHeight;
      }
    });
  }

  // Initialize on DOM ready
  function initAll() {
    document.querySelectorAll(".dj-streaming-text").forEach(initStreamingText);
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
          if (node.classList && node.classList.contains("dj-streaming-text")) {
            initStreamingText(node);
          }
          node.querySelectorAll && node.querySelectorAll(".dj-streaming-text").forEach(initStreamingText);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
