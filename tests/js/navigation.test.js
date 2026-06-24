/**
 * Tests for navigation — URL state management (src/18-navigation.js)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const navSourceCode = fs.readFileSync('./python/djust/static/djust/src/18-navigation.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Mock history.pushState and replaceState since JSDOM has limited support
    const historyCalls = [];
    const origPushState = window.history.pushState.bind(window.history);
    const origReplaceState = window.history.replaceState.bind(window.history);

    window.history.pushState = vi.fn((state, title, url) => {
        historyCalls.push({ method: 'pushState', state, title, url });
        try { origPushState(state, title, url); } catch (e) { /* JSDOM limitation */ }
    });

    window.history.replaceState = vi.fn((state, title, url) => {
        historyCalls.push({ method: 'replaceState', state, title, url });
        try { origReplaceState(state, title, url); } catch (e) { /* JSDOM limitation */ }
    });

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document, historyCalls };
}

/**
 * Create a minimal JSDOM env that loads only 18-navigation.js (not the full bundle).
 * This allows us to inject a mock liveViewWS before the script runs, so click
 * handler tests can exercise the actual URL-update branch without a real WS.
 */
function createNavSourceEnv(bodyHtml = '', liveViewWSMock = null, opts = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    const historyCalls = [];
    const origPushState = window.history.pushState.bind(window.history);
    const origReplaceState = window.history.replaceState.bind(window.history);
    window.history.pushState = vi.fn((state, title, url) => {
        historyCalls.push({ method: 'pushState', state, title, url });
        try { origPushState(state, title, url); } catch (e) {}
    });
    window.history.replaceState = vi.fn((state, title, url) => {
        historyCalls.push({ method: 'replaceState', state, title, url });
        try { origReplaceState(state, title, url); } catch (e) {}
    });

    // Provide djust namespace and liveViewWS before loading nav code so
    // the IIFE's free variable references resolve to these values.
    window.eval('window.djust = { _routeMap: {} };');

    // Seed the route map if the caller supplied one.
    if (opts.routeMap) {
        window.djust._routeMap = opts.routeMap;
    }

    // 18-navigation.js calls the free function isWSConnected() (defined in
    // 02-response-handler.js in the full bundle) and window.djust.safeNavigationTarget
    // (02b-safe-nav.js). When loading ONLY the nav source we must stub them, or
    // handleLiveRedirect throws a ReferenceError. Defaults model the bug scenario:
    // WS connected, a faithful-enough safeNavigationTarget.
    const wsConnected = opts.wsConnected !== undefined ? opts.wsConnected : true;
    window.__wsConnected = wsConnected;
    window.eval('function isWSConnected() { return !!window.__wsConnected; }');

    // safeNavigationTarget spy: faithful-enough — accepts same-origin string
    // paths (returns them unchanged), rejects non-strings/cross-origin like the
    // real guard (02b-safe-nav.js).
    const safeNav = vi.fn((value) => {
        if (typeof value !== 'string' || value.length === 0) return null;
        try {
            const u = new URL(value, window.location.origin);
            if (u.origin !== window.location.origin) return null;
            return u.pathname + u.search + u.hash;
        } catch (_e) {
            return null;
        }
    });
    window.djust.safeNavigationTarget = safeNav;

    // page-loading bar mock — mirror the real shape (start/finish/enabled).
    // The cross-origin branch (and the #1934 fallback) call `.stop?.()` which
    // does not exist; that is intentional (optional-chaining → no-op).
    window.djust.pageLoading = { start: () => {}, finish: () => {}, enabled: true };

    // Capture window.location.href assignments. JSDOM throws
    // "Not implemented: navigation to another Document" on a real href set, so
    // the production code's assignment is wrapped where needed; here we also
    // record what the full-page-nav branch *tried* to navigate to.
    const locationAssignments = [];
    try {
        const realHrefDesc = Object.getOwnPropertyDescriptor(window.location, 'href');
        // Some JSDOM builds forbid redefining location.href; fall back to spying
        // via safeNavigationTarget's return + the throw-catch in the test.
        if (realHrefDesc && realHrefDesc.configurable) {
            Object.defineProperty(window.location, 'href', {
                configurable: true,
                get() { return realHrefDesc.get.call(window.location); },
                set(v) { locationAssignments.push(v); },
            });
        }
    } catch (_e) { /* fall back to the safeNav spy as the full-nav signal */ }

    if (liveViewWSMock !== null) {
        window.eval('var liveViewWS = ' + JSON.stringify(null) + ';');
        window.liveViewWS_mock = liveViewWSMock;
        // Expose as a var so the IIFE can see it as a free variable
        window.eval('var liveViewWS = window.liveViewWS_mock;');
    }

    try {
        window.eval(navSourceCode);
    } catch (e) {}

    return { window, dom, document: dom.window.document, historyCalls, safeNav, locationAssignments };
}

describe('navigation', () => {
    describe('handleNavigation', () => {
        it('live_patch pushes state by default', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                path: '/items/',
                params: { page: '2' },
            });

            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].method).toBe('pushState');
            expect(historyCalls[0].url).toContain('/items/');
            expect(historyCalls[0].url).toContain('page=2');
        });

        it('live_patch with replace uses replaceState', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                path: '/items/',
                params: { page: '3' },
                replace: true,
            });

            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].method).toBe('replaceState');
        });

        it('live_patch sets query params', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                params: { q: 'search', page: '1' },
            });

            expect(historyCalls.length).toBe(1);
            const url = historyCalls[0].url;
            expect(url).toContain('q=search');
            expect(url).toContain('page=1');
        });

        it('live_patch omits empty params', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                params: { q: '', page: '1', empty: null },
            });

            expect(historyCalls.length).toBe(1);
            const url = historyCalls[0].url;
            expect(url).not.toContain('q=');
            expect(url).toContain('page=1');
            expect(url).not.toContain('empty');
        });
    });

    describe('resolveViewPath', () => {
        it('exact match returns view path', () => {
            const { window } = createEnv();

            window.djust._routeMap = {
                '/items/': 'myapp.views.ItemListView',
                '/about/': 'myapp.views.AboutView',
            };

            expect(window.djust.navigation.resolveViewPath('/items/')).toBe('myapp.views.ItemListView');
        });

        it('pattern match with :id works', () => {
            const { window } = createEnv();

            window.djust._routeMap = {
                '/items/:id/': 'myapp.views.ItemDetailView',
            };

            expect(window.djust.navigation.resolveViewPath('/items/42/')).toBe('myapp.views.ItemDetailView');
        });

        it('returns null for unknown path with no container', () => {
            const { window } = createEnv();

            window.djust._routeMap = {
                '/items/': 'myapp.views.ItemListView',
            };

            // Remove any dj-view containers
            const containers = window.document.querySelectorAll('[dj-view]');
            containers.forEach(c => c.removeAttribute('dj-view'));

            expect(window.djust.navigation.resolveViewPath('/unknown/')).toBeNull();
        });

        it('falls back to dj-view attribute', () => {
            const { window, document } = createEnv('<div dj-view="myapp.views.CurrentView"></div>');

            window.djust._routeMap = {};

            expect(window.djust.navigation.resolveViewPath('/any-path/')).toBe('myapp.views.CurrentView');
        });

        it('does not match Object.prototype keys (#1361 prototype-pollution-immune)', () => {
            // Regression for #1361: routeMap access uses Object.entries() walk
            // (own enumerable string-keyed entries only), so paths like
            // 'toString' / 'constructor' that resolve via prototype chain
            // on plain object access must NOT match. If a future refactor
            // reverts to `routeMap[pathname]`, this test fails.
            const { window } = createEnv('');
            window.djust._routeMap = {};

            expect(window.djust.navigation.resolveViewPath('toString')).toBeNull();
            expect(window.djust.navigation.resolveViewPath('constructor')).toBeNull();
            expect(window.djust.navigation.resolveViewPath('hasOwnProperty')).toBeNull();
        });

        it('ignores a polluted Object.prototype (#1361 / #1733 default route map)', () => {
            // Stronger #1361 canary: actually pollute Object.prototype with a
            // key that looks like a route. Because resolveViewPath walks OWN
            // enumerable entries (Object.entries) and never bracket-indexes
            // routeMap[pathname], the injected prototype entry must NOT leak
            // through as a resolved view. This is load-bearing now that #1733
            // populates the route map on every page by default.
            const { window } = createEnv('');
            window.djust._routeMap = {};
            // eslint-disable-next-line no-extend-native
            Object.prototype['/evil/'] = 'attacker.views.Pwned';
            try {
                expect(window.djust.navigation.resolveViewPath('/evil/')).toBeNull();
            } finally {
                delete Object.prototype['/evil/'];
            }
        });
    });

    describe('bindDirectives', () => {
        it('binds dj-patch elements', () => {
            const { window, document } = createEnv('<a dj-patch="?page=2">Page 2</a>');

            window.djust.navigation.bindDirectives();

            const link = document.querySelector('[dj-patch]');
            expect(link.dataset.djustPatchBound).toBe('true');
        });

        it('binds dj-navigate elements', () => {
            const { window, document } = createEnv('<a dj-navigate="/about/">About</a>');

            window.djust.navigation.bindDirectives();

            const link = document.querySelector('[dj-navigate]');
            expect(link.dataset.djustNavigateBound).toBe('true');
        });

        it('does not double-bind dj-patch', () => {
            const { window, document } = createEnv('<a dj-patch="?page=2">Page 2</a>');

            window.djust.navigation.bindDirectives();
            window.djust.navigation.bindDirectives();

            const link = document.querySelector('[dj-patch]');
            expect(link.dataset.djustPatchBound).toBe('true');
        });
    });

    describe('issue #307 regressions', () => {
        it('BUG 1: handleNavigation dispatches to handleLivePatch when action=live_patch', () => {
            // After the BUG 2 fix the server sends type:'navigation' + action:'live_patch'.
            // handleNavigation must use data.action to dispatch correctly.
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'navigation',
                action: 'live_patch',
                path: '/items/',
                params: { page: '5' },
            });

            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].url).toContain('/items/');
            expect(historyCalls[0].url).toContain('page=5');
        });

        it('BUG 1: handleNavigation dispatches to handleLiveRedirect when action=live_redirect', () => {
            // A LiveView target with a CONNECTED WS does the SPA mount: pushState
            // for the new path (#1934 defers pushState into this branch, so the
            // env must have a connected WS — see createNavSourceEnv default).
            const sent = [];
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: (m) => sent.push(m) };
            const { document, historyCalls } = createNavSourceEnv(
                '<div dj-view="myapp.views.DashboardView"></div>',
                wsMock,
                { routeMap: { '/dashboard/': 'myapp.views.DashboardView' }, wsConnected: true },
            );

            document.defaultView.djust.navigation.handleNavigation({
                type: 'navigation',
                action: 'live_redirect',
                path: '/dashboard/',
            });

            // pushState should have been called for the new path (SPA mount).
            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].url).toContain('/dashboard/');
            // …and the live_redirect_mount frame should have gone out over the WS.
            expect(sent.some((m) => m.type === 'live_redirect_mount' && m.view === 'myapp.views.DashboardView')).toBe(true);
        });

        it('BUG 1 (dj-patch): clicking dj-patch="/" updates pathname to /', () => {
            // Before the fix, url.pathname !== '/' skipped the pathname update
            // when the target is the root path.
            // We load the nav source file directly so liveViewWS can be injected.
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: () => {} };
            const { document, historyCalls } = createNavSourceEnv(
                '<a id="home" dj-patch="/">Home</a>',
                wsMock
            );

            document.querySelector('[dj-root] [dj-patch]') &&
                document.defaultView.djust.navigation.bindDirectives();
            document.getElementById('home').click();

            expect(historyCalls.length).toBe(1);
            const pushed = new URL(historyCalls[0].url);
            expect(pushed.pathname).toBe('/');
        });

        it('dj-patch="/some/path/" updates pathname correctly (existing behaviour)', () => {
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: () => {} };
            const { document, historyCalls } = createNavSourceEnv(
                '<a id="nav" dj-patch="/items/">Items</a>',
                wsMock
            );

            document.defaultView.djust.navigation.bindDirectives();
            document.getElementById('nav').click();

            expect(historyCalls.length).toBe(1);
            const pushed = new URL(historyCalls[0].url);
            expect(pushed.pathname).toBe('/items/');
        });
    });

    describe('dj-patch boolean on <a> tags (PR #640)', () => {
        it('boolean dj-patch on <a> falls back to href', () => {
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: () => {} };
            const { document, historyCalls } = createNavSourceEnv(
                '<a id="tab-docs" href="?tab=documents" dj-patch>Documents</a>',
                wsMock
            );

            document.defaultView.djust.navigation.bindDirectives();
            document.getElementById('tab-docs').click();

            expect(historyCalls.length).toBe(1);
            const pushed = new URL(historyCalls[0].url);
            expect(pushed.searchParams.get('tab')).toBe('documents');
        });

        it('boolean dj-patch on <a> uses href, not current URL', () => {
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: () => {} };
            const { document, historyCalls } = createNavSourceEnv(
                '<a id="tab-summary" href="?tab=summary" dj-patch>Summary</a>' +
                '<a id="tab-docs" href="?tab=documents" dj-patch>Documents</a>',
                wsMock
            );

            document.defaultView.djust.navigation.bindDirectives();
            // Click "Documents" — should navigate to ?tab=documents, not stay on current URL
            document.getElementById('tab-docs').click();

            expect(historyCalls.length).toBe(1);
            expect(new URL(historyCalls[0].url).searchParams.get('tab')).toBe('documents');
        });

        it('explicit dj-patch value still takes priority over href', () => {
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: () => {} };
            const { document, historyCalls } = createNavSourceEnv(
                '<a id="link" href="/ignore/" dj-patch="?filter=active">Active</a>',
                wsMock
            );

            document.defaultView.djust.navigation.bindDirectives();
            document.getElementById('link').click();

            expect(historyCalls.length).toBe(1);
            const pushed = new URL(historyCalls[0].url);
            expect(pushed.searchParams.get('filter')).toBe('active');
        });
    });

    describe('_routeMap', () => {
        it('is initialized as an object', () => {
            const { window } = createEnv();
            expect(window.djust._routeMap).toBeDefined();
            expect(typeof window.djust._routeMap).toBe('object');
        });
    });

    describe('exports', () => {
        it('exposes navigation object with expected methods', () => {
            const { window } = createEnv();
            expect(typeof window.djust.navigation.handleNavigation).toBe('function');
            expect(typeof window.djust.navigation.bindDirectives).toBe('function');
            expect(typeof window.djust.navigation.resolveViewPath).toBe('function');
        });
    });

    describe('issue #1599 — cross-origin pushState guard', () => {
        // Reported in production djust.org: clicking <a dj-navigate="https://djustlive.com/">
        // triggered `Uncaught SecurityError: Failed to execute 'pushState' on 'History':
        // A history state object with URL 'https://djustlive.com/' cannot be created in a
        // document with origin 'https://djust.org'`. Browser correctly refuses cross-origin
        // pushState; the framework must detect and fall back to a full-page nav instead
        // of crashing the JS runtime.
        //
        // The load-bearing assertion is: pushState MUST NOT be called with a cross-origin
        // URL. The fix sets `window.location.href = newUrl.toString()` and returns, so
        // the function may throw in JSDOM (which forbids navigation) but in a real browser
        // it does the full-page nav. The pushState-not-called invariant is what matters
        // for the actual bug (the SecurityError happened because pushState WAS called).

        it('handleLiveRedirect with cross-origin path does NOT call pushState with cross-origin URL', () => {
            const { window, historyCalls } = createEnv();

            try {
                window.djust.navigation.handleNavigation({
                    type: 'live_redirect',
                    path: 'https://djustlive.com/',
                });
            } catch (_e) {
                // JSDOM may throw on window.location.href assignment; harmless here.
            }

            // pushState/replaceState MUST NOT have been called with the cross-origin URL.
            const crossOriginPush = historyCalls.find(
                (c) => c.url && c.url.includes('djustlive.com'),
            );
            expect(crossOriginPush).toBeUndefined();
        });

        it('handleLivePatch with cross-origin path does NOT call pushState with cross-origin URL', () => {
            const { window, historyCalls } = createEnv();

            try {
                window.djust.navigation.handleNavigation({
                    type: 'live_patch',
                    path: 'https://djustlive.com/',
                });
            } catch (_e) {
                // JSDOM may throw on window.location.href assignment; harmless here.
            }

            const crossOriginPush = historyCalls.find(
                (c) => c.url && c.url.includes('djustlive.com'),
            );
            expect(crossOriginPush).toBeUndefined();
        });

        it('same-origin paths still use pushState (regression backstop for guard breadth)', () => {
            // If the cross-origin guard accidentally widens to reject same-origin
            // navigations, this test catches it. Same-origin live_redirect to a
            // resolvable LiveView with a connected WS must continue to use
            // pushState (SPA mount). #1934 defers the pushState into that branch,
            // so the env needs a connected WS + populated route map.
            const sent = [];
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: (m) => sent.push(m) };
            const { document, historyCalls } = createNavSourceEnv(
                '<div dj-view="myapp.views.DashboardView"></div>',
                wsMock,
                { routeMap: { '/dashboard/': 'myapp.views.DashboardView' }, wsConnected: true },
            );

            document.defaultView.djust.navigation.handleNavigation({
                type: 'live_redirect',
                path: '/dashboard/',
            });

            // At least one same-origin pushState/replaceState should have occurred.
            const sameOriginCall = historyCalls.find(
                (c) => c.url && c.url.includes('/dashboard/'),
            );
            expect(sameOriginCall).toBeDefined();
        });
    });

    describe('issue #1934 — live_redirect to a non-LiveView path falls back to full-page nav', () => {
        // Bug: with auto_navigate default-on (v1.1), a live_redirect to a path
        // that is NOT a LiveView (e.g. a plain Django TemplateView) stranded the
        // page — the OLD code pushState'd the URL BEFORE the resolveViewPath
        // check, and the SPA-mount block had no full-nav fallback for a falsy
        // resolution. URL bar moved; DOM stayed on the previous LiveView.
        //
        // Fix: resolve the view FIRST; pushState is deferred into the
        // LiveView-resolved + WS-connected branch. A non-LiveView target (falsy
        // resolveViewPath) — or a disconnected WS — does a full-page navigation
        // via safeNavigationTarget, so the URL never leads the DOM.

        it('non-LiveView target: full-page nav, NO pushState, NO WS mount (strand-free)', () => {
            // /onboarding/ is NOT in the route map → resolveViewPath is falsy
            // (mirrors _walk_liveview_routes excluding non-LiveViews).
            const sent = [];
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: (m) => sent.push(m) };
            const { document, historyCalls, safeNav } = createNavSourceEnv(
                '<div dj-view="jira_manager.views.TicketListView"></div>',
                wsMock,
                { routeMap: { '/jira/': 'jira_manager.views.TicketListView' }, wsConnected: true },
            );

            try {
                // handleLiveRedirect is internal; drive it via the public
                // handleNavigation entry (type:'live_redirect' → handleLiveRedirect).
                document.defaultView.djust.navigation.handleNavigation({ type: 'live_redirect', path: '/onboarding/' });
            } catch (_e) {
                // JSDOM throws "Not implemented: navigation to another Document"
                // on window.location.href assignment — that throw IS the full nav.
            }

            // 1) Full-page navigation was chosen: safeNavigationTarget was asked
            //    to validate the non-LiveView target.
            expect(safeNav).toHaveBeenCalledWith('http://localhost:8000/onboarding/');
            // 2) NO history change for the non-LiveView target (the URL must NOT
            //    lead the DOM — this is the strand the bug produced).
            const stranded = historyCalls.find((c) => c.url && c.url.includes('/onboarding/'));
            expect(stranded).toBeUndefined();
            // 3) NO SPA mount frame went out (the old view is not "re-mounted"
            //    under a changed URL).
            expect(sent.some((m) => m.type === 'live_redirect_mount')).toBe(false);
        });

        it('positive case: a LiveView target still SPA-mounts (pushState + WS mount, no full nav)', () => {
            const sent = [];
            const wsMock = { viewMounted: true, ws: { readyState: 1 }, sendMessage: (m) => sent.push(m) };
            const { document, historyCalls, safeNav } = createNavSourceEnv(
                '<div dj-view="myapp.views.HomeView"></div>',
                wsMock,
                {
                    routeMap: {
                        '/home/': 'myapp.views.HomeView',
                        '/dashboard/': 'myapp.views.DashboardView',
                    },
                    wsConnected: true,
                },
            );

            document.defaultView.djust.navigation.handleNavigation({ type: 'live_redirect', path: '/dashboard/' });

            // pushState for the new LiveView path + the live_redirect_mount frame.
            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].method).toBe('pushState');
            expect(historyCalls[0].url).toContain('/dashboard/');
            expect(sent.some((m) => m.type === 'live_redirect_mount' && m.view === 'myapp.views.DashboardView')).toBe(true);
            // The SPA branch must NOT validate a full-nav target.
            expect(safeNav).not.toHaveBeenCalled();
        });

        it('LiveView target but WS not connected: full-page nav (no strand), NO pushState', () => {
            const sent = [];
            // ws not OPEN — isWSConnected() stubbed false.
            const wsMock = { viewMounted: false, ws: { readyState: 3 }, sendMessage: (m) => sent.push(m) };
            const { document, historyCalls, safeNav } = createNavSourceEnv(
                '<div dj-view="myapp.views.HomeView"></div>',
                wsMock,
                { routeMap: { '/dashboard/': 'myapp.views.DashboardView' }, wsConnected: false },
            );

            try {
                document.defaultView.djust.navigation.handleNavigation({ type: 'live_redirect', path: '/dashboard/' });
            } catch (_e) { /* href set throws in JSDOM */ }

            // Even though /dashboard/ IS a LiveView, with no WS we must do a real
            // navigation rather than pushState-then-nothing (strand).
            expect(safeNav).toHaveBeenCalledWith('http://localhost:8000/dashboard/');
            const stranded = historyCalls.find((c) => c.url && c.url.includes('/dashboard/'));
            expect(stranded).toBeUndefined();
            expect(sent.some((m) => m.type === 'live_redirect_mount')).toBe(false);
        });
    });
});
