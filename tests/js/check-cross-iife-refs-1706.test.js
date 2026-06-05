/**
 * Regression test for #1706 + #1716 — whole-class static guard against bare
 * cross-IIFE function references at `scripts/check-cross-iife-refs.mjs`.
 *
 * Background: the "minified client crashes on a cross-IIFE symbol" class
 * recurred 3× (#1676 keep-fnames → #1688/#1690 bare applyPatches → #1689
 * dup). Each fix was per-symbol. This check retires the CLASS by modeling
 * the bundle's lexical scope barriers and flagging any bare reference to a
 * djust-published function (`globalThis.djust.X = X`) that falls OUTSIDE its
 * declaration's barrier scope. Two dangerous shapes are covered:
 *   - GUARD-BLOCK → TOP-LEVEL (#1706): X declared inside the double-load-
 *     guard `else {}` block (modules 00–20, block-scoped) but referenced
 *     bare from bundle top level.
 *   - TOP-LEVEL → TOP-LEVEL (#1716): X declared inside module A's own inner
 *     IIFE (modules 22–51) but referenced bare from a DIFFERENT top-level
 *     module's IIFE. 10 of 58 published functions are top-level-declared and
 *     were gap-exposed before #1716 generalized the scope test.
 * Either shape is out of scope even unminified and throws ReferenceError
 * under terser-minified bundles.
 *
 * This test pins:
 *   1. Zero false positives on the real current bundle.
 *   2. Catches the #1688 shape on a synthetic guard-structured bundle
 *      (top-level module → bare ref → guard-block published function).
 *   3. The empirical canary (#252): the exact pre-#1688 `applyPatches`
 *      shape is flagged naming the symbol + declaring module.
 *   4. `globalThis.djust.X` / `djust.X` MEMBER ACCESS is NOT flagged
 *      (the correct, minification-independent form).
 *   5. Intra-guard cross-module references are NOT flagged (false-positive
 *      control — modules inside the guard block share scope; the bulk of
 *      legit cross-module calls live here).
 *   6. (#1716) The TOP-LEVEL → TOP-LEVEL empirical canary is flagged, while
 *      member access, intra-IIFE refs, local re-binds, and PROGRAM-SCOPE
 *      declarations (the `maybeDeferRemoval` shape — a true global) are NOT
 *      flagged (the load-bearing false-positive control for #1716).
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const SCRIPT = path.resolve('./scripts/check-cross-iife-refs.mjs');

function runCheck({ srcDir = null } = {}) {
    const env = { ...process.env };
    if (srcDir) env.BUNDLE_SRC_DIR = srcDir;
    try {
        const stdout = execFileSync('node', [SCRIPT], {
            env,
            encoding: 'utf8',
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        return { code: 0, stdout, stderr: '' };
    } catch (e) {
        return {
            code: e.status ?? -1,
            stdout: e.stdout ? e.stdout.toString() : '',
            stderr: e.stderr ? e.stderr.toString() : '',
        };
    }
}

function withTempSrcDir(files) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cross-iife-1706-'));
    for (const [name, content] of Object.entries(files)) {
        fs.writeFileSync(path.join(dir, name), content);
    }
    return dir;
}

// A synthetic bundle that mirrors the real double-load-guard structure:
//   00-open.js     opens `if (window._djustClientLoaded) {} else {`
//   10-decl.js     declares + publishes `doThing` INSIDE the guard block
//   21-close.js    closes the guard `else {}` block with `}`
//   30-ref-*.js    sits at bundle top level (after the guard close)
// The reference module's content is supplied per-test.
function guardBundle(refFileName, refBody) {
    return {
        '00-open.js':
            'window.djust = window.djust || {};\n' +
            'if (window._djustClientLoaded) {\n' +
            '} else {\n',
        '10-decl.js':
            'function doThing(x) { return x; }\n' +
            'window.djust.doThing = doThing;\n',
        '21-close.js':
            '} // end double-load guard\n',
        [refFileName]: refBody,
    };
}

describe('check-cross-iife-refs #1706 — bare cross-IIFE reference guard', () => {
    let tempDirs = [];
    beforeEach(() => { tempDirs = []; });
    afterEach(() => {
        for (const d of tempDirs) {
            try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ }
        }
    });

    function mkTempSrc(files) {
        const d = withTempSrcDir(files);
        tempDirs.push(d);
        return d;
    }

    it('real bundle: zero false positives', () => {
        const res = runCheck();
        if (res.code !== 0) {
            // Surface stderr so any future regression is obvious.
            // eslint-disable-next-line no-console
            console.error(res.stderr);
        }
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    it('flags a bare top-level reference to a guard-block published function (#1688 shape)', () => {
        const src = mkTempSrc(guardBundle(
            '30-ref.js',
            '(function () {\n' +
            '    if (typeof doThing === "function") { doThing(1); }\n' +
            '})();\n',
        ));
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(1);
        expect(res.stderr).toMatch(/BARE-CROSS-IIFE-REF: doThing/);
        expect(res.stderr).toMatch(/30-ref\.js/);
        expect(res.stderr).toMatch(/10-decl\.js/);
    });

    it('empirical canary (#252): the exact pre-#1688 applyPatches shape is flagged', () => {
        // Mirror the real pre-#1688 site verbatim:
        //   if (typeof applyPatches === "function" && !djust._applyPatches) {
        //       djust._applyPatches = applyPatches;
        //   }
        const files = {
            '00-open.js':
                'window.djust = window.djust || {};\n' +
                'if (window._djustClientLoaded) {\n} else {\n',
            '12-vdom-patch.js':
                'async function applyPatches(p, r) { return [p, r]; }\n' +
                'window.djust.applyPatches = applyPatches;\n',
            '21-close.js': '}\n',
            '45-child-view.js':
                '(function () {\n' +
                '    const djust = window.djust;\n' +
                '    if (typeof applyPatches === "function" && !djust._applyPatches) {\n' +
                '        djust._applyPatches = applyPatches;\n' +
                '    }\n' +
                '})();\n',
        };
        const src = mkTempSrc(files);
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(1);
        expect(res.stderr).toMatch(/BARE-CROSS-IIFE-REF: applyPatches/);
        expect(res.stderr).toMatch(/45-child-view\.js/);
        expect(res.stderr).toMatch(/published via globalThis\.djust\.applyPatches/);
    });

    it('does NOT flag member access globalThis.djust.X / djust.X (the correct form)', () => {
        const src = mkTempSrc(guardBundle(
            '30-ref.js',
            '(function () {\n' +
            '    const fn = globalThis.djust && globalThis.djust.doThing;\n' +
            '    if (typeof fn === "function") { fn(1); }\n' +
            '    if (typeof window.djust.doThing === "function") { window.djust.doThing(2); }\n' +
            '})();\n',
        ));
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    it('does NOT flag intra-guard cross-module references (false-positive control)', () => {
        // Both the declaration AND the reference live INSIDE the guard block
        // (shared block scope) — this is the bulk of legitimate cross-module
        // calls and must never be flagged.
        const files = {
            '00-open.js':
                'window.djust = window.djust || {};\n' +
                'if (window._djustClientLoaded) {\n} else {\n',
            '10-decl.js':
                'function doThing(x) { return x; }\n' +
                'window.djust.doThing = doThing;\n',
            // Reference is ALSO inside the guard block (before the close).
            '11-user.js':
                'function useIt() { return doThing(1); }\n',
            '21-close.js': '}\n',
        };
        const src = mkTempSrc(files);
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    it('does NOT flag a bare reference whose symbol is locally re-bound', () => {
        const src = mkTempSrc(guardBundle(
            '30-ref.js',
            '(function () {\n' +
            '    function doThing(x) { return x + 1; }\n' +  // shadows the published name
            '    return doThing(1);\n' +
            '})();\n',
        ));
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    // ---------------------------------------------------------------------
    // #1716 — TOP-LEVEL ↔ TOP-LEVEL cross-IIFE gap.
    //
    // Modules 22–51 each sit at bundle top level but wrap their body in
    // their OWN inner IIFE. A function declared inside module 22's IIFE and
    // published via `globalThis.djust.X = X` is NOT visible as a bare name
    // inside module 23's IIFE — exactly the #1676/#1688 ReferenceError class,
    // just between two top-level modules instead of guard-block→top-level.
    // 10 of 58 published functions are top-level-declared and were
    // gap-exposed before this fix. The pre-#1716 narrow scope test only
    // flagged `decl.inGuard && !refInGuard`, so it exited 0 on this shape.
    // ---------------------------------------------------------------------

    // A two-module top-level bundle. Each module wraps its body in its own
    // IIFE (mirrors the real 22–51 shape). The publisher declares + exports
    // `foo` INSIDE its IIFE; the referrer's body is supplied per-test.
    function topLevelBundle(refBody) {
        return {
            '00-open.js':
                'window.djust = window.djust || {};\n' +
                'if (window._djustClientLoaded) {\n} else {\n',
            '21-close.js': '}\n',
            '22-foo.js':
                '(function () {\n' +
                '    function foo(x) { return x; }\n' +
                '    globalThis.djust.foo = foo;\n' +
                '})();\n',
            '23-bar.js': refBody,
        };
    }

    it('empirical canary (#1716): flags a bare top-level-to-top-level cross-IIFE ref', () => {
        // 23-bar.js references `foo` BARE (not `djust.foo`) from inside its
        // own IIFE — foo lives in 22-foo.js's IIFE and is invisible here.
        // Pre-#1716 the check exited 0 (the documented gap); post-fix it
        // must exit 1 and name the symbol + declaring module.
        const src = mkTempSrc(topLevelBundle(
            '(function () {\n' +
            '    if (typeof foo === "function") { foo(1); }\n' +
            '})();\n',
        ));
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(1);
        expect(res.stderr).toMatch(/BARE-CROSS-IIFE-REF: foo/);
        expect(res.stderr).toMatch(/23-bar\.js/);
        expect(res.stderr).toMatch(/22-foo\.js/);
        expect(res.stderr).toMatch(/published via globalThis\.djust\.foo/);
    });

    it('does NOT flag the correct member-access form across two top-level modules', () => {
        const src = mkTempSrc(topLevelBundle(
            '(function () {\n' +
            '    const fn = globalThis.djust && globalThis.djust.foo;\n' +
            '    if (typeof fn === "function") { fn(1); }\n' +
            '})();\n',
        ));
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    it('does NOT flag an intra-module reference within a top-level IIFE', () => {
        // foo declared AND referenced inside the SAME top-level IIFE — in
        // scope, never a bug.
        const files = {
            '00-open.js':
                'window.djust = window.djust || {};\n' +
                'if (window._djustClientLoaded) {\n} else {\n',
            '21-close.js': '}\n',
            '22-foo.js':
                '(function () {\n' +
                '    function foo(x) { return x; }\n' +
                '    function useFoo() { return foo(1); }\n' +
                '    globalThis.djust.foo = foo;\n' +
                '    useFoo();\n' +
                '})();\n',
        };
        const src = mkTempSrc(files);
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    it('does NOT flag a bare ref to a PROGRAM-SCOPE top-level function (maybeDeferRemoval shape)', () => {
        // `42-dj-remove.js` declares `maybeDeferRemoval` at PROGRAM scope
        // (NOT inside an IIFE) and publishes it. A program-scope function
        // declaration is a true global — visible from every sibling module,
        // even minified. A bare reference to it is NOT a bug and must not be
        // flagged. This is the load-bearing false-positive control for the
        // #1716 generalization: a naive "different module ⇒ bug" rule would
        // false-positive here.
        const files = {
            '00-open.js':
                'window.djust = window.djust || {};\n' +
                'if (window._djustClientLoaded) {\n} else {\n',
            '21-close.js': '}\n',
            // Program-scope declaration (no enclosing IIFE).
            '42-prog.js':
                'function progFn(el) { return el; }\n' +
                'globalThis.djust.progFn = progFn;\n',
            // Bare reference from another top-level module's IIFE.
            '43-ref.js':
                '(function () {\n' +
                '    if (typeof progFn === "function") { progFn(1); }\n' +
                '})();\n',
        };
        const src = mkTempSrc(files);
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });

    it('does NOT flag a bare ref to a published function when locally re-bound (top-level)', () => {
        const src = mkTempSrc(topLevelBundle(
            '(function () {\n' +
            '    function foo(x) { return x + 1; }\n' +  // shadows the published name
            '    return foo(1);\n' +
            '})();\n',
        ));
        const res = runCheck({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: cross-IIFE bare-reference check clean/);
    });
});
