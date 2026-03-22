/**
 * Tests for dj-shortcut — declarative keyboard shortcut binding
 *
 * <div dj-shortcut="ctrl+k:open_search, escape:close_modal">
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

describe('dj-shortcut', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('simple key shortcut fires (escape:close_modal)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="escape:close_modal"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('close_modal');
        expect(calls[0].body.key).toBe('Escape');
        expect(calls[0].body.shortcut).toBe('escape');
    });

    it('modifier key shortcut fires (ctrl+k:open_search)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="ctrl+k:open_search"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('open_search');
        expect(calls[0].body.shortcut).toBe('ctrl+k');
    });

    it('does not fire when modifiers do not match', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="ctrl+k:open_search"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Press 'k' without ctrl
        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', ctrlKey: false, bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('multiple shortcuts on one element (comma-separated)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="escape:close_modal, ctrl+k:open_search"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // First shortcut
        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(1);
        expect(getFetchCalls(dom)[0].eventName).toBe('close_modal');

        // Second shortcut
        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true
        }));
        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(2);
        expect(getFetchCalls(dom)[1].eventName).toBe('open_search');
    });

    it('prevent modifier calls preventDefault', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="ctrl+k:open_search:prevent"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const event = new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true
        });
        const preventSpy = vi.spyOn(event, 'preventDefault');

        dom.window.document.dispatchEvent(event);
        await new Promise(r => setTimeout(r, 50));

        expect(preventSpy).toHaveBeenCalled();
    });

    it('without prevent modifier, preventDefault is NOT called', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="ctrl+k:open_search"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const event = new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true
        });
        const preventSpy = vi.spyOn(event, 'preventDefault');

        dom.window.document.dispatchEvent(event);
        await new Promise(r => setTimeout(r, 50));

        expect(preventSpy).not.toHaveBeenCalled();
    });

    it('params include key, code, and shortcut', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="ctrl+shift+s:save_doc"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 's', code: 'KeyS', ctrlKey: true, shiftKey: true, bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].body.key).toBe('s');
        expect(calls[0].body.code).toBe('KeyS');
        expect(calls[0].body.shortcut).toBe('ctrl+shift+s');
    });

    it('dj-value-* params from declaring element are included', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="escape:close" dj-value-panel="settings"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].body.panel).toBe('settings');
    });

    it('does not double-bind on re-call of bindLiveViewEvents', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="escape:close"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('cleanup removes listener when element is removed from DOM', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div id="sc" dj-shortcut="escape:close"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const el = dom.window.document.getElementById('sc');
        el.parentNode.removeChild(el);

        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('skips shortcuts when active element is a form input', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="escape:close"></div><input id="myinput" type="text" /></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Focus the input
        const input = dom.window.document.getElementById('myinput');
        input.focus();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        // Should NOT fire when typing in an input
        expect(getFetchCalls(dom).length).toBe(0);
    });

    it('dj-shortcut-in-input overrides form input skip', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="escape:close" dj-shortcut-in-input></div><input id="myinput" type="text" /></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        // Focus the input
        const input = dom.window.document.getElementById('myinput');
        input.focus();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'Escape', code: 'Escape', bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        // SHOULD fire because of dj-shortcut-in-input
        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('meta modifier key works (cmd on Mac)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><div dj-shortcut="meta+k:spotlight"></div></div>'
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {
            key: 'k', code: 'KeyK', metaKey: true, bubbles: true
        }));

        await new Promise(r => setTimeout(r, 50));

        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('spotlight');
    });
});
