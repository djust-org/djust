/**
 * Tests for dj-window-* and dj-document-* event scoping
 *
 * Bind event listeners on window/document while using the declaring element
 * for context extraction (component_id, dj-value-* params, etc.).
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

describe('dj-window-* / dj-document-* event scoping', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dj-window-keydown fires handler with key and code params', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-window-keydown="handle_key"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'a', code: 'KeyA', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].eventName).toBe('handle_key');
        expect(calls[0].body.key).toBe('a');
        expect(calls[0].body.code).toBe('KeyA');
    });

    it('dj-window-keydown with key modifier filters events', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-window-keydown.escape="close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Non-matching key should NOT fire
        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'a', code: 'KeyA', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));
        expect(getFetchCalls(dom).length).toBe(0);

        // Matching key should fire
        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('close_modal');
    });

    it('dj-document-click fires handler with clientX/clientY', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-document-click="handle_doc_click"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.MouseEvent('click', {
            clientX: 100, clientY: 200, bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].eventName).toBe('handle_doc_click');
        expect(calls[0].body.clientX).toBe(100);
        expect(calls[0].body.clientY).toBe(200);
    });

    it('dj-value-* params from declaring element are included', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-window-keydown="handle_key" dj-value-mode="search"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].body.mode).toBe('search');
    });

    it('component_id from declaring element ancestry is included', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div data-component-id="comp_123"><div dj-window-keydown="handle_key"></div></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Enter', code: 'Enter', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].body.component_id).toBe('comp_123');
    });

    it('does not double-bind on re-call of bindLiveViewEvents', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-window-keydown="handle_key"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents(); // second call

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'x', code: 'KeyX', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        // Should only fire once, not twice
        expect(calls.length).toBe(1);
    });

    it('cleanup removes window listeners when element is removed from DOM', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="scoped" dj-window-keydown="handle_key"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Remove the element from DOM
        const el = dom.window.document.getElementById('scoped');
        el.parentNode.removeChild(el);

        // Re-bind triggers cleanup sweep for elements no longer in DOM
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'a', code: 'KeyA', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        // Listener should have been cleaned up
        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('dj-window-keyup fires handler', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-window-keyup="on_keyup"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keyup', {
            key: 'Enter', code: 'Enter', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('on_keyup');
    });

    it('dj-document-keydown fires on document', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-document-keydown="doc_key"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Tab', code: 'Tab', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('doc_key');
        expect(calls[0].body.key).toBe('Tab');
    });
});
