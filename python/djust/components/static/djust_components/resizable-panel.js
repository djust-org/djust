/**
 * ResizablePanel — drag and keyboard resizing for the ResizablePanel component
 * (dj-hook="ResizablePanel").
 *
 * - Drag the handle (mouse, touch or pen) to resize the panel along its
 *   direction. The size is clamped to min_size / max_size (data-min-size,
 *   data-max-size) and to the panel's container, and is held as an inline
 *   pixel width (or height) on the panel.
 * - The handle is a WAI-ARIA window splitter: Tab to it, then the arrow keys
 *   along the direction resize by 10 px (Shift: 50 px; mirrored in a
 *   right-to-left context, where the handle is on the left edge), Home / End go to the
 *   smallest / largest size, and aria-valuenow / min / max / text follow.
 *   Double-click (or Enter) puts the panel back at its initial size.
 * - ``disabled`` (data-disabled) turns all of it off.
 *
 * The size is the reader's own and is not sent to the server; the hook
 * announces it to the page instead, as a bubbling ``dj-resize`` CustomEvent on
 * the panel with ``detail: {size, direction}`` (px) when a drag or key press
 * ends. A re-render that leaves the panel's style unchanged leaves it as the
 * reader set it; one that changes the style (a new initial_size) takes over.
 *
 * An app's own ``ResizablePanel`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var STEP = 10;
  var BIG_STEP = 50;

  function limit(value, parentSize) {
    if (!value || value === "none" || value === "auto") return NaN;
    var n = parseFloat(value);
    if (isNaN(n)) return NaN;
    if (value.indexOf("%") !== -1) return (n / 100) * parentSize;
    if (value.indexOf("px") !== -1) return n;
    return NaN;
  }

  var resizablePanel = {
    mounted: function () {
      this._bind();
      this._initial = this._styleSize();
      this._set = null;
      this._enhance();
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      // A style the server set (a new initial_size) replaces ours; anything
      // else (an unrelated patch) leaves the reader's size alone.
      var now = this._styleSize();
      if (this._set === null || now !== this._set) {
        this._initial = now;
        this._set = null;
      }
      this._enhance();
    },

    destroyed: function () {
      this._unbind();
    },

    _horizontal: function () {
      return this.el.getAttribute("data-direction") !== "vertical";
    },

    // A horizontal panel in a right-to-left context has its handle on the left
    // edge, so dragging or pressing "outward" grows it the other way round.
    _rtl: function () {
      return this._horizontal() && window.getComputedStyle(this.el).direction === "rtl";
    },

    _prop: function () {
      return this._horizontal() ? "width" : "height";
    },

    _styleSize: function () {
      return this.el.style[this._prop()];
    },

    _handle: function () {
      for (var i = 0; i < this.el.children.length; i++) {
        if (this.el.children[i].classList.contains("dj-resizable-panel__handle")) {
          return this.el.children[i];
        }
      }
      return null;
    },

    _disabled: function () {
      return this.el.getAttribute("data-disabled") === "true";
    },

    _size: function () {
      var rect = this.el.getBoundingClientRect();
      return this._horizontal() ? rect.width : rect.height;
    },

    // {min, max} in px: the declared limits, and the container as the ceiling.
    _bounds: function () {
      var panel = this.el;
      var parent = panel.parentElement;
      var parentSize = parent ? (this._horizontal() ? parent.clientWidth : parent.clientHeight) : 0;
      var cs = window.getComputedStyle(panel);
      var min = limit(this._horizontal() ? cs.minWidth : cs.minHeight, parentSize);
      var max = limit(this._horizontal() ? cs.maxWidth : cs.maxHeight, parentSize);
      if (isNaN(min)) min = 0;
      if (isNaN(max) || max <= 0) max = Infinity;
      if (parentSize > 0) max = Math.min(max, parentSize);
      return { min: min, max: Math.max(min, max) };
    },

    // Role, name and values of the splitter; idempotent, re-applied from
    // updated() because a patch can drop what the hook added.
    _enhance: function () {
      var handle = this._handle();
      if (!handle) return;
      var disabled = this._disabled();
      handle.style.touchAction = "none";
      // The bar of a panel that resizes its width is a vertical separator (the
      // server renders the panel's direction here).
      handle.setAttribute("aria-orientation", this._horizontal() ? "vertical" : "horizontal");
      if (!handle.hasAttribute("aria-label")) handle.setAttribute("aria-label", "Resize panel");
      if (disabled) {
        handle.removeAttribute("tabindex");
        handle.setAttribute("aria-disabled", "true");
        return;
      }
      handle.removeAttribute("aria-disabled");
      if (!handle.hasAttribute("tabindex")) handle.setAttribute("tabindex", "0");
      this._values();
    },

    _values: function () {
      var handle = this._handle();
      if (!handle) return;
      var b = this._bounds();
      var size = Math.round(this._size());
      handle.setAttribute("aria-valuemin", String(Math.round(b.min)));
      if (isFinite(b.max)) handle.setAttribute("aria-valuemax", String(Math.round(b.max)));
      else handle.removeAttribute("aria-valuemax");
      handle.setAttribute("aria-valuenow", String(size));
      handle.setAttribute("aria-valuetext", size + " px");
    },

    _apply: function (px) {
      var b = this._bounds();
      var size = Math.max(b.min, Math.min(b.max, px));
      var value = Math.round(size) + "px";
      this.el.style[this._prop()] = value;
      this._set = this.el.style[this._prop()];
      this._values();
      return size;
    },

    _emit: function () {
      this.el.dispatchEvent(
        new CustomEvent("dj-resize", {
          bubbles: true,
          detail: {
            size: Math.round(this._size()),
            direction: this._horizontal() ? "horizontal" : "vertical",
          },
        })
      );
    },

    _reset: function () {
      this.el.style[this._prop()] = this._initial || "";
      this._set = null;
      this._values();
      this._emit();
    },

    _bind: function () {
      var self = this;
      var panel = this.el;
      this._boundEl = panel;
      var drag = null;

      var h = {
        pointerdown: function (e) {
          var handle = self._handle();
          if (!handle || e.target !== handle && !handle.contains(e.target)) return;
          if (self._disabled() || (e.button !== undefined && e.button !== 0)) return;
          e.preventDefault();
          drag = {
            id: e.pointerId,
            start: self._horizontal() ? e.clientX : e.clientY,
            size: self._size(),
            sign: self._rtl() ? -1 : 1,
          };
          if (handle.setPointerCapture && e.pointerId !== undefined) {
            try {
              handle.setPointerCapture(e.pointerId);
            } catch (_err) {
              // A pointer that is already gone cannot be captured; the drag simply ends.
            }
          }
          handle.focus();
        },

        pointermove: function (e) {
          if (!drag || e.pointerId !== drag.id) return;
          // The button was released somewhere we heard nothing about: stop.
          if (typeof e.buttons === "number" && e.buttons === 0) {
            drag = null;
            self._emit();
            return;
          }
          var pos = self._horizontal() ? e.clientX : e.clientY;
          self._apply(drag.size + drag.sign * (pos - drag.start));
        },

        pointerup: function (e) {
          if (!drag || e.pointerId !== drag.id) return;
          drag = null;
          self._emit();
        },

        pointercancel: function (e) {
          if (!drag || e.pointerId !== drag.id) return;
          drag = null;
          self._emit();
        },

        dblclick: function (e) {
          var handle = self._handle();
          if (!handle || !handle.contains(e.target) || self._disabled()) return;
          self._reset();
        },

        focusin: function (e) {
          var handle = self._handle();
          if (handle && e.target === handle) self._values();
        },

        keydown: function (e) {
          var handle = self._handle();
          if (!handle || e.target !== handle || self._disabled()) return;
          if (e.altKey || e.ctrlKey || e.metaKey) return;
          var horizontal = self._horizontal();
          var rtl = self._rtl();
          var grow = horizontal ? (rtl ? "ArrowLeft" : "ArrowRight") : "ArrowDown";
          var shrink = horizontal ? (rtl ? "ArrowRight" : "ArrowLeft") : "ArrowUp";
          var step = e.shiftKey ? BIG_STEP : STEP;
          var b = self._bounds();
          var target = null;
          if (e.key === grow) target = self._size() + step;
          else if (e.key === shrink) target = self._size() - step;
          else if (e.key === "Home") target = b.min;
          else if (e.key === "End") target = isFinite(b.max) ? b.max : null;
          else if (e.key === "Enter") {
            e.preventDefault();
            self._reset();
            return;
          }
          if (target === null) return;
          e.preventDefault();
          self._apply(target);
          self._emit();
        },
      };
      this._h = h;
      Object.keys(h).forEach(function (type) {
        panel.addEventListener(type, h[type]);
      });
    },

    _unbind: function () {
      var panel = this._boundEl;
      var h = this._h;
      if (panel && h) {
        Object.keys(h).forEach(function (type) {
          panel.removeEventListener(type, h[type]);
        });
      }
      this._h = null;
      this._boundEl = null;
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.ResizablePanel && !window.DjustHooks.ResizablePanel) {
    window.DjustHooks.ResizablePanel = resizablePanel;
  }
})();
