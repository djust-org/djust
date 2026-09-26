/**
 * Tests for dj-track-static — stale asset detection on WS reconnect (v0.6.0).
 *
 * The client seeds a snapshot on DOMContentLoaded and compares against
 * that snapshot on every djust:ws-reconnected event dispatched on
 * document. jsdom doesn't fire actual WS connects in these tests, so
 * we drive djust:ws-reconnected directly.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM, VirtualConsole } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>${bodyHtml}</div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {} close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

describe('dj-track-static', () => {
    let dom;

    it('does not fire dj:stale-assets when asset URLs are unchanged', () => {
        dom = createDom(`<script dj-track-static src="/static/app.abc.js"></script>`);
        const events = [];
        dom.window.document.addEventListener('dj:stale-assets', (e) => events.push(e.detail));

        dom.window.document.dispatchEvent(new dom.window.CustomEvent('djust:ws-reconnected'));
        expect(events.length).toBe(0);
    });

    it('fires dj:stale-assets when a tracked asset URL changes across reconnect', () => {
        dom = createDom(`<script id="a" dj-track-static src="/static/app.abc.js"></script>`);
        const events = [];
        dom.window.document.addEventListener('dj:stale-assets', (e) => events.push(e.detail));

        // Server deployed new code — asset URL mutates.
        const el = dom.window.document.getElementById('a');
        el.setAttribute('src', '/static/app.def.js');

        dom.window.document.dispatchEvent(new dom.window.CustomEvent('djust:ws-reconnected'));
        expect(events.length).toBe(1);
        expect(events[0].changed).toContain('/static/app.def.js');
    });

    it('checkStale reports shouldReload=true when dj-track-static="reload" asset changes', () => {
        // jsdom's window.location.reload is read-only and hard to stub on
        // the instance, so we assert the pure result via
        // window.djust.djTrackStatic._checkStale rather than validating
        // the reload call itself.
        dom = createDom(`<script id="a" dj-track-static="reload" src="/static/app.abc.js"></script>`);
        const el = dom.window.document.getElementById('a');
        el.setAttribute('src', '/static/app.def.js');

        const result = dom.window.djust.djTrackStatic._checkStale();
        expect(result.changed).toContain('/static/app.def.js');
        expect(result.shouldReload).toBe(true);
    });

    it('tracks both <script src> and <link href>', () => {
        dom = createDom(`
            <script id="s" dj-track-static src="/static/js/app.111.js"></script>
            <link id="l" dj-track-static rel="stylesheet" href="/static/css/app.222.css">
        `);
        const events = [];
        dom.window.document.addEventListener('dj:stale-assets', (e) => events.push(e.detail));

        dom.window.document.getElementById('l').setAttribute('href', '/static/css/app.333.css');
        dom.window.document.dispatchEvent(new dom.window.CustomEvent('djust:ws-reconnected'));

        expect(events.length).toBe(1);
        expect(events[0].changed).toContain('/static/css/app.333.css');
    });

    it('exposes _snapshotAssets on window.djust.djTrackStatic', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.djTrackStatic._snapshotAssets).toBe('function');
    });
});

// ---------------------------------------------------------------------------
// #2966 — a deploy is detected by the SERVER on reconnect. The page's tracked
// URLs ride the reconnect mount frame (`track_static`); the mount reply's
// `stale_static` fires dj:stale-assets or reloads. Driven through the real
// LiveViewWebSocket mount() and handleMessage().
// ---------------------------------------------------------------------------

// jsdom's location.reload cannot be stubbed (it is unforgeable); a call is
// observable as its "Not implemented" jsdomError on the virtual console.
async function loadWsPage(headHtml) {
    const reloads = [];
    const virtualConsole = new VirtualConsole();
    virtualConsole.on('jsdomError', (err) => {
        if (/navigation|reload/i.test(String(err && err.message))) reloads.push(err.message);
    });
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head>
${headHtml}
</head>
<body>
  <div dj-view="test.views.Page" dj-root><p>content</p></div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/page/', virtualConsole });
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
    for (const k of ['log', 'warn', 'error', 'debug', 'info']) dom.window.console[k] = () => {};
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const ws = dom.window.djust.liveViewInstance;
    ws.ws.sent.length = 0;
    return { dom, ws, reloads };
}

function mountFrames(ws) {
    return ws.ws.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'mount');
}

const HEAD = `
  <script dj-track-static src="/static/js/app.0123456789ab.js"></script>
  <link dj-track-static rel="stylesheet" href="http://localhost/static/css/site.aaaaaaaaaaaa.css">
  <script dj-track-static src="https://cdn.example.com/lib.js"></script>`;

describe('#2966 server-side stale-asset check', () => {
    it('a reconnect mount carries the URLs the page loaded, same-origin as paths', async () => {
        const { dom, ws } = await loadWsPage(HEAD);
        dom.window.djust._isReconnect = true;
        ws.mount('test.views.Page', {}, { primary: true });
        const [frame] = mountFrames(ws);
        expect(frame.track_static).toEqual([
            '/static/js/app.0123456789ab.js',
            '/static/css/site.aaaaaaaaaaaa.css',
            'https://cdn.example.com/lib.js',
        ]);
    });

    it('a first mount does not ask', async () => {
        const { dom, ws } = await loadWsPage(HEAD);
        dom.window.djust._isReconnect = false;
        ws.mount('test.views.Page', {}, { primary: true });
        expect(mountFrames(ws)[0].track_static).toBeUndefined();
    });

    it('a sibling (non-primary) mount does not ask', async () => {
        const { dom, ws } = await loadWsPage(HEAD);
        dom.window.djust._isReconnect = true;
        ws.mount('test.views.Other', {}, {});
        expect(mountFrames(ws)[0].track_static).toBeUndefined();
    });

    it('a page that tracks nothing sends nothing', async () => {
        const { dom, ws } = await loadWsPage('');
        dom.window.djust._isReconnect = true;
        ws.mount('test.views.Page', {}, { primary: true });
        expect(mountFrames(ws)[0].track_static).toBeUndefined();
    });

    it('stale_static on the mount reply fires dj:stale-assets', async () => {
        const { dom, ws, reloads } = await loadWsPage(HEAD);
        const events = [];
        dom.window.document.addEventListener('dj:stale-assets', (e) => events.push(e.detail));
        ws.primaryViewPath = 'test.views.Page';
        await ws.handleMessage({
            type: 'mount', view: 'test.views.Page', version: 1,
            stale_static: ['/static/js/app.0123456789ab.js'],
        });
        expect(events).toEqual([{ changed: ['/static/js/app.0123456789ab.js'] }]);
        expect(reloads).toEqual([]);
    });

    it('a stale dj-track-static="reload" asset reloads the page', async () => {
        const { dom, ws, reloads } = await loadWsPage(
            '<script dj-track-static="reload" src="/static/js/app.0123456789ab.js"></script>');
        const events = [];
        dom.window.document.addEventListener('dj:stale-assets', (e) => events.push(e.detail));
        ws.primaryViewPath = 'test.views.Page';
        await ws.handleMessage({
            type: 'mount', view: 'test.views.Page', version: 1,
            stale_static: ['/static/js/app.0123456789ab.js'],
        });
        expect(reloads.length).toBe(1);
        expect(events).toEqual([]);
    });

    it('a mount reply without stale_static does nothing', async () => {
        const { dom, ws, reloads } = await loadWsPage(HEAD);
        const events = [];
        dom.window.document.addEventListener('dj:stale-assets', (e) => events.push(e.detail));
        ws.primaryViewPath = 'test.views.Page';
        await ws.handleMessage({ type: 'mount', view: 'test.views.Page', version: 1 });
        await ws.handleMessage({
            type: 'mount', view: 'test.views.Page', version: 2, stale_static: [42, ''],
        });
        expect(events).toEqual([]);
        expect(reloads).toEqual([]);
    });
});
