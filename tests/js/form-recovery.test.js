/**
 * Tests for automatic form recovery on WebSocket reconnect.
 *
 * After reconnect, form fields with dj-change (or dj-input) automatically
 * fire synthetic change events to restore server state when DOM values
 * differ from server-rendered defaults.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

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

describe('Form Recovery on Reconnect', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('text input with dj-change: fires change event with DOM value on reconnect', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="query" dj-change="search" value="default" />
            </div>
        `);
        initClient(dom);

        // Simulate user typing a different value
        const input = dom.window.document.querySelector('input[name="query"]');
        input.value = 'user typed';

        // Trigger form recovery
        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const searchCalls = calls.filter(c => c.eventName === 'search');
        expect(searchCalls.length).toBe(1);
        expect(searchCalls[0].body.value).toBe('user typed');
        expect(searchCalls[0].body.field).toBe('query');
    });

    it('checkbox with dj-change: toggled state recovered', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="checkbox" name="agree" dj-change="toggle_agree" />
            </div>
        `);
        initClient(dom);

        // Simulate user checking the checkbox
        const checkbox = dom.window.document.querySelector('input[name="agree"]');
        checkbox.checked = true;

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const toggleCalls = calls.filter(c => c.eventName === 'toggle_agree');
        expect(toggleCalls.length).toBe(1);
        expect(toggleCalls[0].body.value).toBe(true);
    });

    it('select with dj-change: selected option recovered', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <select name="status" dj-change="update_status">
                    <option value="active" selected>Active</option>
                    <option value="closed">Closed</option>
                </select>
            </div>
        `);
        initClient(dom);

        // Simulate user selecting a different option
        const select = dom.window.document.querySelector('select[name="status"]');
        select.value = 'closed';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const statusCalls = calls.filter(c => c.eventName === 'update_status');
        expect(statusCalls.length).toBe(1);
        expect(statusCalls[0].body.value).toBe('closed');
    });

    it('multiple fields: all recovered in sequence', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="first" dj-change="update_first" value="" />
                <input name="second" dj-change="update_second" value="" />
            </div>
        `);
        initClient(dom);

        const first = dom.window.document.querySelector('input[name="first"]');
        const second = dom.window.document.querySelector('input[name="second"]');
        first.value = 'hello';
        second.value = 'world';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 100));

        const calls = getFetchCalls(dom);
        const firstCalls = calls.filter(c => c.eventName === 'update_first');
        const secondCalls = calls.filter(c => c.eventName === 'update_second');
        expect(firstCalls.length).toBe(1);
        expect(secondCalls.length).toBe(1);
        expect(firstCalls[0].body.value).toBe('hello');
        expect(secondCalls[0].body.value).toBe('world');
    });

    it('dj-no-recover: field is skipped', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="query" dj-change="search" value="" dj-no-recover />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="query"]');
        input.value = 'should not fire';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const searchCalls = calls.filter(c => c.eventName === 'search');
        expect(searchCalls.length).toBe(0);
    });

    it('fields inside dj-auto-recover container: skipped', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <div dj-auto-recover="custom_restore">
                    <input name="query" dj-change="search" value="" />
                </div>
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="query"]');
        input.value = 'typed by user';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const searchCalls = calls.filter(c => c.eventName === 'search');
        expect(searchCalls.length).toBe(0);
    });

    it('field value matches server default: no event fired', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="query" dj-change="search" value="default" />
            </div>
        `);
        initClient(dom);

        // Value remains at default — no recovery needed
        const input = dom.window.document.querySelector('input[name="query"]');
        expect(input.value).toBe('default');

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const searchCalls = calls.filter(c => c.eventName === 'search');
        expect(searchCalls.length).toBe(0);
    });

    it('no dj-change on field: field is skipped', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="query" value="" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="query"]');
        input.value = 'typed by user';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(0);
    });

    it('textarea with dj-change: multiline content recovered', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <textarea name="notes" dj-change="update_notes"></textarea>
            </div>
        `);
        initClient(dom);

        const textarea = dom.window.document.querySelector('textarea[name="notes"]');
        textarea.value = 'line 1\nline 2\nline 3';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const notesCalls = calls.filter(c => c.eventName === 'update_notes');
        expect(notesCalls.length).toBe(1);
        expect(notesCalls[0].body.value).toBe('line 1\nline 2\nline 3');
    });

    it('fields with dj-input but no dj-change: recovered via dj-input handler', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="search" dj-input="live_search" value="" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="search"]');
        input.value = 'user query';

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const searchCalls = calls.filter(c => c.eventName === 'live_search');
        expect(searchCalls.length).toBe(1);
        expect(searchCalls[0].body.value).toBe('user query');
    });

    it('radio buttons: recovers the checked radio in a group', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="radio" name="color" value="red" dj-change="set_color" checked />
                <input type="radio" name="color" value="blue" dj-change="set_color" />
            </div>
        `);
        initClient(dom);

        // Simulate user selecting "blue"
        const red = dom.window.document.querySelector('input[value="red"]');
        const blue = dom.window.document.querySelector('input[value="blue"]');
        red.checked = false;
        blue.checked = true;

        dom.window.djust._isReconnect = true;
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const colorCalls = calls.filter(c => c.eventName === 'set_color');
        // Should fire for both radios whose state differs from default:
        // red was checked by default, now unchecked = differs
        // blue was unchecked by default, now checked = differs
        expect(colorCalls.length).toBe(2);
    });

    it('does NOT fire on initial page load (only on reconnect)', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input name="query" dj-change="search" value="" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('input[name="query"]');
        input.value = 'user input';

        // _isReconnect is false by default
        dom.window.djust._processFormRecovery();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(0);
    });
});
