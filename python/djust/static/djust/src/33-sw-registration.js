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
        var placeholder = document.querySelector(
            'main[data-djust-shell-placeholder="1"]'
        );
        if (!placeholder) {
            return;
        }
        var url = window.location.href;
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
                // Notify the rest of the client that a shell-swap completed so
                // hooks / navigation code can re-run if needed.
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
        var ws = globalThis.djust && globalThis.djust.ws;
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
        var connectionId = _genConnectionId();
        var bridge = {
            connectionId: connectionId,
            bufferedCount: 0,
        };
        globalThis.djust._reconnectBridge = bridge;

        _waitForWs(function (ws) {
            // Monkey-patch sendMessage so that when the WS is not OPEN the
            // serialized payload is handed to the SW instead of dropped.
            if (ws._djustBridgePatched) return;
            ws._djustBridgePatched = true;

            var originalSend = ws.sendMessage.bind(ws);
            ws.sendMessage = function (data) {
                var OPEN = (typeof WebSocket !== 'undefined') ? WebSocket.OPEN : 1;
                var state = ws.ws && ws.ws.readyState;
                if (state !== OPEN) {
                    var payload;
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
                var msg = event.data;
                if (!msg || msg.type !== 'DJUST_DRAIN_REPLY') return;
                if (msg.connectionId !== connectionId) return;
                var ws = globalThis.djust && globalThis.djust.ws;
                if (!ws || !ws.ws) return;
                var OPEN = (typeof WebSocket !== 'undefined') ? WebSocket.OPEN : 1;
                if (ws.ws.readyState !== OPEN) return;
                var messages = msg.messages || [];
                for (var i = 0; i < messages.length; i++) {
                    try {
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
    // Public API
    // -----------------------------------------------------------------

    globalThis.djust.registerServiceWorker = async function (options) {
        options = options || {};
        if (!_swAvailable()) {
            if (globalThis.djustDebug) {
                console.warn('[sw] navigator.serviceWorker unavailable; opt-in features disabled');
            }
            return null;
        }
        var swUrl = options.swUrl || '/static/djust/service-worker.js';
        var scope = options.scope || '/';
        var registration = null;
        try {
            registration = await navigator.serviceWorker.register(swUrl, { scope: scope });
        } catch (err) {
            if (globalThis.djustDebug) {
                console.warn('[sw] registration failed', err);
            }
            return null;
        }
        if (options.instantShell) {
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', initInstantShell);
            } else {
                initInstantShell();
            }
        }
        if (options.reconnectionBridge) {
            initReconnectionBridge();
        }
        return registration;
    };

    // Exposed for tests.
    globalThis.djust._sw = {
        initInstantShell: initInstantShell,
        initReconnectionBridge: initReconnectionBridge,
    };
})();
