/**
 * Unit tests for 03b-sse.js (SSE fallback transport)
 *
 * These tests cover the parts of LiveViewSSE that don't depend on the full
 * client runtime (global functions like handleServerResponse, bindLiveViewEvents,
 * etc.). Integration behaviour (mount, event dispatch, DOM updates) is covered
 * by Python test_sse.py for the server side.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ------------------------------------------------------------------ //
// Minimal global stubs required by the module
// ------------------------------------------------------------------ //

global.window = global.window || {};
global.window.djust = global.window.djust || {};

// Stubs for functions LiveViewSSE calls inside handleMessage
global.clientVdomVersion = 0;
global.setCacheConfig = vi.fn();
global._stampDjIds = vi.fn();
global.bindLiveViewEvents = vi.fn();
global.handleServerResponse = vi.fn();
global.globalLoadingManager = { stopLoading: vi.fn() };
global.dispatchPushEventToHooks = vi.fn();

// Minimal EventSource stub
class EventSourceStub {
    constructor(url) {
        this.url = url;
        this.readyState = EventSource.CONNECTING;
        EventSourceStub.lastInstance = this;
    }
    close() {
        this.readyState = EventSource.CLOSED;
    }
}
EventSourceStub.CONNECTING = 0;
EventSourceStub.OPEN = 1;
EventSourceStub.CLOSED = 2;
global.EventSource = EventSourceStub;

// ------------------------------------------------------------------ //
// Load the module under test
// ------------------------------------------------------------------ //

// We can't directly import a plain-JS class file that uses globals, so we
// eval it with the globals in scope. This mirrors how the browser loads it.
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(
    resolve(__dirname, '../../python/djust/static/djust/src/03b-sse.js'),
    'utf8'
);
eval(src); // eslint-disable-line no-eval

const { LiveViewSSE } = global.window.djust;

// ------------------------------------------------------------------ //
// Tests
// ------------------------------------------------------------------ //

describe('LiveViewSSE', () => {
    let sse;

    beforeEach(() => {
        sse = new LiveViewSSE();
        vi.clearAllMocks();
    });

    describe('constructor', () => {
        it('starts with enabled=true, viewMounted=false', () => {
            expect(sse.enabled).toBe(true);
            expect(sse.viewMounted).toBe(false);
        });

        it('initialises stats counters at zero', () => {
            expect(sse.stats.sent).toBe(0);
            expect(sse.stats.received).toBe(0);
        });
    });

    describe('_generateSessionId', () => {
        it('returns a UUID-shaped string', () => {
            const id = sse._generateSessionId();
            expect(id).toMatch(
                /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
            );
        });

        it('returns a unique ID on each call', () => {
            const ids = new Set(Array.from({ length: 10 }, () => sse._generateSessionId()));
            expect(ids.size).toBe(10);
        });
    });

    describe('sendEvent', () => {
        it('returns false when disabled', () => {
            sse.enabled = false;
            expect(sse.sendEvent('increment', {})).toBe(false);
        });

        it('returns false when viewMounted=false', () => {
            sse.enabled = true;
            sse.viewMounted = false;
            expect(sse.sendEvent('increment', {})).toBe(false);
        });

        it('returns true and posts when enabled and mounted', () => {
            sse.enabled = true;
            sse.viewMounted = true;
            sse.sseBaseUrl = '/djust/sse/test-id/';

            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({ ok: true }),
            });

            const result = sse.sendEvent('increment', { count: 1 });

            expect(result).toBe(true);
            expect(global.fetch).toHaveBeenCalledWith(
                '/djust/sse/test-id/event/',
                expect.objectContaining({
                    method: 'POST',
                    body: JSON.stringify({ event: 'increment', params: { count: 1 } }),
                })
            );
            expect(sse.lastEventName).toBe('increment');
            expect(sse.stats.sent).toBe(1);
        });
    });

    describe('startHeartbeat', () => {
        it('is a no-op (SSE uses server-side keepalives)', () => {
            expect(() => sse.startHeartbeat()).not.toThrow();
        });
    });

    describe('disconnect', () => {
        it('closes the EventSource and resets state', () => {
            sse.connect('myapp.views.TestView', {});
            sse.viewMounted = true;

            sse.disconnect();

            expect(sse.eventSource).toBeNull();
            expect(sse.viewMounted).toBe(false);
            expect(sse.sessionId).toBeNull();
        });

        it('is safe to call when not connected', () => {
            expect(() => sse.disconnect()).not.toThrow();
        });
    });

    describe('handleMessage', () => {
        it('sets viewMounted on mount message', () => {
            sse.handleMessage({
                type: 'mount',
                view: 'myapp.views.Test',
                version: 5,
            });
            expect(sse.viewMounted).toBe(true);
            expect(global.clientVdomVersion).toBe(5);
        });

        it('calls handleServerResponse on patch message', () => {
            sse.lastEventName = 'increment';
            sse.handleMessage({
                type: 'patch',
                patches: [],
                version: 2,
            });
            expect(global.handleServerResponse).toHaveBeenCalledWith(
                expect.objectContaining({ type: 'patch' }),
                'increment',
                null
            );
            expect(sse.lastEventName).toBeNull();
        });

        it('calls handleServerResponse on html_update message', () => {
            sse.handleMessage({
                type: 'html_update',
                html: '<div>hello</div>',
                version: 3,
            });
            expect(global.handleServerResponse).toHaveBeenCalled();
        });

        it('clears loading state on noop without async_pending', () => {
            sse.lastEventName = 'search';
            sse.handleMessage({ type: 'noop' });
            expect(global.globalLoadingManager.stopLoading).toHaveBeenCalledWith('search', null);
            expect(sse.lastEventName).toBeNull();
        });

        it('keeps loading state on noop with async_pending=true', () => {
            sse.lastEventName = 'generate';
            sse.handleMessage({ type: 'noop', async_pending: true });
            // stopLoading should NOT be called
            expect(global.globalLoadingManager.stopLoading).not.toHaveBeenCalled();
        });

        it('dispatches djust:error event on error message', () => {
            const listener = vi.fn();
            global.window.addEventListener('djust:error', listener);

            sse.handleMessage({ type: 'error', error: 'Something broke' });

            expect(listener).toHaveBeenCalled();
            global.window.removeEventListener('djust:error', listener);
        });

        it('reloads page on reload message', () => {
            const reloadMock = vi.fn();
            Object.defineProperty(global.window, 'location', {
                value: { reload: reloadMock, href: '' },
                writable: true,
                configurable: true,
            });

            sse.handleMessage({ type: 'reload' });

            expect(reloadMock).toHaveBeenCalled();
        });
    });
});
