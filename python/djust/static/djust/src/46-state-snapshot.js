
// ============================================================================
// State Snapshot — client half (v0.6.0)
// ============================================================================
//
// Captures the current view's public state on before-navigate and posts it to
// the Service Worker's state cache. On popstate (back-button), looks up a
// cached state and stashes it on window.djust._pendingStateSnapshot so the
// next outbound live_redirect_mount can include it.
//
// Per-view opt-in: the server-side LiveView must declare
// `enable_state_snapshot = True`. The client sends the snapshot regardless
// (belt-and-braces); the server ignores snapshots for non-opt-in views.
//
// Non-invasive: this module only wires listeners and reads/writes one
// globalThis slot. It never mutates the DOM or WebSocket directly.

(function () {
    globalThis.djust = globalThis.djust || {};

    function _swBridge() {
        return globalThis.djust && globalThis.djust._sw;
    }

    function _currentViewSlug(fromUrl) {
        // The route map has pathname -> view-slug entries. When a caller
        // passes ``fromUrl`` (the source URL captured at dispatch time —
        // see Fix #9), prefer that over ``location.pathname`` since
        // pushState() in 18-navigation.js runs BEFORE the
        // ``djust:before-navigate`` dispatch, leaving
        // ``location.pathname`` already pointing at the DESTINATION.
        var pathname = fromUrl
            || ((typeof window !== 'undefined' && window.location)
                ? window.location.pathname
                : '/');
        var routeMap = (globalThis.djust && globalThis.djust._routeMap) || {};
        if (routeMap[pathname]) return routeMap[pathname];
        if (typeof document !== 'undefined') {
            var container = document.querySelector('[dj-view]');
            if (container) return container.getAttribute('dj-view') || '';
        }
        return '';
    }

    function _serializeCurrentState(slug) {
        // The server-side view state is mirrored client-side through the
        // state bus (`05-state-bus.js`) for hydration hints — but the
        // canonical record lives on `window.djust._clientState`, a
        // per-view-slug snapshot dict populated by the mount handler
        // when the server includes ``public_state`` in the mount frame
        // (emitted only when ``enable_state_snapshot = True`` on the
        // view class — see Fix #1).
        if (!slug) return null;
        var bag = (globalThis.djust && globalThis.djust._clientState) || {};
        var state = bag[slug];
        if (!state) return null;
        try {
            return JSON.stringify(state);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn('[state-snapshot] JSON.stringify failed', e);
            }
            return null;
        }
    }

    function _captureBeforeNavigate(event) {
        var bridge = _swBridge();
        if (!bridge || typeof bridge.captureState !== 'function') return;
        // Fix #9: prefer the explicit ``fromUrl`` in the CustomEvent
        // detail so we capture under the SOURCE URL, not the post-
        // pushState destination.
        var fromUrl = (event && event.detail && event.detail.fromUrl)
            || ((typeof window !== 'undefined' && window.location)
                ? window.location.pathname
                : '/');
        var slug = _currentViewSlug(fromUrl);
        if (!slug) return;
        var json = _serializeCurrentState(slug);
        if (!json) return;
        try {
            bridge.captureState(fromUrl, slug, json);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn('[state-snapshot] captureState threw', e);
            }
        }
    }

    // Fix #2: kept for backwards-compat — prewarm the pending slot via
    // an async lookup. Does NOT gate popstate send (the popstate handler
    // in 18-navigation.js now awaits ``lookupStateForUrl`` directly).
    function _popstateLookup(_popstateEvent) {
        var bridge = _swBridge();
        if (!bridge || typeof bridge.lookupState !== 'function') return;
        var url = (typeof window !== 'undefined' && window.location)
            ? window.location.pathname
            : '/';
        bridge.lookupState(url).then(function (reply) {
            if (!reply || !reply.hit) {
                globalThis.djust._pendingStateSnapshot = null;
                return;
            }
            globalThis.djust._pendingStateSnapshot = {
                view_slug: reply.view_slug,
                state_json: reply.state_json,
                ts: reply.ts,
            };
            if (globalThis.djustDebug) {
                console.log('[state-snapshot] restored pending snapshot for', url);
            }
        }).catch(function () {
            globalThis.djust._pendingStateSnapshot = null;
        });
    }

    // Public awaitable used by 18-navigation.js popstate handler (Fix
    // #2). Resolves to ``{view_slug, state_json, ts}`` on hit, or
    // ``null`` on miss / unavailable bridge. Does NOT mutate
    // ``_pendingStateSnapshot`` — the caller owns that slot.
    function lookupStateForUrl(url) {
        var bridge = _swBridge();
        if (!bridge || typeof bridge.lookupState !== 'function') {
            return Promise.resolve(null);
        }
        return bridge.lookupState(url).then(function (reply) {
            if (!reply || !reply.hit) return null;
            return {
                view_slug: reply.view_slug,
                state_json: reply.state_json,
                ts: reply.ts,
            };
        }).catch(function () { return null; });
    }

    if (typeof window !== 'undefined') {
        // before-navigate — fired by 18-navigation.js on live_redirect /
        // dj-navigate / popstate-cross-view. Capture BEFORE the WS frame
        // goes out so the cache entry for the CURRENT URL is fresh.
        window.addEventListener('djust:before-navigate', _captureBeforeNavigate);
        // popstate — lookup the cached snapshot for the destination URL
        // so 18-navigation.js can attach it to the outbound
        // live_redirect_mount frame.
        window.addEventListener('popstate', _popstateLookup);
    }

    globalThis.djust._stateSnapshot = {
        _capture: _captureBeforeNavigate,
        _lookupOnPopstate: _popstateLookup,
        _serialize: _serializeCurrentState,
        lookupStateForUrl: lookupStateForUrl,
    };
})();
