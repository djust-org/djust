/**
 * #2017 item 5 — diagnose a patch that misses because its target is an
 * OFF-WINDOW item held by a [dj-virtual] list.
 *
 * A virtual list keeps off-window items DETACHED, held only in `state.items`
 * (`29-virtual-list.js`: "Off-window items stay detached, held only in
 * state.items"). A server patch targeting such an item therefore resolves to
 * null in `getNodeByPath`, and the existing miss warning says only "node not
 * found", listing generic suggested causes — third-party JS, a changed
 * `{% if %}`, a different rendering path. None of those is the actual reason,
 * which is exactly what made #1988/#1989 expensive to investigate.
 *
 * Contract pinned here:
 *   - `window.djust._findVirtualListHolding(djId)` returns the [dj-virtual]
 *     container currently holding a detached item with that dj-id, else null.
 *   - When a patch misses AND that helper positively identifies a holder, the
 *     miss warning names dj-virtual and the container, so the reader is not
 *     sent after the generic causes.
 *   - No false positives: an ordinary miss on a page with no virtual list (or
 *     with one that is not holding the id) must not mention dj-virtual.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'dangerously',
        url: 'http://localhost/',
    });

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    const warns = [];
    const errors = [];
    dom.window.console = {
        log: () => {},
        warn: (...a) => warns.push(a),
        error: (...a) => errors.push(a),
        groupCollapsed: () => {},
        groupEnd: () => {},
    };

    dom.window.eval(`
        window.WebSocket = class { constructor(){this.readyState=0;} send(){} close(){} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
    `);
    dom.window.eval(clientCode);
    return { dom, warns, errors };
}

/** A [dj-virtual] list tall enough that most rows fall outside the window. */
function virtualListHtml(count) {
    let rows = '';
    for (let i = 0; i < count; i++) {
        rows += `<div data-key="k${i}" dj-id="${100 + i}">row ${i}</div>`;
    }
    return (
        '<div dj-root dj-view="app.V">' +
        `<div id="vl" dj-virtual="rows" dj-virtual-item-height="20" style="height:40px">${rows}</div>` +
        '</div>'
    );
}

const warnText = (warns) => warns.map((a) => a.map(String).join(' ')).join('\n');

describe('#2017 item 5: dj-virtual patch-miss diagnostic', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('exposes _findVirtualListHolding on the public debug surface', () => {
        const { dom } = createEnv(virtualListHtml(30));
        expect(typeof dom.window.djust._findVirtualListHolding).toBe('function');
    });

    it('identifies the container holding a detached off-window item', () => {
        const { dom } = createEnv(virtualListHtml(30));
        dom.window.djust.initVirtualLists();

        // A high-index row is outside the 40px window, so it is detached.
        const held = dom.window.djust._findVirtualListHolding('129');
        expect(held).toBeTruthy();
        expect(held.id).toBe('vl');
    });

    it('returns null for an id no virtual list holds', () => {
        const { dom } = createEnv(virtualListHtml(30));
        dom.window.djust.initVirtualLists();
        expect(dom.window.djust._findVirtualListHolding('99999')).toBeNull();
    });

    it('returns null when the page has no virtual list at all', () => {
        const { dom } = createEnv('<div dj-root dj-view="app.V"><div dj-id="7">x</div></div>');
        expect(dom.window.djust._findVirtualListHolding('7')).toBeNull();
    });

    it('a patch missing an off-window virtual item names dj-virtual in the warning', async () => {
        const { dom, warns } = createEnv(virtualListHtml(30));
        dom.window.djust.initVirtualLists();

        // Patch targets a row that is currently detached (off-window). The
        // path must ALSO be unresolvable: getNodeByPath falls back to the
        // path when the dj-id is absent, and a resolvable path would land on
        // a different node instead of missing (see the PR notes — that
        // fallback landing on the wrong node is its own concern).
        // applyPatches is async — awaiting is what makes the miss warning
        // observable (an un-awaited call reads `warns` before it is written).
        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [0, 99], d: '129', text: 'updated' }],
            null
        );

        const text = warnText(warns);
        expect(text).toMatch(/dj-virtual/i);
        expect(text).toMatch(/vl/);
    });

    it('an ordinary miss with no virtual list does NOT mention dj-virtual', async () => {
        const { dom, warns } = createEnv('<div dj-root dj-view="app.V"><div dj-id="7">x</div></div>');

        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [0, 99], d: '4242', text: 'nope' }],
            null
        );

        const text = warnText(warns);
        // The generic miss warning still fires...
        expect(text).toMatch(/node not found/i);
        // ...but must not blame dj-virtual when nothing is holding the node.
        expect(text).not.toMatch(/dj-virtual/i);
    });
});
