/**
 * Tests for InsertChild with ref_d and RemoveChild with child_d (ID-based resolution).
 *
 * Verifies that InsertChild/RemoveChild resolve by dj-id
 * instead of stale index when ref_d/child_d is provided.
 * Fixes Issue #410.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

let dom;
let document;
let applySinglePatch;
let getSignificantChildren;

beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        runScripts: 'dangerously',
    });
    document = dom.window.document;

    // Polyfill CSS.escape for JSDOM
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (s) => String(s).replace(/([^\w-])/g, '\\$1');
    }

    const clientCode = require('fs').readFileSync(
        './python/djust/static/djust/client.js',
        'utf-8'
    );
    dom.window.eval(clientCode);
    applySinglePatch = dom.window.djust._applySinglePatch;
    getSignificantChildren = dom.window.djust.getSignificantChildren;
});

/**
 * Helper: attach a parent element to the document body inside a
 * [dj-live] container so getNodeByPath / querySelector can find it.
 */
function attachToDOM(parent) {
    const container = document.createElement('div');
    container.setAttribute('dj-live', '');
    container.appendChild(parent);
    document.body.appendChild(container);
}

describe('RemoveChild with child_d — ID-based resolution (#410)', () => {
    it('should resolve child by dj-id when child_d is provided', () => {
        // DOM: [div(a), div(b), div(c)]
        // Remove div(b) by child_d, even if index is wrong
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const a = document.createElement('div');
        a.setAttribute('dj-id', 'a');
        a.textContent = 'A';
        const b = document.createElement('div');
        b.setAttribute('dj-id', 'b');
        b.textContent = 'B';
        const c = document.createElement('div');
        c.setAttribute('dj-id', 'c');
        c.textContent = 'C';

        parent.appendChild(a);
        parent.appendChild(b);
        parent.appendChild(c);
        attachToDOM(parent);

        // Remove child_d='b' — should remove B regardless of index
        applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            d: 'root',
            index: 1,
            child_d: 'b',
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(2);
        expect(children[0].textContent).toBe('A');
        expect(children[1].textContent).toBe('C');
    });

    it('should find child by ID even when index is stale after prior removal', () => {
        // DOM: [div(a), div(b), div(c), div(d)]
        // Remove 1: div(a) at index 0 → DOM: [div(b), div(c), div(d)]
        // Remove 2: div(c) with stale index 2 but child_d='c'
        //   Without child_d, index 2 would target div(d) (wrong!)
        //   With child_d='c', finds div(c) correctly

        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const elems = ['a', 'b', 'c', 'd'].map(id => {
            const el = document.createElement('div');
            el.setAttribute('dj-id', id);
            el.textContent = id.toUpperCase();
            parent.appendChild(el);
            return el;
        });
        attachToDOM(parent);

        // Remove 1: div(a) at index 0
        applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            d: 'root',
            index: 0,
            child_d: 'a',
        });

        // Remove 2: div(c) — stale index 2 would be div(d), but child_d finds div(c)
        applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            d: 'root',
            index: 2,
            child_d: 'c',
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(2);
        expect(children[0].textContent).toBe('B');
        expect(children[1].textContent).toBe('D');
    });

    it('should fall back to index-based resolution when child_d is absent', () => {
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const a = document.createElement('div');
        a.textContent = 'A';
        const b = document.createElement('div');
        b.textContent = 'B';

        parent.appendChild(a);
        parent.appendChild(b);
        attachToDOM(parent);

        // Remove without child_d — uses index fallback
        applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            d: 'root',
            index: 0,
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(1);
        expect(children[0].textContent).toBe('B');
    });
});

describe('InsertChild with ref_d — ID-based resolution (#410)', () => {
    it('should insert before element found by ref_d', () => {
        // DOM: [div(a), div(c)]
        // Insert div(b) before div(c) using ref_d='c'
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const a = document.createElement('div');
        a.setAttribute('dj-id', 'a');
        a.textContent = 'A';
        const c = document.createElement('div');
        c.setAttribute('dj-id', 'c');
        c.textContent = 'C';

        parent.appendChild(a);
        parent.appendChild(c);
        attachToDOM(parent);

        applySinglePatch({
            type: 'InsertChild',
            path: [0],
            d: 'root',
            index: 1,
            ref_d: 'c',
            node: { tag: 'div', attrs: { 'dj-id': 'b' }, children: [{ tag: '#text', text: 'B' }] },
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(3);
        expect(children[0].textContent).toBe('A');
        expect(children[1].textContent).toBe('B');
        expect(children[2].textContent).toBe('C');
    });

    it('should find ref element by ID even when index is stale', () => {
        // DOM: [div(a), div(b), div(c)]
        // Insert 1: new div before div(b) at index 1 → DOM has 4 children
        // Insert 2: new div before div(c) with stale index 2 but ref_d='c'
        //   Without ref_d, index 2 would be div(b) (wrong position)
        //   With ref_d='c', inserts before div(c) correctly

        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const a = document.createElement('div');
        a.setAttribute('dj-id', 'a');
        a.textContent = 'A';
        const b = document.createElement('div');
        b.setAttribute('dj-id', 'b');
        b.textContent = 'B';
        const c = document.createElement('div');
        c.setAttribute('dj-id', 'c');
        c.textContent = 'C';

        parent.appendChild(a);
        parent.appendChild(b);
        parent.appendChild(c);
        attachToDOM(parent);

        // Insert 1: new X before div(b) at index 1
        applySinglePatch({
            type: 'InsertChild',
            path: [0],
            d: 'root',
            index: 1,
            ref_d: 'b',
            node: { tag: 'div', attrs: { 'dj-id': 'x' }, children: [{ tag: '#text', text: 'X' }] },
        });

        // Insert 2: new Y before div(c) — stale index 2 would be div(b), but ref_d finds div(c)
        applySinglePatch({
            type: 'InsertChild',
            path: [0],
            d: 'root',
            index: 2,
            ref_d: 'c',
            node: { tag: 'div', attrs: { 'dj-id': 'y' }, children: [{ tag: '#text', text: 'Y' }] },
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(5);
        expect(children[0].textContent).toBe('A');
        expect(children[1].textContent).toBe('X');
        expect(children[2].textContent).toBe('B');
        expect(children[3].textContent).toBe('Y');
        expect(children[4].textContent).toBe('C');
    });

    it('should fall back to index-based resolution when ref_d is absent', () => {
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const a = document.createElement('div');
        a.textContent = 'A';
        const b = document.createElement('div');
        b.textContent = 'B';

        parent.appendChild(a);
        parent.appendChild(b);
        attachToDOM(parent);

        // Insert without ref_d — uses index fallback
        applySinglePatch({
            type: 'InsertChild',
            path: [0],
            d: 'root',
            index: 1,
            node: { tag: 'div', attrs: {}, children: [{ tag: '#text', text: 'X' }] },
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(3);
        expect(children[0].textContent).toBe('A');
        expect(children[1].textContent).toBe('X');
        expect(children[2].textContent).toBe('B');
    });

    it('should append when ref_d is not provided and index is beyond children', () => {
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const a = document.createElement('div');
        a.textContent = 'A';
        parent.appendChild(a);
        attachToDOM(parent);

        applySinglePatch({
            type: 'InsertChild',
            path: [0],
            d: 'root',
            index: 1,
            node: { tag: 'div', attrs: {}, children: [{ tag: '#text', text: 'B' }] },
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(2);
        expect(children[1].textContent).toBe('B');
    });
});

describe('Combined InsertChild/RemoveChild with conditional {% if %} blocks (#410)', () => {
    it('should correctly handle conditional block removal and insertion', () => {
        // Simulates {% if error %}<div class="error">Error</div>{% endif %}
        // being removed while other elements shift positions.
        //
        // Old DOM: [div(error), div(content), div(footer)]
        // New DOM: [div(content), div(footer)]
        // Patches: RemoveChild(child_d='error')

        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const error = document.createElement('div');
        error.setAttribute('dj-id', 'error');
        error.textContent = 'Error message';
        const content = document.createElement('div');
        content.setAttribute('dj-id', 'content');
        content.textContent = 'Content';
        const footer = document.createElement('div');
        footer.setAttribute('dj-id', 'footer');
        footer.textContent = 'Footer';

        parent.appendChild(error);
        parent.appendChild(content);
        parent.appendChild(footer);
        attachToDOM(parent);

        // Remove the error div using child_d
        applySinglePatch({
            type: 'RemoveChild',
            path: [0],
            d: 'root',
            index: 0,
            child_d: 'error',
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(2);
        expect(children[0].textContent).toBe('Content');
        expect(children[1].textContent).toBe('Footer');
    });

    it('should correctly handle conditional block appearing', () => {
        // Simulates {% if error %}<div class="error">Error</div>{% endif %}
        // appearing, pushing other elements down.
        //
        // Old DOM: [div(content), div(footer)]
        // New DOM: [div(error), div(content), div(footer)]
        // Patches: InsertChild(ref_d='content') at index 0

        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const content = document.createElement('div');
        content.setAttribute('dj-id', 'content');
        content.textContent = 'Content';
        const footer = document.createElement('div');
        footer.setAttribute('dj-id', 'footer');
        footer.textContent = 'Footer';

        parent.appendChild(content);
        parent.appendChild(footer);
        attachToDOM(parent);

        // Insert error div before content using ref_d
        applySinglePatch({
            type: 'InsertChild',
            path: [0],
            d: 'root',
            index: 0,
            ref_d: 'content',
            node: { tag: 'div', attrs: { 'dj-id': 'error' }, children: [{ tag: '#text', text: 'Error message' }] },
        });

        const children = getSignificantChildren(parent);
        expect(children.length).toBe(3);
        expect(children[0].textContent).toBe('Error message');
        expect(children[1].textContent).toBe('Content');
        expect(children[2].textContent).toBe('Footer');
    });
});
