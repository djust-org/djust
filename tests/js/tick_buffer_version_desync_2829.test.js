/**
 * Regression: a server-initiated (tick/async) frame arriving while user events
 * are in flight must not desync the VDOM version (#2829).
 *
 * The client buffers a server-initiated frame while events are pending
 * (`03-websocket.js`) and `break`s before `handleServerResponse`, so the
 * frame's `version` is never applied to `clientVdomVersion`. The NEXT event
 * response therefore fails the strict `clientVdomVersion !== data.version - 1`
 * check (`02-response-handler.js`), which logs "VDOM version mismatch!
 * Expected vN, got vN+2" and sends `{type:'request_html'}` — the full-HTML
 * recovery storm. #1677 fixed this for the `push_to_view` self-broadcast path
 * only; the tick and async paths were never covered.
 *
 * The existing test in `event_sequencing.test.js` ("buffered tick patches are
 * flushed after event response") asserts only that the buffer DRAINED — and the
 * flush it asserts is exactly what fires the mismatch, so it stays green while
 * the bug executes. This test asserts the consequence instead.
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
        dom.window.CSS.escape = function (value) {
            return String(value).replace(/([^\w-])/g, '\\$1');
        };
    }

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

/** A mounted LiveViewWebSocket with clientVdomVersion initialized to 1. */
async function makeWS(dom) {
    const ws = new dom.window.djust.LiveViewWebSocket();
    ws.enabled = true;
    ws.ws = new dom.window.WebSocket();
    ws.viewMounted = true;

    await ws.handleMessage({
        type: 'mount',
        view: 'test.views.TestView',
        version: 1,
        html: '<div dj-root><span id="counter">0</span></div>',
    });

    return ws;
}

function requestHtmlCount(sentMessages) {
    return sentMessages.filter((m) => m.type === 'request_html').length;
}

describe('Tick/async patch version sequencing (#2829)', () => {

    it('a tick arriving between two event responses does not force a full-HTML recovery', async () => {
        const { dom, sentMessages, getSeqState } = createDom();
        const ws = await makeWS(dom);

        // Two events in flight — the window in which a server-initiated frame
        // gets buffered (one pending ref is enough; two is the reported shape).
        ws.sendEvent('click', { n: 1 });
        const refA = getSeqState().pendingEventRef;
        ws.sendEvent('click', { n: 2 });

        // Event A's response: v2. Applied, B still pending.
        await ws.handleMessage({ type: 'patch', patches: [], version: 2, source: 'event', ref: refA });

        // The tick's frame: v3. Server-initiated, arrives while B is pending,
        // so the client buffers it and never applies its version.
        await ws.handleMessage({ type: 'patch', patches: [], version: 3, source: 'tick' });

        // Event B's response: v4. The server's counter is monotonic, so v4 is
        // correct — the client's view is the thing that is stale.
        await ws.handleMessage({ type: 'patch', patches: [], version: 4, source: 'event', ref: refA + 1 });

        // THE SYMPTOM: a spurious full-HTML recovery for a sequence that is
        // entirely valid on the wire.
        expect(
            requestHtmlCount(sentMessages),
            `expected no request_html recovery, got sentMessages=${JSON.stringify(sentMessages)}`,
        ).toBe(0);

        // ...and the buffered frame must still have been APPLIED, not merely
        // dropped — a "fix" that discards it would satisfy the line above.
        expect(getSeqState().tickBufferLength).toBe(0);
    });

    it('a plain event sequence with no tick stays recovery-free (control)', async () => {
        const { dom, sentMessages, getSeqState } = createDom();
        const ws = await makeWS(dom);

        ws.sendEvent('click', { n: 1 });
        const refA = getSeqState().pendingEventRef;
        await ws.handleMessage({ type: 'patch', patches: [], version: 2, source: 'event', ref: refA });

        expect(requestHtmlCount(sentMessages)).toBe(0);
    });

    // ── Guards on the fix itself ─────────────────────────────────────────
    // The fix must not have weakened the detection it routes around. Both
    // cases below fail if the `_deferred` branch is widened (e.g. by treating
    // every gap as explainable, or by honouring a wire-supplied flag).

    it('a GENUINELY dropped patch still forces recovery (detection preserved)', async () => {
        const { dom, sentMessages, getSeqState } = createDom();
        const ws = await makeWS(dom);

        ws.sendEvent('click', { n: 1 });
        const refA = getSeqState().pendingEventRef;
        await ws.handleMessage({ type: 'patch', patches: [], version: 2, source: 'event', ref: refA });

        // v3 is never delivered — lost in transit, not deferred by us. The gap
        // is real corruption and MUST still recover.
        await ws.handleMessage({ type: 'patch', patches: [], version: 4, source: 'event', ref: refA + 1 });

        expect(
            requestHtmlCount(sentMessages),
            'a dropped patch must still trigger request_html — the fix must not have disabled detection',
        ).toBe(1);
    });

    it('a wire-supplied _deferred flag cannot suppress detection', async () => {
        const { dom, sentMessages, getSeqState } = createDom();
        const ws = await makeWS(dom);

        ws.sendEvent('click', { n: 1 });
        const refA = getSeqState().pendingEventRef;
        await ws.handleMessage({ type: 'patch', patches: [], version: 2, source: 'event', ref: refA });

        // The strip is a shared helper called at each transport's inbound
        // entry (WebSocket, SSE, HTTP fallback), so by the time the frame
        // reaches the version check it is indistinguishable from a
        // wire-supplied one. The caller-set pin below keeps a future transport
        // from omitting it.
        await ws.handleMessage({
            type: 'patch', patches: [], version: 4, source: 'event', ref: refA + 1,
            _deferred: true, // hostile/buggy server trying to skip the check
        });

        expect(
            requestHtmlCount(sentMessages),
            'a server-supplied _deferred must be ignored — the flag is client-owned',
        ).toBe(1);
    });

    it('a patch dropped INSIDE the buffered window still forces recovery', async () => {
        // The window that matters: v2 is allocated by the server and LOST in
        // transit while a user event is pending, then v3 arrives and is
        // buffered. Consuming the buffered frame's version must not vouch for
        // the versions between it and the cursor — otherwise the gap is
        // accepted, nothing ever fails the strict check, and the dropped patch
        // is applied by no one: permanent silent DOM/server divergence.
        const { dom, sentMessages, getSeqState } = createDom();
        const ws = await makeWS(dom);

        ws.sendEvent('click', { n: 1 });
        const refA = getSeqState().pendingEventRef;

        // v2 never arrives.
        await ws.handleMessage({ type: 'patch', patches: [], version: 3, source: 'tick' });
        await ws.handleMessage({ type: 'patch', patches: [], version: 4, source: 'event', ref: refA });

        expect(
            requestHtmlCount(sentMessages),
            'a patch lost inside the buffered window must still force recovery',
        ).toBe(1);
    });

});

describe('client-owned frame flags are stripped on every inbound transport (#2829)', () => {

    it('each transport calls the shared strip (caller-set pin, derived not restated)', () => {
        // `_deferred` is a client-owned control flag: if a transport hands a
        // wire-supplied copy to handleServerResponse, the version check can be
        // suppressed on that path. The shared helper makes the behaviour
        // identical wherever it is called, so what needs pinning is the CALLER
        // SET — a future transport that omits the call re-opens the hole.
        //
        // Derived from the built bundle rather than restated as a magic number
        // (canon #2727), and asserted as exact equality so both an omission AND
        // an unexplained extra call site fail here.
        const definition = (clientCode.match(/function stripClientOwnedFrameFlags\(/g) || []).length;
        const calls = (clientCode.match(/stripClientOwnedFrameFlags\(data\);/g) || []).length;
        expect(definition, 'one definition').toBe(1);
        expect(
            calls,
            'exactly one call per inbound transport: websocket, sse, http fallback',
        ).toBe(3);
    });
});
