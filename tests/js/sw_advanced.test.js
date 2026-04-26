/**
 * Tests for v0.6.0 Service Worker advanced features:
 *   - VDOM cache (VDOM_CACHE / VDOM_CACHE_LOOKUP / TTL expiry)
 *   - State snapshot (STATE_SNAPSHOT / STATE_SNAPSHOT_LOOKUP)
 *   - Mount-batch response handler (case 'mount_batch' in 03-websocket.js)
 *
 * The SW script is evaluated in a mock worker environment via vm; the
 * client bundle is evaluated in a JSDOM window. The SW's message router
 * is fired with synthetic events so we exercise handlers directly.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import vm from 'vm';

const SW_SRC = fs.readFileSync(
    './python/djust/static/djust/service-worker.js',
    'utf-8'
);
const CLIENT_SRC = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

// ---------------------------------------------------------------------------
// SW harness — run the SW source in a sandbox with a mock `self` and an
// in-memory Cache Storage impl sufficient for our tests.
// ---------------------------------------------------------------------------

function makeMockCacheStorage() {
    const caches = new Map(); // cacheName -> Map<url, Response-like>
    return {
        async open(name) {
            if (!caches.has(name)) caches.set(name, new Map());
            const store = caches.get(name);
            return {
                async match(key) {
                    return store.get(key) || undefined;
                },
                async put(key, resp) {
                    store.set(key, resp);
                },
                async delete(key) {
                    return store.delete(key);
                },
            };
        },
        async delete(name) {
            return caches.delete(name);
        },
        _storage: caches,
    };
}

function loadSw() {
    const listeners = {};
    // Minimal Response shim — only needs .json().
    class ResponseShim {
        constructor(body) {
            this._body = body;
        }
        async json() {
            return JSON.parse(this._body);
        }
        async text() {
            return String(this._body);
        }
        clone() {
            return new ResponseShim(this._body);
        }
    }
    const sandbox = {
        self: {
            addEventListener(name, fn) {
                listeners[name] = fn;
            },
            skipWaiting: () => Promise.resolve(),
            clients: {
                claim: () => Promise.resolve(),
                matchAll: () => Promise.resolve([]),
            },
            registration: {
                scope: 'http://localhost:8000/',
            },
            location: { origin: 'http://localhost:8000' },
        },
        caches: makeMockCacheStorage(),
        fetch: async () => new ResponseShim(''),
        Response: ResponseShim,
        Headers: global.Headers || class {},
        Request: global.Request || class {},
        module: { exports: {} },
        setTimeout,
        clearTimeout,
        console: { log: () => {}, warn: () => {}, error: () => {} },
        Date,
        Math,
        JSON,
        Promise,
        Map,
        Set,
    };
    sandbox.self.location = sandbox.self.location || { origin: 'http://localhost:8000' };
    vm.createContext(sandbox);
    vm.runInContext(SW_SRC, sandbox);
    return { listeners, exports: sandbox.module.exports, sandbox };
}

// Fire a message event through the SW's message listener.
function fireMessage(listeners, data, source) {
    const src = source || {
        postMessage: vi.fn(),
        type: 'window',
        url: 'http://localhost:8000/page',
    };
    if (src && !src.type) src.type = 'window';
    if (src && !src.url) src.url = 'http://localhost:8000/page';
    const event = {
        data: data,
        source: src,
    };
    return listeners.message(event);
}

// ---------------------------------------------------------------------------
// 1-3. VDOM cache put / lookup / TTL.
// ---------------------------------------------------------------------------

describe('service-worker: VDOM cache', () => {
    it('VDOM_CACHE stores HTML and VDOM_CACHE_LOOKUP returns hit', async () => {
        const { listeners } = loadSw();
        const src = { postMessage: vi.fn(), type: 'window', url: 'http://localhost:8000/' };
        // Put first.
        fireMessage(
            listeners,
            {
                type: 'VDOM_CACHE',
                url: '/orders',
                html: '<div>orders-html</div>',
                version: 3,
                ts: Date.now(),
            },
            src
        );
        // Wait microtasks.
        await new Promise((r) => setTimeout(r, 10));
        // Now look it up.
        fireMessage(
            listeners,
            { type: 'VDOM_CACHE_LOOKUP', requestId: 'rq-1', url: '/orders' },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        // src.postMessage receives the reply.
        expect(src.postMessage).toHaveBeenCalled();
        const reply = src.postMessage.mock.calls[0][0];
        expect(reply.type).toBe('VDOM_CACHE_REPLY');
        expect(reply.hit).toBe(true);
        expect(reply.html).toBe('<div>orders-html</div>');
        expect(reply.version).toBe(3);
        expect(reply.requestId).toBe('rq-1');
    });

    it('VDOM_CACHE_LOOKUP miss returns hit=false and null html', async () => {
        const { listeners } = loadSw();
        const src = { postMessage: vi.fn(), type: 'window', url: 'http://localhost:8000/' };
        fireMessage(
            listeners,
            { type: 'VDOM_CACHE_LOOKUP', requestId: 'rq-2', url: '/never-seen' },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        const reply = src.postMessage.mock.calls[0][0];
        expect(reply.hit).toBe(false);
        expect(reply.html).toBeNull();
    });

    it('VDOM_CACHE_LOOKUP rejects stale entries past TTL', async () => {
        const { listeners } = loadSw();
        const src = { postMessage: vi.fn(), type: 'window', url: 'http://localhost:8000/' };
        // Put with a very old timestamp (2 hours ago > 30 min TTL).
        const oldTs = Date.now() - 2 * 60 * 60 * 1000;
        fireMessage(
            listeners,
            {
                type: 'VDOM_CACHE',
                url: '/stale',
                html: '<div>ancient</div>',
                version: 1,
                ts: oldTs,
            },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        fireMessage(
            listeners,
            { type: 'VDOM_CACHE_LOOKUP', requestId: 'rq-3', url: '/stale' },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        const reply = src.postMessage.mock.calls[0][0];
        expect(reply.hit).toBe(false);
        expect(reply.stale).toBe(true);
        expect(reply.html).toBeNull();
    });
});

// ---------------------------------------------------------------------------
// 4-5. State snapshot put / lookup.
// ---------------------------------------------------------------------------

describe('service-worker: state snapshot', () => {
    it('STATE_SNAPSHOT stores payload; STATE_SNAPSHOT_LOOKUP returns hit', async () => {
        const { listeners } = loadSw();
        const src = { postMessage: vi.fn(), type: 'window', url: 'http://localhost:8000/' };
        fireMessage(
            listeners,
            {
                type: 'STATE_SNAPSHOT',
                url: '/inbox',
                view_slug: 'app.views.Inbox',
                state_json: '{"count":42}',
                ts: Date.now(),
            },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        fireMessage(
            listeners,
            { type: 'STATE_SNAPSHOT_LOOKUP', requestId: 'state-1', url: '/inbox' },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        const reply = src.postMessage.mock.calls[0][0];
        expect(reply.type).toBe('STATE_SNAPSHOT_REPLY');
        expect(reply.hit).toBe(true);
        expect(reply.view_slug).toBe('app.views.Inbox');
        expect(reply.state_json).toBe('{"count":42}');
    });

    it('STATE_SNAPSHOT over 256KB is dropped (defense in depth)', async () => {
        const { listeners } = loadSw();
        const src = { postMessage: vi.fn(), type: 'window', url: 'http://localhost:8000/' };
        const bigPayload = 'x'.repeat(300 * 1024); // 300 KB
        fireMessage(
            listeners,
            {
                type: 'STATE_SNAPSHOT',
                url: '/big',
                view_slug: 'app.Big',
                state_json: bigPayload,
                ts: Date.now(),
            },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        fireMessage(
            listeners,
            { type: 'STATE_SNAPSHOT_LOOKUP', requestId: 'big-1', url: '/big' },
            src
        );
        await new Promise((r) => setTimeout(r, 10));
        const reply = src.postMessage.mock.calls[0][0];
        expect(reply.hit).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// 6. putWithLRU capped at max entries.
// ---------------------------------------------------------------------------

describe('service-worker: LRU eviction', () => {
    it('putWithLRU evicts oldest when over max', async () => {
        const { exports, sandbox } = loadSw();
        const cacheName = '__test_lru__';
        const lru = new Map();
        // Populate 3 entries, cap=2.
        await exports.putWithLRU.call(sandbox, cacheName, lru, '/a', { v: 1 }, 2);
        await exports.putWithLRU.call(sandbox, cacheName, lru, '/b', { v: 2 }, 2);
        await exports.putWithLRU.call(sandbox, cacheName, lru, '/c', { v: 3 }, 2);
        // After insertion '/a' should have been evicted.
        expect(lru.has('/a')).toBe(false);
        expect(lru.has('/b')).toBe(true);
        expect(lru.has('/c')).toBe(true);
        // Cache-level check.
        const storage = sandbox.caches._storage.get(cacheName);
        expect(storage.has('/a')).toBe(false);
        expect(storage.has('/b')).toBe(true);
        expect(storage.has('/c')).toBe(true);
    });
});

// ---------------------------------------------------------------------------
// 7. mount_batch handler applies html to matching [data-djust-target].
// ---------------------------------------------------------------------------

describe('client: mount_batch handler', () => {
    function createEnv() {
        const dom = new JSDOM(
            `<!DOCTYPE html><html><body>
                <div dj-view="app.views.A" data-djust-target="t1"></div>
                <div dj-view="app.views.B" data-djust-target="t2"></div>
            </body></html>`,
            {
                url: 'http://localhost:8000/',
                runScripts: 'dangerously',
                pretendToBeVisual: true,
            }
        );
        const { window } = dom;
        window.console = {
            log: () => {},
            warn: () => {},
            error: () => {},
            debug: () => {},
            info: () => {},
        };
        window.history.pushState = () => {};
        window.history.replaceState = () => {};
        // CSS.escape polyfill — JSDOM doesn't provide it by default.
        if (typeof window.CSS === 'undefined') {
            window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&') };
        } else if (typeof window.CSS.escape !== 'function') {
            window.CSS.escape = (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
        }
        try {
            window.eval(CLIENT_SRC);
        } catch (e) {
            /* ignore unavailable APIs */
        }
        return { window };
    }

    it('applies html from mount_batch to each target by data-djust-target', async () => {
        const { window } = createEnv();
        // Call handleMessage directly on the WebSocket client.
        const ws = window.djust && (window.djust.liveViewInstance || window.djust.ws);
        // Not all environments instantiate LiveViewWebSocket automatically; fall
        // back to creating one directly via the exported class if needed.
        let handler = ws;
        if (!handler) {
            // Try to grab the class via the globals installed by client.js
            // Most builds expose LiveViewWebSocket on window.
            const LiveViewWebSocket = window.LiveViewWebSocket;
            if (LiveViewWebSocket) {
                handler = new LiveViewWebSocket();
            }
        }
        // Fix #5 — fail loudly instead of silently returning when the
        // client env isn't wired. A quietly-passing test masks the
        // missing LiveViewWebSocket export that would otherwise surface
        // as an immediate JSDOM regression.
        expect(handler).toBeTruthy();
        expect(typeof handler.handleMessage).toBe('function');
        await handler.handleMessage({
            type: 'mount_batch',
            session_id: 's-1',
            views: [
                { target_id: 't1', view: 'app.views.A', html: '<span>alpha</span>', version: 1 },
                { target_id: 't2', view: 'app.views.B', html: '<span>beta</span>', version: 2 },
            ],
            failed: [],
        });
        const t1 = window.document.querySelector('[data-djust-target="t1"]');
        const t2 = window.document.querySelector('[data-djust-target="t2"]');
        expect(t1.innerHTML).toContain('alpha');
        expect(t2.innerHTML).toContain('beta');
        expect(window.djust._clientVdomVersions.t1).toBe(1);
        expect(window.djust._clientVdomVersions.t2).toBe(2);
    });
});

// ---------------------------------------------------------------------------
// 8. registerServiceWorker options — vdomCache / stateSnapshot
// ---------------------------------------------------------------------------

describe('client: registerServiceWorker v0.6.0 options', () => {
    function createEnv() {
        const dom = new JSDOM(
            `<!DOCTYPE html><html><body></body></html>`,
            {
                url: 'http://localhost:8000/',
                runScripts: 'dangerously',
                pretendToBeVisual: true,
            }
        );
        const { window } = dom;
        window.console = {
            log: () => {},
            warn: () => {},
            error: () => {},
            debug: () => {},
            info: () => {},
        };
        window.history.pushState = () => {};
        window.history.replaceState = () => {};
        try {
            window.eval(CLIENT_SRC);
        } catch (e) { /* ignore */ }
        return { window };
    }

    it('exposes vdomCache + stateSnapshot senders on djust._sw', async () => {
        const { window } = createEnv();
        const sw = window.djust && window.djust._sw;
        expect(sw).toBeTruthy();
        expect(typeof sw.cacheVdom).toBe('function');
        expect(typeof sw.lookupVdom).toBe('function');
        expect(typeof sw.captureState).toBe('function');
        expect(typeof sw.lookupState).toBe('function');
        expect(typeof sw.initVdomCache).toBe('function');
        expect(typeof sw.initStateSnapshot).toBe('function');
    });

    it('lookupVdom resolves to {hit:false} when SW controller absent', async () => {
        const { window } = createEnv();
        const result = await window.djust._sw.lookupVdom('/any');
        expect(result.hit).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// 9. State snapshot end-to-end wiring (Fix #1) — mount frame carrying
//    public_state populates djust._clientState[view]; the serializer
//    reads from that slot for before-navigate capture.
// ---------------------------------------------------------------------------

describe('client: state snapshot capture wiring (Fix #1, Fix #9)', () => {
    function createEnv() {
        const dom = new JSDOM(
            `<!DOCTYPE html><html><body>
                <div dj-view="app.views.Orders"></div>
            </body></html>`,
            {
                url: 'http://localhost:8000/orders',
                runScripts: 'dangerously',
                pretendToBeVisual: true,
            }
        );
        const { window } = dom;
        window.console = {
            log: () => {}, warn: () => {}, error: () => {}, debug: () => {}, info: () => {},
        };
        window.history.pushState = () => {};
        window.history.replaceState = () => {};
        if (typeof window.CSS === 'undefined') {
            window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&') };
        }
        try { window.eval(CLIENT_SRC); } catch (_e) { /* ignore */ }
        return { window };
    }

    it('case mount stashes public_state into djust._clientState[view]', async () => {
        const { window } = createEnv();
        const LiveViewWebSocket = window.LiveViewWebSocket;
        expect(LiveViewWebSocket).toBeTruthy();
        const handler = new LiveViewWebSocket();
        await handler.handleMessage({
            type: 'mount',
            session_id: 's-1',
            view: 'app.views.Orders',
            version: 1,
            public_state: { query: 'open', page: 3 },
            // No html → skip innerHTML path, just state stash.
        });
        expect(window.djust._clientState).toBeTruthy();
        expect(window.djust._clientState['app.views.Orders']).toEqual({
            query: 'open',
            page: 3,
        });
    });

    it('serializer reads event.detail.fromUrl for source-URL keyed capture', () => {
        const { window } = createEnv();
        // Seed state + route map.
        window.djust._clientState = {
            'app.views.Orders': { query: 'open' },
        };
        window.djust._routeMap = { '/orders': 'app.views.Orders' };
        // Capture the captureState calls on the SW bridge.
        const calls = [];
        window.djust._sw = window.djust._sw || {};
        window.djust._sw.captureState = (url, slug, json) => {
            calls.push({ url: url, slug: slug, json: json });
        };
        // Fire the event with a fromUrl in detail (simulating
        // 18-navigation.js's outbound live_redirect dispatch).
        const evt = new window.CustomEvent('djust:before-navigate', {
            detail: { fromUrl: '/orders', toUrl: '/inbox' },
        });
        window.dispatchEvent(evt);
        expect(calls.length).toBe(1);
        expect(calls[0].url).toBe('/orders');
        expect(calls[0].slug).toBe('app.views.Orders');
        const parsed = JSON.parse(calls[0].json);
        expect(parsed.query).toBe('open');
    });
});

// ---------------------------------------------------------------------------
// 10. mount_batch.navigate entries route through the navigation
//     dispatcher (Fix #4). A view whose on_mount returns a redirect
//     now surfaces to the user.
// ---------------------------------------------------------------------------

describe('client: mount_batch navigate passthrough (Fix #4)', () => {
    it('navigate[] entries invoke navigation.handleNavigation', async () => {
        const dom = new JSDOM(
            `<!DOCTYPE html><html><body>
                <div dj-view="app.views.A" data-djust-target="t1"></div>
            </body></html>`,
            {
                url: 'http://localhost:8000/',
                runScripts: 'dangerously',
                pretendToBeVisual: true,
            }
        );
        const { window } = dom;
        window.console = { log: () => {}, warn: () => {}, error: () => {}, debug: () => {}, info: () => {} };
        window.history.pushState = () => {};
        window.history.replaceState = () => {};
        if (typeof window.CSS === 'undefined') {
            window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&') };
        }
        try { window.eval(CLIENT_SRC); } catch (_e) { /* ignore */ }
        // Stub navigation dispatcher so we can assert.
        const navCalls = [];
        window.djust.navigation = window.djust.navigation || {};
        window.djust.navigation.handleNavigation = (d) => { navCalls.push(d); };
        const handler = new window.LiveViewWebSocket();
        await handler.handleMessage({
            type: 'mount_batch',
            session_id: 's-1',
            views: [],
            failed: [],
            navigate: [
                { target_id: 't1', view: 'app.views.A', to: '/login' },
            ],
        });
        expect(navCalls.length).toBe(1);
        expect(navCalls[0].action).toBe('live_redirect');
        expect(navCalls[0].path).toBe('/login');
    });
});
