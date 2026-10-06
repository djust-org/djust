/**
 * MapPicker — the interaction layer of the MapPicker component
 * (dj-hook="MapPicker"): a Leaflet map with one marker that the reader moves
 * by clicking or tapping, or with the keyboard.
 *
 * Leaflet is vendored (ADR-040) and loaded here on demand: the component's
 * markup carries the URLs and Subresource Integrity values djust.assets
 * computed (data-leaflet), the first map on a page injects the stylesheet and
 * the script once, and a page without a map never fetches them. A page that
 * already loaded Leaflet (for example with {% djust_asset "leaflet" %}) is
 * used as it is.
 *
 * - click / tap: moves the marker and sends {lat, lng} to the server event in
 *   data-pick-event. Both numbers are finite, latitude is clamped to the
 *   Web-Mercator limits and longitude is wrapped to -180..180; the server must
 *   still validate them, the browser is not trusted.
 * - keyboard (focus the map): arrow keys move the marker (Shift: larger
 *   steps), + / - zoom, Enter or Space choose the location (that is what
 *   sends the event), Escape puts the marker back on the last chosen
 *   location. Every move and choice is announced in a polite live region and
 *   shown in a coordinate readout.
 * - the server stays in charge: a new data-lat / data-lng moves the marker
 *   (a keyboard move that was not chosen yet is replaced), a new data-zoom
 *   zooms; a patch that changes none of them leaves the reader's state alone.
 * - tiles: data-tile-url is a Leaflet template (http(s) or root-relative).
 *   The attribution is plain text (data-attribution, optional
 *   data-attribution-url), built with DOM text nodes: no HTML from any
 *   attribute is ever parsed. When no tile loads, the marker still works and a
 *   notice says the tiles are unavailable.
 * - the map follows its container's size (ResizeObserver), honours
 *   prefers-reduced-motion, wheel-zooms only while it has focus (so the page
 *   still scrolls), and is torn down completely on destroyed().
 *
 * An app's own ``MapPicker`` hook is never replaced: this registers in
 * window.DjustHooks, which window.djust.hooks overrides, and only when
 * neither registry already holds one.
 */
(function () {
  "use strict";

  var KEY_STEP = 8;
  var KEY_STEP_LARGE = 64;
  // Web-Mercator's limit: a tile map has no ground above or below it.
  var MAX_LAT = 85.0511287798;
  var uid = 0;

  // --- helpers --------------------------------------------------------------

  function num(value, fallback) {
    var n = typeof value === "number" ? value : parseFloat(value);
    return isFinite(n) ? n : fallback;
  }

  function round6(n) {
    return Math.round(n * 1e6) / 1e6;
  }

  function fmt(lat, lng) {
    return (
      Math.abs(lat).toFixed(5) + "° " + (lat >= 0 ? "N" : "S") + ", " +
      Math.abs(lng).toFixed(5) + "° " + (lng >= 0 ? "E" : "W")
    );
  }

  function isHttpUrl(value) {
    return typeof value === "string" && /^https?:\/\/[^\s]+$/i.test(value);
  }

  // A URL the page may fetch an asset from: http(s) or a root-relative path.
  function isFetchUrl(value) {
    return isHttpUrl(value) || (typeof value === "string" && /^\/(?!\/)[^\s]*$/.test(value));
  }

  function isTileTemplate(value) {
    if (!isFetchUrl(value)) return false;
    var names = value.match(/\{[^{}]*\}/g) || [];
    var seen = {};
    for (var i = 0; i < names.length; i++) {
      var name = names[i].slice(1, -1);
      if (["z", "x", "y", "s", "r"].indexOf(name) < 0) return false;
      seen[name] = true;
    }
    return !!(seen.z && seen.x && seen.y);
  }

  function parseJson(text) {
    if (!text) return null;
    try {
      var value = JSON.parse(text);
      return value && typeof value === "object" ? value : null;
    } catch (_e) {
      return null;
    }
  }

  function reducedMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (_e) {
      return false;
    }
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
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

  // --- loading the vendored Leaflet, once per page --------------------------

  var leafletLoad = null;

  function sameResource(node, attr, url) {
    return node.getAttribute(attr) === url;
  }

  function waitFor(node, what) {
    return new Promise(function (resolve, reject) {
      node.addEventListener("load", function () { resolve(); });
      node.addEventListener("error", function () { reject(new Error(what)); });
    });
  }

  function loadLeaflet(cfg) {
    if (window.L && typeof window.L.map === "function") return Promise.resolve(window.L);
    if (leafletLoad) return leafletLoad;
    if (!cfg || !isFetchUrl(cfg.js) || !isFetchUrl(cfg.css)) {
      return Promise.reject(new Error("no leaflet"));
    }
    var cross = cfg.crossOrigin === "anonymous";
    var head = document.head || document.documentElement;
    var parts = [];

    // What this call adds to the page, to take back if it fails: a failed tag
    // left in place would make the next attempt wait on it for ever.
    var added = [];
    var link = null;
    Array.prototype.forEach.call(document.querySelectorAll("link[rel~='stylesheet']"), function (l) {
      if (sameResource(l, "href", cfg.css)) link = l;
    });
    if (!link) {
      link = document.createElement("link");
      link.setAttribute("rel", "stylesheet");
      link.setAttribute("href", cfg.css);
      if (cfg.cssIntegrity) link.setAttribute("integrity", cfg.cssIntegrity);
      if (cross) link.setAttribute("crossorigin", "anonymous");
      parts.push(waitFor(link, "css"));
      head.appendChild(link);
      added.push(link);
    }

    var script = null;
    Array.prototype.forEach.call(document.querySelectorAll("script[src]"), function (s) {
      if (sameResource(s, "src", cfg.js)) script = s;
    });
    var scriptReady;
    if (script) {
      // A page tag that has not finished yet: wait for it rather than run Leaflet twice.
      scriptReady = waitFor(script, "js");
    } else {
      script = document.createElement("script");
      script.setAttribute("src", cfg.js);
      if (cfg.jsIntegrity) script.setAttribute("integrity", cfg.jsIntegrity);
      if (cross) script.setAttribute("crossorigin", "anonymous");
      scriptReady = waitFor(script, "js");
      head.appendChild(script);
      added.push(script);
    }
    parts.push(scriptReady);

    leafletLoad = Promise.all(parts).then(
      function () {
        if (window.L && typeof window.L.map === "function") return window.L;
        throw new Error("leaflet missing");
      },
      function (err) {
        leafletLoad = null; // a later mount may try again
        added.forEach(function (node) {
          if (node.parentNode) node.parentNode.removeChild(node);
        });
        throw err;
      },
    );
    return leafletLoad;
  }

  // --- the hook -------------------------------------------------------------

  var mapPicker = {
    mounted: function () {
      this._start();
    },

    // A server patch may carry a new position or zoom; one that does not
    // leaves the reader's state (a keyboard move not yet chosen) alone.
    updated: function () {
      if (this._rootEl !== this.el) {
        this._stop();
        this._start();
        return;
      }
      if (!this._map) return;
      // A full morph can strip what the hook added to the map element, or
      // replace the element itself: rebuild instead of running on a husk.
      var mapEl = this.el.querySelector(".dj-map-picker__map");
      if (mapEl !== this._mapEl || !mapEl || !mapEl.querySelector(".leaflet-map-pane")) {
        this._stop();
        this._start();
        return;
      }
      this._describe();
      this._syncFromServer();
    },

    destroyed: function () {
      this._stop();
    },

    // --- setup --------------------------------------------------------------

    _start: function () {
      this._dead = false;
      this._gen = (this._gen || 0) + 1;
      var gen = this._gen;
      var self = this;
      this._rootEl = this.el;
      this._mapEl = this.el.querySelector(".dj-map-picker__map");
      if (!this._mapEl) {
        this._mapEl = el("div", "dj-map-picker__map");
        this.el.appendChild(this._mapEl);
      }
      // The map and the notices are client-owned DOM inside an element the server
      // renders empty. The page's first full morph (the WebSocket mount response)
      // would wipe them, and every later one that reaches this element; ignore
      // keeps the morph from touching it at all. Set here, not in the markup, so
      // it needs no id and an app that renders the markup itself still gets it.
      this._mapEl.setAttribute("dj-update", "ignore");
      this._cfg = parseJson(this.el.getAttribute("data-leaflet"));
      loadLeaflet(this._cfg).then(
        function (L) {
          if (self._dead || self._gen !== gen) return;
          self._build(L);
        },
        function () {
          if (self._dead || self._gen !== gen) return;
          self._failed();
        },
      );
    },

    _failed: function () {
      if (!window.__djMapPickerWarned) {
        window.__djMapPickerWarned = true;
        console.warn(
          "[djust] MapPicker: Leaflet did not load. Check the browser console for an " +
            "integrity (SRI) or Content-Security-Policy error.",
        );
      }
      this._note("The map could not be loaded.");
    },

    _note: function (message) {
      var mapEl = this._mapEl;
      if (!mapEl) return;
      var note = mapEl.querySelector(".dj-map-picker__notice");
      if (!note) {
        // Not a live region of its own: _say announces it once.
        note = el("div", "dj-map-picker__notice");
        mapEl.appendChild(note);
      }
      note.textContent = message;
      note.hidden = !message;
      this._say(message);
    },

    _say: function (message) {
      if (!message) return;
      var live = this._live;
      if (!live) {
        // Before the map is built (a load failure) there is no live region yet.
        live = this._live = el("div", "dj-map-picker__sr");
        live.setAttribute("role", "status");
        live.setAttribute("aria-live", "polite");
        if (this._mapEl) this._mapEl.appendChild(live);
      }
      // Identical text twice in a row is not announced again by most screen readers.
      live.textContent = live.textContent === message ? message + " " : message;
    },

    _build: function (L) {
      var self = this;
      var root = this.el;
      var mapEl = this._mapEl;
      var d = root.dataset;
      this._L = L;
      this._ns = ++uid;

      var maxZoom = Math.max(1, Math.min(22, Math.round(num(d.maxZoom, 19))));
      var lat = this._clampLat(num(d.lat, 0));
      var lng = this._wrapLng(num(d.lng, 0));
      var zoom = Math.max(0, Math.min(maxZoom, Math.round(num(d.zoom, 13))));
      var calm = reducedMotion();

      // The element may still hold a husk of an earlier map (a remount that
      // skipped destroyed): Leaflet refuses to initialise the same node twice.
      if (mapEl._leaflet_id) {
        var fresh = el("div", mapEl.className);
        mapEl.parentNode.replaceChild(fresh, mapEl);
        mapEl = this._mapEl = fresh;
      }
      this._live = null;
      // Created first: a notice raised while the map is still being built is announced.
      var live = (this._live = el("div", "dj-map-picker__sr"));
      live.setAttribute("role", "status");
      live.setAttribute("aria-live", "polite");
      mapEl.appendChild(live);

      var map;
      try {
        map = L.map(mapEl, {
          center: [lat, lng],
          zoom: zoom,
          maxZoom: maxZoom,
          attributionControl: false,
          keyboard: false,
          scrollWheelZoom: false,
          worldCopyJump: true,
          zoomAnimation: !calm,
          fadeAnimation: !calm,
          markerZoomAnimation: !calm,
        });
      } catch (_e) {
        this._failed();
        return;
      }
      this._map = map;
      this._confirmed = { lat: round6(lat), lng: round6(lng) };
      this._pending = false;
      this._server = { lat: lat, lng: lng, zoom: zoom };

      this._tilesLoaded = 0;
      var tileUrl = d.tileUrl;
      if (isTileTemplate(tileUrl)) {
        var layer = L.tileLayer(tileUrl, { maxZoom: maxZoom, attribution: "" });
        layer.on("tileload", function () {
          self._tilesLoaded += 1;
          if (self._tilesLoaded === 1) self._note("");
        });
        layer.on("tileerror", function () {
          if (self._tilesLoaded === 0 && !self._tileWarned) {
            self._tileWarned = true;
            self._note("Map tiles could not be loaded. You can still choose a location.");
          }
        });
        layer.addTo(map);
        this._layer = layer;
      }

      this._marker = L.marker([lat, lng], {
        icon: this._icon(L),
        interactive: false,
        keyboard: false,
        alt: "",
        zIndexOffset: 1000,
      }).addTo(map);

      this._addControls(L, map);
      this._describe();

      this._onClick = function (e) {
        self._pick(e.latlng.lat, e.latlng.lng);
      };
      map.on("click", this._onClick);

      this._onKey = function (e) {
        self._keydown(e);
      };
      mapEl.addEventListener("keydown", this._onKey);
      this._onFocus = function () {
        if (self._map) self._map.scrollWheelZoom.enable();
      };
      this._onBlur = function () {
        if (self._map) self._map.scrollWheelZoom.disable();
      };
      mapEl.addEventListener("focus", this._onFocus);
      mapEl.addEventListener("blur", this._onBlur);

      if (typeof ResizeObserver === "function") {
        this._ro = new ResizeObserver(function () {
          if (self._raf) return;
          self._raf = window.requestAnimationFrame(function () {
            self._raf = 0;
            if (self._map && !self._dead) self._map.invalidateSize({ animate: false });
          });
        });
        this._ro.observe(mapEl);
      }

      this._readout(lat, lng);
    },

    _icon: function (L) {
      var cfg = this._cfg || {};
      if (isFetchUrl(cfg.icon) && isFetchUrl(cfg.shadow)) {
        // Explicit URLs: Leaflet's own path detection fails on hashed static names.
        var options = {
          iconUrl: cfg.icon,
          shadowUrl: cfg.shadow,
          iconSize: [25, 41],
          iconAnchor: [12, 41],
          shadowSize: [41, 41],
        };
        if (isFetchUrl(cfg.icon2x)) options.iconRetinaUrl = cfg.icon2x;
        return L.icon(options);
      }
      return L.divIcon({ className: "dj-map-picker__pin", iconSize: [18, 18], iconAnchor: [9, 9] });
    },

    _addControls: function (L, map) {
      var d = this.el.dataset;
      var self = this;

      // Attribution: text nodes and an <a> built here, never parsed HTML.
      var Attribution = L.Control.extend({
        options: { position: "bottomright" },
        onAdd: function () {
          var box = L.DomUtil.create("div", "leaflet-control-attribution leaflet-control dj-map-picker__attribution");
          var lib = el("a", "", "Leaflet");
          lib.href = "https://leafletjs.com";
          lib.target = "_blank";
          lib.rel = "noopener noreferrer";
          box.appendChild(lib);
          var text = (d.attribution || "").trim();
          if (text) {
            box.appendChild(document.createTextNode(" | "));
            if (isHttpUrl(d.attributionUrl)) {
              var a = el("a", "", text);
              a.href = d.attributionUrl;
              a.target = "_blank";
              a.rel = "noopener noreferrer";
              box.appendChild(a);
            } else {
              box.appendChild(document.createTextNode(text));
            }
          }
          L.DomEvent.disableClickPropagation(box);
          return box;
        },
      });
      new Attribution().addTo(map);

      var Readout = L.Control.extend({
        options: { position: "bottomleft" },
        onAdd: function () {
          var box = L.DomUtil.create("div", "leaflet-control dj-map-picker__coords");
          // The live region announces changes; the readout is for the eye.
          box.setAttribute("aria-hidden", "true");
          self._coords = box;
          L.DomEvent.disableClickPropagation(box);
          return box;
        },
      });
      new Readout().addTo(map);
    },

    // The focusable map element's name, role and instructions. Re-applied
    // after every patch (a morph can drop attributes it does not know).
    _describe: function () {
      var mapEl = this._mapEl;
      if (!mapEl) return;
      var label = this.el.getAttribute("aria-label") || "Map picker";
      mapEl.setAttribute("tabindex", "0");
      mapEl.setAttribute("role", "group");
      mapEl.setAttribute("aria-roledescription", "map");
      mapEl.setAttribute("aria-label", label);
      var helpId = "dj-map-picker-help-" + (this._ns || 0);
      var help = mapEl.querySelector(".dj-map-picker__help");
      if (!help) {
        help = el(
          "div",
          "dj-map-picker__sr dj-map-picker__help",
          "Use the arrow keys to move the marker, Shift for larger steps, plus and minus to zoom, " +
            "Enter to choose the location and Escape to go back to the chosen location.",
        );
        mapEl.appendChild(help);
      }
      help.id = helpId;
      mapEl.setAttribute("aria-describedby", helpId);
    },

    // --- state ---------------------------------------------------------------

    _clampLat: function (lat) {
      return Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
    },

    _wrapLng: function (lng) {
      var w = ((((lng + 180) % 360) + 360) % 360) - 180;
      // 180 and -180 are the same meridian; keep +180 for a request for it.
      return w === -180 && lng > 0 ? 180 : w;
    },

    _readout: function (lat, lng) {
      if (!this._coords) return;
      this._coords.textContent = fmt(lat, lng);
      this._coords.classList.toggle("dj-map-picker__coords--pending", !!this._pending);
    },

    _place: function (lat, lng) {
      this._marker.setLatLng([lat, lng]);
      this._readout(lat, lng);
      // Keep the marker on screen: a keyboard user cannot drag the map.
      var bounds = this._map.getBounds();
      if (!bounds.pad(-0.05).contains([lat, lng])) {
        this._map.panInside([lat, lng], { padding: [40, 40], animate: false });
      }
    },

    _pick: function (rawLat, rawLng) {
      if (!this._map || this._dead) return;
      var lat = round6(this._clampLat(num(rawLat, NaN)));
      var lng = round6(this._wrapLng(num(rawLng, NaN)));
      if (!isFinite(lat) || !isFinite(lng)) return;
      this._confirmed = { lat: lat, lng: lng };
      this._pending = false;
      this._place(lat, lng);
      this._say("Location chosen: " + fmt(lat, lng));
      var eventName = this.el.getAttribute("data-pick-event");
      if (eventName) send(this, this.el, eventName, { lat: lat, lng: lng });
    },

    _move: function (dx, dy) {
      var map = this._map;
      var at = this._marker.getLatLng();
      var zoom = map.getZoom();
      var p = map.project(at, zoom);
      var next = map.unproject(this._L.point(p.x + dx, p.y + dy), zoom);
      var lat = this._clampLat(next.lat);
      var lng = this._wrapLng(next.lng);
      this._pending = true;
      this._place(lat, lng);
      this._say(fmt(lat, lng) + ". Press Enter to choose this location.");
    },

    _keydown: function (e) {
      // Only the map itself: the zoom buttons keep their own Enter / Space.
      if (e.target !== this._mapEl || !this._map || this._dead) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var step = e.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
      var key = e.key;
      if (key === "ArrowLeft") this._move(-step, 0);
      else if (key === "ArrowRight") this._move(step, 0);
      else if (key === "ArrowUp") this._move(0, -step);
      else if (key === "ArrowDown") this._move(0, step);
      else if (key === "+" || key === "=") this._zoomBy(1);
      else if (key === "-" || key === "_") this._zoomBy(-1);
      else if (key === "Enter" || key === " ") {
        // A held key repeats: choose once, not thirty times a second.
        if (!e.repeat) {
          var at = this._marker.getLatLng();
          this._pick(at.lat, at.lng);
        }
      } else if (key === "Escape") {
        if (!this._pending) return;
        this._pending = false;
        this._place(this._confirmed.lat, this._confirmed.lng);
        this._say("Back at the chosen location: " + fmt(this._confirmed.lat, this._confirmed.lng));
      } else return;
      e.preventDefault();
      e.stopPropagation();
    },

    _zoomBy: function (delta) {
      var map = this._map;
      var before = map.getZoom();
      map.setZoom(before + delta, { animate: false });
      var now = map.getZoom();
      this._say(now === before ? "Zoom level " + now + ", the limit" : "Zoom level " + now);
    },

    // New position or zoom from the server. Compared with what the server
    // last said, not with the marker: an unrelated patch changes nothing.
    _syncFromServer: function () {
      var d = this.el.dataset;
      var map = this._map;
      var maxZoom = map.getMaxZoom();
      var lat = this._clampLat(num(d.lat, this._server.lat));
      var lng = this._wrapLng(num(d.lng, this._server.lng));
      var zoom = Math.max(0, Math.min(maxZoom, Math.round(num(d.zoom, this._server.zoom))));
      if (zoom !== this._server.zoom) {
        this._server.zoom = zoom;
        map.setZoom(zoom, { animate: false });
      }
      if (lat !== this._server.lat || lng !== this._server.lng) {
        this._server.lat = lat;
        this._server.lng = lng;
        // The server agreeing with the last chosen location (its echo of a pick)
        // changes nothing, and a keyboard move made since then stays.
        var same = round6(lat) === this._confirmed.lat && round6(lng) === this._confirmed.lng;
        if (!same) {
          this._confirmed = { lat: lat, lng: lng };
          this._pending = false;
          this._place(lat, lng);
          this._say("Location set to " + fmt(lat, lng));
        }
      }
    },

    // --- teardown ------------------------------------------------------------

    _stop: function () {
      this._dead = true;
      this._gen = (this._gen || 0) + 1;
      if (this._raf) {
        window.cancelAnimationFrame(this._raf);
        this._raf = 0;
      }
      if (this._ro) {
        this._ro.disconnect();
        this._ro = null;
      }
      var mapEl = this._mapEl;
      if (mapEl) {
        if (this._onKey) mapEl.removeEventListener("keydown", this._onKey);
        if (this._onFocus) mapEl.removeEventListener("focus", this._onFocus);
        if (this._onBlur) mapEl.removeEventListener("blur", this._onBlur);
      }
      if (this._map) {
        try {
          this._map.remove();
        } catch (_e) {
          // The container may already be gone from the document.
        }
      }
      if (mapEl) {
        Array.prototype.forEach.call(
          mapEl.querySelectorAll(".dj-map-picker__sr, .dj-map-picker__notice"),
          function (node) {
            if (node.parentNode) node.parentNode.removeChild(node);
          },
        );
        delete mapEl._leaflet_id;
      }
      this._map = null;
      this._marker = null;
      this._layer = null;
      this._coords = null;
      this._live = null;
      this._onKey = this._onFocus = this._onBlur = this._onClick = null;
      this._tilesLoaded = 0;
      this._tileWarned = false;
      this._mapEl = null;
      this._rootEl = null;
    },
  };

  var appHooks = (window.djust && window.djust.hooks) || {};
  window.DjustHooks = window.DjustHooks || {};
  if (!appHooks.MapPicker && !window.DjustHooks.MapPicker) {
    window.DjustHooks.MapPicker = mapPicker;
  }
})();
