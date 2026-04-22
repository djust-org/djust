/**
 * Tests for dj-track-static — stale asset detection on WS reconnect (v0.6.0).
 *
 * The client seeds a snapshot on DOMContentLoaded and compares against
 * that snapshot on every djust:ws-reconnected event dispatched on
 * document. jsdom doesn't fire actual WS connects in these tests, so
 * we drive djust:ws-reconnected directly.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
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
