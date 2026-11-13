/**
 * CursorsOverlay — presence announcements and label placement for the
 * CursorsOverlay component (dj-hook="CursorsOverlay").
 *
 * The component draws what the view passes in ``users`` (name, color, x, y):
 * where the cursors are comes from the app (for example PresenceMixin and a
 * push_event), and nothing here sends anything to the server. The hook adds:
 *
 * - Announcements: when someone appears or disappears from ``users`` it says
 *   so in a polite live region ("Alice joined", "Bob left"; a crowd is
 *   summarised as a count). Movement is never announced. The cursors
 *   themselves are decoration (aria-hidden); the group keeps the server's
 *   "N cursors" label.
 * - Label placement: a name label sits right of and below its arrow. When that
 *   would run out of the overlay, or land on another cursor's label, the label
 *   is put on the left and/or above the arrow, and as a last resort moved down
 *   in steps (classes dj-cursors__label--left / --up / --stack-1..3, styled in
 *   components.css). Placement is computed from the positions the server
 *   sent, not from the animated ones, and again after every re-render and
 *   resize.
 * - Motion: the arrows ease to a new position (components.css); that is
 *   switched off for people who prefer reduced motion.
 *
 * Cursors are matched by their position in the list, so a re-render that
 * removes one patches the rest in place: everything the hook derives is
 * recomputed from the DOM after each patch, never remembered per node.
 *
 * An app's own ``CursorsOverlay`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var CURSOR = "dj-cursors__cursor";
  var LABEL = "dj-cursors__label";
  var MAX_ANNOUNCED = 3;
  // Arrow box (width x height in px) and the label's offset from it; the
  // numbers components.css uses.
  var ARROW_H = 20;
  var LABEL_DX = 12;
  var LABEL_DY = -2;
  var STACK_STEP = 24;
  var MAX_STACK = 3;
  var VARIANTS = [[], ["left"], ["up"], ["left", "up"]];

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
    live.textContent = live.textContent === message ? message + " " : message;
  }

  function cursors(root) {
    return Array.prototype.filter.call(root.children, function (c) {
      return c.classList && c.classList.contains(CURSOR);
    });
  }

  function labelOf(cursor) {
    return cursor.querySelector("." + LABEL);
  }

  // The names present, with multiplicity (two people can share a name).
  function nameCounts(root) {
    var counts = Object.create(null);
    cursors(root).forEach(function (c) {
      var name = c.getAttribute("data-user") || "";
      counts[name] = (counts[name] || 0) + 1;
    });
    return counts;
  }

  // left/top as the server wrote them (px). Not read from layout: the arrows
  // are mid-transition most of the time a position changes.
  function position(cursor) {
    var style = cursor.getAttribute("style") || "";
    var left = /(?:^|;)\s*left\s*:\s*(-?[\d.]+)px/.exec(style);
    var top = /(?:^|;)\s*top\s*:\s*(-?[\d.]+)px/.exec(style);
    return { x: left ? parseFloat(left[1]) : 0, y: top ? parseFloat(top[1]) : 0 };
  }

  function intersects(a, b) {
    return a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;
  }

  function summarise(verb, names) {
    if (!names.length) return "";
    if (names.length <= MAX_ANNOUNCED) return names.join(", ") + " " + verb;
    return names.length + " people " + verb;
  }

  var cursorsOverlay = {
    mounted: function () {
      this._names = nameCounts(this.el);
      this._enhance();
      this._layoutSoon();
      var self = this;
      if (typeof window.ResizeObserver === "function") {
        this._ro = new window.ResizeObserver(function () {
          self._layoutSoon();
        });
        this._ro.observe(this.el);
      }
    },

    updated: function () {
      this._enhance();
      this._presence();
      this._layoutSoon();
    },

    destroyed: function () {
      if (this._ro) this._ro.disconnect();
      this._ro = null;
      if (this._raf && window.cancelAnimationFrame) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
    },

    // The cursors are decoration; the group's label says how many there are.
    // Idempotent, re-applied after every patch (positional patches can hand
    // the attributes of a removed cursor to the next one, and a new cursor
    // arrives without them).
    _enhance: function () {
      cursors(this.el).forEach(function (c) {
        if (c.getAttribute("aria-hidden") !== "true") c.setAttribute("aria-hidden", "true");
      });
    },

    _presence: function () {
      var before = this._names || Object.create(null);
      var now = nameCounts(this.el);
      var joined = [];
      var left = [];
      Object.keys(now).forEach(function (name) {
        var extra = now[name] - (before[name] || 0);
        for (var i = 0; i < extra; i++) joined.push(name);
      });
      Object.keys(before).forEach(function (name) {
        var gone = before[name] - (now[name] || 0);
        for (var i = 0; i < gone; i++) left.push(name);
      });
      this._names = now;
      var parts = [summarise("joined", joined), summarise("left", left)].filter(Boolean);
      if (parts.length) announce(parts.join(". "));
    },

    _layoutSoon: function () {
      var self = this;
      if (!window.requestAnimationFrame) {
        this._layout();
        return;
      }
      if (this._raf) return;
      this._raf = window.requestAnimationFrame(function () {
        self._raf = 0;
        self._layout();
      });
    },

    // Put each label where it fits: not outside the overlay, not on another
    // label. Derived from scratch each time, in three passes so the sizes are
    // read after every earlier placement was undone and before any new one is
    // written (one layout, not one per label).
    _layout: function () {
      var root = this.el;
      var width = root.clientWidth;
      var height = root.clientHeight;
      var pairs = [];
      cursors(root).forEach(function (cursor) {
        var label = labelOf(cursor);
        if (label) pairs.push({ cursor: cursor, label: label });
      });
      pairs.forEach(function (p) {
        Array.prototype.slice.call(p.label.classList).forEach(function (c) {
          if (c.indexOf(LABEL + "--") === 0) p.label.classList.remove(c);
        });
      });
      pairs.forEach(function (p) {
        p.w = p.label.offsetWidth;
        p.h = p.label.offsetHeight;
      });
      var placed = [];
      pairs.forEach(function (p) {
        var at = position(p.cursor);
        var w = p.w;
        var h = p.h;
        if (!w || !h || !width || !height) return; // not laid out (hidden, or no layout)

        function rect(variant, stack) {
          var left = variant.indexOf("left") !== -1;
          var up = variant.indexOf("up") !== -1;
          var l = left ? at.x + LABEL_DX - w : at.x + LABEL_DX;
          var t = up ? at.y - h - 2 : at.y + ARROW_H + LABEL_DY;
          t += stack * STACK_STEP;
          return { l: l, t: t, r: l + w, b: t + h };
        }
        function fits(r) {
          return r.l >= 0 && r.t >= 0 && r.r <= width && r.b <= height;
        }
        function free(r) {
          return placed.every(function (q) {
            return !intersects(r, q);
          });
        }

        var chosen = null;
        for (var i = 0; i < VARIANTS.length && !chosen; i++) {
          var r = rect(VARIANTS[i], 0);
          if (fits(r) && free(r)) chosen = { variant: VARIANTS[i], stack: 0, rect: r };
        }
        for (var s = 1; s <= MAX_STACK && !chosen; s++) {
          for (var j = 0; j < VARIANTS.length && !chosen; j++) {
            if (VARIANTS[j].indexOf("up") !== -1) continue;
            var r2 = rect(VARIANTS[j], s);
            if (fits(r2) && free(r2)) chosen = { variant: VARIANTS[j], stack: s, rect: r2 };
          }
        }
        if (!chosen) chosen = { variant: [], stack: 0, rect: rect([], 0) };
        p.chosen = chosen;
        placed.push(chosen.rect);
      });
      pairs.forEach(function (p) {
        if (!p.chosen) return;
        p.chosen.variant.forEach(function (v) {
          p.label.classList.add(LABEL + "--" + v);
        });
        if (p.chosen.stack) p.label.classList.add(LABEL + "--stack-" + p.chosen.stack);
      });
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.CursorsOverlay && !window.DjustHooks.CursorsOverlay) {
    window.DjustHooks.CursorsOverlay = cursorsOverlay;
  }
})();
