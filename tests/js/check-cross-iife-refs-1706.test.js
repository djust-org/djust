/**
 * Regression test for #1706 — whole-class static guard against bare
 * cross-IIFE function references at `scripts/check-cross-iife-refs.mjs`.
 *
 * Background: the "minified client crashes on a cross-IIFE symbol" class
 * recurred 3× (#1676 keep-fnames → #1688/#1690 bare applyPatches → #1689
 * dup). Each fix was per-symbol. This check retires the CLASS: it flags any
 * bare reference to a djust-published function (`globalThis.djust.X = X`)
 * declared inside the double-load-guard `else {}` block (block-scoped,
 * modules 00–20) but referenced from a module at bundle top level (modules
 * 22–51, OUTSIDE the block). Such a reference is out of scope even
 * unminified and throws ReferenceError under terser-minified bundles.
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
});
