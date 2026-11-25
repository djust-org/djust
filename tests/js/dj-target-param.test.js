/**
 * The 1.x `_target` key is gone (#4205): a strict binding never generates it,
 * whatever the handler declares. A handler that needs the field name takes
 * `field` (dj-change / dj-input generate it) or a dj-value-* argument.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { withContracts } from './_open_contracts.js';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${withContracts(bodyHtml)}</body></html>`,
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
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion, ...(() => { const s = document.querySelector('script[data-djust-parameter-contracts]'); if (!s) return {}; const m = JSON.parse(s.textContent); return {parameter_contracts: m.contracts, parameter_contract_view: m.view}; })() }; } };
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

describe('_target is never sent (#4205)', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dj-change does not send _target', async () => {
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
        expect(call.body).not.toHaveProperty('_target');
    });

    it('dj-change with only an id does not send _target', async () => {
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
        expect(call.body).not.toHaveProperty('_target');
    });

    it('dj-change with no name or id does not send _target', async () => {
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
        expect(call.body).not.toHaveProperty('_target');
    });

    it('dj-input does not send _target', async () => {
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
        expect(call.body).not.toHaveProperty('_target');
    });

    it('dj-submit does not send _target', async () => {
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
        expect(call.body).not.toHaveProperty('_target');
    });

    it('the declared field generated value is what identifies the field', async () => {
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
        expect(call.body).not.toHaveProperty('_target');
        expect(call.body).not.toHaveProperty('_targetElement');
        expect(call.body.field).toBe('username');
    });
});
