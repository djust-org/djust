/**
 * SignaturePad — the interaction layer of the SignaturePad component
 * (dj-hook="SignaturePad"): a native canvas drawn with pointer events, with
 * Clear, Undo, Save and a typed-name alternative.
 *
 * - drawing: pointer events (mouse, touch, pen) with pointer capture, so a
 *   stroke that leaves the canvas still ends cleanly; pen pressure varies the
 *   line width; a tap leaves a dot; the page does not scroll while drawing on a
 *   touch screen (the canvas has touch-action: none). The strokes are kept as
 *   points in the component's own units (its width x height), so Undo removes
 *   the last stroke and the drawing survives a resize or a change of pixel ratio.
 * - sharp and responsive: the canvas follows its container's width, its backing
 *   store is the displayed size times the device pixel ratio (up to 3), and it
 *   is redrawn from the strokes when either changes. The exported PNG is always
 *   rendered at the component's own size, never at the screen's.
 * - Save: nothing is drawn -> nothing is sent and the status says so. Otherwise
 *   {signature: "data:image/png;base64,..."} goes to data-save-event (and the
 *   same value into the hidden form field). The value is never longer than
 *   data-max-bytes (default 200 KB): the signature is re-rendered at a lower
 *   resolution until it fits, and refused when it cannot. When the server
 *   answers "Message too large" (the default WebSocket limit is 64 KiB) it is
 *   sent once more at a size that fits. The value is the browser's claim: the
 *   server must validate it (djust.components.signature.decode_signature_data_url).
 * - keyboard alternative: "Type instead" shows a text field; the signature is
 *   then the typed name drawn on the canvas in a script font.
 * - Clear, Undo, the mode button and Save are real buttons, enabled only when
 *   they can do something; every result and refusal is announced in the
 *   component's polite status region. Nothing is created by this script: the
 *   markup carries every control, so a patch cannot remove one.
 *
 * An app's own ``SignaturePad`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when neither
 * registry already holds one.
 */
(function () {
  "use strict";

  var DEFAULT_MAX_BYTES = 200 * 1024;
  // What a WebSocket frame can carry at the default max_message_size (65536),
  // leaving room for the event's JSON envelope.
  var FRAME_TARGET = 48000;
  var MAX_DPR = 3;
  var MAX_POINTS = 20000;
  var MIN_STEP = 0.6;
  var EXPORT_SCALES = [2, 1.5, 1, 0.75, 0.5];
  var FONT = '"Snell Roundhand", "Segoe Script", "Brush Script MT", "Apple Chancery", "Lucida Handwriting", cursive';

  function num(value, fallback) {
    var n = parseInt(value, 10);
    return isFinite(n) ? n : fallback;
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

  function mid(a, b) {
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  }

  var signaturePad = {
    mounted: function () {
      this._bind();
    },

    // A patch can reset what the hook changed on server-rendered elements
    // (button states, the typed field's visibility, the hidden value): they are
    // derived from the hook's state again.
    updated: function () {
      if (this._root !== this.el) {
        this._unbind();
        this._bind();
        return;
      }
      this._claim();
      this._sync();
    },

    destroyed: function () {
      this._unbind();
    },

    // --- setup ---------------------------------------------------------------

    _bind: function () {
      var self = this;
      var root = this.el;
      var canvas = root.querySelector(".dj-signature-pad__canvas");
      this._root = root;
      this._canvas = canvas;
      this._strokes = [];
      this._current = null;
      this._points = 0;
      this._pointer = null;
      this._mode = "draw";
      this._typed = "";
      this._dataUrl = "";
      this._awaiting = null;
      this._raf = 0;
      if (!canvas || typeof canvas.getContext !== "function") return;

      // The canvas attributes are the signature's own size, read once: the
      // backing store is resized below.
      this._w = Math.max(1, num(canvas.getAttribute("width"), 400));
      this._h = Math.max(1, num(canvas.getAttribute("height"), 200));
      this._color = root.getAttribute("data-pen-color") || "#000000";
      this._pen = Math.max(1, Math.min(40, num(root.getAttribute("data-pen-width"), 2)));
      this._ctx = canvas.getContext("2d");

      this._h_down = function (e) {
        self._down(e);
      };
      this._h_move = function (e) {
        self._move(e);
      };
      this._h_up = function (e) {
        self._up(e);
      };
      this._h_menu = function (e) {
        e.preventDefault();
      };
      this._h_click = function (e) {
        var button = e.target.closest ? e.target.closest("button") : null;
        if (button && root.contains(button)) self._press(button);
      };
      this._h_type = function (e) {
        if (!e.target.classList || !e.target.classList.contains("dj-signature-pad__typed-input")) return;
        self._typed = e.target.value;
        self._redraw();
        self._sync();
      };
      this._h_key = function (e) {
        if (e.key === "Enter" && e.target.classList.contains("dj-signature-pad__typed-input")) {
          e.preventDefault();
          self._save();
        }
      };
      this._h_resize = function () {
        self._layoutSoon();
      };
      this._h_error = function (e) {
        self._serverError((e.detail && e.detail.error) || "");
      };

      canvas.addEventListener("pointerdown", this._h_down);
      canvas.addEventListener("pointermove", this._h_move);
      canvas.addEventListener("pointerup", this._h_up);
      canvas.addEventListener("pointercancel", this._h_up);
      canvas.addEventListener("lostpointercapture", this._h_up);
      canvas.addEventListener("contextmenu", this._h_menu);
      root.addEventListener("click", this._h_click);
      root.addEventListener("input", this._h_type);
      root.addEventListener("keydown", this._h_key);
      window.addEventListener("resize", this._h_resize);
      window.addEventListener("djust:error", this._h_error);
      if (typeof ResizeObserver === "function") {
        this._ro = new ResizeObserver(this._h_resize);
        this._ro.observe(root);
      }
      this._claim();
      this._layout();
      this._sync();
    },

    _unbind: function () {
      var canvas = this._canvas;
      var root = this._root;
      if (this._raf) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
      if (this._ro) this._ro.disconnect();
      this._ro = null;
      if (canvas && this._h_down) {
        if (this._pointer !== null && canvas.releasePointerCapture) {
          try {
            canvas.releasePointerCapture(this._pointer);
          } catch (_e) {
            // The pointer is already gone.
          }
        }
        canvas.removeEventListener("pointerdown", this._h_down);
        canvas.removeEventListener("pointermove", this._h_move);
        canvas.removeEventListener("pointerup", this._h_up);
        canvas.removeEventListener("pointercancel", this._h_up);
        canvas.removeEventListener("lostpointercapture", this._h_up);
        canvas.removeEventListener("contextmenu", this._h_menu);
      }
      if (root && this._h_click) {
        root.removeEventListener("click", this._h_click);
        root.removeEventListener("input", this._h_type);
        root.removeEventListener("keydown", this._h_key);
      }
      if (this._h_resize) window.removeEventListener("resize", this._h_resize);
      if (this._h_error) window.removeEventListener("djust:error", this._h_error);
      this._h_down = this._h_move = this._h_up = this._h_menu = null;
      this._h_click = this._h_type = this._h_key = this._h_resize = this._h_error = null;
      this._strokes = [];
      this._current = null;
      this._pointer = null;
      this._awaiting = null;
      this._canvas = null;
      this._ctx = null;
      this._root = null;
    },

    _q: function (cls) {
      return this._root ? this._root.querySelector(".dj-signature-pad__" + cls) : null;
    },

    // The canvas is client-owned: a full morph must not reset its size (which
    // would clear it). Set here, so it needs no id.
    _claim: function () {
      if (this._canvas) this._canvas.setAttribute("dj-update", "ignore");
    },

    _disabled: function () {
      return !!this._root && this._root.classList.contains("dj-signature-pad--disabled");
    },

    _empty: function () {
      return this._mode === "type" ? !this._typed.trim() : this._strokes.length === 0;
    },

    _say: function (message) {
      var status = this._q("status");
      if (!status || !message) return;
      // Identical text twice in a row is not announced again by most screen readers.
      status.textContent = status.textContent === message ? message + "\u00a0" : message;
    },

    // Every control's state, from the hook's state.
    _sync: function () {
      var disabled = this._disabled();
      var empty = this._empty();
      var typing = this._mode === "type";
      var set = function (node, off) {
        if (node) node.disabled = off;
      };
      set(this._q("clear-btn"), disabled || (empty && !this._dataUrl));
      set(this._q("undo-btn"), disabled || typing || this._strokes.length === 0);
      set(this._q("save-btn"), disabled || empty);
      var toggle = this._q("mode-btn");
      if (toggle) {
        toggle.disabled = disabled;
        toggle.setAttribute("aria-pressed", typing ? "true" : "false");
        toggle.textContent = typing ? "Draw instead" : "Type instead";
      }
      var typed = this._q("typed");
      if (typed) typed.hidden = !typing;
      var field = this._q("typed-input");
      if (field) {
        field.disabled = disabled;
        if (field.value !== this._typed) field.value = this._typed;
      }
      if (this._canvas) {
        this._canvas.classList.toggle("dj-signature-pad__canvas--typing", typing);
        if (disabled) this._canvas.setAttribute("aria-disabled", "true");
        else this._canvas.removeAttribute("aria-disabled");
      }
      var hidden = this._q("value");
      if (hidden && hidden.value !== this._dataUrl) hidden.value = this._dataUrl;
    },

    // --- size and pixel ratio -------------------------------------------------------

    _layoutSoon: function () {
      var self = this;
      if (this._raf) return;
      this._raf = window.requestAnimationFrame(function () {
        self._raf = 0;
        if (self._canvas) self._layout();
      });
    },

    _layout: function () {
      var canvas = this._canvas;
      var avail = this._root.clientWidth;
      var cssW = avail > 0 ? Math.min(this._w, avail) : this._w;
      var cssH = Math.max(1, Math.round((this._h * cssW) / this._w));
      var dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      var bw = Math.max(1, Math.round(cssW * dpr));
      var bh = Math.max(1, Math.round(cssH * dpr));
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
      if (canvas.width !== bw || canvas.height !== bh) {
        // Assigning a size clears the canvas: the drawing is redrawn from the strokes.
        canvas.width = bw;
        canvas.height = bh;
      }
      this._redraw();
    },

    // --- drawing --------------------------------------------------------------------------

    _point: function (e) {
      var rect = this._canvas.getBoundingClientRect();
      var rw = rect.width || this._w;
      var rh = rect.height || this._h;
      var x = Math.max(0, Math.min(this._w, ((e.clientX - rect.left) * this._w) / rw));
      var y = Math.max(0, Math.min(this._h, ((e.clientY - rect.top) * this._h) / rh));
      var pressure = e.pointerType === "pen" && e.pressure > 0 ? e.pressure : 0.5;
      return [x, y, pressure];
    },

    _lineWidth: function (stroke, pressure) {
      return stroke.pen ? this._pen * (0.5 + pressure) : this._pen;
    },

    _style: function (ctx) {
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = this._color;
      ctx.fillStyle = this._color;
    },

    // One quadratic segment through p1 between the midpoints of its neighbours.
    _segment: function (ctx, stroke, a, b, c) {
      var from = mid(a, b);
      var to = mid(b, c);
      ctx.lineWidth = this._lineWidth(stroke, b[2]);
      ctx.beginPath();
      ctx.moveTo(from[0], from[1]);
      ctx.quadraticCurveTo(b[0], b[1], to[0], to[1]);
      ctx.stroke();
    },

    _dot: function (ctx, stroke, p) {
      ctx.beginPath();
      ctx.arc(p[0], p[1], this._lineWidth(stroke, p[2]) / 2, 0, Math.PI * 2);
      ctx.fill();
    },

    _drawStroke: function (ctx, stroke) {
      var pts = stroke.pts;
      if (pts.length === 1) {
        this._dot(ctx, stroke, pts[0]);
        return;
      }
      if (pts.length === 2) {
        ctx.lineWidth = this._lineWidth(stroke, pts[1][2]);
        ctx.beginPath();
        ctx.moveTo(pts[0][0], pts[0][1]);
        ctx.lineTo(pts[1][0], pts[1][1]);
        ctx.stroke();
        return;
      }
      // The first and last half-segments are straight so the stroke reaches its ends.
      ctx.lineWidth = this._lineWidth(stroke, pts[0][2]);
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      var first = mid(pts[0], pts[1]);
      ctx.lineTo(first[0], first[1]);
      ctx.stroke();
      for (var i = 1; i < pts.length - 1; i++) this._segment(ctx, stroke, pts[i - 1], pts[i], pts[i + 1]);
      var last = pts[pts.length - 1];
      var before = mid(pts[pts.length - 2], last);
      ctx.lineWidth = this._lineWidth(stroke, last[2]);
      ctx.beginPath();
      ctx.moveTo(before[0], before[1]);
      ctx.lineTo(last[0], last[1]);
      ctx.stroke();
    },

    _typedText: function (ctx) {
      var text = this._typed.trim();
      if (!text) return;
      var size = Math.round(this._h * 0.5);
      ctx.font = "italic " + size + "px " + FONT;
      while (size > 8 && ctx.measureText(text).width > this._w * 0.9) {
        size -= 2;
        ctx.font = "italic " + size + "px " + FONT;
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(text, this._w / 2, this._h / 2);
    },

    // The whole signature, in the component's own units, onto ``ctx``.
    _paint: function (ctx, scale) {
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, this._w, this._h);
      this._style(ctx);
      if (this._mode === "type") {
        this._typedText(ctx);
        return;
      }
      for (var i = 0; i < this._strokes.length; i++) this._drawStroke(ctx, this._strokes[i]);
    },

    _redraw: function () {
      if (!this._ctx) return;
      this._paint(this._ctx, this._canvas.width / this._w);
    },

    _down: function (e) {
      if (this._disabled() || this._mode !== "draw" || this._pointer !== null) return;
      if (!e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
      if (this._points >= MAX_POINTS) {
        this._say("This signature has reached its limit; use Undo or Clear");
        return;
      }
      e.preventDefault();
      this._pointer = e.pointerId;
      if (this._canvas.setPointerCapture) {
        try {
          this._canvas.setPointerCapture(e.pointerId);
        } catch (_e) {
          // A pointer that is already gone cannot be captured.
        }
      }
      var p = this._point(e);
      this._current = { pts: [p], pen: e.pointerType === "pen" };
      this._strokes.push(this._current);
      this._points += 1;
      this._dataUrl = "";
      var ctx = this._ctx;
      var k = this._canvas.width / this._w;
      ctx.setTransform(k, 0, 0, k, 0, 0);
      this._style(ctx);
      this._dot(ctx, this._current, p);
      this._sync();
    },

    _move: function (e) {
      if (e.pointerId !== this._pointer || !this._current) return;
      var events = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : null;
      if (!events || !events.length) events = [e];
      var stroke = this._current;
      var ctx = this._ctx;
      for (var i = 0; i < events.length; i++) {
        if (this._points >= MAX_POINTS) break;
        var p = this._point(events[i]);
        var prev = stroke.pts[stroke.pts.length - 1];
        if (Math.abs(p[0] - prev[0]) < MIN_STEP && Math.abs(p[1] - prev[1]) < MIN_STEP) continue;
        stroke.pts.push(p);
        this._points += 1;
        var n = stroke.pts.length;
        if (n === 2) {
          ctx.lineWidth = this._lineWidth(stroke, p[2]);
          ctx.beginPath();
          ctx.moveTo(prev[0], prev[1]);
          ctx.lineTo(p[0], p[1]);
          ctx.stroke();
        } else {
          this._segment(ctx, stroke, stroke.pts[n - 3], stroke.pts[n - 2], p);
        }
      }
    },

    // The live drawing stops at the midpoint of the last two points; the stroke
    // still has to reach its last point, as a redraw would.
    _finish: function (stroke) {
      var pts = stroke.pts;
      if (pts.length < 3) return;
      var last = pts[pts.length - 1];
      var from = mid(pts[pts.length - 2], last);
      var ctx = this._ctx;
      var k = this._canvas.width / this._w;
      ctx.setTransform(k, 0, 0, k, 0, 0);
      this._style(ctx);
      ctx.lineWidth = this._lineWidth(stroke, last[2]);
      ctx.beginPath();
      ctx.moveTo(from[0], from[1]);
      ctx.lineTo(last[0], last[1]);
      ctx.stroke();
    },

    _up: function (e) {
      if (e.pointerId !== this._pointer) return;
      if (this._current) this._finish(this._current);
      var canvas = this._canvas;
      if (canvas && canvas.releasePointerCapture) {
        try {
          canvas.releasePointerCapture(e.pointerId);
        } catch (_e) {
          // Already released.
        }
      }
      this._pointer = null;
      this._current = null;
      this._sync();
    },

    // --- the buttons ------------------------------------------------------------------------

    _press: function (button) {
      var cls = button.className;
      if (button.disabled) return;
      if (cls.indexOf("dj-signature-pad__clear-btn") >= 0) this._clear();
      else if (cls.indexOf("dj-signature-pad__undo-btn") >= 0) this._undo();
      else if (cls.indexOf("dj-signature-pad__mode-btn") >= 0) this._toggleMode();
      else if (cls.indexOf("dj-signature-pad__save-btn") >= 0) this._save();
    },

    _clear: function () {
      if (this._mode === "type") this._typed = "";
      else this._strokes = [];
      this._points = this._strokes.reduce(function (n, s) {
        return n + s.pts.length;
      }, 0);
      this._dataUrl = "";
      this._awaiting = null;
      this._redraw();
      this._sync();
      this._say("Signature cleared");
    },

    _undo: function () {
      var gone = this._strokes.pop();
      if (!gone) return;
      this._points -= gone.pts.length;
      this._dataUrl = "";
      this._redraw();
      this._sync();
      this._say("Last stroke undone");
    },

    _toggleMode: function () {
      this._mode = this._mode === "type" ? "draw" : "type";
      this._dataUrl = "";
      this._redraw();
      this._sync();
      var typing = this._mode === "type";
      this._say(typing ? "Type your name; it becomes your signature" : "Draw your signature");
      var field = this._q("typed-input");
      if (typing && field) field.focus();
    },

    // --- saving -----------------------------------------------------------------------------------

    _cap: function () {
      return Math.max(1024, num(this._root.getAttribute("data-max-bytes"), DEFAULT_MAX_BYTES));
    },

    // The signature as a PNG data URL no longer than ``limit``: the largest
    // scale that fits (never above the screen's own pixel ratio), or "".
    _export: function (limit, below) {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      for (var i = 0; i < EXPORT_SCALES.length; i++) {
        var scale = EXPORT_SCALES[i];
        if (scale > Math.max(dpr, 1)) continue;
        if (below && scale >= below) continue;
        var out = document.createElement("canvas");
        out.width = Math.max(1, Math.round(this._w * scale));
        out.height = Math.max(1, Math.round(this._h * scale));
        this._paint(out.getContext("2d"), out.width / this._w);
        var url = out.toDataURL("image/png");
        if (url.length <= limit) return { url: url, scale: scale };
      }
      return null;
    },

    _save: function () {
      if (this._disabled()) return;
      if (this._empty()) {
        this._say("Nothing to save: draw or type your signature first");
        return;
      }
      var cap = this._cap();
      var made = this._export(cap, 0);
      if (!made) {
        this._say("This signature is too detailed to send (the limit is " + Math.round(cap / 1024) + " KB). Clear it and draw a simpler one");
        return;
      }
      this._dataUrl = made.url;
      this._sync();
      this._awaiting = { size: made.url.length, scale: made.scale, retried: false };
      this._say("Signature saved");
      var eventName = this._root.getAttribute("data-save-event");
      if (eventName) send(this, this._root, eventName, { signature: made.url });
    },

    // "Message too large (N bytes)": the server's frame limit is smaller than
    // the signature. Once, at a size that fits a default frame.
    _serverError: function (message) {
      var wait = this._awaiting;
      var found = /^Message too large \((\d+) bytes\)/.exec(String(message));
      if (!wait || wait.retried || !found) return;
      var bytes = parseInt(found[1], 10);
      // Only the frame this signature made, not some other message's refusal.
      if (bytes < wait.size || bytes > wait.size + 4096) return;
      wait.retried = true;
      var made = this._export(Math.min(this._cap(), FRAME_TARGET), wait.scale);
      if (!made) {
        this._say("The server's message limit (" + Math.round(bytes / 1024) + " KB) is smaller than this signature. Clear it and draw a simpler one, or raise max_message_size");
        return;
      }
      this._dataUrl = made.url;
      this._sync();
      this._say("Signature sent at a smaller size");
      var eventName = this._root.getAttribute("data-save-event");
      if (eventName) send(this, this._root, eventName, { signature: made.url });
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.SignaturePad && !window.DjustHooks.SignaturePad) {
    window.DjustHooks.SignaturePad = signaturePad;
  }
})();
