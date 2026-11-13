/**
 * ActivityFeed — live updates and feed keyboard navigation for the
 * ActivityFeed component (dj-hook="ActivityFeed", rendered with
 * ``stream_event``).
 *
 * - Streaming: the hook listens for the server push_event named by
 *   data-stream-event and puts the new events at the TOP of the feed (newest
 *   first, as the component renders them). The payload is
 *   ``{"events": [...]}``, ``{"event": {...}}``, a list, or a single dict, each
 *   event ``{user, action, target, time, avatar, icon}``, newest first::
 *
 *       self.push_event("activity_update", {"events": [{"user": "Alice",
 *           "action": "commented on", "target": "Issue #42", "time": "now"}]})
 *
 *   Every field is shown as text, and an avatar only if it is an http(s),
 *   relative or inline image URL. The feed keeps at most data-max-items rows
 *   (the component's ``max_items``, default 50): the oldest fall off the end
 *   (focus on a row that falls off moves to the last row left).
 *   Streamed rows live on the page: a view should either stream them or
 *   re-render ``events``, not both for the same feed.
 * - Announcements: the new activity is read out in a polite live region (a
 *   burst is summarised as a count).
 * - Keyboard (the WAI-ARIA feed pattern): each article is focusable;
 *   Page Down / Page Up move to the next / previous article.
 *
 * An app's own ``ActivityFeed`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var ITEM = "dj-activity-feed__item";
  var DEFAULT_MAX = 50;
  var MAX_ANNOUNCED = 3;

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

  // The server's policy for an <img src>: relative URLs, http(s) and inline
  // data:image URIs; every other scheme is refused.
  function imageUrl(value) {
    var url = String(value === undefined || value === null ? "" : value).trim();
    if (!url) return "";
    var probe = url.replace(/[\u0000- \u007f]+/g, "").toLowerCase();
    if (/^data:image\/[a-z0-9.+-]+[;,]/.test(probe)) return url;
    var scheme = /^([a-z][a-z0-9+.-]*):/.exec(probe);
    if (scheme) return scheme[1] === "http" || scheme[1] === "https" ? url : "";
    return url;
  }

  function text(value) {
    return value === undefined || value === null ? "" : String(value);
  }

  function span(cls, content) {
    var el = document.createElement("span");
    el.className = cls;
    el.textContent = content;
    return el;
  }

  function initials(user) {
    return (
      user
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map(function (w) {
          return w.charAt(0).toUpperCase();
        })
        .join("") || "?"
    );
  }

  // The same structure the server renders for one event.
  function buildItem(event) {
    var user = text(event.user);
    var row = document.createElement("div");
    row.className = ITEM;
    row.setAttribute("role", "article");

    var avatar = document.createElement("span");
    avatar.className = "dj-activity-feed__avatar";
    var src = imageUrl(event.avatar);
    if (src) {
      var img = document.createElement("img");
      img.className = "dj-activity-feed__avatar-img";
      img.setAttribute("src", src);
      img.setAttribute("alt", user);
      avatar.appendChild(img);
    } else {
      avatar.appendChild(span("dj-activity-feed__avatar-initials", initials(user)));
    }
    row.appendChild(avatar);

    var body = document.createElement("div");
    body.className = "dj-activity-feed__body";
    if (text(event.icon)) body.appendChild(span("dj-activity-feed__icon", text(event.icon)));
    var line = document.createElement("span");
    line.className = "dj-activity-feed__text";
    var strong = document.createElement("strong");
    strong.className = "dj-activity-feed__user";
    strong.textContent = user;
    line.appendChild(strong);
    line.appendChild(document.createTextNode(" " + text(event.action)));
    if (text(event.target)) {
      line.appendChild(document.createTextNode(" "));
      line.appendChild(span("dj-activity-feed__target", text(event.target)));
    }
    body.appendChild(line);
    if (text(event.time)) body.appendChild(span("dj-activity-feed__time", text(event.time)));
    row.appendChild(body);
    return row;
  }

  function eventsFrom(payload) {
    var list = [];
    if (Array.isArray(payload)) list = payload;
    else if (payload && Array.isArray(payload.events)) list = payload.events;
    else if (payload && payload.event && typeof payload.event === "object") list = [payload.event];
    else if (payload && typeof payload === "object" && (payload.user || payload.action)) list = [payload];
    return list.filter(function (e) {
      return e && typeof e === "object" && !Array.isArray(e);
    });
  }

  function summary(event) {
    var parts = [text(event.user), text(event.action), text(event.target)].filter(Boolean);
    return parts.join(" ");
  }

  var activityFeed = {
    mounted: function () {
      var self = this;
      this._bind();
      this._enhance();
      var eventName = this.el.getAttribute("data-stream-event");
      if (eventName) {
        this.handleEvent(eventName, function (payload) {
          self._ingest(eventsFrom(payload));
        });
      }
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      // A re-render redraws the rendered rows; count again from them.
      this._rows = undefined;
      this._enhance();
    },

    destroyed: function () {
      this._unbind();
      if (this._raf && window.cancelAnimationFrame) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
    },

    _articles: function () {
      return Array.prototype.filter.call(this.el.children, function (c) {
        return c.classList && c.classList.contains(ITEM);
      });
    },

    // Feed semantics: every article focusable and numbered (the total is
    // unknown for a stream: -1). Idempotent; re-applied from updated().
    _enhance: function () {
      var list = this._articles();
      list.forEach(function (el, i) {
        if (el.getAttribute("tabindex") !== "0") el.setAttribute("tabindex", "0");
        var pos = String(i + 1);
        if (el.getAttribute("aria-posinset") !== pos) el.setAttribute("aria-posinset", pos);
        if (el.getAttribute("aria-setsize") !== "-1") el.setAttribute("aria-setsize", "-1");
      });
    },

    // New rows shift every position: renumber once per frame, however many
    // events arrived in it.
    _renumberSoon: function () {
      var self = this;
      if (!window.requestAnimationFrame) {
        this._enhance();
        return;
      }
      if (this._raf) return;
      this._raf = window.requestAnimationFrame(function () {
        self._raf = 0;
        self._enhance();
      });
    },

    // O(events pushed), not O(rows held).
    _ingest: function (events) {
      if (!events.length) return;
      var root = this.el;
      var max = parseInt(root.getAttribute("data-max-items"), 10);
      if (!(max > 0)) max = DEFAULT_MAX;
      if (this._rows === undefined) this._rows = this._articles().length;

      var fragment = document.createDocumentFragment();
      events.forEach(function (event) {
        fragment.appendChild(buildItem(event));
      });
      root.insertBefore(fragment, root.firstChild);
      this._rows += events.length;
      var active = root.ownerDocument.activeElement;
      var lostFocus = false;
      while (this._rows > max && root.lastElementChild) {
        if (active && root.lastElementChild.contains(active)) lostFocus = true;
        root.removeChild(root.lastElementChild);
        this._rows -= 1;
      }
      // A focused row that fell off the end would drop focus to the page:
      // keep the keyboard user in the feed, on the nearest row left.
      if (lostFocus) {
        var left = this._articles();
        if (left.length) {
          var target = left[left.length - 1];
          // New rows only become focusable on the next frame's renumbering.
          if (target.getAttribute("tabindex") !== "0") target.setAttribute("tabindex", "0");
          target.focus();
        }
      }
      this._renumberSoon();

      if (events.length <= MAX_ANNOUNCED) {
        announce(
          events
            .map(summary)
            .filter(Boolean)
            .join(". ") || "New activity"
        );
      } else {
        announce(events.length + " new activities");
      }
    },

    _bind: function () {
      var root = this.el;
      this._boundEl = root;
      var h = {
        keydown: function (e) {
          if (e.key !== "PageDown" && e.key !== "PageUp") return;
          if (e.altKey || e.ctrlKey || e.metaKey) return;
          var article = e.target && e.target.closest ? e.target.closest("." + ITEM) : null;
          if (!article || article.parentNode !== root) return;
          var next = e.key === "PageDown" ? article.nextElementSibling : article.previousElementSibling;
          if (next && next.classList.contains(ITEM)) {
            e.preventDefault();
            next.focus();
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
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.ActivityFeed && !window.DjustHooks.ActivityFeed) {
    window.DjustHooks.ActivityFeed = activityFeed;
  }
})();
