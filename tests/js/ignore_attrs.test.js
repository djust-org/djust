/**
 * Tests for dj-ignore-attrs — client-owned attributes skipped by
 * VDOM SetAttr patches (Phoenix 1.1 JS.ignore_attributes parity).
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
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

describe('dj-ignore-attrs', () => {
    let dom;
    beforeEach(() => {
        dom = createDom('<dialog id="d" dj-ignore-attrs="open">hi</dialog>');
    });

    it('exposes window.djust.isIgnoredAttr after init', () => {
        expect(typeof dom.window.djust.isIgnoredAttr).toBe('function');
    });

    it('returns false when the element has no dj-ignore-attrs', () => {
        const doc = dom.window.document;
        const el = doc.createElement('div');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(false);
    });

    it('returns true when the attribute is listed', () => {
        const doc = dom.window.document;
        const el = doc.getElementById('d');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(true);
    });

    it('returns false when the attribute is NOT listed', () => {
        const doc = dom.window.document;
        const el = doc.getElementById('d');
        expect(dom.window.djust.isIgnoredAttr(el, 'class')).toBe(false);
    });

    it('tolerates whitespace and multiple keys in the CSV', () => {
        const doc = dom.window.document;
        const el = doc.createElement('div');
        el.setAttribute('dj-ignore-attrs', ' open , data-x ,aria-expanded ');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(true);
        expect(dom.window.djust.isIgnoredAttr(el, 'data-x')).toBe(true);
        expect(dom.window.djust.isIgnoredAttr(el, 'aria-expanded')).toBe(true);
        expect(dom.window.djust.isIgnoredAttr(el, 'foo')).toBe(false);
    });

    it('safely handles null and non-element arguments', () => {
        expect(dom.window.djust.isIgnoredAttr(null, 'open')).toBe(false);
        expect(dom.window.djust.isIgnoredAttr(undefined, 'open')).toBe(false);
        expect(dom.window.djust.isIgnoredAttr({}, 'open')).toBe(false);
    });

    it('integration: applySinglePatch skips SetAttr on ignored key', () => {
        const doc = dom.window.document;
        const el = doc.getElementById('d');
        // Polyfill CSS.escape for JSDOM test env (used by getNodeByPath internals).
        if (!dom.window.CSS) {
            dom.window.CSS = { escape: (s) => String(s).replace(/"/g, '\\"') };
        }
        el.setAttribute('dj-id', 'dialog1');
        el.removeAttribute('open');

        // Simulate a SetAttr patch that would normally set open=""
        const patch = { type: 'SetAttr', path: [], d: 'dialog1', key: 'open', value: '' };
        const result = dom.window.djust._applySinglePatch(patch);
        // Patch returns true (not an error), but attribute was NOT set.
        expect(result).toBe(true);
        expect(el.hasAttribute('open')).toBe(false);

        // Sanity check: non-ignored key DOES get set.
        const patch2 = { type: 'SetAttr', path: [], d: 'dialog1', key: 'title', value: 'x' };
        dom.window.djust._applySinglePatch(patch2);
        expect(el.getAttribute('title')).toBe('x');
    });
});
