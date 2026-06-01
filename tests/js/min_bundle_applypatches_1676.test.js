/**
 * #1676 — guard the minified bundle against terser-mangle × IIFE cross-module
 * reference breakage (`Uncaught ReferenceError: ie is not defined` in
 * client.min.js, production-only).
 *
 * ROOT CAUSE: after the #1635 single-IIFE wrap, terser `--mangle` could rename a
 * cross-module function declaration (`applyPatches` → `ie`) and relocate it so a
 * caller's `await ie(...)` no longer resolved → ReferenceError, crashing the
 * minified client (the unminified bundle resolves via the shared outer-IIFE
 * scope, so the unit suite never caught it). FIX: `--keep-fnames` in
 * `scripts/build-client.sh` — function names are preserved through mangling, so
 * no cross-module reference can be renamed out of scope. Terser-version
 * independent and covers the whole class (every cross-module function), unlike
 * namespacing individual call sites.
 *
 * REPRODUCTION NOTE: the crash is browser-runtime / terser-version specific and
 * does NOT reproduce under jsdom/Node V8 (the IIFE-hoisted mangled declaration
 * resolves at the call site there) — a behavioral jsdom test is tautological
 * (it passes on the broken bundle too; verified via gate-off). The reporter has
 * a bit-exact prod console repro. So this is a STRUCTURAL guard on the fix,
 * non-tautological: revert `--keep-fnames`, rebuild → `applyPatches` mangles to
 * `ie` and both assertions fail (verified).
 */

import { describe, it, expect } from 'vitest';
import fs from 'fs';

const MIN = fs.readFileSync('./python/djust/static/djust/client.min.js', 'utf-8');

describe('minified bundle: cross-module function names preserved (#1676)', () => {
    it('preserves the applyPatches function name (--keep-fnames active)', () => {
        // Gate-off: drop `--keep-fnames` from build-client.sh + rebuild →
        // `applyPatches` mangles to a 2-char name (`ie`) → this fails.
        expect(MIN).toMatch(/function applyPatches\(/);
    });

    it('exports applyPatches by its preserved name, not a mangled identifier', () => {
        // The export `globalThis.djust.applyPatches = applyPatches` must
        // reference the real name (so it resolves), not `=ie`.
        expect(MIN).toMatch(/\.applyPatches=applyPatches/);
    });
});
