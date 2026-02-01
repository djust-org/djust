/**
 * Tests for double-bind prevention and stale closure bugs on VDOM-patched elements.
 *
 * Regression test for #201 — delete_todo sends wrong args after patch.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

describe('Double bind prevention on VDOM-inserted elements', () => {
    let dom, window;

    beforeEach(() => {
        dom = new JSDOM(
            `<!DOCTYPE html><html><body>
                <div data-djust-root="true" id="root">
                    <button dj-click="delete_todo(1)" data-dj-id="b1">Delete 1</button>
                    <button dj-click="delete_todo(2)" data-dj-id="b2">Delete 2</button>
                </div>
            </body></html>`,
            { runScripts: 'dangerously' },
        );
        window = dom.window;

        if (!window.CSS) window.CSS = {};
        if (!window.CSS.escape) {
            window.CSS.escape = (str) => String(str).replace(/([^\w-])/g, '\\$1');
        }

        window.WebSocket = class {
            send() {}
            close() {}
        };

        window.fetch = () => Promise.reject(new Error('no fetch'));

        const clientCode = require('fs').readFileSync(
            './python/djust/static/djust/client.js',
            'utf-8',
        );
        window.eval(clientCode);
    });

    it('createNodeFromVNode sets data-liveview-click-bound on dj-click elements', () => {
        const elem = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(1)', 'data-dj-id': 'b2' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });
        expect(elem.dataset.liveviewClickBound).toBe('true');
    });

    it('bindLiveViewEvents skips elements already marked as bound', () => {
        const newBtn = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(1)', 'data-dj-id': 'b2' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });
        expect(newBtn.dataset.liveviewClickBound).toBe('true');

        const root = window.document.getElementById('root');
        root.querySelector('[data-dj-id="b1"]').replaceWith(newBtn);

        let addEventCount = 0;
        const origAddEvent = newBtn.addEventListener.bind(newBtn);
        newBtn.addEventListener = function(type, ...args) {
            if (type === 'click') addEventCount++;
            return origAddEvent(type, ...args);
        };

        window.djust.bindLiveViewEvents();
        expect(addEventCount).toBe(0);
    });

    it('sets bound flag for other event types (submit, change)', () => {
        const form = window.djust.createNodeFromVNode({
            tag: 'form', attrs: { 'dj-submit': 'save' }, children: [],
        });
        const input = window.djust.createNodeFromVNode({
            tag: 'input', attrs: { 'dj-change': 'validate', type: 'text', name: 'email' }, children: [],
        });
        expect(form.dataset.liveviewSubmitBound).toBe('true');
        expect(input.dataset.liveviewChangeBound).toBe('true');
    });

    it('createNodeFromVNode sets dj-click as DOM attribute', () => {
        const btn = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(2)', 'data-dj-id': 'b2' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });
        expect(btn.getAttribute('dj-click')).toBe('delete_todo(2)');
        expect(btn.getAttribute('data-dj-id')).toBe('b2');
        expect(btn.dataset.liveviewClickBound).toBe('true');
    });
});
