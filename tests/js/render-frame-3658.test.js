/**
 * #3658 (TC9 slice 3): one `render` frame per turn result, on every wire.
 *
 * The client applies a frame's DOM part, then its `effects` in array order.
 * Unknown or malformed effects are skipped without stopping the rest; a
 * `__proto__` / `constructor` kind is inert (the effects table is a switch).
 * A slot's effects are page-level. A server-initiated frame held while the
 * page's own events are pending (#560) holds its effects too, so a pushed
 * effect cannot overtake the answer it follows. The HTTP fallback applies the
 * same frame the same way.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import { PROTOCOL, renderFrame } from './_helpers.js';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const tick = (ms = 15) => new Promise((resolve) => setTimeout(resolve, ms));

function createPage({ http = false } = {}) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><head><title>Old</title></head><body>' +
        '<div id="page" dj-view="app.Page" dj-root><p id="text" dj-id="1">one</p>' +
        '<button id="btn" dj-click="save" dj-id="2">save</button></div>' +
        '<div id="w1" dj-view="app.Widget" dj-lazy="click"></div>' +
        '</body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const win = dom.window;
    if (!win.CSS) win.CSS = {};
    if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    if (http) {
        Object.defineProperty(win, 'DJUST_USE_WEBSOCKET', { get: () => false, set() {} });
        Object.defineProperty(win, 'EventSource', { get: () => undefined, set() {} });
    }
    const sockets = [];
    win.WebSocket = class MockWebSocket {
        constructor(url) {
            this.url = url;
            this.readyState = 1;
            this.sent = [];
            sockets.push(this);
            Promise.resolve().then(() => this.onopen && this.onopen({}));
        }
        send(data) { this.sent.push(JSON.parse(data)); }
        close() { this.readyState = 3; }
    };
    Object.assign(win.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    const warnings = [];
    win.console = { log() {}, error() {}, debug() {}, info() {}, warn: (...a) => warnings.push(a.join(' ')) };
    win.eval(clientCode);

    // Record every effect handler call, in order, with the DOM text it saw.
    const calls = [];
    const text = () => win.document.getElementById('text')?.textContent;
    const record = (kind) => vi.fn((effect) => calls.push([kind, effect, text()]));
    win.djust.flash = { handleFlash: record('flash') };
    win.djust.pageMetadata = { handlePageMetadata: record('page_metadata') };
    win.djust.djLayout = { applyLayout: record('layout') };
    win.djust.navigation = { ...win.djust.navigation, handleNavigation: record('navigation') };
    // `navigate` validates its target first; a rejected one does not navigate.
    win.djust.safeNavigationTarget = vi.fn((to) => { calls.push(['navigate', { to }, text()]); return null; });
    win.addEventListener('djust:push_event', (e) => calls.push(['push_event', e.detail, text()]));
    return { dom, win, doc: win.document, sockets, calls, warnings };
}

async function mountedPage() {
    const page = createPage();
    await tick();
    const socket = page.sockets[0];
    const serve = (frame) => { socket.onmessage({ data: JSON.stringify(frame) }); return tick(); };
    await serve({ type: 'connect', protocol: PROTOCOL, session_id: 's1' });
    await serve({ type: 'mount', view: 'app.Page', version: 1, has_ids: true, protocol: PROTOCOL,
        html: '<p id="text" dj-id="1">one</p><button id="btn" dj-click="save" dj-id="2">save</button>' });
    return { ...page, socket, serve };
}

const SIX = [
    { kind: 'push_event', event: 'chart', payload: { n: 1 } },
    { kind: 'flash', action: 'put', level: 'info', message: 'Saved' },
    { kind: 'page_metadata', action: 'title', value: 'Inbox (3)' },
    { kind: 'layout', path: 'layouts/full.html', html: '<body></body>' },
    { kind: 'navigation', action: 'live_patch', params: { page: 2 }, replace: false },
    { kind: 'navigate', to: '/login/?next=/x/' },
];
const setText = (value) => [{ type: 'SetText', path: [0, 0], d: '1', text: value }];

describe('a render frame', () => {
    it('applies its DOM part, then every effect in array order', async () => {
        const { serve, calls, doc } = await mountedPage();
        await serve(renderFrame({ source: 'tick', version: 2, patches: setText('two'), effects: SIX }));
        expect(doc.getElementById('text').textContent).toBe('two');
        expect(calls.map(([kind]) => kind)).toEqual(
            ['push_event', 'flash', 'page_metadata', 'layout', 'navigation', 'navigate']);
        // Every effect saw the patched DOM.
        expect(calls.every(([, , seen]) => seen === 'two')).toBe(true);
        // Each handler takes the effect object itself.
        expect(calls[1][1]).toMatchObject({ action: 'put', level: 'info', message: 'Saved' });
        expect(calls[0][1]).toEqual({ event: 'chart', payload: { n: 1 } });
    });

    it('with no DOM part applies its effects alone', async () => {
        const { serve, calls, doc } = await mountedPage();
        await serve(renderFrame({ source: 'deferred', effects: [SIX[1]] }));
        expect(calls.map(([kind]) => kind)).toEqual(['flash']);
        expect(doc.getElementById('text').textContent).toBe('one');
    });

    it('skips an unknown or malformed effect and applies the rest', async () => {
        const { serve, calls, win } = await mountedPage();
        win.djustDebug = true;
        await serve(renderFrame({ source: 'tick', effects: [
            { kind: 'hologram', beam: true }, null, 7, 'flash', SIX[1],
        ] }));
        expect(calls.map(([kind]) => kind)).toEqual(['flash']);
    });

    it('ignores effects that are not a list, and still applies the DOM part', async () => {
        const { serve, calls, doc } = await mountedPage();
        await serve({ type: 'render', source: 'tick', version: 2, patches: setText('two'),
            effects: { kind: 'flash', message: 'x' } });
        await serve({ type: 'render', source: 'tick', version: 3, patches: setText('three') });
        expect(doc.getElementById('text').textContent).toBe('three');
        expect(calls).toHaveLength(0);
    });

    it('treats __proto__, constructor and prototype kinds as inert', async () => {
        const { serve, calls, win } = await mountedPage();
        await serve({ type: 'render', source: 'tick',
            effects: JSON.parse('[{"kind":"__proto__","polluted":1},{"kind":"constructor"},{"kind":"prototype"},' +
                '{"kind":"flash","action":"put","level":"info","message":"ok"}]') });
        expect(calls.map(([kind]) => kind)).toEqual(['flash']);
        expect({}.polluted).toBeUndefined();
        expect(win.Object.prototype.polluted).toBeUndefined();
    });

    it('answers its event by ref and ends the loading state, with no DOM part', async () => {
        const { socket, serve, calls, doc } = await mountedPage();
        doc.getElementById('btn').click();
        await tick();
        const event = socket.sent.filter((f) => f.type === 'event').pop();
        expect(event).toBeTruthy();
        await serve(renderFrame({ source: 'event', ref: event.ref, effects: [SIX[0]] }));
        expect(calls.map(([kind]) => kind)).toEqual(['push_event']);
        expect(doc.getElementById('btn').classList.contains('djust-loading')).toBe(false);
    });
});

describe('a held server-initiated frame (#560)', () => {
    it('holds its effects with its patch until the pending event settles', async () => {
        const { socket, serve, calls, doc } = await mountedPage();
        doc.getElementById('btn').click();
        await tick();
        const event = socket.sent.filter((f) => f.type === 'event').pop();

        // A push turn arrives while the event is out: nothing of it applies.
        await serve(renderFrame({ source: 'push', version: 2, patches: setText('pushed'),
            effects: [{ kind: 'flash', action: 'put', level: 'info', message: 'from push' }] }));
        // So does an effects-only NOTIFY turn.
        await serve(renderFrame({ source: 'notify',
            effects: [{ kind: 'push_event', event: 'notified', payload: {} }] }));
        expect(doc.getElementById('text').textContent).toBe('one');
        expect(calls).toHaveLength(0);

        // The event's answer: its own effects first, then the held frames in order.
        await serve(renderFrame({ source: 'event', ref: event.ref, version: 3, patches: setText('answered'),
            effects: [{ kind: 'page_metadata', action: 'title', value: 'Answered' }] }));
        expect(calls.map(([kind, effect]) => [kind, effect.message || effect.value || effect.event])).toEqual([
            ['page_metadata', 'Answered'],
            ['flash', 'from push'],
            ['push_event', 'notified'],
        ]);
        // The held patch applied after the answer's, under its own version.
        expect(doc.getElementById('text').textContent).toBe('pushed');
    });

    it('is not held when no event is pending', async () => {
        const { serve, calls } = await mountedPage();
        await serve(renderFrame({ source: 'push', effects: [SIX[1]] }));
        expect(calls.map(([kind]) => kind)).toEqual(['flash']);
    });
});

describe('a frame addressed to a view beside the page view (#3252)', () => {
    async function withSlot() {
        const page = await mountedPage();
        page.doc.getElementById('w1').click();
        await tick();
        await page.serve({ type: 'mount', view: 'app.Widget', version: 1, has_ids: true, target_id: 'w1',
            protocol: PROTOCOL, html: '<span id="wtext" dj-id="1">w</span>' });
        return page;
    }

    it('applies its DOM part in the slot and its effects page-level, after it', async () => {
        const { serve, calls, doc } = await withSlot();
        let seen = null;
        doc.defaultView.addEventListener('djust:push_event', () => {
            seen = doc.getElementById('wtext')?.textContent;
        });
        await serve(renderFrame({ source: 'tick', target_id: 'w1', version: 2,
            patches: [{ type: 'SetText', path: [0, 0], d: '1', text: 'w2' }],
            effects: [SIX[0], SIX[1]] }));
        expect(doc.getElementById('wtext').textContent).toBe('w2');
        expect(doc.getElementById('text').textContent).toBe('one');
        expect(seen).toBe('w2');
        expect(calls.map(([kind]) => kind)).toEqual(['push_event', 'flash']);
    });

    it('still applies its effects when the slot container is gone', async () => {
        const { serve, calls, doc } = await withSlot();
        doc.getElementById('w1').remove();
        await serve(renderFrame({ source: 'tick', target_id: 'w1', version: 2,
            patches: [{ type: 'SetText', path: [0, 0], d: '1', text: 'w2' }], effects: [SIX[1]] }));
        expect(calls.map(([kind]) => kind)).toEqual(['flash']);
    });
});

describe('HTTP and WebSocket', () => {
    it('apply the same frame identically', async () => {
        const frame = renderFrame({ source: 'event', html: '<p id="text" dj-id="1">whole</p>',
            version: 2, effects: SIX.slice(0, 5) });

        const ws = await mountedPage();
        ws.doc.getElementById('btn').click();
        await tick();
        const event = ws.socket.sent.filter((f) => f.type === 'event').pop();
        await ws.serve({ ...frame, ref: event.ref });

        const http = createPage({ http: true });
        await tick();
        http.win.fetch = vi.fn(async () => ({
            ok: true, status: 200,
            headers: { get: (name) => (name === 'X-Djust-Protocol' ? String(PROTOCOL) : null) },
            json: async () => JSON.parse(JSON.stringify(frame)),
        }));
        await http.win.djust.handleEvent('save', {});
        await tick();

        const shape = (calls) => calls.map(([kind, effect, seen]) => [kind, JSON.stringify(effect), seen]);
        expect(shape(http.calls)).toEqual(shape(ws.calls));
        expect(http.doc.getElementById('text').textContent).toBe('whole');
        expect(ws.doc.getElementById('text').textContent).toBe('whole');
    });
});
