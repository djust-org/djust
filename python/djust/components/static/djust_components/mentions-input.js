/**
 * MentionsInput — the @-mention combobox for the MentionsInput component
 * (dj-hook="MentionsInput").
 *
 * Type ``@`` (at the start of the text or after a space) and the people the
 * component was given in ``users`` are offered, filtered as you type by the
 * start of their name or of any word of it. There is no server lookup: the
 * list is the rendered ``users``, so an app with a very large list should
 * narrow it before rendering.
 *
 * Keyboard (the ARIA combobox pattern: focus stays in the input, the active
 * suggestion is aria-activedescendant): ArrowDown / ArrowUp move through the
 * suggestions, Enter or Tab inserts the active one as ``@Name `` and records
 * who it was, Escape closes the list. Click a suggestion to insert it too.
 *
 * Submitting: Enter, when no suggestion is being chosen, sends the component's
 * ``event`` with
 *
 *     {text: "Thanks @Alice!", mentions: ["1"], value: "Thanks @Alice!",
 *      field: "message", key: "Enter", code: "Enter"}
 *
 * ``mentions`` are the ``id``s of the people whose ``@Name`` token (the one
 * inserted for them) is still in the text, in the order they appear, without
 * repeats: tokens are tracked by position through every edit, so two people
 * with the same name are told apart, and a token that is edited, cut or
 * overtyped is dropped. ``value``, ``field``, ``key`` and ``code`` are
 * what the plain ``dj-keydown.enter`` input event sends and are kept so an
 * existing handler keeps working; ``text`` is the same string as ``value``.
 * Everything in the payload is client-supplied and therefore untrusted:
 * validate ``mentions`` against the people this user may mention (the ids of
 * ``users``), and ``text`` for length, on the server.
 *
 * Without this script the input still sends the plain ``dj-keydown.enter``
 * event, and the suggestion list stays hidden.
 *
 * The suggestion rows are the ones the server rendered (the hook hides the
 * ones that do not match and marks the active one; it never reorders, adds or
 * removes rows), so a re-render that changes ``users`` is patched in place as
 * usual and the hook re-derives what is shown from the rows and the text.
 *
 * An app's own ``MentionsInput`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var INPUT = ".dj-mentions__input";
  var LIST = ".dj-mentions__dropdown";
  var ITEM = ".dj-mentions__item";
  var MAX_SHOWN = 8;
  var MAX_QUERY = 30;
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

  // The routing context a plain dj-* event carries: the nearest LiveComponent
  // (data-component-id) and embedded child view (data-djust-embedded) above
  // the element. Without it an event sent from inside either would reach the
  // page view instead.
  function addContext(params, el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      var ds = node.dataset || {};
      if (params.component_id === undefined && ds.componentId) params.component_id = ds.componentId;
      if (params.view_id === undefined && ds.djustEmbedded) params.view_id = ds.djustEmbedded;
    }
  }

  // Same public entry point dj-click and dj-viewport use, so the event works
  // over WebSocket, SSE and HTTP-only, honours strict parameter contracts,
  // reaches the right view when several are mounted, and carries the same
  // component_id / view_id routing a plain binding on this element would.
  // Falls back to the hook's own pushEvent when the client API is absent.
  function send(hook, element, eventName, params) {
    var d = window.djust;
    if (d && typeof d.handleEvent === "function") {
      var sent = params;
      if (typeof d._strictBinding === "function") {
        var strict = d._strictBinding(element, eventName, params, [], element);
        if (strict === false) return;
        if (strict) sent = strict;
      }
      if (sent === params) addContext(sent, element);
      if (typeof d._markSlotOf === "function") d._markSlotOf(sent, element);
      d.handleEvent(eventName, sent);
    } else if (typeof hook.pushEvent === "function") {
      addContext(params, element);
      hook.pushEvent(eventName, params);
    }
  }

  // Where a text edit happened: the part before ``start`` and after
  // ``removedEnd`` (in the old text) is unchanged, ``delta`` is how much the
  // text grew. The caret after the edit says where it ended, which settles the
  // ambiguity of repeated text ("@Bob @Bob" with the first one deleted).
  function editOf(before, after, caret) {
    var tail = typeof caret === "number" ? after.length - caret : -1;
    if (tail < 0 || tail > before.length || before.slice(before.length - tail) !== after.slice(after.length - tail)) {
      tail = 0;
      var limit = Math.min(before.length, after.length);
      while (tail < limit && before.charAt(before.length - 1 - tail) === after.charAt(after.length - 1 - tail)) tail++;
    }
    var head = 0;
    var room = Math.min(before.length - tail, after.length - tail);
    while (head < room && before.charAt(head) === after.charAt(head)) head++;
    return { start: head, removedEnd: before.length - tail, delta: after.length - before.length };
  }

  function isWordChar(ch) {
    return !!ch && /[\p{L}\p{N}_]/u.test(ch);
  }

  // The mention being typed: an "@" at the start of the text or after
  // whitespace, then up to MAX_QUERY characters without whitespace, ending at
  // the caret. null when the caret is not in one.
  function triggerAt(input) {
    var caret = input.selectionStart;
    if (caret === null || caret === undefined || input.selectionEnd !== caret) return null;
    var value = input.value;
    for (var i = caret - 1; i >= 0 && caret - i - 1 <= MAX_QUERY; i--) {
      var ch = value.charAt(i);
      if (/\s/.test(ch)) return null;
      if (ch === "@") {
        if (i > 0 && !/\s/.test(value.charAt(i - 1))) return null;
        return { at: i, caret: caret, query: value.slice(i + 1, caret) };
      }
    }
    return null;
  }

  // Rows are matched by who they are, not by node: a patch can hand a node to
  // another person.
  function keyOf(row) {
    return (row.getAttribute("data-user-id") || "") + "\u0000" + (row.getAttribute("data-user-name") || "");
  }

  function wordStarts(name, query) {
    var q = query.toLowerCase();
    if (!q) return true;
    return name
      .toLowerCase()
      .split(/\s+/)
      .some(function (word) {
        return word.indexOf(q) === 0;
      });
  }

  var mentionsInput = {
    mounted: function () {
      this._id = ++nextId;
      // The people chosen so far: {id, name, start}, ``start`` being where the
      // "@Name" token is in the text. Kept in step with every edit (_track).
      this._mentions = [];
      this._prev = "";
      this._open = false;
      this._activeId = null;
      this._bind();
      this._enhance();
    },

    // The server re-rendered: the rows may be different people now. What is
    // shown is derived again from the rows and the text.
    updated: function () {
      if (this._boundEl !== this.el) {
        this._unbind();
        this._bind();
      }
      this._enhance();
      if (this._open) this._refresh();
    },

    destroyed: function () {
      this._unbind();
    },

    _input: function () {
      return this.el.querySelector(INPUT);
    },

    _list: function () {
      return this.el.querySelector(LIST);
    },

    _rows: function () {
      var list = this._list();
      return list ? Array.prototype.slice.call(list.querySelectorAll(ITEM)) : [];
    },

    _disabled: function () {
      var input = this._input();
      return !input || input.disabled || this.el.classList.contains("dj-mentions--disabled");
    },

    // ARIA combobox wiring. Idempotent, re-applied after every patch.
    _enhance: function () {
      var input = this._input();
      var list = this._list();
      if (!input || !list) return;
      var listId = list.id || "dj-mentions-" + this._id + "-list";
      if (list.id !== listId) list.id = listId;
      if (!list.hasAttribute("aria-label") && !list.hasAttribute("aria-labelledby")) {
        list.setAttribute("aria-label", "Mention a person");
      }
      var attrs = {
        role: "combobox",
        "aria-autocomplete": "list",
        "aria-haspopup": "listbox",
        "aria-controls": listId,
        "aria-expanded": this._open ? "true" : "false",
      };
      Object.keys(attrs).forEach(function (name) {
        if (input.getAttribute(name) !== attrs[name]) input.setAttribute(name, attrs[name]);
      });
      var self = this;
      this._rows().forEach(function (row, i) {
        var id = "dj-mentions-" + self._id + "-opt-" + i;
        if (row.id !== id) row.id = id;
      });
    },

    // ---- events ----------------------------------------------------------

    _bind: function () {
      var self = this;
      var root = this.el;
      this._boundEl = root;
      var h = {
        // The text as it is just before an edit (typing, paste, cut, drop, IME).
        beforeinput: function (e) {
          if (e.target === self._input()) self._prev = e.target.value;
        },
        input: function (e) {
          if (e.target !== self._input()) return;
          self._track();
          self._refresh();
        },
        keyup: function (e) {
          if (e.target !== self._input()) return;
          var k = e.key;
          if (k === "ArrowLeft" || k === "ArrowRight" || k === "Home" || k === "End") self._refresh();
        },
        click: function (e) {
          var row = e.target && e.target.closest ? e.target.closest(ITEM) : null;
          if (row && root.contains(row)) {
            if (self._disabled()) return;
            self._choose(row);
            return;
          }
          if (e.target === self._input()) self._refresh();
        },
        // Keep focus (and the caret) in the input while a row is pressed.
        mousedown: function (e) {
          if (e.target && e.target.closest && e.target.closest(ITEM)) e.preventDefault();
        },
        focusout: function (e) {
          if (!root.contains(e.relatedTarget)) self._close();
        },
        keydown: function (e) {
          if (e.target !== self._input() || self._disabled()) return;
          if (e.isComposing || e.keyCode === 229) {
            // Choosing a candidate with Enter ends the composition; it is not a
            // submit. djust's own Enter binding has no composition guard, so stop
            // the key here (without preventDefault: the composition must commit).
            if (e.key === "Enter") e.stopPropagation();
            return;
          }
          self._keydown(e);
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

    _keydown: function (e) {
      var key = e.key;
      var plain = !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey;
      if (this._open) {
        if (key === "ArrowDown" || key === "ArrowUp") {
          e.preventDefault();
          this._move(key === "ArrowDown" ? 1 : -1);
          return;
        }
        if ((key === "Enter" && !e.altKey && !e.ctrlKey && !e.metaKey) || (key === "Tab" && plain)) {
          var row = this._activeRow();
          if (row) {
            e.preventDefault();
            // The plain Enter binding would send the half-written text.
            e.stopPropagation();
            this._choose(row);
            return;
          }
        }
        if (key === "Escape") {
          e.preventDefault(); // handled: djust's own handlers leave it alone
          this._close();
          return;
        }
      } else if (key === "ArrowDown" && plain && triggerAt(this._input())) {
        e.preventDefault();
        this._refresh();
        return;
      }
      if (key === "Enter" && !e.altKey && !e.ctrlKey && !e.metaKey && this._input().getAttribute("dj-keydown.enter")) {
        e.preventDefault();
        // This submit replaces the plain Enter binding, which would send the
        // same text without the mentions.
        e.stopPropagation();
        this._submit(e);
      }
    },

    // ---- suggestions -----------------------------------------------------

    _matches: function (query) {
      var shown = [];
      this._rows().forEach(function (row) {
        var match = shown.length < MAX_SHOWN && wordStarts(row.getAttribute("data-user-name") || "", query);
        if (match) shown.push(row);
      });
      return shown;
    },

    // Show what matches what is being typed, or close.
    _refresh: function () {
      var input = this._input();
      var trigger = input && !this._disabled() ? triggerAt(input) : null;
      if (!trigger) {
        this._close();
        return;
      }
      var shown = this._matches(trigger.query);
      this._rows().forEach(function (row) {
        if (shown.indexOf(row) === -1) row.style.display = "none";
        else row.style.removeProperty("display");
      });
      if (!shown.length) {
        var was = this._open;
        this._close();
        if (was || trigger.query) announce("No matching people");
        return;
      }
      var wasOpen = this._open;
      var activeRow = shown.filter(function (r) {
        return keyOf(r) === this._activeId;
      }, this)[0];
      this._open = true;
      this._shown = shown;
      this._list().style.display = "block";
      this._setActive(activeRow || shown[0]);
      this._enhance();
      var count = shown.length;
      if (!wasOpen || count !== this._announced) {
        announce(count + (count === 1 ? " person" : " people") + " to mention");
        this._announced = count;
      }
    },

    _close: function () {
      var input = this._input();
      var list = this._list();
      if (list) list.style.removeProperty("display");
      this._rows().forEach(function (row) {
        row.style.removeProperty("display");
        row.removeAttribute("aria-selected");
      });
      this._open = false;
      this._shown = [];
      this._activeId = null;
      this._announced = -1;
      if (input) {
        input.removeAttribute("aria-activedescendant");
        if (input.getAttribute("aria-expanded") !== "false") input.setAttribute("aria-expanded", "false");
      }
    },

    _activeRow: function () {
      var id = this._activeId;
      return (this._shown || []).filter(function (r) {
        return keyOf(r) === id;
      })[0];
    },

    _setActive: function (row) {
      var input = this._input();
      this._activeId = keyOf(row);
      this._rows().forEach(function (r) {
        if (r === row) r.setAttribute("aria-selected", "true");
        else r.removeAttribute("aria-selected");
      });
      if (row.id) input.setAttribute("aria-activedescendant", row.id);
      if (row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
    },

    _move: function (step) {
      var shown = this._shown || [];
      if (!shown.length) return;
      var current = shown.indexOf(this._activeRow());
      var next = (current + step + shown.length) % shown.length;
      this._setActive(shown[next]);
    },

    // ---- inserting and sending -------------------------------------------

    _choose: function (row) {
      var input = this._input();
      var trigger = triggerAt(input);
      var id = row.getAttribute("data-user-id") || "";
      var name = row.getAttribute("data-user-name") || "";
      if (!trigger || !name) {
        this._close();
        return;
      }
      var token = "@" + name + " ";
      input.focus();
      this._prev = input.value;
      input.setSelectionRange(trigger.at, trigger.caret);
      // An edit the browser makes itself joins its undo stack (undo takes the
      // name back and leaves what was typed); setRangeText does not.
      var done = false;
      if (typeof document.execCommand === "function") {
        try {
          done = document.execCommand("insertText", false, token);
        } catch (_err) {
          done = false;
        }
      }
      if (!done || input.value.slice(trigger.at, trigger.at + token.length) !== token) {
        this._prev = input.value;
        input.setRangeText(token, trigger.at, trigger.caret, "end");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      this._mentions.push({ id: id, name: name, start: trigger.at });
      this._close();
      announce(name + " mentioned");
    },

    // Move every recorded mention with the edit that just happened: unchanged
    // before it, shifted after it, forgotten if the edit touched it.
    _track: function () {
      var input = this._input();
      var now = input.value;
      var before = this._prev;
      this._prev = now;
      if (before === now || !this._mentions.length) return;
      var edit = editOf(before, now, input.selectionStart);
      this._mentions = this._mentions.filter(function (m) {
        var end = m.start + 1 + m.name.length;
        if (end <= edit.start) return true;
        if (m.start >= edit.removedEnd) {
          m.start += edit.delta;
          return true;
        }
        return false;
      });
    },

    // The ids of the people whose "@Name" token is still where it was put, in
    // the order they appear in the text, each once. People are told apart by
    // where their token is, not by name: two people called Alex are two tokens.
    _mentioned: function (text) {
      var seen = Object.create(null);
      return this._mentions
        .filter(function (m) {
          var token = "@" + m.name;
          return (
            text.substr(m.start, token.length) === token &&
            (m.start === 0 || /\s/.test(text.charAt(m.start - 1))) &&
            !isWordChar(text.charAt(m.start + token.length))
          );
        })
        .sort(function (a, b) {
          return a.start - b.start;
        })
        .map(function (m) {
          return m.id;
        })
        .filter(function (id) {
          if (seen[id]) return false;
          seen[id] = true;
          return true;
        });
    },

    _submit: function (e) {
      var input = this._input();
      var eventName = input.getAttribute("dj-keydown.enter");
      if (!eventName) return;
      var text = input.value;
      var params = {
        text: text,
        mentions: this._mentioned(text),
        value: text,
        field: input.getAttribute("name") || "",
        key: e.key,
        code: e.code,
      };
      var d = window.djust;
      if (d && typeof d.collectDjValues === "function") {
        var extra = d.collectDjValues(input);
        Object.keys(extra).forEach(function (k) {
          if (!(k in params)) params[k] = extra[k];
        });
      }
      send(this, input, eventName, params);
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.MentionsInput && !window.DjustHooks.MentionsInput) {
    window.DjustHooks.MentionsInput = mentionsInput;
  }
})();
