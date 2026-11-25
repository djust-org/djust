/**
 * #4001 — client work per patch proportional to the change, not the page.
 *
 * 1. `applyPatches(patches, rootEl, applyIf, changes)` fills `changes` with the
 *    inserted / removed roots, changed attributes and text-only updates, and
 *    still resolves to its boolean.
 * 2. `reinitAfterDOMUpdate(scope, changes)` scans only those for dj-window-* /
 *    dj-document-* declarations and dj-model bindings: no page-wide
 *    `querySelectorAll('*')`, and an element outside the change is not visited.
 *    A change that adds or removes a LiveView root falls back to the full scan.
 * 3. A `SetText` with `parent_d` + `index` resolves its text node by the
 *    parent's dj-id, without walking `path`; one without them (the wire before
 *    #4001) or whose parent is gone still resolves by `path`.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { withContracts } from './_open_contracts.js';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function env(rootHtml, handlers = ['close', 'other']) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${withContracts(rootHtml, null, handlers)}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    const w = dom.window;
    w.console = { log() {}, info() {}, debug() {}, warn() {}, error() {} };
    w.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window._calls = [];
        window.fetch = async function(url, opts) {
            window._calls.push((opts && opts.headers && opts.headers["X-Djust-Event"]) || "");
            return { ok: true, json: async () => ({ patches: [], version: window._calls.length }) };
        };
    `);
    w.eval(clientCode);
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    w.djust.reinitAfterDOMUpdate();
    return dom;
}

/** Apply `patches` the way the response handler does; returns [ok, changes]. */
async function patch(dom, patches) {
    const changes = {};
    const ok = await dom.window.djust.applyPatches(patches, null, null, changes);
    return [ok, changes];
}

/** Record every `querySelectorAll('*')` made while `fn` runs. */
function starScans(dom, fn) {
    const w = dom.window;
    const scans = [];
    const protos = [w.Element.prototype, w.Document.prototype];
    const originals = protos.map((p) => p.querySelectorAll);
    protos.forEach((p, i) => {
        p.querySelectorAll = function (sel) {
            if (sel === '*') scans.push(this);
            return originals[i].call(this, sel);
        };
    });
    try { fn(); } finally { protos.forEach((p, i) => { p.querySelectorAll = originals[i]; }); }
    return scans;
}

const vnode = (tag, id, attrs = {}, children = []) => ({ tag, attrs: { 'dj-id': id, ...attrs }, children });
const text = (t) => ({ tag: '#text', text: t, attrs: {}, children: [] });

async function escape(dom) {
    dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    return dom.window._calls;
}

describe('#4001 applyPatches reports what it changed', () => {
    it('fills inserted (outermost), removed, attrs and text; returns its boolean', async () => {
        const dom = env(
            '<div dj-view="app.V" dj-id="0"><p dj-id="1">a</p><ul dj-id="2"><li dj-id="3">x</li></ul></div>'
        );
        const doc = dom.window.document;
        const [ok, changes] = await patch(dom, [
            { type: 'SetText', path: [0, 0], text: 'b', parent_d: '1', index: 0 },
            { type: 'SetAttr', path: [1], d: '2', key: 'class', value: 'k' },
            { type: 'RemoveChild', path: [1], d: '2', index: 0, child_d: '3' },
            {
                type: 'InsertChild', path: [1], d: '2', index: 0,
                node: vnode('li', '4', {}, [vnode('span', '5', {}, [text('y')])]),
            },
        ]);
        expect(ok).toBe(true);
        expect(changes.scoped).toBe(true);
        expect(changes.inserted).toEqual([doc.querySelector('[dj-id="4"]')]);
        expect(changes.removed.map((n) => n.getAttribute('dj-id'))).toEqual(['3']);
        expect([...changes.attrs.keys()]).toEqual([doc.querySelector('[dj-id="2"]')]);
        expect([...changes.attrs.get(doc.querySelector('[dj-id="2"]'))]).toEqual(['class']);
        expect(changes.text).toBeGreaterThan(0);
        expect(doc.querySelector('[dj-id="1"]').textContent).toBe('b');
    });

    it('a failed batch still resolves false', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><p dj-id="1">a</p></div>');
        const [ok] = await patch(dom, [{ type: 'SetAttr', path: [9, 9], d: 'nope', key: 'a', value: 'b' }]);
        expect(ok).toBe(false);
    });
});

describe('#4001 rescans are scoped to the change', () => {
    it('an inserted dj-window-* element registers without a page-wide scan', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><ul dj-id="2"></ul></div>');
        const [, changes] = await patch(dom, [{
            type: 'InsertChild', path: [0], d: '2', index: 0,
            node: vnode('li', '4', { 'dj-window-keydown.escape': 'close' }),
        }]);
        const li = dom.window.document.querySelector('[dj-id="4"]');
        const scans = starScans(dom, () => dom.window.djust.reinitAfterDOMUpdate(undefined, changes));
        expect(scans).toEqual([li]);
        expect(await escape(dom)).toEqual(['close']);
    });

    it('an element outside the change is not visited', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><p dj-id="1">a</p><b id="out"></b></div>');
        // Set behind the patch path: the scoped rescan has no reason to visit it.
        dom.window.document.getElementById('out').setAttribute('dj-window-keydown.escape', 'close');
        const [, changes] = await patch(dom, [{ type: 'SetText', path: [0, 0], text: 'b', parent_d: '1', index: 0 }]);
        const scans = starScans(dom, () => dom.window.djust.reinitAfterDOMUpdate(undefined, changes));
        expect(scans).toEqual([]);
        // A full rescan (no change record) walks the page and finds it.
        const root = dom.window.document.querySelector('[dj-view]');
        expect(starScans(dom, () => dom.window.djust.reinitAfterDOMUpdate())).toContain(root);
        expect(await escape(dom)).toEqual(['close']);
    });

    it('a changed attribute value re-registers on its element only', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><b dj-id="1" dj-window-keydown.escape="close"></b></div>');
        const [, changes] = await patch(dom, [
            { type: 'SetAttr', path: [0], d: '1', key: 'dj-window-keydown.escape', value: 'other' },
        ]);
        const scans = starScans(dom, () => dom.window.djust.reinitAfterDOMUpdate(undefined, changes));
        expect(scans).toEqual([]);
        expect(await escape(dom)).toEqual(['other']);
    });

    it('a change that inserts a LiveView root scans the page', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><ul dj-id="2"></ul></div>');
        const [, changes] = await patch(dom, [{
            type: 'InsertChild', path: [0], d: '2', index: 0,
            node: vnode('li', '4', { 'dj-view': 'app.Child' }),
        }]);
        const root = dom.window.document.querySelector('[dj-view="app.V"]');
        const scans = starScans(dom, () => dom.window.djust.reinitAfterDOMUpdate(undefined, changes));
        expect(scans).toContain(root);
    });

    it('dj-model binds an inserted input and rebinds a changed one', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><ul dj-id="2"></ul><input dj-id="1" dj-model="a"></div>');
        const doc = dom.window.document;
        const old = doc.querySelector('[dj-id="1"]');
        const oldHandler = old._djustModelHandler;
        expect(old._djustModelSourceValue).toBe('a');
        const [, changes] = await patch(dom, [
            { type: 'InsertChild', path: [0], d: '2', index: 0, node: vnode('input', '4', { 'dj-model': 'b' }) },
            { type: 'SetAttr', path: [1], d: '1', key: 'dj-model', value: 'c' },
        ]);
        const scans = starScans(dom, () => dom.window.djust.reinitAfterDOMUpdate(undefined, changes));
        const inserted = doc.querySelector('[dj-id="4"]');
        expect(scans).toEqual([inserted]);
        expect(inserted._djustModelBound).toBe(true);
        expect(inserted._djustModelSourceValue).toBe('b');
        expect(old._djustModelSourceValue).toBe('c');
        expect(old._djustModelHandler).not.toBe(oldHandler);
    });
});

describe('#4001 SetText by parent dj-id + index', () => {
    const page = '<div dj-view="app.V" dj-id="0"><p dj-id="1">x <i dj-id="2">y</i> z</p></div>';

    it('resolves by parent_d + index, ignoring a path that points elsewhere', async () => {
        const dom = env(page);
        // path [9, 9] resolves nothing: only parent_d + index can land it.
        const [ok] = await patch(dom, [{ type: 'SetText', path: [9, 9], text: 'Z', parent_d: '1', index: 2 }]);
        expect(ok).toBe(true);
        expect(dom.window.document.querySelector('[dj-id="1"]').lastChild.textContent).toBe('Z');
    });

    it('a SetText without the new fields still resolves by path', async () => {
        const dom = env(page);
        const [ok] = await patch(dom, [{ type: 'SetText', path: [0, 0], text: 'X ' }]);
        expect(ok).toBe(true);
        expect(dom.window.document.querySelector('[dj-id="1"]').firstChild.textContent).toBe('X ');
    });

    it('falls back to path when the parent is missing or the index is not a text node', async () => {
        const dom = env(page);
        const [ok] = await patch(dom, [
            { type: 'SetText', path: [0, 0], text: 'A ', parent_d: 'gone', index: 0 },
            { type: 'SetText', path: [0, 2], text: ' C', parent_d: '1', index: 1 },
        ]);
        expect(ok).toBe(true);
        const p = dom.window.document.querySelector('[dj-id="1"]');
        expect(p.firstChild.textContent).toBe('A ');
        expect(p.lastChild.textContent).toBe(' C');
        expect(p.querySelector('i').textContent).toBe('y');
    });
});
