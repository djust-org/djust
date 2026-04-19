/**
 * Infinite Scroll — client-side IntersectionObserver trigger.
 *
 * Watches the sentinel element and fires a djust event (data-event)
 * when it becomes visible, signalling the server to load more items.
 * Respects the data-threshold rootMargin for early triggering.
 * Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  function initInfiniteScroll(el) {
    if (el._djInfiniteScrollInit) return;
    el._djInfiniteScrollInit = true;

    var eventName = el.getAttribute("data-event") || "load_more";
    var threshold = el.getAttribute("data-threshold") || "200px";

    // Don't observe if already finished loading
    if (el.classList.contains("dj-infinite-scroll--finished")) return;

    var observer = new IntersectionObserver(
      function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) {
            // Don't fire if currently loading or finished
            if (
              el.classList.contains("dj-infinite-scroll--loading") ||
              el.classList.contains("dj-infinite-scroll--finished")
            ) {
              return;
            }
            el.dispatchEvent(
              new CustomEvent("djust:" + eventName, {
                bubbles: true,
                detail: { event: eventName },
              })
            );
          }
        }
      },
      { rootMargin: "0px 0px " + threshold + " 0px" }
    );

    observer.observe(el);

    // Store observer for cleanup on re-init
    el._djInfiniteScrollObserver = observer;
  }

  function initAll() {
    document
      .querySelectorAll('[dj-hook="InfiniteScroll"]')
      .forEach(initInfiniteScroll);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-initialize after LiveView patches
  new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var node = added[j];
        if (node.nodeType === 1) {
          if (
            node.getAttribute &&
            node.getAttribute("dj-hook") === "InfiniteScroll"
          ) {
            initInfiniteScroll(node);
          }
          if (node.querySelectorAll) {
            node
              .querySelectorAll('[dj-hook="InfiniteScroll"]')
              .forEach(initInfiniteScroll);
          }
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
