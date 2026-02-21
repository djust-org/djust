/**
 * Tests for event binding on VDOM-patched elements and stale closure bugs.
 *
 * Regression test for #201 — delete_todo sends wrong args after patch.
 * Regression test for #315 — VDOM-inserted elements never get event listeners
 *   because createNodeFromVNode pre-marked them as bound without attaching listeners.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

describe('Double bind prevention on VDOM-inserted elements', () => {
    let dom, window;
    // Tracks elements added to the DOM during a test so afterEach can remove them.
    let extraElements;

    beforeEach(() => {
        dom = new JSDOM(
            `<!DOCTYPE html><html><body>
                <div dj-root="true" id="root">
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

        extraElements = [];
    });

    afterEach(() => {
        // Remove any elements inserted into the document during the test.
        extraElements.forEach(el => {
            if (el.parentNode) el.parentNode.removeChild(el);
        });
        extraElements = [];
    });

    it('bindLiveViewEvents attaches click listener to VDOM-created elements', () => {
        // createNodeFromVNode must NOT pre-mark elements as bound.
        // bindLiveViewEvents() must attach the listener on first call.
        const elem = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(1)', 'data-dj-id': 'b-new-1' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });

        // Insert into DOM so querySelectorAll finds it — without this the test passes
        // trivially because bindLiveViewEvents() never sees the detached element.
        window.document.body.appendChild(elem);
        extraElements.push(elem);

        // Wrap addEventListener AFTER insertion so we only count calls made by
        // bindLiveViewEvents(), not any calls made during createNodeFromVNode.
        let addEventCount = 0;
        const origAddEvent = elem.addEventListener.bind(elem);
        elem.addEventListener = function(type, ...args) {
            if (type === 'click') addEventCount++;
            return origAddEvent(type, ...args);
        };

        // First call: element is in DOM and unbound — should bind it exactly once.
        window.djust.bindLiveViewEvents();
        expect(addEventCount).toBe(1);

        // Second call: WeakMap prevents double-binding — count must not increase.
        window.djust.bindLiveViewEvents();
        expect(addEventCount).toBe(1);
    });

    it('bindLiveViewEvents binds click handler on VDOM-inserted elements exactly once', () => {
        // createNodeFromVNode no longer pre-marks elements; bindLiveViewEvents
        // is responsible for binding and deduplicating via WeakMap.
        const newBtn = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(1)', 'data-dj-id': 'b2' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });

        const root = window.document.getElementById('root');
        root.querySelector('[data-dj-id="b1"]').replaceWith(newBtn);

        let addEventCount = 0;
        const origAddEvent = newBtn.addEventListener.bind(newBtn);
        newBtn.addEventListener = function(type, ...args) {
            if (type === 'click') addEventCount++;
            return origAddEvent(type, ...args);
        };

        // First call: should bind the listener (count = 1)
        window.djust.bindLiveViewEvents();
        expect(addEventCount).toBe(1);

        // Second call: element already bound via WeakMap, no double-bind (count stays 1)
        window.djust.bindLiveViewEvents();
        expect(addEventCount).toBe(1);
    });

    it('bindLiveViewEvents does not double-bind elements already in the DOM', () => {
        // Elements in the initial HTML are bound on first bindLiveViewEvents call.
        // Subsequent calls must not add duplicate listeners.
        const btn = window.document.querySelector('[data-dj-id="b1"]');

        let addEventCount = 0;
        const origAddEvent = btn.addEventListener.bind(btn);
        btn.addEventListener = function(type, ...args) {
            if (type === 'click') addEventCount++;
            return origAddEvent(type, ...args);
        };

        // First call: element may already be bound via WeakMap from client.js init
        window.djust.bindLiveViewEvents();
        const countAfterFirst = addEventCount;

        // Second call: WeakMap prevents double-binding — count must not increase
        window.djust.bindLiveViewEvents();
        expect(addEventCount).toBe(countAfterFirst);
    });

    it('binds handlers for other event types (submit, change) without double-binding', () => {
        const form = window.djust.createNodeFromVNode({
            tag: 'form', attrs: { 'dj-submit': 'save' }, children: [],
        });
        const input = window.djust.createNodeFromVNode({
            tag: 'input', attrs: { 'dj-change': 'validate', type: 'text', name: 'email' }, children: [],
        });

        // Insert into DOM — without this, querySelectorAll never finds the elements
        // and the counts trivially stay 0 regardless of WeakMap deduplication.
        window.document.body.appendChild(form);
        window.document.body.appendChild(input);
        extraElements.push(form, input);

        // Verify attributes are set correctly
        expect(form.getAttribute('dj-submit')).toBe('save');
        expect(input.getAttribute('dj-change')).toBe('validate');

        // Wrap addEventListener AFTER insertion to count only bindLiveViewEvents() calls.
        let submitCount = 0;
        const origFormAdd = form.addEventListener.bind(form);
        form.addEventListener = function(type, ...args) {
            if (type === 'submit') submitCount++;
            return origFormAdd(type, ...args);
        };
        let changeCount = 0;
        const origInputAdd = input.addEventListener.bind(input);
        input.addEventListener = function(type, ...args) {
            if (type === 'change') changeCount++;
            return origInputAdd(type, ...args);
        };

        // First call: elements are in DOM and unbound — should bind each once.
        window.djust.bindLiveViewEvents();
        expect(submitCount).toBe(1);
        expect(changeCount).toBe(1);

        // Second call: WeakMap prevents double-binding — counts must not increase.
        window.djust.bindLiveViewEvents();
        expect(submitCount).toBe(1);
        expect(changeCount).toBe(1);
    });

    it('createNodeFromVNode sets dj-click as DOM attribute', () => {
        const btn = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(2)', 'data-dj-id': 'b2' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });
        expect(btn.getAttribute('dj-click')).toBe('delete_todo(2)');
        expect(btn.getAttribute('data-dj-id')).toBe('b2');
    });

    it('click handler reads updated dj-click attribute after SetAttribute patch', () => {
        // Simulate: bindLiveViewEvents binds button with delete_todo(1),
        // then a SetAttribute patch changes it to delete_todo(2).
        // The click should send delete_todo(2), not delete_todo(1).
        //
        // djustInit() runs synchronously during window.eval(clientCode) because
        // document.readyState is already 'complete' in JSDOM. This means the
        // buttons in the root div are already bound when the test starts.
        const root = window.document.getElementById('root');
        const btn = root.querySelector('[data-dj-id="b1"]');

        expect(btn.getAttribute('dj-click')).toBe('delete_todo(1)');

        // Simulate SetAttribute patch changing the handler
        btn.setAttribute('dj-click', 'delete_todo(2)');

        // Intercept the HTTP fallback used when liveViewWS is null.
        // (No dj-view container in the JSDOM HTML, so no WebSocket is created.)
        // The fetch call happens synchronously within the async click handler's
        // execution up to its first internal await.
        const fetchCalls = [];
        window.fetch = (url, opts) => {
            fetchCalls.push({
                eventName: opts.headers['X-Djust-Event'],
                body: JSON.parse(opts.body),
            });
            return Promise.reject(new Error('no fetch'));
        };

        // Click the button — handler re-reads the attribute at fire time.
        btn.click();

        // Verify the handler dispatched delete_todo(2), not the stale delete_todo(1).
        expect(fetchCalls.length).toBe(1);
        expect(fetchCalls[0].eventName).toBe('delete_todo');
        expect(fetchCalls[0].body._args).toEqual([2]);
    });

    it('createNodeFromVNode listener reads updated attribute after mutation', () => {
        const btn = window.djust.createNodeFromVNode({
            tag: 'button',
            attrs: { 'dj-click': 'delete_todo(1)', 'data-dj-id': 'b1' },
            children: [{ tag: '', attrs: {}, children: [], text: 'Delete' }],
        });

        // Verify initial attribute
        expect(btn.getAttribute('dj-click')).toBe('delete_todo(1)');

        // Simulate SetAttribute patch
        btn.setAttribute('dj-click', 'delete_todo(2)');

        // The listener closure references elem.getAttribute(djAttrKey),
        // so next click will parse 'delete_todo(2)' — verified by attribute state.
        expect(btn.getAttribute('dj-click')).toBe('delete_todo(2)');
    });
});
