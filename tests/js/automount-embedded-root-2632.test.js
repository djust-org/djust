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

const SRC_DIR = './python/djust/static/djust/src';
const CLIENT_SRC = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const WS_MODULE = fs.readFileSync(`${SRC_DIR}/03-websocket.js`, 'utf-8');
const MODULES = fs.readdirSync(SRC_DIR)
    .filter((f) => /^[0-9].*\.js$/.test(f))
    .map((f) => [f, fs.readFileSync(`${SRC_DIR}/${f}`, 'utf-8')]);

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

describe('SSE path picks the page container too (#2632 — the WS/SSE twin)', () => {
    it('_switchToSSETransport connects the page view, not the sticky root before it', () => {
        const dom = boot(STICKY_FIRST + PAGE);
        const connected = [];
        dom.window.djust.LiveViewSSE = class {
            connect(viewPath, params) { connected.push([viewPath, params]); }
        };
        const warn = vi.spyOn(dom.window.console, 'warn').mockImplementation(() => {});
        dom.window.djust._switchToSSETransport();
        expect(connected.length).toBe(1);
        expect(connected[0][0]).toBe('app.views.PageView');
        expect(warn).not.toHaveBeenCalled();
    });

    it('_switchToSSETransport warns and does not connect when only an embedded child exists', () => {
        const dom = boot(EMBEDDED_FIRST);
        const connected = [];
        dom.window.djust.LiveViewSSE = class {
            connect(viewPath) { connected.push(viewPath); }
        };
        const warn = vi.spyOn(dom.window.console, 'warn').mockImplementation(() => {});
        dom.window.djust._switchToSSETransport();
        expect(connected.length).toBe(0);
        expect(warn).toHaveBeenCalledTimes(1);
    });
});

describe('structural pin: ONE page-container helper across src/ (#2632, #1646)', () => {
    // Every site that answers "which element is THE page LiveView container"
    // must go through findPageViewContainer(). A bare `[dj-view]` pick or an
    // inline `:not([dj-sticky-root])` copy is the drift this closes.
    // querySelectorAll('[dj-view]') (stamping/registering ALL containers)
    // and attribute-qualified lookups ([dj-view][data-djust-embedded=…]) are
    // different questions and are not counted.
    const EXPECTED_CALL_SITES = {
        '03-websocket.js': 3,   // prerender morph, html_update replace, autoMount
        '03b-sse.js': 1,        // SSE mount html target
        '09-event-binding.js': 3, // delegated-listener root, getLiveViewRoot, form recovery
        '12-vdom-patch.js': 2,  // positional-fallback root, dj-id stamping root
        '14-init.js': 1,        // _switchToSSETransport
        '18-navigation.js': 2,  // getCurrentViewPath fallback, SW fast-path container
        '46-state-snapshot.js': 1, // snapshot view-path fallback
    };

    it('defines findPageViewContainer once, in 03-websocket.js, with BOTH exclusions', () => {
        expect(WS_MODULE).toContain(
            "querySelector('[dj-view]:not([dj-sticky-root]):not([data-djust-embedded])')"
        );
        const defs = MODULES.flatMap(([, src]) => src.match(/function findPageViewContainer\(\)/g) || []);
        expect(defs.length).toBe(1);
    });

    it('no module carries a bare [dj-view] lookup or an inline :not([dj-sticky-root]) copy', () => {
        for (const [name, src] of MODULES) {
            expect(src.includes("querySelector('[dj-view]')"), `${name}: bare [dj-view] lookup`).toBe(false);
            expect(src.includes("'[dj-view]:not([dj-sticky-root])'"), `${name}: inline qualified copy`).toBe(false);
        }
    });

    it('routes every page-container lookup through the helper (pinned call-site SET)', () => {
        const actual = {};
        for (const [name, src] of MODULES) {
            const all = (src.match(/\bfindPageViewContainer\(\)/g) || []).length;
            const defs = (src.match(/function findPageViewContainer\(\)/g) || []).length;
            if (all - defs > 0) actual[name] = all - defs;
        }
        expect(actual).toEqual(EXPECTED_CALL_SITES);
    });
});
