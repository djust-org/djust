/**
 * A whole-page answer whose render holds a dj-update element (#4283).
 *
 * `_applyDjUpdateElementsInner` used to update only the root's top-level
 * children that carry an id, so a change to any other top-level child was
 * dropped: on a page with `{% dj_flash %}` (a `dj-update="ignore"` container)
 * a toggled `<p>` never appeared. The answer is now morphed as a whole, as one
 * without dj-update is; the dj-update elements keep their rules (an ignored
 * container keeps the client's children, an append container its rows).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { runScripts: 'dangerously' });
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (value) => String(value).replace(/([^\w-])/g, '\\$1');
}
dom.window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
dom.window.eval(clientCode);

const { document } = dom.window;
const apply = dom.window.djust._applyDjUpdateElements;

const FLASH = '<div id="dj-flash-container" class="dj-flash-container" dj-update="ignore" data-dj-auto-dismiss="0"></div>';

function page(inner) {
    document.body.innerHTML = `<div dj-root>${inner}</div>`;
    return document.querySelector('[dj-root]');
}

function answer(inner) {
    const root = document.createElement('div');
    root.innerHTML = inner;
    return root;
}

describe('a whole-page answer with a dj-update element (#4283)', () => {
    beforeEach(() => { document.body.innerHTML = ''; });

    it('an answer with an ignored container still adds an id-less sibling', () => {
        const root = page('<button dj-click="toggle">Toggle</button>' + FLASH);
        apply(root, answer('<button dj-click="toggle">Toggle</button><p>Details</p>' + FLASH));
        const p = root.querySelector('p');
        expect(p).not.toBeNull();
        expect(p.textContent).toBe('Details');
        expect(Array.from(root.children).map((c) => c.tagName)).toEqual(['BUTTON', 'P', 'DIV']);
    });

    it('updates and removes id-less top-level children too', () => {
        const root = page('<h1>Old</h1><p>gone</p>' + FLASH);
        apply(root, answer('<h1>New</h1>' + FLASH));
        expect(root.querySelector('h1').textContent).toBe('New');
        expect(root.querySelector('p')).toBeNull();
    });

    it("keeps the ignored container's client children", () => {
        const root = page('<p>a</p>' + FLASH);
        const container = root.querySelector('#dj-flash-container');
        container.innerHTML = '<div class="dj-flash">Saved!</div>';
        apply(root, answer('<p>b</p>' + FLASH));
        expect(root.querySelector('#dj-flash-container')).toBe(container);
        expect(container.querySelector('.dj-flash').textContent).toBe('Saved!');
        expect(root.querySelector('p').textContent).toBe('b');
    });

    it('keeps focus and typed text', () => {
        const root = page('<form><input name="q" value=""></form><p>0</p>' + FLASH);
        const input = root.querySelector('input');
        input.focus();
        input.value = 'typed';
        apply(root, answer('<form><input name="q" value=""></form><p>1</p>' + FLASH));
        expect(root.querySelector('input')).toBe(input);
        expect(document.activeElement).toBe(input);
        expect(input.value).toBe('typed');
        expect(root.querySelector('p').textContent).toBe('1');
    });

    it('append container keeps accumulated rows', () => {
        const root = page('<ul id="rows" dj-update="append"><li id="r1">one</li></ul><p>1</p>');
        apply(root, answer('<ul id="rows" dj-update="append"><li id="r2">two</li></ul><p>2</p>'));
        expect(Array.from(root.querySelectorAll('#rows li')).map((li) => li.id)).toEqual(['r1', 'r2']);
        expect(root.querySelector('p').textContent).toBe('2');
    });

    it('prepend container puts new rows first, as before', () => {
        const root = page('<ul id="rows" dj-update="prepend"><li id="r1">one</li></ul>');
        apply(root, answer('<ul id="rows" dj-update="prepend"><li id="r3">three</li><li id="r2">two</li></ul>'));
        expect(Array.from(root.querySelectorAll('#rows li')).map((li) => li.id)).toEqual(['r2', 'r3', 'r1']);
    });

    it('creates a dj-update container the page did not have yet', () => {
        const root = page('<p>x</p>');
        apply(root, answer('<p>x</p>' + FLASH));
        expect(root.querySelector('#dj-flash-container')).not.toBeNull();
    });
});

describe('domShape leaves an ignored subtree out (#4283)', () => {
    it('client-added flash messages do not change the shape', () => {
        const root = page('<p>a</p>' + FLASH + '<p>b</p>');
        const before = dom.window.djust.domShape(root);
        root.querySelector('#dj-flash-container').innerHTML = '<div class="dj-flash">Saved!</div> x';
        expect(dom.window.djust.domShape(root)).toBe(before);
        root.querySelector('p').textContent = 'changed';
        expect(dom.window.djust.domShape(root)).not.toBe(before);
    });
});
