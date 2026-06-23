/**
 * #1688 — `Uncaught ReferenceError: applyPatches is not defined` in
 * client.min.js (production-only), a recurrence of the #1676 terser-mangle ×
 * IIFE class with a different manifestation.
 *
 * ROOT CAUSE: `45-child-view.js` referenced the BARE symbol `applyPatches` at
 * two sites (`_applyScopedPatches` and the `djust._applyPatches` expose block).
 * `applyPatches` is declared inside `12-vdom-patch.js`'s own inner IIFE and is
 * published only as `globalThis.djust.applyPatches` (`12-vdom-patch.js:2299`).
 * The bundle is a single outer IIFE but each module has its own inner IIFE, so
 * the bare reference is out of module 45's scope. Unminified, the `typeof`
 * guard returns "undefined" and silently no-ops (leaving `djust._applyPatches`
 * UNWIRED, so `emitChildMountedEvents` never runs); minified, terser's
 * transform of the guard lets the bare reference evaluate and throw.
 *
 * FIX: read the published alias `globalThis.djust.applyPatches` instead of the
 * bare symbol at both sites — minification-independent.
 *
 * WHY THIS TEST IS BEHAVIORAL AND NON-TAUTOLOGICAL: like #1676, the minified
 * crash does not reproduce under jsdom/Node V8 (`typeof` on an out-of-scope
 * identifier is safe there). But the OTHER observable symptom DOES reproduce in
 * jsdom: on the broken code `globalThis.djust._applyPatches` stays `undefined`
 * because the expose block's bare-`applyPatches` guard fails. This test asserts
 * the expose block wires `djust._applyPatches` to the real applier. Gate-off
 * (revert the 45-child-view.js fix → bare symbol → guard fails) leaves
 * `_applyPatches` undefined and this test fails. Verified against the pre-fix
 * bundle: `djust._applyPatches` was `undefined`.
 */

import { describe, it, expect } from 'vitest';
import { createDom } from './_helpers.js';

describe('#1688 — child-view module wires _applyPatches via the published alias', () => {
    it('exposes globalThis.djust.applyPatches as a function (module 12)', () => {
        const dom = createDom('<div dj-view data-djust-embedded="c"></div>');
        expect(typeof dom.window.djust.applyPatches).toBe('function');
    });

    it('wires djust._applyPatches from the alias (gate-off → undefined → fails)', () => {
        const dom = createDom('<div dj-view data-djust-embedded="c"></div>');
        const w = dom.window;
        // The expose block in 45-child-view.js must have run during bundle load
        // and wired the stable alias. On the pre-fix bundle this was undefined.
        expect(typeof w.djust._applyPatches).toBe('function');
        expect(w.djust._applyPatches).toBe(w.djust.applyPatches);
    });

    it('source uses the published alias, not a bare cross-IIFE symbol', async () => {
        const fs = await import('fs');
        const src = fs.readFileSync(
            './python/djust/static/djust/src/45-child-view.js', 'utf-8');
        // The expose + scoped-applier sites must read globalThis.djust.applyPatches.
        expect(src).toMatch(/globalThis\.djust\s*&&\s*globalThis\.djust\.applyPatches/);
        // And must NOT reintroduce a bare `typeof applyPatches` guard (the bug shape).
        expect(src).not.toMatch(/typeof\s+applyPatches\s*[!=]==/);
    });
});
