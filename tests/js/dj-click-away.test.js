/**
 * Tests for dj-click-away — fire event when user clicks outside an element
 *
 * <div dj-click-away="close_dropdown">...</div>
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

describe('dj-click-away', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('fires handler when clicking outside the element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="dropdown" dj-click-away="close_dropdown"><span>Menu</span></div><div id="outside">Other content</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const outside = dom.window.document.getElementById('outside');
        outside.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].eventName).toBe('close_dropdown');
    });

    it('does NOT fire when clicking inside the element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="dropdown" dj-click-away="close_dropdown"><span id="inner">Menu</span></div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const inner = dom.window.document.getElementById('inner');
        inner.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('does NOT fire when clicking on the element itself', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="dropdown" dj-click-away="close_dropdown">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const dropdown = dom.window.document.getElementById('dropdown');
        dropdown.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('includes dj-value-* params from the element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-click-away="close" dj-value-panel-id:int="42">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const outside = dom.window.document.getElementById('outside');
        outside.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBeGreaterThanOrEqual(1);
        expect(calls[0].body.panel_id).toBe(42);
    });

    it('supports dj-confirm before firing', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-click-away="close" dj-confirm="Close panel?">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);

        // User cancels confirmation
        dom.window.confirm = vi.fn().mockReturnValue(false);
        dom.window.djust.bindLiveViewEvents();

        const outside = dom.window.document.getElementById('outside');
        outside.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        expect(dom.window.confirm).toHaveBeenCalledWith('Close panel?');
        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('does not double-bind on re-call of bindLiveViewEvents', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-click-away="close">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        const outside = dom.window.document.getElementById('outside');
        outside.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('multiple click-away elements on same page', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="d1" dj-click-away="close_1">Menu 1</div><div id="d2" dj-click-away="close_2">Menu 2</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const outside = dom.window.document.getElementById('outside');
        outside.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        // Both should fire since click is outside both elements
        expect(calls.length).toBe(2);
        const eventNames = calls.map(c => c.eventName);
        expect(eventNames).toContain('close_1');
        expect(eventNames).toContain('close_2');
    });

    it('cleanup removes listener when element is removed from DOM', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="dropdown" dj-click-away="close">Menu</div><div id="outside">Other</div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Remove element
        const el = dom.window.document.getElementById('dropdown');
        el.parentNode.removeChild(el);

        // Re-bind triggers cleanup
        dom.window.djust.bindLiveViewEvents();

        const outside = dom.window.document.getElementById('outside');
        outside.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(0);
    });
});
