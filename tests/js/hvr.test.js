/**
 * Tests for Hot View Replacement (HVR, v0.6.1) client-side indicator.
 *
 * Exercises:
 *   1. An incoming ``hvr-applied`` message routes through 03-websocket.js,
 *      dispatches a ``djust:hvr-applied`` CustomEvent on ``document``, and
 *      (in debug mode) triggers the toast indicator.
 *   2. The toast DOM element is appended with the expected aria / text
 *      content under ``globalThis.djustDebug = true``.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '<span>content</span>') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head>
         <body><div dj-view="test.V" dj-root>${bodyHtml}</div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
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
    return dom;
}

describe('HVR client', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it("routes hvr-applied frame to djust:hvr-applied CustomEvent", async () => {
        const dom = createDom();
        const { document } = dom.window;
        // NOTE: djustDebug left falsy here so showIndicator no-ops and we
        // only assert the CustomEvent path.
        globalThis.djustDebug = false;

        const spy = vi.fn();
        document.addEventListener('djust:hvr-applied', spy);

        // Construct an LiveViewWebSocket and invoke its message handler
        // directly — the same path that runs on a real `onmessage`.
        const ws = new dom.window.djust.LiveViewWebSocket();
        await ws.handleMessage({
            type: 'hvr-applied',
            view: 'app.views.Dashboard',
            version: 3,
            file: '/app/views/dashboard.py',
        });

        expect(spy).toHaveBeenCalledTimes(1);
        const event = spy.mock.calls[0][0];
        expect(event.detail).toMatchObject({
            type: 'hvr-applied',
            view: 'app.views.Dashboard',
            version: 3,
        });
    });

    it("paints a toast indicator when djustDebug is true", () => {
        const dom = createDom();
        const { window } = dom;
        const { document } = window;
        window.djustDebug = true;
        globalThis.djustDebug = true;

        // The toast helper lives on djust.hvr — hvr-applied handling in
        // 03-websocket routes through it.
        expect(window.djust.hvr).toBeTruthy();
        window.djust.hvr.showIndicator({
            view: 'app.views.Counter',
            version: 1,
            file: '/app/views/counter.py',
        });

        const toast = document.querySelector('.djust-hvr-toast');
        expect(toast).not.toBeNull();
        expect(toast.getAttribute('role')).toBe('status');
        expect(toast.textContent).toContain('HVR applied');
        expect(toast.textContent).toContain('app.views.Counter');
        expect(toast.textContent).toContain('v1');

        // Clean up global so other tests aren't impacted.
        globalThis.djustDebug = false;
    });

    it("no toast when djustDebug is false", () => {
        const dom = createDom();
        const { window } = dom;
        const { document } = window;
        window.djustDebug = false;
        globalThis.djustDebug = false;

        window.djust.hvr.showIndicator({
            view: 'app.views.Counter',
            version: 1,
        });

        const toast = document.querySelector('.djust-hvr-toast');
        expect(toast).toBeNull();
    });
});
