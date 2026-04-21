/**
 * Tests for dj-dialog — v0.5.1 native <dialog> modal integration.
 *
 * Uses jsdom for DOM + MutationObserver. jsdom's HTMLDialogElement
 * stubs showModal/close — we replace them with spies where needed to
 * verify the right call was issued.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${bodyHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null; this.onclose = null; this.onmessage = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;

    // jsdom's HTMLDialogElement doesn't implement showModal/close with the
    // real "open" attribute side-effect — stub it so the observer can drive
    // a realistic state machine.
    const ProtoDialog = dom.window.HTMLDialogElement.prototype;
    ProtoDialog.showModal = function () {
        this.setAttribute('open', '');
        this._wasShownAsModal = true;
    };
    ProtoDialog.close = function () {
        this.removeAttribute('open');
    };
    Object.defineProperty(ProtoDialog, 'open', {
        configurable: true,
        get() { return this.hasAttribute('open'); },
        set(v) { v ? this.setAttribute('open', '') : this.removeAttribute('open'); },
    });

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function waitForMutation(dom) {
    // MutationObserver fires asynchronously on the microtask queue. Flush
    // with a Promise.resolve() chain so the observer callback runs.
    return new Promise((resolve) => dom.window.setTimeout(resolve, 0));
}

describe('dj-dialog', () => {
    let dom;

    it('opens a dialog via showModal when dj-dialog="open" is present on load', async () => {
        dom = createDom('<dialog id="d" dj-dialog="open">hi</dialog>');
        const el = dom.window.document.getElementById('d');
        expect(el.open).toBe(true);
        expect(el._wasShownAsModal).toBe(true);
    });

    it('keeps a dialog closed when dj-dialog="close"', async () => {
        dom = createDom('<dialog id="d" dj-dialog="close">hi</dialog>');
        const el = dom.window.document.getElementById('d');
        expect(el.open).toBe(false);
    });

    it('opens when the attribute flips from close → open', async () => {
        dom = createDom('<dialog id="d" dj-dialog="close">hi</dialog>');
        const el = dom.window.document.getElementById('d');
        expect(el.open).toBe(false);
        el.setAttribute('dj-dialog', 'open');
        await waitForMutation(dom);
        expect(el.open).toBe(true);
    });

    it('closes when the attribute flips from open → close', async () => {
        dom = createDom('<dialog id="d" dj-dialog="open">hi</dialog>');
        const el = dom.window.document.getElementById('d');
        expect(el.open).toBe(true);
        el.setAttribute('dj-dialog', 'close');
        await waitForMutation(dom);
        expect(el.open).toBe(false);
    });

    it('syncs a dialog injected later (VDOM morph simulation)', async () => {
        dom = createDom('<div id="host"></div>');
        const host = dom.window.document.getElementById('host');
        host.innerHTML = '<dialog id="d" dj-dialog="open">late</dialog>';
        await waitForMutation(dom);
        const el = dom.window.document.getElementById('d');
        expect(el.open).toBe(true);
    });

    it('ignores non-dialog elements with dj-dialog attribute', async () => {
        dom = createDom('<div id="d" dj-dialog="open">not a dialog</div>');
        const el = dom.window.document.getElementById('d');
        // Div doesn't have an open property mapped; and showModal shouldn't
        // have been called (it doesn't exist on div). The key test is that
        // no exception was thrown during the initial sync pass.
        expect(el.hasAttribute('open')).toBe(false);
    });

    it('is idempotent — repeated "open" values don\'t fire showModal twice', async () => {
        dom = createDom('<dialog id="d" dj-dialog="open">hi</dialog>');
        const el = dom.window.document.getElementById('d');
        let showCount = 0;
        const original = el.showModal.bind(el);
        el.showModal = function () { showCount += 1; return original(); };
        // Force mutation with the same value — observer fires, but the
        // sync function early-returns because el.open is already true.
        el.setAttribute('dj-dialog', 'open');
        await waitForMutation(dom);
        expect(showCount).toBe(0); // no re-open
    });

    it('exposes _syncDialogState on window.djust.djDialog', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.djDialog._syncDialogState).toBe('function');
    });
});
