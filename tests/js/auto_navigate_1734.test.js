/**
 * Tests for auto_navigate — opt-in Turbo-Drive-style link interception
 * (#1734, ADR-021 Stage 2) in src/18-navigation.js.
 *
 * The opt-out matrix is correctness-critical: a wrong skip either breaks
 * expected browser behavior (new-tab, downloads, external) or hijacks a link
 * that should reload. Each case is pinned here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const navSource = fs.readFileSync(
    './python/djust/static/djust/src/18-navigation.js',
    'utf-8'
);

function createEnv({ routeMap = {}, ws = null, autoNav = true } = {}) {
    const metaTag = autoNav ? '<meta name="djust-auto-navigate" content="1">' : '';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${metaTag}</head><body><div dj-root></div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    window.history.pushState = vi.fn();
    window.history.replaceState = vi.fn();

    // Globals the nav IIFE references as free variables.
    window.eval('window.djust = { _routeMap: ' + JSON.stringify(routeMap) + ' };');
    window.eval('function isWSConnected(){ return true; }');
    window.liveViewWS_mock = ws;
    window.eval('var liveViewWS = window.liveViewWS_mock;');

    try {
        window.eval(navSource);
    } catch (_e) {
        /* JSDOM API gaps */
    }
    // Idempotent — ensures the delegated listener is installed regardless of
    // readyState timing in the test realm.
    window.djust.navigation.installAutoNavigate();
    return { window, document: window.document };
}

function makeWS() {
    return { ws: {}, viewMounted: true, sendMessage: vi.fn() };
}

// A mock click event whose target is a real <a> so `.closest` works.
function clickEvent(link, overrides = {}) {
    return {
        button: 0,
        defaultPrevented: false,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
        target: link,
        preventDefault: vi.fn(function () {
            this.defaultPrevented = true;
        }),
        ...overrides,
    };
}

describe('auto_navigate opt-out matrix (_shouldSkipAutoNavigate)', () => {
    let window, document, skip;
    beforeEach(() => {
        ({ window, document } = createEnv({ routeMap: { '/dashboard/': 'app.Dash' } }));
        skip = window.djust.navigation._shouldSkipAutoNavigate;
    });

    function link(attrs = {}) {
        const a = document.createElement('a');
        a.setAttribute('href', attrs.href || '/dashboard/');
        for (const [k, v] of Object.entries(attrs)) {
            if (k !== 'href') a.setAttribute(k, v);
        }
        document.body.appendChild(a);
        return a;
    }

    it('does NOT skip a plain left-click on a plain link', () => {
        expect(skip(clickEvent(link()), link())).toBe(false);
    });

    it('skips when another handler already prevented default', () => {
        expect(skip(clickEvent(link(), { defaultPrevented: true }), link())).toBe(true);
    });

    it('skips modified clicks (meta/ctrl/shift/alt) and non-left button', () => {
        const a = link();
        expect(skip(clickEvent(a, { metaKey: true }), a)).toBe(true);
        expect(skip(clickEvent(a, { ctrlKey: true }), a)).toBe(true);
        expect(skip(clickEvent(a, { shiftKey: true }), a)).toBe(true);
        expect(skip(clickEvent(a, { altKey: true }), a)).toBe(true);
        expect(skip(clickEvent(a, { button: 1 }), a)).toBe(true);
    });

    it('skips when there is no anchor', () => {
        expect(skip(clickEvent(null), null)).toBe(true);
    });

    it('skips download links, new-context targets, and rel=external', () => {
        expect(skip(clickEvent(link({ download: '' })), link({ download: '' }))).toBe(true);
        const t = link({ target: '_blank' });
        expect(skip(clickEvent(t), t)).toBe(true);
        const ext = link({ rel: 'noopener external' });
        expect(skip(clickEvent(ext), ext)).toBe(true);
    });

    it('skips links inside a [data-no-navigate] subtree', () => {
        const wrap = document.createElement('div');
        wrap.setAttribute('data-no-navigate', '');
        const a = document.createElement('a');
        a.setAttribute('href', '/dashboard/');
        wrap.appendChild(a);
        document.body.appendChild(wrap);
        expect(skip(clickEvent(a), a)).toBe(true);
    });

    it('does NOT skip _self target', () => {
        const a = link({ target: '_self' });
        expect(skip(clickEvent(a), a)).toBe(false);
    });
});

describe('auto_navigate interception (_handleAutoNavigateClick)', () => {
    function setup(routeMap) {
        const ws = makeWS();
        const { window, document } = createEnv({ routeMap, ws });
        return { window, document, ws, handle: window.djust.navigation._handleAutoNavigateClick };
    }
    function anchor(document, href) {
        const a = document.createElement('a');
        a.setAttribute('href', href);
        document.body.appendChild(a);
        return a;
    }

    it('cross-view route → preventDefault + live_redirect_mount', () => {
        const { document, ws, handle } = setup({ '/dashboard/': 'app.Dash' });
        const e = clickEvent(anchor(document, '/dashboard/'));
        handle(e);
        expect(e.preventDefault).toHaveBeenCalled();
        const msg = ws.sendMessage.mock.calls.at(-1)[0];
        expect(msg.type).toBe('live_redirect_mount');
    });

    it('same-view query change → preventDefault + url_change (not remount)', () => {
        const { document, ws, handle } = setup({ '/test/': 'app.Test' });
        const e = clickEvent(anchor(document, '/test/?page=2'));
        handle(e);
        expect(e.preventDefault).toHaveBeenCalled();
        const msg = ws.sendMessage.mock.calls.at(-1)[0];
        expect(msg.type).toBe('url_change');
        expect(msg.params).toEqual({ page: '2' });
    });

    it('path NOT in route map → falls through (no preventDefault)', () => {
        const { document, ws, handle } = setup({ '/dashboard/': 'app.Dash' });
        const e = clickEvent(anchor(document, '/admin/'));
        handle(e);
        expect(e.preventDefault).not.toHaveBeenCalled();
        expect(ws.sendMessage).not.toHaveBeenCalled();
    });

    it('external origin → falls through', () => {
        const { document, handle } = setup({ '/dashboard/': 'app.Dash' });
        const e = clickEvent(anchor(document, 'https://evil.example.com/dashboard/'));
        handle(e);
        expect(e.preventDefault).not.toHaveBeenCalled();
    });

    it('same-page hash-only jump → falls through (browser scrolls)', () => {
        const { document, handle } = setup({ '/test/': 'app.Test' });
        const e = clickEvent(anchor(document, '/test/#section'));
        handle(e);
        expect(e.preventDefault).not.toHaveBeenCalled();
    });

    it('mailto: scheme → falls through', () => {
        const { document, handle } = setup({ '/dashboard/': 'app.Dash' });
        const e = clickEvent(anchor(document, 'mailto:x@y.com'));
        handle(e);
        expect(e.preventDefault).not.toHaveBeenCalled();
    });
});

describe('auto_navigate is opt-in (meta-gated)', () => {
    it('does NOT install the delegated listener without the meta flag', () => {
        const ws = makeWS();
        const { window, document } = createEnv({
            routeMap: { '/dashboard/': 'app.Dash' },
            ws,
            autoNav: false,
        });
        const a = document.createElement('a');
        a.setAttribute('href', '/dashboard/');
        document.body.appendChild(a);
        const ev = new window.MouseEvent('click', { bubbles: true, cancelable: true });
        a.dispatchEvent(ev);
        // No interception: the route link was not SPA-navigated.
        expect(ev.defaultPrevented).toBe(false);
        expect(ws.sendMessage).not.toHaveBeenCalled();
    });

    it('DOES intercept a route link via a real delegated click when enabled', () => {
        const ws = makeWS();
        const { window, document } = createEnv({
            routeMap: { '/dashboard/': 'app.Dash' },
            ws,
            autoNav: true,
        });
        const a = document.createElement('a');
        a.setAttribute('href', '/dashboard/');
        document.body.appendChild(a);
        const ev = new window.MouseEvent('click', { bubbles: true, cancelable: true });
        a.dispatchEvent(ev);
        expect(ev.defaultPrevented).toBe(true);
        expect(ws.sendMessage).toHaveBeenCalled();
    });
});
