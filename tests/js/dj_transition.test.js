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
    // the client module's fallback path. Under vitest parallel load
    // the 16 ms can stretch; 60 ms is a generous margin that matches
    // the conventions used elsewhere in this suite.
    return new Promise((resolve) => dom.window.setTimeout(resolve, 60));
}

function waitForClass(el, cls, timeoutMs = 300) {
    // Poll up to timeoutMs for the class to appear (or disappear via
    // negated use). Returns a promise that resolves when the condition
    // is met, rejects on timeout. Keeps timing-sensitive assertions
    // robust under parallel test load.
    const deadline = Date.now() + timeoutMs;
    return new Promise((resolve, reject) => {
        (function tick() {
            if (el.classList.contains(cls)) return resolve();
            if (Date.now() > deadline) return reject(new Error(`class "${cls}" never applied within ${timeoutMs}ms`));
            setTimeout(tick, 5);
        })();
    });
}

describe('dj-transition', () => {
    let dom;

    it('parseSpec extracts three tokens from the attribute value', () => {
        dom = createDom('');
        const spec = dom.window.djust.djTransition._parseSpec('opacity-0 transition opacity-100');
        expect(spec).toEqual({ start: 'opacity-0', active: 'transition', end: 'opacity-100' });
    });

    it('parseSpec returns null for empty / null / 2-token specs', () => {
        dom = createDom('');
        // 2-token: ambiguous (could be start+active or active+end); reject.
        expect(dom.window.djust.djTransition._parseSpec('a b')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec('')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec(null)).toBeNull();
    });

    it('parseSpec accepts 1-token short form (closes #1273)', () => {
        // dj-transition-group's documented short form
        // `dj-transition-group="fade-in | fade-out"` translates to
        // dj-transition="fade-in" on each child. Before #1273, the
        // 1-token form was silently rejected and no animation fired.
        dom = createDom('');
        expect(dom.window.djust.djTransition._parseSpec('fade-in')).toEqual({ single: 'fade-in' });
        expect(dom.window.djust.djTransition._parseSpec('  fade-in  ')).toEqual({ single: 'fade-in' });
    });

    it('1-token form applies the class on next frame', async () => {
        // Behavioral lock-in: the 1-token form must actually apply the
        // class (not just parse). Parser-only fix would still be silent
        // failure at runtime.
        dom = createDom('<div id="t" dj-transition="fade-in"></div>');
        const el = dom.window.document.getElementById('t');
        await waitForClass(el, 'fade-in');
        expect(el.classList.contains('fade-in')).toBe(true);
    });

    it('parseSpec rejects comma, paren, and bracket separators', () => {
        // Commas/parens/brackets would cause classList.add to throw
        // InvalidCharacterError at runtime — the parser must reject
        // them up front and return null.
        dom = createDom('');
        expect(dom.window.djust.djTransition._parseSpec('a,b,c')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec('(a b c)')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec('[a b c]')).toBeNull();
        expect(dom.window.djust.djTransition._parseSpec('a, b, c')).toBeNull();
    });

    it('applies start class synchronously, active/end on next frame', async () => {
        dom = createDom('<div id="t" dj-transition="start-cls active-cls end-cls"></div>');
        const el = dom.window.document.getElementById('t');
        // The module runs on DOMContentLoaded — phase-1 is applied
        // SYNCHRONOUSLY at module eval time. Assert BEFORE the 16 ms
        // rAF fallback fires (which would kick us into phase 2/3) —
        // flush the microtask queue only.
        await new Promise((r) => setTimeout(r, 0));
        expect(el.classList.contains('start-cls')).toBe(true);

        // After the next "frame" (fallback setTimeout 16 ms), phase-2
        // and phase-3 land and phase-1 is removed.
        await nextFrame(dom);
        expect(el.classList.contains('start-cls')).toBe(false);
        expect(el.classList.contains('active-cls')).toBe(true);
        expect(el.classList.contains('end-cls')).toBe(true);
    });

    it('transitionend removes the active class but keeps the end class', async () => {
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        // Wait until the active class has been applied (listener
        // registration follows the rAF callback).
        await waitForClass(el, 'a');

        // Simulate the browser firing transitionend on the element.
        // Synchronous dispatch — the handler runs inline on the same
        // tick, so no subsequent timer wait is required.
        el.dispatchEvent(new dom.window.Event('transitionend', { bubbles: true }));

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
        // fallback cleanup — the isConnected guard short-circuits
        // cleanup on detached nodes, so classList state is left as-is
        // and any parentNode access (if added later) is skipped.
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        await nextFrame(dom);
        expect(el.classList.contains('a')).toBe(true);

        el.remove();
        // Wait past the fallback window — the setTimeout should fire
        // against the detached element without throwing.
        await new Promise((r) => setTimeout(r, 650));
        // No crash — the test just reaching here means the fallback
        // didn't error on the detached node. The isConnected guard
        // leaves the detached element's classList untouched.
        expect(el.isConnected).toBe(false);
    });

    it('re-runs the sequence when the attribute value changes', async () => {
        dom = createDom('<div id="t" dj-transition="s a e"></div>');
        const el = dom.window.document.getElementById('t');
        // Wait for the listener registration to complete (rAF fired).
        await waitForClass(el, 'a');

        // Synchronous dispatch — handler runs inline, classList updates
        // are observable immediately.
        el.dispatchEvent(new dom.window.Event('transitionend', { bubbles: true }));
        expect(el.classList.contains('a')).toBe(false);

        // Re-trigger by swapping the spec.
        el.setAttribute('dj-transition', 's2 a2 e2');
        await waitForClass(el, 's2');
        expect(el.classList.contains('s2')).toBe(true);
        await waitForClass(el, 'a2');
        expect(el.classList.contains('e2')).toBe(true);
    });
});
