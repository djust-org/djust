/**
 * #3036: a live navigation to a page whose page shell differs is a full load.
 *
 * The server renders the current page's fingerprint into
 * `<meta name="djust-page-shell">` and sends the destination's on the `mount`
 * reply to a `live_redirect_mount`. When they differ the client loads the
 * destination as a normal page instead of swapping the `dj-root`, because the
 * stylesheets and scripts around the root would be the previous page's.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    process.env.DJUST_CLIENT_JS || './python/djust/static/djust/client.js',
    'utf-8',
);

const CURRENT = 'aaaaaaaaaaaaaaaa';
const OTHER = 'bbbbbbbbbbbbbbbb';

async function load(startPath, { shell = CURRENT } = {}) {
    const meta = shell === null ? '' : `<meta name="djust-page-shell" content="${shell}">`;
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${meta}</head><body>
           <div dj-view="test.views.IndexView" dj-root><p id="old">old page</p></div>
         </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost' + startPath },
    );
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; this.sent = []; }
            send(d) { this.sent.push(d); }
            close() {}
        };
        window.requestAnimationFrame = function(cb) { return setTimeout(cb, 0); };
        window.scrollTo = function() {};
    `);
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const loads = [];
    dom.window.djust._fullPageLoad = (url) => loads.push(url);
    dom.window.djust._routeMap = {
        '/items/': 'test.views.IndexView',
        '/items/detail/': 'test.views.DetailView',
    };
    const ws = dom.window.djust.liveViewInstance;
    ws.viewMounted = true;
    ws.ws.sent.length = 0;
    return { dom, ws, loads, loadEntryState: dom.window.history.state };
}

const wait = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

function mountFrame(extra = {}) {
    return {
        type: 'mount',
        view: 'test.views.DetailView',
        version: 1,
        html: '<p id="new">new page</p>',
        ...extra,
    };
}

function rootHtml(dom) {
    return dom.window.document.querySelector('[dj-root]').innerHTML;
}

async function navigate(dom, path) {
    dom.window.djust.navigation.handleNavigation({
        type: 'navigation', action: 'live_redirect', path,
    });
    await wait();
}

describe('page shell fallback (#3036)', () => {
    it('a destination with a different shell is a full load, the root is not swapped', async () => {
        const { dom, ws, loads } = await load('/items/');
        await navigate(dom, '/items/detail/');
        await ws.handleMessage(mountFrame({ page_shell: OTHER }));
        // The address bar already holds the destination; that is what loads.
        expect(loads).toEqual(['/items/detail/']);
        expect(rootHtml(dom)).toContain('id="old"');
        expect(rootHtml(dom)).not.toContain('id="new"');
    });

    it('a destination with the same shell keeps the fast path', async () => {
        const { dom, ws, loads } = await load('/items/');
        await navigate(dom, '/items/detail/');
        await ws.handleMessage(mountFrame({ page_shell: CURRENT }));
        expect(loads).toEqual([]);
        expect(rootHtml(dom)).toContain('id="new"');
    });

    it('keeps the query string and hash of the destination in the full load', async () => {
        const { dom, ws, loads } = await load('/items/');
        await navigate(dom, '/items/detail/?tab=2#part');
        await ws.handleMessage(mountFrame({ page_shell: OTHER }));
        expect(loads).toEqual(['/items/detail/?tab=2#part']);
    });

    it('back/forward onto a page with a different shell is a full load', async () => {
        const { dom, ws, loads, loadEntryState } = await load('/items/');
        await navigate(dom, '/items/detail/');
        await ws.handleMessage(mountFrame({ page_shell: CURRENT }));
        expect(loads).toEqual([]);
        ws.ws.sent.length = 0;
        // Back: the browser changes the URL, then fires popstate.
        dom.window.history.replaceState(loadEntryState, '', '/items/');
        dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate', { state: loadEntryState }));
        await wait();
        const sent = ws.ws.sent.map((raw) => JSON.parse(raw).type);
        expect(sent).toContain('live_redirect_mount');
        await ws.handleMessage(mountFrame({ view: 'test.views.IndexView', page_shell: OTHER }));
        expect(loads).toEqual(['/items/']);
    });

    it('a mount without a fingerprint (initial / reconnect mount) is applied', async () => {
        const { dom, ws, loads } = await load('/items/');
        await ws.handleMessage(mountFrame());
        expect(loads).toEqual([]);
        expect(rootHtml(dom)).toContain('id="new"');
    });

    it('a page without a fingerprint keeps the fast path', async () => {
        const { dom, ws, loads } = await load('/items/', { shell: null });
        await navigate(dom, '/items/detail/');
        await ws.handleMessage(mountFrame({ page_shell: OTHER }));
        expect(loads).toEqual([]);
        expect(rootHtml(dom)).toContain('id="new"');
    });

    it('only a mount frame can trigger it', async () => {
        const { dom, ws, loads } = await load('/items/');
        await ws.handleMessage({ type: 'patch', patches: [], version: 2, page_shell: OTHER });
        await ws.handleMessage({ type: 'page_metadata', action: 'title', value: 'x', page_shell: OTHER });
        expect(loads).toEqual([]);
        expect(dom.window.document.title).toBe('x');
    });

    it('never loads a target taken from the frame', async () => {
        const { dom, ws, loads } = await load('/items/');
        await navigate(dom, '/items/detail/');
        await ws.handleMessage(mountFrame({
            page_shell: OTHER,
            url: 'https://evil.example/', path: '//evil.example/', to: 'javascript:alert(1)',
        }));
        expect(loads).toEqual(['/items/detail/']);
    });

    it('a full load does not loop: the replacement page mounts without a fingerprint', async () => {
        // After the load, the new document carries the destination's meta and
        // its WS mount frame has no page_shell, so nothing compares.
        const { dom, ws, loads } = await load('/items/detail/', { shell: OTHER });
        await ws.handleMessage(mountFrame());
        expect(loads).toEqual([]);
        expect(rootHtml(dom)).toContain('id="new"');
    });

    it('a non-string or empty fingerprint is ignored', async () => {
        const { dom, ws, loads } = await load('/items/');
        for (const bad of [null, '', 7, {}, []]) {
            await ws.handleMessage(mountFrame({ page_shell: bad }));
        }
        expect(loads).toEqual([]);
        expect(rootHtml(dom)).toContain('id="new"');
    });
});

describe('page shell fallback: SSE transport (#3036)', () => {
    it('shares the same decision', async () => {
        const { dom, loads } = await load('/items/');
        // The SSE client calls this helper first thing in _handleMessageImpl.
        expect(dom.window.djust.fallBackToFullLoadOnShellChange(mountFrame({ page_shell: OTHER }))).toBe(true);
        expect(dom.window.djust.fallBackToFullLoadOnShellChange(mountFrame({ page_shell: CURRENT }))).toBe(false);
        expect(loads).toEqual(['/items/']);
    });
});
