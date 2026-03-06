/**
 * Regression tests for checkbox checked property patching.
 *
 * Regression: SetAttr { key: 'checked' } was only calling setAttribute(),
 * which updates the HTML attribute (default/initial state) but does NOT
 * update the DOM property el.checked (live visual state). After user
 * interaction the browser separates the two, so setAttribute alone has
 * no visible effect.
 *
 * Same gap existed in RemoveAttr: removeAttribute('checked') did not set
 * el.checked = false.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
}

dom.window.eval(clientCode);

const { _applySinglePatch } = dom.window.djust;
const document = dom.window.document;

describe('checkbox checked property patching', () => {
    let checkbox;

    beforeEach(() => {
        checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.setAttribute('dj-id', 'cb1');
        checkbox.checked = false;
        document.body.appendChild(checkbox);
    });

    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('SetAttr checked sets el.checked = true', () => {
        expect(checkbox.checked).toBe(false);

        _applySinglePatch({ type: 'SetAttr', d: 'cb1', path: [], key: 'checked', value: '' });

        expect(checkbox.checked).toBe(true);
    });

    it('RemoveAttr checked sets el.checked = false', () => {
        checkbox.checked = true;
        checkbox.setAttribute('checked', '');

        _applySinglePatch({ type: 'RemoveAttr', d: 'cb1', path: [], key: 'checked' });

        expect(checkbox.checked).toBe(false);
    });

    it('SetAttr checked also sets the HTML attribute', () => {
        _applySinglePatch({ type: 'SetAttr', d: 'cb1', path: [], key: 'checked', value: '' });

        expect(checkbox.hasAttribute('checked')).toBe(true);
    });

    it('RemoveAttr checked also removes the HTML attribute', () => {
        checkbox.setAttribute('checked', '');

        _applySinglePatch({ type: 'RemoveAttr', d: 'cb1', path: [], key: 'checked' });

        expect(checkbox.hasAttribute('checked')).toBe(false);
    });

    it('SetAttr value on non-checkbox input is unaffected', () => {
        const text = document.createElement('input');
        text.type = 'text';
        text.setAttribute('dj-id', 'txt1');
        document.body.appendChild(text);

        _applySinglePatch({ type: 'SetAttr', d: 'txt1', path: [], key: 'value', value: 'hello' });

        expect(text.value).toBe('hello');
    });
});
