/**
 * Toast Container — client-side JS for server-pushed toast notifications.
 *
 * Listens for the special __toast__ djust event and renders toast elements
 * inside the {% toast_container %} with auto-dismiss and dismiss buttons.
 */
(function () {
  "use strict";

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function initToastContainer(container) {
    var maxToasts = parseInt(container.dataset.maxToasts, 10) || 5;

    function addToast(detail) {
      var message = detail.message || "";
      var type = detail.type || "info";
      var duration = detail.duration !== undefined ? detail.duration : 3000;

      // Enforce max toasts — remove oldest
      while (container.children.length >= maxToasts) {
        container.removeChild(container.firstChild);
      }

      var toast = document.createElement("div");
      toast.className = "dj-server-toast dj-server-toast--" + escapeHtml(type);
      toast.setAttribute("role", "status");

      var msgSpan = document.createElement("span");
      msgSpan.className = "dj-server-toast__message";
      msgSpan.textContent = message;
      toast.appendChild(msgSpan);

      var dismissBtn = document.createElement("button");
      dismissBtn.className = "dj-server-toast__dismiss";
      dismissBtn.setAttribute("aria-label", "Dismiss");
      dismissBtn.innerHTML = "&times;";
      dismissBtn.addEventListener("click", function () {
        removeToast(toast);
      });
      toast.appendChild(dismissBtn);

      container.appendChild(toast);

      // Auto-dismiss
      if (duration > 0) {
        setTimeout(function () {
          removeToast(toast);
        }, duration);
      }
    }

    function removeToast(toast) {
      if (!toast.parentNode) return;
      toast.classList.add("dj-server-toast--exiting");
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 300);
    }

    // Listen for __toast__ events from djust server push
    document.addEventListener("djust:__toast__", function (e) {
      addToast(e.detail || {});
    });
  }

  function initAll() {
    document.querySelectorAll(".dj-toast-container").forEach(initToastContainer);
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
          if (node.classList && node.classList.contains("dj-toast-container")) {
            initToastContainer(node);
          }
          node.querySelectorAll && node.querySelectorAll(".dj-toast-container").forEach(initToastContainer);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
