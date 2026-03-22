/**
 * Tests for dj-mounted — fire server event when element enters DOM after VDOM patch.
 *
 * <div dj-mounted="on_chart_ready" dj-value-chart-type="bar">...</div>
 * Fires the handler when the element appears in the DOM (not on initial page load).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
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

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

function getFetchCalls(dom) {
    return dom.window._testFetchCalls;
}

describe('dj-mounted', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('does NOT fire on initial page load (only after VDOM patches)', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div dj-mounted="on_ready">Content</div>
            </div>
        `);
        initClient(dom);

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const mountCall = calls.find(c => c.eventName === 'on_ready');
        expect(mountCall).toBeUndefined();
    });

    it('fires handler when element is added after bindLiveViewEvents with _mountReady', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div id="container"></div>
            </div>
        `);
        initClient(dom);

        // Set mount ready flag (simulating initial mount complete)
        dom.window.djust._mountReady = true;

        // Simulate VDOM patch adding a new element with dj-mounted
        const container = dom.window.document.getElementById('container');
        const newEl = dom.window.document.createElement('div');
        newEl.setAttribute('dj-mounted', 'on_chart_ready');
        container.appendChild(newEl);

        // Re-bind (as happens after every VDOM patch)
        dom.window.djust.bindLiveViewEvents();

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const mountCall = calls.find(c => c.eventName === 'on_chart_ready');
        expect(mountCall).toBeDefined();
    });

    it('does not fire again for already-mounted elements', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div id="container"></div>
            </div>
        `);
        initClient(dom);

        dom.window.djust._mountReady = true;

        const container = dom.window.document.getElementById('container');
        const newEl = dom.window.document.createElement('div');
        newEl.setAttribute('dj-mounted', 'on_ready');
        container.appendChild(newEl);

        // First bind — should fire
        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(200);

        const callsAfterFirst = getFetchCalls(dom).filter(c => c.eventName === 'on_ready').length;
        expect(callsAfterFirst).toBe(1);

        // Second bind — same element, should NOT fire again
        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(200);

        const callsAfterSecond = getFetchCalls(dom).filter(c => c.eventName === 'on_ready').length;
        expect(callsAfterSecond).toBe(1);
    });

    it('includes dj-value-* params from mounted element', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div id="container"></div>
            </div>
        `);
        initClient(dom);

        dom.window.djust._mountReady = true;

        const container = dom.window.document.getElementById('container');
        const newEl = dom.window.document.createElement('div');
        newEl.setAttribute('dj-mounted', 'on_chart_ready');
        newEl.setAttribute('dj-value-chart-type', 'bar');
        newEl.setAttribute('dj-value-item-id:int', '42');
        container.appendChild(newEl);

        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const mountCall = calls.find(c => c.eventName === 'on_chart_ready');
        expect(mountCall).toBeDefined();
        expect(mountCall.body.chart_type).toBe('bar');
        expect(mountCall.body.item_id).toBe(42);
    });

    it('fires for replaced element (new DOM node with same dj-mounted)', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div id="container">
                    <div id="widget" dj-mounted="on_widget_ready">Widget</div>
                </div>
            </div>
        `);
        initClient(dom);

        dom.window.djust._mountReady = true;

        // First mount
        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(200);

        const callsAfterFirst = getFetchCalls(dom).filter(c => c.eventName === 'on_widget_ready').length;
        expect(callsAfterFirst).toBe(1);

        // Simulate VDOM replace: remove old element and add new one
        const container = dom.window.document.getElementById('container');
        container.innerHTML = '<div id="widget" dj-mounted="on_widget_ready">New Widget</div>';

        // New DOM node — should fire again
        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(200);

        const callsAfterSecond = getFetchCalls(dom).filter(c => c.eventName === 'on_widget_ready').length;
        expect(callsAfterSecond).toBe(2);
    });
});
