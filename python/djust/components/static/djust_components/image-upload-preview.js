/**
 * ImageUploadPreview — the interaction layer of the ImageUploadPreview
 * component (dj-hook="ImageUploadPreview").
 *
 * The files travel through djust's own upload pipeline, never through
 * anything this hook invents: with an upload slot (data-upload) the input
 * carries dj-upload and the drop zone dj-upload-drop, and djust's client does
 * the chunking, the progress and the cancellation. This script adds what a
 * reader sees around it:
 *
 * - at once, a thumbnail per chosen or dropped image, from a browser object
 *   URL (the file is not read into memory or sent for the preview), with its
 *   name and size;
 * - UX pre-checks (type from data-accept / the input's accept, size from
 *   data-max-size, count from data-max). A file that fails one is listed with
 *   the reason and never sent. They only spare a wasted upload: the server
 *   enforces every limit, and a file it refuses is marked "not accepted";
 * - while a file uploads, a progress bar fed by djust's djust:upload:progress
 *   event and a Cancel button that calls window.djust.uploads.cancelUpload;
 * - Remove for a finished or refused thumbnail; with max 1 a new choice
 *   replaces the old image (and cancels it if it is still uploading);
 * - drag and drop onto the zone (the file input is a real, focusable control
 *   too: Enter or Space opens the picker);
 * - polite announcements of what was chosen, finished, refused or cancelled;
 * - data-event (only when the app named one): sent once when the files of a
 *   selection have finished uploading, with {count} of those that completed
 *   (untrusted: the browser says so). With no upload slot it is sent when
 *   files are chosen, with the number accepted.
 *
 * Object URLs are revoked when a thumbnail is removed or replaced, when the
 * server's own previews take over, and when the component goes away. Every
 * listener belongs to the hook instance and is removed in destroyed().
 *
 * An app's own ``ImageUploadPreview`` hook is never replaced: this registers
 * in window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var SLOT_ERRORS = /^Upload rejected/;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function num(value, fallback) {
    var n = parseInt(value, 10);
    return isFinite(n) && n >= 0 ? n : fallback;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  // Match the ancestry context carried by ordinary dj-* bindings.
  function addContext(params, el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      var ds = node.dataset || {};
      if (params.component_id === undefined && ds.componentId) params.component_id = ds.componentId;
      if (params.view_id === undefined && ds.djustEmbedded) params.view_id = ds.djustEmbedded;
    }
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

  // The accept attribute's tokens: ".png", "image/png", "image/*".
  function acceptTokens(accept) {
    return String(accept || "")
      .split(",")
      .map(function (t) {
        return t.trim().toLowerCase();
      })
      .filter(Boolean);
  }

  function matchesAccept(file, tokens) {
    if (!tokens.length) return true;
    var type = String(file.type || "").toLowerCase();
    var name = String(file.name || "").toLowerCase();
    // A file the browser could not type is left for the server to judge.
    if (!type && !/\.[a-z0-9]+$/.test(name)) return true;
    return tokens.some(function (t) {
      if (t.charAt(0) === ".") return name.slice(-t.length) === t;
      if (t.slice(-2) === "/*") return type.indexOf(t.slice(0, -1)) === 0;
      return type === t;
    });
  }

  function sameFile(a, b) {
    return a === b || (a.name === b.name && a.size === b.size && a.lastModified === b.lastModified);
  }

  var imageUploadPreview = {
    mounted: function () {
      this._bind();
    },

    // A server patch can replace the elements the hook fills, or finish the
    // reader's uploads by rendering the saved previews.
    updated: function () {
      if (this._root !== this.el) {
        this._unbind();
        this._bind();
        return;
      }
      this._claim();
      this._serverTookOver();
    },

    destroyed: function () {
      this._unbind();
    },

    // --- setup --------------------------------------------------------------

    _bind: function () {
      var self = this;
      var root = this.el;
      this._root = root;
      this._items = [];
      this._seq = 0;
      this._inFlight = 0;
      this._finished = 0;
      this._previewSig = this._signature();

      this._onChange = function (e) {
        if (e.target === self._input()) self._changed();
      };
      this._onDrop = function (e) {
        if (!self._zone() || !self._zone().contains(e.target)) return;
        self._dropped(e);
      };
      this._onDragOver = function (e) {
        if (!self._zone() || !self._zone().contains(e.target)) return;
        if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
        e.preventDefault();
        self._zone().classList.add("upload-dragover");
      };
      this._onDragLeave = function (e) {
        var zone = self._zone();
        if (!zone || zone.contains(e.relatedTarget)) return;
        zone.classList.remove("upload-dragover");
      };
      this._onClick = function (e) {
        var button = e.target.closest ? e.target.closest("button[data-action]") : null;
        if (button && self._list() && self._list().contains(button)) self._act(button);
      };
      this._onProgress = function (e) {
        self._progress(e.detail || {});
      };
      this._onUploadError = function (e) {
        self._clientRefusal(e.detail || {});
      };
      this._onServerError = function (e) {
        var detail = e.detail || {};
        if (typeof detail.error === "string" && SLOT_ERRORS.test(detail.error)) self._serverRefusal();
      };

      // Capture, on the root: our checks run before djust's own change and drop
      // handlers on the input and the zone (which receive the event afterwards).
      root.addEventListener("change", this._onChange, true);
      root.addEventListener("drop", this._onDrop, true);
      root.addEventListener("dragover", this._onDragOver, true);
      root.addEventListener("dragleave", this._onDragLeave, true);
      root.addEventListener("click", this._onClick);
      window.addEventListener("djust:upload:progress", this._onProgress);
      window.addEventListener("djust:upload:error", this._onUploadError);
      window.addEventListener("djust:error", this._onServerError);
      this._claim();
    },

    _unbind: function () {
      var root = this._root;
      if (root) {
        root.removeEventListener("change", this._onChange, true);
        root.removeEventListener("drop", this._onDrop, true);
        root.removeEventListener("dragover", this._onDragOver, true);
        root.removeEventListener("dragleave", this._onDragLeave, true);
        root.removeEventListener("click", this._onClick);
      }
      window.removeEventListener("djust:upload:progress", this._onProgress);
      window.removeEventListener("djust:upload:error", this._onUploadError);
      window.removeEventListener("djust:error", this._onServerError);
      (this._items || []).forEach(function (item) {
        if (item.url) URL.revokeObjectURL(item.url);
        item.url = "";
      });
      this._items = [];
      this._root = null;
    },

    // --- the elements ---------------------------------------------------------

    _input: function () {
      return this.el.querySelector(".dj-img-upload__input");
    },
    _zone: function () {
      return this.el.querySelector(".dj-img-upload__dropzone");
    },
    _list: function () {
      return this.el.querySelector(".dj-img-upload__items");
    },
    _status: function () {
      return this.el.querySelector(".dj-img-upload__status");
    },

    // The list and the status line are client-owned DOM inside a server-rendered
    // element: ignore keeps a full morph from emptying them (set here, so it
    // needs no id). After a morph that replaced them, the items move across.
    _claim: function () {
      var list = this._list();
      var status = this._status();
      if (list) list.setAttribute("dj-update", "ignore");
      if (status) status.setAttribute("dj-update", "ignore");
      if (list) {
        this._items.forEach(function (item) {
          if (item.li.parentNode !== list) list.appendChild(item.li);
        });
      }
    },

    _say: function (message) {
      var status = this._status();
      if (!status || !message) return;
      // Identical text twice in a row is not announced again by most screen readers.
      status.textContent = status.textContent === message ? message + "\u00a0" : message;
    },

    // --- limits (UX only) -----------------------------------------------------

    _slot: function () {
      return this.el.getAttribute("data-upload") || "";
    },
    _max: function () {
      return Math.max(1, num(this.el.getAttribute("data-max"), 5));
    },
    _maxSize: function () {
      return num(this.el.getAttribute("data-max-size"), 0);
    },
    _tokens: function () {
      var input = this._input();
      return acceptTokens(input ? input.getAttribute("accept") : "");
    },

    // How many images the component holds: the server's own previews plus the
    // reader's that are kept (not refused, not cancelled).
    _count: function () {
      var kept = this._items.filter(function (i) {
        return i.state !== "refused" && i.state !== "cancelled" && i.state !== "error";
      }).length;
      return this.el.querySelectorAll(".dj-img-upload__thumb").length + kept;
    },

    _verdict: function (file, room) {
      var tokens = this._tokens();
      var maxSize = this._maxSize();
      if (!matchesAccept(file, tokens)) {
        return "not an accepted type (" + (tokens.join(", ") || "any") + ")";
      }
      if (maxSize && file.size > maxSize) {
        return "larger than " + formatSize(maxSize);
      }
      if (room <= 0) return "more than the " + this._max() + " allowed";
      return "";
    },

    // --- choosing -------------------------------------------------------------

    _changed: function () {
      var input = this._input();
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) return;
      this._select(files, input);
    },

    // A drop is turned into the same thing as choosing in the picker: the
    // dropped files become the input's files and one change event follows, so
    // djust's own handler (dj-upload) is the only thing that uploads them.
    _dropped: function (e) {
      var input = this._input();
      var zone = this._zone();
      if (zone) zone.classList.remove("upload-dragover");
      if (!input || !e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
      // No DataTransfer constructor (an old browser): leave the drop to djust's own handler.
      if (!this._setFiles(input, Array.prototype.slice.call(e.dataTransfer.files))) return;
      e.preventDefault();
      e.stopPropagation();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },

    // Replace the input's files. False when the browser has no DataTransfer
    // constructor (then the files stay as chosen and the server alone decides).
    _setFiles: function (input, files) {
      try {
        var transfer = new DataTransfer();
        files.forEach(function (f) {
          transfer.items.add(f);
        });
        input.files = transfer.files;
        return true;
      } catch (_e) {
        return false;
      }
    },

    _select: function (files, input) {
      var self = this;
      var single = this._max() === 1;
      if (single) {
        // One image: a new choice replaces the old one.
        this._items.slice().forEach(function (item) {
          if (item.state === "uploading") self._cancel(item);
          self._drop(item, true);
        });
        files = files.slice(0, 1);
      }
      var room = this._max() - this._count();
      var accepted = [];
      var refused = [];
      var verdicts = [];
      files.forEach(function (file) {
        var why = self._verdict(file, room - accepted.length);
        verdicts.push(why);
        if (why) refused.push({ file: file, why: why });
        else accepted.push(file);
      });

      if (refused.length && !this._setFiles(input, accepted)) {
        // No way to hold the files back: they are shown as chosen and sent; the
        // server refuses what it must.
        accepted = files;
        refused = [];
      }

      var slot = this._slot();
      // djust's client sends nothing without a WebSocket (it logs and returns).
      var offline = slot && !this._connected();
      // In the order they were chosen, refused ones in place.
      files.forEach(function (file, i) {
        if (accepted.indexOf(file) < 0) self._add(file, "refused", verdicts[i]);
        else self._add(file, slot ? "uploading" : "selected");
      });

      var parts = [];
      if (accepted.length) {
        parts.push(accepted.length + (accepted.length === 1 ? " image" : " images") + " chosen");
      }
      refused.forEach(function (r) {
        parts.push(r.file.name + " " + r.why + ", not sent");
      });
      this._say(parts.join(". "));

      if (!slot) {
        if (accepted.length) this._notify(accepted.length);
      } else {
        this._inFlight += accepted.length;
        if (offline) {
          this._items.slice().forEach(function (item) {
            if (item.state === "uploading") self._settle(item, "error", "Not connected: nothing was sent");
          });
          this._say("Not connected: nothing was sent");
        }
      }
    },

    _connected: function () {
      var live = window.djust && window.djust.liveViewInstance;
      return !!(live && live.ws && live.ws.readyState === 1);
    },

    // --- one thumbnail --------------------------------------------------------

    _add: function (file, state, why) {
      var item = { id: ++this._seq, file: file, state: state, ref: "", progress: 0, url: "" };
      var li = el("li", "dj-img-upload__item");
      li.setAttribute("data-state", state);
      var thumb = el("span", "dj-img-upload__item-thumb");
      if (state !== "refused" && /^image\//i.test(file.type || "") && typeof URL.createObjectURL === "function") {
        item.url = URL.createObjectURL(file);
        var img = el("img", "dj-img-upload__item-img");
        img.alt = "";
        img.addEventListener("error", function () {
          if (img.parentNode) img.parentNode.removeChild(img);
          thumb.classList.add("dj-img-upload__item-thumb--broken");
        });
        img.src = item.url;
        thumb.appendChild(img);
      } else {
        thumb.classList.add("dj-img-upload__item-thumb--broken");
      }
      var main = el("span", "dj-img-upload__item-main");
      var name = el("span", "dj-img-upload__item-name", file.name || "");
      var meta = el("span", "dj-img-upload__item-meta", formatSize(file.size || 0));
      var note = el("span", "dj-img-upload__item-note");
      main.appendChild(name);
      main.appendChild(meta);
      main.appendChild(note);
      var bar = null;
      if (state === "uploading") {
        bar = el("progress", "dj-img-upload__item-bar");
        bar.max = 100;
        bar.value = 0;
        bar.setAttribute("aria-label", "Uploading " + (file.name || "image"));
        main.appendChild(bar);
      }
      var button = el("button", "dj-img-upload__item-button");
      button.type = "button";
      li.appendChild(thumb);
      li.appendChild(main);
      li.appendChild(button);
      item.li = li;
      item.note = note;
      item.bar = bar;
      item.button = button;
      this._items.push(item);
      this._style(item, why || "");
      var list = this._list();
      if (list) list.appendChild(li);
      return item;
    },

    // The visible state of a thumbnail: its note and its one button.
    _style: function (item, why) {
      var name = item.file.name || "image";
      item.li.setAttribute("data-state", item.state);
      var note = "";
      var action = "remove";
      var label = "Remove";
      if (item.state === "uploading") {
        note = item.progress ? item.progress + "%" : "Uploading";
        action = "cancel";
        label = "Cancel";
      } else if (item.state === "done") note = "Uploaded";
      else if (item.state === "selected") note = "Ready";
      else if (item.state === "cancelled") note = "Cancelled";
      else if (item.state === "refused") note = "Not sent: " + why;
      else if (item.state === "error") note = why || "Upload failed";
      item.note.textContent = note;
      item.button.textContent = label;
      item.button.setAttribute("data-action", action);
      item.button.setAttribute("aria-label", label + " " + name);
      if (item.bar) item.bar.value = item.progress;
    },

    _find: function (id) {
      for (var i = 0; i < this._items.length; i++) {
        if (this._items[i].id === id) return this._items[i];
      }
      return null;
    },

    // Remove (also what a finished, refused or failed thumbnail's button does)
    // and Cancel.
    _act: function (button) {
      var li = button.closest("li");
      var item = null;
      for (var i = 0; i < this._items.length; i++) {
        if (this._items[i].li === li) item = this._items[i];
      }
      if (!item) return;
      var name = item.file.name || "image";
      if (button.getAttribute("data-action") === "cancel") {
        this._cancel(item);
        this._say("Upload of " + name + " cancelled");
        return;
      }
      this._drop(item);
      this._say(name + " removed");
    },

    // The upload ref djust minted for this file, from its own table of uploads
    // in flight (the ref is created inside the client when the file is sent).
    _refFor: function (item) {
      if (item.ref) return item.ref;
      var uploads = window.djust && window.djust.uploads && window.djust.uploads.activeUploads;
      if (!uploads) return "";
      var found = "";
      uploads.forEach(function (upload, ref) {
        if (!found && upload && upload.file && sameFile(upload.file, item.file)) found = ref;
      });
      return found;
    },

    _cancel: function (item) {
      if (item.state !== "uploading") return;
      var ref = this._refFor(item);
      if (ref && window.djust.uploads && typeof window.djust.uploads.cancelUpload === "function") {
        window.djust.uploads.cancelUpload(ref);
      }
      this._settle(item, "cancelled");
    },

    // Take a thumbnail out for good and give its object URL back.
    _drop: function (item, keepInput) {
      if (item.state === "uploading") this._settle(item, "cancelled");
      if (item.url) URL.revokeObjectURL(item.url);
      item.url = "";
      if (item.li.parentNode) item.li.parentNode.removeChild(item.li);
      var at = this._items.indexOf(item);
      if (at >= 0) this._items.splice(at, 1);
      // A removed file must not go with the form: the input forgets it too
      // (not when a new choice replaces it: the input then holds the new one).
      if (!keepInput) this._forget(item.file);
    },

    // The files are what a form post carries (with no upload slot): drop one
    // from the input as well.
    _forget: function (file) {
      var input = this._input();
      if (!input || !input.files) return;
      var rest = Array.prototype.slice.call(input.files).filter(function (f) {
        return !sameFile(f, file);
      });
      if (rest.length !== input.files.length) this._setFiles(input, rest);
    },

    // A thumbnail's upload ended one way or another.
    _settle: function (item, state, why) {
      if (item.state !== "uploading") return;
      item.state = state;
      this._inFlight = Math.max(0, this._inFlight - 1);
      if (state === "done") this._finished += 1;
      if (item.bar && item.bar.parentNode) item.bar.parentNode.removeChild(item.bar);
      item.bar = null;
      this._style(item, why || "");
      if (this._inFlight === 0 && this._finished > 0) {
        var count = this._finished;
        this._finished = 0;
        this._notify(count);
      }
    },

    _notify: function (count) {
      var event = this.el.getAttribute("data-notify") === "true" ? this.el.getAttribute("data-event") : "";
      if (event) send(this, this.el, event, { count: count });
    },

    // --- what djust's client reports ---------------------------------------------

    _itemForRef: function (ref) {
      for (var i = 0; i < this._items.length; i++) {
        if (this._items[i].ref === ref) return this._items[i];
      }
      var uploads = window.djust && window.djust.uploads && window.djust.uploads.activeUploads;
      var upload = uploads && uploads.get ? uploads.get(ref) : null;
      if (!upload || !upload.file) return null;
      for (var j = 0; j < this._items.length; j++) {
        var item = this._items[j];
        if (item.state === "uploading" && !item.ref && sameFile(item.file, upload.file)) {
          item.ref = ref;
          return item;
        }
      }
      return null;
    },

    _progress: function (detail) {
      if (!this._root || detail.uploadName !== this._slot() || !detail.ref) return;
      var item = this._itemForRef(detail.ref);
      if (!item || item.state !== "uploading") return;
      var pct = Math.max(0, Math.min(100, Math.round(Number(detail.progress) || 0)));
      if (detail.status === "complete") {
        item.progress = 100;
        this._settle(item, "done");
        this._say((item.file.name || "image") + " uploaded");
      } else if (detail.status === "error") {
        this._settle(item, "error", "Not accepted by the server");
        this._say((item.file.name || "image") + " was not accepted by the server");
      } else if (detail.status === "cancelled") {
        this._settle(item, "cancelled");
      } else {
        item.progress = pct;
        this._style(item, "");
      }
    },

    // djust's own client refused the file before sending (its size limit).
    _clientRefusal: function (detail) {
      var name = detail && detail.file;
      for (var i = 0; i < this._items.length; i++) {
        var item = this._items[i];
        if (item.state === "uploading" && !item.ref && item.file.name === name) {
          this._settle(item, "error", "Not sent: " + String(detail.error || "refused"));
          this._say(name + ": " + String(detail.error || "refused"));
          return;
        }
      }
    },

    // The server answered a registration with "Upload rejected" (type, size or
    // entry count): it names no file, so every thumbnail still waiting for its
    // first sign of life is marked.
    _serverRefusal: function () {
      var self = this;
      this._items.slice().forEach(function (item) {
        if (item.state === "uploading" && !item.ref && !item.progress && !self._refFor(item)) {
          self._settle(item, "error", "Not accepted by the server");
          self._say((item.file.name || "image") + " was not accepted by the server");
        }
      });
    },

    // --- the server's own previews ---------------------------------------------------

    _signature: function () {
      var thumbs = this.el.querySelectorAll(".dj-img-upload__thumb-img");
      return Array.prototype.map
        .call(thumbs, function (img) {
          return img.getAttribute("src") || "";
        })
        .join("\n");
    },

    // When the app re-renders with a different previews list it has saved the
    // files: the finished local thumbnails give way to it (uploads in flight and
    // failures stay).
    _serverTookOver: function () {
      var sig = this._signature();
      if (sig === this._previewSig) return;
      this._previewSig = sig;
      var self = this;
      this._items
        .filter(function (i) {
          return i.state === "done" || i.state === "selected";
        })
        .forEach(function (item) {
          self._drop(item);
        });
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.ImageUploadPreview && !window.DjustHooks.ImageUploadPreview) {
    window.DjustHooks.ImageUploadPreview = imageUploadPreview;
  }
})();
