/**
 * Tests for checked/selected DOM property sync in VDOM patching.
 *
 * Covers:
 * - createNodeFromVNode: syncs checked on INPUT, selected on OPTION
 * - SetAttr/RemoveAttr: selected on OPTION elements
 * - Radio inputs: checked property sync
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
