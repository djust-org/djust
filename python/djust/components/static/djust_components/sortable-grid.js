/**
 * SortableGrid — drag-and-drop and keyboard reordering for the SortableGrid
 * component (dj-hook="SortableGrid").
 *
 * Pointer: drag a tile onto another; it is placed before or after the tile
 * under the pointer (left or right half, in reading order) and the new order
 * is sent to the server as ``{order: [id, ...]}`` through the component's
 * ``move_event`` (data-move-event).
 *
 * Keyboard: Tab to the grid (one tab stop), the arrow keys move between tiles
 * (Left/Right by one, Up/Down by a row of data-columns), Space or Enter grabs
 * the focused tile, the arrow keys (Home/End too) move it, Space or Enter
 * drops it and sends the event, Escape (or leaving the grid) puts everything
 * back. Each step is announced in a polite live region.
 *
 * The server stays the authority. Items carry ``data-key`` so the VDOM diff
 * of the re-render the event triggers moves the same DOM nodes instead of
 * rewriting their text in place. A server that ignores the event leaves the
 * dragged order on screen until its next render, as any client-side sort does.
 *
 * An app's own ``SortableGrid`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var ITEM = ".dj-sortable-grid__item";
  var DRAGGING = "dj-sortable-grid__item--dragging";
  var OVER = "dj-sortable-grid__item--over";

  function items(root) {
    return Array.prototype.filter.call(root.children, function (c) {
      return c.matches(ITEM);
    });
  }

  function itemOf(root, node) {
    var el = node && node.nodeType === 1 ? node : node && node.parentElement;
    var item = el && el.closest ? el.closest(ITEM) : null;
    return item && item.parentNode === root ? item : null;
  }

  function order(root) {
    return items(root).map(function (el) {
      return el.getAttribute("data-id") || "";
    });
  }

  function labelOf(el) {
    var l = el.querySelector(".dj-sortable-grid__label");
    return ((l || el).textContent || "").trim();
  }

  function isDisabled(root) {
    return root.getAttribute("data-disabled") === "true";
  }

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
    live.textContent = live.textContent === message ? message + "\u00a0" : message;
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

  var sortableGrid = {
    mounted: function () {
      this._bind();
      this._enhance();
    },

    // A patch that moves a focused node can blur it; note which item had
    // focus, and what the list held, to tell afterwards whether the server
    // changed it under a reorder in progress.
    beforeUpdate: function () {
      var focused = itemOf(this.el, document.activeElement);
      this._focusedId = focused ? focused.getAttribute("data-id") : null;
      this._seen = items(this.el);
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      this._enhance();
      this._reconcile();
      this._refocus();
    },

    destroyed: function () {
      this._unbind();
    },

    // One tab stop (roving tabindex) and a role description. Idempotent, and
    // run again from updated() because a server patch may drop what it added.
    _enhance: function () {
      var root = this.el;
      var list = items(root);
      var disabled = isDisabled(root);
      var activeId = this._activeId;
      var hasActive = list.some(function (el) {
        return el.getAttribute("data-id") === activeId;
      });
      list.forEach(function (el, i) {
        if (disabled) {
          el.removeAttribute("tabindex");
          return;
        }
        var tab = hasActive ? el.getAttribute("data-id") === activeId : i === 0;
        el.setAttribute("tabindex", tab ? "0" : "-1");
        if (!el.hasAttribute("aria-roledescription")) {
          el.setAttribute("aria-roledescription", "sortable item");
        }
      });
    },

    // Give focus back to the item the reader was on if a patch dropped it.
    _refocus: function () {
      var id = this._focusedId;
      this._focusedId = null;
      var active = document.activeElement;
      if (id === null || id === undefined || (active && active !== document.body)) return;
      var match = items(this.el).filter(function (el) {
        return el.getAttribute("data-id") === id;
      })[0];
      if (match) this._focus(match);
    },

    // The server changed the list while a drag or grab was in progress: what
    // was recorded at its start no longer describes the list, so it must not
    // be restored or compared against, and a held item the patch removed
    // ends the reorder.
    _reconcile: function () {
      var held = this._grab || this._drag;
      if (!held) return;
      var was = this._seen || [];
      var now = items(this.el);
      var changed =
        was.length !== now.length ||
        was.some(function (el, i) {
          return el !== now[i];
        });
      if (!changed) return;
      this._dirty = true;
      this._moved = false;
      if (held.parentNode !== this.el) {
        held.classList.remove(DRAGGING);
        this._grab = null;
        this._drag = null;
        this._before = null;
        this._dirty = false;
        announce("The list changed, so the reorder ended");
      }
    },

    // Send the order only if the reader moved something since the list last
    // changed; against the list as it was when the reorder began, unless the
    // server changed it meanwhile.
    _commit: function () {
      var before = this._before;
      var moved = this._moved;
      var dirty = this._dirty;
      this._before = null;
      this._moved = false;
      this._dirty = false;
      if (!moved) return;
      if (!dirty && before) {
        var now = items(this.el);
        var same =
          before.length === now.length &&
          before.every(function (el, i) {
            return el === now[i];
          });
        if (same) return;
      }
      var ids = order(this.el);
      this.el.dispatchEvent(
        new CustomEvent("dj-reorder", {
          bubbles: true,
          detail: { event: this.el.getAttribute("data-move-event") || "", order: ids },
        })
      );
      var eventName = this.el.getAttribute("data-move-event");
      if (eventName) send(this, this.el, eventName, { order: ids });
    },

    _restore: function () {
      var root = this.el;
      this._moving = true;
      if (!this._dirty) {
        (this._before || []).forEach(function (el) {
          if (el.isConnected) root.appendChild(el);
        });
      }
      this._moving = false;
      this._before = null;
      this._dirty = false;
      this._moved = false;
    },

    _clearOver: function () {
      items(this.el).forEach(function (el) {
        el.classList.remove(OVER);
      });
    },

    _place: function (item, target, after) {
      this._moved = true;
      this.el.insertBefore(item, after ? target.nextSibling : target);
    },

    // Moving a focused node blurs it (Chrome fires focusout on the removal),
    // so a move and the refocus that follows are one step the focusout
    // handler must not mistake for the reader leaving.
    _focus: function (item) {
      this._moving = true;
      item.focus();
      this._moving = false;
    },

    _move: function (item, target, after) {
      this._moving = true;
      this._place(item, target, after);
      item.focus();
      this._moving = false;
    },

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;

      function isAfter(target, e) {
        var r = target.getBoundingClientRect();
        return e.clientX > r.left + r.width / 2;
      }

      var h = {
        dragstart: function (e) {
          if (isDisabled(root)) return;
          var item = itemOf(root, e.target);
          if (!item) return;
          self._drag = item;
          self._before = items(root);
          self._dirty = false;
          self._moved = false;
          item.classList.add(DRAGGING);
          if (e.dataTransfer) {
            e.dataTransfer.effectAllowed = "move";
            try {
              // Firefox starts no drag without data.
              e.dataTransfer.setData("text/plain", item.getAttribute("data-id") || "");
            } catch (_err) {
              // Some browsers refuse setData outside a user gesture; the drag still works.
            }
          }
        },

        dragover: function (e) {
          if (!self._drag) return;
          e.preventDefault();
          if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
          self._clearOver();
          var target = itemOf(root, e.target);
          if (target && target !== self._drag) target.classList.add(OVER);
        },

        dragleave: function (e) {
          if (self._drag && !root.contains(e.relatedTarget)) self._clearOver();
        },

        drop: function (e) {
          if (!self._drag) return;
          e.preventDefault();
          var target = itemOf(root, e.target);
          var item = self._drag;
          self._clearOver();
          // A moved node may never get its dragend (Firefox), so finish here.
          item.classList.remove(DRAGGING);
          self._drag = null;
          if (target && target !== item && item.parentNode === root) {
            self._place(item, target, isAfter(target, e));
            self._commit();
            announce(
              labelOf(item) + " dropped at position " +
                (items(root).indexOf(item) + 1) + " of " + items(root).length
            );
          } else {
            self._before = null;
          }
        },

        dragend: function () {
          if (self._drag) self._drag.classList.remove(DRAGGING);
          self._drag = null;
          self._before = null;
          self._clearOver();
        },

        focusin: function (e) {
          var item = itemOf(root, e.target);
          if (!item || isDisabled(root)) return;
          self._activeId = item.getAttribute("data-id");
          items(root).forEach(function (el) {
            el.setAttribute("tabindex", el === item ? "0" : "-1");
          });
        },

        focusout: function (e) {
          if (self._grab && !self._moving && !root.contains(e.relatedTarget)) {
            self._cancelGrab();
          }
        },

        keydown: function (e) {
          if (isDisabled(root) || e.altKey || e.ctrlKey || e.metaKey) return;
          var item = itemOf(root, e.target);
          if (!item || e.target !== item) return;
          var list = items(root);
          var index = list.indexOf(item);
          var key = e.key;
          var cols = parseInt(root.getAttribute("data-columns"), 10) || 1;
          var step = 0;
          if (key === "ArrowLeft") step = -1;
          else if (key === "ArrowRight") step = 1;
          else if (key === "ArrowUp") step = -cols;
          else if (key === "ArrowDown") step = cols;
          var toEdge = key === "Home" ? -index : key === "End" ? list.length - 1 - index : null;
          var grabbed = self._grab === item;

          if (key === " " || key === "Enter") {
            e.preventDefault();
            if (grabbed) self._dropGrab(item);
            else self._startGrab(item);
            return;
          }
          if (key === "Escape" && grabbed) {
            e.preventDefault();
            self._cancelGrab();
            return;
          }
          var delta = step || toEdge;
          if (!delta) return;
          e.preventDefault();
          var next = Math.max(0, Math.min(list.length - 1, index + delta));
          if (next === index) return;
          delta = next - index;
          if (!grabbed) {
            self._focus(list[next]);
            return;
          }
          self._move(item, list[next], delta > 0);
          announce(
            labelOf(item) + " moved to position " + (next + 1) + " of " + list.length
          );
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

    _startGrab: function (item) {
      this._grab = item;
      this._before = items(this.el);
      this._dirty = false;
      this._moved = false;
      item.classList.add(DRAGGING);
      var list = this._before;
      announce(
        labelOf(item) + " grabbed, position " + (list.indexOf(item) + 1) + " of " +
          list.length + ". Use the arrow keys to move, Enter to drop, Escape to cancel."
      );
    },

    _dropGrab: function (item) {
      var list = items(this.el);
      this._grab = null;
      item.classList.remove(DRAGGING);
      this._commit();
      announce(
        labelOf(item) + " dropped at position " + (list.indexOf(item) + 1) + " of " + list.length
      );
    },

    _cancelGrab: function () {
      var item = this._grab;
      this._grab = null;
      if (!item) return;
      item.classList.remove(DRAGGING);
      var changed = this._dirty;
      this._restore();
      this._focus(item);
      announce(
        changed
          ? "Reorder cancelled, the list was updated"
          : "Reorder cancelled, " + labelOf(item) + " is back at its original position"
      );
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.SortableGrid && !window.DjustHooks.SortableGrid) {
    window.DjustHooks.SortableGrid = sortableGrid;
  }
})();
