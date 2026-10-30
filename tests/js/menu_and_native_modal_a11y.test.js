/**
 * Keyboard support for {% dropdown_menu %} and the native {% modal %}.
 *
 * - The keyboard-nav module only knew `{% dropdown %}` (`.dj-dropdown`), so a
 *   `{% dropdown_menu %}` (`.dj-dropdown-menu`) left focus on its trigger on
 *   ArrowDown and had no Escape. It now gets the WAI-ARIA menu pattern over
 *   menuitem / menuitemradio / menuitemcheckbox items.
 * - `{% modal native=True %}` renders a <dialog> opened with showModal(): the
 *   browser owns Escape (cancel → close, reported to the server), the focus
 *   trap and return focus still apply, and a backdrop click closes it.
 *
 * The markup is what the component tags render.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const MENU = `
<div class="dj-dropdown-menu dj-dropdown-menu--open" data-open>
  <button type="button" class="dj-dropdown-menu__trigger" dj-click="toggle_menu" aria-expanded="true" aria-haspopup="menu">View</button>
  <div class="dj-dropdown-menu__content dj-dropdown-menu--left" role="menu">
    <div class="dj-dropdown-menu__heading" role="presentation">Theme</div>
    <button type="button" class="dj-dropdown-menu__item" role="menuitemradio" aria-checked="true" dj-click="m" data-value="light">Light</button>
    <button type="button" class="dj-dropdown-menu__item" role="menuitemradio" aria-checked="false" dj-click="m" data-value="dark">Dark</button>
    <hr class="dj-dropdown-menu__divider" role="separator">
    <button type="button" class="dj-dropdown-menu__item dj-dropdown-menu__item--disabled" role="menuitem" aria-disabled="true" disabled>Off</button>
    <button type="button" class="dj-dropdown-menu__item" role="menuitemcheckbox" aria-checked="false" dj-click="t">Details</button>
    <a class="dj-dropdown-menu__item" role="menuitem" href="/help/">Help</a>
  </div>
</div>`;

const NATIVE_MODAL = `
<dialog class="dj-modal dj-modal--md dj-modal--native" dj-dialog="open" dj-dialog-close-event="close_req" aria-labelledby="req-title">
  <div class="dj-modal__header"><h3 class="dj-modal__title" id="req-title">Request</h3>
    <button class="dj-modal__close" aria-label="Close" dj-click="close_req">&times;</button></div>
  <div class="dj-modal__body"><input id="note" name="note"></div>
</dialog>`;

function createDom(bodyHtml, { nativeDialog = false } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
            <div dj-root dj-view="app.View"><button id="opener">Open</button>${bodyHtml}</div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    const win = dom.window;
    if (nativeDialog) {
        // What a browser's showModal()/close() do that matters here: open the
        // dialog and move focus into it; close and fire `close`.
        win.HTMLDialogElement.prototype.showModal = function () {
            this.setAttribute('open', '');
            const first = this.querySelector('button, input');
            if (first) first.focus();
        };
        win.HTMLDialogElement.prototype.close = function () {
            this.removeAttribute('open');
            this.dispatchEvent(new win.Event('close'));
        };
    }
    win.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    try { win.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    return dom;
}

function key(dom, el, k, opts = {}) {
    const ev = new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...opts });
    el.dispatchEvent(ev);
    return ev;
}

function items(doc) {
    return Array.from(doc.querySelectorAll('[role="menu"] [role^="menuitem"]'));
}

describe('{% dropdown_menu %} keyboard (WAI-ARIA menu)', () => {
    it('ArrowDown from the trigger focuses the first item, ArrowUp the last', () => {
        const dom = createDom(MENU);
        const doc = dom.window.document;
        const trigger = doc.querySelector('.dj-dropdown-menu__trigger');
        trigger.focus();
        const ev = key(dom, trigger, 'ArrowDown');
        expect(ev.defaultPrevented).toBe(true);
        expect(doc.activeElement.textContent).toBe('Light');
        trigger.focus();
        key(dom, trigger, 'ArrowUp');
        expect(doc.activeElement.textContent).toBe('Help');
    });

    it('roves over radio, checkbox and link items, skipping disabled ones, and wraps', () => {
        const dom = createDom(MENU);
        const doc = dom.window.document;
        const [light] = items(doc);
        light.focus();
        const seen = [];
        for (let i = 0; i < 5; i++) {
            key(dom, doc.activeElement, 'ArrowDown');
            seen.push(doc.activeElement.textContent);
        }
        expect(seen).toEqual(['Dark', 'Details', 'Help', 'Light', 'Dark']);
        key(dom, doc.activeElement, 'ArrowUp');
        expect(doc.activeElement.textContent).toBe('Light');
    });

    it('Home and End jump to the first and last item', () => {
        const dom = createDom(MENU);
        const doc = dom.window.document;
        const details = items(doc).find((el) => el.textContent === 'Details');
        details.focus();
        expect(doc.activeElement).toBe(details);
        key(dom, doc.activeElement, 'End');
        expect(doc.activeElement.textContent).toBe('Help');
        key(dom, doc.activeElement, 'Home');
        expect(doc.activeElement.textContent).toBe('Light');
    });

    it('type-ahead moves to the next item starting with the letter', () => {
        const dom = createDom(MENU);
        const doc = dom.window.document;
        items(doc)[0].focus();
        key(dom, doc.activeElement, 'd');
        expect(doc.activeElement.textContent).toBe('Dark');
        key(dom, doc.activeElement, 'd');
        expect(doc.activeElement.textContent).toBe('Details');
        key(dom, doc.activeElement, 'h');
        expect(doc.activeElement.textContent).toBe('Help');
    });

    it('Escape sends the toggle event and returns focus to the trigger', () => {
        const dom = createDom(MENU);
        const doc = dom.window.document;
        const sent = [];
        dom.window.djust.handleEvent = (name) => { sent.push(name); };
        items(doc)[1].focus();
        const ev = key(dom, doc.activeElement, 'Escape');
        expect(ev.defaultPrevented).toBe(true);
        expect(sent).toEqual(['toggle_menu']);
        expect(doc.activeElement).toBe(doc.querySelector('.dj-dropdown-menu__trigger'));
    });
});

describe('{% modal native=True %}', () => {
    it('Escape is left to the browser: not prevented, the close button not pressed', () => {
        const dom = createDom(NATIVE_MODAL, { nativeDialog: true });
        const doc = dom.window.document;
        const dialog = doc.querySelector('dialog');
        expect(dialog.open).toBe(true);
        let pressed = 0;
        doc.querySelector('.dj-modal__close').addEventListener('click', () => { pressed += 1; });
        const ev = key(dom, doc.getElementById('note'), 'Escape');
        expect(ev.defaultPrevented).toBe(false);
        expect(pressed).toBe(0);
    });

    it('the browser closing it (Escape, backdrop click) sends the close event', () => {
        const dom = createDom(NATIVE_MODAL, { nativeDialog: true });
        const doc = dom.window.document;
        const sent = [];
        dom.window.djust.handleEvent = (name) => { sent.push(name); };
        const dialog = doc.querySelector('dialog');
        dialog.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
        expect(dialog.open).toBe(false);
        expect(sent).toEqual(['close_req']);
    });

    it('a click inside the dialog content does not close it', () => {
        const dom = createDom(NATIVE_MODAL, { nativeDialog: true });
        const doc = dom.window.document;
        doc.getElementById('note').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
        expect(doc.querySelector('dialog').open).toBe(true);
    });

    it('keeps the focus trap: Tab from the last control wraps to the first', () => {
        const dom = createDom(NATIVE_MODAL, { nativeDialog: true });
        const doc = dom.window.document;
        doc.getElementById('note').focus();
        const ev = key(dom, doc.getElementById('note'), 'Tab');
        expect(ev.defaultPrevented).toBe(true);
        expect(doc.activeElement).toBe(doc.querySelector('.dj-modal__close'));
    });

    it('returns focus to the opener when a server render removes it, though showModal moved focus first', async () => {
        const dom = createDom('', { nativeDialog: true });
        const win = dom.window;
        const doc = win.document;
        const opener = doc.getElementById('opener');
        opener.focus();
        const holder = doc.createElement('div');
        holder.innerHTML = NATIVE_MODAL;
        doc.querySelector('[dj-root]').appendChild(holder);
        await new Promise((r) => setTimeout(r, 0));
        expect(doc.querySelector('dialog').open).toBe(true);
        expect(doc.activeElement).not.toBe(opener);
        holder.remove();
        await new Promise((r) => setTimeout(r, 0));
        expect(doc.activeElement).toBe(opener);
    });
});
