/**
 * Tests for dj-cloak — FOUC prevention attribute
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '<span>content</span>') {
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

describe('dj-cloak', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('injects [dj-cloak] CSS rule that hides cloaked elements', () => {
        const { dom } = createDom('<div dj-cloak>hidden content</div>');
        const doc = dom.window.document;

        const styles = doc.querySelectorAll('style');
        let found = false;
        for (const style of styles) {
            if (style.textContent.includes('[dj-cloak]') && style.textContent.includes('display')) {
                found = true;
                break;
            }
        }
        expect(found).toBe(true);
    });

    it('removes dj-cloak attribute on WebSocket mount response', () => {
        const { dom } = createDom(
            '<div dj-cloak id="cloaked1">hidden</div><p dj-cloak id="cloaked2">also hidden</p>'
        );
        const doc = dom.window.document;

        expect(doc.getElementById('cloaked1').hasAttribute('dj-cloak')).toBe(true);
        expect(doc.getElementById('cloaked2').hasAttribute('dj-cloak')).toBe(true);

        // Create a new WS and simulate mount
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        ws.handleMessage({ type: 'connect', session_id: 'test-session' });
        ws.skipMountHtml = true;
        ws.handleMessage({ type: 'mount', view: 'test.views.TestView', html: '<div>content</div>', version: 1 });

        expect(doc.getElementById('cloaked1').hasAttribute('dj-cloak')).toBe(false);
        expect(doc.getElementById('cloaked2').hasAttribute('dj-cloak')).toBe(false);
    });

    it('removes dj-cloak on SSE mount response', () => {
        const { dom } = createDom('<div dj-cloak id="sse-cloaked">hidden</div>');
        const doc = dom.window.document;

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

        const sse = new dom.window.djust.LiveViewSSE();
        sse.connect('test.View', {});

        expect(doc.getElementById('sse-cloaked').hasAttribute('dj-cloak')).toBe(true);

        sse.handleMessage({ type: 'mount', view: 'test.View', html: '<div>content</div>', version: 1, has_ids: true });

        expect(doc.getElementById('sse-cloaked').hasAttribute('dj-cloak')).toBe(false);
    });

    it('works correctly when no dj-cloak elements exist', () => {
        const { dom } = createDom('<div>normal content</div>');

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        ws.handleMessage({ type: 'connect', session_id: 'test-session' });
        ws.skipMountHtml = true;

        expect(() => {
            ws.handleMessage({ type: 'mount', view: 'test.views.TestView', html: '<div>content</div>', version: 1 });
        }).not.toThrow();
    });
});
