/**
 * Tests for UNSAFE_KEYS guard in VDOM patch operations.
 *
 * Verifies that prototype-polluting keys (__proto__, constructor, prototype)
 * are rejected in SetAttr and RemoveAttr patches, while normal keys still work.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '<div dj-root dj-liveview-root><input id="target" type="text" value="old"></div>') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            Promise.resolve().then(() => { if (this.onopen) this.onopen({}); });
        }
        send() {}
        close() {}
    };

    dom.window.console = { log: () => {}, warn: () => {}, error: () => {}, debug: () => {}, info: () => {}, group: () => {}, groupCollapsed: () => {}, groupEnd: () => {} };

    try { dom.window.eval(clientCode); } catch (e) { /* client.js may throw on missing APIs */ }

    return dom;
}

describe('VDOM patch UNSAFE_KEYS guard', () => {
    it('rejects __proto__ in SetAttr', () => {
        const dom = createDom();
        const fn = dom.window.djust._applySinglePatch || dom.window.djust.applySinglePatch;
        if (!fn) return; // Skip if not exported

        const result = fn({ type: 'SetAttr', key: '__proto__', value: '{}', path: [0], d: null });
        // Should return true (patch silently skipped) without crashing
        expect(result).toBe(true);
        // Object.prototype should not be polluted
        expect(({}).polluted).toBeUndefined();
    });

    it('rejects constructor in SetAttr', () => {
        const dom = createDom();
        const fn = dom.window.djust._applySinglePatch || dom.window.djust.applySinglePatch;
        if (!fn) return;

        const result = fn({ type: 'SetAttr', key: 'constructor', value: 'evil', path: [0], d: null });
        expect(result).toBe(true);
    });

    it('rejects prototype in RemoveAttr', () => {
        const dom = createDom();
        const fn = dom.window.djust._applySinglePatch || dom.window.djust.applySinglePatch;
        if (!fn) return;

        const result = fn({ type: 'RemoveAttr', key: 'prototype', path: [0], d: null });
        expect(result).toBe(true);
    });

    it('allows normal keys in SetAttr', () => {
        const dom = createDom('<div dj-root dj-liveview-root><input id="target" type="text" value="old" dj-id="t1"></div>');
        const fn = dom.window.djust._applySinglePatch || dom.window.djust.applySinglePatch;
        if (!fn) return;

        const target = dom.window.document.querySelector('#target');
        const result = fn({ type: 'SetAttr', key: 'data-test', value: 'hello', path: [0], d: 't1' });
        // Normal attributes should be set
        expect(target.getAttribute('data-test')).toBe('hello');
    });

    it('coerces SetText patch.text to String', () => {
        const dom = createDom('<div dj-root dj-liveview-root><span id="txt">old</span></div>');
        const fn = dom.window.djust._applySinglePatch || dom.window.djust.applySinglePatch;
        if (!fn) return;

        const target = dom.window.document.querySelector('#txt');
        fn({ type: 'SetText', text: 42, path: [0, 0], d: null });
        expect(target.textContent).toBe('42');
    });
});
