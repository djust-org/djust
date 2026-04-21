/**
 * Tests for dj-viewport-top / dj-viewport-bottom — infinite scroll.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// A controllable IntersectionObserver stub: tests trigger intersections
// manually via window.__fireIntersection(entries).
function installIOStub(win) {
    const observers = [];
    win.IntersectionObserver = class {
        constructor(cb, options) {
            this.cb = cb;
            this.options = options;
            this.targets = new Set();
            observers.push(this);
        }
        observe(el) { this.targets.add(el); }
        unobserve(el) { this.targets.delete(el); }
        disconnect() { this.targets.clear(); }
    };
    win.__observers = observers;
    win.__fireIntersection = function(target, isIntersecting = true) {
        for (const o of observers) {
            if (o.targets.has(target)) {
                o.cb([{ target, isIntersecting }]);
            }
        }
    };
}

function createDom(innerHtml) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${innerHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously', pretendToBeVisual: true });

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
            this.onerror = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;

    installIOStub(dom.window);

    dom.window.eval(clientCode);
    return dom;
}

function makeStream(attrs, kids = 3) {
    const items = [];
    for (let i = 0; i < kids; i++) items.push(`<div id="row-${i}">row ${i}</div>`);
    return `<div id="feed" ${attrs}>${items.join('')}</div>`;
}

describe('dj-viewport-top / dj-viewport-bottom', () => {
    it('exposes init / reset / teardown helpers on window.djust', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older"'));
        expect(typeof dom.window.djust.initInfiniteScroll).toBe('function');
        expect(typeof dom.window.djust.resetViewport).toBe('function');
        expect(typeof dom.window.djust.teardownInfiniteScroll).toBe('function');
    });

    it('no-op when neither top nor bottom attr is present', () => {
        const dom = createDom(makeStream(''));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const container = dom.window.document.getElementById('feed');
        // No observer should have been created for this container.
        const anyWatching = dom.window.__observers.some(o => {
            for (const t of o.targets) if (container.contains(t)) return true;
            return false;
        });
        expect(anyWatching).toBe(false);
    });

    it('dj-viewport-top fires once when first child enters viewport', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const first = dom.window.document.getElementById('row-0');
        const events = [];
        dom.window.document.addEventListener('dj-viewport', e => events.push(e.detail));

        dom.window.__fireIntersection(first, true);
        expect(events.length).toBe(1);
        expect(events[0].event).toBe('load_older');
        expect(events[0].edge).toBe('top');

        // Second intersection on same element is suppressed.
        dom.window.__fireIntersection(first, true);
        expect(events.length).toBe(1);
    });

    it('also invokes window.djust.handleEvent so the server receives the event', () => {
        // Regression: PR #796 Stage 11 review caught that a viewport event
        // only dispatched a CustomEvent but never reached the server —
        // the module called window.djust.pushEvent which doesn't exist.
        // The public entry point is window.djust.handleEvent (from
        // 11-event-handler.js); verify it is called with the right args.
        const dom = createDom(makeStream('dj-viewport-top="load_older"'));
        const calls = [];
        dom.window.djust.handleEvent = (name, params) => {
            calls.push({ name, params });
        };
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const first = dom.window.document.getElementById('row-0');

        dom.window.__fireIntersection(first, true);

        expect(calls.length).toBe(1);
        expect(calls[0].name).toBe('load_older');
        expect(calls[0].params).toEqual({ edge: 'top' });

        // Same-element re-intersection must not re-send to server.
        dom.window.__fireIntersection(first, true);
        expect(calls.length).toBe(1);
    });

    it('dj-viewport-bottom fires once when last child enters viewport', () => {
        const dom = createDom(makeStream('dj-viewport-bottom="load_newer"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const last = dom.window.document.getElementById('row-2');
        const events = [];
        dom.window.document.addEventListener('dj-viewport', e => events.push(e.detail));

        dom.window.__fireIntersection(last, true);
        expect(events.length).toBe(1);
        expect(events[0].event).toBe('load_newer');
        expect(events[0].edge).toBe('bottom');
    });

    it('resetViewport re-arms the observer so the next entry fires again', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const container = dom.window.document.getElementById('feed');
        const first = dom.window.document.getElementById('row-0');
        const events = [];
        dom.window.document.addEventListener('dj-viewport', e => events.push(e.detail));

        dom.window.__fireIntersection(first, true);
        expect(events.length).toBe(1);

        dom.window.djust.resetViewport(container);
        dom.window.__fireIntersection(first, true);
        expect(events.length).toBe(2);
    });

    it('supports both top and bottom on the same container', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older" dj-viewport-bottom="load_newer"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const first = dom.window.document.getElementById('row-0');
        const last = dom.window.document.getElementById('row-2');
        const events = [];
        dom.window.document.addEventListener('dj-viewport', e => events.push(e.detail));

        dom.window.__fireIntersection(first, true);
        dom.window.__fireIntersection(last, true);
        expect(events.map(e => e.edge).sort()).toEqual(['bottom', 'top']);
    });

    it('respects dj-viewport-threshold attribute', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older" dj-viewport-threshold="0.5"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        // Threshold lands on an observer.
        const obs = dom.window.__observers.find(o => o.options && o.options.threshold === 0.5);
        expect(obs).toBeTruthy();
    });

    it('ignores non-intersecting entries', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const first = dom.window.document.getElementById('row-0');
        const events = [];
        dom.window.document.addEventListener('dj-viewport', e => events.push(e.detail));

        dom.window.__fireIntersection(first, false);
        expect(events.length).toBe(0);
    });

    it('teardownInfiniteScroll disconnects the observer', () => {
        const dom = createDom(makeStream('dj-viewport-top="load_older"'));
        dom.window.djust.initInfiniteScroll(dom.window.document);
        const container = dom.window.document.getElementById('feed');
        dom.window.djust.teardownInfiniteScroll(container);
        // After teardown, any outstanding observer targets set is empty.
        for (const o of dom.window.__observers) {
            expect(o.targets.size).toBe(0);
        }
    });
});
