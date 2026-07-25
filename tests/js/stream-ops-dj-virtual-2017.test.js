/**
 * #2017 item 1 — stream ops targeting a [dj-virtual] container must go
 * through the virtual list's item pool, not straight into the DOM.
 *
 * `handleStreamMessage` (17-streaming.js) resolves `op.target` and mutates the
 * element directly: `el.appendChild(frag)`, `el.insertBefore(...)`,
 * `el.remove()`, and a prune that trims element children.
 *
 * A [dj-virtual] container is not an ordinary element. Its children are a
 * WINDOW — the visible slice — while the full collection lives in the list's
 * `state.items`, with off-window rows detached. Appending into the shell
 * therefore does not add a row to the list at all: the item never enters
 * `state.items`, so it is not counted for the spacer height, not reachable by
 * scrolling, and is dropped the moment the window re-renders.
 *
 * The documented pairing in `docs/website/guides/large-lists.md` — stream the
 * data, virtualize the rendering — silently does not work without bespoke app
 * JS, which is what item 1 of #2017 asks to fix. 29-virtual-list.js already
 * exposes the seam: setting `container.__djVirtualItems` replaces the pool.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(rowCount = 10) {
    let rows = '';
    for (let i = 0; i < rowCount; i++) {
        rows += `<div data-key="k${i}" id="row-${i}">row ${i}</div>`;
    }
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-view="app.V">' +
            `<div id="feed" dj-stream="messages" dj-virtual="messages" ` +
            `dj-virtual-item-height="20" style="height:40px">${rows}</div>` +
            '</div></body></html>',
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }
    dom.window.console = {
        log: () => {},
        warn: () => {},
        error: () => {},
        groupCollapsed: () => {},
        groupEnd: () => {},
    };
    dom.window.eval(`
        window.WebSocket = class { constructor(){this.readyState=0;} send(){} close(){} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
    `);
    dom.window.eval(clientCode);
    dom.window.djust.initVirtualLists();
    return dom;
}

/** The pool's row ids, in order — size alone cannot detect misplacement. */
function poolIds(dom) {
    const el = dom.window.document.getElementById('feed');
    const items = dom.window.djust._virtualPoolItems
        ? dom.window.djust._virtualPoolItems(el)
        : null;
    return items ? items.map((n) => n.id) : null;
}

/** How many rows the virtual list actually knows about. */
function poolSize(dom) {
    return dom.window.djust._virtualPoolSize
        ? dom.window.djust._virtualPoolSize(dom.window.document.getElementById('feed'))
        : null;
}

function streamOp(dom, op) {
    dom.window.djust.handleStreamMessage({
        type: 'stream',
        stream: 'messages',
        ops: [op],
    });
}

describe('#2017 item 1: stream ops on a [dj-virtual] container', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('exposes a pool-size probe for diagnosis', () => {
        const dom = createEnv();
        expect(typeof dom.window.djust._virtualPoolSize).toBe('function');
    });

    it('append puts the row at the TAIL of the pool', () => {
        // CHARACTERIZATION, not a proof of this change: append already worked
        // via the pre-existing loose-child absorb (#1989 symptom 2), which
        // appends at the tail — the right place for an append. This test stays
        // green with the routing gated off, and that is correct. It is here so
        // a future refactor of either path cannot silently break append.
        const dom = createEnv(10);
        const before = poolIds(dom);

        streamOp(dom, {
            op: 'append',
            target: '#feed',
            html: '<div data-key="new1" id="row-new1">newest</div>',
        });

        expect(poolIds(dom)).toEqual(before.concat(['row-new1']));
    });

    it('prepend puts the row at the FRONT of the pool (THE BUG)', () => {
        // Size alone cannot catch this. The pre-existing loose-child absorb
        // (#1989 symptom 2) already pulls an appended node into the pool — but
        // it always appends at the TAIL, which is correct for append-only
        // feeds and WRONG for a prepend. That tail-placement assumption is
        // #2017 item 4; routing prepend through the pool is what fixes it.
        const dom = createEnv(10);
        const before = poolIds(dom);

        streamOp(dom, {
            op: 'prepend',
            target: '#feed',
            html: '<div data-key="old1" id="row-old1">oldest</div>',
        });

        expect(poolIds(dom)).toEqual(['row-old1'].concat(before));
    });

    it('prune trims the pool, not just the visible window', () => {
        const dom = createEnv(10);
        streamOp(dom, { op: 'prune', target: '#feed', limit: 4, edge: 'top' });
        expect(poolSize(dom)).toBe(4);
    });

    it('an ordinary (non-virtual) stream target is untouched', () => {
        // Regression guard: the vast majority of stream targets are plain
        // containers and must keep the existing direct-DOM behavior.
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body><div dj-root dj-view="app.V">' +
                '<div id="plain" dj-stream="messages"><div>a</div></div>' +
                '</div></body></html>',
            { runScripts: 'dangerously', url: 'http://localhost/' }
        );
        dom.window.CSS = { escape: (v) => String(v) };
        dom.window.console = { log: () => {}, warn: () => {}, error: () => {}, groupCollapsed: () => {}, groupEnd: () => {} };
        dom.window.eval(`
            window.WebSocket = class { constructor(){this.readyState=0;} send(){} close(){} };
            window.DJUST_USE_WEBSOCKET = false; window.location.reload = function(){};
        `);
        dom.window.eval(clientCode);

        dom.window.djust.handleStreamMessage({
            type: 'stream',
            stream: 'messages',
            ops: [{ op: 'append', target: '#plain', html: '<div>b</div>' }],
        });

        const plain = dom.window.document.getElementById('plain');
        expect(plain.children.length).toBe(2);
        expect(plain.textContent).toContain('b');
    });
});
