/**
 * Tests for event sequencing during ticks (#560).
 *
 * Verifies the client-side event ref tracking and tick buffering:
 * - sendEvent includes a monotonic ref in the message
 * - Tick-sourced patches are buffered during pending events
 * - Buffered tick patches are flushed after event response
 * - Loading states are managed correctly for events vs ticks
 * - Pending state is cleared on error and disconnect
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html>
<html>
<body>
  <div dj-view="test.views.TestView" dj-root>
    <span id="counter">0</span>
  </div>
</body>
</html>`, { runScripts: 'dangerously' });

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = function(value) {
            return String(value).replace(/([^\w-])/g, '\\$1');
        };
    }

    // Stub WebSocket with proper constants
    const sentMessages = [];
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
        send(data) {
            sentMessages.push(JSON.parse(data));
        }
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;

    dom.window.eval(clientCode);

    function getSeqState() {
        return dom.window.djust._getEventSeqState();
    }

    return { dom, sentMessages, getSeqState, MockWebSocket };
}

/**
 * Create a mounted LiveViewWebSocket with VDOM version initialized to 1.
 * Uses the mount message to initialize clientVdomVersion in the correct scope.
 */
function makeWS(dom) {
    const ws = new dom.window.djust.LiveViewWebSocket();
    ws.enabled = true;
    ws.ws = new dom.window.WebSocket();
    ws.viewMounted = true;

    // Initialize VDOM version via mount (sets let-scoped clientVdomVersion)
    ws.handleMessage({
        type: 'mount',
        view: 'test.views.TestView',
        version: 1,
        html: '<div dj-root><span id="counter">0</span></div>',
    });

    return ws;
}

describe('Event sequencing (#560)', () => {

    describe('sendEvent ref tracking', () => {
        it('sendEvent includes a monotonic ref in the message', () => {
            const { dom, sentMessages } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('click', { id: 1 });
            ws.sendEvent('click', { id: 2 });

            const events = sentMessages.filter(m => m.type === 'event');
            expect(events).toHaveLength(2);
            expect(events[0].ref).toBeDefined();
            expect(events[1].ref).toBeDefined();
            expect(events[1].ref).toBeGreaterThan(events[0].ref);
        });

        it('sendEvent sets the event name and params correctly', () => {
            const { dom, sentMessages } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('search', { value: 'hello' });

            const events = sentMessages.filter(m => m.type === 'event');
            expect(events).toHaveLength(1);
            expect(events[0].event).toBe('search');
            expect(events[0].params.value).toBe('hello');
        });

        it('sendEvent sets pending event state', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('click', {});

            const state = getSeqState();
            expect(state.pendingEventRef).not.toBeNull();
            expect(state.pendingEventName).toBe('click');
        });
    });

    describe('tick buffering during pending events', () => {
        it('tick patches with source=tick are buffered when event is pending', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('click', {});

            // Simulate tick arriving while event is pending
            ws.handleMessage({
                type: 'patch',
                patches: [],
                version: 2,
                source: 'tick'
            });

            // Tick should be buffered
            expect(getSeqState().tickBufferLength).toBe(1);
        });

        it('event response is processed and clears pending state', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('click', {});
            const ref = getSeqState().pendingEventRef;

            // Event response with matching ref
            ws.handleMessage({
                type: 'patch',
                patches: [],
                version: 2,
                source: 'event',
                ref: ref
            });

            expect(getSeqState().pendingEventRef).toBeNull();
        });

        it('buffered tick patches are flushed after event response', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('click', {});
            const ref = getSeqState().pendingEventRef;

            // Tick arrives during pending event — gets buffered
            ws.handleMessage({
                type: 'patch',
                patches: [],
                version: 2,
                source: 'tick'
            });
            expect(getSeqState().tickBufferLength).toBe(1);

            // Event response arrives — should flush buffered tick
            ws.handleMessage({
                type: 'patch',
                patches: [],
                version: 3,
                source: 'event',
                ref: ref
            });

            expect(getSeqState().tickBufferLength).toBe(0);
        });

        it('tick patches without pending event are not buffered', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            // No pending event
            expect(getSeqState().pendingEventRef).toBeNull();

            ws.handleMessage({
                type: 'patch',
                patches: [],
                version: 2,
                source: 'tick'
            });

            // Should not buffer — applied immediately
            expect(getSeqState().tickBufferLength).toBe(0);
        });

        it('non-tick patches without ref are not buffered', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            // Patch without source or ref (e.g., broadcast)
            ws.handleMessage({
                type: 'patch',
                patches: [],
                version: 2,
            });

            expect(getSeqState().tickBufferLength).toBe(0);
        });
    });

    describe('noop response clears pending state', () => {
        it('noop with matching ref clears pending event', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('toggle', {});
            const ref = getSeqState().pendingEventRef;
            expect(ref).not.toBeNull();

            ws.handleMessage({ type: 'noop', ref: ref });

            expect(getSeqState().pendingEventRef).toBeNull();
        });

        it('noop flushes buffered tick patches', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('toggle', {});
            const ref = getSeqState().pendingEventRef;

            dom.window.djust._pushTickBuffer({
                type: 'patch', patches: [], version: 2, source: 'tick'
            });
            expect(getSeqState().tickBufferLength).toBe(1);

            ws.handleMessage({ type: 'noop', ref: ref });

            expect(getSeqState().tickBufferLength).toBe(0);
        });
    });

    describe('error clears pending state', () => {
        it('error response clears pending event ref and buffer', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('broken', {});
            dom.window.djust._pushTickBuffer({});

            ws.handleMessage({
                type: 'error',
                error: 'Something went wrong'
            });

            expect(getSeqState().pendingEventRef).toBeNull();
            expect(getSeqState().tickBufferLength).toBe(0);
        });
    });

    describe('disconnect clears pending state', () => {
        it('WebSocket close clears pending event ref and buffer', () => {
            const { dom, getSeqState } = createDom();
            const ws = makeWS(dom);

            ws.sendEvent('test', {});
            dom.window.djust._pushTickBuffer({});

            expect(getSeqState().pendingEventRef).not.toBeNull();

            // Simulate disconnect by calling disconnect() which clears state
            ws._intentionalDisconnect = true;
            ws.disconnect();

            expect(getSeqState().pendingEventRef).toBeNull();
            expect(getSeqState().tickBufferLength).toBe(0);
        });
    });
});
