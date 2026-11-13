/**
 * AnimatedNumber — the counting animation for the AnimatedNumber component
 * (dj-hook="AnimatedNumber").
 *
 * The server renders the final number as text, so the page is correct before
 * and without this script. The hook counts up from 0 to data-value when the
 * number first appears, and from the number on screen to the new data-value
 * whenever a re-render changes it, over data-duration ms, formatted with
 * data-decimals and data-separator.
 *
 * - The number the animation ends on is the text the SERVER rendered, put back
 *   verbatim, so the final digits never depend on client-side rounding.
 * - Nothing is announced while it counts: the animated text is not a live
 *   region, and a reader who reaches it mid-count finds the final value a
 *   moment later.
 * - ``prefers-reduced-motion: reduce``, a zero duration or a value that is not
 *   a finite number skip the animation and leave the server's text alone.
 * - It stops with the element (destroyed) and never touches the prefix or
 *   suffix.
 *
 * An app's own ``AnimatedNumber`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  function group(digits, separator) {
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, separator);
  }

  // The same shape the server renders: truncation without decimals, a
  // thousands separator, ``decimals`` places otherwise. Intermediate frames
  // only; the last frame is the server's own text.
  function format(value, decimals, separator) {
    var negative = value < 0;
    var abs = Math.abs(value);
    var text;
    if (decimals > 0) {
      var fixed = abs.toFixed(decimals).split(".");
      text = group(fixed[0], separator) + "." + fixed[1];
    } else {
      text = group(String(Math.trunc(abs)), separator);
    }
    return (negative && Number(text.replace(/[^\d.]/g, "")) !== 0 ? "-" : "") + text;
  }

  function reducedMotion() {
    return !!(
      window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function ease(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  var animatedNumber = {
    mounted: function () {
      this._shown = 0;
      this._final = null;
      this._written = null;
      this._target = null;
      this._begin(true);
    },

    updated: function () {
      this._begin(false);
    },

    destroyed: function () {
      this._stop();
    },

    _valueEl: function () {
      return this.el.querySelector(".dj-animated-number__value");
    },

    _stop: function () {
      if (this._raf && window.cancelAnimationFrame) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
    },

    // Called on mount and after every patch. Starts an animation only when
    // the target changed (or on first appearance).
    _begin: function (first) {
      var el = this._valueEl();
      if (!el) return;
      var root = this.el;
      var value = parseFloat(root.getAttribute("data-value"));
      var duration = parseInt(root.getAttribute("data-duration"), 10);
      var decimals = parseInt(root.getAttribute("data-decimals"), 10) || 0;
      var separator = root.getAttribute("data-separator");
      if (separator === null) separator = ",";

      // What the server wants on screen: its text, unless the text on the page
      // is our own mid-count frame (no patch touched it).
      var text = el.textContent;
      if (text !== this._written) this._final = text;
      var changed = first || value !== this._target;
      this._target = value;
      if (!changed) return;

      this._stop();
      if (!isFinite(value) || !(duration > 0) || reducedMotion() || !window.requestAnimationFrame) {
        this._shown = isFinite(value) ? value : 0;
        this._written = null;
        if (this._final !== null && el.textContent !== this._final) el.textContent = this._final;
        return;
      }

      var self = this;
      var from = first ? 0 : this._shown;
      // Show the starting point at once: the server's final number must not
      // flash before the count begins.
      var initial = format(from, decimals, separator);
      el.textContent = initial;
      this._written = initial;
      var started = null;
      var step = function (now) {
        if (started === null) started = now;
        var t = Math.min(1, (now - started) / duration);
        var current = el.isConnected ? el : self._valueEl();
        if (!current) return;
        if (t >= 1) {
          self._shown = value;
          self._written = null;
          self._raf = 0;
          current.textContent = self._final;
          return;
        }
        var frame = from + (value - from) * ease(t);
        self._shown = frame;
        var shown = format(frame, decimals, separator);
        self._written = shown;
        current.textContent = shown;
        self._raf = window.requestAnimationFrame(step);
      };
      this._raf = window.requestAnimationFrame(step);
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.AnimatedNumber && !window.DjustHooks.AnimatedNumber) {
    window.DjustHooks.AnimatedNumber = animatedNumber;
  }
})();
