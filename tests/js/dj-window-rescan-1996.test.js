/**
 * Regression tests for #1996:
 *   dj-window-* / dj-document-* never bind on content that appears via a later patch.
 *
 * Root cause: _scanScopedElements() (the only code that populates the scoped
 * registry) was called exclusively from _installScopedDelegation(), which is
 * one-shot (guarded by _scopedDelegationInstalled). bindLiveViewEvents() — run
 * after every WebSocket patch — re-invoked _installScopedDelegation() but it was
 * a no-op after the first mount, so an element that ENTERED the DOM via a later
 * patch (e.g. inside a {% if %} that became true) never registered and its
 * dj-window-keydown.escape handler was silently dead.
 *
 * Fix: bindLiveViewEvents() calls _scanScopedElements() on EVERY invocation while
 * keeping the window/document addEventListener install one-shot. The scan is
 * idempotent per-element, so it must not double-register (no double-fire) nor
 * install duplicate window listeners.
 *
 * These tests faithfully reproduce the real path: mount with the scoped element
 * ABSENT, simulate a patch by inserting the element, call the post-patch bind
 * (window.djust.bindLiveViewEvents — the exact function the client calls after a
 * patch applies), then dispatch a real Escape keydown on window.
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

/**
 * Simulate a server-driven patch that inserts `html` into the LiveView root,
 * then run the post-patch bind exactly as the client does after applying a
 * patch (window.djust.bindLiveViewEvents).
 */
function patchInsert(dom, html) {
    const root = dom.window.document.querySelector('[dj-view]');
    const tmp = dom.window.document.createElement('div');
    tmp.innerHTML = html;
    while (tmp.firstChild) {
        root.appendChild(tmp.firstChild);
    }
    dom.window.djust.bindLiveViewEvents();
}

describe('#1996: dj-window-*/dj-document-* re-scan after patch', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('binds a dj-window-keydown.escape that appears via a later patch (THE BUG)', async () => {
        // Mount with the scoped element ABSENT.
        const dom = createTestEnv('<div dj-view="app.TestView"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Sanity: nothing bound yet — Escape does nothing pre-patch.
        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 30));
        expect(getFetchCalls(dom).length).toBe(0);

        // Server patch flips a {% if %} true → element enters the DOM.
        patchInsert(dom, '<div id="palette" dj-window-keydown.escape="close"></div>');

        // Real Escape keydown on window must now reach the handler.
        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('close');
    });

    it('binds a dj-document-keydown that appears via a later patch', async () => {
        const dom = createTestEnv('<div dj-view="app.TestView"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        patchInsert(dom, '<div dj-document-keydown="doc_key"></div>');

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Tab', code: 'Tab', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('doc_key');
        expect(calls[0].body.key).toBe('Tab');
    });

    it('does NOT double-register when a patch re-scans an already-registered element', async () => {
        // Element present from mount; multiple binds (as would happen across
        // several patches) must not add duplicate registry entries.
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-window-keydown.escape="close"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents(); // patch 1
        dom.window.djust.bindLiveViewEvents(); // patch 2

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        // Exactly one fire — no duplicate registry entries, no duplicate window
        // listeners.
        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('a patch-inserted element fires exactly once (no duplicate window listeners)', async () => {
        const dom = createTestEnv('<div dj-view="app.TestView"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Two subsequent patches (second inserts the scoped element).
        dom.window.djust.bindLiveViewEvents();
        patchInsert(dom, '<div dj-window-keydown.escape="close"></div>');
        // A further no-op patch bind must not multiply the dispatch.
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('still cleans up a scoped element removed by a later patch', async () => {
        // The re-scan must not resurrect entries for elements that left the DOM.
        const dom = createTestEnv('<div dj-view="app.TestView"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        patchInsert(dom, '<div id="palette" dj-window-keydown.escape="close"></div>');

        // Patch removes the element again.
        const el = dom.window.document.getElementById('palette');
        el.parentNode.removeChild(el);
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(0);
    });
});
