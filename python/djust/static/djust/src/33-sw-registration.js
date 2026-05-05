// ============================================================================
// Service Worker Registration — opt-in instant shell + reconnection bridge
// ============================================================================
// Users explicitly opt in from their own init code:
//
//     djust.registerServiceWorker({
//         instantShell: true,
//         reconnectionBridge: true,
//     });
//
// The SW itself is served from /static/djust/service-worker.js and is NOT
// part of the client.js bundle (SW scripts must be separate files).
//
// Instant-shell swap — what works and what doesn't:
//
// - `dj-click`, `dj-submit`, `dj-change`, `dj-input`, and the rest of djust's
//   attribute-based event wiring all work post-swap. They use **document-level
//   event delegation** (not MutationObserver) — a single listener on `document`
//   dispatches based on `e.target.closest('[dj-click]')`, so newly inserted
//   nodes participate automatically without any per-element binding.
//
// - `dj-hook` elements need re-binding after a swap because each hook runs
//   per-element `mount`/`update` callbacks. After replacing the `<main>`
//   innerHTML, we call `djust.reinitAfterDOMUpdate(placeholder)` which scans
//   the swapped subtree for `[dj-hook]`, extracts colocated
//   `<script type="djust/hook">` definitions, and primes dj-virtual /
//   dj-viewport observers.
//
// - **Inline `<script>` tags inside `<main>` will NOT execute**. This is
//   standard browser behavior for `.innerHTML = …`: script nodes are inserted
//   into the tree but not evaluated. If you need JS to run after a shell
//   navigation, either (a) emit the `<script>` OUTSIDE `<main>` in the shell
//   layout so it runs on first load, (b) restructure as a `dj-hook` (which
//   will be re-bound automatically), or (c) use a page-level load listener
//   on the `djust:shell-swapped` CustomEvent dispatched after every swap.
//
// - `registerServiceWorker(options)` is **idempotent**: a second call returns
//   the cached registration promise without re-running `initInstantShell` or
//   `initReconnectionBridge`, so drain listeners and the WS sendMessage
//   patch are applied at most once. **Options from the second call are
//   ignored** — toggling `instantShell: false → true` across calls will NOT
//   start the shell client. Pass both flags on the first call, or reload.
//   If the first call failed (SW register() rejected), the cache is cleared
//   so a retry can succeed.

(function () {
    globalThis.djust = globalThis.djust || {};

    function _swAvailable() {
        return typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
    }

    function _genConnectionId() {
        return 'dj-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    }

    // -----------------------------------------------------------------
    // Instant shell client half
    // -----------------------------------------------------------------

    function initInstantShell() {
        if (!_swAvailable()) return;
        // Only swap when the page we're viewing is the SW-served shell.
        const placeholder = document.querySelector(
            'main[data-djust-shell-placeholder="1"]'
        );
        if (!placeholder) {
            return;
        }
        const url = window.location.href;
        fetch(url, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'X-Djust-Main-Only': '1' },
        })
            .then(function (res) {
                if (!res.ok) {
                    // Server did not honor the header — fall back to full reload.
                    window.location.reload();
                    return null;
                }
                return res.text();
            })
            .then(function (html) {
                if (html === null || html === undefined) return;
                // Replace the placeholder <main> with a real <main> carrying the
                // fresh inner HTML. We use the existing placeholder element
                // so id/classes set by the shell template are preserved.
                placeholder.removeAttribute('data-djust-shell-placeholder');
                // codeql[js/xss] -- html is the server-rendered main content
                // for the current URL (same-origin, trusted). No user input
                // reaches this point.
                placeholder.innerHTML = html;
                // Re-run djust's DOM-update hook: binds dj-hook elements,
                // extracts colocated <script type="djust/hook"> definitions,
                // and primes dj-virtual / dj-viewport observers in the swapped
                // region. Without this, dj-hook content inside <main> stays
                // inert after a shell navigation (dj-click / dj-submit keep
                // working because those use document-level event delegation
                // and don't need per-element binding).
                if (window.djust && typeof window.djust.reinitAfterDOMUpdate === 'function') {
                    try {
                        window.djust.reinitAfterDOMUpdate(placeholder);
                    } catch (e) {
                        if (globalThis.djustDebug) {
                            console.warn('[sw] reinitAfterDOMUpdate failed after shell swap', e);
                        }
                    }
                }
                // Notify the rest of the client that a shell-swap completed so
                // any listeners that care about navigation can react.
                window.dispatchEvent(new CustomEvent('djust:shell-swapped', {
                    detail: { url: url },
                }));
            })
            .catch(function (err) {
                if (globalThis.djustDebug) {
                    console.warn('[sw] instant shell swap failed; reloading', err);
                }
                window.location.reload();
            });
    }

    // -----------------------------------------------------------------
    // Reconnection bridge client half
    // -----------------------------------------------------------------

    function _waitForWs(callback, attempts) {
        attempts = attempts == null ? 50 : attempts;
        const ws = globalThis.djust && globalThis.djust.ws;
        if (ws) {
            callback(ws);
            return;
        }
        if (attempts <= 0) return;
        setTimeout(function () {
            _waitForWs(callback, attempts - 1);
        }, 100);
    }

    function initReconnectionBridge() {
        if (!_swAvailable()) return;
        const connectionId = _genConnectionId();
        const bridge = {
            connectionId: connectionId,
            bufferedCount: 0,
        };
        globalThis.djust._reconnectBridge = bridge;

        _waitForWs(function (ws) {
            // Monkey-patch sendMessage so that when the WS is not OPEN the
            // serialized payload is handed to the SW instead of dropped.
            // Skip the patch when the active transport is SSE: SSE's
            // sendMessage uses fetch (not a persistent socket), and ws.ws
            // is undefined, so the state check would always treat the
            // transport as closed and buffer every payload. (#1237)
            if (ws._djustBridgePatched) return;
            if (globalThis.djust && globalThis.djust.LiveViewSSE
                && ws instanceof globalThis.djust.LiveViewSSE) return;
            ws._djustBridgePatched = true;

            const originalSend = ws.sendMessage.bind(ws);
            ws.sendMessage = function (data) {
                const OPEN = (typeof WebSocket !== 'undefined') ? WebSocket.OPEN : 1;
                const state = ws.ws && ws.ws.readyState;
                if (state !== OPEN) {
                    let payload;
                    try {
                        payload = JSON.stringify(data);
                    } catch (e) {
                        if (globalThis.djustDebug) {
                            console.warn('[sw] cannot serialize WS payload', e);
                        }
                        return;
                    }
                    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
                        navigator.serviceWorker.controller.postMessage({
                            type: 'DJUST_BUFFER',
                            connectionId: connectionId,
                            payload: payload,
                        });
                        bridge.bufferedCount++;
                        if (globalThis.djustDebug) {
                            console.log('[sw] buffered WS payload (#' + bridge.bufferedCount + ')');
                        }
                    }
                    return;
                }
                return originalSend(data);
            };
        });

        // Listen for drain replies from the SW.
        if (navigator.serviceWorker) {
            navigator.serviceWorker.addEventListener('message', function (event) {
                const msg = event.data;
                if (!msg || msg.type !== 'DJUST_DRAIN_REPLY') return;
                if (msg.connectionId !== connectionId) return;
                const ws = globalThis.djust && globalThis.djust.ws;
                if (!ws || !ws.ws) return;
                const OPEN = (typeof WebSocket !== 'undefined') ? WebSocket.OPEN : 1;
                if (ws.ws.readyState !== OPEN) return;
                const messages = msg.messages || [];
                for (let i = 0; i < messages.length; i++) {
                    try {
                        // eslint-disable-next-line security/detect-object-injection
                        ws.ws.send(messages[i]);
                    } catch (e) {
                        if (globalThis.djustDebug) {
                            console.warn('[sw] replay send failed', e);
                        }
                        break;
                    }
                }
                bridge.bufferedCount = 0;
                if (globalThis.djustDebug) {
                    console.log('[sw] drained ' + messages.length + ' buffered WS payloads');
                }
            });
        }

        // When the WS (re)connects, request a drain from the SW.
        window.addEventListener('djust:ws-open', function () {
            if (navigator.serviceWorker && navigator.serviceWorker.controller) {
                navigator.serviceWorker.controller.postMessage({
                    type: 'DJUST_DRAIN',
                    connectionId: connectionId,
                });
            }
        });
    }

    // -----------------------------------------------------------------
    // v0.6.0 — VDOM cache + state snapshot
    // -----------------------------------------------------------------

    // Map requestId -> resolve() function. Populated by lookupVdom /
    // lookupState; drained by the SW-message listener below.
    const _pendingLookups = {};
    let _requestIdCounter = 0;

    function _nextRequestId(prefix) {
        _requestIdCounter += 1;
        return (prefix || 'rq') + '-' + _requestIdCounter + '-' + Date.now().toString(36);
    }

    function _swController() {
        if (!navigator.serviceWorker) return null;
        return navigator.serviceWorker.controller;
    }

    function initVdomCache() {
        if (!_swAvailable()) return;
        if (!navigator.serviceWorker) return;
        navigator.serviceWorker.addEventListener('message', function (event) {
            const data = event.data;
            if (!data || data.type !== 'VDOM_CACHE_REPLY') return;
            const rid = data.requestId;
            // eslint-disable-next-line security/detect-object-injection
            if (rid && _pendingLookups[rid]) {
                try {
                    // eslint-disable-next-line security/detect-object-injection
                    _pendingLookups[rid](data);
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[sw] VDOM_CACHE_REPLY handler threw', e);
                    }
                }
                // eslint-disable-next-line security/detect-object-injection
                delete _pendingLookups[rid];
            }
        });
    }

    function initStateSnapshot() {
        if (!_swAvailable()) return;
        if (!navigator.serviceWorker) return;
        navigator.serviceWorker.addEventListener('message', function (event) {
            const data = event.data;
            if (!data || data.type !== 'STATE_SNAPSHOT_REPLY') return;
            const rid = data.requestId;
            // eslint-disable-next-line security/detect-object-injection
            if (rid && _pendingLookups[rid]) {
                try {
                    // eslint-disable-next-line security/detect-object-injection
                    _pendingLookups[rid](data);
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[sw] STATE_SNAPSHOT_REPLY handler threw', e);
                    }
                }
                // eslint-disable-next-line security/detect-object-injection
                delete _pendingLookups[rid];
            }
        });
    }

    function cacheVdom(url, html, version) {
        const ctrl = _swController();
        if (!ctrl) return;
        ctrl.postMessage({
            type: 'VDOM_CACHE',
            url: url,
            html: html,
            version: typeof version === 'number' ? version : 0,
            ts: Date.now(),
        });
    }

    function lookupVdom(url) {
        return new Promise(function (resolve) {
            const ctrl = _swController();
            if (!ctrl) {
                resolve({ hit: false, stale: false, html: null });
                return;
            }
            const rid = _nextRequestId('vdom');
            // eslint-disable-next-line security/detect-object-injection
            _pendingLookups[rid] = function (reply) { resolve(reply); };
            ctrl.postMessage({
                type: 'VDOM_CACHE_LOOKUP',
                requestId: rid,
                url: url,
            });
            // Safety timeout so callers are never stuck if the SW goes away.
            setTimeout(function () {
                // eslint-disable-next-line security/detect-object-injection
                if (_pendingLookups[rid]) {
                    // eslint-disable-next-line security/detect-object-injection
                    delete _pendingLookups[rid];
                    resolve({ hit: false, stale: false, html: null });
                }
            }, 500);
        });
    }

    function captureState(url, viewSlug, stateJson) {
        const ctrl = _swController();
        if (!ctrl) return;
        // Clamp payload at 64 KB — defense-in-depth against accidental
        // dumps of large collections. SW also enforces 256 KB.
        if (typeof stateJson !== 'string') return;
        if (stateJson.length > 64 * 1024) {
            if (globalThis.djustDebug) {
                console.warn('[sw] STATE_SNAPSHOT payload > 64KB; dropping');
            }
            return;
        }
        ctrl.postMessage({
            type: 'STATE_SNAPSHOT',
            url: url,
            view_slug: viewSlug,
            state_json: stateJson,
            ts: Date.now(),
        });
    }

    function lookupState(url) {
        return new Promise(function (resolve) {
            const ctrl = _swController();
            if (!ctrl) {
                resolve({ hit: false, view_slug: null, state_json: null });
                return;
            }
            const rid = _nextRequestId('state');
            // eslint-disable-next-line security/detect-object-injection
            _pendingLookups[rid] = function (reply) { resolve(reply); };
            ctrl.postMessage({
                type: 'STATE_SNAPSHOT_LOOKUP',
                requestId: rid,
                url: url,
            });
            setTimeout(function () {
                // eslint-disable-next-line security/detect-object-injection
                if (_pendingLookups[rid]) {
                    // eslint-disable-next-line security/detect-object-injection
                    delete _pendingLookups[rid];
                    resolve({ hit: false, view_slug: null, state_json: null });
                }
            }, 500);
        });
    }

    // -----------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------

    // Idempotency guard for registerServiceWorker. Calling it twice is a
    // common init pattern (theme switch, settings toggle, dev-mode reload)
    // and without this guard each call adds another drain listener and
    // another sendMessage wrapper, causing buffered replays to double.
    let _registerPromise = null;
    let _bridgeInitialized = false;
    let _shellInitialized = false;
    let _vdomCacheInitialized = false;
    let _stateSnapshotInitialized = false;

    globalThis.djust.registerServiceWorker = function (options) {
        options = options || {};
        if (_registerPromise) {
            if (globalThis.djustDebug) {
                console.log('[sw] registerServiceWorker called again; returning cached registration');
            }
            return _registerPromise;
        }
        if (!_swAvailable()) {
            if (globalThis.djustDebug) {
                console.warn('[sw] navigator.serviceWorker unavailable; opt-in features disabled');
            }
            // Don't cache a null — allow a later call after the env gains SW
            // support (e.g. polyfill) to still try.
            return Promise.resolve(null);
        }
        const swUrl = options.swUrl || '/static/djust/service-worker.js';
        const scope = options.scope || '/';
        _registerPromise = (async function () {
            let registration = null;
            try {
                registration = await navigator.serviceWorker.register(swUrl, { scope: scope });
            } catch (err) {
                if (globalThis.djustDebug) {
                    console.warn('[sw] registration failed', err);
                }
                // Reset so a later call after fixing the cause can retry.
                _registerPromise = null;
                return null;
            }
            if (options.instantShell && !_shellInitialized) {
                _shellInitialized = true;
                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', initInstantShell);
                } else {
                    initInstantShell();
                }
            }
            if (options.reconnectionBridge && !_bridgeInitialized) {
                _bridgeInitialized = true;
                initReconnectionBridge();
            }
            if (options.vdomCache && !_vdomCacheInitialized) {
                _vdomCacheInitialized = true;
                initVdomCache();
            }
            if (options.stateSnapshot && !_stateSnapshotInitialized) {
                _stateSnapshotInitialized = true;
                initStateSnapshot();
            }
            return registration;
        })();
        return _registerPromise;
    };

    // Exposed for tests and for the navigation / popstate code paths.
    globalThis.djust._sw = {
        initInstantShell: initInstantShell,
        initReconnectionBridge: initReconnectionBridge,
        initVdomCache: initVdomCache,
        initStateSnapshot: initStateSnapshot,
        cacheVdom: cacheVdom,
        lookupVdom: lookupVdom,
        captureState: captureState,
        lookupState: lookupState,
    };
})();
