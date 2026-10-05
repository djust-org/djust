/**
 * #3252 over SSE — several LiveViews on one SSE session are independently live.
 *
 * Drives the real client (client.js) in JSDOM against a mock EventSource and a
 * recording `fetch`: an eager page view and `dj-lazy` views that hydrate beside
 * it. The client half of the contract the server pins in
 * python/djust/tests/test_sse_view_slots_3252.py (the WebSocket twin is
 * view_slots_3252.test.js):
 *
 *  - a lazy view mounts with a frame of its own that names its container, once
 *    the page view is mounted, and does not replace the page view;
 *  - an event from inside it is addressed to it, the page view's is not;
 *  - a frame the server addresses to it applies to that container only, against
 *    its own VDOM version;
 *  - on a reconnect the page view mounts again and the views beside it with it;
 *  - without a stream (no EventSource) hydration says so instead of leaving a
 *    container that looks live and is not;
 *  - a view's frames never replace or revoke the page view's navigation snapshot.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const tick = (ms = 15) => new Promise((resolve) => setTimeout(resolve, ms));

function createPage({ eventSource = true, extra = '' } = {}) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        '<div id="page" dj-view="app.Page" dj-root><p id="page-text">page</p>' +
        '<button id="page-btn" dj-click="click">page</button></div>' +
        '<div id="w1" dj-view="app.Widget" dj-lazy="click"></div>' +
        '<div id="w2" dj-view="app.Widget" dj-lazy="click"></div>' +
        extra +
        '</body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const win = dom.window;
    if (!win.CSS) win.CSS = {};
    if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    win.DJUST_USE_WEBSOCKET = false;
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    const es = { onopen: null, onmessage: null, onerror: null, readyState: 1, close: vi.fn() };
    if (eventSource) {
        win.EventSource = vi.fn(function () { return es; });
        win.EventSource.CLOSED = 2;
        win.EventSource.OPEN = 1;
    }
    const posts = [];
    win.fetch = vi.fn((url, init) => {
        posts.push(JSON.parse(init.body));
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    });
    win.eval(clientCode);
    return { dom, win, doc: win.document, es, posts };
}

const serve = async (page, frame) => {
    page.es.onmessage({ data: JSON.stringify(frame) });
    await tick();
};

const mountFrame = (view, version, html, extra = {}) => ({
    type: 'mount', view, version, html, has_ids: true, ...extra,
});

const PAGE_HTML =
    '<p id="page-text" dj-id="1">page</p><button id="page-btn" dj-click="click" dj-id="2">page</button>';

/** The page, mounted over SSE. */
async function mountedPage(options) {
    const page = createPage(options);
    await tick();
    page.es.onopen();
    await serve(page, mountFrame('app.Page', 1, PAGE_HTML));
    return page;
}

// The page view's own mount frame is posted on open (idempotent: the stream GET
// mounted it); the views beside it name their container.
const mountsOf = (page) => page.posts.filter((p) => p.type === 'mount' && p.target_id);
const eventsOf = (page) => page.posts.filter((p) => p.type === 'event');

async function hydrated(page, ...ids) {
    for (const id of ids) {
        page.doc.getElementById(id).click();
        await tick();
    }
}

describe('hydrating a lazy view over SSE', () => {
    it('mounts it beside the page view with its container as the address', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1', 'w2');
        // Each lazy view is a frame of its own (no mount_batch over SSE).
        expect(mountsOf(page).map((m) => [m.view, m.target_id])).toEqual([
            ['app.Widget', 'w1'],
            ['app.Widget', 'w2'],
        ]);
        expect(page.posts.some((p) => p.type === 'mount_batch')).toBe(false);
        expect(page.doc.getElementById('w1').getAttribute('data-djust-target')).toBe('w1');
    });

    it('waits for the page view to mount, then mounts what was waiting', async () => {
        const page = createPage();
        await tick();
        page.es.onopen();
        await hydrated(page, 'w1');
        expect(mountsOf(page)).toEqual([]);
        await serve(page, mountFrame('app.Page', 1, PAGE_HTML));
        expect(mountsOf(page).map((m) => m.target_id)).toEqual(['w1']);
    });

    it('fills only the addressed container and leaves the page view as it was', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1', 'w2');
        await serve(page, mountFrame('app.Widget', 4,
            '<p dj-id="1">w1 text</p><button id="b1" dj-click="bump" dj-id="2">+</button>',
            { target_id: 'w1' }));
        expect(page.doc.getElementById('w1').textContent).toContain('w1 text');
        expect(page.doc.getElementById('w2').textContent.trim()).toBe('');
        expect(page.doc.getElementById('page-text').textContent).toBe('page');
        expect(page.win.djust.viewSlots.version('w1')).toBe(4);
        expect(page.win.djust.viewSlots.mounted().sort()).toEqual(['w1', 'w2']);
    });
});

describe('events', () => {
    it('from inside a view are addressed to it; the page view\'s are not', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1', 'w2');
        await serve(page, mountFrame('app.Widget', 4,
            '<button id="b1" dj-click="bump" dj-id="1">+</button>', { target_id: 'w1' }));
        await serve(page, mountFrame('app.Widget', 4,
            '<button id="b2" dj-click="bump" dj-id="1">+</button>', { target_id: 'w2' }));
        for (const id of ['b2', 'page-btn', 'b1']) {
            page.doc.getElementById(id).click();
            await tick();
        }
        expect(eventsOf(page).map((e) => [e.event, e.target_id || null])).toEqual([
            ['bump', 'w2'],
            ['click', null],
            ['bump', 'w1'],
        ]);
    });

    it('are addressed from the moment hydration starts, before the mount reply', async () => {
        const page = await mountedPage({
            extra: '<div id="w4" dj-view="app.Widget" dj-lazy="click">' +
                '<button id="w4-btn" dj-click="bump">+</button></div>',
        });
        await hydrated(page, 'w4');
        page.doc.getElementById('w4-btn').click();
        await tick();
        expect(eventsOf(page).map((e) => [e.event, e.target_id])).toEqual([['bump', 'w4']]);
    });
});

describe('a dj-hook', () => {
    it('pushes its event to the view it sits in, over SSE', async () => {
        const page = await mountedPage();
        page.win.DjustHooks = {
            Ping: { mounted() { this.el.addEventListener('click', () => this.pushEvent('hooked', { n: 1 })); } },
        };
        await hydrated(page, 'w1');
        await serve(page, mountFrame('app.Widget', 4,
            '<button id="hook" dj-hook="Ping" dj-id="1">hook</button>', { target_id: 'w1' }));
        page.doc.getElementById('hook').click();
        await tick();
        expect(eventsOf(page)).toEqual([
            { type: 'event', event: 'hooked', params: { n: 1 }, target_id: 'w1' },
        ]);
    });
});

describe('frames addressed to a view', () => {
    it('apply to its container only, against its own version', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1', 'w2');
        await serve(page, mountFrame('app.Widget', 4, '<p dj-id="1">w1 text</p>', { target_id: 'w1' }));
        await serve(page, mountFrame('app.Widget', 9, '<p dj-id="1">w2 text</p>', { target_id: 'w2' }));
        await serve(page, {
            type: 'patch', version: 5, target_id: 'w1', source: 'event',
            patches: [{ type: 'SetText', path: [0], d: '1', text: 'w1 clicked' }],
        });
        expect(page.doc.getElementById('w1').textContent).toContain('w1 clicked');
        expect(page.doc.getElementById('w2').textContent).toContain('w2 text');
        // The page view's cursor is untouched: its next patch is v2.
        await serve(page, {
            type: 'patch', version: 2, source: 'event',
            patches: [{ type: 'SetText', path: [0], d: '1', text: 'page clicked' }],
        });
        expect(page.doc.getElementById('page-text').textContent).toBe('page clicked');
        expect(page.doc.getElementById('w1').textContent).toContain('w1 clicked');
        expect(page.win.djust.viewSlots.version('w1')).toBe(5);
        expect(page.win.djust.viewSlots.version('w2')).toBe(9);
    });

    it('a refusal drops the client\'s registration of the view', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1');
        expect(page.win.djust.viewSlots.mounted()).toEqual(['w1']);
        await serve(page, {
            type: 'error', error: 'No view is mounted at this address',
            code: 'view_unavailable', target_id: 'w1',
        });
        expect(page.win.djust.viewSlots.mounted()).toEqual([]);
    });
});

describe('unmount and navigation', () => {
    it('unmountView tells the server which view went', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1', 'w2');
        expect(page.win.djust.liveViewInstance.unmountView('w1')).toBe(true);
        expect(page.posts.filter((p) => p.type === 'unmount')).toEqual([
            { type: 'unmount', target_id: 'w1' },
        ]);
        expect(page.win.djust.viewSlots.mounted()).toEqual(['w2']);
    });

    it('a live navigation forgets the views beside the page view', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1');
        page.win.djust.liveViewInstance.liveRedirectMount({
            type: 'live_redirect_mount', view: 'app.Other', url: '/other/', params: {},
        });
        expect(page.win.djust.viewSlots.mounted()).toEqual([]);
    });
});

describe('a reconnect', () => {
    it('mounts the page view again and every view beside it', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1', 'w2');
        await serve(page, mountFrame('app.Widget', 4, '<p dj-id="1">w1</p>', { target_id: 'w1' }));
        page.posts.length = 0;
        // The stream dropped and EventSource reconnected: the server mounts the
        // page view for the new session, then the client mounts the rest.
        page.es.onopen();
        await serve(page, mountFrame('app.Page', 1, PAGE_HTML));
        expect(mountsOf(page).map((m) => [m.target_id, m.has_prerendered])).toEqual([
            ['w1', true],
            ['w2', true],
        ]);
        expect(page.win.djust.viewSlots.version('w1')).toBe(null);
    });
});

describe('without a stream', () => {
    it('hydration reports the view as unavailable instead of leaving it looking live', async () => {
        const page = createPage({ eventSource: false });
        await tick();
        const errors = [];
        page.win.addEventListener('djust:error', (e) => errors.push(e.detail));
        await hydrated(page, 'w1');
        expect(errors.map((e) => e.code)).toEqual(['view_unavailable']);
        expect(errors[0].error).toContain('app.Widget');
        expect(page.posts).toEqual([]);
    });
});

describe('the page view\'s navigation snapshot', () => {
    it('is neither replaced nor revoked by a view of the same class beside it', async () => {
        const page = await mountedPage();
        await hydrated(page, 'w1');
        await serve(page, { type: 'noop', source: 'event', view: 'app.Page',
            state_snapshot_signed: 'page-token' });
        expect(page.win.djust._clientState['app.Page']).toBe('page-token');
        await serve(page, mountFrame('app.Page', 3, '<p dj-id="1">w1</p>',
            { target_id: 'w1', state_snapshot_signed: 'slot-token' }));
        await serve(page, { type: 'patch', patches: [], version: 4, source: 'event', view: 'app.Page',
            target_id: 'w1', state_snapshot_signed: null });
        expect(page.win.djust._clientState['app.Page']).toBe('page-token');
    });
});
