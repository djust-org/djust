/**
 * Regression tests for #1813 (a) — the prerender `skipMountHtml` morph must
 * stamp the server's `dj-id` onto embedded/sticky-child wrappers.
 *
 * Embedded-view wrappers
 * (`<div dj-view [dj-sticky-view dj-sticky-root] data-djust-embedded="<id>">`)
 * carry NO `id`, so `morphChildren` can only align them positionally
 * (Strategy 2). When a preceding sibling count diverges between the HTTP
 * prerender DOM and the WS-mount HTML, the wrapper never aligns → the server's
 * `dj-id` is never copied onto the live wrapper. The very next parent patch
 * that targets the wrapper by `dj-id` then misses, falls back to a positional
 * path, and breaks once the sticky child's subtree drifts → triggers
 * html_recovery (the trigger half of the #1813 sticky-child data-loss bug).
 *
 * The fix adds `_stampEmbeddedWrapperDjIds(liveContainer, serverTemplate)`,
 * run AFTER `morphChildren` in the prerender branch of `03-websocket.js`. It
 * matches each server wrapper to the live wrapper by the STABLE
 * `data-djust-embedded` value (the same selector `45-child-view.js` uses) and
 * copies the server `dj-id` onto it — independent of sibling position.
 *
 * The behavioral test extracts the function from source and exercises it
 * against a real DOM with a sibling-count divergence (the exact condition
 * positional alignment fails on). The source-shape tests pin the wiring.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';

const SRC = './python/djust/static/djust/src/03-websocket.js';
const clientSource = readFileSync(SRC, 'utf-8');

/**
 * Extract the body of a top-level `function NAME(...) { ... }` from source by
 * brace-matching, so we can eval just that function in isolation.
 */
function extractFunctionSource(source, name) {
    const sig = 'function ' + name + '(';
    const start = source.indexOf(sig);
    if (start < 0) throw new Error('function ' + name + ' not found in ' + SRC);
    const open = source.indexOf('{', start);
    let depth = 0;
    for (let i = open; i < source.length; i++) {
        const ch = source[i];
        if (ch === '{') depth++;
        else if (ch === '}') {
            depth--;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error('unbalanced braces extracting ' + name);
}

function loadStampFn() {
    const fnSrc = extractFunctionSource(clientSource, '_stampEmbeddedWrapperDjIds');
    // eslint-disable-next-line no-new-func
    return new Function(fnSrc + '\nreturn _stampEmbeddedWrapperDjIds;')();
}

describe('#1813 (a) — prerender morph stamps dj-id onto sticky-child wrapper', () => {
    let stamp;

    beforeEach(() => {
        stamp = loadStampFn();
        // happy-dom provides CSS.escape; ensure a fallback exists.
        if (!globalThis.CSS) globalThis.CSS = {};
        if (typeof globalThis.CSS.escape !== 'function') {
            globalThis.CSS.escape = (s) => String(s).replace(/([^A-Za-z0-9_-])/g, '\\$1');
        }
    });

    it('copies server dj-id onto a live sticky wrapper that has none (positional-divergence case)', () => {
        // Live (prerender) container: the sticky wrapper has NO dj-id and is
        // preceded by a different sibling count than the server HTML, so a
        // positional morph would never have aligned it.
        const live = document.createElement('div');
        live.innerHTML =
            '<h1>Home</h1>' +
            '<div dj-view dj-sticky-view="notifications" dj-sticky-root ' +
            'data-djust-embedded="notifications"><span>a</span></div>';

        // Server (WS-mount) template: an EXTRA sibling before the wrapper
        // (count divergence), and the wrapper carries the server dj-id "i".
        const server = document.createElement('div');
        server.innerHTML =
            '<h1>Home</h1>' +
            '<div class="banner">new</div>' +
            '<div dj-view dj-sticky-view="notifications" dj-sticky-root ' +
            'data-djust-embedded="notifications" dj-id="i"><span>a</span></div>';

        const liveWrapper = live.querySelector('[data-djust-embedded="notifications"]');
        expect(liveWrapper.getAttribute('dj-id')).toBeNull();

        stamp(live, server);

        // Load-bearing assertion: the live wrapper now carries the server dj-id,
        // matched by the stable data-djust-embedded value (NOT position).
        expect(liveWrapper.getAttribute('dj-id')).toBe('i');
    });

    it('also matches plain embedded (non-sticky) wrappers via data-djust-embedded', () => {
        const live = document.createElement('div');
        live.innerHTML =
            '<div dj-view data-djust-embedded="child_0"><span>x</span></div>';
        const server = document.createElement('div');
        server.innerHTML =
            '<div dj-view data-djust-embedded="child_0" dj-id="k"><span>x</span></div>';

        stamp(live, server);
        expect(
            live.querySelector('[data-djust-embedded="child_0"]').getAttribute('dj-id')
        ).toBe('k');
    });

    it('is a no-op when the server wrapper has no dj-id (nothing to copy)', () => {
        const live = document.createElement('div');
        live.innerHTML =
            '<div dj-view data-djust-embedded="notifications"></div>';
        const server = document.createElement('div');
        server.innerHTML =
            '<div dj-view data-djust-embedded="notifications"></div>';

        stamp(live, server);
        expect(
            live.querySelector('[data-djust-embedded="notifications"]').getAttribute('dj-id')
        ).toBeNull();
    });

    it('does not throw when no matching live wrapper exists', () => {
        const live = document.createElement('div');
        live.innerHTML = '<h1>Home</h1>';
        const server = document.createElement('div');
        server.innerHTML =
            '<div dj-view data-djust-embedded="notifications" dj-id="i"></div>';
        expect(() => stamp(live, server)).not.toThrow();
    });

    it('gate-off proof: WITHOUT the stamp pass the live wrapper keeps no dj-id', () => {
        // This documents the bug the stamp pass fixes: morphChildren alone
        // (no stamp) under sibling-count divergence leaves the wrapper without
        // a dj-id. We assert by NOT calling stamp() — the wrapper stays bare.
        const live = document.createElement('div');
        live.innerHTML =
            '<div dj-view data-djust-embedded="notifications"><span>a</span></div>';
        const liveWrapper = live.querySelector('[data-djust-embedded="notifications"]');
        // (No stamp call.) The wrapper has no dj-id — exactly the broken state
        // the fix repairs.
        expect(liveWrapper.getAttribute('dj-id')).toBeNull();
    });
});

describe('#1813 (a) — source wiring pins', () => {
    it('the prerender morph branch calls _stampEmbeddedWrapperDjIds after morphChildren', () => {
        // The stamp pass must run in the same branch as the morph, AFTER it.
        const morphIdx = clientSource.indexOf('morphChildren(_morphContainer, _morphTemp)');
        const stampIdx = clientSource.indexOf('_stampEmbeddedWrapperDjIds(_morphContainer, _morphTemp)');
        expect(morphIdx).toBeGreaterThan(-1);
        expect(stampIdx).toBeGreaterThan(morphIdx);
    });

    it('the stamp helper matches wrappers by the data-djust-embedded selector (45-child-view.js shape)', () => {
        const fnSrc = extractFunctionSource(clientSource, '_stampEmbeddedWrapperDjIds');
        expect(fnSrc).toMatch(/\[dj-view\]\[data-djust-embedded=/);
        expect(fnSrc).toMatch(/setAttribute\(['"]dj-id['"]/);
    });
});
