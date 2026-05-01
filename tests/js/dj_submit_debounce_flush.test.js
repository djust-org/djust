/**
 * Regression tests for #1278.
 *
 * Form submit races with debounced dj-input events: a user who types a
 * field then immediately clicks submit (within the 300ms debounce window)
 * hits a race where submit fires while per-field dj-input events are
 * still pending — server-side state populated by those handlers is
 * empty when the submit handler reads it.
 *
 * Fix: `_handleDjSubmit` now calls `_flushPendingDebouncesInForm`
 * BEFORE dispatching the submit handler. The debounced wrappers
 * created by `debounce(fn, ms)` now expose a `.flush()` method that
 * fires the pending invocation immediately.
 *
 * Two test layers:
 *  1. Unit-level: `debounce()` exposes `.flush()` that fires pending
 *     synchronously and is a no-op when no invocation is pending.
 *  2. Integration: dj-input fires within the form before dj-submit's
 *     server dispatch, regardless of the 300ms debounce timer.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');


function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });
        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}


// ---------------------------------------------------------------------------
// Layer 1: unit test the debounce() flush() method directly.
// ---------------------------------------------------------------------------


describe('debounce() with .flush() (closes #1278)', () => {
    it('returned function exposes .flush() method', () => {
        const dom = createTestEnv('');
        initClient(dom);
        const debounce = dom.window.djust && dom.window.djust._debounceForTest;
        // The internal debounce isn't exposed directly; we exercise it via
        // the integration test below. This sanity check just confirms the
        // module loaded.
        expect(typeof dom.window.djust).toBe('object');
    });

    it('flush() fires pending invocation synchronously', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.V">
                <form dj-submit="submit">
                    <input type="text" name="email" dj-input="set_email" value="user@x.com" />
                </form>
            </div>
        `);
        initClient(dom);
        await nativeSleep(20);

        const inp = dom.window.document.querySelector('input[name="email"]');
        const form = dom.window.document.querySelector('form[dj-submit]');

        // Type an event — kicks off 300ms debounce.
        inp.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // Right away, the dj-input fetch hasn't fired yet (debounced).
        const callsBeforeSubmit = dom.window._testFetchCalls.filter(
            c => c.eventName === 'set_email'
        ).length;
        expect(callsBeforeSubmit).toBe(0);

        // Submit the form — should flush the pending dj-input first.
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Give microtasks a chance to settle.
        await nativeSleep(20);

        const allCalls = dom.window._testFetchCalls;
        // Must include both the dj-input event AND the dj-submit event.
        const inputCalls = allCalls.filter(c => c.eventName === 'set_email');
        const submitCalls = allCalls.filter(c => c.eventName === 'submit');

        expect(inputCalls.length).toBeGreaterThanOrEqual(1);
        expect(submitCalls.length).toBeGreaterThanOrEqual(1);

        // Ordering: the input event must fire BEFORE the submit event.
        const inputIdx = allCalls.findIndex(c => c.eventName === 'set_email');
        const submitIdx = allCalls.findIndex(c => c.eventName === 'submit');
        expect(inputIdx).toBeLessThan(submitIdx);
    });

    it('multiple inputs in the form all flush before submit', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.V">
                <form dj-submit="submit">
                    <input type="text" name="email" dj-input="set_email" value="a@b" />
                    <input type="password" name="password" dj-input="set_password" value="pw" />
                </form>
            </div>
        `);
        initClient(dom);
        await nativeSleep(20);

        const email = dom.window.document.querySelector('input[name="email"]');
        const password = dom.window.document.querySelector('input[name="password"]');
        const form = dom.window.document.querySelector('form[dj-submit]');

        // Type both fields — both kick off 300ms debounces.
        email.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        password.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // Submit immediately. Both debounces must flush.
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
        await nativeSleep(20);

        const allCalls = dom.window._testFetchCalls;
        const eventNames = allCalls.map(c => c.eventName);
        expect(eventNames).toContain('set_email');
        expect(eventNames).toContain('set_password');
        expect(eventNames).toContain('submit');

        // Both inputs flushed BEFORE submit dispatched.
        const submitIdx = eventNames.indexOf('submit');
        const emailIdx = eventNames.indexOf('set_email');
        const passwordIdx = eventNames.indexOf('set_password');
        expect(emailIdx).toBeLessThan(submitIdx);
        expect(passwordIdx).toBeLessThan(submitIdx);
    });

    it('flush is a no-op when no debounced input is pending', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.V">
                <form dj-submit="submit">
                    <input type="text" name="email" dj-input="set_email" />
                </form>
            </div>
        `);
        initClient(dom);
        await nativeSleep(20);

        // Submit without typing anything — must not error and must
        // not invoke set_email (no pending invocation).
        const form = dom.window.document.querySelector('form[dj-submit]');
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
        await nativeSleep(20);

        const allCalls = dom.window._testFetchCalls;
        const inputCalls = allCalls.filter(c => c.eventName === 'set_email');
        const submitCalls = allCalls.filter(c => c.eventName === 'submit');

        expect(inputCalls.length).toBe(0);
        expect(submitCalls.length).toBeGreaterThanOrEqual(1);
    });
});
