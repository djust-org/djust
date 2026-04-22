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

    // ---------------------------------------------------------------------
    // Issue #816 — CSV edge cases (empty / whitespace / double / trailing)
    // ---------------------------------------------------------------------

    it('empty dj-ignore-attrs string does not flag any attribute', () => {
        const doc = dom.window.document;
        const el = doc.createElement('div');
        el.setAttribute('dj-ignore-attrs', '');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(false);
        expect(dom.window.djust.isIgnoredAttr(el, '')).toBe(false);
    });

    it('whitespace-only dj-ignore-attrs string does not flag any attribute', () => {
        const doc = dom.window.document;
        const el = doc.createElement('div');
        el.setAttribute('dj-ignore-attrs', '   ');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(false);
        expect(dom.window.djust.isIgnoredAttr(el, 'class')).toBe(false);
    });

    it('double comma (empty middle entry) is tolerated', () => {
        const doc = dom.window.document;
        const el = doc.createElement('div');
        el.setAttribute('dj-ignore-attrs', 'open,,close');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(true);
        expect(dom.window.djust.isIgnoredAttr(el, 'close')).toBe(true);
        // The empty middle entry must not match an empty attribute name.
        expect(dom.window.djust.isIgnoredAttr(el, '')).toBe(false);
    });

    it('trailing comma is tolerated', () => {
        const doc = dom.window.document;
        const el = doc.createElement('div');
        el.setAttribute('dj-ignore-attrs', 'open,');
        expect(dom.window.djust.isIgnoredAttr(el, 'open')).toBe(true);
        expect(dom.window.djust.isIgnoredAttr(el, '')).toBe(false);
    });

    // ---------------------------------------------------------------------
    // Issue #815 — Morph-path attribute sync/removal honors dj-ignore-attrs
    // ---------------------------------------------------------------------

    it('morph path: removal loop preserves dj-ignore-attrs attribute (#815)', () => {
        // Build an existing element that has `open` set (client-owned) and
        // a `desired` clone with `open` NOT set. Without the morph-path
        // guard, the remove loop would strip `open`. With the guard, `open`
        // stays because it's listed in dj-ignore-attrs. Exercises the
        // equivalent of morphElement's remove loop at 12-vdom-patch.js:746.
        const doc = dom.window.document;
        const existing = doc.createElement('dialog');
        existing.setAttribute('dj-ignore-attrs', 'open');
        existing.setAttribute('open', '');
        existing.setAttribute('class', 'keep-me');

        const desired = doc.createElement('dialog');
        desired.setAttribute('dj-ignore-attrs', 'open');
        desired.setAttribute('class', 'keep-me');
        // desired does NOT have `open` set.

        const isIgnored = dom.window.djust.isIgnoredAttr;
        for (let i = existing.attributes.length - 1; i >= 0; i--) {
            const name = existing.attributes[i].name;
            if (!desired.hasAttribute(name)) {
                if (isIgnored(existing, name)) continue;
                existing.removeAttribute(name);
            }
        }
        // open must remain because it's ignored — without the guard it
        // would have been stripped by the remove loop.
        expect(existing.hasAttribute('open')).toBe(true);
    });

    it('morph path: set loop preserves dj-ignore-attrs attribute value (#815)', () => {
        // Existing has open=""; desired has open="true". Without the guard,
        // the set loop would overwrite. With the guard, existing keeps "".
        // Exercises the equivalent of morphElement's set loop at
        // 12-vdom-patch.js:754.
        const doc = dom.window.document;
        const existing = doc.createElement('dialog');
        existing.setAttribute('dj-ignore-attrs', 'open');
        existing.setAttribute('open', '');

        const desired = doc.createElement('dialog');
        desired.setAttribute('dj-ignore-attrs', 'open');
        desired.setAttribute('open', 'true');  // server wants a different value

        const isIgnored = dom.window.djust.isIgnoredAttr;
        for (const attr of desired.attributes) {
            if (isIgnored(existing, attr.name)) continue;
            if (existing.getAttribute(attr.name) !== attr.value) {
                existing.setAttribute(attr.name, attr.value);
            }
        }
        // existing keeps the client-owned "" value.
        expect(existing.getAttribute('open')).toBe('');
    });
});
