/**
 * #4236 — a `@live_view` function view has no class a socket can mount, so its
 * page marks the root `data-djust-transport="http"`. The client then uses the
 * HTTP fallback directly: no WebSocket, no SSE fallback, no refused mount, and
 * no LIVEVIEW_ALLOWED_MODULES warning (the module is allowed; there is just no
 * class). The same holds for a deliberate `use_websocket: False` page, and a
 * TurboNav load honours both.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const MARKED = '<div dj-root dj-view="djust.live_view.DynamicLiveView" data-djust-transport="http"><p>x</p></div>';
const UNMARKED = '<div dj-root dj-view="app.views.Page"><p>x</p></div>';

function mountPage(body, { preInit = null, eventSource = true } = {}) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${body}</body></html>`, {
        url: 'http://localhost/fn/',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
    });
    const { window } = dom;
    const counts = { ws: 0, sse: 0, fetch: 0 };
    const warnings = [];
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            counts.ws += 1;
            this.readyState = MockWebSocket.CONNECTING;
        }
        send() {}
        close() {}
    }
    window.WebSocket = MockWebSocket;
    if (eventSource) {
        window.EventSource = class {
            constructor() {
                counts.sse += 1;
                this.readyState = 0;
            }
            close() {}
            addEventListener() {}
        };
    }
    window.fetch = async () => {
        counts.fetch += 1;
        return {
            ok: true,
            status: 200,
            headers: { get: () => 'text/html' },
            text: async () => '<html><body></body></html>',
            json: async () => ({}),
        };
    };
    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...a) => warnings.push(a.join(' ')),
    };
    if (preInit) preInit(window);
    window.eval(clientCode);
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    const allowlistWarnings = () => warnings.filter((w) => w.includes('LIVEVIEW_ALLOWED_MODULES'));
    return { window, counts, allowlistWarnings };
}

describe('#4236 a page that asks for the HTTP transport', () => {
    it('a root marked data-djust-transport="http" opens no WebSocket', () => {
        const { window, counts } = mountPage(MARKED);
        expect(counts.ws).toBe(0);
        expect(window.djust.liveViewInstance).toBeTruthy();
        expect(window.djust.liveViewInstance.enabled).toBe(false);
    });

    it('does not fall over to SSE when EventSource exists', () => {
        const { counts } = mountPage(MARKED);
        expect(counts.sse).toBe(0);
    });

    it('sends an event over the HTTP fallback with no allowlist warning', async () => {
        const { window, counts, allowlistWarnings } = mountPage(MARKED);
        await window.djust.handleEvent('increment', {});
        expect(counts.fetch).toBe(1);
        expect(allowlistWarnings()).toEqual([]);
    });

    it('a TurboNav load onto a marked page opens no WebSocket', () => {
        const { window, counts } = mountPage(UNMARKED);
        expect(counts.ws).toBe(1);
        window.document.body.innerHTML = MARKED;
        window.djustInitialized = true;
        window.dispatchEvent(new window.Event('turbo:load'));
        expect(counts.ws).toBe(1);
        expect(window.djust.liveViewInstance.enabled).toBe(false);
    });

    it('a use_websocket: False page sends events over HTTP without the allowlist warning', async () => {
        const { window, counts, allowlistWarnings } = mountPage(UNMARKED, {
            eventSource: false,
            preInit: (w) => { w.DJUST_USE_WEBSOCKET = false; },
        });
        expect(counts.ws).toBe(0);
        await window.djust.handleEvent('increment', {});
        expect(counts.fetch).toBe(1);
        expect(allowlistWarnings()).toEqual([]);
    });

    it('a lazy marked root hydrates over HTTP: no WebSocket, no allowlist warning', async () => {
        // Review S2: a dj-lazy root starts no page transport, so its hydration
        // must read the marker itself.
        const LAZY = '<div dj-root dj-lazy="click" dj-view="djust.live_view.DynamicLiveView" '
            + 'data-djust-transport="http" data-djust-lazy-id="lazy-1"><p>x</p></div>';
        const { window, counts, allowlistWarnings } = mountPage(LAZY);
        expect(counts.ws).toBe(0);
        window.document.querySelector('[dj-lazy]').dispatchEvent(new window.Event('click'));
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(counts.ws).toBe(0);
        expect(counts.sse).toBe(0);
        expect(counts.fetch).toBe(1);
        expect(allowlistWarnings()).toEqual([]);
    });

    it('an unmarked page still opens a WebSocket', () => {
        const { counts } = mountPage(UNMARKED);
        expect(counts.ws).toBe(1);
        expect(counts.sse).toBe(0);
    });
});
