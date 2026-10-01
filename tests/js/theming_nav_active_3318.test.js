/**
 * #3318 -- a `theme_nav_item` outside `dj-root` keeps its active state across
 * client-side navigation.
 *
 * `theme_nav_item` works out `active` from `request.path` at render time. A nav
 * that sits outside the swapped `dj-root` (the `sidebar_topbar` layout, or an
 * app-supplied root inside the `{% block %}` hooks around `<main>`) is never
 * re-rendered by `dj-navigate`, so the highlight stayed on the page you left.
 *
 * The nav template stamps `data-dj-nav="<url>"` on an auto-detected link.
 * `18-navigation.js` dispatches `djust:path-changed` on `document` whenever the
 * rendered pathname moves, and `components.js` recomputes the active state from
 * it (and from `popstate`) with the SAME rule the server applies. The tests here
 * drive the REAL navigation code (live_redirect, live_patch, an auto-navigate
 * click), not a synthetic event, because the first version listened to
 * `djust:navigate-end`, which only the page-loading bar dispatches.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const COMPONENTS_JS = fs.readFileSync(
    './python/djust/theming/static/djust_theming/js/components.js',
    'utf-8'
);
const NAV_SOURCE = fs.readFileSync('./python/djust/static/djust/src/18-navigation.js', 'utf-8');
const CLIENT_JS = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const link = (id, url, extra = '') =>
    `<a id="${id}" href="${url}" data-dj-nav="${url}" class="nav-link theme-nav-link" ${extra}>${id}</a>`;

const ROUTES = {
    '/': 'app.Home',
    '/inbox/': 'app.Inbox',
    '/inbox/archive/': 'app.Archive',
    '/other/': 'app.Other',
};

/** 18-navigation.js (real) + components.js on one page, real history. */
function createDom({ url = 'http://localhost/', nav, autoNav = false, pageLoadingEnabled } = {}) {
    const navHtml =
        nav ||
        link('home', '/') +
            link('inbox', '/inbox/') +
            link('archive', '/inbox/archive/') +
            '<a id="pinned" href="/inbox/" class="nav-link theme-nav-link active" aria-current="page">pinned</a>';
    const meta = autoNav ? '<meta name="djust-auto-navigate" content="1">' : '';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${meta}</head><body><nav>${navHtml}</nav>` +
            '<main dj-root dj-view="a.B"></main></body></html>',
        { url, runScripts: 'outside-only', pretendToBeVisual: true }
    );
    const w = dom.window;
    w.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    w.eval(`window.djust = { _routeMap: ${JSON.stringify(ROUTES)} };`);
    if (pageLoadingEnabled !== undefined) {
        w.djust.pageLoading = { enabled: pageLoadingEnabled, start() {}, finish() {} };
    }
    // Globals 18-navigation.js reads as free variables.
    w.eval('function isWSConnected(){ return true; }');
    w.eval(
        'function findPageViewContainer(){ return document.querySelector("[dj-view]:not([dj-sticky-root]):not([data-djust-embedded])"); }'
    );
    const ws = { ws: {}, viewMounted: true, sendMessage() {}, liveRedirectMount() {} };
    w.liveViewWS_mock = ws;
    w.eval('var liveViewWS = window.liveViewWS_mock;');
    w.eval(NAV_SOURCE);
    w.djust.navigation.installAutoNavigate();
    w.eval(COMPONENTS_JS);
    return dom;
}

// JSDOM is still `loading` when the script runs, so components.js waits for
// DOMContentLoaded to do its first pass.
const settled = () => new Promise((r) => setTimeout(r, 0));

function activeIds(dom) {
    return [...dom.window.document.querySelectorAll('[data-dj-nav].active')].map((a) => a.id);
}

function popstateTo(dom, path) {
    dom.window.history.pushState({}, '', path);
    dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate'));
}

function click(dom, el) {
    el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
}

describe('#3318 -- nav active state follows the real navigation paths', () => {
    it('live_redirect with the page-loading bar DISABLED (no djust:navigate-end)', async () => {
        const dom = createDom({ pageLoadingEnabled: false });
        await settled();
        expect(activeIds(dom)).toEqual(['home']);
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/inbox/' });
        expect(dom.window.location.pathname).toBe('/inbox/');
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('live_redirect with the page-loading bar absent', async () => {
        const dom = createDom();
        await settled();
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/other/' });
        expect(activeIds(dom)).toEqual([]);
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/inbox/' });
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('live_patch to a new path', async () => {
        const dom = createDom();
        await settled();
        dom.window.djust.navigation.handleNavigation({ action: 'live_patch', path: '/inbox/' });
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('live_patch that only changes the query leaves the highlight alone', async () => {
        const dom = createDom({ url: 'http://localhost/inbox/' });
        await settled();
        dom.window.djust.navigation.handleNavigation({
            action: 'live_patch',
            params: { page: '2' },
        });
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('an auto-navigate click on a link to another path', async () => {
        const dom = createDom({
            autoNav: true,
            nav: link('home', '/') + link('inbox', '/inbox/') + link('other', '/other/'),
        });
        await settled();
        click(dom, dom.window.document.getElementById('inbox'));
        expect(dom.window.location.pathname).toBe('/inbox/');
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('back/forward (popstate)', async () => {
        const dom = createDom();
        await settled();
        popstateTo(dom, '/inbox/');
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('sets aria-current="page" on the active link and removes it from the rest', async () => {
        const dom = createDom({ url: 'http://localhost/inbox/' });
        await settled();
        const doc = dom.window.document;
        expect(doc.getElementById('inbox').getAttribute('aria-current')).toBe('page');
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/' });
        expect(doc.getElementById('inbox').hasAttribute('aria-current')).toBe(false);
        expect(doc.getElementById('home').getAttribute('aria-current')).toBe('page');
    });

    it('works on a page that has a djust mount root (components.js stands down there)', async () => {
        const dom = createDom();
        expect(dom.window.document.querySelector('[dj-view]')).not.toBeNull();
        await settled();
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/inbox/' });
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('leaves a link without the marker alone (an explicit active=)', async () => {
        const dom = createDom();
        await settled();
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/other/' });
        const pinned = dom.window.document.getElementById('pinned');
        expect(pinned.classList.contains('active')).toBe(true);
        expect(pinned.getAttribute('aria-current')).toBe('page');
    });

    it('keeps other classes on the link when it toggles', async () => {
        const dom = createDom();
        await settled();
        dom.window.djust.navigation.handleNavigation({ action: 'live_redirect', path: '/inbox/' });
        expect(dom.window.document.getElementById('home').className).toBe('nav-link theme-nav-link');
    });
});

// Mirrors PARITY_CASES in python/djust/tests/test_theming_nav_item_marker_3318.py:
// the server's rule and this one must give the same answer. Columns: the link's
// url, the path Django reports (`request.path`, percent-DECODED), the path the
// browser reports (`location.pathname`, percent-ENCODED), and whether it is active.
const PARITY_CASES = [
    ['/', '/', '/', true],
    ['/', '/inbox/', '/inbox/', false],
    ['/inbox/', '/inbox/', '/inbox/', true],
    ['/inbox/', '/inbox/archive/', '/inbox/archive/', true],
    ['/inbox/', '/other/', '/other/', false],
    ['/inbox/', '/', '/', false],
    ['/inbox/archive/', '/inbox/', '/inbox/', false],
    // No trailing slash: a segment boundary, never a bare string prefix.
    ['/docs', '/docs', '/docs', true],
    ['/docs', '/docs/', '/docs/', true],
    ['/docs', '/docs/intro/', '/docs/intro/', true],
    ['/docs', '/docs-old/', '/docs-old/', false],
    ['/docs/', '/docs-old/', '/docs-old/', false],
    ['/docs/', '/docs', '/docs', true],
    ['/doc', '/docs/', '/docs/', false],
    // Percent-encoding: the link may be either spelling.
    ['/caf%C3%A9/', '/café/', '/caf%C3%A9/', true],
    ['/café/', '/café/', '/caf%C3%A9/', true],
    ['/caf%C3%A9/', '/café/menu/', '/caf%C3%A9/menu/', true],
    ['/caf%C3%A9/', '/cafe/', '/cafe/', false],
];

describe('#3318 -- client rule matches the server rule', () => {
    for (const [url, , locationPath, expected] of PARITY_CASES) {
        it(`${url} on ${locationPath} -> ${expected ? 'active' : 'inactive'}`, async () => {
            const dom = createDom({ url: 'http://localhost' + locationPath, nav: link('x', url) });
            await settled();
            const el = dom.window.document.getElementById('x');
            expect(el.classList.contains('active')).toBe(expected);
            expect(el.getAttribute('aria-current')).toBe(expected ? 'page' : null);
        });
    }
});

describe('#3318 -- dj-navigate aria-current sync and data-dj-nav links', () => {
    async function createClientDom({ themeScript }) {
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body><nav>' +
                '<a id="a" href="/inbox/" dj-navigate="/inbox/" data-dj-nav="/inbox/" aria-current="page">a</a>' +
                '<a id="b" href="/other/" dj-navigate="/other/" aria-current="page">b</a>' +
                '<a id="c" href="/inbox/archive/" dj-navigate="/inbox/archive/" data-dj-nav="/inbox/archive/">c</a>' +
                '</nav><div dj-root dj-view="t.V"></div></body></html>',
            { url: 'http://localhost/inbox/archive/', runScripts: 'dangerously' }
        );
        const win = dom.window;
        if (!win.CSS) win.CSS = {};
        if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        win.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
        win.WebSocket.OPEN = 1;
        if (themeScript) win.djustComponents = { updateNavActive() {} };
        win.eval(CLIENT_JS);
        await new Promise((r) => setTimeout(r, 10));
        win.djust.navigation.updateAriaCurrent();
        return win;
    }

    it('with components.js loaded, leaves a data-dj-nav link to the theme (no strip)', async () => {
        const win = await createClientDom({ themeScript: true });
        expect(win.document.getElementById('a').getAttribute('aria-current')).toBe('page');
        // A plain dj-navigate link that is not an exact match is still stripped.
        expect(win.document.getElementById('b').hasAttribute('aria-current')).toBe(false);
    });

    it('without components.js (include_js=False), the exact-match sync still runs on them', async () => {
        const win = await createClientDom({ themeScript: false });
        // /inbox/ is not the exact path: stripped; /inbox/archive/ is exact: set.
        expect(win.document.getElementById('a').hasAttribute('aria-current')).toBe(false);
        expect(win.document.getElementById('c').getAttribute('aria-current')).toBe('page');
    });
});
