/**
 * #2963: the WebSocket client keeps a loading state up across a reply that
 * carries ``async_pending: true`` and ends it on the work's
 * ``source: "async"`` result frame (which names the event).
 *
 * The server now sets ``async_pending`` for ``start_async`` / ``@background``
 * work (it only ever read a legacy attribute before), and sends the async
 * result frame even when the work raises without ``handle_async_result``.
 * Both frames are shapes the shipped client already handles; this pins that
 * lifecycle end to end through ``handleMessage`` so 1.2.1 needs no client
 * change.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html>
<html>
<body>
  <div dj-view="test.views.TestView" dj-root>
    <span id="counter">0</span>
    <div id="spinner" dj-loading.show dj-loading.for="generate" style="display:none">Loading</div>
  </div>
</body>
</html>`, { runScripts: 'dangerously' });

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = function(value) {
            return String(value).replace(/([^\w-])/g, '\\$1');
        };
    }

    // Stub WebSocket with proper constants
    const sentMessages = [];
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
        send(data) {
            sentMessages.push(JSON.parse(data));
        }
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;

    dom.window.eval(clientCode);

    function getSeqState() {
        return dom.window.djust._getEventSeqState();
    }

    return { dom, sentMessages, getSeqState, MockWebSocket };
}

/**
 * Create a mounted LiveViewWebSocket with VDOM version initialized to 1.
 * Uses the mount message to initialize clientVdomVersion in the correct scope.
 */
async function makeWS(dom) {
    const ws = new dom.window.djust.LiveViewWebSocket();
    ws.enabled = true;
    ws.ws = new dom.window.WebSocket();
    ws.viewMounted = true;

    // Initialize VDOM version via mount (sets let-scoped clientVdomVersion)
    await ws.handleMessage({
        type: 'mount',
        view: 'test.views.TestView',
        version: 1,
        html: '<div dj-root><span id="counter">0</span></div>',
    });

    return ws;
}

function loading(dom) {
    return dom.window.djust.globalLoadingManager.pendingEvents.has('generate');
}

describe('async_pending loading lifecycle over the socket (#2963)', () => {
    it('noop with async_pending keeps loading; the async result frame ends it', async () => {
        const { dom, sentMessages } = createDom();
        const ws = await makeWS(dom);
        dom.window.djust.globalLoadingManager.scanAndRegister();
        dom.window.djust.globalLoadingManager.startLoading('generate');
        ws.sendEvent('generate', {});
        const ref = sentMessages.filter(m => m.type === 'event')[0].ref;

        await ws.handleMessage({ type: 'noop', ref, async_pending: true });
        expect(loading(dom)).toBe(true);

        await ws.handleMessage({
            type: 'patch', patches: [], version: 2, event_name: 'generate', source: 'async',
        });
        expect(loading(dom)).toBe(false);
    });

    it('a patch reply with async_pending keeps loading until the async frame', async () => {
        const { dom, sentMessages } = createDom();
        const ws = await makeWS(dom);
        dom.window.djust.globalLoadingManager.scanAndRegister();
        dom.window.djust.globalLoadingManager.startLoading('generate');
        ws.sendEvent('generate', {});
        const ref = sentMessages.filter(m => m.type === 'event')[0].ref;

        await ws.handleMessage({
            type: 'patch', patches: [], version: 2, event_name: 'generate',
            source: 'event', ref, async_pending: true,
        });
        expect(loading(dom)).toBe(true);

        await ws.handleMessage({
            type: 'patch', patches: [], version: 3, event_name: 'generate', source: 'async',
        });
        expect(loading(dom)).toBe(false);
    });

    it('a reply without async_pending ends loading at once (unchanged)', async () => {
        const { dom, sentMessages } = createDom();
        const ws = await makeWS(dom);
        dom.window.djust.globalLoadingManager.scanAndRegister();
        dom.window.djust.globalLoadingManager.startLoading('generate');
        ws.sendEvent('generate', {});
        const ref = sentMessages.filter(m => m.type === 'event')[0].ref;

        await ws.handleMessage({ type: 'noop', ref });
        expect(loading(dom)).toBe(false);
    });
});
