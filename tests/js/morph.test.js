/**
 * Tests for morphChildren() and morphElement() — DOM morphing that
 * preserves event listeners by reusing existing DOM nodes instead of
 * innerHTML replacement.
 *
 * Fixes: event listeners bound via DOMContentLoaded (e.g., file upload
 * drag/drop) were destroyed on every html_update because innerHTML
 * creates new elements.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

// Load the client module
const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// Execute in JSDOM environment
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

// Polyfill CSS.escape (not available in JSDOM)
if (!dom.window.CSS) {
    dom.window.CSS = {};
}
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function(value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

// Execute the client code
dom.window.eval(clientCode);

const { morphChildren, morphElement } = dom.window.djust;

describe('morphChildren', () => {
    let document;

    beforeEach(() => {
        document = dom.window.document;
    });

    function el(html) {
        const div = document.createElement('div');
        div.innerHTML = html;
        return div;
    }

    it('updates text content without replacing the text node', () => {
        const existing = el('hello');
        const textNode = existing.childNodes[0];
        const desired = el('world');

        morphChildren(existing, desired);

        expect(existing.textContent).toBe('world');
        // Same text node object, not a replacement
        expect(existing.childNodes[0]).toBe(textNode);
    });

    it('preserves element identity when tags match (no ids)', () => {
        const existing = el('<div class="a">old</div>');
        const child = existing.firstChild;
        const desired = el('<div class="b">new</div>');

        morphChildren(existing, desired);

        expect(existing.innerHTML).toBe('<div class="b">new</div>');
        // Same DOM node reused
        expect(existing.firstChild).toBe(child);
    });

    it('matches elements by id', () => {
        const existing = el('<span id="x">old</span>');
        const span = existing.querySelector('#x');
        const desired = el('<span id="x">new</span>');

        morphChildren(existing, desired);

        expect(existing.querySelector('#x').textContent).toBe('new');
        expect(existing.querySelector('#x')).toBe(span);
    });

    it('reorders keyed elements without destroying them', () => {
        const existing = el('<div id="a">A</div><div id="b">B</div><div id="c">C</div>');
        const a = existing.querySelector('#a');
        const b = existing.querySelector('#b');
        const c = existing.querySelector('#c');

        const desired = el('<div id="c">C</div><div id="a">A</div><div id="b">B</div>');

        morphChildren(existing, desired);

        const children = Array.from(existing.children);
        expect(children[0]).toBe(c);
        expect(children[1]).toBe(a);
        expect(children[2]).toBe(b);
    });

    it('removes elements not in desired', () => {
        const existing = el('<div>keep</div><span>remove</span>');
        const desired = el('<div>keep</div>');

        morphChildren(existing, desired);

        expect(existing.children.length).toBe(1);
        expect(existing.firstChild.tagName).toBe('DIV');
    });

    it('adds new elements from desired', () => {
        const existing = el('<div>one</div>');
        const desired = el('<div>one</div><span>two</span>');

        morphChildren(existing, desired);

        expect(existing.children.length).toBe(2);
        expect(existing.children[1].tagName).toBe('SPAN');
        expect(existing.children[1].textContent).toBe('two');
    });

    it('preserves event listeners on reused elements', () => {
        const existing = el('<button id="btn">click</button>');
        const btn = existing.querySelector('#btn');
        let clicked = false;
        btn.addEventListener('click', () => { clicked = true; });

        const desired = el('<button id="btn">click me</button>');
        morphChildren(existing, desired);

        // Button should still be the same DOM node with its listener
        expect(existing.querySelector('#btn')).toBe(btn);
        btn.click();
        expect(clicked).toBe(true);
    });

    it('handles empty existing container', () => {
        const existing = el('');
        const desired = el('<div>new</div>');

        morphChildren(existing, desired);

        expect(existing.children.length).toBe(1);
        expect(existing.firstChild.textContent).toBe('new');
    });

    it('handles empty desired container', () => {
        const existing = el('<div>old</div>');
        const desired = el('');

        morphChildren(existing, desired);

        expect(existing.children.length).toBe(0);
    });

    it('handles mixed text and element nodes', () => {
        const existing = el('text1<div>elem</div>text2');
        const div = existing.querySelector('div');
        const desired = el('updated<div>elem</div>changed');

        morphChildren(existing, desired);

        expect(existing.childNodes[0].textContent).toBe('updated');
        expect(existing.querySelector('div')).toBe(div);
        expect(existing.childNodes[2].textContent).toBe('changed');
    });

    it('replaces element when tag changes at same position', () => {
        const existing = el('<div>old</div>');
        const desired = el('<span>new</span>');

        morphChildren(existing, desired);

        expect(existing.children.length).toBe(1);
        expect(existing.firstChild.tagName).toBe('SPAN');
    });
});

describe('morphElement', () => {
    let document;

    beforeEach(() => {
        document = dom.window.document;
    });

    it('updates attributes', () => {
        const existing = document.createElement('div');
        existing.setAttribute('class', 'old');
        existing.setAttribute('data-x', 'remove');

        const desired = document.createElement('div');
        desired.setAttribute('class', 'new');
        desired.setAttribute('data-y', 'add');

        morphElement(existing, desired);

        expect(existing.getAttribute('class')).toBe('new');
        expect(existing.getAttribute('data-y')).toBe('add');
        expect(existing.hasAttribute('data-x')).toBe(false);
    });

    it('preserves data-liveview binding flags', () => {
        const existing = document.createElement('button');
        existing.setAttribute('dj-click', 'handler');
        existing.dataset.liveviewClickBound = 'true';

        const desired = document.createElement('button');
        desired.setAttribute('dj-click', 'handler');
        // Server HTML doesn't include binding flags

        morphElement(existing, desired);

        // Binding flag should be preserved
        expect(existing.dataset.liveviewClickBound).toBe('true');
    });

    it('skips dj-update="ignore" elements', () => {
        const existing = document.createElement('div');
        existing.setAttribute('dj-update', 'ignore');
        existing.textContent = 'user content';

        const desired = document.createElement('div');
        desired.setAttribute('dj-update', 'ignore');
        desired.textContent = 'server content';

        morphElement(existing, desired);

        // Content should NOT be changed
        expect(existing.textContent).toBe('user content');
    });

    it('recurses into children', () => {
        const existing = document.createElement('div');
        existing.innerHTML = '<span>old</span>';
        const span = existing.firstChild;

        const desired = document.createElement('div');
        desired.innerHTML = '<span>new</span>';

        morphElement(existing, desired);

        expect(existing.firstChild).toBe(span);
        expect(span.textContent).toBe('new');
    });

    it('replaces when tag differs', () => {
        const container = document.createElement('div');
        const existing = document.createElement('div');
        existing.textContent = 'old';
        container.appendChild(existing);

        const desired = document.createElement('span');
        desired.textContent = 'new';

        morphElement(existing, desired);

        expect(container.firstChild.tagName).toBe('SPAN');
        expect(container.firstChild.textContent).toBe('new');
    });

    it('syncs select element selectedIndex', () => {
        const existing = document.createElement('select');
        existing.innerHTML = '<option value="a">A</option><option value="b">B</option><option value="c">C</option>';
        existing.value = 'a';

        const desired = document.createElement('select');
        desired.innerHTML = '<option value="a">A</option><option value="b" selected>B</option><option value="c">C</option>';

        morphElement(existing, desired);

        expect(existing.children.length).toBe(3);
        expect(existing.querySelector('option[value="b"]').hasAttribute('selected')).toBe(true);
    });

    it('syncs radio button checked state', () => {
        const existing = document.createElement('div');
        existing.innerHTML = '<input type="radio" name="color" value="red" checked><input type="radio" name="color" value="blue">';

        const desired = document.createElement('div');
        desired.innerHTML = '<input type="radio" name="color" value="red"><input type="radio" name="color" value="blue" checked>';

        morphElement(existing, desired);

        const radios = existing.querySelectorAll('input[type="radio"]');
        expect(radios[0].hasAttribute('checked')).toBe(false);
        expect(radios[1].hasAttribute('checked')).toBe(true);
    });

    it('does not morph children of dj-update="append"', () => {
        const existing = document.createElement('ul');
        existing.setAttribute('dj-update', 'append');
        existing.innerHTML = '<li id="a">kept</li><li id="b">also kept</li>';

        const desired = document.createElement('ul');
        desired.setAttribute('dj-update', 'append');
        desired.innerHTML = '<li id="c">new only</li>';

        morphElement(existing, desired);

        // Children should NOT be morphed (append mode preserves existing)
        expect(existing.children.length).toBe(2);
        expect(existing.querySelector('#a').textContent).toBe('kept');
    });
});
