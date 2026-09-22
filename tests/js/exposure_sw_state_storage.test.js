/**
 * ADR-038 E1 — destination-level evidence for the browser service-worker
 * state-snapshot sink.
 *
 * The per-hop tests (state_snapshot_signed.test.js, sw_advanced.test.js)
 * each check one step. This file joins the real client bundle to the real
 * service worker through the actual postMessage bridge
 * (33-sw-registration.js captureState/forgetState) and asserts on the bytes
 * that land in CacheStorage — the persisted, on-disk destination.
 *
 * Sentinels are placed in every frame field OUTSIDE the signed token. The
 * contract under test: the only application-derived bytes the browser
 * persists are the server's signed token, verbatim; frames that are not
 * eligible to carry navigation state cannot reach storage; a server
 * revocation removes the persisted entry.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import vm from 'vm';

const SW_SRC = fs.readFileSync('./python/djust/static/djust/service-worker.js', 'utf-8');
const CLIENT_SRC = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const ORIGIN = 'http://localhost:8000';
const STATE_CACHE = 'djust-state-cache-v1';
const VIEW = 'app.views.Orders';
const PATH = '/orders';
const TOKEN = 'eyJzdGF0ZSI6IklOX1RPS0VOIn0:1sig:TOKENMAC';
const OUTSIDE = 'OUTSIDE_TOKEN_SENTINEL';

function loadSw() {
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
    class ResponseShim {
        constructor(body) { this._body = body; }
        async json() { return JSON.parse(this._body); }
        async text() { return String(this._body); }
        clone() { return new ResponseShim(this._body); }
    }
    const listeners = {};
    const sandbox = {
        self: {
            addEventListener(name, fn) { listeners[name] = fn; },
            skipWaiting: () => Promise.resolve(),
            clients: { claim: () => Promise.resolve(), matchAll: () => Promise.resolve([]) },
            registration: { scope: ORIGIN + '/' },
            location: { origin: ORIGIN },
        },
        caches,
        fetch: async () => new ResponseShim(''),
        Response: ResponseShim,
        Headers: class {},
        Request: class {},
        module: { exports: {} },
        setTimeout, clearTimeout,
        console: { log() {}, warn() {}, error() {} },
        Date, Math, JSON, Promise, Map, Set,
    };
    vm.createContext(sandbox);
    vm.runInContext(SW_SRC, sandbox);
    return { listeners, store };
}

// Every raw byte string persisted by the service worker, across all caches.
function persistedBytes(store) {
    const out = [];
    for (const [, c] of store) for (const [, r] of c) out.push(String(r._body));
    return out;
}

function createEnv() {
    const sw = loadSw();
    const pending = [];
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-view="${VIEW}"></div></body></html>`,
        { url: ORIGIN + PATH, runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    window.console = { log() {}, warn() {}, error() {}, debug() {}, info() {} };
    window.history.pushState = () => {};
    window.history.replaceState = () => {};
    if (typeof window.CSS === 'undefined') {
        window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&') };
    }
    // The controller forwards into the real SW message listener, cloning the
    // payload as postMessage's structured clone would.
    const controller = {
        postMessage(msg) {
            sw.listeners.message({
                data: JSON.parse(JSON.stringify(msg)),
                source: { type: 'window', url: ORIGIN + PATH, postMessage: vi.fn() },
                waitUntil: (p) => pending.push(p),
            });
        },
    };
    Object.defineProperty(window.navigator, 'serviceWorker', {
        configurable: true,
        value: {
            controller,
            addEventListener() {},
            removeEventListener() {},
            register: () => Promise.resolve({}),
            ready: Promise.resolve({ active: controller }),
        },
    });
    window.eval(CLIENT_SRC);
    const ws = new window.LiveViewWebSocket();
    ws.primaryViewPath = VIEW;
    return {
        window,
        store: sw.store,
        ws,
        async send(frame) {
            try { await ws.handleMessage(frame); } catch (_e) { /* UI paths irrelevant here */ }
        },
        async navigateAway() {
            window.dispatchEvent(new window.CustomEvent('djust:before-navigate', {
                detail: { fromUrl: PATH },
            }));
            await Promise.all(pending.splice(0));
        },
    };
}

describe('ADR-038 E1: service-worker state-snapshot sink', () => {
    it('persists only the signed token, verbatim, under a fixed envelope', async () => {
        const env = createEnv();
        await env.send({
            type: 'mount',
            session_id: 's-1',
            view: VIEW,
            version: 1,
            state_snapshot_signed: TOKEN,
            public_state: { secret: OUTSIDE },
            assigns: { secret: OUTSIDE },
            state: { secret: OUTSIDE },
            context: { secret: OUTSIDE },
            unrelated_field: OUTSIDE,
        });
        await env.navigateAway();

        const entry = env.store.get(STATE_CACHE)?.get(PATH);
        expect(entry, 'the capture must reach CacheStorage').toBeTruthy();
        const record = JSON.parse(entry._body);
        expect(Object.keys(record).sort()).toEqual(['state_json', 'ts', 'url', 'view_slug']);
        expect(record.state_json).toBe(TOKEN);
        expect(record.url).toBe(PATH);
        expect(record.view_slug).toBe(VIEW);
        expect(typeof record.ts).toBe('number');
        for (const bytes of persistedBytes(env.store)) {
            expect(bytes).not.toContain(OUTSIDE);
        }
    });

    it('frames not eligible to carry navigation state never reach storage', async () => {
        const env = createEnv();
        await env.send({ type: 'mount', session_id: 's-1', view: VIEW, version: 1,
            state_snapshot_signed: TOKEN });
        // A child view's event, a background render and an error frame each
        // carry a token of their own. None may displace the primary token.
        await env.send({ type: 'patch', source: 'event', view: 'app.views.Child',
            patches: [], state_snapshot_signed: 'CHILD_TOKEN_SENTINEL' });
        await env.send({ type: 'patch', source: 'background', view: VIEW,
            patches: [], state_snapshot_signed: 'BACKGROUND_TOKEN_SENTINEL' });
        await env.send({ type: 'error', source: 'event', view: VIEW,
            error: 'x', state_snapshot_signed: 'ERROR_TOKEN_SENTINEL' });
        await env.navigateAway();

        const all = persistedBytes(env.store).join('\n');
        expect(all).toContain(TOKEN);
        for (const s of ['CHILD_TOKEN_SENTINEL', 'BACKGROUND_TOKEN_SENTINEL', 'ERROR_TOKEN_SENTINEL']) {
            expect(all).not.toContain(s);
        }
    });

    it('a server revocation removes the persisted entry', async () => {
        const env = createEnv();
        await env.send({ type: 'mount', session_id: 's-1', view: VIEW, version: 1,
            state_snapshot_signed: TOKEN });
        await env.navigateAway();
        expect(persistedBytes(env.store).join('\n')).toContain(TOKEN);

        // An authorized primary-view acknowledgement revokes with null.
        await env.send({ type: 'noop', source: 'event', view: VIEW,
            state_snapshot_signed: null });
        await env.navigateAway();

        expect(env.store.get(STATE_CACHE)?.has(PATH)).toBe(false);
        for (const bytes of persistedBytes(env.store)) {
            expect(bytes).not.toContain(TOKEN);
        }
    });
});
