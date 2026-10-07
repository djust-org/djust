/**
 * CollabSelection — anchoring, accessibility and presence announcements for
 * the CollabSelection component (dj-hook="CollabSelection").
 *
 * The component draws what the view passes in ``users`` (name, color, start,
 * end, text); where the selections come from is the app's business (for
 * example PresenceMixin and a push_event), and nothing here sends anything to
 * the server.
 *
 * - Anchoring (data-target, the component's ``target`` selector): ``start``
 *   and ``end`` are character offsets into the text of the element the
 *   selector picks (UTF-16 code units over its ``textContent``, ``start``
 *   inclusive, ``end`` exclusive; text inside this component, and the text of
 *   ``<script>`` and ``<style>`` elements in the target, is not counted: they
 *   are in ``textContent`` but are not text a reader sees or selects).
 *   The hook highlights those ranges in the text itself with the CSS Custom
 *   Highlight API (the page's own nodes are not touched) and puts each user's
 *   name above the start of their range. It follows scrolling, resizing and
 *   edits of the target's text. The offsets are clamped to the text, and an
 *   empty or reversed range draws nothing. Where the browser has no
 *   ``CSS.highlights``, the selector matches nothing (the target is looked up
 *   in the document: an element inside a shadow root is not found), or the
 *   page's style policy refuses the highlight rules, the component shows each
 *   selection's own ``text`` where it is rendered, as it does without a
 *   target. The rules are put in a constructed stylesheet, which a policy that
 *   forbids inline ``<style>`` elements (``style-src`` without
 *   ``'unsafe-inline'``) does not block; a ``<style>`` element is the fallback
 *   where those are unsupported.
 * - Accessibility: each selection is a group named "<name> selected: <text>"
 *   (the visible label and highlight are decoration for assistive tech), and
 *   someone's selection appearing or disappearing is announced in a polite
 *   live region; a crowd is summarised as a count. Moving a selection is not
 *   announced.
 *
 * Selections are matched by their position in the list, so a re-render that
 * removes one patches the rest in place: everything the hook derives is
 * recomputed from the DOM after each patch, never remembered per node.
 *
 * An app's own ``CollabSelection`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var RANGE = "dj-collab-sel__range";
  var LABEL = "dj-collab-sel__label";
  var HIGHLIGHT = "dj-collab-sel__highlight";
  var ANCHORED = "dj-collab-sel--anchored";
  var MAX_ANNOUNCED = 3;
  var MAX_SELECTIONS = 100;
  var MAX_LABEL_TEXT = 80;
  var DEFAULTS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#ec4899", "#06b6d4", "#f97316"];
  var nextId = 0;

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

  function ranges(root) {
    return Array.prototype.filter.call(root.children, function (c) {
      return c.classList && c.classList.contains(RANGE);
    });
  }

  function child(el, cls) {
    return Array.prototype.find.call(el.children, function (c) {
      return c.classList && c.classList.contains(cls);
    });
  }

  function nameOf(range) {
    return range.getAttribute("data-user") || "";
  }

  function textOf(range) {
    var h = child(range, HIGHLIGHT);
    return h ? (h.textContent || "").trim() : "";
  }

  function counts(root) {
    var out = Object.create(null);
    ranges(root).forEach(function (r) {
      var name = nameOf(r);
      out[name] = (out[name] || 0) + 1;
    });
    return out;
  }

  // A colour that is safe to write into a style rule: a hex colour, a named
  // colour, or rgb()/hsl() with plain numbers. Anything else (a value that
  // would close the rule and start another) falls back to the palette.
  function safeColor(value, fallback) {
    var v = String(value || "").trim();
    if (/^#[0-9a-f]{3,8}$/i.test(v)) return v;
    if (/^[a-z]{3,20}$/i.test(v)) return v;
    if (/^(?:rgb|hsl)a?\(\s*[\d.]+%?(?:\s*[,\s/]\s*[\d.]+%?){2,3}\s*\)$/i.test(v)) return v;
    return fallback;
  }

  function colorOf(range, index) {
    var fallback = DEFAULTS[index % DEFAULTS.length];
    var raw = "";
    try {
      raw = window.getComputedStyle(range).getPropertyValue("--dj-collab-sel-color");
    } catch (_err) {
      raw = "";
    }
    if (!raw) {
      var style = range.getAttribute("style") || "";
      var m = /--dj-collab-sel-color\s*:\s*([^;]+)/.exec(style);
      raw = m ? m[1] : "";
    }
    return safeColor(raw, fallback);
  }

  function intAttr(el, name) {
    var n = parseInt(el.getAttribute(name), 10);
    return isFinite(n) ? n : 0;
  }

  function summarise(verb, names) {
    if (!names.length) return "";
    if (names.length <= MAX_ANNOUNCED) return names.join(", ") + " " + verb;
    return names.length + " people " + verb;
  }

  // The text nodes of ``target`` (not the ones inside ``skip``) with the
  // offset each starts at.
  function textMap(target, skip) {
    var walker = document.createTreeWalker(target, 4 /* SHOW_TEXT */, null);
    var nodes = [];
    var total = 0;
    var node;
    while ((node = walker.nextNode())) {
      var parent = node.parentNode;
      if (skip && skip.contains(node)) continue;
      if (parent && /^(?:SCRIPT|STYLE)$/i.test(parent.nodeName)) continue;
      nodes.push({ node: node, start: total });
      total += node.nodeValue.length;
    }
    return { nodes: nodes, total: total };
  }

  function locate(map, offset) {
    var nodes = map.nodes;
    for (var i = 0; i < nodes.length; i++) {
      var end = nodes[i].start + nodes[i].node.nodeValue.length;
      if (offset <= end) return { node: nodes[i].node, offset: offset - nodes[i].start };
    }
    return null;
  }

  // What of ``el`` can be seen: its box cut down to every ancestor that
  // clips (a scroll container, overflow: hidden) and to the viewport.
  function visibleRect(el) {
    var r = el.getBoundingClientRect();
    var box = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    function cut(other) {
      box.left = Math.max(box.left, other.left);
      box.top = Math.max(box.top, other.top);
      box.right = Math.min(box.right, other.right);
      box.bottom = Math.min(box.bottom, other.bottom);
    }
    for (var node = el.parentElement; node && node !== document.documentElement; node = node.parentElement) {
      var cs = window.getComputedStyle(node);
      var clips = function (v) {
        return v && v !== "visible";
      };
      if (clips(cs.overflowX) || clips(cs.overflowY)) cut(node.getBoundingClientRect());
    }
    cut({ left: 0, top: 0, right: window.innerWidth || Infinity, bottom: window.innerHeight || Infinity });
    return box;
  }

  // Put the highlight rules where the page's style policy allows them. A
  // constructed stylesheet is not an inline <style> element, so a policy
  // without 'unsafe-inline' for style elements does not refuse it; a <style>
  // element is the fallback, and one the policy refused has no sheet. Returns
  // what to remove later, or null when the rules could not be applied.
  function installRules(css, id) {
    if (typeof window.CSSStyleSheet === "function" && "adoptedStyleSheets" in document) {
      try {
        var sheet = new window.CSSStyleSheet();
        sheet.replaceSync(css);
        document.adoptedStyleSheets = Array.prototype.concat.call(document.adoptedStyleSheets, [sheet]);
        return { sheet: sheet };
      } catch (_err) {
        // fall through to a style element
      }
    }
    var style = document.createElement("style");
    style.setAttribute("data-dj-collab-sel", String(id));
    style.textContent = css;
    document.head.appendChild(style);
    if (!style.sheet) {
      if (style.parentNode) style.parentNode.removeChild(style);
      return null;
    }
    return { style: style };
  }

  function removeRules(installed) {
    if (!installed) return;
    if (installed.sheet) {
      document.adoptedStyleSheets = Array.prototype.filter.call(document.adoptedStyleSheets, function (s) {
        return s !== installed.sheet;
      });
    }
    if (installed.style && installed.style.parentNode) installed.style.parentNode.removeChild(installed.style);
  }

  var collabSelection = {
    mounted: function () {
      this._id = ++nextId;
      this._names = counts(this.el);
      this._enhance();
      this._bindAnchor();
      this._anchorSoon(true);
    },

    updated: function () {
      this._enhance();
      this._presence();
      var wants = !!this.el.getAttribute("data-target");
      if (this._boundEl !== this.el || wants !== !!this._onMove) {
        this._unbindAnchor();
        this._bindAnchor();
      }
      this._anchorSoon(true);
    },

    destroyed: function () {
      this._unbindAnchor();
      this._clearHighlights();
      if (this._raf && window.cancelAnimationFrame) window.cancelAnimationFrame(this._raf);
      this._raf = 0;
    },

    // Each selection is a named group; its label and highlight are visual.
    // Idempotent, re-applied after every patch.
    _enhance: function () {
      ranges(this.el).forEach(function (r) {
        var name = nameOf(r);
        var text = textOf(r);
        var shown = text.length > MAX_LABEL_TEXT ? text.slice(0, MAX_LABEL_TEXT) + "…" : text;
        var label = text ? name + " selected: " + shown : name + "'s selection";
        if (r.getAttribute("role") !== "group") r.setAttribute("role", "group");
        if (r.getAttribute("aria-label") !== label) r.setAttribute("aria-label", label);
        [HIGHLIGHT, LABEL].forEach(function (cls) {
          var el = child(r, cls);
          if (el && el.getAttribute("aria-hidden") !== "true") el.setAttribute("aria-hidden", "true");
        });
      });
    },

    _presence: function () {
      var before = this._names || Object.create(null);
      var now = counts(this.el);
      var appeared = [];
      var gone = [];
      Object.keys(now).forEach(function (name) {
        for (var i = 0; i < now[name] - (before[name] || 0); i++) appeared.push(name);
      });
      Object.keys(before).forEach(function (name) {
        for (var i = 0; i < before[name] - (now[name] || 0); i++) gone.push(name);
      });
      this._names = now;
      var parts = [
        summarise("selected text", appeared),
        summarise("cleared their selection", gone),
      ].filter(Boolean);
      if (parts.length) announce(parts.join(". "));
    },

    // ---- anchoring -------------------------------------------------------

    _target: function () {
      var selector = this.el.getAttribute("data-target");
      if (!selector) return null;
      try {
        return document.querySelector(selector);
      } catch (_err) {
        return null; // not a valid selector
      }
    },

    // Scrolling and resizing only move the names; a change of the target's
    // text (or of the selections, which re-render) redraws the highlights.
    _bindAnchor: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;
      if (!root.getAttribute("data-target")) return;
      this._onMove = function () {
        self._anchorSoon(false);
      };
      this._onText = function () {
        self._anchorSoon(true);
      };
      window.addEventListener("scroll", this._onMove, { capture: true, passive: true });
      window.addEventListener("resize", this._onMove, { passive: true });
      var target = this._target();
      this._observedTarget = target;
      if (target && typeof window.MutationObserver === "function") {
        this._mo = new window.MutationObserver(this._onText);
        this._mo.observe(target, { characterData: true, childList: true, subtree: true });
      }
      if (target && typeof window.ResizeObserver === "function") {
        this._ro = new window.ResizeObserver(this._onMove);
        this._ro.observe(target);
      }
    },

    _unbindAnchor: function () {
      if (this._onMove) {
        window.removeEventListener("scroll", this._onMove, true);
        window.removeEventListener("resize", this._onMove);
      }
      this._onMove = null;
      this._onText = null;
      if (this._mo) this._mo.disconnect();
      if (this._ro) this._ro.disconnect();
      this._mo = null;
      this._ro = null;
      this._observedTarget = null;
      this._boundEl = null;
    },

    _anchorSoon: function (redraw) {
      var self = this;
      if (redraw) this._stale = true;
      if (!window.requestAnimationFrame) {
        this._anchor();
        return;
      }
      if (this._raf) return;
      this._raf = window.requestAnimationFrame(function () {
        self._raf = 0;
        self._anchor();
      });
    },

    _clearHighlights: function () {
      var registry = window.CSS && window.CSS.highlights;
      (this._highlightNames || []).forEach(function (name) {
        if (registry) registry.delete(name);
      });
      this._highlightNames = [];
      removeRules(this._rules);
      this._rules = null;
      this._items = null;
    },

    _unanchor: function () {
      this.el.classList.remove(ANCHORED);
      this._clearHighlights();
      ranges(this.el).forEach(function (r) {
        var label = child(r, LABEL);
        if (!label) return;
        ["position", "left", "top", "visibility", "bottom"].forEach(function (p) {
          label.style.removeProperty(p);
        });
      });
    },

    _anchor: function () {
      var root = this.el;
      var registry = window.CSS && window.CSS.highlights;
      var Highlight = window.Highlight;
      var target = this._target();
      var redraw = this._stale || !this._items;
      this._stale = false;
      if (!root.getAttribute("data-target")) {
        this._unanchor();
        return;
      }
      // The selector may have started matching (or matched something else)
      // since the observers were set up.
      if (target !== this._observedTarget) {
        this._unbindAnchor();
        this._bindAnchor();
        redraw = true;
      }
      if (!target || !registry || typeof Highlight !== "function" || !document.createRange) {
        this._unanchor();
        return;
      }
      if (redraw && !this._draw(target, registry, Highlight)) {
        // The rules could not be applied (a style policy refused them): the
        // component's own inline text is all there is to show.
        this._unanchor();
        return;
      }
      this._place(target);
      root.classList.add(ANCHORED);
    },

    // Highlight every selection at its offsets in the target's text, from
    // scratch. False when the highlight rules could not be applied.
    _draw: function (target, registry, Highlight) {
      var root = this.el;
      this._clearHighlights();
      var map = textMap(target, root);
      var rules = [];
      var created = [];
      var items = [];
      var id = this._id;

      ranges(root)
        .slice(0, MAX_SELECTIONS)
        .forEach(function (r, i) {
          var label = child(r, LABEL);
          var s = Math.max(0, Math.min(intAttr(r, "data-start"), map.total));
          var e = Math.max(0, Math.min(intAttr(r, "data-end"), map.total));
          var item = { label: label, range: null };
          items.push(item);
          if (!(e > s)) return;
          var from = locate(map, s);
          var to = locate(map, e);
          if (!from || !to) return;
          var range = document.createRange();
          try {
            range.setStart(from.node, from.offset);
            range.setEnd(to.node, to.offset);
          } catch (_err) {
            return;
          }
          var name = "dj-collab-" + id + "-" + i;
          var color = colorOf(r, i);
          registry.set(name, new Highlight(range));
          created.push(name);
          rules.push(
            "::highlight(" + name + "){background-color:color-mix(in srgb," + color + " 25%,transparent);" +
              "text-decoration:underline;text-decoration-color:" + color + ";text-decoration-thickness:2px;}"
          );
          item.range = range;
        });

      this._highlightNames = created;
      this._items = items;
      if (rules.length) {
        this._rules = installRules(rules.join("\n"), id);
        if (!this._rules) {
          this._clearHighlights();
          return false;
        }
      }
      return true;
    },

    // Put each name above the start of its range, where that text is now.
    _place: function (target) {
      var targetRect = visibleRect(target);
      var placed = [];
      (this._items || []).forEach(function (item) {
        var label = item.label;
        if (!label) return;
        var hide = function () {
          label.style.setProperty("visibility", "hidden");
        };
        var range = item.range;
        if (!range) {
          hide();
          return;
        }
        var rects = range.getClientRects ? range.getClientRects() : [];
        var first = rects && rects.length ? rects[0] : range.getBoundingClientRect && range.getBoundingClientRect();
        var h = label.offsetHeight || 16;
        var inside =
          first &&
          first.width + first.height > 0 &&
          first.left >= targetRect.left - 1 &&
          first.left <= targetRect.right &&
          first.top >= targetRect.top - 1 &&
          first.top <= targetRect.bottom;
        if (!inside) {
          hide();
          return;
        }
        label.style.setProperty("position", "fixed");
        label.style.removeProperty("visibility");
        placed.push({ label: label, left: Math.round(first.left), top: Math.round(first.top - h - 2) });
      });

      // ``position: fixed`` is relative to the viewport unless an ancestor has
      // a transform (or filter, ...), which then becomes its containing block.
      // Put the first name where it should be and see where it landed: the
      // difference is the same for every name.
      var dx = 0;
      var dy = 0;
      if (placed.length) {
        var probe = placed[0];
        probe.label.style.setProperty("left", probe.left + "px");
        probe.label.style.setProperty("top", probe.top + "px");
        var at = probe.label.getBoundingClientRect();
        if (at && at.width + at.height > 0) {
          dx = probe.left - at.left;
          dy = probe.top - at.top;
        }
        if (!isFinite(dx) || !isFinite(dy)) {
          dx = 0;
          dy = 0;
        }
      }
      placed.forEach(function (p) {
        p.label.style.setProperty("left", Math.round(p.left + dx) + "px");
        p.label.style.setProperty("top", Math.round(p.top + dy) + "px");
      });
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.CollabSelection && !window.DjustHooks.CollabSelection) {
    window.DjustHooks.CollabSelection = collabSelection;
  }
})();
