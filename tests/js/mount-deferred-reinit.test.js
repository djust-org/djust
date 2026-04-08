/**
 * Tests for deferred reinitAfterDOMUpdate on pre-rendered mount.
 *
 * When content is pre-rendered via HTTP GET, the WebSocket mount should
 * defer reinitAfterDOMUpdate() to the next animation frame to avoid
 * layout shift. Regression test for #618.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '<div class="stat-value">42</div>') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${bodyHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously' });

    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
            this.onerror = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;

    dom.window.eval(clientCode);

    return { dom, MockWebSocket };
}

describe('pre-rendered mount deferred reinit', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('defers reinitAfterDOMUpdate when requestAnimationFrame is available', () => {
        const { dom } = createDom();
        const rafCallbacks = [];

        // Provide requestAnimationFrame
        dom.window.requestAnimationFrame = (cb) => {
            rafCallbacks.push(cb);
            return rafCallbacks.length;
        };

        // Re-evaluate client code with rAF available
        dom.window.eval(clientCode);

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });
        ws.handleMessage({ type: 'connect', session_id: 'test-session' });

        // Mark as pre-rendered
        ws.skipMountHtml = true;

        // _mountReady should be false before mount
        expect(dom.window.djust._mountReady).toBeFalsy();

        // Trigger mount
        ws.handleMessage({
            type: 'mount',
            view: 'test.views.TestView',
            html: '<div class="stat-value" dj-id="1">42</div>',
            version: 1,
            has_ids: true,
        });

        // After mount but before rAF fires, _mountReady should NOT be set
        // (it's deferred along with reinitAfterDOMUpdate)
        expect(dom.window.djust._mountReady).toBeFalsy();

        // rAF should have been called
        expect(rafCallbacks.length).toBeGreaterThan(0);

        // Execute the deferred callback
        rafCallbacks.forEach(cb => cb());

        // Now _mountReady should be true
        expect(dom.window.djust._mountReady).toBe(true);
    });

    it('falls back to synchronous reinit without requestAnimationFrame', () => {
        const { dom } = createDom();

        // JSDOM doesn't have requestAnimationFrame by default
        delete dom.window.requestAnimationFrame;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });
        ws.handleMessage({ type: 'connect', session_id: 'test-session' });

        ws.skipMountHtml = true;

        ws.handleMessage({
            type: 'mount',
            view: 'test.views.TestView',
            html: '<div class="stat-value">42</div>',
            version: 1,
        });

        // Without rAF, _mountReady should be set synchronously
        expect(dom.window.djust._mountReady).toBe(true);
    });

    it('does not modify pre-rendered DOM content during mount', () => {
        const { dom } = createDom('<div id="stat">42</div><p id="desc">description</p>');

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });
        ws.handleMessage({ type: 'connect', session_id: 'test-session' });

        ws.skipMountHtml = true;

        const statBefore = dom.window.document.getElementById('stat').textContent;
        const descBefore = dom.window.document.getElementById('desc').textContent;

        ws.handleMessage({
            type: 'mount',
            view: 'test.views.TestView',
            html: '<div id="stat" dj-id="1">42</div><p id="desc" dj-id="2">description</p>',
            version: 1,
            has_ids: true,
        });

        // Content should not change during pre-rendered mount
        expect(dom.window.document.getElementById('stat').textContent).toBe(statBefore);
        expect(dom.window.document.getElementById('desc').textContent).toBe(descBefore);
    });
});
