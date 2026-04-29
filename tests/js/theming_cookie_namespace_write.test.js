/**
 * Regression tests for #1169 sub-items (c) and (d) — namespaced cookie
 * WRITE-side semantics in `python/djust/theming/static/djust_theming/js/theme.js`.
 *
 * Background
 * ----------
 * #1158 added per-project cookie namespacing for theming cookies. The Python
 * tests in `tests/unit/test_theming_cookie_namespace_1158.py` cover the
 * server-side READ. Sub-item (c) of #1169 was that the WRITE side was only
 * verified via string-source assertions ("the source contains
 * `COOKIE_PREFIX + 'djust_theme'`"), not by actually executing the JS in a
 * DOM and inspecting `document.cookie`.
 *
 * These tests load `theme.js` inside a JSDOM, set
 * `window.__djust_theme_cookie_prefix` (the global the inline anti-FOUC
 * script populates from `LIVEVIEW_CONFIG['theme']['cookie_namespace']`),
 * trigger each write entry-point, and assert the cookie jar contains the
 * namespaced name and NOT the unprefixed legacy name.
 *
 * Sub-item (d): the write path also fires `Max-Age=0` on the legacy
 * unprefixed cookie name when a namespace is configured, so old cookies
 * left over from before the namespace was set don't sit in the jar
 * forever.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import path from 'path';

const THEME_JS_PATH = './python/djust/theming/static/djust_theming/js/theme.js';

// Cache the source so we don't re-read on every test.
let _themeJsCache = null;
function getThemeJs() {
    if (_themeJsCache == null) {
        _themeJsCache = fs.readFileSync(THEME_JS_PATH, 'utf-8');
    }
    return _themeJsCache;
}

/**
 * Build a JSDOM with theme.js loaded.
 *
 * @param {string|null} cookiePrefix — value of `window.__djust_theme_cookie_prefix`
 *   to set BEFORE evaluating theme.js. Pass `null` to leave the global unset
 *   (back-compat path: COOKIE_PREFIX defaults to empty string).
 */
function createThemingDom(cookiePrefix) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body></body></html>`,
        { runScripts: 'outside-only', url: 'http://localhost/', pretendToBeVisual: true }
    );
    if (cookiePrefix !== null) {
        dom.window.__djust_theme_cookie_prefix = cookiePrefix;
    }
    // matchMedia stub — theme.js calls it during init.
    dom.window.matchMedia = () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
    });
    // requestAnimationFrame is provided by JSDOM but we want it synchronous
    // for predictable test ordering.
    dom.window.requestAnimationFrame = (cb) => { cb(0); return 1; };
    dom.window.cancelAnimationFrame = () => {};
    // Stub reload so setPreset/setTheme/setPack don't throw on
    // window.location.reload() (JSDOM disallows navigation by default).
    // The location object is non-configurable, so we patch reload directly.
    try {
        dom.window.location.reload = () => {};
    } catch (_) {
        // If JSDOM tightens this in future, fall back to a Proxy-style override.
    }
    dom.window.eval(getThemeJs());
    return dom;
}

/**
 * Parse `document.cookie` into a `{name: value}` map. The browser's cookie
 * jar joins cookies with `'; '`; deleted cookies (Max-Age=0) are simply
 * absent from the resulting string.
 */
function parseCookies(cookieStr) {
    const out = {};
    if (!cookieStr) return out;
    cookieStr.split(';').forEach((pair) => {
        const idx = pair.indexOf('=');
        if (idx < 0) return;
        const name = pair.slice(0, idx).trim();
        const value = pair.slice(idx + 1).trim();
        if (name) out[name] = value;
    });
    return out;
}

describe('#1169(c) — namespaced cookie WRITE side', () => {
    it('setPack writes the namespaced cookie name when prefix is set', () => {
        const dom = createThemingDom('myapp_');
        const tm = dom.window.djustTheme;
        tm.setPack('nyc_core');
        const jar = parseCookies(dom.window.document.cookie);
        expect(jar['myapp_djust_theme_pack']).toBe('nyc_core');
        // Legacy unprefixed name must NOT carry the value (sub-item d ensures
        // it's deleted, not overwritten with the new value).
        expect(jar['djust_theme_pack']).toBeUndefined();
    });

    it('setPreset writes the namespaced cookie name when prefix is set', () => {
        const dom = createThemingDom('myapp_');
        const tm = dom.window.djustTheme;
        tm.setPreset('rose');
        const jar = parseCookies(dom.window.document.cookie);
        expect(jar['myapp_djust_theme_preset']).toBe('rose');
        expect(jar['djust_theme_preset']).toBeUndefined();
    });

    it('setLayout writes the namespaced cookie name when prefix is set', () => {
        const dom = createThemingDom('myapp_');
        const tm = dom.window.djustTheme;
        tm.setLayout('sidebar');
        const jar = parseCookies(dom.window.document.cookie);
        expect(jar['myapp_djust_theme_layout']).toBe('sidebar');
        expect(jar['djust_theme_layout']).toBeUndefined();
    });

    it('back-compat: empty prefix writes the legacy unprefixed cookie name', () => {
        // window.__djust_theme_cookie_prefix unset (default), or set to "".
        const dom = createThemingDom(null);
        const tm = dom.window.djustTheme;
        tm.setPack('nyc_core');
        const jar = parseCookies(dom.window.document.cookie);
        expect(jar['djust_theme_pack']).toBe('nyc_core');
    });
});

describe('#1169(d) — legacy unprefixed cookie cleanup on namespaced write', () => {
    it('setPack deletes the legacy djust_theme_pack cookie when prefix is set', () => {
        const dom = createThemingDom('myapp_');
        // Pre-seed the legacy cookie (simulating a stale value left from
        // before the namespace config was added).
        dom.window.document.cookie = 'djust_theme_pack=old_legacy_value; path=/';
        // Sanity check the seed.
        expect(parseCookies(dom.window.document.cookie)['djust_theme_pack']).toBe('old_legacy_value');

        const tm = dom.window.djustTheme;
        tm.setPack('nyc_core');

        const jar = parseCookies(dom.window.document.cookie);
        expect(jar['myapp_djust_theme_pack']).toBe('nyc_core');
        // Legacy cookie deleted via Max-Age=0 — must NOT be in the jar.
        expect(jar['djust_theme_pack']).toBeUndefined();
    });

    it('setPreset deletes the legacy djust_theme_preset cookie when prefix is set', () => {
        const dom = createThemingDom('myapp_');
        dom.window.document.cookie = 'djust_theme_preset=stale_legacy; path=/';
        expect(parseCookies(dom.window.document.cookie)['djust_theme_preset']).toBe('stale_legacy');

        const tm = dom.window.djustTheme;
        tm.setPreset('rose');

        const jar = parseCookies(dom.window.document.cookie);
        expect(jar['myapp_djust_theme_preset']).toBe('rose');
        expect(jar['djust_theme_preset']).toBeUndefined();
    });

    it('legacy cleanup does NOT fire when prefix is empty (back-compat)', () => {
        // With no namespace, the "namespaced" name and the "legacy" name
        // are identical. The cleanup must be inert in this configuration —
        // otherwise it would Max-Age=0 the cookie we just wrote.
        const dom = createThemingDom('');
        const tm = dom.window.djustTheme;
        tm.setPack('nyc_core');

        const jar = parseCookies(dom.window.document.cookie);
        // The single (unprefixed) cookie must still hold the value.
        expect(jar['djust_theme_pack']).toBe('nyc_core');
    });
});
