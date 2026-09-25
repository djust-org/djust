/**
 * #2948 — service-worker state-cache lifetime and identity clearing
 * (backported from the 1.3 line's ADR-038 D-n).
 *
 * Runs the real client bundle into the real service worker through the
 * postMessage bridge and asserts on the bytes that land in CacheStorage.
 *
 * Contracts under test:
 *   - the worker enforces a max age on state entries and deletes expired
 *     ones on read (the server's DJUST_STATE_SNAPSHOT_MAX_AGE, default 3600s);
 *   - an identity change or logout clears the state, VDOM and shell caches.
 */
import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import vm from 'vm';

const SW_SRC = fs.readFileSync('./python/djust/static/djust/service-worker.js', 'utf-8');
const CLIENT_SRC = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const ORIGIN = 'http://localhost:8000';
const STATE_CACHE = 'djust-state-cache-v1';
const VDOM_CACHE = 'djust-vdom-cache-v1';
const SHELL_CACHE = 'djust-shell-v1';
const VIEW = 'app.views.Orders';
const OTHER_VIEW = 'app.views.Other';

const tick = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

class HeadersShim {
    constructor(init) {
        this._map = new Map();
        if (init instanceof HeadersShim) {
            for (const [k, v] of init._map) this._map.set(k, v);
        } else if (init) {
            for (const [k, v] of Object.entries(init)) this._map.set(k.toLowerCase(), String(v));
        }
    }
    get(k) { return this._map.has(k.toLowerCase()) ? this._map.get(k.toLowerCase()) : null; }
    set(k, v) { this._map.set(k.toLowerCase(), String(v)); }
}

class ResponseShim {
    constructor(body, init) {
        this._body = body;
        this.status = (init && init.status) || 200;
        this.headers = new HeadersShim(init && init.headers);
    }
    async json() { return JSON.parse(this._body); }
    async text() { return String(this._body); }
    clone() { return new ResponseShim(this._body, { status: this.status, headers: this.headers }); }
}

function createWorker() {
    const store = new Map(); // cacheName -> Map<key, ResponseShim>
    const caches = {
        async open(name) {
            if (!store.has(name)) store.set(name, new Map());
            const c = store.get(name);
            return {
                async match(k) { return c.get(k); },
                async put(k, r) { c.set(k, r); },
                async delete(k) { return c.delete(k); },
            };
        },
        async delete(name) { return store.delete(name); },
    };
    const clock = { offsetMs: 0 };
    class WorkerDate extends Date {
        static now() { return Date.now() + clock.offsetMs; }
    }
    const network = { response: null };
    const listeners = {};
    const pending = [];
    const sandbox = {
        self: {
            addEventListener(name, fn) { listeners[name] = fn; },
            skipWaiting: () => Promise.resolve(),
            clients: { claim: () => Promise.resolve(), matchAll: () => Promise.resolve([]) },
            registration: { scope: ORIGIN + '/' },
            location: { origin: ORIGIN },
        },
        caches,
        fetch: async () => network.response,
        Response: ResponseShim,
        Headers: HeadersShim,
        Request: class {},
        module: { exports: {} },
        setTimeout, clearTimeout,
        console: { log() {}, warn() {}, error() {} },
        Date: WorkerDate, Math, JSON, Promise, Map, Set,
    };
    vm.createContext(sandbox);
    vm.runInContext(SW_SRC, sandbox);
    return {
        store,
        clock,
        listeners,
        pending,
        async settle() {
            for (let i = 0; i < 4; i++) {
                await Promise.all(pending.splice(0));
                await tick(5);
            }
        },
        /** A top-level navigation GET through the worker's fetch handler. */
        async navigate(html, headers) {
            network.response = new ResponseShim(html, { status: 200, headers: headers || {} });
            let responded = null;
            listeners.fetch({
                request: {
                    mode: 'navigate',
                    method: 'GET',
                    headers: new HeadersShim({}),
                },
                respondWith(p) { responded = p; },
            });
            const response = await responded;
            await tick(5);
            return response;
        },
    };
}

/** Every raw byte string persisted in one cache (or all caches). */
function bytesIn(store, cacheName) {
    const out = [];
    for (const [name, c] of store) {
        if (cacheName && name !== cacheName) continue;
        for (const [, r] of c) out.push(String(r._body));
    }
    return out.join('\n');
}

async function openPage(worker, path) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="${VIEW}" dj-root><p>content</p></div>
         </body></html>`,
        { url: ORIGIN + path, runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; this.sent = []; }
            send(d) { this.sent.push(d); }
            close() {}
        };
        window.location.reload = function() { window.__reloaded = true; };
        window.requestAnimationFrame = function(cb) { return setTimeout(cb, 0); };
        window.scrollTo = function() {};
        if (typeof window.CSS === 'undefined') {
            window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&') };
        }
    `);
    window.console = { log() {}, warn() {}, error() {}, debug() {}, info() {} };
    const pageListeners = [];
    const source = {
        type: 'window',
        url: ORIGIN + path,
        // The worker's reply reaches the page's navigator.serviceWorker
        // message listeners, as postMessage would deliver it.
        postMessage(reply) {
            const data = JSON.parse(JSON.stringify(reply));
            setTimeout(() => { for (const fn of pageListeners) fn({ data }); }, 0);
        },
    };
    const controller = {
        postMessage(msg) {
            worker.listeners.message({
                data: JSON.parse(JSON.stringify(msg)),
                source,
                waitUntil: (p) => worker.pending.push(p),
            });
        },
    };
    Object.defineProperty(window.navigator, 'serviceWorker', {
        configurable: true,
        value: {
            controller,
            addEventListener(name, fn) { if (name === 'message') pageListeners.push(fn); },
            removeEventListener() {},
            register: () => Promise.resolve({}),
            ready: Promise.resolve({ active: controller }),
        },
    });
    window.eval(CLIENT_SRC);
    await tick(50);
    window.djust._routeMap = { '/orders': VIEW, '/other/': OTHER_VIEW };
    window.djust._sw.initVdomCache();
    window.djust._sw.initStateSnapshot();
    const ws = window.djust.liveViewInstance;
    ws.primaryViewPath = VIEW;
    ws.viewMounted = true;
    return {
        window,
        ws,
        async send(frame) {
            ws.skipMountHtml = false;
            try { await ws.handleMessage(frame); } catch (_e) { /* UI paths irrelevant here */ }
            await tick(5);
            await worker.settle();
        },
        async captureCurrent(fromUrl) {
            window.dispatchEvent(new window.CustomEvent('djust:before-navigate', {
                detail: { fromUrl },
            }));
            await worker.settle();
        },
        sentFrames() {
            return ws.ws.sent.map((raw) => JSON.parse(raw));
        },
    };
}

function mountFrame(extra) {
    return { type: 'mount', session_id: 's-1', view: VIEW, version: 1, ...extra };
}

describe('#2948: state-cache lifetime', () => {
    async function capturedPage(extra) {
        const worker = createWorker();
        const page = await openPage(worker, '/orders');
        await page.send(mountFrame({ state_snapshot_signed: 'TTL_TOKEN', ...extra }));
        await page.captureCurrent('/orders');
        expect(bytesIn(worker.store, STATE_CACHE)).toContain('TTL_TOKEN');
        return { worker, page };
    }

    it('an entry past the default max age (3600s) is not returned and is deleted', async () => {
        const { worker, page } = await capturedPage({});
        worker.clock.offsetMs = 3599 * 1000;
        const fresh = await page.window.djust._sw.lookupState('/orders');
        expect(fresh.hit).toBe(true);
        expect(fresh.state_json).toBe('TTL_TOKEN');

        worker.clock.offsetMs = 3601 * 1000;
        const expired = await page.window.djust._sw.lookupState('/orders');
        await worker.settle();
        expect(expired.hit).toBe(false);
        expect(expired.state_json).toBeFalsy();
        expect(worker.store.get(STATE_CACHE)?.has('/orders')).toBe(false);
        expect(bytesIn(worker.store)).not.toContain('TTL_TOKEN');
    });

    it('the server-sent snapshot max age governs the lookup', async () => {
        const { worker, page } = await capturedPage({ state_snapshot_max_age: 60 });
        worker.clock.offsetMs = 59 * 1000;
        expect((await page.window.djust._sw.lookupState('/orders')).hit).toBe(true);

        worker.clock.offsetMs = 61 * 1000;
        const expired = await page.window.djust._sw.lookupState('/orders');
        await worker.settle();
        expect(expired.hit).toBe(false);
        expect(worker.store.get(STATE_CACHE)?.has('/orders')).toBe(false);
    });
});

describe('#2948: identity change and logout clear the caches', () => {
    async function populated(identity) {
        const worker = createWorker();
        const page = await openPage(worker, '/orders');
        await page.send(mountFrame({
            html: '<p>VDOM_BEFORE</p>',
            state_snapshot_signed: 'STATE_BEFORE',
            ...(identity ? { sw_identity: identity } : {}),
        }));
        await page.captureCurrent('/orders');
        await worker.navigate(
            '<html><body><nav>SHELL_BEFORE</nav><main>x</main></body></html>',
        );
        const all = bytesIn(worker.store);
        for (const s of ['VDOM_BEFORE', 'STATE_BEFORE', 'SHELL_BEFORE']) expect(all).toContain(s);
        return { worker, page };
    }

    it('an unchanged identity keeps all three caches', async () => {
        const { worker, page } = await populated('IDENTITY_A');
        await page.send(mountFrame({ sw_identity: 'IDENTITY_A' }));
        const all = bytesIn(worker.store);
        for (const s of ['VDOM_BEFORE', 'STATE_BEFORE', 'SHELL_BEFORE']) expect(all).toContain(s);
    });

    it('a changed identity clears the state, VDOM and shell caches', async () => {
        const { worker, page } = await populated('IDENTITY_A');
        await page.send(mountFrame({ sw_identity: 'IDENTITY_B', html: '<p>VDOM_AFTER</p>' }));
        const all = bytesIn(worker.store);
        for (const s of ['VDOM_BEFORE', 'STATE_BEFORE', 'SHELL_BEFORE']) expect(all).not.toContain(s);
        // The new identity's own mount is cached after the clear, not before.
        expect(bytesIn(worker.store, VDOM_CACHE)).toContain('VDOM_AFTER');
    });

    it('a disappearing identity (logout) clears the state, VDOM and shell caches', async () => {
        const { worker, page } = await populated('IDENTITY_A');
        await page.send(mountFrame({}));
        const all = bytesIn(worker.store);
        for (const s of ['VDOM_BEFORE', 'STATE_BEFORE', 'SHELL_BEFORE']) expect(all).not.toContain(s);
    });
});
