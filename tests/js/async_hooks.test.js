/**
 * Regression tests for async-tolerant dj-hook lifecycle dispatch
 * (v0.8.6 add — leverages PR-A's async patch path + #1098's queued
 * handleMessage to make user hooks safely async).
 *
 * Contract:
 *   - Sync hook methods behave exactly as before (no API change).
 *   - Async hook methods (returning a Promise) have their rejections
 *     caught and logged via console.error — no Unhandled Promise
 *     Rejection in the browser console, no crash.
 *   - The dispatcher does NOT await — hooks fire-and-forget on the
 *     lifecycle path. User code that needs sequencing should
 *     coordinate explicitly.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

function createDom() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>' +
            '<div dj-root dj-liveview-root dj-view="test.View">' +
            '<div id="hookable" dj-hook="MyHook"></div>' +
            '</div></body></html>',
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Capture console.error calls
    const errors = [];
    const origError = dom.window.console.error.bind(dom.window.console);
    dom.window.console = {
        log: () => {},
        error: (...args) => {
            errors.push(args);
        },
        warn: () => {},
        debug: () => {},
        info: () => {},
    };

    dom.window.eval(clientCode);
    return { dom, errors };
}

describe('Async-tolerant dj-hook dispatch', () => {
    describe('sync hooks (unchanged behavior)', () => {
        it('mounted() called synchronously — no Promise wrapping', () => {
            const { dom } = createDom();
            const calls = [];

            dom.window.djust.hooks = dom.window.djust.hooks || {};
            dom.window.djust.hooks.MyHook = ({
                mounted() {
                    calls.push('mounted');
                },
            });
            dom.window.djust.mountHooks();

            expect(calls).toEqual(['mounted']);
        });

        it('sync mounted() throwing logs to console.error', () => {
            const { dom, errors } = createDom();

            dom.window.djust.hooks = dom.window.djust.hooks || {};
            dom.window.djust.hooks.MyHook = ({
                mounted() {
                    throw new Error('sync boom');
                },
            });
            dom.window.djust.mountHooks();

            expect(errors.length).toBe(1);
            expect(errors[0][0]).toContain('MyHook.mounted()');
        });
    });

    describe('async hooks (new behavior)', () => {
        it('async mounted() returning a resolved Promise does not log', async () => {
            const { dom, errors } = createDom();

            dom.window.djust.hooks = dom.window.djust.hooks || {};
            dom.window.djust.hooks.MyHook = ({
                async mounted() {
                    return 'ok';
                },
            });
            dom.window.djust.mountHooks();

            // Yield to microtask queue so the Promise resolves
            await new Promise((r) => setTimeout(r, 0));

            expect(errors.length).toBe(0);
        });

        it('async mounted() rejecting logs the rejection', async () => {
            const { dom, errors } = createDom();

            dom.window.djust.hooks = dom.window.djust.hooks || {};
            dom.window.djust.hooks.MyHook = ({
                async mounted() {
                    throw new Error('async boom');
                },
            });
            dom.window.djust.mountHooks();

            // Wait for the rejection to propagate to .catch()
            await new Promise((r) => setTimeout(r, 0));

            expect(errors.length).toBe(1);
            expect(errors[0][0]).toContain('MyHook.mounted()');
        });

        it('dispatcher does NOT await — synchronous return path', () => {
            const { dom } = createDom();
            const calls = [];
            let resolveLater;
            const blocker = new Promise((r) => {
                resolveLater = r;
            });

            dom.window.djust.hooks = dom.window.djust.hooks || {};
            dom.window.djust.hooks.MyHook = ({
                async mounted() {
                    calls.push('mounted-start');
                    await blocker;
                    calls.push('mounted-end');
                },
            });

            // mountHooks should return immediately, even while the hook is awaiting
            dom.window.djust.mountHooks();
            calls.push('after-mountHooks');

            // mounted-start ran synchronously (microtask BEFORE the await),
            // after-mountHooks comes from our test, mounted-end is still pending
            expect(calls.includes('after-mountHooks')).toBe(true);
            expect(calls.includes('mounted-end')).toBe(false);

            // Unblock the hook
            resolveLater();
        });
    });
});
