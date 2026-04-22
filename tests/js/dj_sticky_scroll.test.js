/**
 * Tests for dj-sticky-scroll — auto-scroll preservation (v0.6.0).
 *
 * jsdom's layout engine returns 0 for clientHeight / scrollHeight by
 * default because it doesn't do layout. We stub those properties on the
 * tested elements so the "at bottom" math is predictable.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>${bodyHtml}</div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

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

function waitForMutation(dom, ms = 0) {
    return new Promise((resolve) => dom.window.setTimeout(resolve, ms));
}

function stubLayout(el, { clientHeight, scrollHeight, scrollTop = 0 }) {
    // jsdom returns 0 for layout measurements. Override with plain
    // property descriptors so the sticky-scroll math has real numbers.
    Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight });
    Object.defineProperty(el, 'scrollHeight', { configurable: true, writable: true, value: scrollHeight });
    let _scrollTop = scrollTop;
    Object.defineProperty(el, 'scrollTop', {
        configurable: true,
        get() { return _scrollTop; },
        set(v) { _scrollTop = v; },
    });
}

describe('dj-sticky-scroll', () => {
    let dom;

    it('scrolls to bottom when children are appended and user is at bottom', async () => {
        dom = createDom('<div id="log" dj-sticky-scroll></div>');
        const el = dom.window.document.getElementById('log');
        stubLayout(el, { clientHeight: 100, scrollHeight: 100, scrollTop: 0 });
        // Let the module seed `_djStickyAtBottom = true` via _installDjStickyScrollFor
        await waitForMutation(dom, 5);

        // Simulate appending content (scrollHeight grows).
        el.scrollHeight = 500;
        el.appendChild(dom.window.document.createElement('div'));
        await waitForMutation(dom, 5);
        // Observer should have set scrollTop to scrollHeight (bottom).
        expect(el.scrollTop).toBe(500);
    });

    it('stops auto-scrolling when user scrolls up', async () => {
        dom = createDom('<div id="log" dj-sticky-scroll></div>');
        const el = dom.window.document.getElementById('log');
        stubLayout(el, { clientHeight: 100, scrollHeight: 500, scrollTop: 500 });
        await waitForMutation(dom, 5);

        // User scrolls up to read history (scrollTop moves away from bottom).
        el.scrollTop = 100;
        el.dispatchEvent(new dom.window.Event('scroll'));

        // Append content — should NOT auto-scroll because user isn't at bottom.
        el.scrollHeight = 1000;
        el.appendChild(dom.window.document.createElement('div'));
        await waitForMutation(dom, 5);
        expect(el.scrollTop).toBe(100); // unchanged
    });

    it('resumes auto-scrolling when user scrolls back to bottom', async () => {
        dom = createDom('<div id="log" dj-sticky-scroll></div>');
        const el = dom.window.document.getElementById('log');
        stubLayout(el, { clientHeight: 100, scrollHeight: 500, scrollTop: 100 });
        await waitForMutation(dom, 5);

        // User was away from bottom.
        el.dispatchEvent(new dom.window.Event('scroll'));
        expect(el._djStickyAtBottom).toBe(false);

        // User scrolls back to bottom.
        el.scrollTop = 400;  // 400 + 100 = 500 = scrollHeight → at bottom
        el.dispatchEvent(new dom.window.Event('scroll'));
        expect(el._djStickyAtBottom).toBe(true);

        // Append content — should auto-scroll now.
        el.scrollHeight = 800;
        el.appendChild(dom.window.document.createElement('div'));
        await waitForMutation(dom, 5);
        expect(el.scrollTop).toBe(800);
    });

    it('tears down observer when element is removed from DOM', async () => {
        dom = createDom('<div id="log" dj-sticky-scroll></div>');
        const el = dom.window.document.getElementById('log');
        await waitForMutation(dom, 5);
        expect(el._djStickyAtBottom).toBe(true);

        el.remove();
        await waitForMutation(dom, 5);
        // After teardown, the bookkeeping property is cleared.
        expect(el._djStickyAtBottom).toBeUndefined();
    });

    it('exposes _isAtBottom on window.djust.djStickyScroll', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.djStickyScroll._isAtBottom).toBe('function');
    });
});
