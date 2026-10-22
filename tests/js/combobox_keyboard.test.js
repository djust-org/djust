/**
 * The combobox follows the WAI-ARIA combobox keyboard pattern
 * (src/53-component-behaviors.js).
 *
 * Before: the arrow keys did not move through the options, Enter submitted the
 * form with the old value, a typed exact label set the value in the browser
 * without telling the server, and focus dropped to the page after a pick.
 *
 * The events are read off the (mock) socket, so "the server is told" means a
 * frame actually left the client. The markup is what `{% combobox %}` renders.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const COMBOBOX = `
<form id="f">
<div class="form-group">
  <label class="form-label" for="id_city">City</label>
  <div class="combobox" id="id_city-combobox" data-filter="client">
    <input type="hidden" class="combobox-value" id="id_city-value" name="city" value="a">
    <input class="combobox-input form-input" type="text" id="id_city" placeholder="Search" value="Alpha"
           role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="id_city-listbox" autocomplete="off">
    <div class="combobox-dropdown" id="id_city-listbox" role="listbox">
      <div class="combobox-option combobox-option-selected" id="id_city-option-0" role="option" aria-selected="true" dj-click="pick_city" data-value="a" data-field="city">Alpha</div>
      <div class="combobox-option" id="id_city-option-1" role="option" aria-selected="false" dj-click="pick_city" data-value="b" data-field="city">Beta</div>
      <div class="combobox-option" id="id_city-option-2" role="option" aria-selected="false" dj-click="pick_city" data-value="c" data-field="city">Gamma</div>
    </div>
  </div>
</div>
<button type="submit">Send</button>
</form>`;

async function createDom() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root dj-view="app.View">${COMBOBOX}</div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    const win = dom.window;
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
    // Events leave the client only once the view is mounted.
    await win.djust.liveViewInstance.handleMessage({
        type: 'mount', view: 'app.View', version: 1, html: COMBOBOX,
    });
    const doc = win.document;
    const input = doc.getElementById('id_city');
    input.focus();
    const events = () => sent.filter((m) => m.type === 'event').map((m) => [m.event, m.params]);
    const flush = () => new Promise((r) => setTimeout(r, 30));
    return { dom, win, doc, input, events, flush };
}

function key(win, el, k) {
    const ev = new win.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev;
}

function typeText(win, input, text) {
    input.value = text;
    input.dispatchEvent(new win.Event('input', { bubbles: true }));
}

function active(doc, input) {
    return input.getAttribute('aria-activedescendant');
}

describe('combobox keyboard', () => {
    it('ArrowDown / ArrowUp move aria-activedescendant through the options', async () => {
        const { win, doc, input } = await createDom();
        const down = key(win, input, 'ArrowDown');
        expect(down.defaultPrevented).toBe(true);
        expect(active(doc, input)).toBe('id_city-option-0');
        key(win, input, 'ArrowDown');
        expect(active(doc, input)).toBe('id_city-option-1');
        expect(doc.getElementById('id_city-option-1').classList.contains('combobox-option-active')).toBe(true);
        expect(doc.getElementById('id_city-option-0').classList.contains('combobox-option-active')).toBe(false);
        key(win, input, 'ArrowDown');
        key(win, input, 'ArrowDown');
        expect(active(doc, input)).toBe('id_city-option-0');  // wraps
        key(win, input, 'ArrowUp');
        expect(active(doc, input)).toBe('id_city-option-2');
        expect(input.getAttribute('aria-expanded')).toBe('true');
    });

    it('the arrows skip options the typed text filtered out', async () => {
        const { win, doc, input } = await createDom();
        typeText(win, input, 'a');  // Alpha, Beta, Gamma all contain "a"
        typeText(win, input, 'ga');  // only Gamma
        key(win, input, 'ArrowDown');
        expect(active(doc, input)).toBe('id_city-option-2');
        key(win, input, 'ArrowDown');
        expect(active(doc, input)).toBe('id_city-option-2');
    });

    it('Enter commits the active option, tells the server and keeps the form from submitting', async () => {
        const { win, doc, input, events, flush } = await createDom();
        let submitted = 0;
        doc.getElementById('f').addEventListener('submit', (e) => { submitted += 1; e.preventDefault(); });
        key(win, input, 'ArrowDown');
        key(win, input, 'ArrowDown');
        const enter = key(win, input, 'Enter');
        await flush();
        expect(enter.defaultPrevented).toBe(true);
        expect(submitted).toBe(0);
        expect(doc.getElementById('id_city-value').value).toBe('b');
        expect(input.value).toBe('Beta');
        expect(events()).toEqual([['pick_city', { value: 'b', field: 'city' }]]);
        expect(doc.activeElement).toBe(input);
        expect(doc.getElementById('id_city-combobox').classList.contains('combobox--closed')).toBe(true);
        expect(input.getAttribute('aria-expanded')).toBe('false');
        expect(input.hasAttribute('aria-activedescendant')).toBe(false);
    });

    it('a typed exact label commits on Enter and tells the server', async () => {
        const { win, doc, input, events, flush } = await createDom();
        typeText(win, input, 'gamma');
        const enter = key(win, input, 'Enter');
        await flush();
        expect(enter.defaultPrevented).toBe(true);
        expect(doc.getElementById('id_city-value').value).toBe('c');
        expect(input.value).toBe('Gamma');
        expect(events()).toEqual([['pick_city', { value: 'c', field: 'city' }]]);
    });

    it('a typed exact label commits on blur and tells the server once', async () => {
        const { win, doc, input, events, flush } = await createDom();
        typeText(win, input, 'Beta');
        input.dispatchEvent(new win.Event('change', { bubbles: true }));
        input.blur();
        await flush();
        expect(doc.getElementById('id_city-value').value).toBe('b');
        expect(events()).toEqual([['pick_city', { value: 'b', field: 'city' }]]);
    });

    it('Escape closes the list and restores the chosen label', async () => {
        const { win, doc, input, events, flush } = await createDom();
        typeText(win, input, 'Gam');
        key(win, input, 'ArrowDown');
        const esc = key(win, input, 'Escape');
        await flush();
        expect(esc.defaultPrevented).toBe(true);
        expect(input.value).toBe('Alpha');
        expect(doc.getElementById('id_city-value').value).toBe('a');
        expect(doc.getElementById('id_city-combobox').classList.contains('combobox--closed')).toBe(true);
        expect(input.hasAttribute('aria-activedescendant')).toBe(false);
        expect(events()).toEqual([]);
        // The options the typing hid are visible again for the next open.
        expect(doc.getElementById('id_city-option-0').style.display).toBe('');
    });

    it('a mouse pick keeps focus on the input, closes the list and tells the server once', async () => {
        const { win, doc, input, events, flush } = await createDom();
        const option = doc.getElementById('id_city-option-2');
        option.dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        option.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
        await flush();
        expect(doc.activeElement).toBe(input);
        expect(input.value).toBe('Gamma');
        expect(doc.getElementById('id_city-combobox').classList.contains('combobox--closed')).toBe(true);
        input.dispatchEvent(new win.Event('change', { bubbles: true }));
        await flush();
        expect(events()).toEqual([['pick_city', { value: 'c', field: 'city' }]]);
    });

    it('typing or an arrow key reopens a closed list', async () => {
        const { win, doc, input } = await createDom();
        key(win, input, 'ArrowDown');
        key(win, input, 'Enter');
        const box = doc.getElementById('id_city-combobox');
        expect(box.classList.contains('combobox--closed')).toBe(true);
        typeText(win, input, 'B');
        expect(box.classList.contains('combobox--closed')).toBe(false);
        key(win, input, 'Escape');
        key(win, input, 'ArrowDown');
        expect(box.classList.contains('combobox--closed')).toBe(false);
        expect(input.getAttribute('aria-expanded')).toBe('true');
    });

    it('Enter with nothing to commit and the list closed leaves the form submit alone', async () => {
        const { win, input } = await createDom();
        key(win, input, 'Escape');
        const enter = key(win, input, 'Enter');
        expect(enter.defaultPrevented).toBe(false);
    });
});
