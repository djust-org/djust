/**
 * Regression tests for #2113:
 *   a patch whose dj-id is absent because a [dj-virtual] list holds the item
 *   DETACHED falls through to positional-path resolution and lands on a
 *   DIFFERENT node — silently.
 *
 * `getNodeByPath` resolves by dj-id first and falls back to the path when the
 * id is not found (12-vdom-patch.js). That fallback is correct for its original
 * purpose: an id-less patch, or a node whose id changed. It is WRONG when the
 * id is absent specifically because the element is detached off-window — the
 * server addressed a known item, and silently retargeting a different element
 * corrupts the render.
 *
 * Severity: this is worse than the miss #2114 diagnoses. A miss is loud and
 * drops the update; this is silent and applies it to the wrong row.
 * `applyPatches` even returns true.
 *
 * Contract pinned here:
 *   - A patch for a detached virtual item does NOT mutate any other node.
 *   - It is reported as a miss (so #2114's diagnostic explains why).
 *   - The path fallback is otherwise UNCHANGED — id-less patches and ordinary
 *     stale-id patches must still resolve by path, or this fix would break the
 *     resilience the fallback exists to provide.
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
    dom.window.console = {
        log: () => {},
        warn: (...a) => warns.push(a),
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
    return { dom, warns };
}

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

describe('#2113: path-fallback must not retarget a detached virtual item', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('does NOT apply the patch to a different node (THE BUG)', async () => {
        const { dom } = createEnv(virtualListHtml(30));
        dom.window.djust.initVirtualLists();

        // dj-id 129 is off-window, so detached.
        expect(dom.window.djust._findVirtualListHolding('129')).toBeTruthy();

        // Snapshot every attached row's text before the patch.
        const before = Array.from(dom.window.document.querySelectorAll('[dj-id]')).map(
            (el) => el.textContent
        );

        // path [0,0] IS resolvable — that is what makes the bug silent.
        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [0, 0], d: '129', text: 'CORRUPTED' }],
            null
        );

        const after = Array.from(dom.window.document.querySelectorAll('[dj-id]')).map(
            (el) => el.textContent
        );
        expect(after).toEqual(before);
        expect(dom.window.document.body.textContent).not.toContain('CORRUPTED');
    });

    it('reports it as a miss so the #2114 diagnostic can explain it', async () => {
        const { dom, warns } = createEnv(virtualListHtml(30));
        dom.window.djust.initVirtualLists();

        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [0, 0], d: '129', text: 'x' }],
            null
        );

        const text = warns.map((a) => a.map(String).join(' ')).join('\n');
        expect(text).toMatch(/node not found/i);
        expect(text).toMatch(/dj-virtual/i);
    });

    // --- the fallback must otherwise keep working ---

    it('an id-less patch still resolves by path', async () => {
        const { dom } = createEnv(
            '<div dj-root dj-view="app.V"><div dj-id="1">a</div><div dj-id="2">b</div></div>'
        );
        await dom.window.djust.applyPatches([{ type: 'SetText', path: [0], text: 'patched' }], null);
        expect(dom.window.document.body.textContent).toContain('patched');
    });

    it('a stale id with no virtual list still falls back to path', async () => {
        // The resilience the fallback exists for: id changed, path still valid.
        const { dom } = createEnv(
            '<div dj-root dj-view="app.V"><div dj-id="1">a</div><div dj-id="2">b</div></div>'
        );
        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [0], d: 'gone-id', text: 'fellback' }],
            null
        );
        expect(dom.window.document.body.textContent).toContain('fellback');
    });

    it('a held id whose fallback lands OUTSIDE the list still falls back', async () => {
        // The over-suppression case. The list holds 129 detached, but this
        // patch's path resolves to an unrelated live sibling elsewhere in the
        // tree — nothing to do with the virtual list. Suppressing on the id
        // alone would drop a patch main applies correctly.
        //
        // dj-ids are purely positional (crates/djust_vdom/src/lib.rs), so a
        // drifted id colliding with one a windowed list still holds is
        // routine, not exotic.
        let rows = '';
        for (let i = 0; i < 30; i++) {
            rows += `<div data-key="k${i}" dj-id="${100 + i}">row ${i}</div>`;
        }
        const { dom } = createEnv(
            '<div dj-root dj-view="app.V">' +
                `<div id="vl" dj-virtual="rows" dj-virtual-item-height="20" style="height:40px">${rows}</div>` +
                '<div id="sibling">untouched</div>' +
                '</div>'
        );
        dom.window.djust.initVirtualLists();
        expect(dom.window.djust._findVirtualListHolding('129')).toBeTruthy();

        // From dj-root: child 0 is the list itself, child 1 is the sibling —
        // so path [1] lands OUTSIDE the virtual container.
        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [1], d: '129', text: 'FELLBACK' }],
            null
        );

        expect(dom.window.document.getElementById('sibling').textContent).toBe('FELLBACK');
    });

    it('a virtual list present but NOT holding the id still falls back', async () => {
        // Only a detached item the list actually holds should suppress the
        // fallback — otherwise merely having a virtual list on the page would
        // disable path resilience everywhere.
        const { dom } = createEnv(
            virtualListHtml(30).replace('</div></div>', '</div><div dj-id="9">tail</div></div>')
        );
        dom.window.djust.initVirtualLists();
        expect(dom.window.djust._findVirtualListHolding('unknown-id')).toBeNull();

        await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [0, 0], d: 'unknown-id', text: 'fellback2' }],
            null
        );
        expect(dom.window.document.body.textContent).toContain('fellback2');
    });
});
