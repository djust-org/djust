/**
 * #2033 — dj-virtual teardown on SPA-nav container reuse.
 *
 * When SPA-style navigation reuses the SAME physical [dj-virtual] container
 * node across a view / data-source change (instead of remounting), nothing
 * tears the client-side virtualization down: its state lives in a WeakMap
 * keyed on the container node's identity, and the never-removed shell/spacer
 * survive the server morph. The result is overlapping, garbled content — a
 * leftover row from a previously-viewed thread rendered at the shell's stale
 * translateY, on top of the new view's real rows.
 *
 * These tests reproduce the exact sequence at the DOM-structure + WeakMap-state
 * + item-pool level (JSDOM has no layout engine, so no pixel assertions):
 *   1. build + init a virtualized container (tracked: shell/spacer + STATE),
 *   2. mutate the SAME node to a different view (attr-loss OR dj-id change),
 *      leaving the surviving shell as a real morph would,
 *   3. run the reinit path,
 *   4. assert the stale shell/spacer are gone, the container shows ONLY the new
 *      view's rows (no cross-view merge), and STATE no longer virtualizes it.
 * Plus a regression guard: the NORMAL same-thread re-render still self-heals
 * via absorbLooseChildren (#1988/#1989) — the fix is scoped to identity CHANGE.
 */

import { describe, it, expect } from 'vitest';
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
</html>`, {
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        url: 'http://localhost/',
    });

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

// A virtualized "thread" container (fixed-height mode) with `count` old rows.
// dj-id lets us exercise the identity-change-via-dj-id branch.
function virtualThreadHtml(count, { source = 'messages', djId = 'thread-a' } = {}) {
    const rows = [];
    for (let i = 0; i < count; i++) {
        rows.push(`<div class="oldrow" data-i="${i}">Old ${i}</div>`);
    }
    return `<div id="pane" dj-virtual="${source}" dj-id="${djId}" ` +
        `dj-virtual-item-height="20" dj-virtual-overscan="2" ` +
        `style="height: 400px; overflow: auto;">${rows.join('')}</div>`;
}

function setupPane(dom, { clientHeight = 400 } = {}) {
    const container = dom.window.document.getElementById('pane');
    Object.defineProperty(container, 'clientHeight', {
        configurable: true, value: clientHeight,
    });
    Object.defineProperty(container, 'scrollTop', {
        configurable: true, writable: true, value: 0,
    });
    dom.window.djust.initVirtualLists(dom.window.document);
    return container;
}

// Mirror reinitAfterDOMUpdate's dj-virtual block (09-event-binding.js): scan +
// then refresh every still-[dj-virtual] container.
function reinit(dom, container) {
    dom.window.djust.initVirtualLists(dom.window.document);
    dom.window.document.querySelectorAll('[dj-virtual]').forEach((el) => {
        dom.window.djust.refreshVirtualList(el);
    });
    return container;
}

function makeNewRows(dom, count) {
    const rows = [];
    for (let i = 0; i < count; i++) {
        const el = dom.window.document.createElement('div');
        el.className = 'newrow';
        el.setAttribute('data-n', String(i));
        el.textContent = 'New ' + i;
        rows.push(el);
    }
    return rows;
}

function looseChildren(container) {
    return Array.from(container.children).filter(
        (c) => !c.hasAttribute('data-dj-virtual-shell') &&
               !c.hasAttribute('data-dj-virtual-spacer')
    );
}

describe('#2033 dj-virtual teardown on SPA-nav container reuse', () => {
    it('tears down a container that LOST dj-virtual (nav to a non-virtualized view)', () => {
        // 1. Virtualize a thread and confirm it is tracked.
        const dom = createDom(virtualThreadHtml(30));
        const container = setupPane(dom);
        expect(container.querySelector('[data-dj-virtual-shell]')).not.toBeNull();
        expect(container.querySelector('[data-dj-virtual-spacer]')).not.toBeNull();
        // The visible slice of OLD rows lives inside the surviving shell.
        expect(container.querySelector('[data-dj-virtual-shell]').querySelector('.oldrow'))
            .not.toBeNull();

        // 2. SPA nav to a small NON-virtualized thread: the server morph
        //    removes dj-virtual (+ height attr), keeps the shell/spacer it has
        //    no notion of (the #2033 DOM dump), and authors the new view's rows
        //    as loose children of the SAME container node.
        container.removeAttribute('dj-virtual');
        container.removeAttribute('dj-virtual-item-height');
        for (const row of makeNewRows(dom, 3)) container.appendChild(row);

        // 3. Reinit path.
        reinit(dom, container);

        // 4. Teardown ran: stale shell/spacer (carrying the leftover old rows)
        //    are gone; the container shows ONLY the new view's rows; no old row
        //    leaked in anywhere.
        expect(container.querySelector('[data-dj-virtual-shell]')).toBeNull();
        expect(container.querySelector('[data-dj-virtual-spacer]')).toBeNull();
        expect(container.querySelectorAll('.oldrow').length).toBe(0);
        expect(container.querySelectorAll('.newrow').length).toBe(3);
        expect(looseChildren(container).length).toBe(3);

        // STATE no longer treats it as virtualized: a refresh is now a no-op.
        const before = container.innerHTML;
        dom.window.djust.refreshVirtualList(container);
        expect(container.innerHTML).toBe(before);
    });

    it('re-virtualizes fresh when the view IDENTITY changes (dj-id) but dj-virtual is kept — no cross-source merge', () => {
        // Small thread, big viewport → all rows fit in the shell window.
        const dom = createDom(virtualThreadHtml(6, { djId: 'thread-a' }));
        const container = setupPane(dom);
        const originalShell = container.querySelector('[data-dj-virtual-shell]');
        expect(originalShell.querySelectorAll('.oldrow').length).toBe(6);

        // SPA nav to a DIFFERENT thread that is ALSO virtualized against the
        // same source var: the morph keeps dj-virtual + item-height, changes
        // the host view's dj-id, keeps the shell (old rows inside), and appends
        // the new thread's rows as loose children.
        container.setAttribute('dj-id', 'thread-b');
        for (const row of makeNewRows(dom, 4)) container.appendChild(row);

        reinit(dom, container);

        // A FRESH virtualization is established over ONLY the new rows: the old
        // shell (and its leftover rows) is gone, the pool is the new view only.
        const healedShell = container.querySelector('[data-dj-virtual-shell]');
        expect(healedShell).not.toBeNull();
        expect(healedShell).not.toBe(originalShell);
        expect(container.querySelector('[data-dj-virtual-spacer]')).not.toBeNull();
        // No leftover old rows merged into the new pool.
        expect(healedShell.querySelectorAll('.oldrow').length).toBe(0);
        expect(container.querySelectorAll('.oldrow').length).toBe(0);
        expect(healedShell.querySelectorAll('.newrow').length).toBe(4);
        // Spacer sized to the NEW pool only: 4 * 20 = 80 (not 6-old + 4-new).
        expect(container.querySelector('[data-dj-virtual-spacer]').style.height).toBe('80px');
        expect(looseChildren(container).length).toBe(0);
    });

    it('regression: a same-thread re-render still self-heals via absorbLooseChildren (#1988/#1989)', () => {
        // Same identity (dj-virtual, source value, dj-id all unchanged): a
        // stream-appended row must still be absorbed into the existing pool —
        // the fix must NOT tear this down.
        const dom = createDom(virtualThreadHtml(3));
        const container = setupPane(dom);
        const shell = container.querySelector('[data-dj-virtual-shell]');
        const spacer = container.querySelector('[data-dj-virtual-spacer]');
        expect(shell.children.length).toBe(3);

        // Server re-render appends ONE new row OUTSIDE the shell/spacer wrapper,
        // WITHOUT changing dj-virtual / dj-id (same thread).
        const appended = dom.window.document.createElement('div');
        appended.className = 'oldrow';
        appended.setAttribute('data-i', '3');
        appended.textContent = 'Old 3';
        container.appendChild(appended);

        reinit(dom, container);

        // Self-healed in place: same shell instance, the appended row absorbed
        // into the pool (same element reference), NOT torn down.
        const shellAfter = container.querySelector('[data-dj-virtual-shell]');
        expect(shellAfter).toBe(shell);
        expect(container.querySelector('[data-dj-virtual-spacer]')).toBe(spacer);
        expect(shellAfter.children.length).toBe(4);
        expect(shellAfter.querySelector('[data-i="3"]')).toBe(appended);
        expect(looseChildren(container).length).toBe(0);
    });
});
