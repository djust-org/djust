/**
 * Tests for reconnection backoff with jitter
 *
 * Validates that WebSocket reconnection uses jittered exponential backoff
 * with min/max delay caps, increased max attempts, reconnection banner,
 * and CSS data attribute tracking.
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
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0;
            static OPEN = 1;
            static CLOSING = 2;
            static CLOSED = 3;
            constructor() {
                this.readyState = 1;
                this.onopen = null;
                this.onclose = null;
                this.onmessage = null;
                this.onerror = null;
            }
            send() {}
            close() {}
        };
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });
    `);

    dom.window.eval(clientCode);

    return dom;
}

describe('Reconnection Backoff with Jitter', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('maxReconnectAttempts is 10', () => {
        const dom = createDom();
        const ws = new dom.window.djust.LiveViewWebSocket();
        expect(ws.maxReconnectAttempts).toBe(10);
    });

    it('jitter: delay is always <= baseDelay (capped)', () => {
        const dom = createDom();

        // Run multiple trials
        for (let trial = 0; trial < 10; trial++) {
            const ws = new dom.window.djust.LiveViewWebSocket();
            ws.connect('ws://localhost/ws/live/');
            ws.ws.onopen({ type: 'open' });

            // Capture the setTimeout delay
            let capturedDelay = null;
            const origSetTimeout = dom.window.setTimeout;
            dom.window.setTimeout = function(fn, delay) {
                capturedDelay = delay;
                // Don't actually schedule
            };

            ws.ws.onclose({ type: 'close' });

            dom.window.setTimeout = origSetTimeout;

            if (capturedDelay !== null) {
                const baseDelay = ws.reconnectDelay * Math.pow(2, ws.reconnectAttempts - 1);
                const cappedBase = Math.min(baseDelay, ws.maxReconnectDelayMs);
                expect(capturedDelay).toBeLessThanOrEqual(cappedBase);
            }
        }
    });

    it('min delay: delay is always >= 500ms', () => {
        const dom = createDom();

        // Override Math.random to return 0
        const origRandom = dom.window.Math.random;
        dom.window.Math.random = () => 0;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        let capturedDelay = null;
        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function(fn, delay) {
            capturedDelay = delay;
        };

        ws.ws.onclose({ type: 'close' });

        dom.window.setTimeout = origSetTimeout;
        dom.window.Math.random = origRandom;

        expect(capturedDelay).not.toBeNull();
        expect(capturedDelay).toBeGreaterThanOrEqual(500);
    });

    it('max delay cap: delay never exceeds 30s', () => {
        const dom = createDom();

        // Override Math.random to return near 1.0
        const origRandom = dom.window.Math.random;
        dom.window.Math.random = () => 0.9999;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        // Force high attempt count
        ws.reconnectAttempts = 9;

        let capturedDelay = null;
        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function(fn, delay) {
            capturedDelay = delay;
        };

        ws.ws.onclose({ type: 'close' });

        dom.window.setTimeout = origSetTimeout;
        dom.window.Math.random = origRandom;

        expect(capturedDelay).not.toBeNull();
        expect(capturedDelay).toBeLessThanOrEqual(30000);
    });

    it('attempt count: data-dj-reconnect-attempt increments on each attempt', () => {
        const dom = createDom();
        const body = dom.window.document.body;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        // Suppress actual reconnect scheduling
        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function() {};

        // First disconnect
        ws.ws.onclose({ type: 'close' });
        expect(body.getAttribute('data-dj-reconnect-attempt')).toBe('1');

        // Second disconnect (simulate: just fire onclose again after incrementing)
        ws.ws.onclose({ type: 'close' });
        expect(body.getAttribute('data-dj-reconnect-attempt')).toBe('2');

        dom.window.setTimeout = origSetTimeout;
    });

    it('attempt count cleared on successful reconnect', () => {
        const dom = createDom();
        const body = dom.window.document.body;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function() {};

        ws.ws.onclose({ type: 'close' });
        expect(body.getAttribute('data-dj-reconnect-attempt')).toBe('1');

        dom.window.setTimeout = origSetTimeout;

        // Simulate successful reconnect
        ws.ws.onopen({ type: 'open' });
        expect(body.hasAttribute('data-dj-reconnect-attempt')).toBe(false);
    });

    it('banner appears after first reconnect attempt', () => {
        const dom = createDom();
        const doc = dom.window.document;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        // No banner initially
        expect(doc.getElementById('dj-reconnecting-banner')).toBeNull();

        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function() {};

        ws.ws.onclose({ type: 'close' });

        dom.window.setTimeout = origSetTimeout;

        // Banner should appear
        const banner = doc.getElementById('dj-reconnecting-banner');
        expect(banner).not.toBeNull();
        expect(banner.textContent).toContain('attempt 1 of 10');
    });

    it('banner removed on successful reconnect', () => {
        const dom = createDom();
        const doc = dom.window.document;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function() {};

        ws.ws.onclose({ type: 'close' });
        expect(doc.getElementById('dj-reconnecting-banner')).not.toBeNull();

        dom.window.setTimeout = origSetTimeout;

        // Simulate successful reconnect
        ws.ws.onopen({ type: 'open' });
        expect(doc.getElementById('dj-reconnecting-banner')).toBeNull();
    });

    it('banner removed on intentional disconnect (TurboNav)', () => {
        const dom = createDom();
        const doc = dom.window.document;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function() {};

        ws.ws.onclose({ type: 'close' });
        expect(doc.getElementById('dj-reconnecting-banner')).not.toBeNull();

        dom.window.setTimeout = origSetTimeout;

        // Intentional disconnect (TurboNav)
        ws.disconnect();
        expect(doc.getElementById('dj-reconnecting-banner')).toBeNull();
    });

    it('CSS custom property --dj-reconnect-attempt set correctly', () => {
        const dom = createDom();
        const body = dom.window.document.body;

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });

        const origSetTimeout = dom.window.setTimeout;
        dom.window.setTimeout = function() {};

        ws.ws.onclose({ type: 'close' });
        expect(body.style.getPropertyValue('--dj-reconnect-attempt')).toBe('1');

        dom.window.setTimeout = origSetTimeout;

        // Cleared on reconnect
        ws.ws.onopen({ type: 'open' });
        expect(body.style.getPropertyValue('--dj-reconnect-attempt')).toBe('');
    });
});
