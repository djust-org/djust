/**
 * JsonViewer — expand/collapse and copy for the JsonViewer component
 * (dj-hook="JsonViewer").
 *
 * The tree is rendered by the server; this makes it interactive without
 * touching its structure, so a server re-render still patches cleanly:
 *
 * - click, Enter or Space on a node's toggle expands or collapses it
 *   (ArrowRight expands, ArrowLeft collapses), keeping the class
 *   ``dj-json__node--collapsed``, ``aria-expanded`` and the arrow in step;
 * - the Copy button writes the formatted JSON to the clipboard and says so
 *   in a polite live region.
 *
 * The expand/collapse state is the reader's own. A re-render that leaves a
 * node's markup unchanged leaves it as the reader set it.
 *
 * An app's own ``JsonViewer`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var NODE = ".dj-json__node";
  var COLLAPSED = "dj-json__node--collapsed";

  function announce(message) {
    var live = document.getElementById("dj-component-live");
    if (!live) {
      live = document.createElement("div");
      live.id = "dj-component-live";
      live.setAttribute("role", "status");
      live.setAttribute("aria-live", "polite");
      live.style.cssText =
        "position:absolute;width:1px;height:1px;overflow:hidden;" +
        "clip:rect(0 0 0 0);white-space:nowrap";
      document.body.appendChild(live);
    }
    // Identical text twice in a row is not announced again by most screen readers.
    live.textContent = live.textContent === message ? message + "\u00a0" : message;
  }

  function setCollapsed(node, collapsed) {
    var toggle = node.querySelector(":scope > .dj-json__toggle");
    if (!toggle) return;
    node.classList.toggle(COLLAPSED, collapsed);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    // The count badge exists only on nodes the server rendered collapsed.
    var count = node.querySelector(":scope > .dj-json__count");
    if (count) count.hidden = !collapsed;
    toggle.textContent = collapsed ? "▶" : "▼";
  }

  function toggleNode(toggle, collapsed) {
    var node = toggle.parentElement;
    if (!node || !node.matches(NODE)) return;
    var now = node.classList.contains(COLLAPSED);
    setCollapsed(node, collapsed === undefined ? !now : collapsed);
  }

  // The raw JSON is HTML-escaped inside its <script> block, which browsers do
  // not decode there; decode it with an inert parse (no scripts run).
  function rawJson(root) {
    var raw = root.querySelector(".dj-json-viewer__raw");
    if (!raw) return "";
    var doc = new DOMParser().parseFromString(raw.textContent || "", "text/html");
    return doc.documentElement.textContent || "";
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (_err) {
        ok = false;
      }
      document.body.removeChild(ta);
      if (ok) resolve();
      else reject(new Error("copy failed"));
    });
  }

  var jsonViewer = {
    mounted: function () {
      this._bind();
      this._enhance();
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      this._enhance();
    },

    destroyed: function () {
      this._unbind();
      clearTimeout(this._copyTimer);
    },

    // The toggle is a role=button span whose only content is an arrow glyph;
    // give it a name. Idempotent, and run again from updated().
    _enhance: function () {
      Array.prototype.forEach.call(
        this.el.querySelectorAll(".dj-json__toggle"),
        function (toggle) {
          if (toggle.hasAttribute("aria-label")) return;
          var node = toggle.parentElement;
          var kind = node && node.classList.contains("dj-json__node--array") ? "array" : "object";
          toggle.setAttribute("aria-label", "Toggle " + kind);
        }
      );
    },

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;

      var h = {
        click: function (e) {
          var target = e.target && e.target.closest ? e.target : null;
          if (!target) return;
          var toggle = target.closest(".dj-json__toggle");
          if (toggle && root.contains(toggle)) {
            toggleNode(toggle);
            return;
          }
          var copy = target.closest(".dj-json-viewer__copy");
          if (copy && root.contains(copy)) self._copy(copy);
        },

        keydown: function (e) {
          var toggle = e.target && e.target.classList && e.target.classList.contains("dj-json__toggle")
            ? e.target
            : null;
          if (!toggle || e.altKey || e.ctrlKey || e.metaKey) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleNode(toggle);
          } else if (e.key === "ArrowRight") {
            e.preventDefault();
            toggleNode(toggle, false);
          } else if (e.key === "ArrowLeft") {
            e.preventDefault();
            toggleNode(toggle, true);
          }
        },
      };
      this._h = h;
      Object.keys(h).forEach(function (type) {
        root.addEventListener(type, h[type]);
      });
    },

    _unbind: function () {
      var root = this._boundEl;
      var h = this._h;
      if (root && h) {
        Object.keys(h).forEach(function (type) {
          root.removeEventListener(type, h[type]);
        });
      }
      this._h = null;
      this._boundEl = null;
    },

    _copy: function (button) {
      var self = this;
      var text = rawJson(this.el);
      if (!text) return;
      copyText(text).then(
        function () {
          announce("JSON copied to the clipboard");
          // A second click while "Copied" shows must not capture "Copied".
          if (!self._copyTimer) self._copyOriginal = button.textContent;
          button.textContent = "Copied";
          clearTimeout(self._copyTimer);
          self._copyTimer = setTimeout(function () {
            button.textContent = self._copyOriginal;
            self._copyTimer = null;
          }, 1500);
        },
        function () {
          announce("Could not copy the JSON");
        }
      );
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.JsonViewer && !window.DjustHooks.JsonViewer) {
    window.DjustHooks.JsonViewer = jsonViewer;
  }
})();
