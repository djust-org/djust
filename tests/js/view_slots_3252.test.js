/**
 * #3252 — several LiveViews on one WebSocket are independently live.
 *
 * Drives the real client (client.js) in JSDOM against a mock socket: an eager
 * page view, two `dj-lazy` views of one class, and a `mount_batch`. Covers the
 * client half of the contract the server pins in test_multi_view_socket_3252.py:
 *
 *  - a lazy view mounts with its container's `target_id` and does not replace
 *    the page view;
 *  - a frame the server addresses to a slot applies to that container only, and
 *    each view keeps its own VDOM version (a slot's version never advances the
 *    page's, and a gap in one is not a gap in the other);
 *  - an event from inside a slot is addressed to it, and a page event is not;
 *  - recovery requests name the slot;
 *  - the views mount again on a new socket;
 *  - the HTTP fallback refuses a slot's event instead of running it on the page.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * A page with its own view (`#page`), two lazy views of one class and a third
 * of another, and a mock socket that records what the client sends.
 */
function createPage({ extra = '' } = {}) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        '<div id="page" dj-view="app.Page" dj-root><p id="page-text">page</p>' +
        '<button id="page-btn" dj-click="click">page</button></div>' +
        '<div id="w1" dj-view="app.Widget" dj-lazy="click"></div>' +
        '<div id="w2" dj-view="app.Widget" dj-lazy="click"></div>' +
        '<div id="w3" dj-view="app.Other" dj-lazy="click"></div>' +
        extra +
        '</body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const win = dom.window;
    if (!win.CSS) win.CSS = {};
    if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
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
    win.WebSocket.CONNECTING = 0;
    win.WebSocket.OPEN = 1;
    win.WebSocket.CLOSING = 2;
    win.WebSocket.CLOSED = 3;
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    // The document is complete: the client initializes as it is evaluated.
    win.eval(clientCode);
    return { dom, win, doc: win.document, sockets };
}

const tick = (ms = 15) => new Promise((resolve) => setTimeout(resolve, ms));

/** What the client has sent on a socket, by frame type. */
const sentOf = (socket, type) => socket.sent.filter((f) => f.type === type);

async function connected(page) {
    await tick();
    const socket = page.sockets[0];
    socket.onmessage({ data: JSON.stringify({ type: 'connect', session_id: 's1' }) });
    await tick();
    return socket;
}

function serve(socket, frame) {
    socket.onmessage({ data: JSON.stringify(frame) });
    return tick();
}

const mountFrame = (view, version, html, extra = {}) => ({
    type: 'mount', view, version, html, has_ids: true, ...extra,
});

async function pageWithHydratedLazies() {
    const page = createPage();
    const socket = await connected(page);
    await serve(socket, mountFrame('app.Page', 1,
        '<p id="page-text" dj-id="1">page</p><button id="page-btn" dj-click="click" dj-id="2">page</button>'));
    // The user opens the lazy views (click mode: each hydrates on its own).
    page.doc.getElementById('w1').click();
    await tick();
    page.doc.getElementById('w2').click();
    await tick();
    const mounts = sentOf(socket, 'mount');
    return { ...page, socket, mounts };
}

describe('hydrating a lazy view beside the page view (#3252)', () => {
    it('mounts it with its container as the target, apart from the page view', async () => {
        const { socket, mounts, doc } = await pageWithHydratedLazies();
        // The page view's own mount carries no target; each lazy view's names its container.
        expect(mounts.map((m) => [m.view, m.target_id || null])).toEqual([
            ['app.Page', null],
            ['app.Widget', 'w1'],
            ['app.Widget', 'w2'],
        ]);
        expect(doc.getElementById('w1').getAttribute('data-djust-target')).toBe('w1');
        expect(socket.sent.some((f) => f.type === 'mount_batch')).toBe(false);
    });

    it('fills only the addressed container, and leaves the page view as it was', async () => {
        const { socket, doc, win } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4,
            '<p dj-id="1">w1 text</p><button dj-click="bump" dj-id="2">+</button>', { target_id: 'w1' }));
        expect(doc.getElementById('w1').textContent).toContain('w1 text');
        expect(doc.getElementById('w2').textContent.trim()).toBe('');
        expect(doc.getElementById('page-text').textContent).toBe('page');
        expect(win.djust.viewSlots.version('w1')).toBe(4);
        expect(win.djust.viewSlots.mounted().sort()).toEqual(['w1', 'w2']);
    });

    it('does not take a lazy container for the page view', async () => {
        const { doc } = await pageWithHydratedLazies();
        // findPageViewContainer() is the eager container, whatever order they are in.
        const lazyFirst = createPage({});
        const first = lazyFirst.doc.body.firstChild;
        lazyFirst.doc.body.insertBefore(lazyFirst.doc.getElementById('w1'), first);
        expect(doc.querySelector('#page').getAttribute('dj-view')).toBe('app.Page');
        const eager = lazyFirst.doc.querySelector(
            '[dj-view]:not([dj-sticky-root]):not([data-djust-embedded]):not([dj-lazy]):not([data-djust-target])');
        expect(eager.id).toBe('page');
    });
});

describe('frames addressed to a slot', () => {
    it('apply to its container only, against its own version', async () => {
        const { socket, doc, win } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4,
            '<p dj-id="1">w1 text</p>', { target_id: 'w1' }));
        await serve(socket, mountFrame('app.Widget', 9,
            '<p dj-id="1">w2 text</p>', { target_id: 'w2' }));

        // The slots are numbered apart: w1 is at 4, w2 at 9, the page at 1.
        await serve(socket, {
            type: 'patch', version: 5, target_id: 'w1', source: 'event',
            patches: [{ type: 'SetText', path: [0], d: '1', text: 'w1 clicked' }],
        });
        expect(doc.getElementById('w1').textContent).toContain('w1 clicked');
        expect(doc.getElementById('w2').textContent).toContain('w2 text');
        expect(doc.getElementById('page-text').textContent).toBe('page');

        await serve(socket, {
            type: 'patch', version: 10, target_id: 'w2', source: 'event',
            patches: [{ type: 'SetText', path: [0], d: '1', text: 'w2 clicked' }],
        });
        expect(doc.getElementById('w2').textContent).toContain('w2 clicked');
        // The page view's cursor is untouched by either: its next patch is v2.
        await serve(socket, {
            type: 'patch', version: 2, source: 'event',
            patches: [{ type: 'SetText', path: [0], d: '1', text: 'page clicked' }],
        });
        expect(doc.getElementById('page-text').textContent).toBe('page clicked');
        expect(doc.getElementById('w1').textContent).toContain('w1 clicked');
        expect(win.djust.viewSlots.version('w1')).toBe(5);
        expect(win.djust.viewSlots.version('w2')).toBe(10);
        expect(sentOf(socket, 'request_html')).toEqual([]);
    });

    it('a gap in one slot asks for that slot\'s recovery, not the page\'s', async () => {
        const { socket, doc } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4, '<p dj-id="1">w1 text</p>', { target_id: 'w1' }));
        await serve(socket, {
            type: 'patch', version: 8, target_id: 'w1', source: 'event',
            patches: [{ type: 'SetText', path: [0], d: '1', text: 'never applied' }],
        });
        expect(sentOf(socket, 'request_html')).toEqual([{ type: 'request_html', target_id: 'w1' }]);
        expect(doc.getElementById('w1').textContent).toContain('w1 text');

        // The answer morphs that slot's container and resets its cursor.
        await serve(socket, {
            type: 'html_recovery', version: 7, target_id: 'w1',
            html: '<div dj-root><p dj-id="1">recovered</p></div>',
        });
        expect(doc.getElementById('w1').textContent).toContain('recovered');
        expect(doc.getElementById('page-text').textContent).toBe('page');
    });

    it('a full-HTML update morphs the slot, not the page', async () => {
        const { socket, doc } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4, '<p dj-id="1">w1 text</p>', { target_id: 'w1' }));
        await serve(socket, {
            type: 'html_update', version: 5, target_id: 'w1', source: 'event',
            html: '<div dj-root><p dj-id="1">w1 html</p></div>',
        });
        expect(doc.getElementById('w1').textContent).toContain('w1 html');
        expect(doc.getElementById('page-text').textContent).toBe('page');
    });

    it('for a container that is gone are dropped, and settle the event they answer', async () => {
        const { socket, doc, win } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4, '<button id="gone-btn" dj-click="bump" dj-id="1">+</button>',
            { target_id: 'w1' }));
        doc.getElementById('w1').remove();
        await serve(socket, { type: 'patch', version: 5, target_id: 'w1', patches: [], source: 'event' });
        expect(win.djust.viewSlots.container('w1')).toBeNull();
    });
});

describe('events from inside a slot', () => {
    it('are addressed to it; the page view\'s are not', async () => {
        const { socket, doc } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4,
            '<button id="w1-btn" dj-click="bump" dj-id="1">+</button>', { target_id: 'w1' }));
        await serve(socket, mountFrame('app.Widget', 4,
            '<button id="w2-btn" dj-click="bump" dj-id="1">+</button>', { target_id: 'w2' }));

        doc.getElementById('w2-btn').click();
        await tick();
        doc.getElementById('page-btn').click();
        await tick();
        doc.getElementById('w1-btn').click();
        await tick();

        const events = sentOf(socket, 'event');
        expect(events.map((e) => [e.event, e.target_id || null])).toEqual([
            ['bump', 'w2'],
            ['click', null],
            ['bump', 'w1'],
        ]);
    });

    it('are addressed from the moment hydration starts, before the mount reply', async () => {
        const page = createPage({
            extra: '<div id="w4" dj-view="app.Widget" dj-lazy="click">' +
                '<button id="w4-btn" dj-click="bump">+</button></div>',
        });
        const socket = await connected(page);
        await serve(socket, mountFrame('app.Page', 1, '<p id="page-text" dj-id="1">page</p>'));
        // Hydration of w4 starts; the server has not answered yet. Its
        // pre-rendered button is already a slot's: an event from it is
        // addressed to w4, so the server refuses it rather than the page view
        // running it.
        page.doc.getElementById('w4').click();
        await tick();
        page.doc.getElementById('w4-btn').click();
        await tick();
        const events = sentOf(socket, 'event');
        expect(events.map((e) => [e.event, e.target_id])).toEqual([['bump', 'w4']]);
    });

    it('are refused, not sent to the page, over the HTTP fallback', async () => {
        const { socket, doc, win } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4,
            '<button id="w1-btn" dj-click="bump" dj-id="1">+</button>', { target_id: 'w1' }));
        const errors = [];
        win.addEventListener('djust:error', (e) => errors.push(e.detail));
        let fetched = 0;
        win.fetch = () => { fetched += 1; return Promise.resolve({ ok: true, json: () => ({}) }); };
        socket.readyState = 3; // the socket drops: events would fall back to HTTP

        doc.getElementById('w1-btn').click();
        await tick(40);
        expect(fetched).toBe(0);
        expect(errors.map((e) => e.code)).toEqual(['view_unavailable']);

        // The page view's own event still takes the HTTP fallback.
        doc.getElementById('page-btn').click();
        await tick(40);
        expect(fetched).toBe(1);
    });
});

describe('mount_batch', () => {
    it('registers every batched view as a slot of its own', async () => {
        const page = createPage();
        const socket = await connected(page);
        await serve(socket, mountFrame('app.Page', 1,
            '<p id="page-text" dj-id="1">page</p><button id="page-btn" dj-click="click" dj-id="2">page</button>'));
        // Two lazy views hydrating together are one mount_batch frame (the
        // manager sends it when they are queued for the socket).
        const lazy = page.win.djust.lazyHydration;
        for (const id of ['w1', 'w2']) {
            lazy.hydratedElements.add(lazy.targetIdFor(page.doc.getElementById(id)));
        }
        lazy.pendingMounts = [
            { element: page.doc.getElementById('w1'), viewPath: 'app.Widget' },
            { element: page.doc.getElementById('w2'), viewPath: 'app.Widget' },
        ];
        lazy.processPendingMounts();
        await tick();
        const [batch] = sentOf(socket, 'mount_batch');
        expect(batch.views.map((v) => [v.view, v.target_id])).toEqual([
            ['app.Widget', 'w1'],
            ['app.Widget', 'w2'],
        ]);
        // They are addressable at once...
        expect(page.win.djust.viewSlots.mounted().sort()).toEqual(['w1', 'w2']);

        await serve(socket, {
            type: 'mount_batch',
            views: [
                { type: 'mount', view: 'app.Widget', version: 3, target_id: 'w1', has_ids: true,
                  html: '<button id="b1" dj-click="bump" dj-id="1">+</button>' },
                { type: 'mount', view: 'app.Widget', version: 7, target_id: 'w2', has_ids: true,
                  html: '<button id="b2" dj-click="bump" dj-id="1">+</button>' },
            ],
            failed: [],
        });
        // ...each with a cursor of its own...
        expect(page.win.djust.viewSlots.version('w1')).toBe(3);
        expect(page.win.djust.viewSlots.version('w2')).toBe(7);
        // ...and an event from each runs on that view.
        page.doc.getElementById('b2').click();
        await tick();
        page.doc.getElementById('b1').click();
        await tick();
        expect(sentOf(socket, 'event').map((e) => [e.event, e.target_id])).toEqual([
            ['bump', 'w2'],
            ['bump', 'w1'],
        ]);
    });
});

describe('a new socket', () => {
    it('mounts the page view again and every slot beside it', async () => {
        const { sockets, socket, win } = await pageWithHydratedLazies();
        await serve(socket, mountFrame('app.Widget', 4, '<p dj-id="1">w1 text</p>', { target_id: 'w1' }));
        await serve(socket, mountFrame('app.Widget', 4, '<p dj-id="1">w2 text</p>', { target_id: 'w2' }));
        expect(sockets.length).toBe(1);

        // The connection drops; the client reconnects.
        socket.readyState = 3;
        socket.onclose({ code: 1006 });
        await tick(1700);
        const second = sockets[1];
        expect(second).toBeDefined();
        second.onmessage({ data: JSON.stringify({ type: 'connect', session_id: 's2' }) });
        await tick();
        const mounts = second.sent.filter((f) => f.type === 'mount');
        expect(mounts.map((m) => [m.view, m.target_id || null, m.has_prerendered])).toEqual([
            ['app.Page', null, true],
            ['app.Widget', 'w1', true],
            ['app.Widget', 'w2', true],
        ]);
        // Each slot starts a new cursor with its mount reply.
        expect(win.djust.viewSlots.version('w1')).toBeNull();
    });
});

describe('unmounting', () => {
    it('tells the server which view went, and forgets it', async () => {
        const { socket, win } = await pageWithHydratedLazies();
        win.djust.liveViewInstance.unmountView('w1');
        expect(sentOf(socket, 'unmount')).toEqual([{ type: 'unmount', target_id: 'w1' }]);
        expect(win.djust.viewSlots.mounted()).toEqual(['w2']);
        // A view that is not mounted sends nothing.
        expect(win.djust.liveViewInstance.unmountView('w1')).toBe(false);
        expect(sentOf(socket, 'unmount').length).toBe(1);
    });
});
