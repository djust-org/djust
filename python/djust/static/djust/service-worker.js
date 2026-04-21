// djust service worker (v0.5.0)
// =============================
// Two opt-in features:
//
// 1. Instant page shell — on the first successful navigate request, the SW
//    caches the response split into "shell" (everything outside <main>) and
//    "main" (the <main> inner HTML). Subsequent navigates are answered
//    immediately from the cached shell; the client then fetches the current
//    URL with `X-Djust-Main-Only: 1` and swaps in the fresh <main> contents.
//
// 2. WebSocket reconnection bridge — the client posts outgoing WS messages
//    to the SW when the socket is not OPEN. The SW buffers them in memory
//    (per connection_id, capped at 50). On reconnect the client issues a
//    DJUST_DRAIN and replays each message in order.
//
// Opt-in only: this file is NOT auto-registered. Users call
// `djust.registerServiceWorker({ instantShell: true, reconnectionBridge: true })`
// from their own init code.

const SHELL_CACHE = 'djust-shell-v1';
const SHELL_KEY = '__djust_shell__';
const BUFFER_CAP = 50;

// connectionId (string) -> message[] (serialized WS payloads as strings)
const RECONNECT_BUFFER = new Map();

// ---------------------------------------------------------------------------
// Install / activate — take control immediately
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// ---------------------------------------------------------------------------
// Instant shell
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
    const req = event.request;
    // Only intercept top-level navigation GETs. All other requests pass through.
    if (req.mode !== 'navigate' || req.method !== 'GET') {
        return;
    }
    // Never intercept the "main-only" sub-request — it is not a navigation
    // but some browsers still set mode=navigate for header-only variants.
    if (req.headers.get('X-Djust-Main-Only') === '1') {
        return;
    }
    event.respondWith(handleNavigate(req));
});

async function handleNavigate(request) {
    let cache;
    try {
        cache = await caches.open(SHELL_CACHE);
    } catch (err) {
        // Cache API unavailable (private mode on some browsers) — fall back.
        return fetch(request);
    }

    const cached = await cache.match(SHELL_KEY);
    if (cached) {
        // Warm cache: serve the shell with a placeholder <main>. The client
        // detects `data-djust-shell-placeholder="1"` and fetches the real
        // main content via `X-Djust-Main-Only: 1`.
        return cached.clone();
    }

    // Cold cache: fetch the real response, cache a shell version, and return
    // the original response unchanged so the first load is not degraded.
    let response;
    try {
        response = await fetch(request);
    } catch (err) {
        // Network failure — propagate so the browser shows its own error.
        throw err;
    }
    try {
        const cloned = response.clone();
        const text = await cloned.text();
        const { shell } = splitShellAndMain(text);
        // Only cache when we actually extracted a <main>; otherwise the page
        // has no swap target and caching a shell-less HTML is pointless.
        if (shell !== null) {
            const headers = new Headers(response.headers);
            headers.set('X-Djust-Shell-Cached', '1');
            await cache.put(
                SHELL_KEY,
                new Response(shell, {
                    status: 200,
                    headers: headers,
                })
            );
        }
    } catch (err) {
        // Ignore cache-population failures — user still gets the live page.
    }
    return response;
}

// Returns { shell, main } — shell is HTML with <main> replaced by a
// placeholder element; main is the inner HTML of the original <main>.
// If the input has no <main>, returns { shell: null, main: null }.
function splitShellAndMain(html) {
    const mainRe = /<main\b[^>]*>([\s\S]*?)<\/main>/i;
    const match = html.match(mainRe);
    if (!match) {
        return { shell: null, main: null };
    }
    const placeholder = '<main id="djust-main" data-djust-shell-placeholder="1"></main>';
    const shell = html.replace(mainRe, placeholder);
    return { shell: shell, main: match[1] };
}

// ---------------------------------------------------------------------------
// Reconnection bridge — buffer outgoing WS messages during disconnect
// ---------------------------------------------------------------------------

self.addEventListener('message', (event) => {
    const msg = event.data;
    if (!msg || typeof msg !== 'object' || !msg.type) {
        return;
    }

    if (msg.type === 'DJUST_BUFFER') {
        const connId = String(msg.connectionId || 'default');
        const buf = RECONNECT_BUFFER.get(connId) || [];
        buf.push(msg.payload);
        // Cap at BUFFER_CAP — drop oldest to keep bounded.
        while (buf.length > BUFFER_CAP) {
            buf.shift();
        }
        RECONNECT_BUFFER.set(connId, buf);
        return;
    }

    if (msg.type === 'DJUST_DRAIN') {
        const connId = String(msg.connectionId || 'default');
        const buf = RECONNECT_BUFFER.get(connId) || [];
        RECONNECT_BUFFER.delete(connId);
        const reply = {
            type: 'DJUST_DRAIN_REPLY',
            connectionId: connId,
            messages: buf,
        };
        // Prefer the source port if available; fall back to broadcasting
        // to all controlled clients.
        if (event.source && typeof event.source.postMessage === 'function') {
            event.source.postMessage(reply);
            return;
        }
        self.clients
            .matchAll({ includeUncontrolled: false })
            .then((clients) => {
                for (const client of clients) {
                    client.postMessage(reply);
                }
            });
        return;
    }

    if (msg.type === 'DJUST_CLEAR_SHELL') {
        // Allow the client to invalidate the cached shell (e.g. after a
        // deployment or explicit "refresh shell" action).
        caches.open(SHELL_CACHE).then((c) => c.delete(SHELL_KEY)).catch(() => {});
        return;
    }
});

// Exposed for tests in module environments (Node). Harmless in browser.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        splitShellAndMain: splitShellAndMain,
        _internal: {
            SHELL_CACHE: SHELL_CACHE,
            SHELL_KEY: SHELL_KEY,
            BUFFER_CAP: BUFFER_CAP,
            RECONNECT_BUFFER: RECONNECT_BUFFER,
        },
    };
}
