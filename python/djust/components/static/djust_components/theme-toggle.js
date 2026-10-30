/**
 * Theme Toggle: legacy standalone script for {% theme_toggle %}.
 *
 * Not needed any more: the djust client bundle switches the mode on a click of
 * a toggle button (`data-theme-mode`), and djust.theming's theme.js does the
 * same when {% theme_head %} is on the page. Both store the choice under
 * "djust-theme-mode", the key djust.theming reads.
 *
 * Kept so pages that still include it keep working: it applies the stored mode
 * at load (moving a choice saved under the old "djust-theme" key) and handles
 * clicks only when the client bundle's component behaviours are absent.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "djust-theme-mode";
  var LEGACY_KEY = "djust-theme";
  var MODES = ["light", "dark", "system"];

  function read() {
    try {
      var legacy = localStorage.getItem(LEGACY_KEY);
      if (legacy && !localStorage.getItem(STORAGE_KEY)) {
        localStorage.setItem(STORAGE_KEY, legacy);
      }
      if (legacy) localStorage.removeItem(LEGACY_KEY);
      return localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }

  function applyTheme(mode) {
    var dark =
      mode === "dark" ||
      (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    document.documentElement.setAttribute("data-theme-mode", mode);
  }

  function sync(mode) {
    document.querySelectorAll(".dj-theme-toggle").forEach(function (toggle) {
      toggle.setAttribute("data-current", mode);
      toggle.querySelectorAll("[data-theme-mode]").forEach(function (btn) {
        var on = btn.getAttribute("data-theme-mode") === mode;
        btn.setAttribute("aria-checked", on ? "true" : "false");
        btn.classList.toggle("dj-theme-toggle__btn--active", on);
      });
    });
  }

  var stored = read();
  if (stored && MODES.indexOf(stored) >= 0 && !window.djustTheme) applyTheme(stored);

  document.addEventListener("click", function (e) {
    if (document._djustComponentBehaviors || window.djustTheme) return;
    var btn = e.target.closest && e.target.closest(".dj-theme-toggle [data-theme-mode]");
    if (!btn) return;
    var mode = btn.getAttribute("data-theme-mode");
    if (MODES.indexOf(mode) < 0) return;
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch (err) {
      /* storage blocked */
    }
    applyTheme(mode);
    sync(mode);
  });

  function init() {
    if (stored && MODES.indexOf(stored) >= 0) sync(stored);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
