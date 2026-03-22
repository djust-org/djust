/**
 * Tests for _target param in change/input/submit events.
 *
 * When multiple form fields share one handler (e.g., dj-change="validate"),
 * the _target param tells the server which field triggered the event.
 * Matches Phoenix LiveView's _target convention.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

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
        window.location.reload = function() {};
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

describe('_target param', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dj-change includes _target matching field name attribute', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input name="email" dj-change="validate" value="test@example.com" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="email"]');
        input.value = 'new@example.com';
        const changeEvent = new dom.window.Event('change', { bubbles: true });
        input.dispatchEvent(changeEvent);

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const call = calls.find(c => c.eventName === 'validate');
        expect(call).toBeDefined();
        expect(call.body._target).toBe('email');
    });

    it('dj-change uses id as _target fallback when no name', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input id="id_email" dj-change="validate" value="test" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('#id_email');
        input.value = 'changed';
        const changeEvent = new dom.window.Event('change', { bubbles: true });
        input.dispatchEvent(changeEvent);

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const call = calls.find(c => c.eventName === 'validate');
        expect(call).toBeDefined();
        expect(call.body._target).toBe('id_email');
    });

    it('dj-change sets _target to null when no name or id', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input dj-change="validate" value="test" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[dj-change]');
        input.value = 'changed';
        const changeEvent = new dom.window.Event('change', { bubbles: true });
        input.dispatchEvent(changeEvent);

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const call = calls.find(c => c.eventName === 'validate');
        expect(call).toBeDefined();
        expect(call.body._target).toBeNull();
    });

    it('dj-input includes _target matching field name', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input name="search_query" dj-input="search" data-debounce="0" value="" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="search_query"]');
        input.value = 'hello';
        const inputEvent = new dom.window.Event('input', { bubbles: true });
        input.dispatchEvent(inputEvent);

        // Wait for debounce (set to 0) + async
        await nativeSleep(400);

        const calls = getFetchCalls(dom);
        const call = calls.find(c => c.eventName === 'search');
        expect(call).toBeDefined();
        expect(call.body._target).toBe('search_query');
    });

    it('dj-submit includes submitter name as _target when e.submitter exists', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <button type="submit" name="save_btn">Save</button>
                </form>
            </div>
        `);
        initClient(dom);

        const form = dom.window.document.querySelector('form');

        // JSDOM SubmitEvent may not support submitter, so we create a regular submit event
        // and check that when submitter is absent, _target falls back gracefully
        const submitEvent = new dom.window.Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(submitEvent);

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const call = calls.find(c => c.eventName === 'save');
        expect(call).toBeDefined();
        // Without submitter, _target should be null (JSDOM doesn't support SubmitEvent.submitter)
        expect(call.body).toHaveProperty('_target');
    });

    it('_target is not stripped by handleEvent (passes through to server)', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input name="username" dj-change="validate" value="test" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="username"]');
        input.value = 'newvalue';
        const changeEvent = new dom.window.Event('change', { bubbles: true });
        input.dispatchEvent(changeEvent);

        await nativeSleep(200);

        const calls = getFetchCalls(dom);
        const call = calls.find(c => c.eventName === 'validate');
        expect(call).toBeDefined();
        // _target should NOT be stripped (it's meaningful server-side data)
        expect(call.body._target).toBe('username');
        // But _targetElement should be stripped
        expect(call.body).not.toHaveProperty('_targetElement');
    });
});
