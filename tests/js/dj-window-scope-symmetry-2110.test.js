/**
 * Regression tests for #2110:
 *   the scoped-attr eviction sweep and the refresh scan disagree on SCOPE.
 *
 * - The eviction sweep walks the whole `_scopedRegistry` and keeps any entry
 *   whose element is still `document.contains(...)` with its attribute present.
 * - The refresh scan (#2108) only visits `root` + `root.querySelectorAll('*')`,
 *   where `root = document.querySelector('[dj-view]') || '[dj-root]' || document`.
 *
 * So an entry whose element is still in the document but OUTSIDE the selected
 * root is neither refreshed nor evicted — it keeps dispatching its ORIGINAL
 * handler forever.
 *
 * Two shapes, both reproduced below:
 *   1. An element that leaves the root but stays in the document.
 *   2. Multi-root: a second `[dj-view]` appears EARLIER in document order, so
 *      `querySelector` switches roots and every entry belonging to the old root
 *      goes permanently stale.
 *
 * Surfaced by the Stage 11 review of PR #2109. Strictly narrower than pre-#2109
 * behavior (where every in-place rename was stale), so this is a residual gap,
 * not a regression.
 *
 * Fix shape: the sweep and the scan resolve the governed element set the SAME
 * way — every `[dj-view]`/`[dj-root]` in the document, not just the first.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'dangerously',
        url: 'http://localhost/',
    });

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            window._testFetchCalls.push(eventName);
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

const calls = (dom) => dom.window._testFetchCalls;
const press = (dom) =>
    dom.window.dispatchEvent(
        new dom.window.KeyboardEvent('keydown', { key: 'k', code: 'KeyK', bubbles: true })
    );

describe('#2110: sweep/scan scope symmetry', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('element moved OUT of the root and renamed does not fire the stale handler', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="d" dj-window-keydown="old"></div></div>' +
                '<div id="outside"></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const d = dom.window.document.getElementById('d');
        dom.window.document.getElementById('outside').appendChild(d);
        d.setAttribute('dj-window-keydown', 'renamed');
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        // The element is no longer governed by any LiveView root. It must NOT
        // keep firing the handler the server rendered before it left.
        expect(calls(dom)).not.toContain('old');
    });

    it('multi-root: a second [dj-view] earlier in the document does not orphan the first', async () => {
        // The realistic djust shape is a NESTED child root (live_render /
        // sticky views embed inside the parent), which the outer root's
        // querySelectorAll already covers. This test pins the harder case: a
        // root that is NOT an ancestor of the registered element.
        const dom = createTestEnv('<div id="host"></div><div id="b" dj-view="app.B" dj-window-keydown="bOld"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // A patch inserts another root EARLIER in document order, so
        // querySelector('[dj-view]') now selects it instead of B.
        const host = dom.window.document.getElementById('host');
        host.innerHTML = '<div id="a" dj-view="app.A"></div>';

        // B's own handler is renamed by its own server re-render.
        dom.window.document.getElementById('b').setAttribute('dj-window-keydown', 'bNew');
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        // B must be refreshed, not orphaned at its original value.
        expect(calls(dom)).toEqual(['bNew']);
    });

    it('a second root is scanned, not just the first', async () => {
        // Registration (not just refresh) must reach every root.
        const dom = createTestEnv(
            '<div id="a" dj-view="app.A"></div><div id="b" dj-view="app.B" dj-window-keydown="fromB"></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(calls(dom)).toEqual(['fromB']);
    });

    it('nested child root still works (the common live_render shape)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.Parent">' +
                '<div dj-view data-djust-embedded="child-1" dj-window-keydown="childKey"></div>' +
                '</div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        // Exactly once — the nested root must not be scanned twice.
        expect(calls(dom)).toEqual(['childKey']);
    });

    it('no double-fire when an element is reachable from two roots', async () => {
        // A nested root's descendants are reachable via BOTH the outer root's
        // querySelectorAll and the inner root's own scan.
        const dom = createTestEnv(
            '<div dj-view="app.Parent">' +
                '<div dj-view data-djust-embedded="c1">' +
                '<div id="deep" dj-window-keydown="deepKey"></div>' +
                '</div>' +
                '</div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(calls(dom)).toEqual(['deepKey']);
    });
});
