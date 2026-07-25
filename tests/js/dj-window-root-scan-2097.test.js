/**
 * Regression tests for #2097:
 *   dj-window-* / dj-document-* placed ON the dj-root / dj-view element itself
 *   is never registered, so the binding is silently dead.
 *
 * Root cause: _scanScopedElements() resolves
 *   root = document.querySelector('[dj-view]') || document.querySelector('[dj-root]') || document
 * and then scans `root.querySelectorAll('*')`. querySelectorAll matches
 * DESCENDANTS ONLY, so the root element's own attributes are never examined.
 * Placing the attribute one level deeper works, which is what makes this so
 * confusing in the wild — no error, no WS frame, just a dead directive.
 *
 * Note on scope: the issue ALSO reported a post-morph stale-registry cause.
 * That one was already fixed by #1996 (bindLiveViewEvents() re-runs
 * _scanScopedElements() on every invocation; only the window/document
 * addEventListener install stays one-shot). The last test in this file pins
 * that behavior so the two causes cannot be conflated again, and so a future
 * refactor that reverts to scan-once is caught here as well as in
 * dj-window-rescan-1996.test.js.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'dangerously',
        url: 'http://localhost/',
    });

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

const getFetchCalls = (dom) => dom.window._testFetchCalls;

describe('#2097: scoped attrs on the root element itself', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('binds dj-window-keydown placed ON the [dj-view] root (THE BUG)', async () => {
        // This is the snake-arena shape: the game board IS the dj-view root and
        // carries the keyboard binding.
        const dom = createTestEnv('<div dj-view="app.SnakeView" dj-window-keydown="key"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'ArrowUp', code: 'ArrowUp', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('key');
        expect(calls[0].body.key).toBe('ArrowUp');
    });

    it('binds dj-document-keydown placed ON the [dj-root] root', async () => {
        const dom = createTestEnv('<div dj-root dj-document-keydown="doc_key"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'Tab', code: 'Tab', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('doc_key');
    });

    it('honors a key filter on the root element (dj-window-keydown.escape)', async () => {
        const dom = createTestEnv('<div dj-view="app.TestView" dj-window-keydown.escape="close"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Non-matching key must NOT fire — proves the filter still applies at root.
        dom.window.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'a', code: 'KeyA', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 30));
        expect(getFetchCalls(dom).length).toBe(0);

        dom.window.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('close');
    });

    it('does not double-fire when root AND a descendant both carry scoped attrs', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView" dj-window-keydown="root_key">' +
                '<div dj-window-keydown="child_key"></div>' +
                '</div>'
        );
        initClient(dom);
        // Several binds, as would happen across multiple patches.
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'w', code: 'KeyW', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 50));

        const names = getFetchCalls(dom).map((c) => c.eventName).sort();
        expect(names).toEqual(['child_key', 'root_key']);
    });

    it('re-registers the root binding after the root element is REPLACED', async () => {
        // Defense in depth for the issue's cause 1: a patch that swaps the root
        // subtree must not leave a stale registry entry behind.
        const dom = createTestEnv('<div id="host"><div dj-view="app.TestView" dj-window-keydown="key"></div></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const host = dom.window.document.getElementById('host');
        host.innerHTML = '<div dj-view="app.TestView" dj-window-keydown="key2"></div>';
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'x', code: 'KeyX', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        // Only the live element's handler fires; the removed one is gone.
        expect(calls.map((c) => c.eventName)).toEqual(['key2']);
    });

    it('cause 1 is already fixed: the scan re-runs on every bind (#1996 pin)', async () => {
        // Descendant case — the #1996 fix. Pinned here so a future refactor
        // cannot revert to scan-once and blame #2097.
        const dom = createTestEnv('<div dj-view="app.TestView"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const root = dom.window.document.querySelector('[dj-view]');
        root.innerHTML = '<div dj-window-keydown="late"></div>';
        dom.window.djust.bindLiveViewEvents();

        dom.window.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'q', code: 'KeyQ', bubbles: true })
        );
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['late']);
    });
});
