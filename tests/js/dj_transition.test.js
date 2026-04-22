/**
 * Tests for dj-transition — declarative CSS enter/leave transitions (v0.6.0).
 *
 * jsdom doesn't fire `transitionend` automatically, so we either
 * dispatch it manually or rely on the 600 ms fallback timeout (stubbed
 * via fake timers). The fake-timers approach keeps the test fast and
 * deterministic.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
    // the client module's fallback path. Flush via setTimeout(0).
    return new Promise((resolve) => dom.window.setTimeout(resolve, 30));
}

describe('dj-transition', () => {
    let dom;

    it('parseSpec extracts three tokens from the attribute value', () => {
        dom = createDom('');
        const spec = dom.window.djust.djTransition._parseSpec('opacity-0 transition opacity-100');
        expect(spec).toEqual({ start: 'opacity-0', active: 'transition', end: 'opacity-100' });
    });

    it('parseSpec returns null for specs with fewer than three tokens', () => {
        dom = createDom('');
        expect(dom.window.djust.djTransition._parseSpec('a b')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec('')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec(null)).toBeNull();
    });

    it('applies start class synchronously, active/end on next frame', async () => {
        dom = createDom('<div id="t" dj-transition="start-cls active-cls end-cls"></div>');
        const el = dom.window.document.getElementById('t');
        // The module runs on DOMContentLoaded — phase-1 is applied synchronously.
        // jsdom processes MutationObserver microtasks before returning; assert after
        // flushing the queue.
        await new Promise((r) => setTimeout(r, 0));
        expect(el.classList.contains('start-cls')).toBe(true);

        // After the next "frame" (fallback setTimeout 16ms), phase-2 and phase-3 land.
        await nextFrame(dom);
        expect(el.classList.contains('start-cls')).toBe(false);
        expect(el.classList.contains('active-cls')).toBe(true);
        expect(el.classList.contains('end-cls')).toBe(true);
    });

    it('transitionend removes the active class but keeps the end class', async () => {
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        await nextFrame(dom);

        // Simulate the browser firing transitionend on the element.
        el.dispatchEvent(new dom.window.Event('transitionend', { bubbles: true }));
        // Give the handler a tick to clean up.
        await new Promise((r) => setTimeout(r, 0));

        expect(el.classList.contains('a')).toBe(false);
        expect(el.classList.contains('e')).toBe(true);
    });

    it('fallback timeout removes the active class if transitionend never fires', async () => {
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        await nextFrame(dom);
        expect(el.classList.contains('a')).toBe(true);

        // Wait past the 600ms fallback window.
        await new Promise((r) => setTimeout(r, 650));
        expect(el.classList.contains('a')).toBe(false);
        expect(el.classList.contains('e')).toBe(true);
    });

    it('exposes _installDjTransitionFor on window.djust.djTransition', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.djTransition._installDjTransitionFor).toBe('function');
    });

    it('fallback timer on a removed element does not throw', async () => {
        // An element removed mid-transition should not crash the
        // fallback cleanup — classList.remove on a detached element is
        // a no-op, and the WeakMap entry is orphaned but harmless.
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        await nextFrame(dom);
        expect(el.classList.contains('a')).toBe(true);

        el.remove();
        // Wait past the fallback window — the setTimeout should fire
        // against the detached element without throwing.
        await new Promise((r) => setTimeout(r, 650));
        // No crash — the test just reaching here means the fallback
        // didn't error on the detached node.
        expect(el.classList.contains('a')).toBe(false);
    });

    it('re-runs the sequence when the attribute value changes', async () => {
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        await nextFrame(dom);
        el.dispatchEvent(new dom.window.Event('transitionend', { bubbles: true }));
        await new Promise((r) => setTimeout(r, 0));
        expect(el.classList.contains('a')).toBe(false);

        // Re-trigger by swapping the spec.
        el.setAttribute('dj-transition', 's2 a2 e2');
        await new Promise((r) => setTimeout(r, 0));
        expect(el.classList.contains('s2')).toBe(true);
        await nextFrame(dom);
        expect(el.classList.contains('a2')).toBe(true);
        expect(el.classList.contains('e2')).toBe(true);
    });
});
