/**
 * Tests for djust.apiPrefix / djust.apiUrl (#987).
 *
 * Covers the client-side half of FORCE_SCRIPT_NAME / sub-path-mount
 * support. The server-side half is the ``{% djust_client_config %}``
 * template tag (``python/djust/tests/test_client_config_tag.py``).
 *
 * Each doc claim from ``docs/website/guides/server-functions.md`` and
 * ``docs/website/guides/http-api.md`` "Sub-path deploys" has a matching
 * test here (Action Tracker #124 / #125):
 *
 *   - "Client falls back to '/djust/api/' when no meta tag" →
 *     test_default_prefix_when_no_meta
 *   - "Meta tag content overrides the default" →
 *     test_meta_tag_overrides_default
 *   - "Explicit window.djust.apiPrefix takes priority over meta" →
 *     test_explicit_global_override_wins
 *   - "djust.apiUrl(path) joins prefix + path" →
 *     test_apiUrl_joins_correctly
 *   - "Leading slash on path is normalized" →
 *     test_apiUrl_normalizes_leading_slash
 *   - "Missing trailing slash on prefix is normalized" →
 *     test_apiUrl_normalizes_trailing_slash_on_prefix
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * Build a fresh JSDOM with client.js evaluated. Options:
 *   - meta: set <meta name="djust-api-prefix" content="...">.
 *   - preInit: a function called inside the window BEFORE client.js
 *              evaluates — use to set window.djust.apiPrefix for the
 *              explicit-override test.
 */
function createEnv(opts = {}) {
    const head = opts.meta
        ? `<meta name="djust-api-prefix" content="${opts.meta}">`
        : '';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${head}</head><body></body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    window.history.pushState = () => {};
    window.history.replaceState = () => {};

    if (typeof opts.preInit === 'function') {
        opts.preInit(window);
    }

    try { window.eval(clientCode); } catch (_) { /* non-fatal DOM APIs missing */ }
    return { window };
}

describe('djust.apiPrefix — bootstrap-time resolution', () => {
    it('test_default_prefix_when_no_meta: falls back to /djust/api/ when no meta tag', () => {
        const { window } = createEnv();
        expect(window.djust.apiPrefix).toBe('/djust/api/');
    });

    it('test_meta_tag_overrides_default: reads meta[name="djust-api-prefix"]', () => {
        const { window } = createEnv({ meta: '/mysite/djust/api/' });
        expect(window.djust.apiPrefix).toBe('/mysite/djust/api/');
    });

    it('test_explicit_global_override_wins: window.djust.apiPrefix set before init takes priority', () => {
        const { window } = createEnv({
            meta: '/from-meta/',
            preInit: (w) => {
                w.djust = w.djust || {};
                w.djust.apiPrefix = '/custom-override/';
            },
        });
        expect(window.djust.apiPrefix).toBe('/custom-override/');
    });
});

describe('djust.apiUrl — prefix + path joiner', () => {
    it('test_apiUrl_joins_correctly: concatenates default prefix + relative path', () => {
        const { window } = createEnv();
        expect(window.djust.apiUrl('call/v/f/')).toBe('/djust/api/call/v/f/');
    });

    it('test_apiUrl_normalizes_leading_slash: leading "/" on path is stripped', () => {
        // Otherwise '/djust/api/' + '/call/v/f/' → '/djust/api//call/v/f/'
        // (double slash), which some reverse-proxies mangle.
        const { window } = createEnv();
        expect(window.djust.apiUrl('/call/v/f/')).toBe('/djust/api/call/v/f/');
    });

    it('test_apiUrl_normalizes_trailing_slash_on_prefix: missing "/" on prefix is added', () => {
        const { window } = createEnv({ meta: '/api' });
        expect(window.djust.apiUrl('x')).toBe('/api/x');
    });
});
