/**
 * Every value of a repeated query key reaches handle_params() (#4125).
 *
 * The page GET and the fallback POST hand handle_params() a key given once as
 * its value and a repeated key as the list of its values. The client built the
 * socket mount's params with `Object.fromEntries(new URLSearchParams(...))`,
 * which keeps only the last value, so `?tag=a&tag=b` reached a socket-mounted
 * view as `'b'`. These pin the frames the built client sends: the socket
 * mount, a url_change, and the SSE stream URL.
 */
import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const QUERY = '?tag=a&tag=b&page=2&empty=';
const EXPECTED = { tag: ['a', 'b'], page: '2', empty: '' };

async function socketPage(path) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="app.views.ListView" dj-root><p>content</p></div>
         </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost' + path },
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
    return { dom, ws: dom.window.djust.liveViewInstance };
}

function frames(ws, type) {
    return ws.ws.sent.map((raw) => JSON.parse(raw)).filter((frame) => frame.type === type);
}

describe('repeated query keys', () => {
    it('the socket mount frame carries every value of a repeated key', async () => {
        const { dom, ws } = await socketPage('/items/' + QUERY);
        try {
            // What the server's connect frame triggers.
            if (frames(ws, 'mount').length === 0) ws.autoMount();
            const [mount] = frames(ws, 'mount');
            expect(mount).toBeTruthy();
            expect(mount.params).toEqual(EXPECTED);
        } finally { dom.window.close(); }
    });

    it('a url_change frame carries every value of a repeated key', async () => {
        const { dom, ws } = await socketPage('/items/');
        try {
            ws.viewMounted = true;
            dom.window.djust._routeMap = { '/items/': 'app.views.ListView' };
            ws.ws.sent.length = 0;
            const state = { djust: true };
            dom.window.history.replaceState(state, '', '/items/' + QUERY);
            dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate', { state }));
            await new Promise((resolve) => setTimeout(resolve, 30));
            const [change] = frames(ws, 'url_change');
            expect(change).toBeTruthy();
            expect(change.params).toEqual(EXPECTED);
        } finally { dom.window.close(); }
    });

    it('the SSE stream URL carries every value, not a joined string', () => {
        const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
            url: 'http://localhost:8000/items/' + QUERY,
            runScripts: 'dangerously',
        });
        const { window } = dom;
        window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
        window.EventSource = vi.fn(function () {
            return { onopen: null, onmessage: null, onerror: null, readyState: 1, close: vi.fn(), CLOSED: 2 };
        });
        window.EventSource.CLOSED = 2;
        window.EventSource.OPEN = 1;
        window.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
        try { window.eval(clientCode); } catch (_e) { /* missing DOM APIs */ }
        try {
            const sse = new window.djust.LiveViewSSE();
            sse.connect('app.views.ListView', { tag: ['a', 'b'], page: '2', empty: '' });
            const url = new URL(window.EventSource.mock.calls[0][0], window.location.origin);
            expect(url.searchParams.getAll('tag')).toEqual(['a', 'b']);
            expect(url.searchParams.get('page')).toBe('2');
            expect(url.searchParams.get('empty')).toBe('');
        } finally { window.close(); }
    });
});
