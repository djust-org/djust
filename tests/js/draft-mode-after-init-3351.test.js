/**
 * #3351: initDraftMode ran only at init. A `data-draft` field a patch inserted
 * saved nothing, a field replaced by a new element stopped saving, and a draft
 * root inside a lazily hydrated view was never wired. These run the real bundle
 * (client.js) in jsdom, through reinitAfterDOMUpdate, the funnel every patch,
 * lazy hydration and live_redirect goes through.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const FORM = (key, fields) =>
    `<form id="f" data-draft-enabled data-draft-key="${key}">${fields}</form>`;

// djustInit is queued as a microtask by the bundle: wait for its own flag.
async function initialized(window) {
    for (let i = 0; i < 200 && !window.djustInitialized; i++) {
        await new Promise((resolve) => setTimeout(resolve, 5));
    }
    expect(window.djustInitialized).toBe(true);
}

// Boot the bundle on `bodyHtml`, with `saved` already in localStorage under the
// draft key. The draft manager's 500 ms debounce is held on a queue the test
// flushes, so a test decides when a save lands.
async function boot(bodyHtml, saved = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    for (const [key, data] of Object.entries(saved)) {
        window.localStorage.setItem(`djust_draft_${key}`, JSON.stringify({ data, timestamp: 1 }));
    }

    const queued = new Map();
    let nextId = 1e6;
    const realSetTimeout = window.setTimeout.bind(window);
    const realClearTimeout = window.clearTimeout.bind(window);
    window.setTimeout = (fn, ms, ...args) => {
        if (ms !== 500) return realSetTimeout(fn, ms, ...args);
        const id = nextId++;
        queued.set(id, fn);
        return id;
    };
    window.clearTimeout = (id) => {
        if (queued.delete(id)) return;
        realClearTimeout(id);
    };

    // Every listener the bundle puts on the document, to count draft wiring.
    const documentListeners = [];
    const realAdd = window.document.addEventListener.bind(window.document);
    window.document.addEventListener = (type, ...rest) => {
        documentListeners.push(type);
        return realAdd(type, ...rest);
    };

    try { window.eval(clientCode); } catch (e) { /* client.js may throw on missing DOM APIs */ }
    await initialized(window);

    const setItem = vi.spyOn(window.Storage.prototype, 'setItem');
    return {
        window,
        document: window.document,
        setItem,
        documentListeners,
        flush() { for (const [id, fn] of [...queued]) { queued.delete(id); fn(); } },
        stored(key) {
            const raw = window.localStorage.getItem(`djust_draft_${key}`);
            return raw ? JSON.parse(raw).data : null;
        },
        type(field, value) {
            if (field.type === 'checkbox') field.checked = value;
            else field.value = value;
            field.dispatchEvent(new window.Event('input', { bubbles: true }));
        },
    };
}

const text = (name) => `<input type="text" name="${name}" data-draft="true">`;

describe('draft mode: fields that arrive after init (#3351)', () => {
    it('saves a field a patch inserted', async () => {
        const env = await boot(FORM('k', text('a') + '<div id="slot"></div>'));
        env.document.getElementById('slot').innerHTML = text('late');
        env.window.djust.reinitAfterDOMUpdate();

        env.type(env.document.querySelector('[name="late"]'), 'typed late');
        env.flush();

        expect(env.stored('k')).toEqual({ a: '', late: 'typed late' });
    });

    it('keeps saving the original fields alongside it', async () => {
        const env = await boot(FORM('k', text('a') + '<div id="slot"></div>'));
        env.document.getElementById('slot').innerHTML = text('late');
        env.window.djust.reinitAfterDOMUpdate();

        env.type(env.document.querySelector('[name="a"]'), 'first');
        env.flush();

        expect(env.stored('k')).toEqual({ a: 'first', late: '' });
    });

    it('saves a field that was replaced by a new element', async () => {
        const env = await boot(FORM('k', text('a')));
        const old = env.document.querySelector('[name="a"]');
        const fresh = env.document.createElement('textarea');
        fresh.name = 'a';
        fresh.setAttribute('data-draft', 'true');
        old.replaceWith(fresh); // a morph that changes the tag
        env.window.djust.reinitAfterDOMUpdate();

        env.type(fresh, 'in the replacement');
        env.flush();

        expect(env.stored('k')).toEqual({ a: 'in the replacement' });
    });

    it('wires a draft root that a lazily hydrated view brought in', async () => {
        const env = await boot('<div id="lazy" dj-view="app.W"></div>', { late_key: { a: 'saved' } });
        const lazy = env.document.getElementById('lazy');
        lazy.innerHTML = FORM('late_key', text('a'));

        env.window.djust.reinitAfterDOMUpdate(lazy);
        const field = env.document.querySelector('[name="a"]');
        expect(field.value).toBe('saved'); // restored

        env.type(field, 'edited');
        env.flush();
        expect(env.stored('late_key')).toEqual({ a: 'edited' });
    });

    it('saves each root under its own key and from its own fields', async () => {
        const env = await boot(
            FORM('one', text('a')) +
            '<section id="two" data-draft-enabled data-draft-key="two">' + text('b') + '</section>');

        env.type(env.document.querySelector('[name="b"]'), 'bee');
        env.flush();

        expect(env.stored('two')).toEqual({ b: 'bee' });
        expect(env.stored('one')).toBe(null);
    });
});

describe('draft mode: restoring into a field that arrives after init (#3351)', () => {
    it('restores the saved value into an inserted field, once', async () => {
        const env = await boot(FORM('k', '<div id="slot"></div>'), { k: { late: 'from storage' } });
        env.document.getElementById('slot').innerHTML = text('late');
        const field = env.document.querySelector('[name="late"]');

        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('from storage');

        // The user clears it; later patches must not put the old value back.
        field.value = '';
        env.window.djust.reinitAfterDOMUpdate();
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('');
    });

    it('never overwrites a field the user is typing in', async () => {
        const env = await boot(FORM('k', '<div id="slot"></div>'), { k: { late: 'from storage' } });
        env.document.getElementById('slot').innerHTML = text('late');
        const field = env.document.querySelector('[name="late"]');
        field.focus();
        field.value = 'half typed';

        env.window.djust.reinitAfterDOMUpdate();

        expect(field.value).toBe('half typed');
        // ... and it does not land on it after focus moves away either
        field.blur();
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('half typed');
    });

    it('never overwrites an edited field, focused or not', async () => {
        const env = await boot(
            FORM('k', text('a') + '<div id="slot"></div>'), { k: { a: 'old' } });
        const field = env.document.querySelector('[name="a"]');
        env.type(field, 'new');
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('new');
    });

    it('restores a replacement element from a save the debounce has not written yet', async () => {
        const env = await boot(FORM('k', text('a')), { k: { a: 'stale' } });
        const old = env.document.querySelector('[name="a"]');
        env.type(old, 'just typed'); // save queued, storage still holds 'stale'
        const fresh = env.document.createElement('textarea');
        fresh.name = 'a';
        fresh.setAttribute('data-draft', 'true');
        old.replaceWith(fresh);

        env.window.djust.reinitAfterDOMUpdate();

        expect(fresh.value).toBe('just typed');
    });

    it('restores checkboxes by checked', async () => {
        const env = await boot(
            FORM('k', '<div id="slot"></div>'), { k: { agree: true } });
        env.document.getElementById('slot').innerHTML =
            '<input type="checkbox" name="agree" data-draft="true">';
        env.window.djust.reinitAfterDOMUpdate();
        expect(env.document.querySelector('[name="agree"]').checked).toBe(true);
    });
});

describe('draft mode: fields present at init behave as before (#3351)', () => {
    it('restores every field at page load', async () => {
        const env = await boot(
            FORM('k', text('a') + '<textarea name="b" data-draft="true"></textarea>' +
                '<input type="checkbox" name="c" data-draft="true">'),
            { k: { a: 'A', b: 'B', c: true } });
        const d = env.document;
        expect(d.querySelector('[name="a"]').value).toBe('A');
        expect(d.querySelector('[name="b"]').value).toBe('B');
        expect(d.querySelector('[name="c"]').checked).toBe(true);
    });

    it('saves what is typed, debounced into one write', async () => {
        const env = await boot(FORM('k', text('a') + text('b')));
        const a = env.document.querySelector('[name="a"]');
        env.type(a, '1');
        env.type(a, '12');
        env.type(a, '123');
        expect(env.setItem).not.toHaveBeenCalled();
        env.flush();
        expect(env.setItem).toHaveBeenCalledTimes(1);
        expect(env.stored('k')).toEqual({ a: '123', b: '' });
    });

    it('ignores fields that do not carry data-draft', async () => {
        const env = await boot(FORM('k', text('a') + '<input name="other">'));
        env.type(env.document.querySelector('[name="other"]'), 'x');
        env.flush();
        expect(env.setItem).not.toHaveBeenCalled();
    });

    it('never collects an unsafe field name', async () => {
        const env = await boot(FORM('k', text('a') + text('__proto__')));
        env.type(env.document.querySelector('[name="a"]'), 'x');
        env.flush();
        expect(Object.keys(env.stored('k'))).toEqual(['a']);
    });
});

describe('draft mode: re-initialising is idempotent (#3351)', () => {
    it('installs one listener per event type on the document, however many updates run', async () => {
        const env = await boot(FORM('k', text('a')));
        const count = (type) => env.documentListeners.filter((t) => t === type).length;
        const input = count('input');
        const change = count('change');
        expect(input).toBeGreaterThanOrEqual(1);
        for (let i = 0; i < 6; i++) env.window.djust.reinitAfterDOMUpdate();
        expect(count('input')).toBe(input);
        expect(count('change')).toBe(change);
    });

    it('writes once per edit after many re-inits', async () => {
        const env = await boot(FORM('k', text('a') + '<div id="slot"></div>'));
        env.document.getElementById('slot').innerHTML = text('late');
        for (let i = 0; i < 6; i++) env.window.djust.reinitAfterDOMUpdate();

        env.type(env.document.querySelector('[name="late"]'), 'v');
        env.flush();

        expect(env.setItem).toHaveBeenCalledTimes(1);
    });

    it('keeps its state off the DOM, so a morph cannot reset it', async () => {
        const env = await boot(FORM('k', '<div id="slot"></div>'), { k: { late: 'saved' } });
        env.document.getElementById('slot').innerHTML = text('late');
        const field = env.document.querySelector('[name="late"]');
        const before = field.getAttributeNames().sort();
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.getAttributeNames().sort()).toEqual(before);

        // A morph rewrote the element's attributes; the value must stay the user's.
        field.value = 'mine';
        for (const name of field.getAttributeNames()) {
            if (name !== 'name' && name !== 'data-draft' && name !== 'type') field.removeAttribute(name);
        }
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('mine');
    });

    it('saves a field of a root that has data-draft-clear, after the clear', async () => {
        const env = await boot(
            '<form id="f" data-draft-enabled data-draft-key="k" data-draft-clear>' + text('a') + '</form>',
            { k: { a: 'old' } });
        expect(env.stored('k')).toBe(null); // the page-load clear
        env.type(env.document.querySelector('[name="a"]'), 'new');
        env.flush();
        expect(env.stored('k')).toEqual({ a: 'new' });
    });
});

// The page-load mount morphs the HTTP-prerendered DOM against the server's HTML
// (#1610), which writes the server's value into every field the user is not in:
// the restore done at init was undone by it, so a saved draft never reached a
// LiveView page's static fields. Driven through the real mount handler.
describe('draft mode: the mount morph does not undo the restore (#3351)', () => {
    const markup = FORM('k', text('a') + text('b'));

    async function mounted(saved, typeBeforeMount) {
        const dom = new JSDOM(
            `<div dj-root dj-view="test.Editor">${markup}</div>`,
            { runScripts: 'dangerously', url: 'http://localhost/' });
        const w = dom.window;
        w.DJUST_USE_WEBSOCKET = false;
        w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        w.localStorage.setItem('djust_draft_k', JSON.stringify({ data: saved, timestamp: 1 }));
        w.eval(clientCode);
        w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
        await initialized(w);
        const socket = new w.djust.LiveViewWebSocket();
        socket.primaryViewPath = 'test.Editor';
        socket.skipMountHtml = true;
        if (typeBeforeMount) typeBeforeMount(w);
        await socket.handleMessage({ type: 'mount', view: 'test.Editor', html: markup, has_ids: true });
        for (let i = 0; i < 10; i++) await new Promise((resolve) => w.setTimeout(resolve, 0));
        return { dom, w };
    }

    it('keeps the saved draft in a static field after the prerender morph', async () => {
        const { dom, w } = await mounted({ a: 'saved a', b: 'saved b' });
        try {
            expect(w.document.querySelector('[name="a"]').value).toBe('saved a');
            expect(w.document.querySelector('[name="b"]').value).toBe('saved b');
        } finally { dom.window.close(); }
    });

    it('keeps what the user typed before the mount over the saved draft', async () => {
        const { dom, w } = await mounted({ a: 'saved a', b: 'saved b' }, (win) => {
            const a = win.document.querySelector('[name="a"]');
            a.value = 'typed a';
            a.dispatchEvent(new win.Event('input', { bubbles: true }));
        });
        try {
            // The morph writes the server's '' into `a`; the restore must not
            // replace the user's text with the stale draft either way: `a` is
            // an edited field, so it is skipped and `b` is restored.
            expect(w.document.querySelector('[name="b"]').value).toBe('saved b');
            expect(w.document.querySelector('[name="a"]').value).not.toBe('saved a');
        } finally { dom.window.close(); }
    });
});

describe('draft mode: a mount of another container leaves the page alone (#3351)', () => {
    const widget = '<div id="w1" dj-view="test.W" data-djust-target="w1"><p id="ph">placeholder</p></div>';
    const page = FORM('k', '<input type="text" name="title" id="title" data-draft="true" value="">') + widget;

    async function booted() {
        const dom = new JSDOM(
            `<div dj-root dj-view="test.Editor">${page}</div>`,
            { runScripts: 'dangerously', url: 'http://localhost/' });
        const w = dom.window;
        w.DJUST_USE_WEBSOCKET = false;
        w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        w.localStorage.setItem('djust_draft_k', JSON.stringify({ data: { title: 'olddraft' }, timestamp: 1 }));
        w.eval(clientCode);
        w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
        await initialized(w);
        const socket = new w.djust.LiveViewWebSocket();
        socket.primaryViewPath = 'test.Editor';
        return { dom, w, socket };
    }
    const settle = async (w) => {
        for (let i = 0; i < 10; i++) await new Promise((resolve) => w.setTimeout(resolve, 0));
    };

    it('does not revert a field the server has set when a lazy view mounts', async () => {
        const { dom, w, socket } = await booted();
        try {
            const title = w.document.querySelector('#title');
            expect(title.value).toBe('olddraft'); // page load: the draft wins
            title.value = 'SERVER'; // a handler of the page view set it; no user edit
            await socket.handleMessage({
                type: 'mount', view: 'test.W', target_id: 'w1', version: 1,
                html: '<p dj-id="1">widget</p>', has_ids: true,
            });
            await settle(w);
            expect(w.document.querySelector('#w1').textContent).toContain('widget'); // it did mount
            expect(title.value).toBe('SERVER');
        } finally { dom.window.close(); }
    });

    it('does not put a draft over the server value on a reconnect mount', async () => {
        const { dom, w, socket } = await booted();
        try {
            const title = w.document.querySelector('#title');
            title.value = 'SERVER';
            w.djust._isReconnect = true;
            socket.skipMountHtml = true;
            await socket.handleMessage({
                type: 'mount', view: 'test.Editor', has_ids: true,
                html: FORM('k', '<input type="text" name="title" id="title" data-draft="true" value="SERVER">') + widget,
            });
            await settle(w);
            expect(w.document.querySelector('#title').value).toBe('SERVER');
        } finally { dom.window.close(); }
    });
});

describe('draft mode: a slot restores its draft on its first mount only (#3351)', () => {
    const inner = FORM('slotk', '<input type="text" name="sf" id="sf" data-draft="true" value="">');
    const frame = (html) => ({
        type: 'mount', view: 'test.W', target_id: 'w1', version: 1, html, has_ids: true,
    });

    async function booted() {
        const dom = new JSDOM(
            `<div dj-root dj-view="test.Editor"><p>page</p></div>` +
            `<div id="w1" dj-view="test.W" data-djust-target="w1">${inner}</div>`,
            { runScripts: 'dangerously', url: 'http://localhost/' });
        const w = dom.window;
        w.DJUST_USE_WEBSOCKET = false;
        w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        w.localStorage.setItem('djust_draft_slotk', JSON.stringify({ data: { sf: 'olddraft' }, timestamp: 1 }));
        w.eval(clientCode);
        w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
        await initialized(w);
        const socket = new w.djust.LiveViewWebSocket();
        socket.primaryViewPath = 'test.Editor';
        return { dom, w, socket };
    }
    const settle = async (w) => {
        for (let i = 0; i < 10; i++) await new Promise((resolve) => w.setTimeout(resolve, 0));
    };

    it('restores on the first mount, keeps the server value on a later one (the flag is already clear)', async () => {
        const { dom, w, socket } = await booted();
        try {
            const sf = () => w.document.querySelector('#sf');
            await socket.handleMessage(frame(inner));
            await settle(w);
            expect(sf().value).toBe('olddraft'); // first mount: the draft wins

            sf().value = 'SERVER'; // a handler of the slot's view set it
            expect(w.djust._isReconnect).toBe(false); // _processAutoRecover has cleared it
            await socket.handleMessage(frame(inner.replace('value=""', 'value="SERVER"'))); // re-mount after a reconnect
            await settle(w);
            expect(sf().value).toBe('SERVER');
        } finally { dom.window.close(); }
    });
});

describe('draft mode: a removed slot that comes back mounts as new (#3351)', () => {
    const inner = FORM('slotk', '<input type="text" name="sf" id="sf" data-draft="true" value="">');
    const frame = (html) => ({
        type: 'mount', view: 'test.W', target_id: 'w1', version: 1, html, has_ids: true,
    });
    const container = (w) => {
        const el = w.document.createElement('div');
        el.id = 'w1';
        el.setAttribute('dj-view', 'test.W');
        el.setAttribute('data-djust-target', 'w1');
        el.innerHTML = inner;
        return el;
    };

    it('restores its draft again after the slot was unmounted and re-inserted', async () => {
        const dom = new JSDOM(
            `<div dj-root dj-view="test.Editor"><p>page</p></div>`,
            { runScripts: 'dangerously', url: 'http://localhost/' });
        const w = dom.window;
        try {
            w.DJUST_USE_WEBSOCKET = false;
            w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
            w.localStorage.setItem('djust_draft_slotk', JSON.stringify({ data: { sf: 'olddraft' }, timestamp: 1 }));
            w.eval(clientCode);
            w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
            await initialized(w);
            const socket = new w.djust.LiveViewWebSocket();
            socket.primaryViewPath = 'test.Editor';
            const settle = async () => {
                for (let i = 0; i < 10; i++) await new Promise((resolve) => w.setTimeout(resolve, 0));
            };

            w.document.body.appendChild(container(w));
            await socket.handleMessage(frame(inner));
            await settle();
            expect(w.document.querySelector('#sf').value).toBe('olddraft');

            // The view's container leaves the page and the client confirms it.
            w.document.getElementById('w1').remove();
            expect(socket.unmountView('w1')).toBe(false); // socket is down: nothing to send, slot forgotten
            expect(w.djust.viewSlots.mounted()).toEqual([]);

            // Later the same id comes back (another step of the page): a patch
            // inserts its container, then its mount morphs it against the
            // server's HTML.
            w.document.body.appendChild(container(w));
            w.djust.reinitAfterDOMUpdate();
            expect(w.document.querySelector('#sf').value).toBe('olddraft'); // first appearance
            await socket.handleMessage(frame(inner.replace('value=""', 'value="SERVER"')));
            await settle();
            expect(w.document.querySelector('#sf').value).toBe('olddraft'); // a first mount again
        } finally { dom.window.close(); }
    });
});

describe('draft mode: a draft keeps the fields the page no longer holds (#3351)', () => {
    const step1 = text('s1');
    const step2 = text('s2');

    it('keeps step 1 when step 2 is typed, restores it on the way back, and a clear removes all', async () => {
        const env = await boot(FORM('k', step1));
        const form = env.document.getElementById('f');

        env.type(env.document.querySelector('[name="s1"]'), 'one');
        env.flush();
        expect(env.stored('k')).toEqual({ s1: 'one' });

        form.innerHTML = step2; // next: step 2 replaces step 1
        env.window.djust.reinitAfterDOMUpdate();
        env.type(env.document.querySelector('[name="s2"]'), 'two');
        env.flush();
        expect(env.stored('k')).toEqual({ s1: 'one', s2: 'two' });

        form.innerHTML = step1; // back: a new step 1 element
        env.window.djust.reinitAfterDOMUpdate();
        expect(env.document.querySelector('[name="s1"]').value).toBe('one');

        // submit: clear_draft() puts data-draft-clear on the next render
        form.setAttribute('data-draft-clear', '');
        env.window.djust.reinitAfterDOMUpdate();
        expect(env.stored('k')).toBe(null);
        env.type(env.document.querySelector('[name="s1"]'), 'fresh');
        env.flush();
        expect(env.stored('k')).toEqual({ s1: 'fresh' });
    });

    it('restores a field a morph kept but renamed (one input reused for each step)', async () => {
        const env = await boot(FORM('k', text('s1')));
        const field = env.document.querySelector('[name="s1"]');
        env.type(field, 'one');
        env.flush();

        field.name = 's2'; // the morph reuses the element for step 2 ...
        field.value = '';
        env.window.djust.reinitAfterDOMUpdate();
        env.type(field, 'two');
        env.flush();

        field.name = 's1'; // ... and again for step 1
        field.value = '';
        env.window.djust.reinitAfterDOMUpdate();

        expect(field.value).toBe('one');
        expect(env.stored('k')).toEqual({ s1: 'one', s2: 'two' });
    });

    it('survives a rename round trip with edits under both names', async () => {
        const env = await boot(FORM('k', text('a')));
        const field = env.document.querySelector('[name="a"]');
        env.type(field, 'x'); // edited under a
        env.flush();

        field.name = 'b'; // the morph reuses the element for b
        field.value = '';
        env.window.djust.reinitAfterDOMUpdate();
        env.type(field, 'y'); // edited under b
        env.flush();

        field.name = 'a'; // and back: a is a new field again, its saved value returns
        field.value = '';
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('x');

        field.name = 'b';
        field.value = '';
        env.window.djust.reinitAfterDOMUpdate();
        expect(field.value).toBe('y');
        expect(env.stored('k')).toEqual({ a: 'x', b: 'y' });
    });

    it('merges with an unwritten save as well as with storage', async () => {
        const env = await boot(FORM('k', step1 + '<div id="slot"></div>'));
        env.type(env.document.querySelector('[name="s1"]'), 'one'); // queued, not written
        env.document.getElementById('slot').innerHTML = step2;
        env.document.querySelector('[name="s1"]').remove();
        env.window.djust.reinitAfterDOMUpdate();
        env.type(env.document.querySelector('[name="s2"]'), 'two');
        env.flush();
        expect(env.stored('k')).toEqual({ s1: 'one', s2: 'two' });
    });

    it('never merges an unsafe key from storage into the draft', async () => {
        const env = await boot(FORM('k', text('a')));
        env.window.localStorage.setItem(
            'djust_draft_k', '{"data":{"__proto__":{"polluted":true},"old":"x"},"timestamp":1}');
        env.type(env.document.querySelector('[name="a"]'), 'v');
        env.flush();
        const stored = env.stored('k');
        expect(Object.keys(stored).sort()).toEqual(['a', 'old']);
        expect({}.polluted).toBeUndefined();
    });
});

describe('draft mode: file inputs (#3351)', () => {
    it('neither saves nor restores a file input, and does not throw', async () => {
        const env = await boot(
            FORM('k', text('a') + '<div id="slot"></div>'), { k: { up: 'C:\\fakepath\\x.txt', a: 'A' } });
        env.document.getElementById('slot').innerHTML =
            '<input type="file" name="up" data-draft="true">';
        expect(() => env.window.djust.reinitAfterDOMUpdate()).not.toThrow();
        expect(env.document.querySelector('[name="a"]').value).toBe('A');
        env.type(env.document.querySelector('[name="a"]'), 'B');
        env.flush();
        expect(env.stored('k')).toEqual({ up: 'C:\\fakepath\\x.txt', a: 'B' }); // 'up' kept, not rewritten
    });
});
