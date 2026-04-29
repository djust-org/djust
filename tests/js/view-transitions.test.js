/**
 * Tests for View Transitions API integration in `applyPatches` (PR-B).
 *
 * Replaces the (withdrawn) PR #1092 test file. Per ADR-013, the wrap
 * is enabled when ALL of these are true:
 *
 *   1. `document.startViewTransition` is a function
 *   2. `document.body` is non-null
 *   3. `<body dj-view-transitions>` opt-in attribute is present
 *   4. `prefers-reduced-motion: reduce` is NOT set
 *
 * Critical correctness invariant: the wrap callback runs in a microtask,
 * NOT synchronously. The vitest stub MUST honor this — invoking the
 * callback synchronously would lie about real-browser semantics and
 * mask the same kind of bug PR #1092 shipped (innerResult observed
 * before the inner call ran).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

function makeTransitionStub({
    failCallback = false,
    asyncCallback = true,
} = {}) {
    /**
     * Microtask-faithful `startViewTransition` stub. Real browsers run
     * the callback in a microtask AFTER capturing a frame, then resolve
     * `updateCallbackDone` once it returns. Sync invocation would lie.
     */
    const calls = [];
    const skipCalls = [];
    const stub = (callback) => {
        calls.push(callback);
        const transition = {
            updateCallbackDone: null,
            skipTransition: () => {
                skipCalls.push(true);
            },
        };
        if (asyncCallback) {
            transition.updateCallbackDone = (async () => {
                // Yield once to mimic the spec — captures pre-frame, then
                // runs the callback.
                await Promise.resolve();
                if (failCallback) {
                    throw new Error('callback failed');
                }
                callback();
            })();
        } else {
            // Synchronous variant — for tests that explicitly want to
            // verify the wrap doesn't depend on sync timing.
            try {
                callback();
                transition.updateCallbackDone = Promise.resolve();
            } catch (err) {
                transition.updateCallbackDone = Promise.reject(err);
            }
        }
        return transition;
    };
    stub.calls = calls;
    stub.skipCalls = skipCalls;
    return stub;
}

function createDom({
    bodyHasOptIn = false,
    hasStartViewTransition = true,
    reducedMotion = false,
    transitionStub = null,
} = {}) {
    const bodyAttr = bodyHasOptIn ? ' dj-view-transitions' : '';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body${bodyAttr}>` +
            '<div dj-root dj-liveview-root dj-view="test.View">' +
            '<p id="content">original</p>' +
            '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    if (hasStartViewTransition) {
        dom.window.document.startViewTransition =
            transitionStub || makeTransitionStub();
    }

    // matchMedia stub — JSDOM doesn't ship one
    dom.window.matchMedia = (query) => ({
        matches: reducedMotion && query.includes('reduce'),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
    });

    const origConsole = dom.window.console;
    dom.window.console = {
        log: () => {},
        error: origConsole.error.bind(origConsole),
        warn: () => {},
        debug: () => {},
        info: () => {},
    };

    dom.window.eval(clientCode);
    return dom;
}

const SET_TEXT_PATCH = [{ type: 'SetText', path: [0], text: 'updated' }];

describe('View Transitions wrap', () => {
    describe('_shouldUseViewTransition gate', () => {
        it('returns true when API present + opt-in attribute + body + no reduce', async () => {
            const dom = createDom({ bodyHasOptIn: true });
            const result = dom.window.djust.applyPatches(SET_TEXT_PATCH);
            await result;
            // The transition stub records calls — non-zero means the wrap fired
            expect(dom.window.document.startViewTransition.calls.length).toBe(1);
        });

        it('returns false when API missing — direct path runs', async () => {
            const dom = createDom({
                bodyHasOptIn: true,
                hasStartViewTransition: false,
            });
            await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            // No wrap invoked since startViewTransition is undefined
            expect(dom.window.document.startViewTransition).toBeUndefined();
        });

        it('returns false when body opt-in attribute is absent', async () => {
            const dom = createDom({ bodyHasOptIn: false });
            await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(dom.window.document.startViewTransition.calls.length).toBe(0);
        });

        it('returns false when prefers-reduced-motion: reduce', async () => {
            const dom = createDom({
                bodyHasOptIn: true,
                reducedMotion: true,
            });
            await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(dom.window.document.startViewTransition.calls.length).toBe(0);
        });
    });

    describe('wrap path correctness', () => {
        it('returns Promise<true> on successful inner-loop completion', async () => {
            const dom = createDom({ bodyHasOptIn: true });
            const result = await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(result).toBe(true);
        });

        it('actually mutates the DOM through the wrap', async () => {
            const dom = createDom({ bodyHasOptIn: true });
            const before = dom.window.document.getElementById('content').textContent;
            expect(before).toBe('original');
            await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            const after = dom.window.document.getElementById('content').textContent;
            expect(after).toBe('updated');
        });

        it('callback runs in a microtask, not synchronously — DOM is unchanged before await', async () => {
            const dom = createDom({ bodyHasOptIn: true });
            const promise = dom.window.djust.applyPatches(SET_TEXT_PATCH);
            // Before awaiting: the wrap callback hasn't run yet
            // (startViewTransition's callback is microtask-deferred)
            expect(
                dom.window.document.getElementById('content').textContent
            ).toBe('original');
            await promise;
            expect(
                dom.window.document.getElementById('content').textContent
            ).toBe('updated');
        });

        it('returns Promise<false> when transition.updateCallbackDone rejects', async () => {
            const failingStub = makeTransitionStub({ failCallback: true });
            const dom = createDom({
                bodyHasOptIn: true,
                transitionStub: failingStub,
            });
            const result = await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(result).toBe(false);
            // skipTransition was called to abandon the animation
            expect(failingStub.skipCalls.length).toBe(1);
        });

        it('honors dynamic mid-session opt-in toggle', async () => {
            const dom = createDom({ bodyHasOptIn: false });
            // First patch: no wrap
            await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(dom.window.document.startViewTransition.calls.length).toBe(0);

            // Toggle opt-in mid-session
            dom.window.document.body.setAttribute('dj-view-transitions', '');

            // Second patch: should wrap now
            await dom.window.djust.applyPatches([
                { type: 'SetText', path: [0], text: 'second' },
            ]);
            expect(dom.window.document.startViewTransition.calls.length).toBe(1);
        });
    });

    describe('direct (no-wrap) path', () => {
        it('returns Promise<true> on success', async () => {
            const dom = createDom({ bodyHasOptIn: false });
            const result = await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(result).toBe(true);
        });

        it('mutates DOM without invoking startViewTransition', async () => {
            const dom = createDom({ bodyHasOptIn: false });
            await dom.window.djust.applyPatches(SET_TEXT_PATCH);
            expect(
                dom.window.document.getElementById('content').textContent
            ).toBe('updated');
            expect(dom.window.document.startViewTransition.calls.length).toBe(0);
        });

        it('empty patches returns Promise<true> without wrapping', async () => {
            const dom = createDom({ bodyHasOptIn: true });
            const result = await dom.window.djust.applyPatches([]);
            expect(result).toBe(true);
            // Empty-patch fast path bypasses the wrap entirely
            expect(dom.window.document.startViewTransition.calls.length).toBe(0);
        });
    });
});
