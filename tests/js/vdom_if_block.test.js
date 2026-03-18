/**
 * Regression tests for VDOM patching with {% if %} blocks (#559).
 *
 * When {% if %} blocks toggle, the Rust VDOM emits <!--dj-if--> comment
 * placeholders and counts them in child indices. The client must also
 * count comment nodes to keep indices aligned.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function(value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

dom.window.eval(clientCode);

const {
    getSignificantChildren,
    createNodeFromVNode,
} = dom.window.djust;

const document = dom.window.document;

describe('Comment node handling for {% if %} blocks (#559)', () => {

    describe('getSignificantChildren', () => {
        it('includes comment nodes in child list', () => {
            const parent = document.createElement('div');
            parent.innerHTML = '<h5>Title</h5><!--dj-if--><p>Description</p>';

            const children = getSignificantChildren(parent);

            // Should include: h5, comment, p (3 children)
            expect(children.length).toBe(3);
            expect(children[0].tagName).toBe('H5');
            expect(children[1].nodeType).toBe(dom.window.Node.COMMENT_NODE);
            expect(children[1].textContent).toBe('dj-if');
            expect(children[2].tagName).toBe('P');
        });

        it('still filters whitespace-only text nodes', () => {
            const parent = document.createElement('div');
            parent.appendChild(document.createTextNode('  \n  '));
            parent.appendChild(document.createElement('span'));
            parent.appendChild(document.createComment('dj-if'));

            const children = getSignificantChildren(parent);

            // Whitespace text filtered, span + comment kept
            expect(children.length).toBe(2);
            expect(children[0].tagName).toBe('SPAN');
            expect(children[1].nodeType).toBe(dom.window.Node.COMMENT_NODE);
        });

        it('preserves correct indices matching server VDOM', () => {
            // Server VDOM for: <h5>Title</h5>{% if show %}...{% endif %}<p>Desc</p>
            // When show=False: h5, <!--dj-if-->, p → indices 0, 1, 2
            const parent = document.createElement('div');
            parent.innerHTML = '<h5>Title</h5><!--dj-if--><p>Description</p>';

            const children = getSignificantChildren(parent);

            // Index 1 should be the comment (matching server index)
            expect(children[1].nodeType).toBe(dom.window.Node.COMMENT_NODE);
            // Index 2 should be <p> (matching server index)
            expect(children[2].tagName).toBe('P');
        });
    });

    describe('createNodeFromVNode', () => {
        it('creates a comment node for #comment tag', () => {
            const node = createNodeFromVNode({ tag: '#comment', text: 'dj-if' });

            expect(node.nodeType).toBe(dom.window.Node.COMMENT_NODE);
            expect(node.textContent).toBe('dj-if');
        });

        it('creates a comment with empty text when no text provided', () => {
            const node = createNodeFromVNode({ tag: '#comment' });

            expect(node.nodeType).toBe(dom.window.Node.COMMENT_NODE);
            expect(node.textContent).toBe('');
        });

        it('still creates text nodes for #text tag', () => {
            const node = createNodeFromVNode({ tag: '#text', text: 'hello' });

            expect(node.nodeType).toBe(dom.window.Node.TEXT_NODE);
            expect(node.textContent).toBe('hello');
        });

        it('still creates element nodes for HTML tags', () => {
            const node = createNodeFromVNode({ tag: 'div', attrs: { class: 'test' } });

            expect(node.nodeType).toBe(dom.window.Node.ELEMENT_NODE);
            expect(node.tagName).toBe('DIV');
            expect(node.getAttribute('class')).toBe('test');
        });
    });
});
