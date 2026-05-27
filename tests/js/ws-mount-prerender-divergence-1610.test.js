/**
 * Regression tests for #1610 — WS-mount HTML must be applied to the
 * pre-rendered DOM, not silently dropped.
 *
 * BEFORE this fix, the `skipMountHtml` branch in `03-websocket.js` only
 * stamped `dj-id` attributes onto the existing prerender DOM via
 * `_stampDjIds(data.html)`. Any content that diverged between
 * HTTP-prerender context and WS-mount context (presence counts,
 * `_websocket_session_id`-derived values, per-connection state) was
 * silently dropped — the DOM stayed at the prerender value until the
 * user did a full page reload.
 *
 * The fix: in the same branch, build a temp `<div>` from `data.html`
 * and call `morphChildren(container, _morphTemp)` — the same helper
 * used by `handleEmbeddedUpdate` (line ~1127) and the `html_recovery`
 * path (line ~641). `morphChildren` preserves keyed nodes by id, so
 * the dj-id stamp step is folded into the morph.
 *
 * These tests assert the source shape post-fix. The companion
 * server-side test in
 * `python/djust/tests/test_ws_mount_prerender_divergence_1610.py`
 * pins server correctness (was always right; regression backstop).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';

const clientSource = readFileSync('./python/djust/static/djust/src/03-websocket.js', 'utf-8');

function getSkipMountHtmlBranch(source) {
    // The branch starts at `if (this.skipMountHtml) {` and ends at the
    // matching `} else if (data.html) {` that begins the non-prerender
    // branch. Slice between them and inspect the body.
    const skipIdx = source.indexOf('if (this.skipMountHtml) {');
    const elseIfIdx = source.indexOf('} else if (data.html) {', skipIdx);
    if (skipIdx < 0 || elseIfIdx < 0) {
        throw new Error('skipMountHtml branch boundaries not found in 03-websocket.js');
    }
    return source.slice(skipIdx, elseIfIdx);
}

describe('#1610 — WS-mount HTML applied to prerendered DOM via morphChildren', () => {
    let source;
    let branch;

    beforeEach(() => {
        source = clientSource;
        branch = getSkipMountHtmlBranch(source);
    });

    it('the skipMountHtml branch calls morphChildren to apply WS-mount HTML', () => {
        // Post-fix: the branch must call morphChildren(...) when the
        // server emitted dj-id attributes. This is the load-bearing
        // assertion — without it the bug class is unfixed.
        expect(branch).toMatch(/morphChildren\(/);
    });

    it('the morph branch builds a temp div from data.html (mirrors handleEmbeddedUpdate)', () => {
        // The canonical shape (lines 1124-1127 in the existing
        // handleEmbeddedUpdate path):
        //   const _morphTemp = document.createElement('div');
        //   _morphTemp.innerHTML = html;
        //   morphChildren(container, _morphTemp);
        // The fix MUST reuse this shape — anything else is a bespoke
        // path that could drift from the canonical embedded-update
        // semantics.
        expect(branch).toMatch(/document\.createElement\(['"]div['"]\)/);
        expect(branch).toMatch(/\.innerHTML\s*=\s*data\.html/);
    });

    it('the morph branch selects a [dj-view] / [dj-root] container excluding sticky', () => {
        // Sticky LiveViews must not be morphed by the parent's mount
        // HTML — they are independent attach points (#1393 era).
        // The container selector must exclude [dj-sticky-root].
        expect(branch).toMatch(/\[dj-view\][^\]]*:not\(\[dj-sticky-root\]\)|dj-sticky-root/);
        // And there must be a [dj-view] or [dj-root] selector somewhere
        // in the branch.
        expect(branch).toMatch(/\[dj-view\]|\[dj-root\]/);
    });

    it('the morph branch falls back to _stampDjIds when no container is found', () => {
        // Edge case: if there's no [dj-view]/[dj-root] in the DOM
        // (very unusual, but possible in tests or exotic embeds), the
        // branch must fall back to the dj-id stamp so subsequent
        // ID-based patches still resolve. This was the OLD behavior
        // — preserved as a safety net.
        expect(branch).toMatch(/_stampDjIds\(/);
    });

    it('the morph branch keeps skipMountHtml = false set AFTER the morph', () => {
        // Critical post-mount invariant: the rAF wrap and the
        // `this.skipMountHtml = false;` reset must remain. The fix
        // only changed the inner if-block, not the surrounding
        // post-mount block.
        expect(branch).toMatch(/this\.skipMountHtml\s*=\s*false/);
    });

    it('the post-mount rAF wrap is preserved (regression guard for #619)', () => {
        // The fix MUST NOT regress #619 — the post-mount block must
        // still be wrapped in requestAnimationFrame. This is the
        // existing invariant; we re-pin it because the fix touches
        // the same branch.
        expect(branch).toMatch(/typeof requestAnimationFrame === ['"]function['"]/);
        expect(branch).toMatch(/requestAnimationFrame\(runPostMount\)/);
    });

    it('the morph call uses the temp div built from data.html as the source argument', () => {
        // Verify the call shape: morphChildren(container, tempDiv).
        // The second argument must be the temp div constructed from
        // data.html — a bare reference to data.html would be wrong
        // (morphChildren expects an element, not a string).
        const morphCall = branch.match(/morphChildren\([^,]+,\s*([^)]+)\)/);
        expect(morphCall).toBeTruthy();
        const sourceArg = morphCall[1].trim();
        // The source arg must be the local temp div variable, NOT
        // `data.html` (a string) and NOT a global lookup.
        expect(sourceArg).not.toBe('data.html');
    });

    it('the OLD shape (bare _stampDjIds with no morph) is removed from the primary path', () => {
        // Belt-and-suspenders: the bug-shipping shape was the branch
        // containing ONLY `_stampDjIds(data.html)` with no morph
        // alongside. After the fix, even if _stampDjIds remains as a
        // fallback, the primary path must call morphChildren.
        //
        // Strip block comments + line comments so the rationale
        // block doesn't count toward primary code.
        const branchCode = branch
            .replace(/\/\*[\s\S]*?\*\//g, '')
            .replace(/\/\/[^\n]*/g, '');
        // morphChildren must be present in executable code.
        expect(branchCode).toMatch(/morphChildren\(/);
    });
});
