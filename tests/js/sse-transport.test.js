/**
 * Tests for SSE (Server-Sent Events) transport — 03b-sse.js
 *
 * Verifies LiveViewSSE class: connect/disconnect lifecycle, event sending
 * via HTTP POST, message handling, session ID generation, and connection
 * state CSS classes.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Mock EventSource
    const mockEventSource = {
        onopen: null,
        onmessage: null,
        onerror: null,
        readyState: 1, // OPEN
        close: vi.fn(),
        CLOSED: 2,
    };
    window.EventSource = vi.fn(function () { return mockEventSource; });
    window.EventSource.CLOSED = 2;

    // Mock fetch for event POSTs
    window.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document, mockEventSource };
}

describe('LiveViewSSE', () => {
    describe('constructor', () => {
        it('initializes with default state', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();

            expect(sse.sessionId).toBeNull();
            expect(sse.eventSource).toBeNull();
            expect(sse.enabled).toBe(true);
            expect(sse.viewMounted).toBe(false);
            expect(sse.stats.sent).toBe(0);
            expect(sse.stats.received).toBe(0);
        });
    });

    describe('connect', () => {
        it('creates EventSource with session URL', () => {
            const { window, mockEventSource } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');

            expect(window.EventSource).toHaveBeenCalledTimes(1);
            const url = window.EventSource.mock.calls[0][0];
            expect(url).toContain('/djust/sse/');
            expect(url).toContain('view=myapp.views.HomeView');
            expect(sse.sessionId).toBeTruthy();
            expect(sse.eventSource).toBe(mockEventSource);
        });

        it('does not connect when disabled', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.enabled = false;
            sse.connect('myapp.views.HomeView');

            expect(window.EventSource).not.toHaveBeenCalled();
            expect(sse.sessionId).toBeNull();
        });

        it('sets dj-connected CSS class on open', () => {
            const { window, document, mockEventSource } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');

            // Simulate EventSource open
            mockEventSource.onopen();

            expect(document.body.classList.contains('dj-connected')).toBe(true);
            expect(document.body.classList.contains('dj-disconnected')).toBe(false);
        });

        it('forwards URL params to stream URL', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView', { page: '2', sort: 'name' });

            const url = window.EventSource.mock.calls[0][0];
            expect(url).toContain('page=2');
            expect(url).toContain('sort=name');
        });
    });

    describe('disconnect', () => {
        it('closes EventSource and resets state', () => {
            const { window, mockEventSource } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');
            sse.disconnect();

            expect(mockEventSource.close).toHaveBeenCalled();
            expect(sse.eventSource).toBeNull();
            expect(sse.sessionId).toBeNull();
            expect(sse.viewMounted).toBe(false);
        });

        it('removes connection CSS classes', () => {
            const { window, document, mockEventSource } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');
            mockEventSource.onopen();
            sse.disconnect();

            expect(document.body.classList.contains('dj-connected')).toBe(false);
            expect(document.body.classList.contains('dj-disconnected')).toBe(false);
        });
    });

    describe('sendEvent', () => {
        it('sends event via HTTP POST', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');
            sse.viewMounted = true;

            const result = sse.sendEvent('increment', { amount: 1 });

            expect(result).toBe(true);
            expect(window.fetch).toHaveBeenCalledTimes(1);
            const [url, opts] = window.fetch.mock.calls[0];
            expect(url).toContain('/djust/sse/');
            expect(url.endsWith('/event/')).toBe(true);
            expect(opts.method).toBe('POST');
            expect(opts.headers['Content-Type']).toBe('application/json');
            const body = JSON.parse(opts.body);
            expect(body.event).toBe('increment');
            expect(body.params.amount).toBe(1);
        });

        it('returns false when not mounted', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');
            // viewMounted is false by default

            const result = sse.sendEvent('increment');

            expect(result).toBe(false);
            expect(window.fetch).not.toHaveBeenCalled();
        });

        it('returns false when disabled', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.enabled = false;

            const result = sse.sendEvent('increment');

            expect(result).toBe(false);
        });

        it('tracks sent stats', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');
            sse.viewMounted = true;

            sse.sendEvent('increment', {});
            sse.sendEvent('decrement', {});

            expect(sse.stats.sent).toBe(2);
            expect(sse.stats.sentBytes).toBeGreaterThan(0);
        });
    });

    describe('handleMessage', () => {
        it('handles mount message and sets viewMounted', async () => {
            const { window } = createEnv('<div dj-root></div>');
            const sse = new window.djust.LiveViewSSE();

            await sse.handleMessage({
                type: 'mount',
                view: 'myapp.views.HomeView',
                html: '<p>Hello</p>',
            });

            expect(sse.viewMounted).toBe(true);
        });

        it('handles error message', async () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            const errors = [];
            window.addEventListener('djust:error', (e) => errors.push(e.detail));

            await sse.handleMessage({ type: 'error', error: 'Something broke' });

            expect(errors).toHaveLength(1);
            expect(errors[0].error).toBe('Something broke');
        });

        it('handles reload message without throwing', async () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();

            // reload triggers navigation which JSDOM doesn't support,
            // but the handler should not throw
            await expect(sse.handleMessage({ type: 'reload' })).resolves.not.toThrow();
        });

        it('tracks received stats', () => {
            const { window, mockEventSource } = createEnv('<div dj-root></div>');
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');

            // Simulate incoming message via onmessage
            const msgData = JSON.stringify({ type: 'noop' });
            mockEventSource.onmessage({ data: msgData });

            expect(sse.stats.received).toBe(1);
            expect(sse.stats.receivedBytes).toBe(msgData.length);
        });
    });

    describe('session ID generation', () => {
        it('generates UUID-formatted session ID', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');

            // UUID format: 8-4-4-4-12 hex chars
            expect(sse.sessionId).toMatch(
                /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
            );
        });
    });

    describe('startHeartbeat', () => {
        it('is a no-op (SSE uses server-side keepalives)', () => {
            const { window } = createEnv();
            const sse = new window.djust.LiveViewSSE();

            // Should not throw
            expect(() => sse.startHeartbeat()).not.toThrow();
        });
    });

    describe('connection error handling', () => {
        it('sets dj-disconnected on permanent close', () => {
            const { window, document, mockEventSource } = createEnv();
            const sse = new window.djust.LiveViewSSE();
            sse.connect('myapp.views.HomeView');
            mockEventSource.onopen();

            // Simulate permanent close
            mockEventSource.readyState = window.EventSource.CLOSED;
            mockEventSource.onerror(new Event('error'));

            expect(sse.enabled).toBe(false);
            expect(document.body.classList.contains('dj-disconnected')).toBe(true);
            expect(document.body.classList.contains('dj-connected')).toBe(false);
        });
    });
});
