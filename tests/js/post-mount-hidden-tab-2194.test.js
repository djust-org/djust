/**
 * Post-mount work must run even when the tab is hidden (#2194).
 *
 * `03-websocket.js` used to schedule ALL post-mount work through a single
 * `requestAnimationFrame`:
 *
 *     if (typeof requestAnimationFrame === 'function') {
 *         requestAnimationFrame(runPostMount);
 *     } else {
 *         runPostMount();
 *     }
 *
 * `runPostMount` is the only path to `reinitAfterDOMUpdate()`,
 * `window.djust._mountReady = true`, and reconnect form-recovery /
 * `dj-auto-recover`. Browsers do not fire rAF callbacks in a hidden tab, and a
 * page is routinely hidden at mount: opened in a background tab, restored with
 * the session, or prerendered.
 *
 * Measured in a real hidden tab BEFORE the fix: a queued rAF was still pending
 * seconds later, `_mountReady` stayed `undefined`, and the #1610 mount morph
 * had already wiped the `[dj-virtual]` shell with nothing to restore it — 60
 * rows rendered, no virtualization. AFTER the fix, the same hidden tab reports
 * `_mountReady: true`, `shell: true`, 13 rows.
 *
 * FIX #619 (defer to avoid a mid-paint layout flash) is preserved: the VISIBLE
 * path is untouched. When the document is hidden there is no paint in progress,
 * so #619's reason to defer does not apply.
 */

import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';

const WS = fs.readFileSync(
    path.resolve('python/djust/static/djust/src/03-websocket.js'),
    'utf-8'
);

/** Source with comments stripped — a negative assertion must not match prose. */
const WS_CODE = WS
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');

describe('#2194 — post-mount work must not be rAF-only', () => {
    it('does not gate post-mount solely on requestAnimationFrame', () => {
        // The exact pre-fix shape: rAF with only a "no rAF function" fallback,
        // which never triggers in a browser — the hidden case falls through it.
        const rafOnly =
            /if \(typeof requestAnimationFrame === 'function'\) \{\s*requestAnimationFrame\(runPostMount\);\s*\} else \{\s*runPostMount\(\);\s*\}/;
        expect(WS_CODE).not.toMatch(rafOnly);
    });

    it('runs post-mount immediately when the document is hidden', () => {
        expect(WS_CODE).toMatch(/if \(document\.hidden\) \{\s*runPostMount\(\);/);
    });

    it('keeps the rAF path for the visible case (preserves FIX #619)', () => {
        expect(WS_CODE).toMatch(/requestAnimationFrame\(/);
        // #619's whole point is that the visible path defers past the paint.
        expect(WS).toMatch(/FIX #619/);
    });

    it('has a timeout backstop for occluded/throttled tabs', () => {
        // `document.hidden` is false for an occluded or minimised window, where
        // rAF may still be throttled. Without a backstop those tabs regress to
        // the original bug.
        expect(WS_CODE).toMatch(/setTimeout\(once,\s*\d+\)/);
    });

    it('guards the race so post-mount runs exactly once', () => {
        // rAF and the timeout can both fire; `done` must make it idempotent.
        // Running twice would re-enter form recovery and dj-auto-recover.
        expect(WS_CODE).toMatch(/let done = false;/);
        expect(WS_CODE).toMatch(/if \(!done\) \{ done = true; runPostMount\(\); \}/);
    });

    it('uses a backstop longer than a frame so rAF still wins when visible', () => {
        // A setTimeout(0) backstop would beat rAF (~16ms) on every visible
        // mount, silently undoing FIX #619 while looking correct.
        const m = WS_CODE.match(/setTimeout\(once,\s*(\d+)\)/);
        expect(m).not.toBeNull();
        expect(Number(m[1])).toBeGreaterThan(16);
    });

    it('still sets _mountReady and runs recovery inside runPostMount', () => {
        // Guard the payload, not just the scheduling: these are what a hidden
        // tab was losing.
        expect(WS_CODE).toMatch(/window\.djust\._mountReady = true;/);
        expect(WS_CODE).toMatch(/_processFormRecovery/);
        expect(WS_CODE).toMatch(/_processAutoRecover/);
    });
});
