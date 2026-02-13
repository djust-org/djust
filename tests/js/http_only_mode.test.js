/**
 * Tests for HTTP-only mode (use_websocket: False).
 *
 * Verifies that when window.DJUST_USE_WEBSOCKET === false:
 * - No WebSocket connection is opened
 * - A disabled LiveViewWebSocket instance is created for eager containers
 * - Lazy hydration skips WebSocket connection
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * Create a JSDOM environment with a LiveView container.
 * @param {object} opts
 * @param {boolean} opts.useWebSocket - value for DJUST_USE_WEBSOCKET (default: undefined)
 * @param {boolean} opts.lazy - add dj-lazy attribute (default: false)
 */
function createDom({ useWebSocket, lazy = false } = {}) {
    const lazyAttr = lazy ? ' dj-lazy' : '';
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        `<div dj-root dj-liveview-root dj-view="test.View"${lazyAttr}>` +
        '<p>content</p>' +
        '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    // Polyfill CSS.escape
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Track WebSocket construction attempts
    let wsConstructed = false;
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            wsConstructed = true;
            this.readyState = 1;
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send() {}
        close() {}
    };
    dom.window.WebSocket.OPEN = 1;
    dom.window.WebSocket.CONNECTING = 0;
    dom.window.WebSocket.CLOSING = 2;
    dom.window.WebSocket.CLOSED = 3;

    // Set DJUST_USE_WEBSOCKET before client.js runs
    if (useWebSocket !== undefined) {
        dom.window.DJUST_USE_WEBSOCKET = useWebSocket;
    }

    // Suppress console noise
    const origConsole = dom.window.console;
    dom.window.console = {
        log: () => {},
        error: origConsole.error.bind(origConsole),
        warn: () => {},
        debug: () => {},
        info: () => {},
    };

    dom.window.eval(clientCode);

    return { dom, wasWsConstructed: () => wsConstructed };
}

describe('HTTP-only mode (DJUST_USE_WEBSOCKET === false)', () => {
    it('does not open a WebSocket connection', async () => {
        const { dom, wasWsConstructed } = createDom({ useWebSocket: false });
        await new Promise(r => setTimeout(r, 10));

        // WebSocket constructor should not have been called
        expect(wasWsConstructed()).toBe(false);
    });

    it('creates a disabled LiveViewWebSocket instance', async () => {
        const { dom } = createDom({ useWebSocket: false });
        await new Promise(r => setTimeout(r, 10));

        const ws = dom.window.djust.liveViewInstance;
        expect(ws).toBeDefined();
        expect(ws.enabled).toBe(false);
    });

    it('does not start heartbeat in HTTP-only mode', async () => {
        const { dom } = createDom({ useWebSocket: false });
        await new Promise(r => setTimeout(r, 10));

        const ws = dom.window.djust.liveViewInstance;
        // heartbeat interval should not be set
        expect(ws._heartbeatInterval || ws.heartbeatInterval || null).toBeNull();
    });
});

describe('WebSocket mode (default)', () => {
    it('opens a WebSocket connection when DJUST_USE_WEBSOCKET is not set', async () => {
        const { wasWsConstructed } = createDom({ useWebSocket: undefined });
        await new Promise(r => setTimeout(r, 10));

        expect(wasWsConstructed()).toBe(true);
    });

    it('opens a WebSocket connection when DJUST_USE_WEBSOCKET is true', async () => {
        const { wasWsConstructed } = createDom({ useWebSocket: true });
        await new Promise(r => setTimeout(r, 10));

        expect(wasWsConstructed()).toBe(true);
    });
});

describe('Lazy hydration in HTTP-only mode', () => {
    it('does not open WebSocket for lazy elements when DJUST_USE_WEBSOCKET is false', async () => {
        const { dom, wasWsConstructed } = createDom({ useWebSocket: false, lazy: true });
        await new Promise(r => setTimeout(r, 10));

        // Force hydrate all lazy elements
        dom.window.djust.lazyHydration.hydrateAll();
        await new Promise(r => setTimeout(r, 10));

        // Still no WebSocket connection
        expect(wasWsConstructed()).toBe(false);
    });
});
