/**
 * ADR-026 iteration 2 — the client applies the keyed splice ops (#2017).
 *
 * Iteration 1 (PR #2126) taught the Rust differ to emit `VirtualInsert` /
 * `VirtualMove` / `VirtualRemove` for a `[dj-virtual]` parent instead of
 * index-addressed child ops, because an index means different things on the
 * two sides: the Nth ITEM to the server, the Nth VISIBLE item here. Those ops
 * ship dark — the differ's flag defaults off — because nothing could apply
 * them. This is that applier.
 *
 * The ops land in the list's ITEM POOL, not the DOM: a `[dj-virtual]`
 * container's children are only the visible window, with off-window rows
 * detached and held in `state.items`. A row spliced into the DOM instead never
 * enters the pool — not counted for the spacer height, not reachable by
 * scrolling, dropped on the next render.
 *
 * Two constraints iteration 1's review established, both pinned below:
 *
 * - **Emitted order is load-bearing.** `before_key` names an anchor that an
 *   EARLIER op placed, so the ops must not be split into the natural
 *   Remove/Move/Insert phases the other patch kinds use.
 * - **The key may be spelled either way.** The Rust parser reads `dj-key` OR
 *   `data-key` into `VNode.key`, while a virtual list's own `keyAttr`
 *   defaults to `data-key` and is configurable — so a lookup that checks only
 *   one spelling silently misses on lists that use the other.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * A mounted virtual list with `count` keyed rows.
 * @param {object} opts - `keyAttr` picks the attribute the rows carry.
 */
function createEnv(count = 6, opts = {}) {
    const keyAttr = opts.keyAttr || 'data-key';
    // `declare: false` writes the rows with `keyAttr` but does NOT tell the
    // list about it — the realistic dj-key template shape, and the only way
    // the attribute fallback is exercised.
    const declare = opts.declare !== false && !!opts.keyAttr;
    const attrDecl = declare ? ` dj-virtual-key-attr="${opts.keyAttr}"` : '';
    let rows = '';
    for (let i = 0; i < count; i++) {
        rows += `<div ${keyAttr}="k${i}" id="row-${i}">row ${i}</div>`;
    }
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-view="app.V">' +
            `<div id="feed" dj-id="7" dj-virtual="rows"${attrDecl} ` +
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

/** The pool's keys, in order — the thing every assertion here is about. */
function poolKeys(dom, keyAttr = 'data-key') {
    const el = dom.window.document.getElementById('feed');
    const items = dom.window.djust._virtualPoolItems(el);
    return items ? items.map((n) => n.getAttribute(keyAttr)) : null;
}

/** A VNode for a row, in the shape the Rust differ serialises. */
function rowVNode(key) {
    return {
        tag: 'div',
        attrs: { 'data-key': key, 'dj-id': `n-${key}` },
        children: [{ tag: '#text', attrs: {}, children: [], text: `row ${key}`, key: null }],
        text: null,
        key,
    };
}

function insert(key, before_key) {
    return { type: 'VirtualInsert', path: [], d: '7', key, node: rowVNode(key), before_key };
}
function move(key, before_key) {
    return { type: 'VirtualMove', path: [], d: '7', key, before_key };
}
function remove(key) {
    return { type: 'VirtualRemove', path: [], d: '7', key };
}

async function apply(dom, patches) {
    return dom.window.djust.applyPatches(patches, dom.window.document.querySelector('[dj-root]'));
}

describe('#2017 iteration 2: applying keyed splice ops', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('exposes the keyed-op seam', () => {
        const dom = createEnv();
        expect(typeof dom.window.djust._virtualKeyedOp).toBe('function');
        expect(typeof dom.window.djust._flushVirtualKeyedOps).toBe('function');
    });

    it('inserts a row into the POOL, not just the visible window', async () => {
        // The whole point. The window shows 2 of 6 rows; a row appended to the
        // DOM would be invisible to the pool and dropped on the next render.
        const dom = createEnv(6);
        await apply(dom, [insert('new', null)]);

        expect(poolKeys(dom)).toEqual(['k0', 'k1', 'k2', 'k3', 'k4', 'k5', 'new']);
    });

    it('honours before_key rather than always appending', async () => {
        // #2017 item 4: the pre-existing loose-child absorb always appends at
        // the tail, which is right for an append-only feed and wrong for
        // anything else. before_key is what fixes it.
        const dom = createEnv(3);
        await apply(dom, [insert('new', 'k1')]);

        expect(poolKeys(dom)).toEqual(['k0', 'new', 'k1', 'k2']);
    });

    it('removes by key', async () => {
        const dom = createEnv(4);
        await apply(dom, [remove('k2')]);

        expect(poolKeys(dom)).toEqual(['k0', 'k1', 'k3']);
    });

    it('moves by key', async () => {
        const dom = createEnv(4);
        await apply(dom, [move('k3', 'k1')]);

        expect(poolKeys(dom)).toEqual(['k0', 'k3', 'k1', 'k2']);
    });

    it('a move with no before_key goes to the tail', async () => {
        const dom = createEnv(4);
        await apply(dom, [move('k0', null)]);

        expect(poolKeys(dom)).toEqual(['k1', 'k2', 'k3', 'k0']);
    });

    it('applies a batch in EMITTED order, so anchors resolve', async () => {
        // The load-bearing constraint. Prepending [x, y] emits, in order,
        // "insert y before k0" then "insert x before y" (the differ walks in
        // reverse so each anchor is already placed). Applying them in any
        // other order strands x at the tail.
        const dom = createEnv(2);
        await apply(dom, [insert('y', 'k0'), insert('x', 'y')]);

        expect(poolKeys(dom)).toEqual(['x', 'y', 'k0', 'k1']);
    });

    it('keeps the keyed ops in ONE sort phase', () => {
        // _sortPatches assigns phases to every other patch kind, and splitting
        // these into Remove/Move/Insert phases would break the anchors. Pinned
        // here rather than trusted, because the sort is the one place a future
        // change could reorder them.
        const dom = createEnv(2);
        const batch = [insert('y', 'k0'), move('k1', null), remove('k0'), insert('x', 'y')];
        const before = batch.map((p) => `${p.type}:${p.key}`);

        dom.window.djust._sortPatches(batch);

        expect(batch.map((p) => `${p.type}:${p.key}`)).toEqual(before);
    });

    it('finds rows keyed with dj-key when the list is NOT configured for it', async () => {
        // The Rust parser reads EITHER attribute into VNode.key
        // (crates/djust_vdom/src/parser.rs:433), while a virtual list's keyAttr
        // defaults to `data-key`. So the realistic mismatch is a template
        // written with `dj-key` and no `dj-virtual-key-attr` — the fallback is
        // the only thing that finds those rows.
        //
        // An earlier version of this test set dj-virtual-key-attr="dj-key",
        // which makes the FIRST lookup succeed and the fallback dead code:
        // gate-off proved it passed with the fallback removed.
        const dom = createEnv(3, { keyAttr: 'dj-key', declare: false });
        await apply(dom, [remove('k1')]);

        expect(poolKeys(dom, 'dj-key')).toEqual(['k0', 'k2']);
    });

    it('renders the touched list, exactly once per patch batch', async () => {
        // TWO assertions, and the first is the one that matters. Every other
        // test here reads the POOL, which the ops mutate directly — so none of
        // them needs the render to have happened at all. Gate-off proved it:
        // removing the flush entirely failed nothing, and an upper-bound-only
        // assertion (`<= 1`) is satisfied by ZERO renders. So assert the
        // visible window actually reflects the change, and pin the count from
        // BOTH sides: rendering per op re-slices the window each time, and in
        // variable mode recomputes every offset — O(items) per op.
        const dom = createEnv(6);
        const el = dom.window.document.getElementById('feed');
        // Rendered rows live in the inner shell, NOT as direct children of the
        // container — those are the shell and the spacer. Observing the
        // container is why the first version of this saw zero renders.
        const shell = el.querySelector('[data-dj-virtual-shell]');
        expect(shell).toBeTruthy();
        let renders = 0;
        const observer = new dom.window.MutationObserver(() => {
            renders++;
        });
        observer.observe(shell, { childList: true });

        await apply(dom, [insert('a', 'k0'), insert('b', 'k0'), insert('c', 'k0')]);
        observer.disconnect();

        const visible = Array.from(shell.children)
            .map((n) => n.getAttribute('data-key'))
            .filter(Boolean);
        expect(visible.length).toBeGreaterThan(0);
        expect(visible[0]).toBe('a');
        expect(renders).toBe(1);
    });

    it('a re-inserted key replaces rather than duplicating', async () => {
        // A duplicate key would make every later op addressing it ambiguous —
        // the differ refuses duplicates on its side (DJE-052), and the pool
        // must not manufacture one either.
        const dom = createEnv(3);
        await apply(dom, [insert('k1', null)]);

        const keys = poolKeys(dom);
        expect(keys.filter((k) => k === 'k1')).toHaveLength(1);
        expect(keys).toEqual(['k0', 'k2', 'k1']);
    });

    it('removing an absent key is a no-op, not a failure', async () => {
        // The end state is what matters: the key is not in the pool either
        // way. Failing here would abort the rest of the batch.
        const dom = createEnv(3);
        const ok = await apply(dom, [remove('nope')]);

        expect(ok).toBe(true);
        expect(poolKeys(dom)).toEqual(['k0', 'k1', 'k2']);
    });

    it('reports rather than silently dropping an op on a non-virtual target', async () => {
        // The server only emits these for a [dj-virtual] parent, so this means
        // the list has not mounted here. Dropping it silently strands the row.
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body><div dj-root dj-view="app.V">' +
                '<div id="plain" dj-id="7"><div data-key="k0">a</div></div>' +
                '</div></body></html>',
            { runScripts: 'dangerously', url: 'http://localhost/' }
        );
        dom.window.CSS = { escape: (v) => String(v) };
        const warnings = [];
        dom.window.console = {
            log: () => {},
            warn: (...a) => warnings.push(a.join(' ')),
            error: () => {},
            groupCollapsed: () => {},
            groupEnd: () => {},
        };
        dom.window.eval(`
            window.WebSocket = class { constructor(){this.readyState=0;} send(){} close(){} };
            window.DJUST_USE_WEBSOCKET = false; window.location.reload = function(){};
        `);
        dom.window.eval(clientCode);

        const ok = await dom.window.djust.applyPatches(
            [remove('k0')],
            dom.window.document.querySelector('[dj-root]')
        );

        expect(ok).toBe(false);
        expect(warnings.join(' ')).toMatch(/virtual list/i);
    });

    it('leaves an ordinary keyed list untouched', async () => {
        // Regression guard: the vast majority of lists are not virtualised and
        // must keep using the index-addressed child ops.
        const dom = createEnv(4);
        const before = poolKeys(dom);

        await apply(dom, [{ type: 'SetText', path: [0, 0], d: null, text: 'changed' }]);

        expect(poolKeys(dom)).toEqual(before);
    });
});
