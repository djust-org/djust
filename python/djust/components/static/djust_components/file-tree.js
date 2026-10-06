/**
 * FileTree — expand/collapse and keyboard navigation for the FileTree
 * component (dj-hook="FileTree").
 *
 * The tree is rendered by the server (a folder row followed by a sibling
 * ``.dj-file-tree__children`` block); this makes it a WAI-ARIA tree without
 * changing that structure, so a server re-render still patches cleanly:
 *
 * - click a folder, or its arrow, to expand or collapse it. The state is the
 *   reader's own and lives on the page: it is not sent to the server. It is
 *   remembered by the folder's NAME PATH (its name and its parents' names), not
 *   by its position, so a re-render that inserts, removes or reorders rows
 *   leaves each folder as the reader set it. The server's own open/closed
 *   markup applies to every folder the reader has not touched (and again once
 *   the reader's choice matches it): the hook remembers the server's
 *   ``display:none`` per children block, writes the reader's choice as an
 *   inline display itself (so no stylesheet is needed), and marks a block the
 *   reader changed with ``data-dj-open``. The same keys keep the roving tab
 *   stop and keyboard focus on the same row across a re-render;
 * - one tab stop (roving tabindex). Up/Down move through the visible rows,
 *   Right expands a folder or steps into it, Left collapses it or steps out to
 *   its parent, Home/End jump, typing a letter jumps to the next row starting
 *   with it, Enter/Space opens a folder or activates a file;
 * - activating a file clicks the row, so it fires exactly the event the
 *   server rendered for it (``event``); the hook sends nothing of its own;
 * - rows get role=treeitem with aria-level, aria-expanded and aria-selected,
 *   and each children block role=group.
 *
 * A large tree is cheap: interactions are delegated from the root, and the
 * attributes are applied again only when the tree's own markup changed.
 *
 * An app's own ``FileTree`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var NODE = "dj-file-tree__node";
  var GROUP = "dj-file-tree__children";
  var TOGGLE = "dj-file-tree__toggle";
  var ICON = "dj-file-tree__icon";
  var NAME = "dj-file-tree__name";
  var SELECTED = "dj-file-tree__node--selected";
  var FOLDER_CLOSED = "📁";
  var FOLDER_OPEN = "📂";
  var TYPEAHEAD_MS = 600;
  var OPEN_ATTR = "data-dj-open";
  var SEP = "\u0001";
  var DUP = "\u0000";

  function isNode(el) {
    return !!el && el.nodeType === 1 && el.classList.contains(NODE);
  }

  // A folder row is followed by its children block.
  function groupOf(node) {
    var next = node.nextElementSibling;
    return next && next.classList.contains(GROUP) ? next : null;
  }

  function toggleOf(node) {
    for (var i = 0; i < node.children.length; i++) {
      if (node.children[i].classList.contains(TOGGLE)) return node.children[i];
    }
    return null;
  }

  function isFolder(node) {
    return !!groupOf(node) && !!toggleOf(node);
  }

  // What the server last rendered for a children block (open or not): a
  // collapsed folder carries an inline ``display:none``. The hook writes the
  // inline display itself, so it remembers the server's value per element (in
  // JavaScript, not on the page) and learns a new one when a server patch
  // changes the block's style.
  var SERVER_OPEN = new WeakMap();

  function serverOpen(group) {
    if (!SERVER_OPEN.has(group)) SERVER_OPEN.set(group, group.style.display !== "none");
    return SERVER_OPEN.get(group);
  }

  // What is shown: the inline display (the reader's choice where there is one,
  // the server's otherwise).
  function isExpanded(node) {
    var group = groupOf(node);
    return !!group && group.style.display !== "none";
  }

  function rowName(node) {
    var name = node.getAttribute("data-name");
    return name === null ? nameOf(node) : name;
  }

  // Every row under ``container``, in document order, with its depth and its
  // name path. Rows inside a collapsed folder are skipped unless ``all`` is
  // set. A path segment is the row's name, plus an occurrence number when a
  // sibling before it has the same name.
  function walk(container, depth, all, fn, prefix) {
    var kids = container.children;
    var seen = null;
    for (var i = 0; i < kids.length; i++) {
      var node = kids[i];
      if (!isNode(node)) continue;
      var name = rowName(node);
      seen = seen || Object.create(null);
      var occurrence = seen[name] || 0;
      seen[name] = occurrence + 1;
      var key = (prefix || "") + SEP + (occurrence ? name + DUP + occurrence : name);
      fn(node, depth, key);
      if (isFolder(node) && (all || isExpanded(node))) {
        walk(groupOf(node), depth + 1, all, fn, key);
      }
    }
  }

  // The same key for one row, from its position among its siblings.
  function pathKey(node) {
    var segments = [];
    var cur = node;
    while (isNode(cur)) {
      var name = rowName(cur);
      var occurrence = 0;
      for (var p = cur.previousElementSibling; p; p = p.previousElementSibling) {
        if (isNode(p) && rowName(p) === name) occurrence += 1;
      }
      segments.push(occurrence ? name + DUP + occurrence : name);
      var group = cur.parentElement;
      if (!group || !group.classList.contains(GROUP)) break;
      cur = group.previousElementSibling;
    }
    return SEP + segments.reverse().join(SEP);
  }

  // The row with this key, or null.
  function findByKey(root, key) {
    var segments = key.split(SEP).slice(1);
    var container = root;
    var found = null;
    for (var s = 0; s < segments.length; s++) {
      var want = segments[s];
      found = null;
      var seen = Object.create(null);
      var kids = container.children;
      for (var i = 0; i < kids.length; i++) {
        var node = kids[i];
        if (!isNode(node)) continue;
        var name = rowName(node);
        var occurrence = seen[name] || 0;
        seen[name] = occurrence + 1;
        if ((occurrence ? name + DUP + occurrence : name) === want) {
          found = node;
          break;
        }
      }
      if (!found) return null;
      if (s < segments.length - 1) {
        container = groupOf(found);
        if (!container) return null;
      }
    }
    return found;
  }

  // Whether every folder above the row is open.
  function shown(node) {
    for (var cur = parentRow(node); cur; cur = parentRow(cur)) {
      if (!isExpanded(cur)) return false;
    }
    return true;
  }

  function visibleRows(root) {
    var rows = [];
    walk(root, 0, false, function (node) {
      rows.push(node);
    });
    return rows;
  }

  function parentRow(node) {
    var group = node.parentElement;
    if (!group || !group.classList.contains(GROUP)) return null;
    var prev = group.previousElementSibling;
    return isNode(prev) ? prev : null;
  }

  function nameOf(node) {
    var el = null;
    for (var i = 0; i < node.children.length; i++) {
      if (node.children[i].classList.contains(NAME)) el = node.children[i];
    }
    return ((el || node).textContent || "").trim().toLowerCase();
  }

  function setAttr(el, name, value) {
    if (el.getAttribute(name) !== value) el.setAttribute(name, value);
  }

  var fileTree = {
    mounted: function () {
      this._open = new Map();
      this._bind();
      this._sync();
    },

    // A server patch may shift rows under the reader's focus and tab stop:
    // note where they are, by name path, before it lands.
    beforeUpdate: function () {
      var active = document.activeElement;
      this._focusKey = isNode(active) && this.el.contains(active) ? pathKey(active) : null;
      this._activeKey = this._active && this.el.contains(this._active) ? pathKey(this._active) : null;
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
        this._dirty = true;
      }
      // Anything that changed the tree reached the observer (its callback runs
      // after this synchronous patch, so ask for the pending records); nothing
      // else costs a walk, however many patches the page receives.
      if (this._observer) {
        var pending = this._observer.takeRecords();
        if (pending.length) {
          this._noteRecords(pending);
          this._dirty = true;
        }
      }
      if (this._dirty) this._sync();
      this._refocus();
    },

    // Put keyboard focus back on the row it was on, by path, if the patch
    // dropped it or left it on a different row; failing that, on the nearest
    // folder above it that is still shown.
    _refocus: function () {
      var key = this._focusKey;
      this._focusKey = null;
      if (key === null || key === undefined) return;
      var root = this.el;
      var active = document.activeElement;
      // Lost: nothing has focus, or focus is on an element of the tree that a
      // patch turned into something else. Moved: a row that now holds another path.
      var lost = !active || active === document.body || (root.contains(active) && !isNode(active));
      var moved = isNode(active) && root.contains(active) && pathKey(active) !== key;
      if (!lost && !moved) return;
      var target = findByKey(root, key);
      while (key.indexOf(SEP) !== key.lastIndexOf(SEP) && (!target || !shown(target))) {
        key = key.slice(0, key.lastIndexOf(SEP));
        target = findByKey(root, key);
      }
      if (!target || !shown(target)) target = visibleRows(root)[0];
      if (target) this._focus(target);
    },

    destroyed: function () {
      this._unbind();
    },

    // Reflect each folder's state (the children block's visibility is the
    // truth) and the ARIA tree semantics onto the rows, and keep one tab stop.
    _sync: function () {
      var root = this.el;
      var self = this;
      this._dirty = false;
      // The tab stop stays on the same row by name path across a patch.
      var activeKey = this._activeKey;
      this._activeKey = null;
      var active = !activeKey && this._active && root.contains(this._active) ? this._active : null;
      var firstRow = null;
      var selectedRow = null;
      var keyed = null;
      var rows = [];
      var seenFolders = this._open.size > 1000 ? new Set() : null;
      setAttr(root, "role", "tree");
      walk(root, 0, true, function (node, depth, key) {
        rows.push(node);
        if (!firstRow) firstRow = node;
        if (!selectedRow && node.classList.contains(SELECTED)) selectedRow = node;
        if (activeKey && !keyed && key === activeKey) keyed = node;
        setAttr(node, "role", "treeitem");
        setAttr(node, "aria-level", String(depth + 1));
        setAttr(node, "aria-selected", node.classList.contains(SELECTED) ? "true" : "false");
        if (isFolder(node)) {
          if (seenFolders) seenFolders.add(key);
          self._apply(node, key);
        }
      });
      if (seenFolders) {
        // A long-lived tree whose folders come and go does not keep every
        // choice the reader ever made.
        this._open.forEach(function (_open, key) {
          if (!seenFolders.has(key)) self._open.delete(key);
        });
      }
      var tab = keyed || active || selectedRow || firstRow;
      this._active = tab;
      rows.forEach(function (node) {
        setAttr(node, "tabindex", node === tab ? "0" : "-1");
      });
      if (this._observer) this._observer.takeRecords();
    },

    // Make a folder row agree with what is shown: the reader's choice for its
    // name path if there is one (carried on the children block), the server's
    // markup otherwise.
    _apply: function (node, key) {
      var group = groupOf(node);
      var server = serverOpen(group);
      var chosen = this._open.get(key);
      if (chosen !== undefined && chosen === server) {
        // The reader's choice now agrees with the server; follow the server again.
        this._open.delete(key);
        chosen = undefined;
      }
      var open = chosen === undefined ? server : chosen;
      var want = open ? "" : "none";
      // The style attribute is left in place even when empty: a server patch
      // that removes it must still reach the observer (removing an attribute
      // that is not there leaves no record), and that is how the hook learns
      // the server's new value.
      if (group.style.display !== want) group.style.display = want;
      if (chosen === undefined) {
        if (group.hasAttribute(OPEN_ATTR)) group.removeAttribute(OPEN_ATTR);
      } else {
        setAttr(group, OPEN_ATTR, chosen ? "true" : "false");
      }
      this._reflect(node);
    },

    // A server patch that rewrote a children block's style tells us what the
    // server now wants for it. (Records of the hook's own writes are always
    // discarded with takeRecords(), so what is left is the server's.)
    _noteRecords: function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === "attributes" && r.attributeName === "style" && r.target.classList &&
            r.target.classList.contains(GROUP)) {
          SERVER_OPEN.set(r.target, r.target.style.display !== "none");
        }
      }
    },

    // The ARIA state, arrow and icon of a folder row for what is shown.
    _reflect: function (node) {
      var group = groupOf(node);
      var open = isExpanded(node);
      node.classList.toggle("dj-file-tree__node--expanded", open);
      setAttr(node, "aria-expanded", open ? "true" : "false");
      setAttr(group, "role", "group");
      var toggle = toggleOf(node);
      if (toggle) {
        setAttr(toggle, "aria-expanded", open ? "true" : "false");
        // The row is the control; the arrow is only a click target.
        setAttr(toggle, "tabindex", "-1");
        setAttr(toggle, "aria-hidden", "true");
        var glyph = open ? "▼" : "▶";
        if (toggle.textContent !== glyph) toggle.textContent = glyph;
      }
      for (var i = 0; i < node.children.length; i++) {
        var icon = node.children[i];
        if (icon.classList.contains(ICON)) {
          var folder = open ? FOLDER_OPEN : FOLDER_CLOSED;
          if (icon.textContent !== folder) icon.textContent = folder;
        }
      }
    },

    _setExpanded: function (node, open) {
      var group = groupOf(node);
      if (!group || isExpanded(node) === open) return;
      var key = pathKey(node);
      this._open.set(key, open);
      this._apply(node, key);
      if (!open) {
        var active = document.activeElement;
        if (active && group.contains(active)) this._focus(node);
        if (this._active && group.contains(this._active)) this._rove(node);
      }
      if (this._observer) this._observer.takeRecords();
    },

    _toggle: function (node) {
      this._setExpanded(node, !isExpanded(node));
    },

    _rove: function (node) {
      if (this._active && this._active !== node && this.el.contains(this._active)) {
        setAttr(this._active, "tabindex", "-1");
      }
      this._active = node;
      setAttr(node, "tabindex", "0");
      if (this._observer) this._observer.takeRecords();
    },

    _focus: function (node) {
      if (!node) return;
      this._rove(node);
      node.focus();
    },

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;
      var typed = "";
      var typedAt = 0;

      var h = {
        click: function (e) {
          var node = e.target && e.target.closest ? e.target.closest("." + NODE) : null;
          if (!node || !root.contains(node)) return;
          self._rove(node);
          if (isFolder(node)) self._toggle(node);
        },

        focusin: function (e) {
          var node = e.target;
          if (isNode(node) && root.contains(node)) self._rove(node);
        },

        keydown: function (e) {
          var node = e.target;
          if (!isNode(node) || !root.contains(node)) return;
          if (e.altKey || e.ctrlKey || e.metaKey) return;
          var key = e.key;
          var folder = isFolder(node);
          var rows;

          if (key === "ArrowDown" || key === "ArrowUp") {
            e.preventDefault();
            rows = visibleRows(root);
            var at = rows.indexOf(node);
            self._focus(rows[at + (key === "ArrowDown" ? 1 : -1)]);
          } else if (key === "ArrowRight") {
            e.preventDefault();
            if (folder && !isExpanded(node)) self._setExpanded(node, true);
            else if (folder) self._focus(groupOf(node).querySelector("." + NODE));
          } else if (key === "ArrowLeft") {
            e.preventDefault();
            if (folder && isExpanded(node)) self._setExpanded(node, false);
            else self._focus(parentRow(node));
          } else if (key === "Home" || key === "End") {
            e.preventDefault();
            rows = visibleRows(root);
            self._focus(key === "Home" ? rows[0] : rows[rows.length - 1]);
          } else if (key === "Enter" || key === " ") {
            e.preventDefault();
            if (folder) self._toggle(node);
            else node.click();
          } else if (key.length === 1) {
            var now = Date.now();
            typed = now - typedAt > TYPEAHEAD_MS ? key.toLowerCase() : typed + key.toLowerCase();
            typedAt = now;
            rows = visibleRows(root);
            var from = rows.indexOf(node);
            // The same letter again moves on; a longer prefix stays put.
            var start = typed.length > 1 ? from : from + 1;
            for (var i = 0; i < rows.length; i++) {
              var candidate = rows[(start + i) % rows.length];
              if (nameOf(candidate).indexOf(typed) === 0) {
                self._focus(candidate);
                break;
              }
            }
          }
        },
      };
      this._h = h;
      Object.keys(h).forEach(function (type) {
        root.addEventListener(type, h[type]);
      });

      if (window.MutationObserver) {
        this._observer = new MutationObserver(function (records) {
          self._noteRecords(records);
          self._dirty = true;
        });
        this._observer.observe(root, {
          childList: true,
          subtree: true,
          // A rename or a swap that rewrites names (and their text) in place
          // moves a folder's name path without changing the tree's shape.
          characterData: true,
          attributes: true,
          // The state the hook reads (class, style, data-name) and what it
          // writes (a morph that restores the server's markup resets these,
          // and then the next patch must apply them again).
          attributeFilter: ["class", "style", "data-name", "tabindex", "role", "aria-level", "aria-selected", "aria-expanded", OPEN_ATTR],
        });
      }
    },

    _unbind: function () {
      var root = this._boundEl;
      var h = this._h;
      if (root && h) {
        Object.keys(h).forEach(function (type) {
          root.removeEventListener(type, h[type]);
        });
      }
      if (this._observer) this._observer.disconnect();
      this._observer = null;
      this._h = null;
      this._boundEl = null;
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.FileTree && !window.DjustHooks.FileTree) {
    window.DjustHooks.FileTree = fileTree;
  }
})();
