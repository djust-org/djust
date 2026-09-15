/**
 * Regression tests for #2845:
 *   dj-shortcut / dj-click-away keep serving the OLD handler closure when the
 *   attribute VALUE changes on an element that SURVIVES the VDOM patch.
 *
 * Sibling of #2832 (which fixed the REMOVAL half on this path) and #2108
 * (which fixed the same VALUE-CHANGE half on the dj-window-* / dj-document-*
 * registry path by refreshing entry.parsed / entry.requiredKey in place).
 * The sweep path's eviction predicate judges attribute PRESENCE, and the bind
 * loops skip already-marked elements via _isHandlerBound — so an element whose
 * attribute value changed under a morph keeps dispatching the OLD closure.
 *
 * Harness: identical to stale-scoped-listeners-2832.test.js — the dj-shortcut
 * listener lives on `document`, so keydown is dispatched on document.body to
 * bubble up; the dj-click-away listener is capture-phase on `document`.
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

const getFetchCalls = (dom) => dom.window._testFetchCalls;
const eventNames = (dom) => getFetchCalls(dom).map((c) => c.eventName);
const flush = () => new Promise((r) => setTimeout(r, 50));

const pressKey = (dom, key) => {
    dom.window.document.body.dispatchEvent(
        new dom.window.KeyboardEvent('keydown', { key, code: key, bubbles: true })
    );
};

const clickOutside = (dom) => {
    dom.window.document.getElementById('outside').dispatchEvent(
        new dom.window.MouseEvent('click', { bubbles: true })
    );
};

describe('#2845: a VALUE change on a surviving element must rebuild the closure', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dj-shortcut: dispatches the NEW handler after the attribute value changes', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        pressKey(dom, 'Escape');
        await flush();

        // Stimulus check: directive live under the OLD value before we change it.
        expect(eventNames(dom)).toEqual(['close_modal']);

        // Server re-render changes the VALUE; morphdom keeps the element and
        // only mutates the attribute. A bind pass must pick the new value up.
        dom.window.document.getElementById('sc').setAttribute('dj-shortcut', 'escape:renamed');
        dom.window.djust.bindLiveViewEvents();

        pressKey(dom, 'Escape');
        await flush();

        // Pre-fix this dispatched close_modal a SECOND time (stale closure).
        expect(eventNames(dom)).toEqual(['close_modal', 'renamed']);
    });

    it('dj-shortcut: the OLD key binding stops firing after the value changes to a different key', async () => {
        // Rebuild semantics, not value-merge: the new parsed bindings fully
        // replace the old ones, so a key the template no longer binds is dead.
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        pressKey(dom, 'Escape');
        await flush();
        expect(eventNames(dom)).toEqual(['close_modal']);

        dom.window.document.getElementById('sc').setAttribute('dj-shortcut', 'enter:renamed');
        dom.window.djust.bindLiveViewEvents();

        pressKey(dom, 'Escape');
        await flush();
        // Escape is no longer bound — the old binding must not fire.
        expect(eventNames(dom)).toEqual(['close_modal']);

        pressKey(dom, 'Enter');
        await flush();
        expect(eventNames(dom)).toEqual(['close_modal', 'renamed']);
    });

    it('dj-click-away: dispatches the NEW handler after the attribute value changes', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="dd" dj-click-away="close_dropdown">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        clickOutside(dom);
        await flush();
        expect(eventNames(dom)).toEqual(['close_dropdown']);

        dom.window.document.getElementById('dd').setAttribute('dj-click-away', 'renamed');
        dom.window.djust.bindLiveViewEvents();

        clickOutside(dom);
        await flush();

        // Pre-fix this dispatched close_dropdown a SECOND time.
        expect(eventNames(dom)).toEqual(['close_dropdown', 'renamed']);
    });

    it('dj-shortcut: adding dj-shortcut-in-input on a surviving element lifts the input gate', async () => {
        // The shortcut closure also captures `dj-shortcut-in-input` PRESENCE at
        // bind time; that capture is part of the same stale-closure class, so
        // the rebuild key must cover it.
        const dom = createTestEnv(
            '<div dj-view="app.V"><input id="field" value=""><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Focus an input: the gate must swallow Escape while the flag is absent.
        dom.window.document.getElementById('field').focus();
        pressKey(dom, 'Escape');
        await flush();
        expect(eventNames(dom)).toEqual([]);

        // Template adds dj-shortcut-in-input; the element survives the morph.
        dom.window.document.getElementById('sc').setAttribute('dj-shortcut-in-input', '');
        dom.window.djust.bindLiveViewEvents();

        pressKey(dom, 'Escape');
        await flush();
        expect(eventNames(dom)).toEqual(['close_modal']);
    });

    it('dj-shortcut: an unchanged value on a surviving element does NOT rebind (no double dispatch)', async () => {
        // The skip fast-path must stay: an unchanged value on a re-bind must
        // not attach a second listener, or every patch double-dispatches.
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Several re-binds with the value unchanged (e.g. unrelated patches).
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        pressKey(dom, 'Escape');
        await flush();

        expect(eventNames(dom)).toEqual(['close_modal']);
    });
});
