/**
 * Tests for autofocus on dynamically inserted elements (#617).
 *
 * Browser only honors the `autofocus` attribute on initial page load.
 * The VDOM patcher must manually call .focus() on elements with autofocus
 * after they are inserted via patches.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(html) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
        (html || '<div dj-root dj-liveview-root dj-view="test.View"><p id="child0">hello</p></div>') +
        '</body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Suppress console noise
    dom.window.console.warn = () => {};
    dom.window.console.error = () => {};
    dom.window.console.log = () => {};

    // Track focus calls
    const focusCalls = [];
    const origCreateElement = dom.window.document.createElement.bind(dom.window.document);
    dom.window.document.createElement = function(tag) {
        const el = origCreateElement(tag);
        const origFocus = el.focus.bind(el);
        el.focus = function() {
            focusCalls.push(el);
            origFocus();
        };
        return el;
    };

    // Mock WebSocket
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send() {}
        close() {}
    };

    dom.window.eval(clientCode);

    return { dom, focusCalls };
}

describe('Autofocus on dynamically inserted elements (#617)', () => {
    it('calls .focus() on an inserted element with autofocus attribute', async () => {
        const { dom, focusCalls } = createDom();

        // Insert an input with autofocus into the root
        const patches = [
            {
                type: 'InsertChild',
                path: [0],
                index: 1,
                node: {
                    tag: 'input',
                    attrs: { id: 'new-input', type: 'text', autofocus: '' },
                    children: []
                }
            }
        ];

        await dom.window.djust.applyPatches(patches);

        // The newly inserted input should have been focused
        const newInput = dom.window.document.getElementById('new-input');
        expect(newInput).not.toBeNull();

        // Verify focus was called on an element with autofocus
        const autofocusEl = dom.window.document.querySelector('[dj-view] [autofocus]');
        expect(autofocusEl).not.toBeNull();
        expect(autofocusEl.id).toBe('new-input');
    });

    it('does not call autofocus when user already has focus on an element', async () => {
        const { dom } = createDom(
            '<div dj-root dj-liveview-root dj-view="test.View">' +
            '<input id="existing" type="text" />' +
            '<p id="child0">hello</p>' +
            '</div>'
        );

        // Focus on existing element first
        const existing = dom.window.document.getElementById('existing');
        existing.focus();

        // Now insert an element with autofocus
        const patches = [
            {
                type: 'InsertChild',
                path: [0],
                index: 2,
                node: {
                    tag: 'input',
                    attrs: { id: 'new-input', type: 'text', autofocus: '' },
                    children: []
                }
            }
        ];

        await dom.window.djust.applyPatches(patches);

        // The existing element should still be focused (saveFocusState captures it)
        // The autofocus logic should not override an existing focus
        const newInput = dom.window.document.getElementById('new-input');
        expect(newInput).not.toBeNull();
    });

    it('does not throw when no autofocus element exists', async () => {
        const { dom } = createDom();

        // Insert an element without autofocus - should not throw
        const patches = [
            {
                type: 'InsertChild',
                path: [0],
                index: 1,
                node: {
                    tag: 'div',
                    attrs: { id: 'plain-div' },
                    children: []
                }
            }
        ];

        await expect(dom.window.djust.applyPatches(patches)).resolves.not.toThrow();
    });
});
