/**
 * Tests for getNodeByPath path-based fallback.
 *
 * Regression tests for #198: VDOM path fallback corrupts sibling elements
 * when removing list items. Both the Rust VDOM parser and the JS client
 * filter out whitespace-only text nodes, so path indices only count
 * significant nodes (elements + non-whitespace text nodes).
 *
 * Non-breaking spaces (\u00A0 / &nbsp;) are preserved as significant.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

// Load the client module
const clientCode = await import('fs').then(fs =>
    fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8')
);

// Execute in JSDOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

// Polyfill CSS.escape for JSDOM
if (!dom.window.CSS) {
    dom.window.CSS = {};
}
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (str) => str.replace(/([^\w-])/g, '\\$1');
}

// Execute the client code
dom.window.eval(clientCode);

const { _getNodeByPath } = dom.window.djust;
const document = dom.window.document;

describe('getNodeByPath - path fallback with whitespace nodes (#198)', () => {
    let root;

    beforeEach(() => {
        root = document.createElement('div');
        root.setAttribute('dj-root', '');
        document.body.appendChild(root);
    });

    afterEach(() => {
        root.remove();
    });

    it('should skip whitespace text nodes to match server VDOM indices', () => {
        // DOM childNodes: div(A), "\n    ", div(B), "\n    ", div(C)
        // Significant children (what Rust VDOM sees): div(A)=0, div(B)=1, div(C)=2
        root.innerHTML = '<div>A</div>\n    <div>B</div>\n    <div>C</div>';

        // Path [1] should reach the second div (B), skipping whitespace
        const node = _getNodeByPath([1], null);

        expect(node).not.toBeNull();
        expect(node.nodeType).toBe(dom.window.Node.ELEMENT_NODE);
        expect(node.tagName).toBe('DIV');
        expect(node.textContent).toBe('B');
    });

    it('should correctly traverse nested paths skipping whitespace', () => {
        root.innerHTML = [
            '<form>',
            '<div class="field">Field 1</div>',
            '\n            ',
            '<div class="field">Field 2</div>',
            '\n            ',
            '<div class="counter">Count: 5</div>',
            '\n        ',
            '</form>'
        ].join('');

        // Significant children of <form>: div(0), div(1), div(2)
        // Path [0, 2] should reach the counter div
        const counterNode = _getNodeByPath([0, 2], null);

        expect(counterNode).not.toBeNull();
        expect(counterNode.tagName).toBe('DIV');
        expect(counterNode.textContent).toBe('Count: 5');
    });

    it('should reach correct element for SetText patch after list removal', () => {
        root.innerHTML = [
            '<div class="todo-app">',
            '\n    ',
            '<ul class="todo-list"></ul>',
            '\n    ',
            '<p class="counter">0 items left</p>',
            '\n',
            '</div>'
        ].join('');

        // Significant children of todo-app: ul(0), p(1)
        const counterP = _getNodeByPath([0, 1], null);

        expect(counterP).not.toBeNull();
        expect(counterP.tagName).toBe('P');
        expect(counterP.textContent).toBe('0 items left');

        // The text node inside <p> at path [0, 1, 0]
        const counterText = _getNodeByPath([0, 1, 0], null);
        expect(counterText).not.toBeNull();
        expect(counterText.nodeType).toBe(dom.window.Node.TEXT_NODE);
        expect(counterText.textContent).toBe('0 items left');
    });

    it('should not corrupt sibling pre/code block when list items are removed', () => {
        root.innerHTML = [
            '<div>',
            '\n    ',
            '<ul></ul>',
            '\n    ',
            '<pre><code>function foo() {\n    return 42;\n}</code></pre>',
            '\n',
            '</div>'
        ].join('');

        // Significant children of div: ul(0), pre(1)
        const preNode = _getNodeByPath([0, 1], null);

        expect(preNode).not.toBeNull();
        expect(preNode.tagName).toBe('PRE');
    });

    it('preserves non-breaking space text nodes as significant', () => {
        root.innerHTML = '<div>A</div>\u00A0<div>B</div>';

        // DOM childNodes: div(A), "\u00A0", div(B)
        // Significant: div(A)=0, "\u00A0"=1, div(B)=2
        const nbspNode = _getNodeByPath([1], null);

        expect(nbspNode).not.toBeNull();
        expect(nbspNode.nodeType).toBe(dom.window.Node.TEXT_NODE);
        expect(nbspNode.textContent).toBe('\u00A0');
    });

    it('prefers ID-based lookup over path fallback', () => {
        root.innerHTML = '<div data-dj-id="abc">target</div><div>other</div>';

        const node = _getNodeByPath([99], 'abc'); // bad path, good ID
        expect(node).not.toBeNull();
        expect(node.getAttribute('data-dj-id')).toBe('abc');
    });
});
