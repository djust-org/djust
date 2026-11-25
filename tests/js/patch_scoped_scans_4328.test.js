/**
 * #4328 (#4001 slices 2 and 3) — every post-patch rescan follows the patch.
 *
 * After an incremental patch, reinitAfterDOMUpdate(scope, changes) visits only
 * what the patch inserted or re-attributed. Each test below inserts an element
 * through a real patch and proves the scan that owns its directive still binds
 * it. Then: no page-wide query runs for an unrelated text patch; a hook gets
 * updated() only when its element or subtree changed (or it asks for every
 * render); a batch applied without a change record (a child or sticky view)
 * forces the next rescan to scan the page; and a root SetText addressed by
 * parent dj-id ignores a nested view's element with the same id.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { withContracts } from './_open_contracts.js';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function env(rootHtml, { handlers = ['close', 'other', 'shown', 'more', 'k'], hooks = '' } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${withContracts(rootHtml, null, handlers)}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    const w = dom.window;
    w.console = { log() {}, info() {}, debug() {}, warn() {}, error() {} };
    w.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
        window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
        window._calls = [];
        window.fetch = async function(url, opts) {
            window._calls.push((opts && opts.headers && opts.headers["X-Djust-Event"]) || "");
            return { ok: true, json: async () => ({ patches: [], version: window._calls.length }) };
        };
        ${hooks}
    `);
    w.eval(clientCode);
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    w.djust._mountReady = true;
    w.djust.reinitAfterDOMUpdate();
    return dom;
}

const vnode = (tag, id, attrs = {}, children = []) => ({ tag, attrs: { 'dj-id': id, ...attrs }, children });
const text = (t) => ({ tag: '#text', text: t, attrs: {}, children: [] });
const PAGE = '<div dj-view="app.V" dj-id="0"><p dj-id="1">a</p><ul dj-id="2"></ul></div>';

/** Insert `node` into the <ul dj-id="2"> by a patch, then rescan with its record. */
async function insert(dom, node) {
    const changes = {};
    const ok = await dom.window.djust.applyPatches(
        [{ type: 'InsertChild', path: [1], d: '2', index: 0, node }], null, null, changes);
    expect(ok).toBe(true);
    expect(changes.scoped).toBe(true);
    dom.window.djust.reinitAfterDOMUpdate(undefined, changes);
    return dom.window.document.querySelector(`[dj-id="${node.attrs['dj-id']}"]`);
}

async function textPatch(dom, t, extra = {}) {
    const changes = {};
    await dom.window.djust.applyPatches(
        [{ type: 'SetText', path: [0, 0], text: t, parent_d: '1', index: 0, ...extra }], null, null, changes);
    return changes;
}

const scopedAttrs = (el) => (el._djustScopedListeners || []).map((e) => e.attrName);
const settle = () => new Promise((r) => setTimeout(r, 20));

describe('#4328 an element a patch inserts is bound by every scoped scan', () => {
    it('dj-poll', async () => {
        const dom = env(PAGE);
        const el = await insert(dom, vnode('li', '9', { 'dj-poll': 'more', 'dj-poll-interval': '60000' }));
        expect(el._djustPollIntervalId).toBeDefined();
        dom.window.close();
    });

    it('dj-click-away, dj-shortcut, dj-mouseenter / dj-mouseleave', async () => {
        const dom = env(PAGE);
        const el = await insert(dom, vnode('li', '9', {
            'dj-click-away': 'close', 'dj-shortcut': 'escape:close',
            'dj-mouseenter': 'other', 'dj-mouseleave': 'other',
        }));
        expect(scopedAttrs(el).sort()).toEqual(['dj-click-away', 'dj-mouseenter', 'dj-mouseleave', 'dj-shortcut']);
    });

    it('dj-loading', async () => {
        const dom = env(PAGE);
        const el = await insert(dom, vnode('li', '9', { 'dj-loading.show': '', 'dj-loading.for': 'more' }));
        expect(dom.window.djust.globalLoadingManager.registeredElements.has(el)).toBe(true);
    });

    it('dj-mounted', async () => {
        const dom = env(PAGE);
        await insert(dom, vnode('li', '9', { 'dj-mounted': 'shown' }));
        await settle();
        expect(dom.window._calls).toContain('shown');
    });

    it('dj-upload and dj-upload-drop', async () => {
        const dom = env(PAGE);
        const input = await insert(dom, vnode('input', '9', { type: 'file', 'dj-upload': 'docs' }));
        const zone = await insert(dom, vnode('li', '8', { 'dj-upload-drop': 'docs' }));
        expect(input._djUploadBound).toBe(true);
        expect(zone._djDropBound).toBe(true);
    });

    it('dj-patch, dj-navigate and aria-current', async () => {
        const dom = env(PAGE);
        const proto = dom.window.EventTarget.prototype;
        const original = proto.addEventListener;
        const seen = [];
        proto.addEventListener = function (type, ...rest) {
            if (this.getAttribute && this.getAttribute('dj-id')) seen.push(`${this.getAttribute('dj-id')}:${type}`);
            return original.call(this, type, ...rest);
        };
        try {
            await insert(dom, vnode('a', '9', { 'dj-patch': '?tab=1' }));
            const nav = await insert(dom, vnode('a', '8', { 'dj-navigate': '/' }));
            expect(nav.getAttribute('aria-current')).toBe('page');
        } finally {
            proto.addEventListener = original;
        }
        expect(seen).toContain('9:click');
        expect(seen).toContain('8:click');
    });

    it('data-dj-native-dropdown', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0" data-component-id="c1"><p dj-id="1">a</p><ul dj-id="2"></ul></div>');
        const seen = [];
        const proto = dom.window.EventTarget.prototype;
        const original = proto.addEventListener;
        proto.addEventListener = function (type, ...rest) {
            if (type === 'toggle') seen.push(this.getAttribute('dj-id'));
            return original.call(this, type, ...rest);
        };
        try {
            await insert(dom, vnode('details', '9', {
                'data-dj-native-dropdown': '', 'data-dj-observe-toggle': 'other',
                'data-dj-observe-lifetime': 'l1', 'data-dj-observe-sequence': '0',
            }));
        } finally {
            proto.addEventListener = original;
        }
        expect(seen).toEqual(['9']);
    });

    it('data-draft-clear on an inserted draft root clears the draft', async () => {
        const dom = env(PAGE);
        const store = dom.window.localStorage;
        store.setItem('djust_draft_k', JSON.stringify({ data: { a: 'x' }, timestamp: Date.now(), format: 2 }));
        await insert(dom, vnode('li', '9', { 'data-draft-enabled': '', 'data-draft-key': 'k', 'data-draft-clear': '' }));
        expect(store.getItem('djust_draft_k')).toBeNull();
    });

    it('a data-draft field it inserts is restored from the saved draft', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0" data-draft-enabled data-draft-key="f"><p dj-id="1">a</p><ul dj-id="2"></ul></div>');
        dom.window.localStorage.setItem('djust_draft_f',
            JSON.stringify({ data: { note: 'saved' }, timestamp: Date.now(), format: 2 }));
        const field = await insert(dom, vnode('input', '9', { name: 'note', 'data-draft': 'true' }));
        expect(field.value).toBe('saved');
    });

    it('a colocated hook script it inserts is registered', async () => {
        const dom = env(PAGE);
        // A VNode cannot carry a <script>; a dj-if subtree's HTML can.
        const changes = {};
        const ok = await dom.window.djust.applyPatches([{
            type: 'InsertSubtree', id: 'if-x-0', path: [1], d: '2', index: 0,
            html: '<!--dj-if id="if-x-0"--><script type="djust/hook" data-hook="Inline">'
                + 'return { mounted() {} };</script><!--/dj-if-->',
        }], null, null, changes);
        expect(ok).toBe(true);
        dom.window.djust.reinitAfterDOMUpdate(undefined, changes);
        const script = dom.window.document.querySelector('script[data-hook="Inline"]');
        expect(script.dataset.djustHookRegistered).toBe('1');
    });

    it('an offline indicator it inserts is synced', async () => {
        const dom = env(PAGE);
        const el = await insert(dom, vnode('span', '9', { class: 'djust-offline-indicator', 'data-online-class': 'up' }));
        expect(el.classList.contains('up')).toBe(true);
    });

    it('dj-scroll-into-view', async () => {
        const dom = env(PAGE);
        const scrolled = [];
        dom.window.Element.prototype.scrollIntoView = function () { scrolled.push(this.getAttribute('dj-id')); };
        await insert(dom, vnode('li', '9', { 'dj-scroll-into-view': 'instant' }));
        expect(scrolled).toEqual(['9']);
    });

    it('dj-viewport-* and dj-virtual containers are set up', async () => {
        const dom = env(PAGE);
        expect(dom.window.djust._infiniteScrollTracked()).toBe(0);
        await insert(dom, vnode('li', '9', { 'dj-viewport-bottom': 'more' }, [vnode('span', '10', {}, [text('r')])]));
        expect(dom.window.djust._infiniteScrollTracked()).toBe(1);
        expect(dom.window.djust._virtualListsTracked()).toBe(0);
        await insert(dom, vnode('div', '8', { 'dj-virtual': 'rows', 'dj-virtual-item-height': '20', style: 'height:100px' },
            [vnode('div', '11', { 'dj-key': 'a' }, [text('a')])]));
        expect(dom.window.djust._virtualListsTracked()).toBe(1);
    });

    it('dj-model on a non-control element is left alone, as by the full scan', async () => {
        const dom = env(PAGE);
        const div = await insert(dom, vnode('div', '9', { 'dj-model.debounce-300': 'x' }));
        expect(div._djustModelBound).toBeUndefined();
        const input = await insert(dom, vnode('input', '8', { 'dj-model.debounce-300': 'y' }));
        expect(input._djustModelBound).toBe(true);
    });
});

describe('#4328 an unrelated patch costs no page-wide query', () => {
    it('a text patch makes no document-wide querySelectorAll in the rescan', async () => {
        const dom = env(PAGE);
        const changes = await textPatch(dom, 'b');
        const calls = [];
        const protos = [dom.window.Element.prototype, dom.window.Document.prototype];
        const originals = protos.map((p) => p.querySelectorAll);
        protos.forEach((p, i) => {
            p.querySelectorAll = function (sel) { calls.push(sel); return originals[i].call(this, sel); };
        });
        try {
            dom.window.djust.reinitAfterDOMUpdate(undefined, changes);
        } finally {
            protos.forEach((p, i) => { p.querySelectorAll = originals[i]; });
        }
        expect(calls).toEqual([]);
    });
});

describe('#4328 hooks hear only of their own changes', () => {
    const HOOKS = `
        window.log = [];
        const def = (name, extra) => Object.assign({
            mounted() { window.log.push(name + ':mounted'); },
            updated() { window.log.push(name + ':updated'); },
            destroyed() { window.log.push(name + ':destroyed'); },
        }, extra);
        window.djust = window.djust || {};
        window.djust.hooks = { A: def('A'), B: def('B'), R: def('R', { updateOn: 'render' }) };
    `;
    const HOOK_PAGE = '<div dj-view="app.V" dj-id="0"><p dj-id="1">a</p><ul dj-id="2"></ul>'
        + '<div dj-id="3" dj-hook="A"><b dj-id="4">x</b></div><div dj-id="5" dj-hook="B"></div>'
        + '<div dj-id="6" dj-hook="R"></div></div>';

    it('an inserted hook mounts; only the changed hook and an every-render hook update', async () => {
        const dom = env(HOOK_PAGE, { hooks: HOOKS });
        const w = dom.window;
        // Let the page's load event run its one deferred hook sweep first.
        await settle();
        w.log.length = 0;
        await insert(dom, vnode('li', '9', { 'dj-hook': 'B' }));
        expect(w.log).toEqual(['B:mounted', 'R:updated']);
        w.log.length = 0;
        const changes = {};
        await w.djust.applyPatches([{ type: 'SetText', path: [2, 0, 0], text: 'y', parent_d: '4', index: 0 }],
            null, null, changes);
        w.djust.reinitAfterDOMUpdate(undefined, changes);
        expect(w.log).toEqual(['A:updated', 'R:updated']);
        w.log.length = 0;
        const attr = {};
        await w.djust.applyPatches([{ type: 'SetAttr', path: [3], d: '5', key: 'data-v', value: '1' }],
            null, null, attr);
        w.djust.reinitAfterDOMUpdate(undefined, attr);
        expect(w.log).toEqual(['B:updated', 'R:updated']);
    });

    it('a hook element a patch removes is destroyed', async () => {
        const dom = env(HOOK_PAGE, { hooks: HOOKS });
        const w = dom.window;
        w.log.length = 0;
        const changes = {};
        await w.djust.applyPatches([{ type: 'RemoveChild', path: [], d: '0', index: 3, child_d: '5' }],
            null, null, changes);
        w.djust.reinitAfterDOMUpdate(undefined, changes);
        expect(w.log).toEqual(['B:destroyed', 'R:updated']);
    });

    it('without a change record every hook is updated, as before', async () => {
        const dom = env(HOOK_PAGE, { hooks: HOOKS });
        dom.window.log.length = 0;
        dom.window.djust.reinitAfterDOMUpdate();
        expect(dom.window.log).toEqual(['A:updated', 'B:updated', 'R:updated']);
    });
});

describe('#4328 batches without a change record force the next full scan', () => {
    it('a dj-window element a child-view batch inserted binds on the next root patch', async () => {
        const dom = env(PAGE);
        const w = dom.window;
        // A child or sticky update applies patches with no collector and no reinit.
        await w.djust.applyPatches([{ type: 'InsertChild', path: [1], d: '2', index: 0,
            node: vnode('li', '9', { 'dj-window-keydown.escape': 'close', 'dj-model': 'q' }) }]);
        const changes = await textPatch(dom, 'b');
        w.djust.reinitAfterDOMUpdate(undefined, changes);
        expect(w.document.querySelector('[dj-id="9"]')._djustModelBound).toBe(true);
        w.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        await settle();
        expect(w._calls).toEqual(['close']);
        // ...once: the flag is spent, and the next patch is scoped again.
        expect(w.djust._fullRescanPending).toBe(false);
    });
});

describe('#4328 SetText by parent dj-id stays in its own view', () => {
    it('a {% live_render %} child before the root text with the same dj-id is not edited', async () => {
        // A child view numbers its dj-ids itself: its <p dj-id="1"> comes first.
        const dom = env('<div dj-view="app.V" dj-id="0">'
            + '<div data-djust-embedded="child" dj-view="app.Child"><p dj-id="1">child</p></div>'
            + '<p dj-id="1">0</p></div>');
        const ok = await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [1, 0], text: '1', parent_d: '1', index: 0 }]);
        expect(ok).toBe(true);
        const ps = dom.window.document.querySelectorAll('p');
        expect(ps[0].textContent).toBe('child');
        expect(ps[1].textContent).toBe('1');
    });

    it('a parent inside a dj-virtual list is left to the path', async () => {
        const dom = env('<div dj-view="app.V" dj-id="0"><div dj-virtual="rows" dj-id="7"><p dj-id="1">r</p></div><p dj-id="8">0</p></div>');
        // parent_d points into the virtual list, path at the root's own <p>: the path wins.
        const ok = await dom.window.djust.applyPatches(
            [{ type: 'SetText', path: [1, 0], text: '1', parent_d: '1', index: 0 }]);
        expect(ok).toBe(true);
        expect(dom.window.document.querySelector('[dj-id="8"]').textContent).toBe('1');
    });
});
