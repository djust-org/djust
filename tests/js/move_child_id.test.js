/**
 * Tests for MoveChild with child_d (ID-based child resolution).
 *
 * Verifies that MoveChild resolves the child to move by dj-id
 * instead of stale index when child_d is provided.
 * Regression test for Issue #225.
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

describe('MoveChild with child_d — ID-based resolution (#225)', () => {
    it('should resolve child by dj-id when child_d is provided', () => {
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const text = document.createTextNode('hello');
        const section = document.createElement('section');
        section.setAttribute('dj-id', 's1');
        const ul = document.createElement('ul');
        ul.setAttribute('dj-id', 'u1');

        parent.appendChild(text);
        parent.appendChild(section);
        parent.appendChild(ul);
        attachToDOM(parent);

        // Move ul (index 2) to index 0 using child_d
        applySinglePatch({
            type: 'MoveChild',
            path: [0],
            d: 'root',
            from: 2,
            to: 0,
            child_d: 'u1',
        });

        const children = getSignificantChildren(parent);
        expect(children[0].tagName).toBe('UL');
        expect(children[2].tagName).toBe('SECTION');
    });

    it('should find child by ID even when index is stale after prior moves', () => {
        // Initial DOM: [text, section(dj-id=s1), ul(dj-id=u1)]
        // Move 1: ul from index 2 to 0 → DOM: [ul, text, section]
        // Move 2: section from (old) index 1 to 1
        //   Without child_d, index 1 is now "text" node (wrong!)
        //   With child_d="s1", finds section correctly

        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'root');

        const text = document.createTextNode('hello');
        const section = document.createElement('section');
        section.setAttribute('dj-id', 's1');
        const ul = document.createElement('ul');
        ul.setAttribute('dj-id', 'u1');

        parent.appendChild(text);
        parent.appendChild(section);
        parent.appendChild(ul);
        attachToDOM(parent);

        // Move 1: ul to front
        applySinglePatch({
            type: 'MoveChild',
            path: [0],
            d: 'root',
            from: 2,
            to: 0,
            child_d: 'u1',
        });

        // Move 2: section — stale index 1 would be text node, but child_d finds section
        applySinglePatch({
            type: 'MoveChild',
            path: [0],
            d: 'root',
            from: 1,
            to: 1,
            child_d: 's1',
        });

        const children = getSignificantChildren(parent);
        expect(children[0].tagName).toBe('UL');
        expect(children[1].tagName).toBe('SECTION');
    });

    it('should fall back to index-based resolution when child_d is absent', () => {
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'p1');

        const a = document.createElement('div');
        a.textContent = 'A';
        const b = document.createElement('div');
        b.textContent = 'B';

        parent.appendChild(a);
        parent.appendChild(b);
        attachToDOM(parent);

        // Move without child_d — uses index fallback
        applySinglePatch({
            type: 'MoveChild',
            path: [0],
            d: 'p1',
            from: 1,
            to: 0,
        });

        const children = getSignificantChildren(parent);
        expect(children[0].textContent).toBe('B');
        expect(children[1].textContent).toBe('A');
    });
});
