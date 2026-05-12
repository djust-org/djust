/**
 * Regression test for #1449 — depth-N call-graph walker for the bundle
 * init-order lint at `scripts/check-bundle-init-order.mjs`.
 *
 * Background: the v0.9.5 lint caught only DIRECT top-level reads of
 * late-declared `let`/`const`. #1370 was a TRANSITIVE TDZ:
 *   djustInit() → mountHooks() → _ensureHooksInit() → _activeHooks
 * The shallow lint stayed silent. #1449 extends the lint with a
 * depth-N call-graph walker; this test pins:
 *
 *   1. Zero false positives on the current bundle.
 *   2. Catches the #1370 transitive shape on a synthetic bundle.
 *   3. Skips deferral-site callbacks (addEventListener / setTimeout /
 *      Promise.then / Observer ctors / etc.).
 *   4. `--shallow-only` reverts to v0.9.5 behavior (escape hatch).
 *   5. Terminates on cyclic call graphs.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const SCRIPT = path.resolve('./scripts/check-bundle-init-order.mjs');

function runLint({ srcDir = null, args = [] } = {}) {
    const env = { ...process.env };
    if (srcDir) env.BUNDLE_SRC_DIR = srcDir;
    try {
        const stdout = execFileSync('node', [SCRIPT, ...args], {
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
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bundle-init-1449-'));
    for (const [name, content] of Object.entries(files)) {
        fs.writeFileSync(path.join(dir, name), content);
    }
    return dir;
}

describe('check-bundle-init-order #1449 — depth-N walker', () => {
    let tempDirs = [];
    beforeEach(() => { tempDirs = []; });
    afterEach(() => {
        for (const d of tempDirs) {
            try { fs.rmSync(d, { recursive: true, force: true }); } catch {}
        }
    });

    function mkTempSrc(files) {
        const d = withTempSrcDir(files);
        tempDirs.push(d);
        return d;
    }

    it('current bundle: zero false positives at default depth', () => {
        const res = runLint();
        if (res.code !== 0) {
            // Surface stderr to make any future regression obvious.
            // eslint-disable-next-line no-console
            console.error(res.stderr);
        }
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: bundle init-order check clean/);
    });

    it('catches transitive TDZ in the #1370 shape (depth-N walker)', () => {
        // Synthetic bundle:
        //  00-pre.js:  djustInit(); function djustInit() { mountHooks(); }
        //  10-hooks.js: function mountHooks() { _activeHooks.push(1); }
        //               let _activeHooks = [];
        // Concat: 00 before 10 → root call line (00) < decl line (10) → FLAG.
        const src = mkTempSrc({
            '00-pre.js':
                'function djustInit() { mountHooks(); }\n' +
                'djustInit();\n',
            '10-hooks.js':
                'function mountHooks() { _activeHooks.push(1); }\n' +
                'let _activeHooks = [];\n',
        });
        const res = runLint({ srcDir: src });
        expect(res.code).toBe(1);
        expect(res.stderr).toMatch(/USE-BEFORE-DECL \(transitive\): _activeHooks/);
        expect(res.stderr).toMatch(/call chain:.*djustInit\(\).*mountHooks\(\)/);
    });

    it('skips deferral-site callbacks (addEventListener / setTimeout / Promise.then / Observer)', () => {
        // Each deferred-callback site reads a late-declared `let` in
        // another module. NONE should be flagged — those bodies run
        // post-parse, when the let has been initialized.
        const src = mkTempSrc({
            '00-deferred.js':
                'document.addEventListener("click", function() { return _lateA; });\n' +
                'window.addEventListener("load", () => _lateA);\n' +
                'setTimeout(function() { return _lateA; }, 0);\n' +
                'setInterval(() => _lateA, 100);\n' +
                'requestAnimationFrame(() => _lateA);\n' +
                'queueMicrotask(() => _lateA);\n' +
                'Promise.resolve().then(function() { return _lateA; });\n' +
                'Promise.resolve().then(() => _lateA, () => _lateA);\n' +
                'Promise.resolve().catch(() => _lateA);\n' +
                'Promise.resolve().finally(() => _lateA);\n' +
                'new MutationObserver(function() { return _lateA; });\n' +
                'new IntersectionObserver(() => _lateA, {});\n' +
                'new ResizeObserver(() => _lateA);\n' +
                // Named-function reference passed as handler — also deferred.
                'function _handler() { return _lateA; }\n' +
                'document.addEventListener("x", _handler);\n',
            '10-late.js':
                'let _lateA = 1;\n',
        });
        const res = runLint({ srcDir: src });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/OK: bundle init-order check clean/);
    });

    it('--shallow-only matches v0.9.5 behavior (transitive shape is NOT flagged)', () => {
        const src = mkTempSrc({
            '00-pre.js':
                'function djustInit() { mountHooks(); }\n' +
                'djustInit();\n',
            '10-hooks.js':
                'function mountHooks() { _activeHooks.push(1); }\n' +
                'let _activeHooks = [];\n',
        });
        const res = runLint({ srcDir: src, args: ['--shallow-only'] });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/shallow-only/);
    });

    it('--max-depth=0 is equivalent to --shallow-only', () => {
        const src = mkTempSrc({
            '00-pre.js':
                'function djustInit() { mountHooks(); }\n' +
                'djustInit();\n',
            '10-hooks.js':
                'function mountHooks() { _activeHooks.push(1); }\n' +
                'let _activeHooks = [];\n',
        });
        const res = runLint({ srcDir: src, args: ['--max-depth=0'] });
        expect(res.code).toBe(0);
        expect(res.stdout).toMatch(/shallow-only/);
    });

    it('still catches DIRECT top-level reads (shallow path stays armed)', () => {
        const src = mkTempSrc({
            '00-direct.js': 'console.log(_lateA);\n',
            '10-late.js': 'let _lateA = 1;\n',
        });
        const res = runLint({ srcDir: src });
        expect(res.code).toBe(1);
        expect(res.stderr).toMatch(/USE-BEFORE-DECL(?! \(transitive\)): _lateA/);
    });

    it('terminates on cyclic call graphs (a() → b() → a())', () => {
        const src = mkTempSrc({
            '00-cycle.js':
                'function a() { b(); }\n' +
                'function b() { a(); }\n' +
                'a();\n',
            '10-noop.js': 'let _unused = 1;\n',
        });
        const res = runLint({ srcDir: src });
        // Should terminate and produce a clean result (no use of `_unused`).
        expect(res.code).toBe(0);
    });

    it('respects --max-depth=1 (deeper chains are not walked)', () => {
        // f1() → f2() → f3() → reads _late
        // At max-depth=1 only f1's body is descended (f2 call is recognized
        // but its body is at depth 2, beyond the cap). The use is not
        // reachable → no flag.
        const src = mkTempSrc({
            '00-chain.js':
                'function f1() { f2(); }\n' +
                'function f2() { f3(); }\n' +
                'function f3() { _late.x = 1; }\n' +
                'f1();\n',
            '10-late.js': 'let _late = {};\n',
        });
        const shallow = runLint({ srcDir: src, args: ['--max-depth=1'] });
        expect(shallow.code).toBe(0);
        const deep = runLint({ srcDir: src, args: ['--max-depth=8'] });
        expect(deep.code).toBe(1);
        expect(deep.stderr).toMatch(/_late/);
    });
});
