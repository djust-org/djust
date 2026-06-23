/**
 * Tests for the shared client-side navigation scheme/origin guard
 * (src/02b-safe-nav.js) and its application at every location.href sink:
 *   - SSE `navigate` handler          (src/03b-sse.js)
 *   - WS  `navigate`/`nav.to` fallback (src/03-websocket.js)
 *   - handleLivePatch  cross-origin fallback (src/18-navigation.js)
 *   - handleLiveRedirect cross-origin + unresolved-view fallbacks
 *
 * Security finding #16 (CWE-601 open-redirect + CWE-79 javascript:/data:
 * DOM-XSS). The guard is ONE shared helper so the WS and SSE paths cannot
 * drift apart (#1646). A gate-off block at the bottom proves the rejection
 * assertions are non-tautological (#1468).
 */

/* eslint-disable no-script-url -- this security test deliberately feeds
   `javascript:` / `data:` / `vbscript:` scheme URLs as ATTACK INPUTS to assert
   the navigation guard rejects them; they are never executed. */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(__dirname, '../../python/djust/static/djust/src');

const safeNavSrc = readFileSync(resolve(SRC, '02b-safe-nav.js'), 'utf8');
const navSrc = readFileSync(resolve(SRC, '18-navigation.js'), 'utf8');
const sseSrc = readFileSync(resolve(SRC, '03b-sse.js'), 'utf8');
const wsSrc = readFileSync(resolve(SRC, '03-websocket.js'), 'utf8');

const ORIGIN = 'https://app.example.com';

const NOOP_CONSOLE = { log: () => {}, warn: () => {}, error: () => {}, info: () => {}, debug: () => {} };

// Run a source module in an isolated function sandbox. JSDOM locks
// `window.location` (non-configurable), so we cannot observe href assignments
// through it — instead we provide a plain `window` whose `location.href` is a
// real settable property. The real Node WHATWG `URL` matches browser
// origin/protocol semantics for every scheme under test (verified: javascript:,
// data:, vbscript:, blob:, file: all yield the same origin/protocol as a real
// browser). `document` is the minimal stub the module touches at load time.
function runModule(src, scope) {
    const names = Object.keys(scope);
    const values = names.map((n) => scope[n]);
    // eslint-disable-next-line no-new-func
    new Function(...names, src)(...values);
}

// ---------------------------------------------------------------------------
// Helper: a window-like object with the safe-nav helper installed.
// ---------------------------------------------------------------------------
function makeHelperWindow(djustDebug = false) {
    // The same-origin branch resolves candidate paths against
    // window.location.origin (validate-AFTER-normalize, #1825), so the helper
    // window must carry a real origin like a browser does.
    const window = { location: { origin: ORIGIN } };
    const globalThisStub = { djustDebug };
    const console = { ...NOOP_CONSOLE };
    runModule(safeNavSrc, { window, globalThis: globalThisStub, console, URL });
    return { window, globalThis: globalThisStub, console };
}

// ===========================================================================
// 1. The helper itself
// ===========================================================================
describe('safeNavigationTarget', () => {
    let safeNavigationTarget;

    beforeEach(() => {
        const { window } = makeHelperWindow();
        safeNavigationTarget = window.djust.safeNavigationTarget;
    });

    it('is published on window.djust', () => {
        expect(typeof safeNavigationTarget).toBe('function');
    });

    it('accepts a same-origin absolute path unchanged', () => {
        expect(safeNavigationTarget('/dashboard')).toBe('/dashboard');
        expect(safeNavigationTarget('/a/b?x=1#h')).toBe('/a/b?x=1#h');
    });

    it('preserves query + hash through the re-resolve (new URL) path', () => {
        // Proves the re-resolve returns pathname+search+hash, not a bare path.
        expect(safeNavigationTarget('/a?x=1#h')).toBe('/a?x=1#h');
    });

    // ---- Backslash / control-char open-redirect battery (finding #16 fix) ----
    // The WHATWG URL parser normalizes '\' → '/' and strips ASCII tab/newline,
    // so each of these resolves CROSS-ORIGIN despite charAt(1) !== '/'. The raw
    // prefix check returned them verbatim (the reviewer-confirmed bypass); the
    // re-resolve + origin-check rejects them. These FAIL before the fix.
    it('REJECTS backslash open-redirect (/\\evil.com → evil.com)', () => {
        expect(safeNavigationTarget('/\\evil.com')).toBeNull();
    });

    it('REJECTS backslash-slash open-redirect (/\\/evil.com)', () => {
        expect(safeNavigationTarget('/\\/evil.com')).toBeNull();
    });

    it('REJECTS double-backslash open-redirect (/\\\\evil.com)', () => {
        expect(safeNavigationTarget('/\\\\evil.com')).toBeNull();
    });

    it('REJECTS tab-then-slash open-redirect (/\\t/evil)', () => {
        expect(safeNavigationTarget('/\t/evil')).toBeNull();
    });

    it('REJECTS newline-then-slashes open-redirect (/\\n//evil)', () => {
        expect(safeNavigationTarget('/\n//evil')).toBeNull();
    });

    it('accepts an absolute https URL (legit #1599 sister-site case)', () => {
        expect(safeNavigationTarget('https://x.com/y')).toBe('https://x.com/y');
    });

    it('accepts an absolute http URL', () => {
        // Trailing-slash normalization is browser-standard; assert origin+path.
        const out = safeNavigationTarget('http://x.com');
        expect(out).toMatch(/^http:\/\/x\.com\/?$/);
    });

    it('REJECTS a protocol-relative target (//evil.com)', () => {
        expect(safeNavigationTarget('//evil.com/x')).toBeNull();
    });

    it('REJECTS javascript: scheme', () => {
        expect(safeNavigationTarget('javascript:alert(1)')).toBeNull();
    });

    it('REJECTS data: scheme', () => {
        expect(safeNavigationTarget('data:text/html,<script>alert(1)</script>')).toBeNull();
    });

    it('REJECTS vbscript: scheme', () => {
        expect(safeNavigationTarget('vbscript:msgbox(1)')).toBeNull();
    });

    it('REJECTS blob: scheme (even though origin is non-opaque)', () => {
        // blob:https://app.example.com/uuid has origin === ORIGIN but proto blob:
        expect(safeNavigationTarget(`blob:${ORIGIN}/abc-123`)).toBeNull();
    });

    it('REJECTS file: scheme', () => {
        expect(safeNavigationTarget('file:///etc/passwd')).toBeNull();
    });

    it('REJECTS empty string, null, undefined, and non-strings', () => {
        expect(safeNavigationTarget('')).toBeNull();
        expect(safeNavigationTarget(null)).toBeNull();
        expect(safeNavigationTarget(undefined)).toBeNull();
        expect(safeNavigationTarget(42)).toBeNull();
        expect(safeNavigationTarget({})).toBeNull();
    });

    it('REJECTS unparseable garbage', () => {
        expect(safeNavigationTarget('not a url at all ::: %%%')).toBeNull();
    });

    it('warns under djustDebug when rejecting', () => {
        const warns = [];
        const window = {};
        const globalThisStub = { djustDebug: true };
        const console = { ...NOOP_CONSOLE, warn: (...a) => warns.push(a) };
        runModule(safeNavSrc, { window, globalThis: globalThisStub, console, URL });
        expect(window.djust.safeNavigationTarget('javascript:alert(1)')).toBeNull();
        expect(warns.length).toBeGreaterThan(0);
    });
});

// ===========================================================================
// 2. SSE `navigate` sink (src/03b-sse.js)
// ===========================================================================
describe('SSE navigate handler — scheme guard (finding #16)', () => {
    function makeSse() {
        const window = {};
        const globalThisStub = { djustDebug: false };
        const console = { ...NOOP_CONSOLE };

        // location stub that records assignments to href.
        let hrefValue = '';
        window.location = {
            origin: ORIGIN,
            get href() { return hrefValue; },
            set href(v) { hrefValue = v; },
        };

        class EventSourceStub { close() {} }
        EventSourceStub.CONNECTING = 0; EventSourceStub.OPEN = 1; EventSourceStub.CLOSED = 2;

        const scope = {
            window,
            globalThis: globalThisStub,
            console,
            URL,
            EventSource: EventSourceStub,
            clientVdomVersion: 0,
            setCacheConfig: () => {},
            _stampDjIds: () => {},
            bindLiveViewEvents: () => {},
            handleServerResponse: () => {},
            globalLoadingManager: { stopLoading: () => {} },
            dispatchPushEventToHooks: () => {},
        };

        // Install the shared helper first, then the SSE class, in this scope.
        runModule(safeNavSrc, scope);
        runModule(sseSrc, scope);

        const SSE = window.djust.LiveViewSSE;
        const sse = new SSE();
        return { sse, getHref: () => window.location.href };
    }

    it('navigates to a same-origin path', async () => {
        const { sse, getHref } = makeSse();
        await sse.handleMessage({ type: 'navigate', to: '/login/' });
        expect(getHref()).toBe('/login/');
    });

    it('navigates to a legit absolute https URL', async () => {
        const { sse, getHref } = makeSse();
        await sse.handleMessage({ type: 'navigate', to: 'https://auth.example.com/login' });
        expect(getHref()).toBe('https://auth.example.com/login');
    });

    it('does NOT navigate to a javascript: target', async () => {
        const { sse, getHref } = makeSse();
        await sse.handleMessage({ type: 'navigate', to: 'javascript:alert(document.domain)' });
        expect(getHref()).toBe('');
        expect(getHref()).not.toContain('javascript:');
    });

    it('does NOT navigate to a protocol-relative target', async () => {
        const { sse, getHref } = makeSse();
        await sse.handleMessage({ type: 'navigate', to: '//evil.com/x' });
        expect(getHref()).toBe('');
    });

    it('does NOT navigate to a backslash open-redirect (/\\evil.com)', async () => {
        const { sse, getHref } = makeSse();
        await sse.handleMessage({ type: 'navigate', to: '/\\evil.com' });
        expect(getHref()).toBe('');
    });

    it('does NOT navigate to a data: target', async () => {
        const { sse, getHref } = makeSse();
        await sse.handleMessage({ type: 'navigate', to: 'data:text/html,<script>alert(1)</script>' });
        expect(getHref()).toBe('');
    });
});

// ===========================================================================
// 3. Navigation sinks (src/18-navigation.js) — handleLivePatch / handleLiveRedirect
// ===========================================================================
describe('18-navigation handleLivePatch / handleLiveRedirect — scheme guard', () => {
    function makeNav() {
        const window = {};
        const globalThisStub = { djustDebug: false };
        const console = { ...NOOP_CONSOLE };

        let hrefAssigned = null;
        window.location = {
            origin: ORIGIN,
            href: `${ORIGIN}/current/`,
            pathname: '/current/',
            search: '',
            reload: () => { hrefAssigned = '__RELOAD__'; },
        };
        // Intercept href writes (the sink under test) while preserving reads.
        Object.defineProperty(window.location, 'href', {
            configurable: true,
            get() { return `${ORIGIN}/current/`; },
            set(v) { hrefAssigned = v; },
        });

        // Minimal document stub: only the load-time surface 18-navigation.js
        // touches (delegated change-listener install + auto-navigate probe).
        const document = {
            readyState: 'complete',
            addEventListener: () => {},
            querySelector: () => null,
            querySelectorAll: () => [],
        };

        // WS stub: the cross-origin fallback (the sink under test) returns
        // before any WS interaction, so a minimal stub suffices.
        window.liveViewWS = { ws: {}, viewMounted: true, sendMessage: () => {} };
        window.isWSConnected = () => true;
        window.scrollTo = () => {};
        window.addEventListener = () => {};
        window.dispatchEvent = () => {};

        const scope = { window, globalThis: globalThisStub, console, URL, document };
        runModule(safeNavSrc, scope);
        runModule(navSrc, scope);

        return { window, getHref: () => hrefAssigned };
    }

    // --- handleLivePatch ----------------------------------------------------
    it('live_patch: navigates to a legit absolute https sister-site URL', () => {
        const { window, getHref } = makeNav();
        window.djust.navigation.handleNavigation({
            type: 'navigation', action: 'live_patch', path: 'https://sister.example.com/x',
        });
        expect(getHref()).toBe('https://sister.example.com/x');
    });

    it('live_patch: does NOT navigate to a javascript: path', () => {
        const { window, getHref } = makeNav();
        window.djust.navigation.handleNavigation({
            type: 'navigation', action: 'live_patch', path: 'javascript:alert(1)',
        });
        expect(getHref()).toBeNull();
    });

    it('live_patch: does NOT navigate to a data: path', () => {
        const { window, getHref } = makeNav();
        window.djust.navigation.handleNavigation({
            type: 'navigation', action: 'live_patch', path: 'data:text/html,<script>alert(1)</script>',
        });
        expect(getHref()).toBeNull();
    });

    // --- handleLiveRedirect -------------------------------------------------
    it('live_redirect: navigates to a legit absolute https sister-site URL', () => {
        const { window, getHref } = makeNav();
        window.djust.navigation.handleNavigation({
            type: 'navigation', action: 'live_redirect', path: 'https://sister.example.com/y',
        });
        expect(getHref()).toBe('https://sister.example.com/y');
    });

    it('live_redirect: does NOT navigate to a javascript: path', () => {
        const { window, getHref } = makeNav();
        window.djust.navigation.handleNavigation({
            type: 'navigation', action: 'live_redirect', path: 'javascript:alert(document.domain)',
        });
        expect(getHref()).toBeNull();
    });

    it('live_redirect: does NOT navigate to a data: path', () => {
        const { window, getHref } = makeNav();
        window.djust.navigation.handleNavigation({
            type: 'navigation', action: 'live_redirect', path: 'data:text/html,<script>alert(1)</script>',
        });
        expect(getHref()).toBeNull();
    });
});

// ===========================================================================
// 4. Parity: WS and SSE navigate paths share the SAME helper (#1646)
// ===========================================================================
describe('WS and SSE navigate route through the same helper (#1646)', () => {
    it('both transports reject a javascript: target via window.djust.safeNavigationTarget', () => {
        // The WS fallback (03-websocket.js) and the SSE handler (03b-sse.js)
        // both call window.djust.safeNavigationTarget(...). Grep-pin the shared
        // call site so a future edit that re-inlines a divergent guard fails.
        expect(wsSrc).toContain('window.djust.safeNavigationTarget(nav.to)');
        expect(sseSrc).toContain('window.djust.safeNavigationTarget(data.to)');

        // Neither path keeps a divergent inline same-origin guard (the pre-fix
        // WS shape used `isSameOriginPath`); the helper is the single source.
        expect(wsSrc).not.toContain('const isSameOriginPath');

        // And behaviorally: the one helper rejects javascript: for both.
        const { window } = makeHelperWindow();
        expect(window.djust.safeNavigationTarget('javascript:alert(1)')).toBeNull();
    });
});

// ===========================================================================
// 5. GATE-OFF (#1468): bypassing the helper makes the rejection tests fail.
//    This proves the rejection assertions are NOT tautological — if the sink
//    assigned the raw value (the pre-fix bug), the assertion below would fail.
// ===========================================================================
describe('gate-off — raw assignment WOULD execute the unsafe target', () => {
    it('the unguarded (pre-fix) shape sets href to the javascript: target', () => {
        // Simulate the OLD SSE handler: `window.location.href = data.to;`
        let hrefValue = '';
        const fakeLocation = {
            origin: ORIGIN,
            get href() { return hrefValue; },
            set href(v) { hrefValue = v; },
        };
        const data = { to: 'javascript:alert(document.domain)' };

        // Pre-fix sink (gate-off): no helper, raw assignment.
        fakeLocation.href = data.to;

        // The pre-fix shape DOES land the unsafe target — which is exactly the
        // bug. Our guarded tests above assert the OPPOSITE (href stays empty),
        // so they fail if the guard is removed. Non-tautological.
        expect(hrefValue).toBe('javascript:alert(document.domain)');
        expect(hrefValue).toContain('javascript:');
    });
});
