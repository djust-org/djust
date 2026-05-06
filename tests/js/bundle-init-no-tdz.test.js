/**
 * Regression test for #1370 — v0.9.4rc1 hooks TDZ bundle-init crash.
 *
 * Failure mode (rc1):
 *   `Uncaught ReferenceError: Cannot access 'G' before initialization`
 *   on EVERY page load and EVERY WS patch. `G` is the minified
 *   `_activeHooks`, declared `let _activeHooks;` at
 *   `python/djust/static/djust/src/19-hooks.js:54`. Module 19 is
 *   concatenated AFTER the bootstrap call at bundle line ~7842;
 *   `let` is in TDZ until the declaration line is REACHED. When
 *   `document.readyState !== 'loading'` (the normal case when scripts
 *   run after page load — e.g., deferred scripts, dynamic injection,
 *   turbo-nav re-init), `djustInit()` is invoked SYNCHRONOUSLY at
 *   line 7842 BEFORE `let _activeHooks;` runs at line 9586.
 *   `djustInit() → mountHooks() → _ensureHooksInit()` reads
 *   `_activeHooks` → TDZ ReferenceError.
 *
 * Why PR #1359 (eslint cleanup) missed this:
 *   Existing vitest import-order tests catch DECLARED-EARLY-USED-LATE
 *   patterns (`liveViewWS`, `clientVdomVersion`, etc.). `_activeHooks`
 *   is the INVERSE pattern (DECLARED-LATE-USED-EARLY in the
 *   concat-order sense). vitest's import-order does not simulate
 *   bundle-concat-order execution under `readyState === 'complete'`.
 *
 * What this test guards:
 *   Loads the BUNDLED client.js in a JSDOM env where
 *   `document.readyState === 'complete'` (script runs AFTER DOMContentLoaded,
 *   like deferred/late-injected scripts in production). Asserts no
 *   `ReferenceError: Cannot access 'X' before initialization` is thrown
 *   during bootstrap. Catches the structural class — any future `let`
 *   declared late in the concat that's used early will trip this.
 *
 * Verification (Action #1196, doc-claim-verbatim TDD):
 *   This test FAILS against the rc1 bundle (verified locally 2026-05-02
 *   via temp-restore from `scratch/client-pre-fix.js`; not committed).
 *   PASSES against the fixed bundle (`var _activeHooks` / `var _hookIdCounter`).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import path from 'path';

const BUNDLE_PATH = path.resolve('./python/djust/static/djust/client.js');
const clientCode = fs.readFileSync(BUNDLE_PATH, 'utf-8');

/**
 * Build a JSDOM where document.readyState === 'complete' BEFORE the
 * client.js bundle is evaluated. This is the production failure mode:
 * bundle scripts that run after DOMContentLoaded (deferred / dynamically
 * injected / turbo-nav re-init) take the synchronous-init branch at
 * `if (document.readyState === 'loading') ... else djustInit();`,
 * which fires the TDZ.
 */
async function makeDomWithCompleteReadyState(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true }
    );

    // Mock WebSocket so init doesn't blow up trying to connect.
    dom.window.WebSocket = class MockWebSocket {
        constructor() { this.readyState = 1; }
        send() {}
        close() {}
        addEventListener() {}
        removeEventListener() {}
    };
    dom.window.WebSocket.OPEN = 1;
    dom.window.WebSocket.CONNECTING = 0;
    dom.window.WebSocket.CLOSING = 2;
    dom.window.WebSocket.CLOSED = 3;

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Wait for the JSDOM document to reach 'complete' so the bundle
    // takes the synchronous `djustInit()` path at line ~7842.
    await new Promise(resolve => {
        if (dom.window.document.readyState === 'complete') return resolve();
        dom.window.addEventListener('load', resolve, { once: true });
    });

    return dom;
}

describe('bundle-init: no TDZ ReferenceError on bootstrap (#1370)', () => {
    it('synchronous bootstrap (readyState=complete) does not throw TDZ ReferenceError', async () => {
        const dom = await makeDomWithCompleteReadyState();

        expect(dom.window.document.readyState).toBe('complete');

        // Do NOT swallow errors — that's the bug-class we are guarding
        // against. With readyState === 'complete', `djustInit()` runs
        // synchronously inside `eval`. If `_activeHooks` is `let`-declared
        // AFTER the bootstrap call site, the TDZ ReferenceError propagates
        // out of `eval()` directly.
        let initError = null;
        try {
            dom.window.eval(clientCode);
        } catch (e) {
            initError = e;
        }

        if (initError) {
            const message = String(initError.message || initError);
            // The specific TDZ failure mode from rc1.
            expect(message).not.toMatch(/Cannot access '[^']+' before initialization/);
            // Any other unexpected init failure should also surface.
            throw initError;
        }

        // Sanity: bootstrap actually ran far enough to set the init flag.
        // If TDZ fired, djustInit would have thrown before reaching
        // `window.djustInitialized = true`.
        expect(dom.window.djustInitialized).toBe(true);
    });

    it('hooks state is reachable post-init without TDZ', async () => {
        // Specific assertion: the public hooks API works after bootstrap.
        // If `_activeHooks` were still TDZ-trapped (the rc1 failure),
        // `mountHooks()` would have thrown the ReferenceError during init.
        const dom = await makeDomWithCompleteReadyState(
            '<div dj-view="x.y.Z" dj-hook="MyHook"></div>'
        );

        let initError = null;
        try {
            dom.window.eval(clientCode);
        } catch (e) {
            initError = e;
        }
        expect(initError).toBeNull();
        expect(dom.window.djustInitialized).toBe(true);
    });
});
