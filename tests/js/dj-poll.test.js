/**
 * Tests for dj-poll declarative polling
 *
 * Tests the poll binding, interval handling, visibility pausing,
 * _skipLoading flag, and cleanup on reinit.
 *
 * Issue: https://github.com/djust-org/djust/issues/269
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * Create a JSDOM instance with dj-poll element and fetch spy.
 * Uses real JSDOM timers (not mocked) since mocked timers break
 * the closure scope chain for function declarations inside the
 * client.js double-load guard block.
 */
function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    // Set up stubs and spies inside JSDOM context
    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;

        // Stub location.reload to prevent JSDOM "Not implemented" errors
        window.location.reload = function() {};

        // JSDOM defaults to hidden=true (visibilityState="prerender").
        // Override to simulate a visible page for poll tests.
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

/** Eval client.js and trigger DOMContentLoaded to initialize djust */
function initClient(dom) {
    dom.window.eval(clientCode);
    // JSDOM readyState is 'loading' — manually fire DOMContentLoaded
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

/** Get captured fetch calls from inside JSDOM */
function getFetchCalls(dom) {
    return dom.window._testFetchCalls;
}

describe('dj-poll', () => {
    it('should set liveviewPollBound flag on element', () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="refresh"></div></div>'
        );
        initClient(dom);

        const el = dom.window.document.querySelector('[dj-poll]');
        expect(el.dataset.liveviewPollBound).toBe('true');
    });

    it('should store interval ID on element', () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="refresh"></div></div>'
        );
        initClient(dom);

        const el = dom.window.document.querySelector('[dj-poll]');
        expect(el._djustPollIntervalId).toBeDefined();
    });

    it('should use custom interval attribute (binding test)', () => {
        // We can't easily verify the exact ms value without mocking,
        // but we verify the element is bound and has an interval ID
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="check" dj-poll-interval="2000"></div></div>'
        );
        initClient(dom);

        const el = dom.window.document.querySelector('[dj-poll]');
        expect(el.dataset.liveviewPollBound).toBe('true');
        expect(el._djustPollIntervalId).toBeDefined();
    });

    it('should fire poll event via HTTP fallback with _skipLoading', async () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="refresh" dj-poll-interval="100"></div></div>'
        );
        initClient(dom);

        expect(getFetchCalls(dom).length).toBe(0);

        // Use Node.js native timer to wait — happy-dom's setTimeout may not
        // be synchronized with JSDOM's setInterval
        await nativeSleep(500);

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].eventName).toBe('refresh');
        expect(calls[0].body._skipLoading).toBe(true);
    });

    it('should skip poll when document is hidden', async () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="refresh" dj-poll-interval="100"></div></div>'
        );
        initClient(dom);

        // Simulate page hidden
        Object.defineProperty(dom.window.document, 'hidden', {
            value: true,
            writable: true,
            configurable: true,
        });

        await nativeSleep(500);
        // Should NOT fire when hidden
        expect(getFetchCalls(dom).length).toBe(0);

        // Make visible again
        Object.defineProperty(dom.window.document, 'hidden', {
            value: false,
            writable: true,
            configurable: true,
        });

        await nativeSleep(500);
        expect(getFetchCalls(dom).length).toBeGreaterThanOrEqual(1);
    });

    it('should clean up and re-bind poll after clearing', () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="refresh"></div></div>'
        );
        initClient(dom);

        const el = dom.window.document.querySelector('[dj-poll]');
        const firstId = el._djustPollIntervalId;
        expect(firstId).toBeDefined();

        // Simulate cleanup
        dom.window.clearInterval(firstId);
        delete el.dataset.liveviewPollBound;

        // Re-bind
        dom.window.djust.bindLiveViewEvents();

        expect(el.dataset.liveviewPollBound).toBe('true');
        expect(el._djustPollIntervalId).toBeDefined();
        // Should be a different interval ID
        expect(el._djustPollIntervalId).not.toBe(firstId);
    });

    it('should extract data-* params and include them in poll requests', async () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.PollView"><div dj-poll="refresh" dj-poll-interval="100" data-room-id:int="42"></div></div>'
        );
        initClient(dom);

        await nativeSleep(500);

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].body.room_id).toBe(42);
    });

    it('should not create interval for elements without dj-poll', () => {
        const dom = createTestEnv(
            '<div data-djust-view="app.MyView"><button dj-click="do_thing">Click</button></div>'
        );
        initClient(dom);

        const el = dom.window.document.querySelector('button');
        expect(el._djustPollIntervalId).toBeUndefined();
        expect(el.dataset.liveviewPollBound).toBeUndefined();
    });
});
