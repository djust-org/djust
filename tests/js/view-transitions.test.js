/**
 * Tests for the View Transitions API integration in `applyPatches`
 * (12-vdom-patch.js).
 *
 * Opt-in semantics — the patch loop is wrapped in
 * `document.startViewTransition()` only when:
 *   1. The browser supports `document.startViewTransition` (Chrome 111+,
 *      Edge 111+, Safari 18+; Firefox in active development).
 *   2. The body element carries `dj-view-transitions`.
 *
 * Both conditions matter: per-WS-update animation would be jarring on
 * rapid updates (keystrokes, streaming markdown, cursor presence), so we
 * never enable it implicitly.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom({ bodyAttrs = '', startViewTransition = null } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html>
<html><head></head>
<body ${bodyAttrs}>
  <div dj-view="test.views.TestView" dj-root id="root">
    <span id="text">before</span>
  </div>
</body>
</html>`,
        { runScripts: 'dangerously' }
    );

    // Stub MockWebSocket so the bundle's connect() doesn't reach the network.
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
            this.send = vi.fn();
            this.close = vi.fn();
        }
    }
    dom.window.WebSocket = MockWebSocket;

    // Inject the View Transitions stub (or omit to simulate an
    // unsupported browser).
    if (startViewTransition) {
        dom.window.document.startViewTransition = startViewTransition;
    }

    // Evaluate the bundled client.js inside the JSDOM realm.
    dom.window.eval(clientCode);

    return { dom, doc: dom.window.document };
}

function deliverPatch(dom, patches) {
    const ws = new dom.window.djust.LiveViewWebSocket();
    ws.connect('ws://localhost/ws/live/');
    ws.ws.onopen({ type: 'open' });
    ws.handleMessage({ type: 'connect', session_id: 'test-session' });
    ws.skipMountHtml = true;
    ws.handleMessage({
        type: 'mount',
        view: 'test.views.TestView',
        html: '<div id="root"><span id="text">before</span></div>',
        version: 1,
    });
    // Apply a SetText patch on the <span> to flip "before" -> "after".
    ws.handleMessage({
        type: 'patch',
        patches: patches,
        version: 2,
    });
}

describe('View Transitions API integration in applyPatches', () => {
    let stub;

    beforeEach(() => {
        stub = vi.fn((cb) => {
            // Spec: callback runs synchronously; animation is deferred.
            cb();
            // Return a stand-in for the ViewTransition object.
            return {
                ready: Promise.resolve(),
                finished: Promise.resolve(),
                updateCallbackDone: Promise.resolve(),
                skipTransition: () => {},
            };
        });
    });

    it('wraps applyPatches in startViewTransition when body has dj-view-transitions AND API is supported', () => {
        const { dom, doc } = createDom({
            bodyAttrs: 'dj-view-transitions',
            startViewTransition: stub,
        });

        // Deliver a SetText patch; applyPatches should run inside
        // startViewTransition's callback.
        deliverPatch(dom, [
            { type: 'SetText', path: [0, 0], text: 'after' },
        ]);

        expect(stub).toHaveBeenCalledTimes(1);
        // The callback was invoked synchronously, so the DOM is already mutated.
        expect(doc.getElementById('text').textContent).toBe('after');
    });

    it('does NOT wrap when body lacks dj-view-transitions, even if API is supported', () => {
        const { dom, doc } = createDom({
            bodyAttrs: '',
            startViewTransition: stub,
        });

        deliverPatch(dom, [
            { type: 'SetText', path: [0, 0], text: 'after' },
        ]);

        expect(stub).not.toHaveBeenCalled();
        expect(doc.getElementById('text').textContent).toBe('after');
    });

    it('does NOT wrap when API is unsupported, even if body has the opt-in attribute', () => {
        const { dom, doc } = createDom({
            bodyAttrs: 'dj-view-transitions',
            startViewTransition: null, // unsupported
        });

        // Without `startViewTransition`, the patch falls through to the
        // direct apply path. Should NOT throw.
        expect(() => {
            deliverPatch(dom, [
                { type: 'SetText', path: [0, 0], text: 'after' },
            ]);
        }).not.toThrow();
        expect(doc.getElementById('text').textContent).toBe('after');
    });

    it('still applies patches successfully when wrapped', () => {
        // Sanity: the wrapper must not break the inner patch loop's success
        // contract. Verifies the wrap fires AND the DOM is mutated correctly
        // inside the callback.
        const { dom, doc } = createDom({
            bodyAttrs: 'dj-view-transitions',
            startViewTransition: stub,
        });

        deliverPatch(dom, [
            { type: 'SetText', path: [0, 0], text: 'first' },
        ]);

        expect(stub).toHaveBeenCalledTimes(1);
        // Verify the callback was invoked with a function that, when run,
        // applies the patch.
        const callbackArg = stub.mock.calls[0][0];
        expect(typeof callbackArg).toBe('function');
        // The DOM mutation already happened (callback ran synchronously
        // per spec, and per the stub's eager invocation).
        expect(doc.getElementById('text').textContent).toBe('first');
    });

    it('opting in dynamically (post-mount) is honored on subsequent patches', () => {
        // Caller may toggle the opt-in at runtime (e.g. theme switch).
        // The check happens at every applyPatches call — no startup latch.
        const { dom, doc } = createDom({
            bodyAttrs: '', // start without opt-in
            startViewTransition: stub,
        });

        // First patch: not wrapped (opt-in absent).
        deliverPatch(dom, [
            { type: 'SetText', path: [0, 0], text: 'mid' },
        ]);
        expect(stub).not.toHaveBeenCalled();
        expect(doc.getElementById('text').textContent).toBe('mid');

        // Toggle the opt-in attribute at runtime.
        doc.body.setAttribute('dj-view-transitions', '');

        // Second patch path: send another patch via the same WS.
        // (Setup helper does both mount+patch in one call, so reuse manually.)
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });
        ws.handleMessage({ type: 'connect', session_id: 'test-session' });
        ws.skipMountHtml = true;
        ws.handleMessage({
            type: 'mount',
            view: 'test.views.TestView',
            html: '<div id="root"><span id="text">mid</span></div>',
            version: 1,
        });
        ws.handleMessage({
            type: 'patch',
            patches: [{ type: 'SetText', path: [0, 0], text: 'late' }],
            version: 2,
        });

        expect(stub).toHaveBeenCalledTimes(1);
        expect(doc.getElementById('text').textContent).toBe('late');
    });
});
