/**
 * Tour — spotlight, popover placement, focus and keyboard for the Tour
 * component (dj-hook="Tour").
 *
 * The server renders the current step only (title, text, buttons, and the
 * step's ``target`` selector in data-target); this makes it a guided tour:
 *
 * - the page behind is dimmed with a cut-out around the target element and a
 *   ring around it; the popover sits beside the target (below, above, right,
 *   left, whichever fits) and follows it as the page scrolls or resizes. A
 *   target that does not exist, is hidden, or is not a valid selector (also
 *   after a server patch removed it) leaves the popover centred with nothing
 *   highlighted, and the tour keeps working;
 * - the overlay and popover are ``position: fixed``, which a transformed (or
 *   filtered) ancestor turns into "relative to that ancestor". The hook
 *   notices when the overlay does not cover the viewport and puts it back
 *   there, so the tour lines up and dims the whole page wherever it was
 *   rendered; the target is scrolled into view once per step (without
 *   animation under prefers-reduced-motion);
 * - the popover takes focus when a step appears, Tab stays inside it, and
 *   focus returns to what had it when the tour ends. The one exception is the
 *   highlighted target itself: clicking a field in the spotlight focuses it and
 *   it keeps focus, so the reader can type there (arrow keys then belong to the
 *   field; Escape and Tab still act on the tour). The dialog is named by
 *   the step title and described by its text, and a new step is announced
 *   ("Step 2 of 5: Create");
 * - ArrowRight / ArrowLeft go to the next / previous step (not past the last:
 *   finishing is a deliberate button press), and Escape skips the tour. Each
 *   of them presses the component's own Next / Back / Skip / Finish button, so
 *   the events and values are exactly the ones the server rendered
 *   (event + "value" next | prev | skip | finish); the hook sends nothing of
 *   its own. Escape on the last step, which has no Skip, presses Finish.
 *
 * An app's own ``Tour`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var PAD = 4; // breathing room around the target
  var GAP = 12; // between target and popover
  var MARGIN = 8; // from the viewport edge
  var FOCUSABLE = "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";
  var uid = 0;

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

  function child(root, cls) {
    return root.querySelector("." + cls);
  }

  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  var tour = {
    mounted: function () {
      this._opener = document.activeElement;
      this._step = this.el.getAttribute("data-step");
      this._bind();
      this._enhance();
      this._scrollTarget();
      this._place();
      this._focusPopover();
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      var step = this.el.getAttribute("data-step");
      var changed = step !== this._step;
      this._step = step;
      this._enhance();
      if (changed) {
        this._scrolled = false;
        this._scrollTarget();
      }
      // The target may have moved, appeared or vanished with the patch.
      this._watch();
      this._place();
      if (changed || !this.el.contains(document.activeElement)) this._focusPopover();
      if (changed) this._announceStep();
    },

    destroyed: function () {
      this._unbind();
      var opener = this._opener;
      this._opener = null;
      if (opener && opener !== document.body && opener.isConnected && opener.focus) opener.focus();
    },

    _popover: function () {
      return child(this.el, "dj-tour__popover");
    },

    _overlay: function () {
      return child(this.el, "dj-tour__overlay");
    },

    // The element to spotlight, or null: no selector, a selector that does not
    // parse, no match, a match inside the tour itself, or one that is not shown.
    _target: function () {
      var selector = this.el.getAttribute("data-target");
      if (!selector) return null;
      var el = null;
      try {
        el = document.querySelector(selector);
      } catch (_err) {
        return null;
      }
      if (!el || this.el.contains(el)) return null;
      var rect = el.getBoundingClientRect();
      return rect.width > 0 || rect.height > 0 ? el : null;
    },

    // Name, description and a focusable popover; idempotent, re-applied from
    // updated() because a patch can drop what the hook added.
    _enhance: function () {
      var root = this.el;
      var popover = this._popover();
      if (!popover) return;
      if (!popover.hasAttribute("tabindex")) popover.setAttribute("tabindex", "-1");
      var title = child(root, "dj-tour__title");
      var content = child(root, "dj-tour__content");
      uid += this._uid ? 0 : 1;
      this._uid = this._uid || uid;
      if (title) {
        if (!title.id) title.id = "dj-tour-title-" + this._uid;
        if (!root.hasAttribute("aria-labelledby") && !root.hasAttribute("aria-label")) {
          root.setAttribute("aria-labelledby", title.id);
        }
      }
      if (content) {
        if (!content.id) content.id = "dj-tour-content-" + this._uid;
        if (!root.hasAttribute("aria-describedby")) root.setAttribute("aria-describedby", content.id);
      }
    },

    _focusPopover: function () {
      var popover = this._popover();
      if (popover && popover.focus) popover.focus();
    },

    _announceStep: function () {
      var title = child(this.el, "dj-tour__title");
      var step = parseInt(this.el.getAttribute("data-step"), 10);
      var total = parseInt(this.el.getAttribute("data-total"), 10);
      if (isNaN(step) || isNaN(total)) return;
      announce("Step " + (step + 1) + " of " + total + (title ? ": " + title.textContent.trim() : ""));
    },

    _scrollTarget: function () {
      if (this._scrolled) return;
      this._scrolled = true;
      var el = this._target();
      if (el && el.scrollIntoView) {
        el.scrollIntoView({
          block: "center",
          inline: "nearest",
          behavior: reducedMotion() ? "auto" : "smooth",
        });
      }
    },

    _placeSoon: function () {
      var self = this;
      if (!window.requestAnimationFrame) {
        this._place();
        return;
      }
      if (this._raf) return;
      this._raf = window.requestAnimationFrame(function () {
        self._raf = 0;
        self._place();
      });
    },

    // Spotlight, ring and popover for the current target, in the overlay's own
    // coordinates.
    _place: function () {
      var overlay = this._overlay();
      var popover = this._popover();
      if (!overlay || !popover) return;
      var target = this._target();
      var ring = this._ring;
      if (ring && ring.parentNode !== overlay) ring = this._ring = null;
      var frame = this._frame(overlay);
      var W = frame.W;
      var H = frame.H;

      if (!target) {
        overlay.style.clipPath = "";
        if (ring) ring.style.display = "none";
        if (frame.moved) {
          // Centre it ourselves: its 50% / 50% is measured from the ancestor.
          popover.style.transform = "none";
          popover.style.left = "0px";
          popover.style.top = "0px";
          var c = popover.getBoundingClientRect();
          popover.style.left = Math.round((W - c.width) / 2 - frame.ox) + "px";
          popover.style.top = Math.round((H - c.height) / 2 - frame.oy) + "px";
        } else {
          popover.style.top = "";
          popover.style.left = "";
          popover.style.transform = "";
        }
        return;
      }

      var t = target.getBoundingClientRect();
      var x = t.left - PAD;
      var y = t.top - PAD;
      var w = t.width + 2 * PAD;
      var h = t.height + 2 * PAD;

      // A cut-out in the dimmed overlay (even-odd: the inner rectangle is a hole).
      var x2 = x + w;
      var y2 = y + h;
      overlay.style.clipPath =
        "polygon(evenodd, 0 0, " + W + "px 0, " + W + "px " + H + "px, 0 " + H + "px, 0 0, " +
        x + "px " + y + "px, " + x + "px " + y2 + "px, " + x2 + "px " + y2 + "px, " +
        x2 + "px " + y + "px, " + x + "px " + y + "px)";

      if (!ring) {
        ring = this._ring = document.createElement("div");
        ring.className = "dj-tour__ring";
        ring.setAttribute("aria-hidden", "true");
        ring.style.position = "absolute";
        ring.style.pointerEvents = "none";
        ring.style.boxSizing = "border-box";
        ring.style.border = "2px solid var(--dj-tour-highlight-color, #3b82f6)";
        ring.style.borderRadius = "var(--dj-tour-highlight-radius, 0.375rem)";
        overlay.appendChild(ring);
      }
      ring.style.display = "";
      // Just outside the cut-out: the border would be clipped away inside it.
      ring.style.left = x - 2 + "px";
      ring.style.top = y - 2 + "px";
      ring.style.width = w + 4 + "px";
      ring.style.height = h + 4 + "px";

      // The popover is fixed-positioned like the overlay: measure it with no
      // offset transform, then put it where it fits.
      popover.style.transform = "none";
      popover.style.left = "0px";
      popover.style.top = "0px";
      var p = popover.getBoundingClientRect();
      var pw = p.width;
      var ph = p.height;
      var left;
      var top;
      var centred = x + w / 2 - pw / 2;
      if (y2 + GAP + ph <= H - MARGIN) {
        top = y2 + GAP;
        left = centred;
      } else if (y - GAP - ph >= MARGIN) {
        top = y - GAP - ph;
        left = centred;
      } else if (x2 + GAP + pw <= W - MARGIN) {
        left = x2 + GAP;
        top = y + h / 2 - ph / 2;
      } else if (x - GAP - pw >= MARGIN) {
        left = x - GAP - pw;
        top = y + h / 2 - ph / 2;
      } else {
        left = centred;
        top = H - ph - MARGIN; // nothing fits beside it: the bottom edge, over the target
      }
      left = Math.max(MARGIN, Math.min(left, W - pw - MARGIN));
      top = Math.max(MARGIN, Math.min(top, H - ph - MARGIN));
      popover.style.left = Math.round(left - frame.ox) + "px";
      popover.style.top = Math.round(top - frame.oy) + "px";
    },

    // Viewport size and the offset of what ``position: fixed`` is measured
    // from. Normally the viewport itself; inside a transformed ancestor it is
    // that ancestor's box, so the overlay is given the viewport's geometry
    // explicitly.
    _frame: function (overlay) {
      var doc = document.documentElement;
      var vw = doc.clientWidth || window.innerWidth;
      var vh = doc.clientHeight || window.innerHeight;
      var r = overlay.getBoundingClientRect();
      var normal =
        Math.abs(r.left) < 1 && Math.abs(r.top) < 1 &&
        Math.abs(r.width - vw) < 2 && Math.abs(r.height - vh) < 2;
      if (normal && !this._moved) return { ox: 0, oy: 0, W: r.width, H: r.height, moved: false };
      var s = overlay.style;
      s.left = "0px";
      s.top = "0px";
      s.right = "auto";
      s.bottom = "auto";
      s.width = "0px";
      s.height = "0px";
      var o = overlay.getBoundingClientRect();
      var ox = o.left;
      var oy = o.top;
      s.left = -ox + "px";
      s.top = -oy + "px";
      s.width = vw + "px";
      s.height = vh + "px";
      this._moved = true;
      return { ox: ox, oy: oy, W: vw, H: vh, moved: true };
    },

    _press: function (cls) {
      var button = child(this.el, cls);
      if (button && !button.disabled) button.click();
    },

    _onKey: function (e) {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      var root = this.el;
      // Arrow keys in a field the reader is typing into belong to the field.
      var t = e.target;
      var editing =
        t && !root.contains(t) && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""));
      if (editing && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
      var step = parseInt(root.getAttribute("data-step"), 10);
      var total = parseInt(root.getAttribute("data-total"), 10);
      var last = step === total - 1;
      if (e.key === "Escape") {
        e.preventDefault();
          // Only the top dialog owns Escape; ancestor and window shortcuts
          // must not act on the same consumed key.
          e.stopPropagation();
        if (child(root, "dj-tour__skip")) this._press("dj-tour__skip");
        else if (last) this._press("dj-tour__next");
      } else if (e.key === "ArrowRight") {
        if (last) return;
        e.preventDefault();
        this._press("dj-tour__next");
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        this._press("dj-tour__prev");
      } else if (e.key === "Tab") {
        var items = Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (el) {
          return !el.disabled;
        });
        var popover = this._popover();
        var active = document.activeElement;
        if (!items.length) {
          e.preventDefault();
          this._focusPopover();
          return;
        }
        var first = items[0];
        var end = items[items.length - 1];
        if (e.shiftKey && (active === first || active === popover || !root.contains(active))) {
          e.preventDefault();
          end.focus();
        } else if (!e.shiftKey && (active === end || !root.contains(active))) {
          e.preventDefault();
          first.focus();
        }
      }
    },

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;
      var h = {
        keydown: function (e) {
          self._onKey(e);
        },
      };
      this._h = h;
      Object.keys(h).forEach(function (type) {
        root.addEventListener(type, h[type]);
      });
      // Escape and the arrows work wherever focus has wandered to; focus that
      // leaves the (modal) dialog is brought back.
      this._docKey = function (e) {
        if (!root.contains(e.target)) self._onKey(e);
      };
      this._docFocus = function (e) {
        if (root.contains(e.target)) return;
        var target = self._target();
        if (target && target.contains(e.target)) return; // the highlighted field keeps focus
        self._focusPopover();
      };
      this._reflow = function () {
        self._placeSoon();
      };
      document.addEventListener("keydown", this._docKey);
      document.addEventListener("focusin", this._docFocus);
      window.addEventListener("resize", this._reflow);
      document.addEventListener("scroll", this._reflow, { capture: true, passive: true });
      this._watch();
    },

    // Re-place when the popover or the current target changes size.
    _watch: function () {
      if (!window.ResizeObserver || !this._reflow) return;
      if (this._ro) this._ro.disconnect();
      this._ro = new window.ResizeObserver(this._reflow);
      var popover = this._popover();
      if (popover) this._ro.observe(popover);
      var target = this._target();
      if (target) this._ro.observe(target);
    },

    _unbind: function () {
      var root = this._boundEl;
      var h = this._h;
      if (root && h) {
        Object.keys(h).forEach(function (type) {
          root.removeEventListener(type, h[type]);
        });
      }
      if (this._docKey) document.removeEventListener("keydown", this._docKey);
      if (this._docFocus) document.removeEventListener("focusin", this._docFocus);
      if (this._reflow) {
        window.removeEventListener("resize", this._reflow);
        document.removeEventListener("scroll", this._reflow, true);
      }
      if (this._ro) this._ro.disconnect();
      if (this._raf && window.cancelAnimationFrame) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
      this._ro = null;
      this._h = null;
      this._docKey = null;
      this._docFocus = null;
      this._reflow = null;
      this._boundEl = null;
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.Tour && !window.DjustHooks.Tour) {
    window.DjustHooks.Tour = tour;
  }
})();
