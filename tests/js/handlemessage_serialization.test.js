/**
 * Regression tests for #1098: handleMessage interleaving across await boundaries.
 *
 * After PR-A (#1099) made `LiveViewWebSocket.handleMessage` and
 * `LiveViewSSE.handleMessage` async, two adjacent inbound frames could
 * fire-and-forget `_handleMessageImpl` concurrently and interleave their
 * `await handleServerResponse` calls, racing on shared state like
 * `_pendingEventRefs` / `_tickBuffer`.
 *
 * The fix wraps each public `handleMessage(data)` invocation onto a
 * per-instance `_inflight` Promise chain. These tests verify rapid-fire
 * frames drain sequentially regardless of how their async work
 * interleaves at the microtask level.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

function createDom() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-liveview-root dj-view="test.View"></div>' +
            '</body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Mock WebSocket so the LiveViewWebSocket constructor can run
    const sentMessages = [];
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            this._sent = sentMessages;
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send(data) {
            this._sent.push(JSON.parse(data));
        }
        close() {}
    };
    dom.window.WebSocket.OPEN = 1;
    dom.window.WebSocket.CONNECTING = 0;
    dom.window.WebSocket.CLOSING = 2;
    dom.window.WebSocket.CLOSED = 3;

    // Suppress noise but keep error visibility for assertions
    const origConsole = dom.window.console;
    dom.window.console = {
        log: () => {},
        error: origConsole.error.bind(origConsole),
        warn: () => {},
        debug: () => {},
        info: () => {},
    };

    dom.window.eval(clientCode);
    return dom;
}

describe('handleMessage serialization (#1098)', () => {
    describe('LiveViewWebSocket', () => {
        let dom;
        let ws;

        beforeEach(async () => {
            dom = createDom();
            await new Promise((r) => setTimeout(r, 10));
            ws = dom.window.djust.liveViewInstance;
            expect(ws).toBeDefined();
        });

        it('exposes handleMessage and _handleMessageImpl separately', () => {
            expect(typeof ws.handleMessage).toBe('function');
            expect(typeof ws._handleMessageImpl).toBe('function');
            // The public entry point is NOT the impl
            expect(ws.handleMessage).not.toBe(ws._handleMessageImpl);
        });

        it('drains rapid-fire messages in submission order', async () => {
            const events = [];
            const original = ws._handleMessageImpl.bind(ws);
            ws._handleMessageImpl = async function (data) {
                events.push({ phase: 'start', id: data.id });
                // Simulate async work that varies in duration —
                // earlier messages take LONGER than later ones, so a
                // racing implementation would finish out of order.
                await new Promise((r) => setTimeout(r, data.delay));
                events.push({ phase: 'end', id: data.id });
                return original(data);
            };

            const p1 = ws.handleMessage({ type: 'noop', id: 1, delay: 30 });
            const p2 = ws.handleMessage({ type: 'noop', id: 2, delay: 5 });
            const p3 = ws.handleMessage({ type: 'noop', id: 3, delay: 1 });

            await Promise.all([p1, p2, p3]);

            // Sequential drain: start1, end1, start2, end2, start3, end3.
            // A racing implementation would produce something like
            // start1, start2, start3, end3, end2, end1.
            expect(events).toEqual([
                { phase: 'start', id: 1 },
                { phase: 'end', id: 1 },
                { phase: 'start', id: 2 },
                { phase: 'end', id: 2 },
                { phase: 'start', id: 3 },
                { phase: 'end', id: 3 },
            ]);
        });

        it('continues draining even when one message rejects', async () => {
            const events = [];
            const original = ws._handleMessageImpl.bind(ws);
            ws._handleMessageImpl = async function (data) {
                events.push({ phase: 'start', id: data.id });
                if (data.shouldThrow) {
                    throw new Error(`boom-${data.id}`);
                }
                events.push({ phase: 'end', id: data.id });
                return original(data);
            };

            const p1 = ws.handleMessage({ type: 'noop', id: 1 });
            const p2 = ws.handleMessage({ type: 'noop', id: 2, shouldThrow: true });
            const p3 = ws.handleMessage({ type: 'noop', id: 3 });

            await Promise.all([p1, p2, p3]);

            // Frame 2 throws, but frame 3 still runs (the wrapper's
            // .catch swallows the rejection so the chain continues).
            expect(events.map((e) => `${e.phase}:${e.id}`)).toEqual([
                'start:1',
                'end:1',
                'start:2',
                'start:3',
                'end:3',
            ]);
        });

        it('returned promise resolves after this frame drains', async () => {
            let drained = false;
            const original = ws._handleMessageImpl.bind(ws);
            ws._handleMessageImpl = async function (data) {
                await new Promise((r) => setTimeout(r, 20));
                drained = true;
                return original(data);
            };

            const p = ws.handleMessage({ type: 'noop' });
            // Before await: drain has not completed
            expect(drained).toBe(false);
            await p;
            expect(drained).toBe(true);
        });
    });

    describe('LiveViewSSE', () => {
        let dom;
        let sse;

        beforeEach(() => {
            dom = createDom();
            const SSE = dom.window.djust.LiveViewSSE;
            expect(SSE).toBeDefined();
            sse = new SSE();
        });

        it('exposes handleMessage and _handleMessageImpl separately', () => {
            expect(typeof sse.handleMessage).toBe('function');
            expect(typeof sse._handleMessageImpl).toBe('function');
            expect(sse.handleMessage).not.toBe(sse._handleMessageImpl);
        });

        it('drains rapid-fire SSE messages in submission order', async () => {
            const events = [];
            const original = sse._handleMessageImpl.bind(sse);
            sse._handleMessageImpl = async function (data) {
                events.push({ phase: 'start', id: data.id });
                await new Promise((r) => setTimeout(r, data.delay));
                events.push({ phase: 'end', id: data.id });
                return original(data);
            };

            const p1 = sse.handleMessage({ type: 'noop', id: 1, delay: 20 });
            const p2 = sse.handleMessage({ type: 'noop', id: 2, delay: 1 });

            await Promise.all([p1, p2]);

            expect(events).toEqual([
                { phase: 'start', id: 1 },
                { phase: 'end', id: 1 },
                { phase: 'start', id: 2 },
                { phase: 'end', id: 2 },
            ]);
        });
    });
});
