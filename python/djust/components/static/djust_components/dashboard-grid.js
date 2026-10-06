/**
 * DashboardGrid — drag, resize and keyboard control for the DashboardGrid
 * component (dj-hook="DashboardGrid").
 *
 * Panels sit on the grid by ``col`` / ``row`` (1-based) and ``width`` /
 * ``height`` (in cells). The hook turns a gesture into a request, in whole grid
 * units, and the server stays the authority:
 *
 *   move    ->  data-move-event    with {id, col, row}
 *   resize  ->  data-resize-event  with {id, width, height}
 *
 * ``id`` is the panel's ``id`` as rendered. Nothing is moved on the client: the
 * panel stays where the server put it until the server re-renders with new
 * numbers (an app that ignores the event leaves the panel where it was).
 * These payloads come from the browser, so treat them as untrusted: check that
 * ``id`` is a panel of this dashboard the user may change, that the numbers
 * are integers, and clamp them to the grid (``1 <= col``, ``col + width - 1
 * <= columns``, ``1 <= row``) before using them; the docstring of the
 * component has a handler that does.
 *
 * Pointer (mouse, touch, pen): drag a panel by its header to move it, drag the
 * handle at its bottom edge to resize it. A dashed cell shows where it will
 * land (a ::after on the grid, nothing is added to the DOM); letting go sends
 * the event, Escape (or a cancelled pointer) puts everything back. A press
 * without movement does nothing, and buttons and fields in a header work as
 * usual.
 *
 * Keyboard (one tab stop, like a toolbar): the arrow keys move between panels;
 * Space or Enter grabs the focused panel, the arrow keys then move it and
 * Shift+arrow keys resize it (Right/Down grow, Left/Up shrink), Space or Enter
 * drops it and sends the move and/or resize events, Escape (or leaving the
 * grid) cancels. Each step is announced in a polite live region.
 *
 * The browser's own drag-and-drop is switched off on the panels (the markup
 * says ``draggable``), so text in a panel can be selected and the pointer
 * gestures work for touch too.
 *
 * Right-to-left grids are mirrored (column 1 is on the right, the arrow keys
 * point where they point), and a grid drawn smaller or larger than it is laid
 * out (a transform or zoom on an ancestor) is measured as drawn.
 *
 * Panels are matched by ``data-panel-id``, never by position in the grid or
 * by node: a re-render patches nodes in place, so a gesture in progress is
 * followed by id, and cancelled if its panel goes away.
 *
 * An app's own ``DashboardGrid`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var PANEL = "dj-dashboard-grid__panel";
  var HEADER = "dj-dashboard-grid__panel-header";
  var RESIZE = "dj-dashboard-grid__panel-resize";
  var DRAGGING = "dj-dashboard-grid__panel--dragging";
  var THRESHOLD = 4;
  var INTERACTIVE = "a,button,input,select,textarea,label,summary,[contenteditable],[role=button]";

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

  // Same public entry point dj-click and dj-viewport use, so the event works
  // over WebSocket, SSE and HTTP-only, honours strict parameter contracts and
  // reaches the right view when several are mounted. Falls back to the hook's
  // own pushEvent when the client API is absent.
  function send(hook, root, eventName, params) {
    var d = window.djust;
    if (d && typeof d.handleEvent === "function") {
      var sent = params;
      if (typeof d._strictBinding === "function") {
        var strict = d._strictBinding(root, eventName, params, []);
        if (strict === false) return;
        if (strict) sent = strict;
      }
      if (typeof d._markSlotOf === "function") d._markSlotOf(sent, root);
      d.handleEvent(eventName, sent);
    } else if (typeof hook.pushEvent === "function") {
      hook.pushEvent(eventName, params);
    }
  }

  function panels(root) {
    return Array.prototype.filter.call(root.children, function (c) {
      return c.classList && c.classList.contains(PANEL);
    });
  }

  function idOf(panel) {
    return panel.getAttribute("data-panel-id") || "";
  }

  function byId(root, id) {
    return panels(root).filter(function (p) {
      return idOf(p) === id;
    })[0];
  }

  function panelOf(root, node) {
    var el = node && node.nodeType === 1 ? node : node && node.parentElement;
    var panel = el && el.closest ? el.closest("." + PANEL) : null;
    return panel && panel.parentNode === root ? panel : null;
  }

  function titleOf(panel) {
    var t = panel.querySelector(".dj-dashboard-grid__panel-title");
    return ((t || panel).textContent || "").trim();
  }

  function columnsOf(root) {
    var n = parseInt(root.getAttribute("data-columns"), 10);
    return n > 0 ? n : 1;
  }

  // The numbers the server wrote into the panel's style:
  // grid-column:C/span W;grid-row:R/span H. Not read from layout, which is
  // wherever the browser put an overlapping or auto-placed panel.
  function cellsOf(panel) {
    var style = panel.getAttribute("style") || "";
    var c = /grid-column\s*:\s*(-?\d+)\s*\/\s*span\s+(\d+)/.exec(style);
    var r = /grid-row\s*:\s*(-?\d+)\s*\/\s*span\s+(\d+)/.exec(style);
    return {
      col: c ? Math.max(1, parseInt(c[1], 10)) : 1,
      w: c ? Math.max(1, parseInt(c[2], 10)) : 1,
      row: r ? Math.max(1, parseInt(r[1], 10)) : 1,
      h: r ? Math.max(1, parseInt(r[2], 10)) : 1,
    };
  }

  // The rows the grid has: the lowest edge of any panel.
  function rowCount(root) {
    var rows = 1;
    panels(root).forEach(function (p) {
      var c = cellsOf(p);
      rows = Math.max(rows, c.row + c.h - 1);
    });
    return rows;
  }

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  // Where a cell may go. A panel may be put one row below the lowest one
  // (that is how a dashboard grows); the server has the last word.
  function limits(root) {
    return { columns: columnsOf(root), rows: rowCount(root) + 1 };
  }

  function fitMove(root, cells, col, row) {
    var lim = limits(root);
    return {
      col: clamp(col, 1, Math.max(1, lim.columns - cells.w + 1)),
      row: clamp(row, 1, Math.max(1, lim.rows - cells.h + 1)),
    };
  }

  function fitSize(root, cells, w, h) {
    var lim = limits(root);
    return {
      w: clamp(w, 1, Math.max(1, lim.columns - cells.col + 1)),
      h: clamp(h, 1, Math.max(1, lim.rows - cells.row + 1)),
    };
  }

  // ---- geometry: the track starts and ends, in viewport pixels -------------

  function trackSizes(value) {
    return String(value || "")
      .split(/\s+/)
      .map(parseFloat)
      .filter(function (n) {
        return isFinite(n);
      });
  }

  // Column and row tracks of the grid, in viewport pixels, in the grid's own
  // order: [{start, end}], column 1 first (on the right in a right-to-left
  // grid), plus one virtual row below the last (the row a panel can grow
  // into). The track sizes the browser computes are layout pixels, so they are
  // scaled to the viewport by the ratio of the grid's drawn to its laid-out size
  // (a transform or zoom on an ancestor).
  function metrics(root) {
    var cs = window.getComputedStyle(root);
    var rect = root.getBoundingClientRect();
    var columns = columnsOf(root);
    var rtl = cs.direction === "rtl";
    var sx = root.offsetWidth ? rect.width / root.offsetWidth : 1;
    var sy = root.offsetHeight ? rect.height / root.offsetHeight : 1;
    var gapX = (parseFloat(cs.columnGap) || 0) * sx;
    var gapY = (parseFloat(cs.rowGap) || 0) * sy;
    var padL = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.paddingLeft) || 0);
    var padR = (parseFloat(cs.borderRightWidth) || 0) + (parseFloat(cs.paddingRight) || 0);
    var y0 = rect.top + ((parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.paddingTop) || 0)) * sy;
    var widths = trackSizes(cs.gridTemplateColumns).map(function (w) {
      return w * sx;
    });
    if (widths.length !== columns) {
      var inner = rect.width - (padL + padR) * sx;
      var each = Math.max(1, (inner - gapX * (columns - 1)) / columns);
      widths = [];
      for (var i = 0; i < columns; i++) widths.push(each);
    }
    var rows = rowCount(root);
    var heights = trackSizes(cs.gridTemplateRows).map(function (h) {
      return h * sy;
    });
    if (heights.length < rows) {
      var tall = Math.max(1, (rect.height - gapY * (rows - 1)) / rows);
      heights = [];
      for (var j = 0; j < rows; j++) heights.push(tall);
    }
    // the virtual row below
    heights = heights.concat([heights[heights.length - 1] || 1]);
    function across(sizes, origin, gap, dir) {
      var at = origin;
      return sizes.map(function (size) {
        var t = { start: at, end: at + dir * size };
        at += dir * (size + gap);
        return t;
      });
    }
    var cols = rtl
      ? across(widths, rect.right - padR * sx, gapX, -1)
      : across(widths, rect.left + padL * sx, gapX, 1);
    return { cols: cols, rows: across(heights, y0, gapY, 1), rtl: rtl };
  }

  // The 1-based track whose start (or end) is nearest a pixel position.
  function nearest(tracks, pos, edge) {
    var best = 0;
    var bestDistance = Infinity;
    tracks.forEach(function (t, i) {
      var d = Math.abs(t[edge] - pos);
      if (d < bestDistance) {
        bestDistance = d;
        best = i;
      }
    });
    return best + 1;
  }

  // ---- the hook ---------------------------------------------------------------

  var dashboardGrid = {
    mounted: function () {
      this._gesture = null;
      this._bind();
      this._enhance();
    },

    // A patch that moves a focused node can blur it; note which panel had
    // focus to give it back afterwards.
    beforeUpdate: function () {
      var focused = panelOf(this.el, document.activeElement);
      this._focusedId = focused ? idOf(focused) : null;
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      this._enhance();
      var g = this._gesture;
      if (g) {
        if (!byId(this.el, g.id)) this._cancel("its panel is gone");
        else this._paint();
      }
      var id = this._focusedId;
      this._focusedId = null;
      if (id && !panelOf(this.el, document.activeElement)) {
        var panel = byId(this.el, id);
        if (panel) panel.focus();
      }
    },

    destroyed: function () {
      this._cancelQuiet();
      this._unbind();
    },

    // Panels: named, in the tab order once, native drag off. Idempotent,
    // re-applied after every patch (a new panel arrives without any of it).
    _enhance: function () {
      var list = panels(this.el);
      var active = this._activeId;
      if (!active || !byId(this.el, active)) active = list.length ? idOf(list[0]) : null;
      this._activeId = active;
      list.forEach(function (p) {
        if (p.getAttribute("draggable") !== "false") p.setAttribute("draggable", "false");
        if (p.getAttribute("role") !== "group") p.setAttribute("role", "group");
        var title = titleOf(p);
        if (title && p.getAttribute("aria-label") !== title) p.setAttribute("aria-label", title);
        var tab = idOf(p) === active ? "0" : "-1";
        if (p.getAttribute("tabindex") !== tab) p.setAttribute("tabindex", tab);
        var bar = p.querySelector("." + RESIZE);
        if (bar && bar.getAttribute("aria-hidden") !== "true") bar.setAttribute("aria-hidden", "true");
      });
    },

    // ---- events ------------------------------------------------------------

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;
      var h = {
        pointerdown: function (e) {
          if (e.button !== undefined && e.button !== 0) return;
          if (self._gesture) return;
          var panel = panelOf(root, e.target);
          if (!panel) return;
          var target = e.target && e.target.closest ? e.target : null;
          if (target && target.closest("." + RESIZE)) {
            self._begin("resize", panel, e);
          } else if (target && target.closest("." + HEADER)) {
            if (target.closest(INTERACTIVE)) return;
            self._begin("move", panel, e);
          }
        },
        pointermove: function (e) {
          var g = self._gesture;
          if (!g || g.kind === "key" || (g.pointerId !== undefined && e.pointerId !== g.pointerId)) return;
          self._track(e);
        },
        pointerup: function (e) {
          var g = self._gesture;
          if (!g || g.kind === "key" || (g.pointerId !== undefined && e.pointerId !== g.pointerId)) return;
          self._finish();
        },
        pointercancel: function (e) {
          var g = self._gesture;
          if (!g || g.kind === "key" || (g.pointerId !== undefined && e.pointerId !== g.pointerId)) return;
          self._cancel("cancelled");
        },
        focusin: function (e) {
          var panel = panelOf(root, e.target);
          if (!panel) return;
          self._activeId = idOf(panel);
          self._enhance();
        },
        focusout: function (e) {
          var g = self._gesture;
          if (g && g.kind === "key" && !root.contains(e.relatedTarget)) self._cancel("left the grid");
        },
        keydown: function (e) {
          self._keydown(e);
        },
      };
      this._h = h;
      Object.keys(h).forEach(function (type) {
        root.addEventListener(type, h[type]);
      });
      // Escape cancels a pointer gesture wherever focus is.
      this._onKey = function (e) {
        var g = self._gesture;
        if (g && g.kind !== "key" && e.key === "Escape") {
          e.preventDefault();
          self._cancel("cancelled");
        }
      };
      document.addEventListener("keydown", this._onKey);
    },

    _unbind: function () {
      var root = this._boundEl;
      var h = this._h;
      if (root && h) {
        Object.keys(h).forEach(function (type) {
          root.removeEventListener(type, h[type]);
        });
      }
      if (this._onKey) document.removeEventListener("keydown", this._onKey);
      this._onKey = null;
      this._h = null;
      this._boundEl = null;
    },

    // ---- pointer gestures --------------------------------------------------

    _begin: function (kind, panel, e) {
      var cells = cellsOf(panel);
      var rect = panel.getBoundingClientRect();
      this._gesture = {
        kind: kind,
        id: idOf(panel),
        pointerId: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
        cells: cells,
        next: { col: cells.col, row: cells.row, w: cells.w, h: cells.h },
        moved: false,
      };
      if (e.pointerId !== undefined && this.el.setPointerCapture) {
        try {
          this.el.setPointerCapture(e.pointerId);
        } catch (_err) {
          // A pointer that is already gone: the gesture just ends with the next event.
        }
      }
      // The press must not start a text selection or a native drag.
      e.preventDefault();
    },

    _track: function (e) {
      var g = this._gesture;
      var dx = e.clientX - g.x;
      var dy = e.clientY - g.y;
      if (!g.moved && Math.abs(dx) < THRESHOLD && Math.abs(dy) < THRESHOLD) return;
      var panel = byId(this.el, g.id);
      if (!panel) {
        this._cancel("its panel is gone");
        return;
      }
      if (!g.moved) {
        g.moved = true;
        this._paintDragging();
      }
      var m = metrics(this.el);
      if (g.kind === "move") {
        // the panel's inline-start edge (its right edge in a right-to-left grid)
        var col = nearest(m.cols, (m.rtl ? g.rect.right : g.rect.left) + dx, "start");
        var row = nearest(m.rows, g.rect.top + dy, "start");
        var at = fitMove(this.el, g.cells, col, row);
        g.next = { col: at.col, row: at.row, w: g.cells.w, h: g.cells.h };
      } else {
        // its inline-end edge (the left one in a right-to-left grid)
        var wEnd = nearest(m.cols, (m.rtl ? g.rect.left : g.rect.right) + dx, "end");
        var hEnd = nearest(m.rows, g.rect.bottom + dy, "end");
        var size = fitSize(this.el, g.cells, wEnd - g.cells.col + 1, hEnd - g.cells.row + 1);
        g.next = { col: g.cells.col, row: g.cells.row, w: size.w, h: size.h };
      }
      this._paint();
    },

    _finish: function () {
      var g = this._gesture;
      this._gesture = null;
      this._unpaint();
      this._release(g);
      if (!g || !g.moved) return;
      this._sendChanges(g, true);
    },

    _release: function (g) {
      if (g && g.pointerId !== undefined && this.el.releasePointerCapture) {
        try {
          this.el.releasePointerCapture(g.pointerId);
        } catch (_err) {
          // Already released (the browser does it on pointerup).
        }
      }
    },

    // The events for what the gesture changed. A drop that changed nothing
    // sends nothing.
    _sendChanges: function (g, speak) {
      var el = this.el;
      var panel = byId(el, g.id);
      var title = panel ? titleOf(panel) : g.id;
      var did = false;
      if (g.next.col !== g.cells.col || g.next.row !== g.cells.row) {
        var moveEvent = el.getAttribute("data-move-event");
        if (moveEvent) {
          send(this, el, moveEvent, { id: g.id, col: g.next.col, row: g.next.row });
          did = true;
        }
      }
      if (g.next.w !== g.cells.w || g.next.h !== g.cells.h) {
        var resizeEvent = el.getAttribute("data-resize-event");
        if (resizeEvent) {
          send(this, el, resizeEvent, { id: g.id, width: g.next.w, height: g.next.h });
          did = true;
        }
      }
      if (speak) {
        announce(
          did
            ? title + " dropped at column " + g.next.col + ", row " + g.next.row + ", " + g.next.w + " wide, " + g.next.h + " high"
            : title + " dropped where it was"
        );
      }
    },

    _cancel: function (why) {
      var g = this._gesture;
      this._gesture = null;
      this._unpaint();
      this._release(g);
      if (g && (g.moved || g.kind === "key")) announce("Cancelled, " + why);
    },

    _cancelQuiet: function () {
      var g = this._gesture;
      this._gesture = null;
      this._unpaint();
      this._release(g);
    },

    // ---- feedback: a dashed cell (a ::after on the grid) and a class ---------

    _paint: function () {
      var g = this._gesture;
      if (!g || (!g.moved && g.kind !== "key")) return;
      var root = this.el;
      root.setAttribute("data-dj-drop", g.kind === "resize" ? "resize" : "move");
      root.style.setProperty("--dj-drop-col", String(g.next.col));
      root.style.setProperty("--dj-drop-row", String(g.next.row));
      root.style.setProperty("--dj-drop-w", String(g.next.w));
      root.style.setProperty("--dj-drop-h", String(g.next.h));
      this._paintDragging();
    },

    _paintDragging: function () {
      var g = this._gesture;
      var id = g ? g.id : null;
      panels(this.el).forEach(function (p) {
        p.classList.toggle(DRAGGING, id !== null && idOf(p) === id);
      });
    },

    _unpaint: function () {
      var root = this.el;
      root.removeAttribute("data-dj-drop");
      ["--dj-drop-col", "--dj-drop-row", "--dj-drop-w", "--dj-drop-h"].forEach(function (p) {
        root.style.removeProperty(p);
      });
      panels(root).forEach(function (p) {
        p.classList.remove(DRAGGING);
      });
    },

    // ---- keyboard ----------------------------------------------------------

    _keydown: function (e) {
      var root = this.el;
      var panel = panelOf(root, e.target);
      if (!panel || e.target !== panel) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      var key = e.key;
      var g = this._gesture;
      var grabbed = g && g.kind === "key" && g.id === idOf(panel);
      var dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[key];
      // The arrows point where they point: in a right-to-left grid column 1
      // is on the right, so Right means one column less.
      if (dir && dir[0] && window.getComputedStyle(root).direction === "rtl") dir = [-dir[0], 0];

      if (key === " " || key === "Enter") {
        e.preventDefault();
        if (grabbed) this._drop();
        else if (!g) this._grab(panel);
        return;
      }
      if (key === "Escape" && grabbed) {
        e.preventDefault();
        this._cancel("the panel is back where it was");
        return;
      }
      if (!dir) return;
      e.preventDefault();
      if (!grabbed) {
        if (!g) this._focusNeighbour(panel, dir);
        return;
      }
      var next = g.next;
      if (e.shiftKey) {
        var size = fitSize(root, g.cells, next.w + dir[0], next.h + dir[1]);
        next = { col: next.col, row: next.row, w: size.w, h: size.h };
      } else {
        var at = fitMove(root, g.cells, next.col + dir[0], next.row + dir[1]);
        next = { col: at.col, row: at.row, w: next.w, h: next.h };
      }
      g.next = next;
      this._paint();
      announce(
        titleOf(panel) + ": column " + next.col + ", row " + next.row + ", " + next.w + " wide, " + next.h + " high"
      );
    },

    _grab: function (panel) {
      var cells = cellsOf(panel);
      this._gesture = {
        kind: "key",
        id: idOf(panel),
        cells: cells,
        next: { col: cells.col, row: cells.row, w: cells.w, h: cells.h },
        moved: true,
      };
      this._paint();
      announce(
        titleOf(panel) + " grabbed, column " + cells.col + ", row " + cells.row +
          ". Arrow keys move it, Shift and arrow keys resize it, Enter drops it, Escape cancels."
      );
    },

    _drop: function () {
      var g = this._gesture;
      this._gesture = null;
      this._unpaint();
      this._sendChanges(g, true);
      var panel = byId(this.el, g.id);
      if (panel) panel.focus();
    },

    // The panel nearest in a direction: strictly further along that axis,
    // then the closest on the other.
    _focusNeighbour: function (panel, dir) {
      var from = cellsOf(panel);
      var best = null;
      var bestScore = Infinity;
      panels(this.el).forEach(function (p) {
        if (p === panel) return;
        var c = cellsOf(p);
        var along = dir[0] ? (c.col - from.col) * dir[0] : (c.row - from.row) * dir[1];
        if (along <= 0) return;
        var across = dir[0] ? Math.abs(c.row - from.row) : Math.abs(c.col - from.col);
        var score = across * 1000 + along;
        if (score < bestScore) {
          bestScore = score;
          best = p;
        }
      });
      if (best) best.focus();
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.DashboardGrid && !window.DjustHooks.DashboardGrid) {
    window.DjustHooks.DashboardGrid = dashboardGrid;
  }
})();
