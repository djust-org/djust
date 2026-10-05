/**
 * FileTree — expand/collapse and keyboard navigation for the FileTree
 * component (dj-hook="FileTree").
 *
 * The tree is rendered by the server (a folder row followed by a sibling
 * ``.dj-file-tree__children`` block); this makes it a WAI-ARIA tree without
 * changing that structure, so a server re-render still patches cleanly:
 *
 * - click a folder, or its arrow, to expand or collapse it. The state is the
 *   reader's own and lives on the page: it is not sent to the server. A
 *   re-render that leaves a folder's markup unchanged leaves it as the reader
 *   set it;
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
  var EXPANDED = "dj-file-tree__node--expanded";
  var SELECTED = "dj-file-tree__node--selected";
  var FOLDER_CLOSED = "📁";
  var FOLDER_OPEN = "📂";
  var TYPEAHEAD_MS = 600;

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

  function isExpanded(node) {
    var group = groupOf(node);
    return !!group && group.style.display !== "none";
  }

  // Every row under ``container``, in document order, with its depth. Rows
  // inside a collapsed folder are skipped unless ``all`` is set.
  function walk(container, depth, all, fn) {
    var kids = container.children;
    for (var i = 0; i < kids.length; i++) {
      var node = kids[i];
      if (!isNode(node)) continue;
      fn(node, depth);
      if (isFolder(node) && (all || isExpanded(node))) walk(groupOf(node), depth + 1, all, fn);
    }
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
      this._bind();
      this._sync();
    },

    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
        this._sync();
      }
      // Anything that changed the tree reached the observer (its callback runs
      // after this synchronous patch, so ask for the pending records); nothing
      // else costs a walk, however many patches the page receives.
      if (this._observer && this._observer.takeRecords().length) this._dirty = true;
      if (this._dirty) this._sync();
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
      var active = this._active && root.contains(this._active) ? this._active : null;
      var firstRow = null;
      var selectedRow = null;
      var rows = [];
      setAttr(root, "role", "tree");
      walk(root, 0, true, function (node, depth) {
        rows.push(node);
        if (!firstRow) firstRow = node;
        if (!selectedRow && node.classList.contains(SELECTED)) selectedRow = node;
        setAttr(node, "role", "treeitem");
        setAttr(node, "aria-level", String(depth + 1));
        setAttr(node, "aria-selected", node.classList.contains(SELECTED) ? "true" : "false");
        if (isFolder(node)) self._reflect(node);
      });
      var tab = active || selectedRow || firstRow;
      this._active = tab;
      rows.forEach(function (node) {
        setAttr(node, "tabindex", node === tab ? "0" : "-1");
      });
      if (this._observer) this._observer.takeRecords();
    },

    // Make a folder row agree with its children block.
    _reflect: function (node) {
      var group = groupOf(node);
      var open = group.style.display !== "none";
      node.classList.toggle(EXPANDED, open);
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
      group.style.display = open ? "" : "none";
      // The server renders ``style="display:none"``; an emptied style attribute
      // is the same as none at all.
      if (open && !group.getAttribute("style")) group.removeAttribute("style");
      this._reflect(node);
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
        this._observer = new MutationObserver(function () {
          self._dirty = true;
        });
        this._observer.observe(root, {
          childList: true,
          subtree: true,
          attributes: true,
          // The state the hook reads (class, style) and what it writes (a morph
          // that restores the server's markup resets these, and then the next
          // patch must apply them again).
          attributeFilter: ["class", "style", "tabindex", "role", "aria-level", "aria-selected", "aria-expanded"],
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
