/**
 * ImageLightbox — keyboard, swipe and focus handling for the ImageLightbox
 * component (dj-hook="ImageLightbox").
 *
 * The lightbox is rendered by the server only while it is open, so the hook
 * lives exactly as long as the dialog is on screen:
 *
 * - focus moves into the dialog when it opens, Tab stays inside it, and focus
 *   returns to what had it when the dialog closes; the page behind does not
 *   scroll while it is open;
 * - Escape closes it, ArrowLeft / ArrowRight go to the previous / next image,
 *   and a horizontal swipe does the same on touch screens. Each of these
 *   activates the component's own Close / Previous / Next control, so the
 *   events and values are exactly the ones the server rendered
 *   (close_event, navigate_event); the hook sends nothing of its own;
 * - when the server shows another image, its caption and position are
 *   announced in a polite live region.
 *
 * An app's own ``ImageLightbox`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  // Page scroll is locked while ANY lightbox is open: one lock, taken by the
  // first and given back by the last. A page that was already locked is left
  // locked.
  var scrollLocks = 0;
  var scrollOwned = false;
  var scrollPrev = "";

  function lockScroll() {
    if (scrollLocks === 0) {
      var body = document.body;
      scrollOwned = body.style.overflow !== "hidden";
      if (scrollOwned) {
        scrollPrev = body.style.overflow;
        body.style.overflow = "hidden";
      }
    }
    scrollLocks += 1;
  }

  function unlockScroll() {
    if (scrollLocks === 0) return;
    scrollLocks -= 1;
    if (scrollLocks === 0 && scrollOwned && document.body.style.overflow === "hidden") {
      document.body.style.overflow = scrollPrev;
    }
    if (scrollLocks === 0) scrollOwned = false;
  }

  var FOCUSABLE = "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])";
  var SWIPE_MIN = 50;

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

  function control(root, cls) {
    return root.querySelector("." + cls);
  }

  function description(root) {
    var img = root.querySelector(".dj-lightbox__image");
    var caption = root.querySelector(".dj-lightbox__caption");
    var counter = root.querySelector(".dj-lightbox__counter");
    var parts = [];
    if (img && img.getAttribute("alt")) parts.push(img.getAttribute("alt"));
    if (caption && caption.textContent) parts.push(caption.textContent.trim());
    if (counter && counter.textContent) parts.push("Image " + counter.textContent.trim());
    return parts.join(". ");
  }

  var imageLightbox = {
    mounted: function () {
      this._opener = document.activeElement;
      this._lockScroll();
      this._bind();
      this._enhance();
      this._described = description(this.el);
      var first = control(this.el, "dj-lightbox__close") || this.el;
      this._focus(first);
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      this._enhance();
      // A patch that replaced the control the reader was on leaves focus on
      // the page behind; put it back on the same kind of control.
      if (!this.el.contains(document.activeElement)) {
        var again = (this._lastClass && control(this.el, this._lastClass)) ||
          control(this.el, "dj-lightbox__close") || this.el;
        this._focus(again);
      }
      var now = description(this.el);
      if (now && now !== this._described) announce(now);
      this._described = now;
    },

    destroyed: function () {
      this._unbind();
      this._unlockScroll();
      var opener = this._opener;
      this._opener = null;
      if (opener && opener !== document.body && opener.isConnected && opener.focus) {
        opener.focus();
      }
    },

    // Client-side attributes, re-applied from updated() in case a patch drops
    // them.
    _enhance: function () {
      var root = this.el;
      if (!root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");
      if (!root.hasAttribute("aria-label") && !root.hasAttribute("aria-labelledby")) {
        root.setAttribute("aria-label", "Image viewer");
      }
    },

    _focus: function (el) {
      if (el && el.focus) el.focus();
    },

    _lockScroll: function () {
      if (this._locked) return;
      this._locked = true;
      lockScroll();
    },

    _unlockScroll: function () {
      if (!this._locked) return;
      this._locked = false;
      unlockScroll();
    },

    _press: function (cls) {
      var button = control(this.el, cls);
      if (button) button.click();
    },

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;
      var startX = 0;
      var startY = 0;
      var touching = false;

      var onKey = function (e) {
        if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
        if (e.key === "Escape") {
          e.preventDefault();
          self._press("dj-lightbox__close");
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          self._press("dj-lightbox__prev");
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          self._press("dj-lightbox__next");
        } else if (e.key === "Tab") {
          var items = Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (el) {
            return !el.disabled && el.getAttribute("aria-hidden") !== "true";
          });
          if (!items.length) {
            e.preventDefault();
            self._focus(root);
            return;
          }
          var first = items[0];
          var last = items[items.length - 1];
          var active = document.activeElement;
          if (e.shiftKey && (active === first || active === root || !root.contains(active))) {
            e.preventDefault();
            self._focus(last);
          } else if (!e.shiftKey && (active === last || !root.contains(active))) {
            e.preventDefault();
            self._focus(first);
          }
        }
      };

      var h = {
        keydown: onKey,
        focusin: function (e) {
          var el = e.target;
          if (!el || !el.classList) return;
          ["dj-lightbox__close", "dj-lightbox__prev", "dj-lightbox__next"].forEach(function (cls) {
            if (el.classList.contains(cls)) self._lastClass = cls;
          });
        },
        touchstart: function (e) {
          if (e.touches.length !== 1) {
            touching = false;
            return;
          }
          touching = true;
          startX = e.touches[0].clientX;
          startY = e.touches[0].clientY;
        },
        touchend: function (e) {
          if (!touching || !e.changedTouches.length) return;
          touching = false;
          var dx = e.changedTouches[0].clientX - startX;
          var dy = e.changedTouches[0].clientY - startY;
          if (Math.abs(dx) >= SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * 1.5) {
            self._press(dx < 0 ? "dj-lightbox__next" : "dj-lightbox__prev");
          }
        },
      };
      this._h = h;
      Object.keys(h).forEach(function (type) {
        root.addEventListener(type, h[type], type.indexOf("touch") === 0 ? { passive: true } : false);
      });
      // Escape and the arrows work wherever focus has wandered to while open.
      this._docKey = function (e) {
        if (!root.contains(e.target)) onKey(e);
      };
      document.addEventListener("keydown", this._docKey);
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
      this._h = null;
      this._docKey = null;
      this._boundEl = null;
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.ImageLightbox && !window.DjustHooks.ImageLightbox) {
    window.DjustHooks.ImageLightbox = imageLightbox;
  }
})();
