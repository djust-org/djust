/**
 * Tests for dj-mutation — declarative MutationObserver attribute (v0.6.0).
 *
 * Uses jsdom for DOM + MutationObserver + CustomEvent. The dj-mutation
 * module dispatches a dj-mutation-fire CustomEvent on the marked
 * element rather than directly invoking the djust event pipeline, so
 * tests assert on the CustomEvent rather than on WS sends.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { createDom as sharedCreateDom, nextFrame } from './_helpers.js';

function createDom(bodyHtml = '') {
    // Match this file's historical `dj-view` host ("test.views.TestView")
    // so tests that assume that exact string keep passing.
    return sharedCreateDom(bodyHtml, { view: 'test.views.TestView' });
}

function waitForMutation(dom, ms = 0) {
    return nextFrame(dom, ms);
}

describe('dj-mutation', () => {
    let dom;

    it('fires dj-mutation-fire on attribute change', async () => {
        dom = createDom('<div id="w" dj-mutation="on_change" dj-mutation-attr="class" dj-mutation-debounce="0"></div>');
        const el = dom.window.document.getElementById('w');
        const events = [];
        el.addEventListener('dj-mutation-fire', (e) => events.push(e.detail));

        el.setAttribute('class', 'updated');
        await waitForMutation(dom, 30);

        expect(events.length).toBe(1);
        expect(events[0].handler).toBe('on_change');
        expect(events[0].payload.mutation).toBe('attributes');
        expect(events[0].payload.attrs).toContain('class');
    });

    it('fires dj-mutation-fire on childList change when no attr filter set', async () => {
        dom = createDom('<div id="w" dj-mutation="on_kids" dj-mutation-debounce="0"></div>');
        const el = dom.window.document.getElementById('w');
        const events = [];
        el.addEventListener('dj-mutation-fire', (e) => events.push(e.detail));

        const child = dom.window.document.createElement('span');
        el.appendChild(child);
        await waitForMutation(dom, 30);

        expect(events.length).toBe(1);
        expect(events[0].handler).toBe('on_kids');
        expect(events[0].payload.mutation).toBe('childList');
        expect(events[0].payload.added).toBe(1);
    });

    it('debounces rapid bursts into a single event', async () => {
        dom = createDom('<div id="w" dj-mutation="on_change" dj-mutation-attr="class" dj-mutation-debounce="50"></div>');
        const el = dom.window.document.getElementById('w');
        const events = [];
        el.addEventListener('dj-mutation-fire', (e) => events.push(e.detail));

        // Three rapid-fire mutations within the debounce window.
        el.setAttribute('class', 'a');
        el.setAttribute('class', 'b');
        el.setAttribute('class', 'c');
        await waitForMutation(dom, 70);

        expect(events.length).toBe(1);
    });

    it('installs an observer on an element inserted later (VDOM morph)', async () => {
        dom = createDom('<div id="host"></div>');
        const host = dom.window.document.getElementById('host');
        host.innerHTML = '<div id="late" dj-mutation="on_change" dj-mutation-attr="data-v" dj-mutation-debounce="0"></div>';
        await waitForMutation(dom, 30);

        const el = dom.window.document.getElementById('late');
        const events = [];
        el.addEventListener('dj-mutation-fire', (e) => events.push(e.detail));
        el.setAttribute('data-v', '1');
        await waitForMutation(dom, 30);

        expect(events.length).toBe(1);
        expect(events[0].payload.attrs).toContain('data-v');
    });

    it('exposes _installDjMutationFor on window.djust.djMutation', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.djMutation._installDjMutationFor).toBe('function');
    });

    it('forwards to window.djust.handleEvent so server @event_handler runs', async () => {
        dom = createDom('<div id="w" dj-mutation="server_handler" dj-mutation-attr="class" dj-mutation-debounce="0"></div>');
        const el = dom.window.document.getElementById('w');
        const calls = [];
        dom.window.djust.handleEvent = function (name, params) {
            calls.push({ name: name, params: params });
            return Promise.resolve();
        };

        el.setAttribute('class', 'x');
        await waitForMutation(dom, 30);

        expect(calls.length).toBe(1);
        expect(calls[0].name).toBe('server_handler');
        expect(calls[0].params.mutation).toBe('attributes');
        expect(calls[0].params.attrs).toContain('class');
    });

    it('does not fire the CustomEvent if the element is removed before the debounce timer expires', async () => {
        dom = createDom(`<div id="el" dj-mutation="handle_change" dj-mutation-debounce="100"></div>`);
        await new Promise((r) => setTimeout(r, 20));
        const el = dom.window.document.getElementById('el');
        let fired = false;
        el.addEventListener('dj-mutation-fire', () => { fired = true; });

        // Trigger a mutation (which arms the debounce timer)
        el.setAttribute('data-trigger', 'yes');
        // Remove the element BEFORE the debounce timer (100ms) fires
        await new Promise((r) => setTimeout(r, 20));
        el.remove();
        // Wait past the debounce window
        await new Promise((r) => setTimeout(r, 120));

        expect(fired).toBe(false);
    });

    it('preventDefault on dj-mutation-fire cancels the server call', async () => {
        dom = createDom('<div id="w" dj-mutation="server_handler" dj-mutation-attr="class" dj-mutation-debounce="0"></div>');
        const el = dom.window.document.getElementById('w');
        const calls = [];
        dom.window.djust.handleEvent = function (name, params) {
            calls.push({ name: name, params: params });
        };
        el.addEventListener('dj-mutation-fire', (e) => e.preventDefault());

        el.setAttribute('class', 'x');
        await waitForMutation(dom, 30);

        expect(calls.length).toBe(0);
    });
});
