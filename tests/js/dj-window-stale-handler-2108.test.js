/**
 * Regression tests for #2108:
 *   the scoped-attr registry keeps a STALE handler when the attribute VALUE
 *   changes on an element that survives the patch.
 *
 * Root cause: _scanScopedElements() dedupes registry entries on
 * `(element identity, attrName)` only — the attribute VALUE is never compared
 * and `entry.parsed` is never refreshed. So when the server re-renders and
 * changes the handler in place on a surviving element:
 *
 *     <div dj-window-keydown="old">   ->   <div dj-window-keydown="renamed">
 *
 * the element counts as "already registered", the new value is dropped, and the
 * OLD handler keeps firing forever.
 *
 * Why in-place mutation is the realistic shape: morphdom PATCHES a surviving
 * element's attributes rather than replacing the node. An element that IS
 * replaced dodges the bug entirely, because the `document.contains()` sweep at
 * the top of the scan evicts the dead entry. So this only bites the nodes that
 * survive — including the dj-root/dj-view element itself, which is the morph
 * anchor and never replaced (that is why #2107, which added the root to the
 * scan, made this path more reachable).
 *
 * Surfaced by the Stage 11 review of PR #2107; pre-existing on both the root
 * and descendant paths.
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

const press = (dom, key = 'k') =>
    dom.window.dispatchEvent(
        new dom.window.KeyboardEvent('keydown', { key, code: 'Key' + key.toUpperCase(), bubbles: true })
    );

describe('#2108: stale scoped handler after in-place value change', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('descendant: renaming the handler in place fires the NEW handler (THE BUG)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="d" dj-window-keydown="old"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Server re-render changes the handler on a SURVIVING element.
        dom.window.document.getElementById('d').setAttribute('dj-window-keydown', 'renamed');
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['renamed']);
    });

    it('root element: renaming the handler in place fires the NEW handler', async () => {
        // The dj-view root is the morph anchor — never replaced, always mutated
        // in place, so it is the most likely node to hit this.
        const dom = createTestEnv('<div dj-view="app.V" dj-window-keydown="old"></div>');
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.querySelector('[dj-view]').setAttribute('dj-window-keydown', 'renamed');
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['renamed']);
    });

    it('refreshes the key filter too, not just the handler name', async () => {
        // requiredKey is stored alongside parsed and is equally stale.
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="d" dj-window-keydown.escape="close"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const el = dom.window.document.getElementById('d');
        el.removeAttribute('dj-window-keydown.escape');
        el.setAttribute('dj-window-keydown.enter', 'submit');
        dom.window.djust.bindLiveViewEvents();

        // Escape must NO LONGER fire — the old filter is gone.
        press(dom, 'Escape');
        await new Promise((r) => setTimeout(r, 30));
        expect(getFetchCalls(dom).length).toBe(0);

        press(dom, 'Enter');
        await new Promise((r) => setTimeout(r, 50));
        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['submit']);
    });

    it('still does not double-register when the value is UNCHANGED', async () => {
        // The dedupe must keep working — refreshing must not turn into
        // "append a second entry every bind".
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="d" dj-window-keydown="same"></div></div>'
        );
        initClient(dom);
        for (let i = 0; i < 5; i++) {
            dom.window.djust.bindLiveViewEvents();
        }

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['same']);
    });

    it('inline handler args from the new value take effect, not the old ones', async () => {
        // parseEventHandler carries inline args through as `_args` (raw arg
        // strings, not parsed kwargs). A stale `parsed` would send the OLD args
        // even when the handler NAME is unchanged — the nastiest shape of this
        // bug, because the event still looks right on the wire.
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="d" dj-window-keydown="move(dir=\'up\')"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document
            .getElementById('d')
            .setAttribute('dj-window-keydown', "move(dir='down')");
        dom.window.djust.bindLiveViewEvents();

        press(dom);
        await new Promise((r) => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('move');
        expect(calls[0].body._args).toEqual(["dir='down'"]);
    });
});
