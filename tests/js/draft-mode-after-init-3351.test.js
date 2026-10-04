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
    await new Promise((resolve) => realSetTimeout(resolve, 20)); // djustInit is a microtask

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
        await new Promise((resolve) => w.setTimeout(resolve, 20)); // djustInit
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
