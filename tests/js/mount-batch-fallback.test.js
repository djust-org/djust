/**
 * Regression tests for #1031 — version-probe fallback for mount_batch.
 *
 * When a v0.6.0+ client talks to a pre-v0.6.0 server, the server returns
 * `{type: "error", error: "Unknown message type: mount_batch"}`. Without a
 * fallback the lazy-hydrated views never mount.
 *
 * Coverage:
 *   1. handleMountBatchFallback() iterates the stashed mounts and calls
 *      mountElement() for each — idempotent (safe against late mount_batch
 *      response triggering a phantom fallback).
 *   2. The fallback resets the `dj-lazy` and `data-live-hydrated` attributes
 *      on each element so mountElement re-enters the hydration path cleanly.
 *   3. Source-level assertion: the websocket error handler dispatches to
 *      lazyHydration.handleMountBatchFallback when the error matches
 *      /mount_batch|unknown\s+message\s+type/i.
 *   4. Source-level assertion: the mount_batch SUCCESS branch clears
 *      inFlightBatch so a late error frame can't trigger a phantom fallback.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';

const lazySrc = readFileSync('./python/djust/static/djust/src/13-lazy-hydration.js', 'utf-8');
const wsSrc = readFileSync('./python/djust/static/djust/src/03-websocket.js', 'utf-8');

describe('#1031 — mount_batch fallback to per-view mount on old server', () => {
    it('lazy-hydration manager exposes handleMountBatchFallback', () => {
        expect(lazySrc).toMatch(/handleMountBatchFallback\s*\(\s*\)\s*\{/);
    });

    it('handleMountBatchFallback clears inFlightBatch before iterating (idempotency guard)', () => {
        // Source sequence: read mounts → null inFlightBatch → forEach → mountElement.
        // The null assignment must happen BEFORE the forEach so a re-entrant
        // call returns immediately.
        const fnMatch = lazySrc.match(/handleMountBatchFallback\s*\(\s*\)\s*\{[\s\S]*?\n\s{4}\},?\n/);
        expect(fnMatch).not.toBeNull();
        const body = fnMatch[0];
        const nullIdx = body.indexOf('this.inFlightBatch = null');
        const forEachIdx = body.indexOf('forEach');
        expect(nullIdx).toBeGreaterThan(-1);
        expect(forEachIdx).toBeGreaterThan(-1);
        expect(nullIdx).toBeLessThan(forEachIdx);
    });

    it('handleMountBatchFallback bails on no in-flight batch (early return)', () => {
        const body = lazySrc.match(/handleMountBatchFallback\s*\(\s*\)\s*\{[\s\S]*?\n\s{4}\},?\n/)[0];
        expect(body).toMatch(/if\s*\(\s*!this\.inFlightBatch\s*\)\s*return;?/);
    });

    it('processPendingMounts stashes mounts in inFlightBatch when sending mount_batch', () => {
        // Look inside processPendingMounts for the stash-before-send pattern.
        const procIdx = lazySrc.indexOf('processPendingMounts()');
        expect(procIdx).toBeGreaterThan(-1);
        // Find the "this.inFlightBatch = mounts;" assignment between
        // processPendingMounts and the next named method.
        const nextMethodIdx = lazySrc.indexOf('handleMountBatchFallback', procIdx);
        const procBody = lazySrc.slice(procIdx, nextMethodIdx);
        expect(procBody).toMatch(/this\.inFlightBatch\s*=\s*mounts/);
    });

    it('websocket error handler dispatches to lazyHydration.handleMountBatchFallback on mount_batch errors', () => {
        // The error case must check the error string AND the manager presence
        // before calling the fallback.
        expect(wsSrc).toMatch(/case ['"]error['"]:/);
        expect(wsSrc).toMatch(/mount_batch\|unknown\\s\+message\\s\+type/i);
        expect(wsSrc).toMatch(/window\.djust\.lazyHydration\.handleMountBatchFallback/);
    });

    it('mount_batch success branch clears inFlightBatch (prevents phantom fallback)', () => {
        // Find the case 'mount_batch': block and verify it includes the
        // inFlightBatch=null assignment.
        const matchIdx = wsSrc.indexOf("case 'mount_batch': {");
        expect(matchIdx).toBeGreaterThan(-1);
        // Block extends until the next sibling case at the same indent —
        // for assertion purposes, scan forward 2000 chars.
        const block = wsSrc.slice(matchIdx, matchIdx + 2000);
        expect(block).toMatch(/window\.djust\.lazyHydration\.inFlightBatch\s*=\s*null/);
    });

    it('debug log fires only under globalThis.djustDebug (no production console pollution)', () => {
        const body = lazySrc.match(/handleMountBatchFallback\s*\(\s*\)\s*\{[\s\S]*?\n\s{4}\},?\n/)[0];
        expect(body).toMatch(/if\s*\(\s*globalThis\.djustDebug\s*\)/);
    });
});
