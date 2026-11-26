/**
 * #3658 review: a `defer()` follow-up (an effects-only `render` frame with no
 * `ref`) must not overtake the server frames the WebSocket holds while an
 * event is pending (#560). It is held behind them and applied in arrival order.
 * Also: the service-worker reconnection bridge names the protocol on a
 * buffered mount verb, since its replay bypasses sendMessage.
 */
import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <div dj-view="test.views.TestView" dj-root><span id="counter">0</span></div>
</body></html>`, { runScripts: 'dangerously', url: 'http://localhost/' });
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.eval(clientCode);
    return dom;
}

async function mountedSocket(dom) {
    const ws = new dom.window.djust.LiveViewWebSocket();
    ws.enabled = true;
    ws.ws = new dom.window.WebSocket();
    ws.viewMounted = true;
    await ws.handleMessage({
        type: 'mount', view: 'test.views.TestView', version: 1,
        html: '<div dj-root><span id="counter">0</span></div>',
    });
    return ws;
}

describe('deferred follow-up keeps arrival order (#3658)', () => {
    it('is held behind a held async frame and applied after it', async () => {
        const dom = createDom();
        const ws = await mountedSocket(dom);
        const seen = [];
        dom.window.addEventListener('djust:push_event', (e) => {
            seen.push([e.detail.event, dom.window.document.getElementById('counter').textContent]);
        });

        ws.sendEvent('click', {});
        const ref = dom.window.djust._getEventSeqState().pendingEventRef;

        // An async turn's render is held while the click is pending ...
        await ws.handleMessage({
            type: 'render', source: 'async', version: 2,
            html: '<div dj-root><span id="counter">7</span></div>', effects: [],
        });
        // ... and its defer() follow-up must wait behind it.
        await ws.handleMessage({
            type: 'render', source: 'deferred',
            effects: [{ kind: 'push_event', event: 'after_async', payload: {} }],
        });
        expect(dom.window.djust._getEventSeqState().tickBufferLength).toBe(2);
        expect(seen).toEqual([]);

        // The click's answer settles; the held frames apply in arrival order.
        await ws.handleMessage({ type: 'render', source: 'event', ref, effects: [] });
        expect(dom.window.djust._getEventSeqState().tickBufferLength).toBe(0);
        expect(seen).toEqual([['after_async', '7']]);
    });

    it('a ref-less frame is not held when nothing is held', async () => {
        const dom = createDom();
        const ws = await mountedSocket(dom);
        const seen = [];
        dom.window.addEventListener('djust:push_event', (e) => seen.push(e.detail.event));
        await ws.handleMessage({
            type: 'render', source: 'deferred',
            effects: [{ kind: 'push_event', event: 'now', payload: {} }],
        });
        expect(seen).toEqual(['now']);
        expect(dom.window.djust._getEventSeqState().tickBufferLength).toBe(0);
    });

    it('a flush_push_events() frame applies on arrival while an event is pending', async () => {
        const dom = createDom();
        const ws = await mountedSocket(dom);
        const seen = [];
        dom.window.addEventListener('djust:push_event', (e) => seen.push(e.detail.event));
        ws.sendEvent('click', {});
        // source "flush" is outside the held set: with nothing held it is not
        // held back behind the pending click (as "async" would be).
        await ws.handleMessage({
            type: 'render', source: 'flush',
            effects: [{ kind: 'push_event', event: 'progress', payload: {} }],
        });
        expect(seen).toEqual(['progress']);
        expect(dom.window.djust._getEventSeqState().tickBufferLength).toBe(0);
    });
});

describe('service-worker reconnection bridge names the protocol (#3658)', () => {
    it('adds protocol to a buffered mount verb, not to other frames', () => {
        const dom = createDom();
        const posted = [];
        Object.defineProperty(dom.window.navigator, 'serviceWorker', {
            configurable: true,
            value: { controller: { postMessage: (m) => posted.push(m) }, addEventListener() {} },
        });
        const ws = {
            ws: { readyState: 3 },
            sendMessage: () => { throw new Error('must not reach the closed socket'); },
        };
        dom.window.djust.ws = ws;
        dom.window.djust._sw.initReconnectionBridge();

        ws.sendMessage({ type: 'mount', view: 'v' });
        ws.sendMessage({ type: 'live_redirect_mount', view: 'v' });
        ws.sendMessage({ type: 'event', event: 'e' });

        const payloads = posted.filter((m) => m.type === 'DJUST_BUFFER').map((m) => JSON.parse(m.payload));
        expect(payloads[0].protocol).toBe(dom.window.djust.PROTOCOL);
        expect(payloads[1].protocol).toBe(dom.window.djust.PROTOCOL);
        expect('protocol' in payloads[2]).toBe(false);
    });
});
