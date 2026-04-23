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

    it('teardownVirtualList removes the observer, frees state, and restores original children (#798)', () => {
        const dom = createDom(makeListHtml(10));
        const container = setupContainer(dom);
        dom.window.djust.teardownVirtualList(container);
        // After teardown:
        //  - the injected shell/spacer are gone (#798 — was leaky before)
        //  - the original 10 children are back in the container
        //  - refresh is a no-op (state map was cleared)
        expect(container.querySelector('[data-dj-virtual-shell]')).toBeNull();
        expect(container.querySelector('[data-dj-virtual-spacer]')).toBeNull();
        expect(container.children.length).toBe(10);

        // Sanity: refreshVirtualList after teardown must not blow up.
        Object.defineProperty(container, 'scrollTop', {
            configurable: true,
            writable: true,
            value: 80,
        });
        dom.window.djust.refreshVirtualList(container);
        expect(container.children.length).toBe(10);
    });

    // -----------------------------------------------------------------
    // Variable-height mode (#797) — ResizeObserver-driven heights
    // -----------------------------------------------------------------

    function makeVariableListHtml(count) {
        const items = [];
        for (let i = 0; i < count; i++) {
            items.push(`<div class="row" data-i="${i}">Row ${i}</div>`);
        }
        // No dj-virtual-item-height — opt-in via dj-virtual-variable-height.
        return `<div id="list" dj-virtual="items" dj-virtual-variable-height dj-virtual-overscan="0" dj-virtual-estimated-height="50" style="height: 100px; overflow: auto;">${items.join('')}</div>`;
    }

    // Inject a ResizeObserver stub that lets tests dispatch entries manually
    // via `window.__djRoEntries(entries)` — jsdom does not implement RO.
    function installStubResizeObserver(dom) {
        const win = dom.window;
        const instances = [];
        class StubRO {
            constructor(cb) {
                this.cb = cb;
                this.targets = new Set();
                instances.push(this);
            }
            observe(target) { this.targets.add(target); }
            unobserve(target) { this.targets.delete(target); }
            disconnect() { this.targets.clear(); }
        }
        win.ResizeObserver = StubRO;
        win.__djRoInstances = instances;
        // Helper: deliver a synthetic resize entry to all RO instances
        // observing any node in `nodeHeights` (keys = DOM nodes, values = px).
        win.__djRoResize = (nodeHeights) => {
            for (const ro of instances) {
                const entries = [];
                for (const [node, h] of nodeHeights) {
                    if (ro.targets.has(node)) {
                        entries.push({
                            target: node,
                            borderBoxSize: [{ blockSize: h, inlineSize: 1 }],
                            contentRect: { height: h, width: 1 },
                        });
                    }
                }
                if (entries.length) ro.cb(entries, ro);
            }
        };
    }

    it('variable mode activates via dj-virtual-variable-height attribute', () => {
        const dom = createDom(makeVariableListHtml(10));
        installStubResizeObserver(dom);
        const container = setupContainer(dom, { clientHeight: 100 });
        // Mode is variable → shell/spacer are injected just like fixed mode.
        expect(container.querySelector('[data-dj-virtual-shell]')).not.toBeNull();
        expect(container.querySelector('[data-dj-virtual-spacer]')).not.toBeNull();
        // With no heights measured yet, spacer uses estimated (50) * 10 = 500
        const spacer = container.querySelector('[data-dj-virtual-spacer]');
        expect(spacer.style.height).toBe('500px');
    });

    it('does not activate without either height attribute', () => {
        const dom = createDom(
            `<div id="list" dj-virtual="items" style="height:100px;"><div>a</div></div>`
        );
        installStubResizeObserver(dom);
        const container = dom.window.document.getElementById('list');
        dom.window.djust.initVirtualLists(dom.window.document);
        expect(container.querySelector('[data-dj-virtual-shell]')).toBeNull();
    });

    it('renders mixed-height items with correct cumulative offsets (#797)', () => {
        // 5 items, estimated default 50. Scroll to top, overscan=0. We feed
        // synthetic heights [80, 30, 120, 40, 60] via the RO stub, then
        // re-render and assert the spacer equals the measured sum and the
        // transform uses the measured prefix sum.
        const dom = createDom(makeVariableListHtml(5));
        installStubResizeObserver(dom);
        const container = setupContainer(dom, { clientHeight: 60 });
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const spacer = container.querySelector('[data-dj-virtual-spacer]');

        // Initial render at estimated heights: spacer = 5 * 50 = 250.
        expect(spacer.style.height).toBe('250px');

        // Feed measured heights through the RO stub. All 5 items are observed
        // (overscan=0, but all 5 fit inside the initial window expansion
        // because 60px viewport / 50px estimate = 2 visible; we also feed
        // the ones not yet rendered on the next scroll pass below).
        const measured = [80, 30, 120, 40, 60];
        const expectedTotal = measured.reduce((a, b) => a + b, 0); // 330

        // First pass: only rows currently attached to the shell get observed.
        // That's indices 0..1 (2 visible). Scroll down to pull the rest in.
        const firstBatch = new Map();
        for (const node of shell.children) {
            const idx = parseInt(node.getAttribute('data-i'), 10);
            firstBatch.set(node, measured[idx]);
        }
        dom.window.__djRoResize(firstBatch);
        dom.window.djust.refreshVirtualList(container);

        // Scroll down progressively to expose each item, feeding its height.
        for (let scrollTop = 80; scrollTop <= 260; scrollTop += 40) {
            Object.defineProperty(container, 'scrollTop', {
                configurable: true, writable: true, value: scrollTop,
            });
            dom.window.djust.refreshVirtualList(container);
            const batch = new Map();
            for (const node of shell.children) {
                const idx = parseInt(node.getAttribute('data-i'), 10);
                batch.set(node, measured[idx]);
            }
            dom.window.__djRoResize(batch);
            dom.window.djust.refreshVirtualList(container);
        }

        // After all 5 items measured, spacer should equal real sum (330px).
        expect(spacer.style.height).toBe(expectedTotal + 'px');

        // Scroll back to top and verify the prefix-sum transform for row 2
        // equals heights[0] + heights[1] = 80 + 30 = 110.
        Object.defineProperty(container, 'scrollTop', {
            configurable: true, writable: true, value: 115, // lands on row 2
        });
        dom.window.djust.refreshVirtualList(container);
        // shell.style.transform = translateY(offsets[start]) where start = 2.
        expect(shell.style.transform).toBe('translateY(110px)');
    });

    it('ResizeObserver height change updates cache and offsets', () => {
        const dom = createDom(makeVariableListHtml(3));
        installStubResizeObserver(dom);
        const container = setupContainer(dom, { clientHeight: 500 }); // big
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const spacer = container.querySelector('[data-dj-virtual-spacer]');
        expect(spacer.style.height).toBe('150px'); // 3 * 50 estimated

        // Feed initial heights: [40, 40, 40].
        const initial = new Map();
        for (const node of shell.children) {
            initial.set(node, 40);
        }
        dom.window.__djRoResize(initial);
        dom.window.djust.refreshVirtualList(container);
        expect(spacer.style.height).toBe('120px'); // 3 * 40

        // Now the middle item's content expands to 200px. Deliver a resize
        // entry just for that node; the cache + spacer should update.
        const grown = new Map();
        grown.set(shell.children[1], 200);
        dom.window.__djRoResize(grown);
        dom.window.djust.refreshVirtualList(container);
        // 40 + 200 + 40 = 280
        expect(spacer.style.height).toBe('280px');
        // Row 2 (index 2) offset = 40 + 200 = 240; scroll there to verify
        // transform.
        Object.defineProperty(container, 'scrollTop', {
            configurable: true, writable: true, value: 240,
        });
        dom.window.djust.refreshVirtualList(container);
        // start = firstVisibleIndex at scrollTop 240 → index 2 (offset 240).
        // overscan=0, so transform = translateY(240px).
        expect(shell.style.transform).toBe('translateY(240px)');
    });

    it('fixed-height mode remains unchanged when dj-virtual-item-height is set (regression)', () => {
        // Same contract as the original fixed-mode test above. Explicit
        // regression guard: adding variable-height support must not regress
        // the fixed-height code path.
        const dom = createDom(makeListHtml(1000));
        installStubResizeObserver(dom);
        const container = setupContainer(dom, { clientHeight: 100 });
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const spacer = container.querySelector('[data-dj-virtual-spacer]');
        // 1000 * 20 = 20000 (fixed math unchanged)
        expect(spacer.style.height).toBe('20000px');
        // 100/20=5 visible + 2 top (clamped to 0) + 2 bottom = 7
        expect(shell.children.length).toBe(7);

        // Scroll to mid-list; fixed-math must still drive identity recycling.
        Object.defineProperty(container, 'scrollTop', {
            configurable: true, writable: true, value: 400,
        });
        dom.window.djust.refreshVirtualList(container);
        expect(shell.children[0].getAttribute('data-i')).toBe('18'); // 20-2
        expect(shell.style.transform).toBe('translateY(360px)');     // 18*20
    });
});
