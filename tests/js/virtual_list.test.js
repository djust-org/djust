/**
 * Tests for dj-virtual — virtual/windowed lists with DOM recycling.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(innerHtml) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${innerHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously', pretendToBeVisual: true });

    // Stub WebSocket so djustInit doesn't blow up.
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

    // Give jsdom an IntersectionObserver stub (not used by virtual lists,
    // but 30-infinite-scroll module also loads).
    if (typeof dom.window.IntersectionObserver === 'undefined') {
        dom.window.IntersectionObserver = class {
            observe() {}
            unobserve() {}
            disconnect() {}
        };
    }

    dom.window.eval(clientCode);
    return dom;
}

function makeListHtml(count) {
    const items = [];
    for (let i = 0; i < count; i++) {
        items.push(`<div class="row" data-i="${i}">Row ${i}</div>`);
    }
    return `<div id="list" dj-virtual="items" dj-virtual-item-height="20" dj-virtual-overscan="2" style="height: 100px; overflow: auto;">${items.join('')}</div>`;
}

function setupContainer(dom, opts = {}) {
    const container = dom.window.document.getElementById('list');
    // Force layout metrics that jsdom doesn't compute.
    Object.defineProperty(container, 'clientHeight', {
        configurable: true,
        value: opts.clientHeight ?? 100,
    });
    Object.defineProperty(container, 'scrollTop', {
        configurable: true,
        writable: true,
        value: 0,
    });
    dom.window.djust.initVirtualLists(dom.window.document);
    return container;
}

describe('dj-virtual', () => {
    it('exposes init / refresh / teardown helpers on window.djust', () => {
        const dom = createDom(makeListHtml(5));
        expect(typeof dom.window.djust.initVirtualLists).toBe('function');
        expect(typeof dom.window.djust.refreshVirtualList).toBe('function');
        expect(typeof dom.window.djust.teardownVirtualList).toBe('function');
    });

    it('renders only visible slice + overscan for a large list', () => {
        const dom = createDom(makeListHtml(1000));
        const container = setupContainer(dom, { clientHeight: 100 });
        // 100 / 20 = 5 visible + 2 overscan top + 2 overscan bottom (top clamped to 0)
        const shell = container.querySelector('[data-dj-virtual-shell]');
        // scrollTop=0 → start=0, end=min(1000, 0+5+2)=7
        expect(shell.children.length).toBe(7);
    });

    it('creates a spacer sized to the virtual length', () => {
        const dom = createDom(makeListHtml(100));
        const container = setupContainer(dom);
        const spacer = container.querySelector('[data-dj-virtual-spacer]');
        // 100 * 20 = 2000
        expect(spacer.style.height).toBe('2000px');
    });

    it('handles an empty list', () => {
        const dom = createDom(`<div id="list" dj-virtual="items" dj-virtual-item-height="20" style="height:100px;"></div>`);
        const container = setupContainer(dom);
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const spacer = container.querySelector('[data-dj-virtual-spacer]');
        expect(shell.children.length).toBe(0);
        expect(spacer.style.height).toBe('0px');
    });

    it('handles a list shorter than the viewport', () => {
        const dom = createDom(makeListHtml(3));
        const container = setupContainer(dom, { clientHeight: 200 });
        const shell = container.querySelector('[data-dj-virtual-shell]');
        expect(shell.children.length).toBe(3);
    });

    it('requires dj-virtual-item-height to activate', () => {
        const dom = createDom(`<div id="list" dj-virtual="items" style="height:100px;"><div>a</div><div>b</div></div>`);
        const container = dom.window.document.getElementById('list');
        dom.window.djust.initVirtualLists(dom.window.document);
        // No shell was injected.
        expect(container.querySelector('[data-dj-virtual-shell]')).toBeNull();
    });

    it('recycles item element identity across scroll', () => {
        const dom = createDom(makeListHtml(50));
        const container = setupContainer(dom, { clientHeight: 100 });
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const firstRefAtTop = shell.children[0];
        expect(firstRefAtTop.getAttribute('data-i')).toBe('0');

        // Scroll to mid-list so the slice shifts.
        Object.defineProperty(container, 'scrollTop', {
            configurable: true,
            writable: true,
            value: 400, // 400 / 20 = row 20
        });
        dom.window.djust.refreshVirtualList(container);

        const firstAfterScroll = shell.children[0];
        // At scrollTop=400, start = max(0, 20-2) = 18
        expect(firstAfterScroll.getAttribute('data-i')).toBe('18');
    });

    it('updates transform to position the visible window correctly', () => {
        const dom = createDom(makeListHtml(100));
        const container = setupContainer(dom, { clientHeight: 100 });
        const shell = container.querySelector('[data-dj-virtual-shell]');
        expect(shell.style.transform).toBe('translateY(0px)');

        Object.defineProperty(container, 'scrollTop', {
            configurable: true,
            writable: true,
            value: 200, // row 10, start = 10-2 = 8
        });
        dom.window.djust.refreshVirtualList(container);
        // 8 * 20 = 160
        expect(shell.style.transform).toBe('translateY(160px)');
    });

    it('respects custom overscan', () => {
        const html = makeListHtml(100).replace('dj-virtual-overscan="2"', 'dj-virtual-overscan="5"');
        const dom = createDom(html);
        const container = setupContainer(dom, { clientHeight: 100 });
        const shell = container.querySelector('[data-dj-virtual-shell]');
        // 5 visible + 0 top overscan (clamped) + 5 bottom = 10
        expect(shell.children.length).toBe(10);
    });

    it('allows replacing the item pool via __djVirtualItems + refresh', () => {
        const dom = createDom(makeListHtml(5));
        const container = setupContainer(dom);
        const newItems = [];
        for (let i = 0; i < 3; i++) {
            const el = dom.window.document.createElement('div');
            el.className = 'row';
            el.setAttribute('data-i', 'new-' + i);
            newItems.push(el);
        }
        container.__djVirtualItems = newItems;
        dom.window.djust.refreshVirtualList(container);
        const shell = container.querySelector('[data-dj-virtual-shell]');
        expect(shell.children.length).toBe(3);
        expect(shell.children[0].getAttribute('data-i')).toBe('new-0');
    });

    it('teardownVirtualList removes the observer and frees state', () => {
        const dom = createDom(makeListHtml(10));
        const container = setupContainer(dom);
        dom.window.djust.teardownVirtualList(container);
        // After teardown, refresh is a no-op (state map was cleared).
        // We verify by mutating scrollTop and checking the shell doesn't
        // repaint (visibleStart was not reset).
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const beforeCount = shell.children.length;
        Object.defineProperty(container, 'scrollTop', {
            configurable: true,
            writable: true,
            value: 80,
        });
        dom.window.djust.refreshVirtualList(container);
        expect(shell.children.length).toBe(beforeCount);
    });
});
