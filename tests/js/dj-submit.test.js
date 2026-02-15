/**
 * Tests for dj-submit form data collection
 *
 * Verifies that dj-submit forms correctly collect FormData from named inputs
 * and send the field values as event params to the server.
 *
 * Bug: dj-submit handler calls handleEvent with params containing only
 * _targetElement (the form element), but no form field data. The FormData
 * collection step (Object.fromEntries(new FormData(e.target).entries()))
 * works correctly, but the params arrive at handleEvent empty.
 *
 * Issue: https://github.com/djust-org/djust/issues/308
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * Create a JSDOM instance with form elements and fetch spy.
 * WebSocket is disabled to force HTTP fallback so we can inspect
 * the params sent in the fetch body.
 */
function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;

        // Stub location.reload to prevent JSDOM "Not implemented" errors
        window.location.reload = function() {};

        // JSDOM defaults to hidden=true
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

function getFetchCalls(dom) {
    return dom.window._testFetchCalls;
}

describe('dj-submit', () => {
    it('should send form field values as event params', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="create_item">
                    <input type="text" name="title" value="Test Title" />
                    <textarea name="description">Test description</textarea>
                    <select name="priority">
                        <option value="P1">P1</option>
                        <option value="P2" selected>P2</option>
                    </select>
                    <button type="submit">Submit</button>
                </form>
            </div>
        `);
        initClient(dom);

        // Verify form exists
        const form = dom.window.document.querySelector('form[dj-submit]');
        expect(form).toBeTruthy();

        // Submit the form
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Wait for async handleEvent to complete and fall back to HTTP
        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);

        const submitCall = calls.find(c => c.eventName === 'create_item');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.title).toBe('Test Title');
        expect(submitCall.body.description).toBe('Test description');
        expect(submitCall.body.priority).toBe('P2');
    });

    it('should send user-entered values (not just initial values)', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="create_item">
                    <input type="text" name="title" value="" />
                    <button type="submit">Submit</button>
                </form>
            </div>
        `);
        initClient(dom);

        // Simulate user typing into the input
        const input = dom.window.document.querySelector('input[name="title"]');
        input.value = 'User typed value';

        // Submit the form
        const form = dom.window.document.querySelector('form');
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const submitCall = calls.find(c => c.eventName === 'create_item');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.title).toBe('User typed value');
    });

    it('should not include _targetElement in server params', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="save_data">
                    <input type="text" name="name" value="Alice" />
                    <button type="submit">Save</button>
                </form>
            </div>
        `);
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const submitCall = calls.find(c => c.eventName === 'save_data');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.name).toBe('Alice');
        expect(submitCall.body).not.toHaveProperty('_targetElement');
    });

    it('should collect all named fields from nested form structure', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="create_idea">
                    <div class="grid">
                        <div class="col-span-2">
                            <input type="text" name="title" value="My Idea" />
                        </div>
                        <div class="col-span-2">
                            <textarea name="description">A great idea</textarea>
                        </div>
                        <div>
                            <select name="category">
                                <option value="idea" selected>Idea</option>
                                <option value="tech_debt">Tech Debt</option>
                            </select>
                        </div>
                        <div>
                            <select name="priority">
                                <option value="P0">P0</option>
                                <option value="P2" selected>P2</option>
                            </select>
                        </div>
                    </div>
                    <button type="submit">Add Idea</button>
                </form>
            </div>
        `);
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const submitCall = calls.find(c => c.eventName === 'create_idea');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.title).toBe('My Idea');
        expect(submitCall.body.description).toBe('A great idea');
        expect(submitCall.body.category).toBe('idea');
        expect(submitCall.body.priority).toBe('P2');
        expect(submitCall.body).not.toHaveProperty('_targetElement');
        // No numeric keys from HTMLFormElement serialization
        expect(submitCall.body).not.toHaveProperty('0');
        expect(submitCall.body).not.toHaveProperty('1');
    });

    it('should re-bind form after VDOM replaces DOM node (stale bound flag)', async () => {
        // This test reproduces the VDOM morphing bug:
        // 1. Form is initially bound by bindLiveViewEvents() → data-liveview-submit-bound="true"
        // 2. VDOM patch replaces the form's parent, inserting a new form element
        // 3. The new form has data-liveview-submit-bound="true" (from morphElement preserving
        //    data-liveview-* attributes) but NO event listener (listeners don't survive cloning)
        // 4. bindLiveViewEvents() sees the flag and SKIPS binding → form submit does nothing
        //
        // Fix: Use a WeakSet to track bound elements instead of data attributes
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <div id="ideas-container">
                    <form dj-submit="create_idea">
                        <input type="text" name="title" value="Original" />
                        <button type="submit">Submit</button>
                    </form>
                </div>
            </div>
        `);
        initClient(dom);

        // Verify initial binding works
        const originalForm = dom.window.document.querySelector('form[dj-submit]');
        expect(originalForm).toBeTruthy();

        // Simulate VDOM Replace patch: replace the form with a new DOM node
        // The new node has the same dj-submit attribute but is a different
        // DOM object — so the WeakMap won't have it, and bindLiveViewEvents
        // should re-bind the submit handler.
        const container = dom.window.document.getElementById('ideas-container');
        const newFormHtml = `<form dj-submit="create_idea">
            <input type="text" name="title" value="After VDOM replace" />
            <button type="submit">Submit</button>
        </form>`;
        container.innerHTML = newFormHtml;

        // Re-run bindLiveViewEvents (this happens after every patch application)
        dom.window.djust.bindLiveViewEvents();

        // Submit the NEW form
        const newForm = dom.window.document.querySelector('form[dj-submit]');
        newForm.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const submitCall = calls.find(c => c.eventName === 'create_idea');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.title).toBe('After VDOM replace');
    });

    it('should re-bind after morphElement preserves flag on new element', async () => {
        // Simulates the morphChildren Strategy 3 (clone) path where morphElement
        // copies attributes including data-liveview-submit-bound from the desired
        // element onto a new element, but the event listener is not copied.
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="save_data">
                    <input type="text" name="name" value="Alice" />
                    <button type="submit">Save</button>
                </form>
            </div>
        `);
        initClient(dom);

        // Simulate: create a fresh form element (as if cloned by VDOM)
        // This is a different DOM node, so the WeakMap won't track it
        const djView = dom.window.document.querySelector('[dj-view]');
        const freshForm = dom.window.document.createElement('form');
        freshForm.setAttribute('dj-submit', 'save_data');
        freshForm.innerHTML = '<input type="text" name="name" value="Bob" /><button type="submit">Save</button>';

        // Replace old form with new one
        const oldForm = dom.window.document.querySelector('form');
        djView.replaceChild(freshForm, oldForm);

        // bindLiveViewEvents should detect the stale flag and re-bind
        dom.window.djust.bindLiveViewEvents();

        freshForm.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const submitCall = calls.find(c => c.eventName === 'save_data');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.name).toBe('Bob');
    });

    it('should send form data when client-dev.js handleEvent wrapper is active', async () => {
        // This test reproduces the exact production bug: client-dev.js wraps
        // handleEvent with a logging wrapper. The dj-submit handler's closure
        // then calls the wrapper instead of the original. This test verifies
        // form data still reaches the server through the wrapper chain.
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="create_idea">
                    <div class="grid">
                        <input type="text" name="title" value="Dev Tools Test" />
                        <textarea name="description">Test with dev wrapper</textarea>
                        <select name="category">
                            <option value="idea" selected>Idea</option>
                        </select>
                    </div>
                    <button type="submit">Submit</button>
                </form>
            </div>
        `);
        initClient(dom);

        // Simulate what client-dev.js does: wrap handleEvent
        // client-dev.js runs in a strict-mode IIFE and reassigns the global handleEvent
        dom.window.eval(`
            (function() {
                'use strict';
                if (typeof handleEvent !== 'undefined') {
                    const originalHandleEvent = handleEvent;

                    // This is the exact pattern from client-dev.js line 279
                    window.handleEvent = handleEvent = async function (eventName, params) {
                        const startTime = performance.now();
                        let result = null;
                        let error = null;

                        try {
                            result = await originalHandleEvent(eventName, params);
                        } catch (e) {
                            error = e;
                            throw e;
                        } finally {
                            // Create clean params without internal fields
                            const cleanParams = { ...params };
                            delete cleanParams._targetElement;
                            delete cleanParams._skipDecorators;
                        }

                        return result;
                    };
                }
            })();
        `);

        const form = dom.window.document.querySelector('form');
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const submitCall = calls.find(c => c.eventName === 'create_idea');
        expect(submitCall).toBeDefined();
        expect(submitCall.body.title).toBe('Dev Tools Test');
        expect(submitCall.body.description).toBe('Test with dev wrapper');
        expect(submitCall.body.category).toBe('idea');
        expect(submitCall.body).not.toHaveProperty('_targetElement');
    });
});
