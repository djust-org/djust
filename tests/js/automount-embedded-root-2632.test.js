/**
 * #2632 — autoMount must pick the PAGE container, not a valueless `dj-view`.
 *
 * `{% live_render %}` emits `<div dj-view data-djust-embedded="…">` and
 * `<div dj-view dj-sticky-view="…" dj-sticky-root>` — a VALUELESS `dj-view`
 * that `querySelector('[dj-view]')` matches by attribute presence. When such
 * a root precedes the page container in document order, `autoMount` read
 * `""` as the view path and refused the mount ("Container found but no view
 * path specified") against a correct template. Two sibling lookups in
 * 03-websocket.js excluded sticky roots; this one did not (#1646 drift).
 *
 * Structural pin: every page-container lookup in 03-websocket.js goes through
 * ONE helper, so the next site added without the qualifier fails loudly.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const CLIENT_SRC = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const WS_MODULE = fs.readFileSync('./python/djust/static/djust/src/03-websocket.js', 'utf-8');

function boot(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    // HTTP-only mode: djustInit creates the LiveViewWebSocket instance
    // without connecting, so autoMount can be driven directly.
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(CLIENT_SRC);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function driveAutoMount(dom) {
    const client = dom.window.djust.liveViewInstance;
    expect(client, 'djustInit must have created the client instance').toBeTruthy();
    const mount = vi.spyOn(client, 'mount').mockImplementation(() => {});
    const warn = vi.spyOn(dom.window.console, 'warn').mockImplementation(() => {});
    client.autoMount();
    return { mount, warn };
}

const PAGE = '<div dj-view="app.views.PageView" dj-root><p>page</p></div>';
const EMBEDDED_FIRST = '<div dj-view data-djust-embedded="child-1"><p>child</p></div>';
const STICKY_FIRST = '<div dj-view dj-sticky-view="app.views.Nav" dj-sticky-root><p>nav</p></div>';

afterEach(() => vi.restoreAllMocks());

describe('autoMount picks the page container (#2632)', () => {
    it('ignores an embedded child with a bare dj-view that precedes the page root', () => {
        const dom = boot(EMBEDDED_FIRST + PAGE);
        const { mount, warn } = driveAutoMount(dom);
        expect(mount).toHaveBeenCalledTimes(1);
        expect(mount.mock.calls[0][0]).toBe('app.views.PageView');
        expect(warn).not.toHaveBeenCalled();
    });

    it('ignores a sticky root with a bare dj-view that precedes the page root', () => {
        const dom = boot(STICKY_FIRST + PAGE);
        const { mount, warn } = driveAutoMount(dom);
        expect(mount).toHaveBeenCalledTimes(1);
        expect(mount.mock.calls[0][0]).toBe('app.views.PageView');
        expect(warn).not.toHaveBeenCalled();
    });

    it('still mounts a lone page root (the document-order case that always worked)', () => {
        const dom = boot(PAGE);
        const { mount, warn } = driveAutoMount(dom);
        expect(mount).toHaveBeenCalledTimes(1);
        expect(mount.mock.calls[0][0]).toBe('app.views.PageView');
        expect(warn).not.toHaveBeenCalled();
    });

    it('warns (does not mount) when the only dj-view is an embedded child', () => {
        // No page container at all: the old code found the embedded child and
        // warned "no view path"; the new code finds nothing and warns "no
        // container". Either way NO mount is attempted with an empty path.
        const dom = boot(EMBEDDED_FIRST);
        const { mount, warn } = driveAutoMount(dom);
        expect(mount).not.toHaveBeenCalled();
        expect(warn).toHaveBeenCalledTimes(1);
    });
});

describe('structural pin: one page-container helper in 03-websocket.js (#2632, #1646)', () => {
    it('defines findPageViewContainer with BOTH exclusions', () => {
        expect(WS_MODULE).toContain(
            "querySelector('[dj-view]:not([dj-sticky-root]):not([data-djust-embedded])')"
        );
    });

    it('has no bare [dj-view] lookup and no inline :not([dj-sticky-root]) copy', () => {
        // The bare form is the #2632 bug; the inline-qualified form is the
        // pre-fix drift (two sites carried it by hand, one did not).
        expect(WS_MODULE).not.toContain("querySelector('[dj-view]')");
        const inlineQualified = WS_MODULE.split("'[dj-view]:not([dj-sticky-root])'").length - 1;
        expect(inlineQualified).toBe(0);
    });

    it('routes every page-container lookup through the helper (pinned call-site set)', () => {
        const all = WS_MODULE.match(/\bfindPageViewContainer\(\)/g) || [];
        const defs = WS_MODULE.match(/function findPageViewContainer\(\)/g) || [];
        expect(defs.length).toBe(1);
        // Exactly the three sites the issue enumerated: prerender morph
        // (skipMountHtml), html_update replacement, and autoMount.
        expect(all.length - defs.length).toBe(3);
    });
});
