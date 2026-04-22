/**
 * Tests for dj-remove — declarative CSS exit transitions (v0.6.0, phase 2a).
 *
 * jsdom doesn't fire `transitionend` automatically, so we either
 * dispatch it manually or rely on the fallback timeout.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
            <div dj-view="test.V" dj-root>${bodyHtml}</div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {} close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function nextFrame(dom) {
    // requestAnimationFrame in jsdom is backed by setTimeout(_, 16) in
    // the client module's fallback path. Under vitest parallel load
    // the 16 ms can stretch; 60 ms is a generous margin that matches
    // the conventions used elsewhere in this suite.
    return new Promise((resolve) => dom.window.setTimeout(resolve, 60));
}

describe('dj-remove', () => {
    let dom;

    it('parseSpec extracts { start, active, end } from three tokens', () => {
        dom = createDom('');
        const spec = dom.window.djust.djRemove._parseRemoveSpec('opacity-100 transition-opacity opacity-0');
        expect(spec).toEqual({ start: 'opacity-100', active: 'transition-opacity', end: 'opacity-0' });
    });

    it('parseSpec returns { single } for one-token form', () => {
        dom = createDom('');
        const spec = dom.window.djust.djRemove._parseRemoveSpec('fade-out');
        expect(spec).toEqual({ single: 'fade-out' });
    });

    it('parseSpec returns null for empty or whitespace input', () => {
        dom = createDom('');
        expect(dom.window.djust.djRemove._parseRemoveSpec('')).toBeNull();
        expect(dom.window.djust.djRemove._parseRemoveSpec('   ')).toBeNull();
        expect(dom.window.djust.djRemove._parseRemoveSpec(null)).toBeNull();
        expect(dom.window.djust.djRemove._parseRemoveSpec(undefined)).toBeNull();
        // Two-token form is ambiguous — also null.
        expect(dom.window.djust.djRemove._parseRemoveSpec('a b')).toBeNull();
    });

    it('maybeDeferRemoval returns false for elements without dj-remove', () => {
        dom = createDom('<div id="t"></div>');
        const el = dom.window.document.getElementById('t');
        expect(dom.window.djust.maybeDeferRemoval(el)).toBe(false);
    });

    it('maybeDeferRemoval returns false for a detached element', () => {
        dom = createDom('');
        const el = dom.window.document.createElement('div');
        el.setAttribute('dj-remove', 'fade-out');
        // el has no parentNode
        expect(dom.window.djust.maybeDeferRemoval(el)).toBe(false);
    });

    it('maybeDeferRemoval defers physical removal (element stays mounted)', async () => {
        dom = createDom('<div id="t" dj-remove="fade-out"></div>');
        const el = dom.window.document.getElementById('t');
        const parent = el.parentNode;
        const deferred = dom.window.djust.maybeDeferRemoval(el);
        expect(deferred).toBe(true);
        // Right after the call, the element is still in the DOM.
        expect(el.parentNode).toBe(parent);
        // After the next frame, the single-token class is applied.
        await nextFrame(dom);
        expect(el.classList.contains('fade-out')).toBe(true);
        // Element is STILL mounted — we're waiting for the fallback timer.
        expect(el.parentNode).toBe(parent);
    });

    it('fallback timer (600ms default) finalizes the removal', async () => {
        dom = createDom('<div id="t" dj-remove="fade-out"></div>');
        const el = dom.window.document.getElementById('t');
        const parent = el.parentNode;
        dom.window.djust.maybeDeferRemoval(el);
        await nextFrame(dom);
        expect(el.parentNode).toBe(parent);
        // Wait past the 600ms fallback window.
        await new Promise((r) => setTimeout(r, 700));
        expect(el.parentNode).toBeNull();
    });

    it('dj-remove-duration overrides the fallback timer', async () => {
        dom = createDom('<div id="t" dj-remove="fade-out" dj-remove-duration="100"></div>');
        const el = dom.window.document.getElementById('t');
        const parent = el.parentNode;
        dom.window.djust.maybeDeferRemoval(el);
        await nextFrame(dom);
        expect(el.parentNode).toBe(parent);
        // Wait past the overridden 100ms window (but well before default 600ms).
        await new Promise((r) => setTimeout(r, 180));
        expect(el.parentNode).toBeNull();
    });

    it('VDOM RemoveChild patch defers removal for [dj-remove] children', async () => {
        dom = createDom('<ul id="list"><li id="a" dj-remove="fade-out">A</li></ul>');
        const list = dom.window.document.getElementById('list');
        const item = dom.window.document.getElementById('a');
        // Apply a RemoveChild patch targeting index 0 of the <ul>.
        // The ul is the first element child of the [dj-view] root, so
        // path=[0] resolves to the ul; the patch removes its child at
        // index 0 (the <li>). _applySinglePatch is the exposed hook.
        dom.window.djust._applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            index: 0,
        });
        // The RemoveChild handler should have called maybeDeferRemoval,
        // which returned true — the item should still be mounted.
        expect(item.parentNode).toBe(list);
        await nextFrame(dom);
        expect(item.classList.contains('fade-out')).toBe(true);
        // Still mounted before the fallback fires.
        expect(item.parentNode).toBe(list);
        // After the fallback, physically gone.
        await new Promise((r) => setTimeout(r, 700));
        expect(item.parentNode).toBeNull();
    });

    it('stripping dj-remove cancels the pending removal', async () => {
        dom = createDom('<div id="t" dj-remove="fade-out"></div>');
        const el = dom.window.document.getElementById('t');
        const parent = el.parentNode;
        dom.window.djust.maybeDeferRemoval(el);
        await nextFrame(dom);
        expect(el.classList.contains('fade-out')).toBe(true);
        expect(el.parentNode).toBe(parent);

        // Strip the attribute — pending removal should cancel.
        el.removeAttribute('dj-remove');
        // MutationObserver is microtask-scheduled; wait a tick.
        await new Promise((r) => setTimeout(r, 20));
        // The exit class was reverted…
        expect(el.classList.contains('fade-out')).toBe(false);
        // …and the element stays mounted past the fallback window.
        await new Promise((r) => setTimeout(r, 700));
        expect(el.parentNode).toBe(parent);
    });
});
