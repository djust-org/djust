/**
 * Regression tests for #2832:
 *   dj-shortcut / dj-click-away keep firing after the template stops
 *   declaring the directive, when the element SURVIVES the VDOM patch.
 *
 * Root cause: the two scoped-listener paths carried different eviction
 * predicates. The _scopedRegistry path (dj-window-* / dj-document-*) evicts
 * on three conditions — element detached, attribute gone, element no longer
 * governed by any LiveView root (#2108, #2110). _sweepOrphanedScopedListeners
 * (dj-shortcut / dj-click-away) checked only the detached condition. Because
 * morphdom PATCHES a surviving element's attributes rather than replacing the
 * node, the element keeps both its listener AND its _isHandlerBound marker:
 * nothing rebinds, nothing evicts, and a removed dj-shortcut keeps
 * dispatching server events on every keypress.
 *
 * Harness note: the dj-shortcut listener is attached to `document`, so the
 * keydown must be dispatched on document.body (it bubbles UP to document).
 * A keydown dispatched straight on `window` never reaches the listener and
 * yields [] on BOTH sides of the removal — which looks like a passing test
 * while proving nothing.
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

const pressEscape = (dom) => {
    dom.window.document.body.dispatchEvent(
        new dom.window.KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true })
    );
};

const clickOutside = (dom) => {
    dom.window.document.getElementById('outside').dispatchEvent(
        new dom.window.MouseEvent('click', { bubbles: true })
    );
};

describe('#2832: scoped listeners must stop when the template stops declaring them', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dj-shortcut: stops firing after the attribute is removed from a surviving element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));

        // Stimulus check: the directive must actually be live before we
        // remove it — otherwise the second half proves nothing.
        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal']);

        // Server re-render drops the attribute; morphdom keeps the element.
        dom.window.document.getElementById('sc').removeAttribute('dj-shortcut');
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));

        // Unchanged — the stale listener must have been evicted.
        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal']);
    });

    it('dj-click-away: stops firing after the attribute is removed from a surviving element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="dd" dj-click-away="close_dropdown">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        clickOutside(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_dropdown']);

        dom.window.document.getElementById('dd').removeAttribute('dj-click-away');
        dom.window.djust.bindLiveViewEvents();

        clickOutside(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_dropdown']);
    });

    it('dj-shortcut: evicting un-marks the element so a re-declared attribute re-binds with the NEW handler', async () => {
        // Eviction must clear the _boundHandlers marker too — a surviving
        // element keeps its marker otherwise, so even after eviction the
        // re-scan would skip re-binding and the OLD closure would keep
        // serving a directive whose value changed.
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));
        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal']);

        // Attribute goes away; a bind observes the gone state and evicts.
        dom.window.document.getElementById('sc').removeAttribute('dj-shortcut');
        dom.window.djust.bindLiveViewEvents();

        // Attribute returns with a DIFFERENT handler value.
        dom.window.document.getElementById('sc').setAttribute('dj-shortcut', 'escape:renamed');
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal', 'renamed']);
    });

    it('removing only one of two scoped directives on an element evicts just that one', async () => {
        // Per-attribute eviction granularity: one element carrying BOTH
        // dj-shortcut and dj-click-away loses only the directive the template
        // actually dropped. (On the pre-fix code the shortcut keeps firing.)
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="both" dj-shortcut="escape:close_modal" dj-click-away="close_dropdown">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));
        clickOutside(dom);
        await new Promise((r) => setTimeout(r, 50));
        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal', 'close_dropdown']);

        // Only dj-shortcut is dropped from the template.
        dom.window.document.getElementById('both').removeAttribute('dj-shortcut');
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));
        clickOutside(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual([
            'close_modal', 'close_dropdown', 'close_dropdown',
        ]);
    });

    it('dj-shortcut: stops firing when the element is no longer governed by any LiveView root', async () => {
        // Same eviction predicate's third condition, on the sweep path: the
        // element stays in the document and keeps its attribute, but it has
        // been reparented outside every [dj-view]/[dj-root] — the registry
        // path evicts such entries (#2110); the sweep path must too.
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="sc" dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));
        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal']);

        // Reparent outside the root — still in the document, still declared.
        const el = dom.window.document.getElementById('sc');
        dom.window.document.body.appendChild(el);
        dom.window.djust.bindLiveViewEvents();

        pressEscape(dom);
        await new Promise((r) => setTimeout(r, 50));

        expect(getFetchCalls(dom).map((c) => c.eventName)).toEqual(['close_modal']);
    });
});
