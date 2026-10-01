/**
 * #3318 -- a `theme_nav_item` outside `dj-root` keeps its active state across
 * client-side navigation.
 *
 * `theme_nav_item` works out `active` from `request.path` at render time. A nav
 * that sits outside the swapped `dj-root` (the `sidebar_topbar` layout, or an
 * app-supplied root inside the `{% block %}` hooks around `<main>`) is never
 * re-rendered by `dj-navigate`, so the highlight stayed on the page you left.
 *
 * The nav template stamps `data-dj-nav="<url>"` on an auto-detected link and
 * `components.js` recomputes the active state on `djust:navigate-end` and
 * `popstate` with the SAME rule the server applies: exact match for `/`, a
 * path prefix for everything else.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const COMPONENTS_JS = fs.readFileSync(
    './python/djust/theming/static/djust_theming/js/components.js',
    'utf-8'
);
const CLIENT_JS = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const link = (id, url, extra = '') =>
    `<a id="${id}" href="${url}" data-dj-nav="${url}" class="nav-link theme-nav-link" ${extra}>${id}</a>`;

function createDom({ url = 'http://localhost/', root = '<main dj-root dj-view="a.B"></main>', nav } = {}) {
    const navHtml =
        nav ||
        link('home', '/') + link('inbox', '/inbox/') + link('archive', '/inbox/archive/') +
        '<a id="pinned" href="/inbox/" class="nav-link theme-nav-link active" aria-current="page">pinned</a>';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><nav>${navHtml}</nav>${root}</body></html>`,
        { url, runScripts: 'outside-only', pretendToBeVisual: true }
    );
    dom.window.eval(COMPONENTS_JS);
    return dom;
}

// JSDOM is still `loading` when the script runs, so components.js waits for
// DOMContentLoaded to do its first pass.
const settled = () => new Promise((r) => setTimeout(r, 0));

function go(dom, path, eventTarget = 'document-navigate-end') {
    dom.window.history.pushState({}, '', path);
    if (eventTarget === 'document-navigate-end') {
        dom.window.document.dispatchEvent(new dom.window.CustomEvent('djust:navigate-end'));
    } else if (eventTarget === 'popstate') {
        dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate'));
    }
}

function activeIds(dom) {
    return [...dom.window.document.querySelectorAll('[data-dj-nav].active')].map((a) => a.id);
}

describe('#3318 -- nav active state follows client-side navigation', () => {
    it('moves the highlight on djust:navigate-end', async () => {
        const dom = createDom({ url: 'http://localhost/' });
        await settled();
        expect(activeIds(dom)).toEqual(['home']);
        go(dom, '/inbox/');
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('sets aria-current="page" on the active link and removes it from the rest', async () => {
        const dom = createDom({ url: 'http://localhost/inbox/' });
        await settled();
        const doc = dom.window.document;
        expect(doc.getElementById('inbox').getAttribute('aria-current')).toBe('page');
        go(dom, '/');
        expect(doc.getElementById('inbox').hasAttribute('aria-current')).toBe(false);
        expect(doc.getElementById('home').getAttribute('aria-current')).toBe('page');
    });

    it('matches "/" exactly, never as a prefix', () => {
        const dom = createDom({ url: 'http://localhost/inbox/' });
        expect(activeIds(dom)).not.toContain('home');
    });

    it('matches every other url as a path prefix, like the server does', () => {
        const dom = createDom({ url: 'http://localhost/inbox/' });
        go(dom, '/inbox/archive/2024/');
        expect(activeIds(dom)).toEqual(['inbox', 'archive']);
    });

    it('follows back/forward (popstate)', () => {
        const dom = createDom({ url: 'http://localhost/' });
        go(dom, '/inbox/', 'popstate');
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('works on a page that has a djust mount root (components.js stands down there)', () => {
        const dom = createDom({ url: 'http://localhost/', root: '<main dj-root dj-view="a.B"></main>' });
        expect(dom.window.document.querySelector('[dj-view]')).not.toBeNull();
        go(dom, '/inbox/');
        expect(activeIds(dom)).toEqual(['inbox']);
    });

    it('leaves a link without the marker alone (an explicit active=)', () => {
        const dom = createDom({ url: 'http://localhost/' });
        go(dom, '/somewhere/else/');
        const pinned = dom.window.document.getElementById('pinned');
        expect(pinned.classList.contains('active')).toBe(true);
        expect(pinned.getAttribute('aria-current')).toBe('page');
    });

    it('keeps other classes on the link when it toggles', () => {
        const dom = createDom({ url: 'http://localhost/' });
        go(dom, '/inbox/');
        const home = dom.window.document.getElementById('home');
        expect(home.className).toBe('nav-link theme-nav-link');
    });
});

// Mirrors PARITY_CASES in python/djust/tests/test_theming_nav_item_marker_3318.py:
// the server's rule and this one must give the same answer.
const PARITY_CASES = [
    ['/', '/', true],
    ['/', '/inbox/', false],
    ['/inbox/', '/inbox/', true],
    ['/inbox/', '/inbox/archive/', true],
    ['/inbox/', '/other/', false],
    ['/inbox/', '/', false],
    ['/inbox/archive/', '/inbox/', false],
];

describe('#3318 -- client rule matches the server rule', () => {
    for (const [url, path, expected] of PARITY_CASES) {
        it(`${url} on ${path} -> ${expected ? 'active' : 'inactive'}`, async () => {
            const dom = createDom({ url: 'http://localhost' + path, nav: link('x', url) });
            await settled();
            const el = dom.window.document.getElementById('x');
            expect(el.classList.contains('active')).toBe(expected);
            expect(el.getAttribute('aria-current')).toBe(expected ? 'page' : null);
        });
    }
});

describe('#3318 -- dj-navigate aria-current sync leaves data-dj-nav links to the theme', () => {
    it('does not strip aria-current from a prefix-active data-dj-nav link', async () => {
        const dom = new JSDOM(
            '<!DOCTYPE html><html><body><nav>' +
                '<a id="a" href="/inbox/" dj-navigate="/inbox/" data-dj-nav="/inbox/" aria-current="page">a</a>' +
                '<a id="b" href="/other/" dj-navigate="/other/" aria-current="page">b</a>' +
                '</nav><div dj-root dj-view="t.V"></div></body></html>',
            { url: 'http://localhost/inbox/archive/', runScripts: 'dangerously' }
        );
        const win = dom.window;
        if (!win.CSS) win.CSS = {};
        if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
        win.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
        win.WebSocket.OPEN = 1;
        win.eval(CLIENT_JS);
        await new Promise((r) => setTimeout(r, 10));
        win.djust.navigation.updateAriaCurrent();
        // Theme-managed link: untouched. Plain dj-navigate link that is not an exact match: stripped.
        expect(win.document.getElementById('a').getAttribute('aria-current')).toBe('page');
        expect(win.document.getElementById('b').hasAttribute('aria-current')).toBe(false);
    });
});
