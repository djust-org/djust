/**
 * Tests for connection state CSS classes on <body>
 * (dj-connected / dj-disconnected)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    <span>content</span>
  </div>
</body>
</html>`, { runScripts: 'dangerously' });

    // Stub WebSocket
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

describe('Connection State CSS Classes', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('adds dj-connected on WebSocket open', () => {
        const { dom } = createDom();
        const body = dom.window.document.body;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');

        // Simulate onopen on the internal ws
        ws.ws.onopen({ type: 'open' });

        expect(body.classList.contains('dj-connected')).toBe(true);
        expect(body.classList.contains('dj-disconnected')).toBe(false);
    });

    it('adds dj-disconnected on WebSocket close', () => {
        const { dom } = createDom();
        const body = dom.window.document.body;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.maxReconnectAttempts = 0;
        ws.connect('ws://localhost/ws/live/');

        // Open first
        ws.ws.onopen({ type: 'open' });
        expect(body.classList.contains('dj-connected')).toBe(true);

        // Then close
        ws.ws.readyState = 3; // CLOSED
        ws.ws.onclose({ type: 'close' });

        expect(body.classList.contains('dj-disconnected')).toBe(true);
        expect(body.classList.contains('dj-connected')).toBe(false);
    });

    it('removes both classes on intentional disconnect()', () => {
        const { dom } = createDom();
        const body = dom.window.document.body;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');

        ws.ws.onopen({ type: 'open' });
        expect(body.classList.contains('dj-connected')).toBe(true);

        // Intentional disconnect
        ws.disconnect();

        expect(body.classList.contains('dj-connected')).toBe(false);
        expect(body.classList.contains('dj-disconnected')).toBe(false);
    });

    it('SSE transport sets dj-connected on EventSource open', () => {
        const { dom } = createDom();
        const body = dom.window.document.body;

        // Mock EventSource
        class MockEventSource {
            static CLOSED = 2;
            constructor() {
                this.readyState = 1;
                this.onmessage = null;
                this.onopen = null;
                this.onerror = null;
            }
            close() {}
        }
        dom.window.EventSource = MockEventSource;
        // #1237: onopen now POSTs a mount frame via sendMessage.
        dom.window.fetch = vi.fn(() =>
            Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
        );

        const sse = new dom.window.djust.LiveViewSSE();
        sse.connect('test.View', {});

        // Simulate onopen
        if (sse.eventSource && sse.eventSource.onopen) {
            sse.eventSource.onopen({ type: 'open' });
        }

        expect(body.classList.contains('dj-connected')).toBe(true);
        expect(body.classList.contains('dj-disconnected')).toBe(false);
    });

    it('SSE transport sets dj-disconnected on EventSource CLOSED error', () => {
        const { dom } = createDom();
        const body = dom.window.document.body;

        class MockEventSource {
            static CLOSED = 2;
            constructor() {
                this.readyState = 1;
                this.onmessage = null;
                this.onopen = null;
                this.onerror = null;
            }
            close() {}
        }
        dom.window.EventSource = MockEventSource;
        dom.window.fetch = vi.fn(() =>
            Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
        );

        const sse = new dom.window.djust.LiveViewSSE();
        sse.connect('test.View', {});

        // Open first
        if (sse.eventSource.onopen) sse.eventSource.onopen({ type: 'open' });
        expect(body.classList.contains('dj-connected')).toBe(true);

        // Simulate CLOSED error
        sse.eventSource.readyState = 2; // CLOSED
        if (sse.eventSource.onerror) sse.eventSource.onerror({ type: 'error' });

        expect(body.classList.contains('dj-disconnected')).toBe(true);
        expect(body.classList.contains('dj-connected')).toBe(false);
    });

    it('SSE disconnect() removes both classes', () => {
        const { dom } = createDom();
        const body = dom.window.document.body;

        class MockEventSource {
            static CLOSED = 2;
            constructor() {
                this.readyState = 1;
                this.onmessage = null;
                this.onopen = null;
                this.onerror = null;
            }
            close() {}
        }
        dom.window.EventSource = MockEventSource;
        dom.window.fetch = vi.fn(() =>
            Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
        );

        const sse = new dom.window.djust.LiveViewSSE();
        sse.connect('test.View', {});

        if (sse.eventSource.onopen) sse.eventSource.onopen({ type: 'open' });
        expect(body.classList.contains('dj-connected')).toBe(true);

        sse.disconnect();

        expect(body.classList.contains('dj-connected')).toBe(false);
        expect(body.classList.contains('dj-disconnected')).toBe(false);
    });
});
