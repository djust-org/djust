/**
 * Tests for dj-auto-recover — custom reconnection recovery attribute
 *
 * After WebSocket reconnects, elements with dj-auto-recover="handler_name"
 * fire a server event with serialized DOM state (form values, data-* attrs)
 * to let the server restore custom state.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { withContracts } from './_open_contracts.js';
import { readFileSync } from 'fs';

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

describe('dj-auto-recover', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('does NOT fire on initial page load', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <div dj-auto-recover="restore_state">
                    <input name="query" value="hello" />
                </div>
            </div>
        `);
        initClient(dom);

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const recoverCalls = calls.filter(c => c.eventName === 'restore_state');
        expect(recoverCalls.length).toBe(0);
    });

    it('fires after reconnect with only the declared contract: no serialized form values or data-* attributes (#4205)', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <div dj-auto-recover="restore_state" data-page="3" data-sort="name">
                    <input name="query" value="search term" />
                    <textarea name="notes">some notes</textarea>
                </div>
            </div>
        `);
        initClient(dom);

        dom.window.djust._isReconnect = true;
        dom.window.djust._processAutoRecover();

        await new Promise(r => setTimeout(r, 50));

        const recoverCalls = getFetchCalls(dom).filter(c => c.eventName === 'restore_state');
        expect(recoverCalls.length).toBe(1);
        const params = recoverCalls[0].body;
        expect(params._form_values).toBeUndefined();
        expect(params._data_attrs).toBeUndefined();
        expect(params.query).toBeUndefined();
    });

    it('supports multiple dj-auto-recover elements firing independently', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <div dj-auto-recover="restore_search">
                    <input name="q" value="hello" />
                </div>
                <div dj-auto-recover="restore_filters">
                    <select name="status"><option value="open" selected>Open</option></select>
                </div>
            </div>
        `);
        initClient(dom);

        dom.window.djust._isReconnect = true;
        dom.window.djust._processAutoRecover();

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        const searchCalls = calls.filter(c => c.eventName === 'restore_search');
        const filterCalls = calls.filter(c => c.eventName === 'restore_filters');
        expect(searchCalls.length).toBe(1);
        expect(filterCalls.length).toBe(1);
    });

    it('clears reconnect flag after processing', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <div dj-auto-recover="restore_state">
                    <input name="q" value="test" />
                </div>
            </div>
        `);
        initClient(dom);

        dom.window.djust._isReconnect = true;
        dom.window.djust._processAutoRecover();

        await new Promise(r => setTimeout(r, 50));

        expect(dom.window.djust._isReconnect).toBe(false);
    });
});
