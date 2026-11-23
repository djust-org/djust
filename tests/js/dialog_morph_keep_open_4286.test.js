/**
 * #4286: a full-page morph (html_update) must not close an open native
 * <dialog dj-dialog="open">. showModal() puts `open` on the element and the
 * server never renders it, so the morph's attribute sync used to strip it.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div id="root" dj-view="test.views.TestView" dj-root>${bodyHtml}</div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    class MockWebSocket {
        constructor() { this.readyState = 1; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;

    // jsdom has no real showModal/close: reflect them onto the attribute.
    const ProtoDialog = dom.window.HTMLDialogElement.prototype;
    ProtoDialog.showModal = function () { this.setAttribute('open', ''); };
    ProtoDialog.close = function () { this.removeAttribute('open'); };
    Object.defineProperty(ProtoDialog, 'open', {
        configurable: true,
        get() { return this.hasAttribute('open'); },
        set(v) { v ? this.setAttribute('open', '') : this.removeAttribute('open'); },
    });

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function flush(dom) {
    return new Promise((resolve) => dom.window.setTimeout(resolve, 0));
}

function desiredRoot(dom, html) {
    const div = dom.window.document.createElement('div');
    div.innerHTML = html;
    return div;
}

describe('html_update morph and an open native dialog (#4286)', () => {
    it('keeps `open` on a dj-dialog="open" dialog, on the same element', async () => {
        const dom = createDom('<dialog id="m" dj-dialog="open"><p id="count">0</p></dialog>');
        const doc = dom.window.document;
        const dialog = doc.getElementById('m');
        await flush(dom);
        expect(dialog.hasAttribute('open')).toBe(true);

        // The server's HTML never carries `open`.
        dom.window.djust.morphChildren(
            doc.getElementById('root'),
            desiredRoot(dom, '<dialog id="m" dj-dialog="open"><p id="count">2</p></dialog>'),
        );
        await flush(dom);

        expect(doc.getElementById('m')).toBe(dialog);
        expect(dialog.hasAttribute('open')).toBe(true);
        expect(doc.getElementById('count').textContent).toBe('2');
    });

    it('closes the dialog when the server says dj-dialog="close"', async () => {
        const dom = createDom('<dialog id="m" dj-dialog="open">x</dialog>');
        const doc = dom.window.document;
        const dialog = doc.getElementById('m');
        await flush(dom);
        expect(dialog.hasAttribute('open')).toBe(true);

        dom.window.djust.morphChildren(
            doc.getElementById('root'),
            desiredRoot(dom, '<dialog id="m" dj-dialog="close">x</dialog>'),
        );
        await flush(dom);

        expect(doc.getElementById('m')).toBe(dialog);
        expect(dialog.hasAttribute('open')).toBe(false);
    });

    it('still removes `open` from a dialog without dj-dialog', async () => {
        const dom = createDom('<dialog id="m" open>x</dialog>');
        const doc = dom.window.document;
        const dialog = doc.getElementById('m');

        dom.window.djust.morphChildren(
            doc.getElementById('root'),
            desiredRoot(dom, '<dialog id="m">x</dialog>'),
        );
        await flush(dom);

        expect(doc.getElementById('m')).toBe(dialog);
        expect(dialog.hasAttribute('open')).toBe(false);
    });

    it('removes `open` when the desired dialog has no dj-dialog at all', async () => {
        const dom = createDom('<dialog id="m" dj-dialog="open">x</dialog>');
        const doc = dom.window.document;
        const dialog = doc.getElementById('m');
        await flush(dom);
        expect(dialog.hasAttribute('open')).toBe(true);

        dom.window.djust.morphChildren(
            doc.getElementById('root'),
            desiredRoot(dom, '<dialog id="m">x</dialog>'),
        );
        await flush(dom);

        expect(dialog.hasAttribute('open')).toBe(false);
    });

    it('keeps `open` through a real html_update frame', async () => {
        const dom = createDom('<dialog id="m" dj-dialog="open"><p id="count">0</p></dialog>');
        const doc = dom.window.document;
        const dialog = doc.getElementById('m');
        await flush(dom);
        expect(dialog.hasAttribute('open')).toBe(true);

        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.enabled = true;
        ws.ws = new dom.window.WebSocket();
        ws.viewMounted = true;
        await ws.handleMessage({
            type: 'html_update',
            version: 2,
            event_name: 'typed',
            html:
                '<div dj-view="test.views.TestView" dj-root>' +
                '<dialog id="m" dj-dialog="open"><p id="count">2</p></dialog></div>',
        });
        await flush(dom);

        expect(doc.getElementById('m')).toBe(dialog);
        expect(dialog.hasAttribute('open')).toBe(true);
        expect(doc.getElementById('count').textContent).toBe('2');
    });
});
