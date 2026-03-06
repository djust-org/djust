/**
 * Parity regression tests: morphElement vs applySinglePatch for #422.
 *
 * Issue #422 fixed a divergence where applySinglePatch correctly synced
 * DOM properties (checked, selected) but morphElement only synced
 * attributes — leaving the two VDOM update paths producing different
 * visible DOM state for checkbox, radio, and option elements.
 *
 * Invariant: after applying equivalent changes through both code paths,
 * the resulting DOM property AND attribute state must be identical.
 *
 * These tests guard against future regressions where one path is updated
 * but the other is forgotten.
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

const { _applySinglePatch, morphElement } = dom.window.djust;
const document = dom.window.document;

afterEach(() => {
    document.body.innerHTML = '';
});

/**
 * Create a DOM element attached to document.body so querySelector works
 * for applySinglePatch (which looks up elements by dj-id).
 */
function attach(tag, attrs = {}) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'checked') {
            el.checked = v;
            if (v) el.setAttribute('checked', '');
        } else if (k === 'selected') {
            el.selected = v;
            if (v) el.setAttribute('selected', '');
        } else {
            el.setAttribute(k, v);
        }
    }
    document.body.appendChild(el);
    return el;
}

/**
 * Create a detached DOM element for use as the "desired" state in
 * morphElement (not appended to document.body).
 */
function detached(tag, attrs = {}) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        el.setAttribute(k, v);
    }
    return el;
}

// ---------------------------------------------------------------------------
// Checkbox parity
// ---------------------------------------------------------------------------

describe('parity: checkbox toggle ON', () => {
    it('morphElement and applySinglePatch both set checked = true', () => {
        // --- morphElement path ---
        const morphEl = attach('input', { type: 'checkbox', 'dj-id': 'cb-morph-on' });
        morphEl.checked = false;
        morphEl.removeAttribute('checked');

        const desired = detached('input', { type: 'checkbox', 'dj-id': 'cb-morph-on', checked: '' });
        morphElement(morphEl, desired);

        // --- applySinglePatch path ---
        const patchEl = attach('input', { type: 'checkbox', 'dj-id': 'cb-patch-on' });
        patchEl.checked = false;
        patchEl.removeAttribute('checked');

        _applySinglePatch({ type: 'SetAttr', d: 'cb-patch-on', path: [], key: 'checked', value: '' });

        // Both must agree
        expect(morphEl.checked).toBe(true);
        expect(patchEl.checked).toBe(true);
        expect(morphEl.checked).toBe(patchEl.checked);

        expect(morphEl.hasAttribute('checked')).toBe(true);
        expect(patchEl.hasAttribute('checked')).toBe(true);
        expect(morphEl.hasAttribute('checked')).toBe(patchEl.hasAttribute('checked'));
    });
});

describe('parity: checkbox toggle OFF', () => {
    it('morphElement and applySinglePatch both set checked = false', () => {
        // --- morphElement path ---
        const morphEl = attach('input', { type: 'checkbox', 'dj-id': 'cb-morph-off', checked: true });

        const desired = detached('input', { type: 'checkbox', 'dj-id': 'cb-morph-off' });
        // desired has no checked attribute — morph should remove it
        morphElement(morphEl, desired);

        // --- applySinglePatch path ---
        const patchEl = attach('input', { type: 'checkbox', 'dj-id': 'cb-patch-off', checked: true });

        _applySinglePatch({ type: 'RemoveAttr', d: 'cb-patch-off', path: [], key: 'checked' });

        // Both must agree
        expect(morphEl.checked).toBe(false);
        expect(patchEl.checked).toBe(false);
        expect(morphEl.checked).toBe(patchEl.checked);

        expect(morphEl.hasAttribute('checked')).toBe(false);
        expect(patchEl.hasAttribute('checked')).toBe(false);
        expect(morphEl.hasAttribute('checked')).toBe(patchEl.hasAttribute('checked'));
    });
});

// ---------------------------------------------------------------------------
// Radio parity
// ---------------------------------------------------------------------------

describe('parity: radio select', () => {
    it('morphElement and applySinglePatch both set checked = true on radio', () => {
        // Use distinct name attrs to prevent browser radio-group interference
        const morphEl = attach('input', { type: 'radio', name: 'r-morph-select', value: 'a', 'dj-id': 'rad-morph-sel' });
        morphEl.checked = false;
        morphEl.removeAttribute('checked');

        const desired = detached('input', { type: 'radio', name: 'r-morph-select', value: 'a', 'dj-id': 'rad-morph-sel', checked: '' });
        morphElement(morphEl, desired);

        const patchEl = attach('input', { type: 'radio', name: 'r-patch-select', value: 'a', 'dj-id': 'rad-patch-sel' });
        patchEl.checked = false;
        patchEl.removeAttribute('checked');

        _applySinglePatch({ type: 'SetAttr', d: 'rad-patch-sel', path: [], key: 'checked', value: '' });

        expect(morphEl.checked).toBe(true);
        expect(patchEl.checked).toBe(true);
        expect(morphEl.checked).toBe(patchEl.checked);
    });
});

describe('parity: radio deselect', () => {
    it('morphElement and applySinglePatch both set checked = false on radio', () => {
        const morphEl = attach('input', { type: 'radio', name: 'r-morph-desel', value: 'a', 'dj-id': 'rad-morph-desel', checked: true });

        const desired = detached('input', { type: 'radio', name: 'r-morph-desel', value: 'a', 'dj-id': 'rad-morph-desel' });
        morphElement(morphEl, desired);

        const patchEl = attach('input', { type: 'radio', name: 'r-patch-desel', value: 'a', 'dj-id': 'rad-patch-desel', checked: true });

        _applySinglePatch({ type: 'RemoveAttr', d: 'rad-patch-desel', path: [], key: 'checked' });

        expect(morphEl.checked).toBe(false);
        expect(patchEl.checked).toBe(false);
        expect(morphEl.checked).toBe(patchEl.checked);
    });
});

// ---------------------------------------------------------------------------
// Option / select parity
// ---------------------------------------------------------------------------

describe('parity: option select', () => {
    it('morphElement and applySinglePatch both set selected = true on option', () => {
        // morphElement path: morph the full <select> so the option gets updated
        const morphSelect = document.createElement('select');
        morphSelect.innerHTML = '<option value="a" dj-id="opt-morph-sel">A</option><option value="b">B</option>';
        document.body.appendChild(morphSelect);
        const morphOpt = morphSelect.querySelector('[dj-id="opt-morph-sel"]');
        morphOpt.selected = false;
        morphOpt.removeAttribute('selected');

        const desiredSelect = document.createElement('select');
        desiredSelect.innerHTML = '<option value="a" dj-id="opt-morph-sel" selected>A</option><option value="b">B</option>';
        morphElement(morphSelect, desiredSelect);

        // applySinglePatch path: patch the individual option element
        const patchSelect = document.createElement('select');
        patchSelect.innerHTML = '<option value="a" dj-id="opt-patch-sel">A</option><option value="b">B</option>';
        document.body.appendChild(patchSelect);
        const patchOpt = patchSelect.querySelector('[dj-id="opt-patch-sel"]');
        patchOpt.selected = false;
        patchOpt.removeAttribute('selected');

        _applySinglePatch({ type: 'SetAttr', d: 'opt-patch-sel', path: [], key: 'selected', value: '' });

        expect(morphOpt.selected).toBe(true);
        expect(patchOpt.selected).toBe(true);
        expect(morphOpt.selected).toBe(patchOpt.selected);

        expect(morphOpt.hasAttribute('selected')).toBe(true);
        expect(patchOpt.hasAttribute('selected')).toBe(true);
    });
});

describe('parity: option deselect (A → B switch)', () => {
    it('morphElement and applySinglePatch produce same state when switching selected option', () => {
        // Realistic scenario: user switches from option A to option B.
        // Option A loses selected, option B gains it.

        // --- morphElement path ---
        const morphSelect = document.createElement('select');
        morphSelect.innerHTML =
            '<option value="a" dj-id="sw-morph-a" selected>A</option>' +
            '<option value="b" dj-id="sw-morph-b">B</option>';
        document.body.appendChild(morphSelect);

        const desiredSelect = document.createElement('select');
        desiredSelect.innerHTML =
            '<option value="a" dj-id="sw-morph-a">A</option>' +
            '<option value="b" dj-id="sw-morph-b" selected>B</option>';
        morphElement(morphSelect, desiredSelect);

        const morphOptA = morphSelect.querySelector('[dj-id="sw-morph-a"]');
        const morphOptB = morphSelect.querySelector('[dj-id="sw-morph-b"]');

        // --- applySinglePatch path ---
        const patchSelect = document.createElement('select');
        patchSelect.innerHTML =
            '<option value="a" dj-id="sw-patch-a" selected>A</option>' +
            '<option value="b" dj-id="sw-patch-b">B</option>';
        document.body.appendChild(patchSelect);

        // Remove selected from A, add to B
        _applySinglePatch({ type: 'RemoveAttr', d: 'sw-patch-a', path: [], key: 'selected' });
        _applySinglePatch({ type: 'SetAttr', d: 'sw-patch-b', path: [], key: 'selected', value: '' });

        const patchOptA = patchSelect.querySelector('[dj-id="sw-patch-a"]');
        const patchOptB = patchSelect.querySelector('[dj-id="sw-patch-b"]');

        // Option A: both paths deselected
        expect(morphOptA.selected).toBe(false);
        expect(patchOptA.selected).toBe(false);
        expect(morphOptA.selected).toBe(patchOptA.selected);
        expect(morphOptA.hasAttribute('selected')).toBe(patchOptA.hasAttribute('selected'));

        // Option B: both paths selected
        expect(morphOptB.selected).toBe(true);
        expect(patchOptB.selected).toBe(true);
        expect(morphOptB.selected).toBe(patchOptB.selected);
        expect(morphOptB.hasAttribute('selected')).toBe(patchOptB.hasAttribute('selected'));
    });
});
