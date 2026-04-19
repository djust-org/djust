/**
 * Theme Toggle — client-side theme switching.
 *
 * Reads prefers-color-scheme, stores preference in localStorage under
 * "djust-theme", applies data-theme attribute to <html>.
 *
 * If the toggle has a dj-click attribute, clicking a theme button also
 * sends the selected value to the server for persistence.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "djust-theme";

  function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function applyTheme(theme) {
    var resolved = theme === "system" ? getSystemTheme() : theme;
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.setAttribute("data-theme-mode", theme);
  }

  function activateButton(toggle, theme) {
    var buttons = toggle.querySelectorAll(".dj-theme-toggle__btn");
    buttons.forEach(function (btn) {
      var isActive = btn.getAttribute("data-theme") === theme;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      btn.classList.toggle("dj-theme-toggle__btn--active", isActive);
    });
    toggle.setAttribute("data-current", theme);
  }

  function initToggle(toggle) {
    // Determine initial theme
    var stored = localStorage.getItem(STORAGE_KEY);
    var current = stored || toggle.getAttribute("data-current") || "system";

    applyTheme(current);
    activateButton(toggle, current);

    toggle.addEventListener("click", function (e) {
      var btn = e.target.closest(".dj-theme-toggle__btn");
      if (!btn) return;
      var theme = btn.getAttribute("data-theme");
      if (!theme) return;

      localStorage.setItem(STORAGE_KEY, theme);
      applyTheme(theme);
      activateButton(toggle, theme);

      // If dj-click is set, push the event value via the data-value attribute
      // so djust's event system picks it up
      var djClick = toggle.getAttribute("dj-click");
      if (djClick) {
        btn.setAttribute("dj-click", djClick);
        btn.setAttribute("data-value", theme);
      }
    });
  }

  // Listen for system theme changes when user has selected "system"
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", function () {
      var current = localStorage.getItem(STORAGE_KEY) || "system";
      if (current === "system") {
        applyTheme("system");
      }
    });

  // Initialize on DOM load and observe for LiveView morphs
  function initAll() {
    document
      .querySelectorAll(".dj-theme-toggle")
      .forEach(function (el) {
        if (!el._djThemeInit) {
          initToggle(el);
          el._djThemeInit = true;
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // MutationObserver for LiveView compatibility
  new MutationObserver(function () {
    initAll();
  }).observe(document.body, { childList: true, subtree: true });
})();
