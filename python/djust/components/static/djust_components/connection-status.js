/**
 * Connection Status Bar — hooks into djust client.js WebSocket lifecycle.
 *
 * Shows/hides a slim bar based on WebSocket connection state:
 * - Hidden when connected
 * - Yellow "Reconnecting..." when disconnected
 * - Green "Reconnected" flash on recovery
 */
(function () {
  "use strict";

  function initConnectionStatus(el) {
    const reconnectingText = el.dataset.reconnectingText || "Reconnecting...";
    const connectedText = el.dataset.connectedText || "Reconnected";
    const textEl = el.querySelector(".dj-connection-status__text");
    let hideTimeout = null;

    function showReconnecting() {
      if (hideTimeout) { clearTimeout(hideTimeout); hideTimeout = null; }
      el.style.display = "";
      el.classList.remove("dj-connection-status--connected");
      el.classList.add("dj-connection-status--reconnecting");
      if (textEl) textEl.textContent = reconnectingText;
    }

    function showConnected() {
      el.classList.remove("dj-connection-status--reconnecting");
      el.classList.add("dj-connection-status--connected");
      if (textEl) textEl.textContent = connectedText;
      // Flash then hide after 2 seconds
      hideTimeout = setTimeout(function () {
        el.style.display = "none";
        el.classList.remove("dj-connection-status--connected");
        hideTimeout = null;
      }, 2000);
    }

    // djust client.js dispatches these lifecycle events
    document.addEventListener("djust:disconnected", showReconnecting);
    document.addEventListener("djust:reconnected", showConnected);

    // Store cleanup ref
    el._djCleanup = function () {
      document.removeEventListener("djust:disconnected", showReconnecting);
      document.removeEventListener("djust:reconnected", showConnected);
      if (hideTimeout) clearTimeout(hideTimeout);
    };
  }

  function initAll() {
    document.querySelectorAll(".dj-connection-status").forEach(initConnectionStatus);
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
          if (node.classList && node.classList.contains("dj-connection-status")) {
            initConnectionStatus(node);
          }
          node.querySelectorAll && node.querySelectorAll(".dj-connection-status").forEach(initConnectionStatus);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
