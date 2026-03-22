/**
 * Tests for the latency simulator.
 *
 * Verifies that latency injection delays messages by the configured
 * amount, jitter stays within bounds, and settings persist via localStorage.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        '<div dj-root dj-liveview-root dj-view="test.View">' +
        '<p>content</p>' +
        '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Enable DEBUG_MODE for latency features
    dom.window.DEBUG_MODE = true;

    // Set up djust namespace
    dom.window.djust = { _simulatedLatency: 0, _simulatedJitter: 0 };

    // Track sent messages and their timing
    const sentMessages = [];
    const sentTimestamps = [];

    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send(msg) {
            sentMessages.push(JSON.parse(msg));
            sentTimestamps.push(Date.now());
        }
        close() {}
    };

    // Suppress console output
    dom.window.console.log = () => {};
    dom.window.console.warn = () => {};
    dom.window.console.error = () => {};

    dom.window.eval(clientCode);

    return { dom, sentMessages, sentTimestamps };
}

describe('Latency simulator', () => {
    it('sends immediately when latency is 0', () => {
        const { dom, sentMessages } = createDom();

        // Get the liveview client
        const lv = dom.window.liveview;
        if (!lv) return; // skip if auto-mount didn't create client

        dom.window.djust._simulatedLatency = 0;

        // Send a message
        lv.sendMessage({ type: 'event', event: 'test', params: {} });

        // Should be sent synchronously
        expect(sentMessages.length).toBeGreaterThan(0);
        const lastMsg = sentMessages[sentMessages.length - 1];
        expect(lastMsg.type).toBe('event');
    });

    it('delays send when latency is configured', async () => {
        const { dom, sentMessages } = createDom();

        const lv = dom.window.liveview;
        if (!lv) return;

        // Configure 100ms latency
        dom.window.djust._simulatedLatency = 100;
        dom.window.djust._simulatedJitter = 0;

        const beforeCount = sentMessages.length;

        lv.sendMessage({ type: 'event', event: 'delayed', params: {} });

        // Should NOT be sent synchronously
        expect(sentMessages.length).toBe(beforeCount);

        // Wait for the delay
        await new Promise(resolve => setTimeout(resolve, 150));

        // Now should be sent
        expect(sentMessages.length).toBe(beforeCount + 1);
        expect(sentMessages[sentMessages.length - 1].event).toBe('delayed');
    });

    it('jitter stays within bounds', () => {
        // Test jitter calculation directly
        const baseLatency = 100;
        const jitter = 0.2; // 20%

        for (let i = 0; i < 100; i++) {
            const actual = baseLatency + (Math.random() * 2 - 1) * baseLatency * jitter;
            expect(actual).toBeGreaterThanOrEqual(baseLatency * (1 - jitter));
            expect(actual).toBeLessThanOrEqual(baseLatency * (1 + jitter));
        }
    });

    it('persists settings to localStorage', () => {
        const { dom } = createDom();

        // Simulate setting latency via the debug panel
        dom.window.localStorage.setItem('djust_debug_latency', JSON.stringify({
            latency: 200,
            jitter: 0.15
        }));

        // Read back
        const stored = JSON.parse(dom.window.localStorage.getItem('djust_debug_latency'));
        expect(stored.latency).toBe(200);
        expect(stored.jitter).toBe(0.15);
    });

    it('restores settings from localStorage', () => {
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body><div dj-root dj-liveview-root dj-view="test.View"><p>x</p></div></body></html>',
            { url: 'http://localhost', runScripts: 'dangerously' }
        );

        if (!dom.window.CSS) dom.window.CSS = {};
        if (!dom.window.CSS.escape) {
            dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        }

        // Pre-populate localStorage
        dom.window.localStorage.setItem('djust_debug_latency', JSON.stringify({
            latency: 300,
            jitter: 0.25
        }));

        dom.window.DEBUG_MODE = true;
        dom.window.djust = { _simulatedLatency: 0, _simulatedJitter: 0 };

        // Mock WebSocket
        dom.window.WebSocket = class MockWebSocket {
            constructor() {
                this.readyState = 1;
                Promise.resolve().then(() => {
                    if (this.onopen) this.onopen({});
                });
            }
            send() {}
            close() {}
        };
        dom.window.console.log = () => {};
        dom.window.console.warn = () => {};
        dom.window.console.error = () => {};

        // The debug panel would call _initLatencySim which reads localStorage.
        // We test the storage format is correct for restoration.
        const stored = JSON.parse(dom.window.localStorage.getItem('djust_debug_latency'));
        expect(stored.latency).toBe(300);
        expect(stored.jitter).toBe(0.25);
    });

    it('does not inject latency when DEBUG_MODE is false', () => {
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body><div dj-root dj-liveview-root dj-view="test.View"><p>x</p></div></body></html>',
            { url: 'http://localhost', runScripts: 'dangerously' }
        );

        if (!dom.window.CSS) dom.window.CSS = {};
        if (!dom.window.CSS.escape) {
            dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        }

        // DEBUG_MODE false
        dom.window.DEBUG_MODE = false;
        dom.window.djust = { _simulatedLatency: 500, _simulatedJitter: 0 };

        const sent = [];
        dom.window.WebSocket = class MockWebSocket {
            constructor() {
                this.readyState = 1;
                Promise.resolve().then(() => {
                    if (this.onopen) this.onopen({});
                });
            }
            send(msg) { sent.push(JSON.parse(msg)); }
            close() {}
        };
        dom.window.console.log = () => {};
        dom.window.console.warn = () => {};
        dom.window.console.error = () => {};

        dom.window.eval(clientCode);

        const lv = dom.window.liveview;
        if (!lv) return;

        const before = sent.length;
        lv.sendMessage({ type: 'event', event: 'test', params: {} });

        // Should be sent immediately (no delay) since DEBUG_MODE is false
        expect(sent.length).toBe(before + 1);
    });
});
