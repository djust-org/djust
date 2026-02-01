/**
 * Tests for getNodeByPath path-based fallback.
 *
 * Regression tests for #198: VDOM path fallback corrupts sibling elements
 * when removing list items because whitespace text nodes are filtered out
 * during path traversal, causing index mismatches with the server's Rust VDOM.
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
        root.setAttribute('data-djust-root', '');
        document.body.appendChild(root);
    });

    afterEach(() => {
        root.remove();
    });

    it('should count whitespace text nodes in path indices to match server VDOM', () => {
        // Server VDOM has: [element(0), whitespace(1), element(2), whitespace(3), element(4)]
        // Path [2] should reach the second element, not skip whitespace
        root.innerHTML = '<div>A</div>\n    <div>B</div>\n    <div>C</div>';

        // The DOM childNodes are: div, "\n    ", div, "\n    ", div
        // Path [2] should reach the second div (containing "B"), not the third
        const node = _getNodeByPath([2], null);

        expect(node).not.toBeNull();
        expect(node.nodeType).toBe(dom.window.Node.ELEMENT_NODE);
        expect(node.tagName).toBe('DIV');
        expect(node.textContent).toBe('B');
    });

    it('should reach whitespace text nodes by index', () => {
        root.innerHTML = '<div>A</div>\n    <div>B</div>';

        // Path [1] should reach the whitespace text node "\n    "
        const node = _getNodeByPath([1], null);

        expect(node).not.toBeNull();
        expect(node.nodeType).toBe(dom.window.Node.TEXT_NODE);
        expect(node.textContent.trim()).toBe('');
    });

    it('should correctly traverse nested paths with whitespace nodes', () => {
        // Simulates a form with whitespace between fields - the exact bug scenario
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

        const form = root.childNodes[0]; // the <form>
        // form.childNodes: div(0), "\n..."(1), div(2), "\n..."(3), div(4), "\n..."(5)

        // Path [0, 4] should reach the counter div (index 4 in form's childNodes)
        const counterNode = _getNodeByPath([0, 4], null);

        expect(counterNode).not.toBeNull();
        expect(counterNode.tagName).toBe('DIV');
        expect(counterNode.textContent).toBe('Count: 5');
    });

    it('should reach correct text node for SetText patch after list removal', () => {
        // This is the exact bug scenario from #198:
        // A todo list with items removed, sibling counter text gets corrupted
        // because path fallback skips whitespace nodes
        root.innerHTML = [
            '<div class="todo-app">',
            '\n    ',
            '<ul class="todo-list"></ul>',  // empty after removing items
            '\n    ',
            '<p class="counter">0 items left</p>',
            '\n',
            '</div>'
        ].join('');

        // Server sends SetText patch with path [0, 4, 0] targeting the counter text
        // div.todo-app childNodes: "\n    "(0), ul(1), "\n    "(2), p(3), "\n"(4)
        // Wait -- let me verify the actual DOM structure
        const todoApp = root.childNodes[0];

        // Find the <p> counter - it should be at index 4 in childNodes
        // (childNodes: "\n    ", ul, "\n    ", p, "\n")
        const counterP = _getNodeByPath([0, 3], null);

        expect(counterP).not.toBeNull();
        expect(counterP.tagName).toBe('P');
        expect(counterP.textContent).toBe('0 items left');

        // The text node inside <p> at path [0, 3, 0]
        const counterText = _getNodeByPath([0, 3, 0], null);
        expect(counterText).not.toBeNull();
        expect(counterText.nodeType).toBe(dom.window.Node.TEXT_NODE);
        expect(counterText.textContent).toBe('0 items left');
    });

    it('should not corrupt sibling pre/code block when list items are removed', () => {
        // Another manifestation of #198: patches meant for a counter
        // get applied to text inside a <pre> code block
        root.innerHTML = [
            '<div>',
            '\n    ',
            '<ul></ul>',
            '\n    ',
            '<pre><code>function foo() {\n    return 42;\n}</code></pre>',
            '\n',
            '</div>'
        ].join('');

        // Path [0, 3] should reach the <pre>, not skip whitespace and land elsewhere
        const preNode = _getNodeByPath([0, 3], null);

        expect(preNode).not.toBeNull();
        expect(preNode.tagName).toBe('PRE');
    });

    it('prefers ID-based lookup over path fallback', () => {
        root.innerHTML = '<div data-dj-id="abc">target</div><div>other</div>';

        const node = _getNodeByPath([99], 'abc'); // bad path, good ID
        expect(node).not.toBeNull();
        expect(node.getAttribute('data-dj-id')).toBe('abc');
    });
});
