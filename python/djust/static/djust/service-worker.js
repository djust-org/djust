// djust service worker (v0.6.0)
// =============================
// Opt-in features:
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
// 3. VDOM patch cache (v0.6.0) — per-URL HTML snapshots served instantly on
//    popstate / back navigation, before the WebSocket mount reply arrives.
//    TTL enforced on lookup; LRU capped on insertion.
//
// 4. State snapshot cache (v0.6.0) — per-URL JSON snapshots of public
//    LiveView state, posted on before-navigate and restored on popstate.
//    Opt-in per-view via `enable_state_snapshot = True` on the server.
//
// Opt-in only: this file is NOT auto-registered. Users call
// `djust.registerServiceWorker({ instantShell, reconnectionBridge, vdomCache, stateSnapshot })`
// from their own init code.

const SHELL_CACHE = 'djust-shell-v1';
const SHELL_KEY = '__djust_shell__';
const BUFFER_CAP = 50;

// v0.6.0 advanced caches.
const VDOM_CACHE = 'djust-vdom-cache-v1';
const STATE_CACHE = 'djust-state-cache-v1';
// LRU order tracking — Map preserves insertion order; we rotate on access.
const VDOM_LRU = new Map();
const STATE_LRU = new Map();
// Defaults; client can override via VDOM_CONFIG message.
let VDOM_TTL_MS = 1800 * 1000; // 30 minutes
let VDOM_MAX_ENTRIES = 50;
let STATE_MAX_ENTRIES = 50;
// Size cap for client-submitted state_json (defense in depth).
const STATE_JSON_MAX_BYTES = 256 * 1024;

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
    // Defense-in-depth origin check: only accept messages from WindowClient
    // sources in this service worker's registration scope. Service workers
    // are inherently same-origin (they cannot be loaded cross-origin), but
    // restricting to WindowClient rejects unexpected source types and the
    // scope check adds a belt-and-braces barrier against a compromised
    // same-origin frame outside the SW's scope.
    //
    // Explicit event.origin comparison satisfies CodeQL's
    // js/missing-origin-check (alert #2170). event.origin is populated for
    // Window/Worker clients — reject anything cross-origin on the off chance
    // it reaches us, even though the platform already forbids cross-origin
    // service-worker messaging.
    if (event.origin && event.origin !== self.location.origin) {
        return;
    }
    if (!event.source || event.source.type !== 'window') {
        return;
    }
    if (
        self.registration &&
        self.registration.scope &&
        event.source.url &&
        event.source.url.indexOf(self.registration.scope) !== 0
    ) {
        return;
    }
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

    // -----------------------------------------------------------------
    // v0.6.0 advanced features — VDOM cache + state snapshot
    // -----------------------------------------------------------------

    if (msg.type === 'VDOM_CONFIG') {
        // Client-side config propagation. Accepts {ttl_seconds, max_entries}.
        if (typeof msg.ttl_seconds === 'number' && msg.ttl_seconds > 0) {
            VDOM_TTL_MS = Math.floor(msg.ttl_seconds) * 1000;
        }
        if (typeof msg.max_entries === 'number' && msg.max_entries >= 1) {
            VDOM_MAX_ENTRIES = Math.floor(msg.max_entries);
        }
        return;
    }

    if (msg.type === 'VDOM_CACHE') {
        if (!msg.url || typeof msg.html !== 'string') return;
        putWithLRU(VDOM_CACHE, VDOM_LRU, msg.url, {
            url: msg.url,
            html: msg.html,
            version: typeof msg.version === 'number' ? msg.version : 0,
            ts: typeof msg.ts === 'number' ? msg.ts : Date.now(),
        }, VDOM_MAX_ENTRIES).catch(() => {});
        return;
    }

    if (msg.type === 'VDOM_CACHE_LOOKUP') {
        lookupCached(VDOM_CACHE, msg.url).then((entry) => {
            const now = Date.now();
            const ts = entry && typeof entry.ts === 'number' ? entry.ts : 0;
            const stale = entry ? now - ts > VDOM_TTL_MS : false;
            const reply = {
                type: 'VDOM_CACHE_REPLY',
                requestId: msg.requestId,
                hit: !!entry && !stale,
                stale: !!entry && stale,
                html: entry && !stale ? entry.html : null,
                version: entry && !stale ? entry.version : null,
                ts: ts,
            };
            postReply(event, reply);
        }).catch(() => {
            postReply(event, {
                type: 'VDOM_CACHE_REPLY',
                requestId: msg.requestId,
                hit: false,
                stale: false,
                html: null,
                version: null,
                ts: 0,
            });
        });
        return;
    }

    if (msg.type === 'STATE_SNAPSHOT') {
        if (!msg.url || typeof msg.state_json !== 'string') return;
        // Size cap — belt-and-braces against pathological payloads.
        if (msg.state_json.length > STATE_JSON_MAX_BYTES) {
            return;
        }
        putWithLRU(STATE_CACHE, STATE_LRU, msg.url, {
            url: msg.url,
            view_slug: typeof msg.view_slug === 'string' ? msg.view_slug : '',
            state_json: msg.state_json,
            ts: typeof msg.ts === 'number' ? msg.ts : Date.now(),
        }, STATE_MAX_ENTRIES).catch(() => {});
        return;
    }

    if (msg.type === 'STATE_SNAPSHOT_LOOKUP') {
        lookupCached(STATE_CACHE, msg.url).then((entry) => {
            const reply = {
                type: 'STATE_SNAPSHOT_REPLY',
                requestId: msg.requestId,
                hit: !!entry,
                view_slug: entry ? entry.view_slug : null,
                state_json: entry ? entry.state_json : null,
                ts: entry ? entry.ts : 0,
            };
            postReply(event, reply);
        }).catch(() => {
            postReply(event, {
                type: 'STATE_SNAPSHOT_REPLY',
                requestId: msg.requestId,
                hit: false,
                view_slug: null,
                state_json: null,
                ts: 0,
            });
        });
        return;
    }

    if (msg.type === 'DJUST_CLEAR_VDOM_CACHE') {
        caches.delete(VDOM_CACHE).catch(() => {});
        VDOM_LRU.clear();
        return;
    }

    if (msg.type === 'DJUST_CLEAR_STATE_CACHE') {
        caches.delete(STATE_CACHE).catch(() => {});
        STATE_LRU.clear();
        return;
    }
});

// ---------------------------------------------------------------------------
// v0.6.0 helpers
// ---------------------------------------------------------------------------

// Insert into a named Cache with LRU eviction. The LRU tracking Map mirrors
// insertion order; when full we evict the oldest keys.
async function putWithLRU(cacheName, lru, url, body, maxEntries) {
    let cache;
    try {
        cache = await caches.open(cacheName);
    } catch (_e) {
        return; // Cache API unavailable
    }
    // Keep the Map in sync so an update moves the key to the end.
    if (lru.has(url)) {
        lru.delete(url);
    }
    lru.set(url, true);
    // Evict oldest until under the cap.
    while (lru.size > maxEntries) {
        const oldest = lru.keys().next().value;
        if (oldest === undefined) break;
        lru.delete(oldest);
        try {
            await cache.delete(oldest);
        } catch (_e) {
            // Best-effort eviction; ignore failures.
        }
    }
    const payload = JSON.stringify(body);
    const response = new Response(payload, {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
    });
    try {
        await cache.put(url, response);
    } catch (_e) {
        // Ignore cache write failures.
    }
}

async function lookupCached(cacheName, url) {
    if (!url) return null;
    let cache;
    try {
        cache = await caches.open(cacheName);
    } catch (_e) {
        return null;
    }
    let res;
    try {
        res = await cache.match(url);
    } catch (_e) {
        return null;
    }
    if (!res) return null;
    try {
        return await res.json();
    } catch (_e) {
        return null;
    }
}

function postReply(event, reply) {
    if (event && event.source && typeof event.source.postMessage === 'function') {
        try {
            event.source.postMessage(reply);
            return;
        } catch (_e) {
            // Fall through to broadcast.
        }
    }
    if (typeof self !== 'undefined' && self.clients && typeof self.clients.matchAll === 'function') {
        self.clients.matchAll({ includeUncontrolled: false })
            .then((clients) => {
                for (const client of clients) {
                    try {
                        client.postMessage(reply);
                    } catch (_e) {
                        // Best-effort per-client; continue.
                    }
                }
            })
            .catch(() => {});
    }
}

// Exposed for tests in module environments (Node). Harmless in browser.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        splitShellAndMain: splitShellAndMain,
        putWithLRU: putWithLRU,
        lookupCached: lookupCached,
        _internal: {
            SHELL_CACHE: SHELL_CACHE,
            SHELL_KEY: SHELL_KEY,
            BUFFER_CAP: BUFFER_CAP,
            RECONNECT_BUFFER: RECONNECT_BUFFER,
            VDOM_CACHE: VDOM_CACHE,
            STATE_CACHE: STATE_CACHE,
            VDOM_LRU: VDOM_LRU,
            STATE_LRU: STATE_LRU,
            getVdomTtlMs: () => VDOM_TTL_MS,
            getVdomMaxEntries: () => VDOM_MAX_ENTRIES,
            getStateMaxEntries: () => STATE_MAX_ENTRIES,
        },
    };
}
