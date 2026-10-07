/**
 * ImageCropper — the interaction layer of the ImageCropper component
 * (dj-hook="ImageCropper"): a crop box over an image that the reader moves,
 * resizes and sends.
 *
 * - The box lives in the image's NATURAL pixels. What is sent, and all that is
 *   sent, is {x, y, width, height} as whole numbers of those pixels, to
 *   data-crop-event; the server crops (djust.components.cropping validates the
 *   numbers, which are the browser's claim, and crops with Pillow). Nothing about
 *   the image's bytes passes through here, and the image is never uploaded.
 * - Pointer events (mouse, touch, pen) with pointer capture: drag the box to
 *   move it, a handle (corner or edge) to resize it, or the image around it to
 *   draw a new one. The page does not scroll during a touch drag
 *   (touch-action: none).
 * - data-aspect-ratio ("16/9", "4:3" or "1.5") locks the shape while moving and
 *   resizing and starts the box in that shape; empty is free. data-min-width /
 *   data-min-height are natural pixels; the box never gets smaller (an image
 *   smaller than that is selected whole).
 * - Keyboard: focus the crop area, then the arrow keys move it by one screen
 *   pixel (Shift: ten times that), Alt with an arrow resizes it, Enter crops and
 *   Escape resets. Every change is announced with the box in image pixels.
 * - The box follows the image when its size on screen changes (ResizeObserver),
 *   is cleared when the server shows another image, and stays through patches
 *   that do not. A broken image disables Crop and says so.
 *
 * Nothing is created by this script: the handles, the status line and the
 * buttons are in the markup, so a patch cannot remove one; the selection is
 * marked client-owned (dj-update="ignore") because the hook sets its style.
 *
 * An app's own ``ImageCropper`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when neither
 * registry already holds one.
 */
(function () {
  "use strict";

  var HANDLE_SIGNS = {
    nw: [-1, -1], n: [0, -1], ne: [1, -1], e: [1, 0], se: [1, 1], s: [0, 1], sw: [-1, 1], w: [-1, 0],
  };
  var NUDGE = 1;
  var NUDGE_LARGE = 10;
  var DRAG_START = 3;

  // --- geometry: pure functions on boxes {x, y, w, h} in natural pixels --------------

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function parseRatio(value) {
    var text = String(value == null ? "" : value).trim().replace(":", "/");
    if (!text) return 0;
    var ratio;
    if (text.indexOf("/") >= 0) {
      var parts = text.split("/");
      ratio = parseFloat(parts[0]) / parseFloat(parts[1]);
    } else {
      ratio = parseFloat(text);
    }
    return isFinite(ratio) && ratio >= 0.05 && ratio <= 20 ? ratio : 0;
  }

  // The size limits of an image: a box never has to be larger than the image.
  function limits(nw, nh, minW, minH, ratio) {
    var mw = Math.min(Math.max(1, minW), nw);
    var mh = Math.min(Math.max(1, minH), nh);
    if (ratio) {
      // The smallest box of the ratio that satisfies both minimums and fits.
      var w = Math.max(mw, mh * ratio);
      var h = w / ratio;
      if (w > nw) { w = nw; h = w / ratio; }
      if (h > nh) { h = nh; w = h * ratio; }
      mw = Math.min(w, nw);
      mh = Math.min(h, nh);
    }
    return { minW: mw, minH: mh };
  }

  function initialBox(nw, nh, ratio, minW, minH) {
    var w = nw * 0.8;
    var h = nh * 0.8;
    if (ratio) {
      if (w / h > ratio) w = h * ratio;
      else h = w / ratio;
    }
    var lim = limits(nw, nh, minW, minH, ratio);
    w = Math.max(w, lim.minW);
    h = ratio ? w / ratio : Math.max(h, lim.minH);
    if (w > nw) { w = nw; if (ratio) h = w / ratio; }
    if (h > nh) { h = nh; if (ratio) w = h * ratio; }
    return { x: (nw - w) / 2, y: (nh - h) / 2, w: w, h: h };
  }

  function moveBox(box, dx, dy, nw, nh) {
    return {
      x: clamp(box.x + dx, 0, nw - box.w),
      y: clamp(box.y + dy, 0, nh - box.h),
      w: box.w,
      h: box.h,
    };
  }

  // Resize ``box`` by dragging ``handle`` by (dx, dy). The opposite side (corner or
  // edge) stays; a locked ratio is kept; the box stays inside the image and above
  // the minimum.
  function resizeBox(box, handle, dx, dy, ratio, nw, nh, minW, minH) {
    var sign = HANDLE_SIGNS[handle];
    var lim = limits(nw, nh, minW, minH, ratio);
    var x0 = box.x;
    var y0 = box.y;
    var x1 = box.x + box.w;
    var y1 = box.y + box.h;
    var corner = sign[0] !== 0 && sign[1] !== 0;
    if (!ratio) {
      if (sign[0] < 0) x0 = clamp(x0 + dx, 0, x1 - lim.minW);
      if (sign[0] > 0) x1 = clamp(x1 + dx, x0 + lim.minW, nw);
      if (sign[1] < 0) y0 = clamp(y0 + dy, 0, y1 - lim.minH);
      if (sign[1] > 0) y1 = clamp(y1 + dy, y0 + lim.minH, nh);
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    }
    if (corner) {
      // The opposite corner stays; the diagonal drag sets the size.
      var ax = sign[0] < 0 ? x1 : x0;
      var ay = sign[1] < 0 ? y1 : y0;
      var cx = (sign[0] < 0 ? x0 : x1) + dx;
      var cy = (sign[1] < 0 ? y0 : y1) + dy;
      var w = (Math.abs(cx - ax) + Math.abs(cy - ay) * ratio) / 2;
      var room = Math.min(sign[0] < 0 ? ax : nw - ax, (sign[1] < 0 ? ay : nh - ay) * ratio);
      w = clamp(w, lim.minW, Math.max(lim.minW, room));
      var h = w / ratio;
      return { x: sign[0] < 0 ? ax - w : ax, y: sign[1] < 0 ? ay - h : ay, w: w, h: h };
    }
    if (sign[0] !== 0) {
      // A side handle changes the width; the height follows, centred.
      var mid = box.y + box.h / 2;
      var sideRoom = sign[0] < 0 ? x1 : nw - x0;
      var heightRoom = 2 * Math.min(mid, nh - mid) * ratio;
      var nwid = clamp(box.w + sign[0] * dx, lim.minW, Math.max(lim.minW, Math.min(sideRoom, heightRoom)));
      var nhgt = nwid / ratio;
      return { x: sign[0] < 0 ? x1 - nwid : x0, y: clamp(mid - nhgt / 2, 0, nh - nhgt), w: nwid, h: nhgt };
    }
    var midX = box.x + box.w / 2;
    var sideRoomV = sign[1] < 0 ? y1 : nh - y0;
    var widthRoom = (2 * Math.min(midX, nw - midX)) / ratio;
    var nhei = clamp(box.h + sign[1] * dy, lim.minH, Math.max(lim.minH, Math.min(sideRoomV, widthRoom)));
    var nwei = nhei * ratio;
    return { x: clamp(midX - nwei / 2, 0, nw - nwei), y: sign[1] < 0 ? y1 - nhei : y0, w: nwei, h: nhei };
  }

  // A new box from the point where the drag started to where the pointer is.
  function newBox(ax, ay, px, py, ratio, nw, nh, minW, minH) {
    ax = clamp(ax, 0, nw);
    ay = clamp(ay, 0, nh);
    px = clamp(px, 0, nw);
    py = clamp(py, 0, nh);
    var lim = limits(nw, nh, minW, minH, ratio);
    var dirX = px >= ax ? 1 : -1;
    var dirY = py >= ay ? 1 : -1;
    var w = Math.abs(px - ax);
    var h = Math.abs(py - ay);
    if (ratio) {
      w = (w + h * ratio) / 2;
      var room = Math.min(dirX > 0 ? nw - ax : ax, (dirY > 0 ? nh - ay : ay) * ratio);
      w = clamp(w, lim.minW, Math.max(lim.minW, room));
      h = w / ratio;
    } else {
      w = Math.max(w, lim.minW);
      h = Math.max(h, lim.minH);
    }
    // Where the point has too little room for the minimum, the box is shifted
    // back inside rather than anchored at the point.
    return {
      x: clamp(dirX > 0 ? ax : ax - w, 0, nw - w),
      y: clamp(dirY > 0 ? ay : ay - h, 0, nh - h),
      w: w,
      h: h,
    };
  }

  // The whole numbers to send: inside the image, at least one pixel.
  function toPixels(box, nw, nh) {
    var x = clamp(Math.round(box.x), 0, nw - 1);
    var y = clamp(Math.round(box.y), 0, nh - 1);
    var w = clamp(Math.round(box.w), 1, nw - x);
    var h = clamp(Math.round(box.h), 1, nh - y);
    return { x: x, y: y, width: w, height: h };
  }

  // Same public entry point dj-click and dj-viewport use, so the event works
  // over WebSocket, SSE and HTTP-only, honours strict parameter contracts and
  // reaches the right view when several are mounted. Falls back to the hook's
  // own pushEvent when the client API is absent.
  function addContext(params, el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      var ds = node.dataset || {};
      if (params.component_id === undefined && ds.componentId) params.component_id = ds.componentId;
      if (params.view_id === undefined && ds.djustEmbedded) params.view_id = ds.djustEmbedded;
    }
  }

  function send(hook, root, eventName, params) {
    var d = window.djust;
    if (d && typeof d.handleEvent === "function") {
      var sent = params;
      if (typeof d._strictBinding === "function") {
        var strict = d._strictBinding(root, eventName, params, [], root);
        if (strict === false) return;
        if (strict) sent = strict;
      }
      if (sent === params) addContext(sent, root);
      if (typeof d._markSlotOf === "function") d._markSlotOf(sent, root);
      d.handleEvent(eventName, sent);
    } else if (typeof hook.pushEvent === "function") {
      addContext(params, root);
      hook.pushEvent(eventName, params);
    }
  }

  var imageCropper = {
    geometry: {
      parseRatio: parseRatio,
      limits: limits,
      initialBox: initialBox,
      moveBox: moveBox,
      resizeBox: resizeBox,
      newBox: newBox,
      toPixels: toPixels,
    },

    mounted: function () {
      this._bind();
    },

    // A patch can replace the image (another one to crop), change the shape or
    // reset what the hook sets on server-rendered elements.
    updated: function () {
      if (this._root !== this.el || this._img !== this._find("image")) {
        this._unbind();
        this._bind();
        return;
      }
      this._claim();
      var ratio = parseRatio(this.el.getAttribute("data-aspect-ratio"));
      var minW = this._num("data-min-width", 50);
      var minH = this._num("data-min-height", 50);
      if (ratio !== this._ratio || minW !== this._minW || minH !== this._minH) {
        this._ratio = ratio;
        this._minW = minW;
        this._minH = minH;
        if (this._ready) this._reset(false);
      }
      this._sync();
    },

    destroyed: function () {
      this._unbind();
    },

    // --- setup -------------------------------------------------------------------

    _find: function (name) {
      return this._root ? this._root.querySelector(".dj-image-cropper__" + name) : this.el.querySelector(".dj-image-cropper__" + name);
    },

    _num: function (attr, fallback) {
      var n = parseInt(this.el.getAttribute(attr), 10);
      return isFinite(n) && n > 0 ? n : fallback;
    },

    _bind: function () {
      var self = this;
      var root = this.el;
      this._root = root;
      this._img = this._find("image");
      this._wrap = this._find("canvas");
      this._sel = this._find("selection");
      this._ready = false;
      this._box = null;
      this._start = null;
      this._drag = null;
      this._ratio = parseRatio(root.getAttribute("data-aspect-ratio"));
      this._minW = this._num("data-min-width", 50);
      this._minH = this._num("data-min-height", 50);
      if (!this._img || !this._wrap || !this._sel) return;

      this._h_load = function () {
        self._loaded();
      };
      this._h_error = function () {
        self._failed();
      };
      this._h_down = function (e) {
        self._down(e);
      };
      this._h_move = function (e) {
        self._move(e);
      };
      this._h_up = function (e) {
        self._up(e);
      };
      this._h_key = function (e) {
        self._key(e);
      };
      this._h_click = function (e) {
        var button = e.target.closest ? e.target.closest("button") : null;
        if (!button || !root.contains(button) || button.disabled) return;
        if (button.classList.contains("dj-image-cropper__crop-btn")) self._crop();
        else if (button.classList.contains("dj-image-cropper__reset-btn")) self._reset(true);
      };
      this._h_resize = function () {
        self._place();
      };

      this._img.addEventListener("load", this._h_load);
      this._img.addEventListener("error", this._h_error);
      this._wrap.addEventListener("pointerdown", this._h_down);
      this._wrap.addEventListener("pointermove", this._h_move);
      this._wrap.addEventListener("pointerup", this._h_up);
      this._wrap.addEventListener("pointercancel", this._h_up);
      this._wrap.addEventListener("lostpointercapture", this._h_up);
      this._sel.addEventListener("keydown", this._h_key);
      root.addEventListener("click", this._h_click);
      window.addEventListener("resize", this._h_resize);
      if (typeof ResizeObserver === "function") {
        this._ro = new ResizeObserver(this._h_resize);
        this._ro.observe(this._img);
      }
      this._claim();
      if (this._img.complete && this._img.naturalWidth > 0) this._loaded();
      else if (this._img.complete && this._img.getAttribute("src")) this._failed();
      this._sync();
    },

    _unbind: function () {
      if (this._img && this._h_load) {
        this._img.removeEventListener("load", this._h_load);
        this._img.removeEventListener("error", this._h_error);
      }
      if (this._wrap && this._h_down) {
        if (this._drag && this._wrap.releasePointerCapture) {
          try {
            this._wrap.releasePointerCapture(this._drag.id);
          } catch (_e) {
            // The pointer is already gone.
          }
        }
        this._wrap.removeEventListener("pointerdown", this._h_down);
        this._wrap.removeEventListener("pointermove", this._h_move);
        this._wrap.removeEventListener("pointerup", this._h_up);
        this._wrap.removeEventListener("pointercancel", this._h_up);
        this._wrap.removeEventListener("lostpointercapture", this._h_up);
      }
      if (this._sel && this._h_key) this._sel.removeEventListener("keydown", this._h_key);
      if (this._root && this._h_click) this._root.removeEventListener("click", this._h_click);
      if (this._h_resize) window.removeEventListener("resize", this._h_resize);
      if (this._ro) this._ro.disconnect();
      this._ro = null;
      this._h_load = this._h_error = this._h_down = this._h_move = this._h_up = null;
      this._h_key = this._h_click = this._h_resize = null;
      this._drag = null;
      this._box = null;
      this._start = null;
      this._ready = false;
      this._img = this._wrap = this._sel = this._root = null;
    },

    // The selection is client-owned: its style is set here and must survive a morph.
    _claim: function () {
      if (this._sel) this._sel.setAttribute("dj-update", "ignore");
    },

    _disabled: function () {
      return !!this._root && this._root.classList.contains("dj-image-cropper--disabled");
    },

    _say: function (message) {
      var status = this._find("status");
      if (!status || !message) return;
      // Identical text twice in a row is not announced again by most screen readers.
      status.textContent = status.textContent === message ? message + "\u00a0" : message;
    },

    _describe: function () {
      var p = toPixels(this._box, this._nw, this._nh);
      return "Crop area " + p.x + ", " + p.y + ", " + p.width + " by " + p.height + " pixels of " + this._nw + " by " + this._nh;
    },

    // --- the image ------------------------------------------------------------------------

    _loaded: function () {
      if (!this._img) return;
      this._nw = this._img.naturalWidth;
      this._nh = this._img.naturalHeight;
      if (!(this._nw > 0 && this._nh > 0)) {
        this._failed();
        return;
      }
      this._ready = true;
      this._start = initialBox(this._nw, this._nh, this._ratio, this._minW, this._minH);
      this._box = { x: this._start.x, y: this._start.y, w: this._start.w, h: this._start.h };
      this._place();
      this._sync();
      this._say(this._describe());
    },

    _failed: function () {
      this._ready = false;
      this._box = null;
      this._sync();
      this._say("The image could not be loaded");
    },

    // The box on screen: natural pixels times the image's displayed scale.
    _scale: function () {
      var shown = this._img.clientWidth;
      return shown > 0 && this._nw > 0 ? shown / this._nw : 0;
    },

    _place: function () {
      if (!this._ready || !this._box || !this._sel) return;
      var k = this._scale();
      if (!k) return;
      var s = this._sel.style;
      s.left = this._box.x * k + "px";
      s.top = this._box.y * k + "px";
      s.width = this._box.w * k + "px";
      s.height = this._box.h * k + "px";
    },

    _sync: function () {
      var off = !this._ready || this._disabled();
      if (this._sel) {
        this._sel.hidden = !this._ready;
        if (this._ready) this._place();
      }
      var crop = this._find("crop-btn");
      var reset = this._find("reset-btn");
      if (crop) crop.disabled = off;
      if (reset) reset.disabled = off;
    },

    _reset: function (announce) {
      if (!this._ready) return;
      this._start = initialBox(this._nw, this._nh, this._ratio, this._minW, this._minH);
      this._box = { x: this._start.x, y: this._start.y, w: this._start.w, h: this._start.h };
      this._place();
      if (announce) this._say("Reset. " + this._describe());
    },

    _crop: function () {
      if (!this._ready || this._disabled()) return;
      var pixels = toPixels(this._box, this._nw, this._nh);
      this._say("Crop sent: " + pixels.x + ", " + pixels.y + ", " + pixels.width + " by " + pixels.height + " pixels");
      var eventName = this._root.getAttribute("data-crop-event");
      if (eventName) send(this, this._root, eventName, pixels);
    },

    // --- pointer ----------------------------------------------------------------------------

    _natural: function (e) {
      var rect = this._img.getBoundingClientRect();
      var k = this._scale();
      return k ? [(e.clientX - rect.left) / k, (e.clientY - rect.top) / k] : [0, 0];
    },

    _down: function (e) {
      if (!this._ready || this._disabled() || this._drag) return;
      if (!e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
      var handle = e.target.closest ? e.target.closest("[data-handle]") : null;
      var inside = this._sel.contains(e.target);
      e.preventDefault();
      if (this._wrap.setPointerCapture) {
        try {
          this._wrap.setPointerCapture(e.pointerId);
        } catch (_e) {
          // A pointer that is already gone cannot be captured.
        }
      }
      var at = this._natural(e);
      this._drag = {
        id: e.pointerId,
        mode: handle ? "resize" : inside ? "move" : "new",
        handle: handle ? handle.getAttribute("data-handle") : "",
        from: at,
        startBox: { x: this._box.x, y: this._box.y, w: this._box.w, h: this._box.h },
        moved: false,
      };
      if (inside || handle) this._sel.focus();
    },

    _move: function (e) {
      var d = this._drag;
      if (!d || e.pointerId !== d.id) return;
      var at = this._natural(e);
      var dx = at[0] - d.from[0];
      var dy = at[1] - d.from[1];
      var k = this._scale() || 1;
      if (!d.moved && Math.abs(dx) * k < DRAG_START && Math.abs(dy) * k < DRAG_START) return;
      d.moved = true;
      if (d.mode === "move") this._box = moveBox(d.startBox, dx, dy, this._nw, this._nh);
      else if (d.mode === "resize") this._box = resizeBox(d.startBox, d.handle, dx, dy, this._ratio, this._nw, this._nh, this._minW, this._minH);
      else this._box = newBox(d.from[0], d.from[1], at[0], at[1], this._ratio, this._nw, this._nh, this._minW, this._minH);
      this._place();
    },

    _up: function (e) {
      var d = this._drag;
      if (!d || e.pointerId !== d.id) return;
      this._drag = null;
      if (this._wrap && this._wrap.releasePointerCapture) {
        try {
          this._wrap.releasePointerCapture(e.pointerId);
        } catch (_e) {
          // Already released.
        }
      }
      if (d.moved) this._say(this._describe());
    },

    // --- keyboard ---------------------------------------------------------------------------------

    _key: function (e) {
      if (!this._ready || this._disabled() || e.ctrlKey || e.metaKey) return;
      var k = this._scale() || 1;
      var step = Math.max(1, Math.round(NUDGE / k)) * (e.shiftKey ? NUDGE_LARGE : 1);
      var key = e.key;
      var dx = key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0;
      var dy = key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0;
      if (dx || dy) {
        if (e.altKey) {
          // Alt: the arrows grow and shrink the box (right/down grow).
          if (dx) this._box = resizeBox(this._box, "e", dx, 0, this._ratio, this._nw, this._nh, this._minW, this._minH);
          else this._box = resizeBox(this._box, "s", 0, dy, this._ratio, this._nw, this._nh, this._minW, this._minH);
        } else {
          this._box = moveBox(this._box, dx, dy, this._nw, this._nh);
        }
        this._place();
        this._say(this._describe());
      } else if (key === "Enter") {
        this._crop();
      } else if (key === "Escape") {
        this._reset(true);
      } else {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.ImageCropper && !window.DjustHooks.ImageCropper) {
    window.DjustHooks.ImageCropper = imageCropper;
  }
})();
