/**
 * LogViewer — following and streaming for the LogViewer component
 * (dj-hook="LogViewer").
 *
 * - Follow: with data-auto-scroll (the component's ``auto_scroll``) the log
 *   stays pinned to its newest line as lines arrive, whether the server
 *   re-renders with more ``lines`` or streams them. If the reader scrolls up
 *   to read, it stops following; scrolling back to the bottom resumes it.
 * - Stream: with data-stream-event (the component's ``stream_event``) the
 *   hook listens for that server push_event and appends the lines it carries,
 *   coloured by level like the rendered ones. The payload is
 *   ``{"lines": [...]}``, ``{"line": "..."}``, a list or a single string::
 *
 *       self.push_event("new_logs", {"lines": ["12:00 ERROR timeout"]})
 *
 *   It honours data-max-lines (oldest lines drop off), data-line-numbers and
 *   data-filter-level. Streamed lines live on the client: a view should
 *   either stream them or re-render ``lines``, not both for the same log.
 * - Keyboard: the scrolling body is focusable, so the arrow, Page and Home/End
 *   keys scroll it.
 *
 * An app's own ``LogViewer`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var LEVEL_RE = /\b(INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL|CRITICAL)\b/i;
  // 1px of slack for sub-pixel scroll math.
  var TOLERANCE = 1;

  function detectLevel(line) {
    var m = LEVEL_RE.exec(line);
    if (!m) return "";
    var level = m[1].toUpperCase();
    if (level === "WARN" || level === "WARNING") return "warn";
    if (level === "ERROR" || level === "FATAL" || level === "CRITICAL") return "error";
    if (level === "DEBUG" || level === "TRACE") return "debug";
    return "info";
  }

  function scroller(root) {
    return root.querySelector(".dj-log-viewer__body") || root;
  }

  function atBottom(el) {
    return el.scrollTop + el.clientHeight >= el.scrollHeight - TOLERANCE;
  }

  function linesFrom(payload) {
    if (Array.isArray(payload)) return payload;
    if (typeof payload === "string") return [payload];
    if (payload && Array.isArray(payload.lines)) return payload.lines;
    if (payload && payload.line !== undefined && payload.line !== null) return [payload.line];
    return [];
  }

  var logViewer = {
    mounted: function () {
      var self = this;
      this._pinned = true;
      this._enhance();
      this._follow();
      var eventName = this.el.getAttribute("data-stream-event");
      if (eventName) {
        this.handleEvent(eventName, function (payload) {
          self._append(linesFrom(payload));
        });
      }
    },

    // Remember whether the reader is at the newest line before the patch.
    beforeUpdate: function () {
      this._pinned = atBottom(scroller(this.el));
    },

    updated: function () {
      // A re-render redraws the rendered lines; count again from them.
      this._count = undefined;
      this._enhance();
      if (this._pinned) this._follow();
    },

    _autoScroll: function () {
      return this.el.getAttribute("data-auto-scroll") === "true";
    },

    _follow: function () {
      if (!this._autoScroll()) return;
      var body = scroller(this.el);
      body.scrollTop = body.scrollHeight;
    },

    // A scrollable region must be reachable from the keyboard. Idempotent,
    // and run again from updated() because a patch may drop the attribute.
    _enhance: function () {
      var body = scroller(this.el);
      if (!body.hasAttribute("tabindex")) body.setAttribute("tabindex", "0");
    },

    _append: function (lines) {
      if (!lines.length) return;
      var root = this.el;
      var body = scroller(root);
      var pinned = atBottom(body);
      var filter = (root.getAttribute("data-filter-level") || "").toLowerCase();
      var showNumbers = root.getAttribute("data-line-numbers") === "true";
      var max = parseInt(root.getAttribute("data-max-lines"), 10) || 0;
      var rendered = body.querySelectorAll(".dj-log-viewer__line");
      if (this._count === undefined) {
        var last = rendered.length ? rendered[rendered.length - 1] : null;
        var num = last ? last.querySelector(".dj-log-viewer__num") : null;
        this._count = (num && parseInt(num.textContent, 10)) || rendered.length;
      }

      for (var i = 0; i < lines.length; i++) {
        var text = String(lines[i]);
        var level = detectLevel(text);
        this._count += 1;
        if (filter && level !== filter) continue;

        var row = document.createElement("div");
        row.className = "dj-log-viewer__line" + (level ? " dj-log-viewer__line--" + level : "");
        if (showNumbers) {
          var n = document.createElement("span");
          n.className = "dj-log-viewer__num";
          n.textContent = String(this._count);
          row.appendChild(n);
        }
        var t = document.createElement("span");
        t.className = "dj-log-viewer__text";
        t.textContent = text;
        row.appendChild(t);
        body.appendChild(row);
      }

      if (max > 0) {
        var all = body.querySelectorAll(".dj-log-viewer__line");
        for (var j = 0; j < all.length - max; j++) body.removeChild(all[j]);
      }
      if (pinned) this._follow();
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.LogViewer && !window.DjustHooks.LogViewer) {
    window.DjustHooks.LogViewer = logViewer;
  }
})();
