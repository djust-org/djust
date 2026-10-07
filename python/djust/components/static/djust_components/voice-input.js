/**
 * VoiceInput — the interaction layer of the VoiceInput component
 * (dj-hook="VoiceInput"): a microphone button over the browser's Web Speech API.
 *
 * Privacy first. Speech recognition is done by the browser and, in Chrome and
 * Edge, the audio may be sent to the browser vendor's speech service; the
 * component's disclosure line says so, beside the button, at all times.
 *
 * - Nothing starts until the reader presses the microphone. There is no
 *   auto-start, and listening is only ever the reader's own act.
 * - Listening stops, by aborting (what was not yet final is discarded), when
 *   focus leaves the component, the window loses focus, the tab is hidden, the
 *   page is left or navigated (pagehide, popstate, djust:before-navigate), the
 *   component is removed, or data-max-seconds pass. Pressing the button again
 *   stops it and keeps the phrase in progress.
 * - Only FINAL transcripts are sent, as {text} to data-event (cleaned: control
 *   and bidirectional characters removed, whitespace collapsed, at most 2000
 *   characters). Interim guesses are shown (aria-hidden) and never sent. The
 *   text is the browser's claim: the server must treat it as untrusted.
 * - Audio is never recorded, kept or sent by this script: it does not touch
 *   getUserMedia, MediaRecorder, Web Audio, storage or the network.
 * - A browser without the API gets a disabled button and "not supported" in the
 *   status region. Permission denied, no speech, no microphone, no connection to
 *   the speech service and an unsupported language are each announced in the
 *   polite status region, in plain words.
 * - data-lang is the recognition language (a BCP 47 tag; anything else falls
 *   back to the page's language). data-continuous keeps listening after each
 *   phrase until stopped (still bounded by data-max-seconds).
 *
 * Nothing is created by this script: the disclosure, the interim text and the
 * status region are in the markup, so a patch cannot remove one; what the hook
 * sets on server-rendered elements (aria-pressed, disabled, aria-describedby) is
 * derived from its state again after every patch.
 *
 * An app's own ``VoiceInput`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when neither
 * registry already holds one.
 */
(function () {
  "use strict";

  var LANG = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$/;
  // Unpaired surrogates, control characters and the invisible and bidirectional-override characters a
  // transcript has no use for: C0 and C1 controls, line/paragraph separators,
  // zero-width and direction marks, bidi embeddings and isolates, the BOM.
  var BAD_RANGES = [[0, 8], [11, 31], [127, 159], [8232, 8233], [8203, 8207], [8234, 8238], [8294, 8297], [65279, 65279], [55296, 57343]];

  function isBad(code) {
    for (var i = 0; i < BAD_RANGES.length; i++) {
      if (code >= BAD_RANGES[i][0] && code <= BAD_RANGES[i][1]) return true;
    }
    return false;
  }

  var MAX_TEXT = 2000;
  var uid = 0;

  var MESSAGES = {
    unsupported: "Voice input is not supported in this browser.",
    listening: "Listening. Speak now.",
    stopped: "Stopped listening.",
    focus: "Stopped listening because you moved away from the microphone.",
    hidden: "Stopped listening because the page is no longer in view.",
    timeout: "Stopped listening: the time limit was reached.",
    "not-allowed": "Microphone access was denied. Allow the microphone for this site in your browser's settings to use voice input.",
    "service-not-allowed": "Your browser does not allow speech recognition for this page.",
    "no-speech": "No speech was heard. Press the microphone and try again.",
    "audio-capture": "No microphone was found. Check that one is connected and not in use.",
    network: "The speech service could not be reached. Voice input needs a network connection.",
    "language-not-supported": "This language is not supported for voice input.",
    "bad-grammar": "Voice input could not be started.",
    other: "Voice input stopped because of a problem.",
  };

  // The error codes of the Web Speech API that have a message of their own; any
  // other value (the browser's claim) is reported as a general problem.
  var ERROR_CODES = ["not-allowed", "service-not-allowed", "no-speech", "audio-capture", "network", "language-not-supported", "bad-grammar"];

  function recognitionClass() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function clean(value) {
    if (typeof value !== "string") return "";
    var text = value.slice(0, MAX_TEXT * 4);
    var out = "";
    for (var i = 0; i < text.length; i++) {
      var code = text.codePointAt(i);
      out += isBad(code) ? " " : String.fromCodePoint(code);
      if (code > 0xffff) i++;
    }
    return Array.from(out.split(/\s+/).filter(Boolean).join(" ")).slice(0, MAX_TEXT).join("");
  }

  // Same public entry point dj-click and dj-viewport use, so the event works
  // over WebSocket, SSE and HTTP-only, honours strict parameter contracts and
  // reaches the right view when several are mounted. Falls back to the hook's
  // own pushEvent when the client API is absent.
  function addContext(params, el) {
    for (var node = el; node && node !== document.body; node = node.parentElement) {
      var ds = node.dataset || {};
      if (params.component_id === undefined && ds.componentId) params.component_id = ds.componentId;
      if (params.view_id === undefined && ds.djustEmbedded) params.view_id = ds.djustEmbedded;
    }
  }

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

  var voiceInput = {
    mounted: function () {
      this._bind();
    },

    // A patch can reset what the hook sets on server-rendered elements; they are
    // derived from its state again. A listening session is not interrupted.
    updated: function () {
      if (this._root !== this.el) {
        this._unbind();
        this._bind();
        return;
      }
      this._render();
    },

    destroyed: function () {
      this._unbind();
    },

    // --- setup ---------------------------------------------------------------

    _bind: function () {
      var self = this;
      var button = this.el;
      this._root = button;
      this._wrap = button.closest ? button.closest(".dj-voice-field") : null;
      this._rec = null;
      this._listening = false;
      this._aborting = false;
      this._timer = 0;
      this._stoppedFor = "";
      this._errored = false;
      this._gotText = false;
      this._ns = ++uid;
      this._supported = !!recognitionClass();

      this._h_click = function () {
        self._press();
      };
      this._h_focusout = function (e) {
        if (!self._listening) return;
        var to = e.relatedTarget;
        if (to && self._wrap && self._wrap.contains(to)) return;
        self._abort("focus");
      };
      this._h_blur = function () {
        self._abort("focus");
      };
      this._h_visibility = function () {
        if (document.hidden) self._abort("hidden");
      };
      this._h_leave = function () {
        self._abort("");
      };

      button.addEventListener("click", this._h_click);
      (this._wrap || button).addEventListener("focusout", this._h_focusout);
      window.addEventListener("blur", this._h_blur);
      document.addEventListener("visibilitychange", this._h_visibility);
      window.addEventListener("pagehide", this._h_leave);
      window.addEventListener("popstate", this._h_leave);
      window.addEventListener("djust:before-navigate", this._h_leave);
      this._render();
    },

    _unbind: function () {
      this._abort("");
      var button = this._root;
      if (button && this._h_click) {
        button.removeEventListener("click", this._h_click);
        (this._wrap || button).removeEventListener("focusout", this._h_focusout);
      }
      if (this._h_blur) {
        window.removeEventListener("blur", this._h_blur);
        document.removeEventListener("visibilitychange", this._h_visibility);
        window.removeEventListener("pagehide", this._h_leave);
        window.removeEventListener("popstate", this._h_leave);
        window.removeEventListener("djust:before-navigate", this._h_leave);
      }
      this._h_click = this._h_focusout = this._h_blur = this._h_visibility = this._h_leave = null;
      this._root = this._wrap = null;
    },

    _q: function (cls) {
      var scope = this._wrap || this._root;
      return scope ? scope.querySelector(".dj-voice-input__" + cls) : null;
    },

    _say: function (message) {
      var status = this._q("status");
      if (!status || !message) return;
      status.textContent = message;
    },

    _interim: function (text) {
      var node = this._q("interim");
      if (node) node.textContent = text || "";
    },

    // Everything the hook sets on server-rendered elements, from its state.
    _render: function () {
      var button = this._root;
      if (!button) return;
      button.setAttribute("aria-pressed", this._listening ? "true" : "false");
      button.disabled = !this._supported;
      if (!this._supported) button.setAttribute("aria-disabled", "true");
      else button.removeAttribute("aria-disabled");
      var disclosure = this._q("disclosure");
      if (disclosure) {
        if (!disclosure.id || disclosure.id.indexOf("dj-voice-disclosure-") !== 0) {
          disclosure.id = "dj-voice-disclosure-" + this._ns;
        }
        button.setAttribute("aria-describedby", disclosure.id);
      }
      if (!this._supported) {
        var status = this._q("status");
        if (status && !status.textContent.trim()) status.textContent = MESSAGES.unsupported;
      }
    },

    // --- listening --------------------------------------------------------------

    _language: function () {
      var lang = this._root.getAttribute("data-lang") || "";
      if (LANG.test(lang)) return lang;
      var page = document.documentElement.getAttribute("lang") || "";
      return LANG.test(page) ? page : "en-US";
    },

    _press: function () {
      if (!this._supported) return;
      if (this._listening) this._finishByUser();
      else this._start();
    },

    _start: function () {
      var Recognition = recognitionClass();
      if (!Recognition || this._listening) return;
      var self = this;
      var rec;
      try {
        rec = new Recognition();
        rec.lang = this._language();
        rec.continuous = this._root.getAttribute("data-continuous") === "true";
        rec.interimResults = true;
        rec.maxAlternatives = 1;
      } catch (_e) {
        this._say(MESSAGES.other);
        return;
      }
      this._rec = rec;
      this._aborting = false;
      this._errored = false;
      this._gotText = false;
      this._stoppedFor = "";
      rec.onresult = function (e) {
        if (self._rec === rec) self._result(e);
      };
      rec.onerror = function (e) {
        if (self._rec === rec) self._error(e && e.error);
      };
      rec.onend = function () {
        if (self._rec === rec) self._ended();
      };
      this._listening = true;
      this._interim("");
      this._render();
      this._say(MESSAGES.listening);
      var seconds = parseInt(this._root.getAttribute("data-max-seconds"), 10);
      if (!(seconds > 0)) seconds = 60;
      this._timer = window.setTimeout(function () {
        self._abort("timeout");
      }, Math.min(600, seconds) * 1000);
      try {
        rec.start();
      } catch (_e) {
        // start() throws when a session is already running: nothing is
        // listening, so the button must not stay "pressed".
        this._error("other");
        this._ended();
      }
    },

    _result: function (e) {
      var results = e.results || [];
      var interim = "";
      for (var i = e.resultIndex || 0; i < results.length; i++) {
        var result = results[i];
        if (!result || !result[0]) continue;
        var text = clean(result[0].transcript);
        if (result.isFinal) {
          if (text) {
            this._gotText = true;
            var eventName = this._root.getAttribute("data-event");
            if (eventName) send(this, this._root, eventName, { text: text });
          }
        } else {
          interim += (interim ? " " : "") + text;
        }
      }
      this._interim(interim);
    },

    _error: function (code) {
      // Our own abort() reports "aborted": not a problem.
      if (code === "aborted" && this._aborting) return;
      this._errored = true;
      var key = ERROR_CODES.indexOf(code) >= 0 ? code : "other";
      this._say(MESSAGES[key]);
      this._interim("");
    },

    _ended: function () {
      window.clearTimeout(this._timer);
      this._timer = 0;
      var reason = this._stoppedFor;
      var announce = !this._errored && !this._aborting;
      this._listening = false;
      this._rec = null;
      this._interim("");
      this._render();
      if (announce) this._say(MESSAGES.stopped);
      if (this._aborting) this._say(MESSAGES[reason] || MESSAGES.stopped);
    },

    // The reader presses the button again: stop and keep the phrase in progress.
    _finishByUser: function () {
      var rec = this._rec;
      if (!rec) return;
      try {
        rec.stop();
      } catch (_e) {
        this._ended();
      }
    },

    // Stop at once and discard what was not final. ``reason`` is what to tell
    // the reader ("" for leaving the page or going away: nothing to tell).
    _abort: function (reason) {
      var rec = this._rec;
      if (!rec || !this._listening) return;
      this._aborting = true;
      this._stoppedFor = reason;
      try {
        rec.abort();
      } catch (_e) {
        // Already ended.
      }
      // An aborted recognition may never fire "end": settle now.
      this._ended();
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.VoiceInput && !window.DjustHooks.VoiceInput) {
    window.DjustHooks.VoiceInput = voiceInput;
  }
})();
