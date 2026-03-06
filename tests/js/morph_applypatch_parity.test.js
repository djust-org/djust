/**
 * Parity tests: morphElement and applySinglePatch must produce identical
 * DOM state for checkbox toggle and option selection scenarios.
 *
 * Regression guard for #422: ensures future changes to either code path
 * cannot silently diverge — both must sync the DOM *property* (not just the
 * HTML attribute) so the browser reflects the correct state immediately.
 *
 * Key invariants:
 *   - checkbox .checked property matches the server's intent
 *   - radio   .checked property matches the server's intent
 *   - option  .selected property matches the server's intent
 *   - morphElement and applySinglePatch produce the *same* property values
 */

import { describe, it, expect, afterEach } from 'vitest';
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

const { morphElement, _applySinglePatch } = dom.window.djust;
const document = dom.window.document;

afterEach(() => {
    document.body.innerHTML = '';
});

// Attach an element to document.body (needed for dj-id querySelector lookup)
function attach(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    const el = tpl.content.firstChild;
    document.body.appendChild(el);
    return el;
}

// Create a detached element (used as the "desired" state for morphElement)
function detached(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    return tpl.content.firstChild;
}

// ─── Checkbox parity ────────────────────────────────────────────────────────

describe('checkbox parity: toggle ON', () => {
    it('both strategies set .checked = true and add checked attribute', () => {
        // morphElement strategy
        const morphEl = attach('<input type="checkbox">');
        morphEl.checked = false;
        morphElement(morphEl, detached('<input type="checkbox" checked>'));

        // applySinglePatch strategy
        const patchEl = attach('<input type="checkbox" dj-id="chk-on">');
        patchEl.checked = false;
        _applySinglePatch({ type: 'SetAttr', d: 'chk-on', path: [], key: 'checked', value: '' });

        // Property (the thing the browser actually renders)
        expect(morphEl.checked).toBe(true);
        expect(patchEl.checked).toBe(true);

        // Attribute (serialization / hydration)
        expect(morphEl.hasAttribute('checked')).toBe(true);
        expect(patchEl.hasAttribute('checked')).toBe(true);

        // Parity
        expect(morphEl.checked).toBe(patchEl.checked);
        expect(morphEl.hasAttribute('checked')).toBe(patchEl.hasAttribute('checked'));
    });
});

describe('checkbox parity: toggle OFF', () => {
    it('both strategies set .checked = false and remove checked attribute', () => {
        // morphElement strategy
        const morphEl = attach('<input type="checkbox" checked>');
        morphEl.checked = true;
        morphElement(morphEl, detached('<input type="checkbox">'));

        // applySinglePatch strategy
        const patchEl = attach('<input type="checkbox" checked dj-id="chk-off">');
        patchEl.checked = true;
        _applySinglePatch({ type: 'RemoveAttr', d: 'chk-off', path: [], key: 'checked' });

        expect(morphEl.checked).toBe(false);
        expect(patchEl.checked).toBe(false);

        expect(morphEl.hasAttribute('checked')).toBe(false);
        expect(patchEl.hasAttribute('checked')).toBe(false);

        // Parity
        expect(morphEl.checked).toBe(patchEl.checked);
        expect(morphEl.hasAttribute('checked')).toBe(patchEl.hasAttribute('checked'));
    });
});

// ─── Radio parity ────────────────────────────────────────────────────────────

describe('radio parity: select', () => {
    it('both strategies set .checked = true and add checked attribute', () => {
        // Use distinct names so the two radios do not form a browser radio
        // group — otherwise checking patchEl would uncheck morphEl.
        const morphEl = attach('<input type="radio" name="r-morph-select">');
        morphEl.checked = false;
        morphElement(morphEl, detached('<input type="radio" name="r-morph-select" checked>'));

        const patchEl = attach('<input type="radio" name="r-patch-select" dj-id="rad-on">');
        patchEl.checked = false;
        _applySinglePatch({ type: 'SetAttr', d: 'rad-on', path: [], key: 'checked', value: '' });

        expect(morphEl.checked).toBe(true);
        expect(patchEl.checked).toBe(true);

        expect(morphEl.hasAttribute('checked')).toBe(true);
        expect(patchEl.hasAttribute('checked')).toBe(true);

        expect(morphEl.checked).toBe(patchEl.checked);
        expect(morphEl.hasAttribute('checked')).toBe(patchEl.hasAttribute('checked'));
    });
});

describe('radio parity: deselect', () => {
    it('both strategies set .checked = false and remove checked attribute', () => {
        const morphEl = attach('<input type="radio" name="r-morph-desel" checked>');
        morphEl.checked = true;
        morphElement(morphEl, detached('<input type="radio" name="r-morph-desel">'));

        const patchEl = attach('<input type="radio" name="r-patch-desel" checked dj-id="rad-off">');
        patchEl.checked = true;
        _applySinglePatch({ type: 'RemoveAttr', d: 'rad-off', path: [], key: 'checked' });

        expect(morphEl.checked).toBe(false);
        expect(patchEl.checked).toBe(false);

        expect(morphEl.hasAttribute('checked')).toBe(false);
        expect(patchEl.hasAttribute('checked')).toBe(false);

        expect(morphEl.checked).toBe(patchEl.checked);
        expect(morphEl.hasAttribute('checked')).toBe(patchEl.hasAttribute('checked'));
    });
});

// ─── Option selection parity ─────────────────────────────────────────────────

describe('option selection parity: select', () => {
    it('both strategies set .selected = true and add selected attribute', () => {
        // morphElement strategy — morph the whole <select> so morphElement
        // applies select.value sync AND option attribute sync together.
        const morphSelect = attach(
            '<select><option value="a">A</option><option value="b">B</option></select>'
        );
        morphSelect.value = 'a';
        const desiredSelect = detached(
            '<select><option value="a">A</option><option value="b" selected>B</option></select>'
        );
        morphElement(morphSelect, desiredSelect);
        const morphOption = morphSelect.querySelector('option[value="b"]');

        // applySinglePatch strategy — patch the OPTION element directly.
        const patchSelect = attach(
            '<select><option value="a">A</option><option value="b" dj-id="opt-b">B</option></select>'
        );
        patchSelect.value = 'a';
        _applySinglePatch({ type: 'SetAttr', d: 'opt-b', path: [], key: 'selected', value: '' });
        const patchOption = patchSelect.querySelector('option[value="b"]');

        // Property
        expect(morphOption.selected).toBe(true);
        expect(patchOption.selected).toBe(true);

        // Attribute
        expect(morphOption.hasAttribute('selected')).toBe(true);
        expect(patchOption.hasAttribute('selected')).toBe(true);

        // Parity
        expect(morphOption.selected).toBe(patchOption.selected);
        expect(morphOption.hasAttribute('selected')).toBe(patchOption.hasAttribute('selected'));
    });
});

describe('option selection parity: deselect', () => {
    it('deselecting A while selecting B produces identical state via both strategies', () => {
        // In a single-select, simply removing `selected` from the only selected
        // option causes the browser to implicitly re-select the first option.
        // A realistic server update switches selection from A → B, which is what
        // both strategies must produce: optA.selected=false, optB.selected=true.

        // morphElement strategy — morph the whole SELECT from A→B
        const morphSelect = attach(
            '<select><option value="a" selected>A</option><option value="b">B</option></select>'
        );
        morphSelect.value = 'a';
        morphElement(
            morphSelect,
            detached('<select><option value="a">A</option><option value="b" selected>B</option></select>')
        );
        const morphOptA = morphSelect.querySelector('option[value="a"]');
        const morphOptB = morphSelect.querySelector('option[value="b"]');

        // applySinglePatch strategy — patch OPTION elements individually
        const patchSelect = attach(
            '<select>' +
            '<option value="a" selected dj-id="ds-opt-a">A</option>' +
            '<option value="b" dj-id="ds-opt-b">B</option>' +
            '</select>'
        );
        const patchOptA = patchSelect.querySelector('option[value="a"]');
        const patchOptB = patchSelect.querySelector('option[value="b"]');
        // Server sends: add selected to B, remove selected from A
        _applySinglePatch({ type: 'SetAttr',    d: 'ds-opt-b', path: [], key: 'selected', value: '' });
        _applySinglePatch({ type: 'RemoveAttr', d: 'ds-opt-a', path: [], key: 'selected' });

        // Previously selected option A is now deselected
        expect(morphOptA.selected).toBe(false);
        expect(patchOptA.selected).toBe(false);

        expect(morphOptA.hasAttribute('selected')).toBe(false);
        expect(patchOptA.hasAttribute('selected')).toBe(false);

        // Newly selected option B is now selected
        expect(morphOptB.selected).toBe(true);
        expect(patchOptB.selected).toBe(true);

        expect(morphOptB.hasAttribute('selected')).toBe(true);
        expect(patchOptB.hasAttribute('selected')).toBe(true);

        // Parity
        expect(morphOptA.selected).toBe(patchOptA.selected);
        expect(morphOptB.selected).toBe(patchOptB.selected);
    });
});
