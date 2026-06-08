/**
 * #1756 — dj-navigate auto-manages aria-current="page" on [dj-navigate] links
 * so a persistent nav (which lives OUTSIDE [dj-root]) tracks the current page
 * across SPA navigation, instead of keeping the server-rendered highlight on
 * the page you navigated from.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

function createDom(url = 'http://localhost/') {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
            '<nav>' +
            '  <a id="home" dj-navigate="/">Home</a>' +
            '  <a id="examples" dj-navigate="/examples/">Examples</a>' +
            '  <a id="docs" dj-navigate="/docs/">Docs</a>' +
            '  <a id="hosting" dj-navigate="https://djustlive.com">Hosting</a>' +
            '</nav>' +
            '<div dj-root dj-view="test.View"></div>' +
            '</body></html>',
        { url, runScripts: 'dangerously' }
    );
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }
    dom.window.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
    dom.window.WebSocket.OPEN = 1;
    const oc = dom.window.console;
    dom.window.console = { log: () => {}, error: oc.error.bind(oc), warn: () => {}, debug: () => {}, info: () => {} };
    dom.window.eval(clientCode);
    return dom;
}

function activeIds(win) {
    return [...win.document.querySelectorAll('[dj-navigate][aria-current="page"]')].map((a) => a.id);
}

describe('#1756 dj-navigate aria-current sync', () => {
    let dom, win, nav;
    beforeEach(async () => {
        dom = createDom('http://localhost/');
        await new Promise((r) => setTimeout(r, 10));
        win = dom.window;
        nav = win.djust && win.djust.navigation;
    });

    it('exposes navigation.updateAriaCurrent', () => {
        expect(nav).toBeDefined();
        expect(typeof nav.updateAriaCurrent).toBe('function');
    });

    it('marks the link matching the current path as aria-current="page"', () => {
        nav.updateAriaCurrent();
        expect(activeIds(win)).toEqual(['home']);
    });

    it('moves the highlight when the URL changes and never marks cross-origin links', () => {
        nav.updateAriaCurrent();
        expect(activeIds(win)).toEqual(['home']);

        win.history.pushState({}, '', '/examples/');
        nav.updateAriaCurrent();
        expect(activeIds(win)).toEqual(['examples']); // home dropped, examples set

        win.history.pushState({}, '', '/docs/');
        nav.updateAriaCurrent();
        expect(activeIds(win)).toEqual(['docs']);

        // The external "Hosting" link (https://djustlive.com, pathname "/") must
        // never be marked current, even when on the home page.
        win.history.pushState({}, '', '/');
        nav.updateAriaCurrent();
        expect(activeIds(win)).toEqual(['home']);
        expect(win.document.getElementById('hosting').hasAttribute('aria-current')).toBe(false);
    });

    it('does not clobber an app-authored aria-current of a different value', () => {
        const docs = win.document.getElementById('docs');
        docs.setAttribute('aria-current', 'step'); // app-managed, not "page"
        win.history.pushState({}, '', '/');
        nav.updateAriaCurrent();
        // home is "page"; docs keeps its app value (we only manage "page")
        expect(win.document.getElementById('home').getAttribute('aria-current')).toBe('page');
        expect(docs.getAttribute('aria-current')).toBe('step');
    });
});
