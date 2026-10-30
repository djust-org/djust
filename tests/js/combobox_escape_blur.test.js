/**
 * The combobox's Escape inside a dialog or sheet, and its commit on blur
 * (src/53-component-behaviors.js).
 *
 * Before: with the list open, Escape inside a `{% modal %}` closed the whole
 * dialog (the dialog keyboard handler runs first, and a native <dialog>
 * closes itself), and app-level window Escape bindings fired too, because the
 * combobox only called preventDefault(). A typed exact label committed on
 * Tab pulled focus back to the input, so the first Tab was swallowed, and the
 * list stayed filtered to the typed text.
 *
 * The markup is what `{% combobox %}`, `{% modal %}` and `{% sheet %}` render.
 * Events are read off the (mock) socket. A real browser's Escape and focus
 * order are checked in tests/playwright/test_combobox_escape_blur.py.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const COMBOBOX = `
<div class="combobox" id="city-combobox" data-filter="client">
  <input type="hidden" class="combobox-value" id="city-value" name="city" value="a">
  <input class="combobox-input form-input" type="text" id="city" value="Alpha"
         role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="city-listbox" autocomplete="off">
  <div class="combobox-dropdown" id="city-listbox" role="listbox">
    <div class="combobox-option combobox-option-selected" id="city-option-0" role="option" aria-selected="true" dj-click="pick_city" data-value="a">Alpha</div>
    <div class="combobox-option" id="city-option-1" role="option" aria-selected="false" dj-click="pick_city" data-value="b">Beta</div>
    <div class="combobox-option" id="city-option-2" role="option" aria-selected="false" dj-click="pick_city" data-value="c">Gamma</div>
  </div>
</div>
<input id="after">`;

const NATIVE_MODAL = `
<dialog class="dj-modal dj-modal--md dj-modal--native" dj-dialog="open" dj-dialog-close-event="close_modal" aria-labelledby="dlg-title">
  <div class="dj-modal__header"><h3 class="dj-modal__title" id="dlg-title">Pick</h3>
    <button class="dj-modal__close" aria-label="Close" dj-click="close_modal">&times;</button></div>
  <div class="dj-modal__body">${COMBOBOX}</div>
</dialog>`;

const DIV_MODAL = `
<div class="dj-modal-backdrop">
  <div class="dj-modal dj-modal--md" role="dialog" aria-modal="true" aria-labelledby="dlg-title">
    <div class="dj-modal__header"><h3 class="dj-modal__title" id="dlg-title">Pick</h3>
      <button class="dj-modal__close" aria-label="Close" dj-click="close_modal">&times;</button></div>
    <div class="dj-modal__body">${COMBOBOX}</div>
  </div>
</div>`;

// A `{% sheet %}` with the app-level Escape binding the Point Guard port uses.
const SHEET = `
<div class="sheet-overlay" dj-click="close_sheet" data-open="true"></div>
<div class="sheet sheet-right" data-open="true" dj-window-keydown.escape="close_sheet">
  <div class="sheet-header-close"><button class="sheet-close" dj-click="close_sheet">&times;</button></div>
  <div class="sheet-body">${COMBOBOX}</div>
</div>`;

async function createDom(html) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root dj-view="app.View">${html}</div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    const win = dom.window;
    // What a browser's showModal()/close() do that matters here.
    win.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    win.HTMLDialogElement.prototype.close = function () {
        this.removeAttribute('open');
        this.dispatchEvent(new win.Event('close'));
    };
    const sent = [];
    win.WebSocket = class {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() { this.readyState = 1; setTimeout(() => this.onopen && this.onopen({}), 0); }
        send(data) { sent.push(JSON.parse(data)); }
        close() {}
    };
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    win.eval(clientCode);
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    await new Promise((r) => setTimeout(r, 10));
    await win.djust.liveViewInstance.handleMessage({
        type: 'mount', view: 'app.View', version: 1, html,
    });
    const doc = win.document;
    const input = doc.getElementById('city');
    input.focus();
    const events = () => sent.filter((m) => m.type === 'event').map((m) => m.event);
    const flush = () => new Promise((r) => setTimeout(r, 30));
    return { win, doc, input, events, flush };
}

// A browser's Escape in a modal <dialog>: the keydown, then the dialog's
// cancelable `cancel`, then close() unless cancelled. `cancel` is sent even
// when the keydown was prevented, the worst case for the combobox.
function escape(win, el) {
    const ev = new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    const dialog = el.closest('dialog');
    if (dialog && dialog.open) {
        const cancel = new win.Event('cancel', { cancelable: true });
        dialog.dispatchEvent(cancel);
        if (!cancel.defaultPrevented) dialog.close();
    }
    el.dispatchEvent(new win.KeyboardEvent('keyup', { key: 'Escape', bubbles: true }));
    return ev;
}

function typeText(win, input, text) {
    input.value = text;
    input.dispatchEvent(new win.Event('input', { bubbles: true }));
}

function shown(doc) {
    return Array.from(doc.querySelectorAll('.combobox-option'))
        .filter((o) => o.style.display !== 'none').map((o) => o.textContent);
}

function closed(doc) {
    return doc.getElementById('city-combobox').classList.contains('combobox--closed');
}

describe('combobox Escape inside a native-dialog {% modal %}', () => {
    it('with the list open, closes the list and restores the label; the dialog stays open', async () => {
        const { win, doc, input, events, flush } = await createDom(NATIVE_MODAL);
        typeText(win, input, 'Be');
        const ev = escape(win, input);
        await flush();
        expect(ev.defaultPrevented).toBe(true);
        expect(doc.querySelector('dialog').open).toBe(true);
        expect(input.value).toBe('Alpha');
        expect(closed(doc)).toBe(true);
        expect(shown(doc)).toEqual(['Alpha', 'Beta', 'Gamma']);
        expect(events()).toEqual([]);
    });

    it('the dialog\'s own cancel is refused for that key press only', async () => {
        const { win, doc, input } = await createDom(NATIVE_MODAL);
        const dialog = doc.querySelector('dialog');
        input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        const cancel = new win.Event('cancel', { cancelable: true });
        dialog.dispatchEvent(cancel);
        expect(cancel.defaultPrevented).toBe(true);
        input.dispatchEvent(new win.KeyboardEvent('keyup', { key: 'Escape', bubbles: true }));
        const later = new win.Event('cancel', { cancelable: true });
        dialog.dispatchEvent(later);
        expect(later.defaultPrevented).toBe(false);
    });

    it('with the list closed, Escape closes the dialog as before', async () => {
        const { win, doc, input, events, flush } = await createDom(NATIVE_MODAL);
        escape(win, input);  // closes the list
        const ev = escape(win, input);
        await flush();
        expect(ev.defaultPrevented).toBe(false);
        expect(doc.querySelector('dialog').open).toBe(false);
        expect(events()).toEqual(['close_modal']);
    });
});

describe('combobox Escape inside a <div> {% modal %}', () => {
    it('the dialog keyboard handler does not see an Escape the open list takes', async () => {
        const { win, doc, input, events, flush } = await createDom(DIV_MODAL);
        let pressed = 0;
        doc.querySelector('.dj-modal__close').addEventListener('click', () => { pressed += 1; });
        escape(win, input);
        await flush();
        expect(pressed).toBe(0);
        expect(events()).toEqual([]);
        escape(win, input);
        await flush();
        expect(pressed).toBe(1);
        expect(events()).toEqual(['close_modal']);
    });
});

describe('combobox Escape inside a {% sheet %}', () => {
    it('an app-level window Escape binding fires only once the list is closed', async () => {
        const { win, input, events, flush } = await createDom(SHEET);
        let windowSaw = 0;
        win.addEventListener('keydown', (e) => { if (e.key === 'Escape') windowSaw += 1; });
        escape(win, input);
        await flush();
        expect(windowSaw).toBe(0);
        expect(events()).toEqual([]);
        escape(win, input);
        await flush();
        expect(windowSaw).toBe(1);
        expect(events()).toEqual(['close_sheet']);
    });

    it('other keys still reach window bindings while the list is open', async () => {
        const { win, input } = await createDom(SHEET);
        let windowSaw = 0;
        win.addEventListener('keydown', () => { windowSaw += 1; });
        input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
        expect(windowSaw).toBe(1);
    });
});

describe('combobox commit on blur', () => {
    // A browser fires `change` as focus leaves; by then focus may already be
    // on the next control.
    it('a typed exact label commits without pulling focus back, and the filter resets', async () => {
        const { win, doc, input, events, flush } = await createDom(NATIVE_MODAL);
        typeText(win, input, 'Beta');
        expect(shown(doc)).toEqual(['Beta']);
        const after = doc.getElementById('after');
        after.focus();
        input.dispatchEvent(new win.Event('change', { bubbles: true }));
        await flush();
        expect(doc.activeElement).toBe(after);
        expect(doc.getElementById('city-value').value).toBe('b');
        expect(input.value).toBe('Beta');
        expect(events()).toEqual(['pick_city']);
        expect(shown(doc)).toEqual(['Alpha', 'Beta', 'Gamma']);
        expect(doc.querySelector('dialog').open).toBe(true);
    });

    it('text matching no option is dropped and the filter resets', async () => {
        const { win, doc, input, events, flush } = await createDom(SHEET);
        typeText(win, input, 'Zed');
        expect(shown(doc)).toEqual([]);
        doc.getElementById('after').focus();
        input.dispatchEvent(new win.Event('change', { bubbles: true }));
        await flush();
        expect(input.value).toBe('Alpha');
        expect(events()).toEqual([]);
        expect(shown(doc)).toEqual(['Alpha', 'Beta', 'Gamma']);
    });

    it('a mouse pick still keeps focus on the input', async () => {
        const { win, doc, input, flush } = await createDom(NATIVE_MODAL);
        const option = doc.getElementById('city-option-2');
        doc.getElementById('after').focus();
        option.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
        await flush();
        expect(doc.activeElement).toBe(input);
    });
});
