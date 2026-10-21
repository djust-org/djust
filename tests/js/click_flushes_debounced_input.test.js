/**
 * A click must not overtake a debounced edit the user made before it.
 *
 * `dj-submit` flushes the pending `dj-debounce` input events of its own form
 * before it sends (#1278), and the handler-level `@debounce` gate (#2656).
 * `dj-click` flushed neither: type into a `dj-input dj-debounce="300"` field,
 * click a button within 300 ms, and the click reached the server first; the
 * input followed with the text typed BEFORE the click, so a handler that
 * "clears the error while typing" undid what the click's handler had shown.
 * A form submit whose debounced field lives outside that form (a dialog's
 * note next to its confirm form) had the same race.
 */

import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function setup(bodyHtml, beforeClient = '') {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'dangerously',
        url: 'http://localhost/',
    });
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', { value: false, writable: true, configurable: true });
        window._calls = [];
        window.fetch = async function(url, opts) {
            var name = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch (e) {}
            window._calls.push({ name: name, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._calls.length }; } };
        };
        ${beforeClient}
    `);
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom.window;
}

function type(win, el, value) {
    el.value = value;
    el.dispatchEvent(new win.Event('input', { bubbles: true }));
}

const names = (win) => win._calls.map((c) => c.name);

describe('dj-click after a debounced dj-input', () => {
    it('sends the pending input first, with the typed value', async () => {
        const win = setup(`
<div dj-view="app.V" dj-root>
  <textarea name="note" dj-input="edit_note" dj-debounce="300"></textarea>
  <button id="deny" dj-click="deny">Deny</button>
</div>`);
        await nativeSleep(10);
        const note = win.document.querySelector('textarea');
        type(win, note, 'too late');
        win.document.getElementById('deny').click();
        await nativeSleep(20);

        expect(names(win)).toEqual(['edit_note', 'deny']);
        expect(win._calls[0].body.value).toBe('too late');

        // The debounce timer must not send the same edit again afterwards.
        await nativeSleep(400);
        expect(names(win)).toEqual(['edit_note', 'deny']);
    });

    it('sends nothing extra when no input is pending', async () => {
        const win = setup(`
<div dj-view="app.V" dj-root>
  <textarea name="note" dj-input="edit_note" dj-debounce="50"></textarea>
  <button id="deny" dj-click="deny">Deny</button>
</div>`);
        await nativeSleep(10);
        type(win, win.document.querySelector('textarea'), 'x');
        await nativeSleep(120); // the debounce has fired on its own
        win.document.getElementById('deny').click();
        await nativeSleep(20);
        expect(names(win)).toEqual(['edit_note', 'deny']);
    });

    it('also flushes a handler-level @debounce edit', async () => {
        const win = setup(`
<div dj-view="app.V" dj-root>
  <input name="q" dj-input="search">
  <button id="go" dj-click="go">Go</button>
</div>`, 'window.handlerMetadata = { search: { debounce: { wait: 0.4, max_wait: null } } };');
        await nativeSleep(10);
        type(win, win.document.querySelector('input'), 'abc');
        win.document.getElementById('go').click();
        await nativeSleep(20);
        expect(names(win)).toEqual(['search', 'go']);
        expect(win._calls[0].body.value).toBe('abc');
    });
});

describe('dj-click and @throttle', () => {
    it('keeps a throttle window: its trailing call is not sent by the click', async () => {
        const win = setup(`
<div dj-view="app.V" dj-root>
  <button id="go" dj-click="go">Go</button>
</div>`, 'window.handlerMetadata = { track: { throttle: { interval: 0.3, leading: true, trailing: true } } };');
        await nativeSleep(10);
        win.djust.handleEvent('track', { value: '1' }); // leading: sent
        win.djust.handleEvent('track', { value: '2' }); // parked as the trailing call
        win.document.getElementById('go').click();
        await nativeSleep(20);
        expect(names(win)).toEqual(['track', 'go']);
        await nativeSleep(400);
        expect(names(win)).toEqual(['track', 'go', 'track']);
        expect(win._calls[2].body.value).toBe('2');
    });
});

describe('dj-submit after a debounced dj-input outside its form', () => {
    it('sends the pending input first', async () => {
        const win = setup(`
<div dj-view="app.V" dj-root>
  <textarea name="note" dj-input="edit_note" dj-debounce="300"></textarea>
  <form dj-submit="confirm"><button type="submit">Confirm</button></form>
</div>`);
        await nativeSleep(10);
        type(win, win.document.querySelector('textarea'), 'reason');
        win.document.querySelector('form').dispatchEvent(
            new win.Event('submit', { bubbles: true, cancelable: true }));
        await nativeSleep(20);
        expect(names(win)).toEqual(['edit_note', 'confirm']);
    });
});
