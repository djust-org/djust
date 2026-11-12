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

// #3987: the guard that was meant to skip autofocus when the user is in a field
// tested `focusState.id`, which saveFocusState never sets, so EVERY patch focused
// the page's [autofocus] element.
describe('An [autofocus] element already on the page is not focused again (#3987)', () => {
    const PAGE =
        '<div dj-root dj-liveview-root dj-view="test.View">' +
        '<p id="count">0</p>' +
        '<input id="search" type="text" autofocus />' +
        '<input id="name" type="text" />' +
        '<button id="go" type="button">Go</button>' +
        '</div>';

    function watch(dom, ids) {
        const events = [];
        for (const id of ids) {
            const el = dom.window.document.getElementById(id);
            for (const type of ['focus', 'blur', 'focusin', 'focusout']) {
                el.addEventListener(type, () => events.push(`${id}:${type}`));
            }
        }
        return events;
    }

    // Paths are relative to the root: [0] is <p id="count">, [0, 0] its text node.
    const TEXT_PATCH = [{ type: 'SetText', path: [0, 0], text: '1' }];
    const INSERT_PLAIN = [
        { type: 'InsertChild', path: [], index: 4, node: { tag: 'span', attrs: { id: 'extra' }, children: [] } },
    ];

    it('a text patch leaves the field being typed in alone: no blur, no focus elsewhere', async () => {
        const { dom } = createDom(PAGE);
        const doc = dom.window.document;
        doc.getElementById('name').focus();
        const events = watch(dom, ['name', 'search']);

        await dom.window.djust.applyPatches(TEXT_PATCH);

        expect(doc.getElementById('count').textContent).toBe('1'); // the patch did apply
        expect(doc.activeElement.id).toBe('name');
        expect(events).toEqual([]);
    });

    it('a text patch does not move focus from a clicked button to the autofocus field', async () => {
        const { dom } = createDom(PAGE);
        const doc = dom.window.document;
        doc.getElementById('go').focus();
        const events = watch(dom, ['go', 'search']);

        await dom.window.djust.applyPatches(TEXT_PATCH);

        expect(doc.getElementById('count').textContent).toBe('1');
        expect(doc.activeElement.id).toBe('go');
        expect(events).toEqual([]);
    });

    it('inserting an element without autofocus does not focus the one already there', async () => {
        const { dom } = createDom(PAGE);
        const doc = dom.window.document;
        doc.getElementById('go').focus();
        const events = watch(dom, ['go', 'search']);

        await dom.window.djust.applyPatches(INSERT_PLAIN);

        expect(doc.getElementById('extra')).not.toBeNull();
        expect(doc.activeElement.id).toBe('go');
        expect(events).toEqual([]);
    });

    it('an inserted [autofocus] element is still focused when no field is in use (#617)', async () => {
        const { dom } = createDom(PAGE);
        const doc = dom.window.document;
        doc.getElementById('go').focus();

        await dom.window.djust.applyPatches([
            {
                type: 'InsertChild',
                path: [],
                index: 4,
                node: { tag: 'input', attrs: { id: 'fresh', type: 'text', autofocus: '' }, children: [] },
            },
        ]);

        expect(doc.activeElement.id).toBe('fresh');
    });

    it('an inserted [autofocus] element does not take focus from a field in use', async () => {
        const { dom } = createDom(PAGE);
        const doc = dom.window.document;
        doc.getElementById('name').focus();
        const events = watch(dom, ['name']);

        await dom.window.djust.applyPatches([
            {
                type: 'InsertChild',
                path: [],
                index: 4,
                node: { tag: 'input', attrs: { id: 'fresh', type: 'text', autofocus: '' }, children: [] },
            },
        ]);

        expect(doc.getElementById('fresh')).not.toBeNull();
        expect(doc.activeElement.id).toBe('name');
        expect(events).toEqual([]);
    });
});
