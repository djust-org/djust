/**
 * Tests for checked/selected DOM property sync in VDOM patching.
 *
 * Covers:
 * - createNodeFromVNode: syncs checked on INPUT, selected on OPTION
 * - SetAttr/RemoveAttr: selected on OPTION elements
 * - Radio inputs: checked property sync
 * - activeElement guard: checked updates even when focused (unlike value)
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

const { _applySinglePatch, createNodeFromVNode } = dom.window.djust;
const document = dom.window.document;

afterEach(() => {
    document.body.innerHTML = '';
});

describe('createNodeFromVNode checked property sync', () => {
    it('sets el.checked = true when vnode has checked attr', () => {
        const node = createNodeFromVNode({
            tag: 'input',
            attrs: { type: 'checkbox', checked: '' },
            children: [],
        });
        expect(node.checked).toBe(true);
    });

    it('does not set checked on non-INPUT elements', () => {
        const node = createNodeFromVNode({
            tag: 'div',
            attrs: { checked: 'true' },
            children: [],
        });
        expect(node.checked).toBeUndefined();
    });

    it('sets checked on radio input', () => {
        const node = createNodeFromVNode({
            tag: 'input',
            attrs: { type: 'radio', checked: '' },
            children: [],
        });
        expect(node.checked).toBe(true);
    });

    it('checkbox without checked attr has checked = false', () => {
        const node = createNodeFromVNode({
            tag: 'input',
            attrs: { type: 'checkbox' },
            children: [],
        });
        expect(node.checked).toBe(false);
    });
});

describe('createNodeFromVNode selected property sync', () => {
    it('sets el.selected = true when option vnode has selected attr', () => {
        const node = createNodeFromVNode({
            tag: 'option',
            attrs: { value: 'a', selected: '' },
            children: [],
        });
        expect(node.selected).toBe(true);
    });

    it('does not set selected on non-OPTION elements', () => {
        const node = createNodeFromVNode({
            tag: 'div',
            attrs: { selected: 'true' },
            children: [],
        });
        expect(node.selected).toBeUndefined();
    });
});

describe('SetAttr/RemoveAttr selected on OPTION', () => {
    let option;

    function setup() {
        option = document.createElement('option');
        option.setAttribute('dj-id', 'opt1');
        option.selected = false;
        document.body.appendChild(option);
    }

    it('SetAttr selected sets el.selected = true', () => {
        setup();
        expect(option.selected).toBe(false);

        _applySinglePatch({ type: 'SetAttr', d: 'opt1', path: [], key: 'selected', value: '' });

        expect(option.selected).toBe(true);
        expect(option.hasAttribute('selected')).toBe(true);
    });

    it('RemoveAttr selected sets el.selected = false', () => {
        setup();
        option.selected = true;
        option.setAttribute('selected', '');

        _applySinglePatch({ type: 'RemoveAttr', d: 'opt1', path: [], key: 'selected' });

        expect(option.selected).toBe(false);
        expect(option.hasAttribute('selected')).toBe(false);
    });
});

describe('SetAttr/RemoveAttr checked on radio input', () => {
    let radio;

    function setup() {
        radio = document.createElement('input');
        radio.type = 'radio';
        radio.setAttribute('dj-id', 'rad1');
        radio.checked = false;
        document.body.appendChild(radio);
    }

    it('SetAttr checked sets radio el.checked = true', () => {
        setup();
        _applySinglePatch({ type: 'SetAttr', d: 'rad1', path: [], key: 'checked', value: '' });

        expect(radio.checked).toBe(true);
    });

    it('RemoveAttr checked sets radio el.checked = false', () => {
        setup();
        radio.checked = true;
        radio.setAttribute('checked', '');

        _applySinglePatch({ type: 'RemoveAttr', d: 'rad1', path: [], key: 'checked' });

        expect(radio.checked).toBe(false);
    });
});

describe('SetAttr checked updates even when element is activeElement', () => {
    // Intentional behavioral difference: the `value` branch in SetAttr skips
    // setting el.value when the element is focused (to avoid clobbering in-progress
    // user input). The `checked` branch does NOT skip focused elements because a
    // checkbox toggled by the server should always reflect authoritative state —
    // there is no in-progress "typing" to protect.
    let checkbox;

    function setup() {
        checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.setAttribute('dj-id', 'chk-focused');
        checkbox.checked = false;
        document.body.appendChild(checkbox);
        checkbox.focus();
    }

    it('SetAttr checked updates el.checked when checkbox is document.activeElement', () => {
        setup();
        expect(dom.window.document.activeElement).toBe(checkbox);

        _applySinglePatch({ type: 'SetAttr', d: 'chk-focused', path: [], key: 'checked', value: '' });

        expect(checkbox.checked).toBe(true);
    });

    it('SetAttr value skips el.value update when input is document.activeElement', () => {
        // Contrasting behavior: focused text inputs are skipped so
        // in-progress user input is not overwritten.
        const input = document.createElement('input');
        input.type = 'text';
        input.setAttribute('dj-id', 'txt-focused');
        input.value = 'user typing';
        document.body.appendChild(input);
        input.focus();

        expect(dom.window.document.activeElement).toBe(input);

        _applySinglePatch({ type: 'SetAttr', d: 'txt-focused', path: [], key: 'value', value: 'server value' });

        // el.value is NOT updated because the input is focused
        expect(input.value).toBe('user typing');
        // But the attribute IS updated
        expect(input.getAttribute('value')).toBe('server value');
    });
});
