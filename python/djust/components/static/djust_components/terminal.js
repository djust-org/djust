/**
 * Terminal — streaming, ANSI colour and following for the Terminal component
 * (dj-hook="Terminal").
 *
 * - Stream: with data-stream-event (the component's ``stream_event``) the hook
 *   listens for that server push_event and appends the lines it carries. The
 *   payload is ``{"lines": [...]}``, ``{"line": "..."}``, a list or a string::
 *
 *       self.push_event("term_out", {"lines": ["\u001b[32mok\u001b[0m build"]})
 *
 *   Lines are rendered like the server renders them: ANSI SGR sequences
 *   (reset 0, bold 1, foreground 30-37 and 90-97; other codes are ignored)
 *   become styled spans, built with the DOM API so line text is only ever
 *   text. Anything that is not such a sequence (cursor movement, an
 *   unterminated escape) stays visible as text, as it does on the server.
 *   Styled runs are flat spans and the work per line is bounded (parameters
 *   per sequence are capped).
 *   It honours data-line-numbers and data-max-lines (the component's
 *   ``show_line_numbers`` and ``max_lines``; oldest lines drop off). Streamed
 *   lines live on the page: a view should either stream them or re-render
 *   ``output``, not both for the same terminal.
 * - Follow: the terminal stays pinned to the newest line until the reader
 *   scrolls up; scrolling back to the bottom resumes it (the same rules as
 *   LogViewer: the reader's position is tracked from scroll events and the
 *   follow is one scroll per frame).
 * - Accessibility: the output is a role=log region named by the title, kept
 *   aria-live=off so a build log does not flood a screen reader; the scrolling
 *   body is focusable for keyboard scrolling.
 *
 * An app's own ``Terminal`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var MAX_PARAMS = 16;
  // The colours the server maps.
  var COLORS = {
    30: "#000", 31: "#e74c3c", 32: "#2ecc71", 33: "#f1c40f", 34: "#3498db",
    35: "#9b59b6", 36: "#1abc9c", 37: "#ecf0f1", 90: "#7f8c8d", 91: "#ff6b6b",
    92: "#55efc4", 93: "#ffeaa7", 94: "#74b9ff", 95: "#a29bfe", 96: "#81ecec", 97: "#fff",
  };
  // A line is scanned in one linear pass: the pattern cannot backtrack
  // (digits and semicolons then an m, anchored at an ESC).
  var SGR = /\u001b\[([0-9;]*)m/g;
  var TOLERANCE = 1;

  function atBottom(el) {
    return el.scrollTop + el.clientHeight >= el.scrollHeight - TOLERANCE;
  }

  function scroller(root) {
    return root.querySelector(".dj-terminal__body") || root;
  }

  function linesFrom(payload) {
    if (Array.isArray(payload)) return payload;
    if (typeof payload === "string") return [payload];
    if (payload && Array.isArray(payload.lines)) return payload.lines;
    if (payload && payload.line !== undefined && payload.line !== null) return [payload.line];
    return [];
  }

  function run(state, content) {
    if (!content) return null;
    if (!state.bold && !state.color) return document.createTextNode(content);
    var el = document.createElement("span");
    if (state.bold) el.style.fontWeight = "bold";
    if (state.color) el.style.color = state.color;
    el.textContent = content;
    return el;
  }

  // ANSI text -> a fragment of text nodes and flat styled spans.
  function render(textValue) {
    var out = document.createDocumentFragment();
    var state = { bold: false, color: "" };
    var last = 0;
    var m;
    SGR.lastIndex = 0;
    while ((m = SGR.exec(textValue)) !== null) {
      var before = run(state, textValue.slice(last, m.index));
      if (before) out.appendChild(before);
      last = m.index + m[0].length;
      var codes = m[1].split(";", MAX_PARAMS);
      for (var i = 0; i < codes.length; i++) {
        var code = codes[i];
        if (code === "" || code === "0") {
          state.bold = false;
          state.color = "";
        } else if (code === "1") {
          state.bold = true;
        } else if (Object.prototype.hasOwnProperty.call(COLORS, code)) {
          state.color = COLORS[code];
        }
      }
    }
    var rest = run(state, textValue.slice(last));
    if (rest) out.appendChild(rest);
    return out;
  }

  var terminal = {
    mounted: function () {
      var self = this;
      this._pinned = true;
      this._bindScroll();
      this._enhance();
      this._follow();
      this._lastTop = scroller(this.el).scrollTop;
      var eventName = this.el.getAttribute("data-stream-event");
      if (eventName) {
        this.handleEvent(eventName, function (payload) {
          self._append(linesFrom(payload));
        });
      }
    },

    updated: function () {
      if (this._scrollEl !== this.el) {
        this._unbindScroll();
        this._bindScroll();
      }
      // A re-render redraws the rendered lines; count again from them.
      this._count = undefined;
      this._enhance();
      if (this._pinned !== false) this._follow();
      this._lastTop = scroller(this.el).scrollTop;
    },

    destroyed: function () {
      this._unbindScroll();
      if (this._raf && window.cancelAnimationFrame) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
    },

    // Role, name and a focusable scroll body; idempotent, re-applied from
    // updated() because a patch can drop what the hook added.
    _enhance: function () {
      var root = this.el;
      if (!root.hasAttribute("role")) root.setAttribute("role", "log");
      if (!root.hasAttribute("aria-live")) root.setAttribute("aria-live", "off");
      if (!root.hasAttribute("aria-label") && !root.hasAttribute("aria-labelledby")) {
        var title = root.querySelector(".dj-terminal__title");
        root.setAttribute("aria-label", (title && title.textContent.trim()) || "Terminal output");
      }
      var body = scroller(root);
      if (!body.hasAttribute("tabindex")) body.setAttribute("tabindex", "0");
      // Trimming old lines must not make the browser nudge scrollTop (which
      // would read as the reader scrolling up); the hook follows the bottom itself.
      body.style.overflowAnchor = "none";
    },

    _bindScroll: function () {
      var self = this;
      var root = this.el;
      this._scrollEl = root;
      // Scroll events arrive a frame late, after more lines may have been
      // appended, so only the reader moving UP leaves the bottom.
      this._onScroll = function (e) {
        if (e.target !== scroller(root)) return;
        var top = e.target.scrollTop;
        if (atBottom(e.target)) self._pinned = true;
        else if (top < (self._lastTop === undefined ? top : self._lastTop) - 1) self._pinned = false;
        self._lastTop = top;
      };
      root.addEventListener("scroll", this._onScroll, true);
    },

    _unbindScroll: function () {
      if (this._scrollEl && this._onScroll) {
        this._scrollEl.removeEventListener("scroll", this._onScroll, true);
      }
      this._scrollEl = null;
      this._onScroll = null;
    },

    // Trimming shrinks the content, and the browser clamps scrollTop to match:
    // a decrease nobody asked for, whose (late) scroll event must not read as the
    // reader scrolling up. Move the remembered position to the new maximum (where
    // the clamp lands) instead of forgetting it, so a reader who really scrolls up
    // afterwards is still recognised. Once per frame, never per append: reading
    // scrollHeight forces a layout.
    _settle: function () {
      if (!this._trimmed) return;
      this._trimmed = false;
      var body = scroller(this.el);
      var maxTop = body.scrollHeight - body.clientHeight;
      if (this._lastTop !== undefined && this._lastTop > maxTop) this._lastTop = maxTop;
    },

    _follow: function () {
      var body = scroller(this.el);
      body.scrollTop = body.scrollHeight;
    },

    // One scroll per frame, and not if the reader scrolled up meanwhile.
    _followSoon: function () {
      var self = this;
      if (!window.requestAnimationFrame) {
        this._settle();
        this._follow();
        return;
      }
      if (this._raf) return;
      this._raf = window.requestAnimationFrame(function () {
        self._raf = 0;
        self._settle();
        if (self._pinned !== false) self._follow();
      });
    },

    // O(lines pushed), not O(lines held).
    _append: function (lines) {
      if (!lines.length) return;
      var root = this.el;
      var body = scroller(root);
      var pinned = this._pinned !== false;
      var showNumbers = root.getAttribute("data-line-numbers") === "true";
      var max = parseInt(root.getAttribute("data-max-lines"), 10) || 0;
      if (this._count === undefined) {
        var last = body.lastElementChild;
        var num = last ? last.querySelector(".dj-terminal__line-num") : null;
        this._rows = body.childElementCount;
        this._count = (num && parseInt(num.textContent, 10)) || this._rows;
      }

      var added = document.createDocumentFragment();
      for (var i = 0; i < lines.length; i++) {
        var raw = lines[i];
        this._count += 1;
        var row = document.createElement("div");
        row.className = "dj-terminal__line";
        if (showNumbers) {
          var n = document.createElement("span");
          n.className = "dj-terminal__line-num";
          n.textContent = String(this._count);
          row.appendChild(n);
        }
        var t = document.createElement("span");
        t.className = "dj-terminal__text";
        t.appendChild(render(raw === undefined || raw === null ? "" : String(raw)));
        row.appendChild(t);
        added.appendChild(row);
        this._rows += 1;
      }
      body.appendChild(added);

      if (max > 0 && this._rows > max) {
        while (this._rows > max && body.firstElementChild) {
          body.removeChild(body.firstElementChild);
          this._rows -= 1;
        }
        this._trimmed = true; // _settle() accounts for it once per frame
      }
      if (pinned) this._followSoon();
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.Terminal && !window.DjustHooks.Terminal) {
    window.DjustHooks.Terminal = terminal;
  }
})();
