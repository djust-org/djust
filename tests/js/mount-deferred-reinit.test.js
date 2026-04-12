/**
 * Regression tests for #619 — defer `reinitAfterDOMUpdate` on pre-rendered mount.
 *
 * When a page is pre-rendered via HTTP GET, calling `reinitAfterDOMUpdate()`
 * synchronously after stamping `dj-id` attributes forces the browser to
 * recalculate layout mid-paint. This causes a visible flash where large
 * elements (e.g. dashboard stat values) briefly render at the wrong size.
 *
 * The fix wraps the post-mount block in `requestAnimationFrame(...)` so
 * event binding + form recovery happens after the next paint. Falls back
 * to synchronous execution when `requestAnimationFrame` is unavailable
 * (e.g. JSDOM by default).
 *
 * These tests assert that:
 *   1. The post-mount block runs via rAF when rAF is present.
 *   2. The post-mount block still runs synchronously when rAF is missing.
 *   3. reinit() runs BEFORE the _mountReady flag is set (ordering invariant).
 *   4. reinit() runs BEFORE form recovery (ordering invariant on reconnect).
 *   5. Non-prerendered mounts (data.html path) are unchanged — still sync.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';

const clientSource = readFileSync('./python/djust/static/djust/src/03-websocket.js', 'utf-8');

/**
 * Extract the post-mount block source for direct inspection. We run
 * assertions against the *source* because the real mount flow is deeply
 * wired into the WebSocket lifecycle and faking enough of that to
 * actually execute the branch is much more code than the fix itself.
 * Source-level assertions catch the regression exactly — any future
 * change that removes the rAF wrapper will flip a test red.
 */
function getWebSocketSource() {
    return clientSource;
}

describe('#619 — defer reinitAfterDOMUpdate on pre-rendered mount', () => {
    let source;

    beforeEach(() => {
        source = getWebSocketSource();
    });

    it('wraps the pre-rendered post-mount block in requestAnimationFrame', () => {
        // The skipMountHtml branch must use rAF when it is available.
        expect(source).toMatch(/if \(this\.skipMountHtml\) \{/);
        expect(source).toMatch(/typeof requestAnimationFrame === ['"]function['"]/);
        expect(source).toMatch(/requestAnimationFrame\(runPostMount\)/);
    });

    it('falls back to synchronous execution when requestAnimationFrame is unavailable', () => {
        // The fallback branch must still call runPostMount() directly so
        // JSDOM tests (and exotic non-browser environments) don't regress.
        const fallbackPattern = /if \(typeof requestAnimationFrame === ['"]function['"]\) \{\s*requestAnimationFrame\(runPostMount\);\s*\} else \{\s*runPostMount\(\);\s*\}/s;
        expect(source).toMatch(fallbackPattern);
    });

    it('names the post-mount closure runPostMount (stable call site for future debugging)', () => {
        expect(source).toMatch(/const runPostMount = \(\) => \{/);
    });

    it('calls reinitAfterDOMUpdate() FIRST inside the post-mount closure', () => {
        // Ordering invariant: event binding must happen before dj-mounted
        // handlers fire (so the handlers can rely on bound listeners) and
        // before form recovery (so recovered inputs have change listeners).
        const closureMatch = source.match(/const runPostMount = \(\) => \{([\s\S]*?)\n\s*\};/);
        expect(closureMatch).toBeTruthy();
        const closureBody = closureMatch[1];

        const reinitIdx = closureBody.indexOf('reinitAfterDOMUpdate()');
        const mountReadyIdx = closureBody.indexOf('window.djust._mountReady = true');
        const formRecoveryIdx = closureBody.indexOf('_processFormRecovery');

        expect(reinitIdx).toBeGreaterThanOrEqual(0);
        expect(mountReadyIdx).toBeGreaterThan(reinitIdx);
        expect(formRecoveryIdx).toBeGreaterThan(reinitIdx);
    });

    it('sets _mountReady inside the deferred closure, not before it', () => {
        // Setting _mountReady synchronously while reinit is still queued
        // would let dj-mounted handlers fire against an un-bound DOM.
        // The flag MUST be set inside runPostMount.
        const closureMatch = source.match(/const runPostMount = \(\) => \{([\s\S]*?)\n\s*\};/);
        expect(closureMatch).toBeTruthy();
        expect(closureMatch[1]).toContain('window.djust._mountReady = true');
    });

    it('runs form recovery only on reconnect, inside the deferred closure', () => {
        const closureMatch = source.match(/const runPostMount = \(\) => \{([\s\S]*?)\n\s*\};/);
        expect(closureMatch).toBeTruthy();
        const closureBody = closureMatch[1];
        expect(closureBody).toMatch(/if \(window\.djust\._isReconnect\) \{/);
        expect(closureBody).toContain('_processFormRecovery');
        expect(closureBody).toContain('_processAutoRecover');
    });

    it('non-prerendered (data.html innerHTML replace) branch calls reinit synchronously', () => {
        // The `else if (data.html)` branch does a full innerHTML replace,
        // which already invalidates layout — there's no pre-paint to
        // protect, so reinit can still be synchronous. Only the
        // skipMountHtml path needed the rAF wrap.
        const elseIfMatch = source.match(/\} else if \(data\.html\) \{([\s\S]*?)\n\s{16}\}/);
        expect(elseIfMatch).toBeTruthy();
        const body = elseIfMatch[1];
        // reinit should appear directly (not inside a runPostMount closure).
        expect(body).toContain('reinitAfterDOMUpdate()');
        expect(body).not.toContain('requestAnimationFrame(runPostMount)');
    });

    it('leaves the old sync call-site removed so the fix cannot be silently reverted', () => {
        // The skipMountHtml branch must contain exactly one
        // reinitAfterDOMUpdate() call, and it must be inside the
        // runPostMount closure (not a sync fallthrough that regresses #619).
        const skipIdx = source.indexOf('if (this.skipMountHtml) {');
        const elseIfIdx = source.indexOf('} else if (data.html) {', skipIdx);
        expect(skipIdx).toBeGreaterThanOrEqual(0);
        expect(elseIfIdx).toBeGreaterThan(skipIdx);

        const branchBody = source.slice(skipIdx, elseIfIdx);
        // Strip single-line comments so "// FIX #619: ...reinitAfterDOMUpdate()..."
        // in the rationale block doesn't inflate the count.
        const branchCode = branchBody.replace(/\/\/[^\n]*/g, '');
        const reinitCount = (branchCode.match(/reinitAfterDOMUpdate\(\)/g) || []).length;
        expect(reinitCount).toBe(1);

        const closureStart = branchCode.indexOf('const runPostMount');
        const reinitIdx = branchCode.indexOf('reinitAfterDOMUpdate()');
        expect(closureStart).toBeGreaterThanOrEqual(0);
        expect(reinitIdx).toBeGreaterThan(closureStart);
    });
});
