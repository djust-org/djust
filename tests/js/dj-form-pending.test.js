/**
 * Tests for `dj-form-pending` — React 19 `useFormStatus` equivalent (v0.8.0).
 *
 * Any element nested inside a `<form dj-submit>` can declare
 * `dj-form-pending="hide|show|disabled"` and react automatically when the
 * ancestor form's submit handler is in-flight. The form itself gets a
 * `data-djust-form-pending="true"` attribute while pending.
 *
 * Modes:
 *   - "hide"     → hidden while pending, visible otherwise (idle label)
 *   - "show"     → visible while pending, hidden otherwise (loading spinner)
 *   - "disabled" → disabled=true while pending, original state otherwise
 *
 * State is set BEFORE the network round-trip and cleared in finally so it
 * always resolves regardless of error.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml, { fetchDelayMs = 0 } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' },
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
        window._fetchDelayMs = ${fetchDelayMs};
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            window._testFetchCalls.push({ eventName: eventName });
            if (window._fetchDelayMs > 0) {
                await new Promise(r => setTimeout(r, window._fetchDelayMs));
            }
            return { ok: true, json: async function() {
                return { patches: [], version: window._mockVersion };
            } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

describe('dj-form-pending', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('sets data-djust-form-pending on the form during submit', async () => {
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <button type="submit">Save</button>
                </form>
            </div>`,
            { fetchDelayMs: 50 },
        );
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        expect(form.hasAttribute('data-djust-form-pending')).toBe(false);

        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Synchronously after dispatch, before the fetch resolves, the form
        // should be marked pending.
        expect(form.getAttribute('data-djust-form-pending')).toBe('true');

        // Wait for the network round-trip to complete.
        await nativeSleep(200);

        // Pending state cleared on resolve.
        expect(form.hasAttribute('data-djust-form-pending')).toBe(false);
    });

    it('hides elements with dj-form-pending="hide" during submit', async () => {
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <button type="submit">
                        <span dj-form-pending="hide">Save</span>
                        <span dj-form-pending="show" hidden>Saving...</span>
                    </button>
                </form>
            </div>`,
            { fetchDelayMs: 50 },
        );
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        const idleLabel = dom.window.document.querySelector('[dj-form-pending="hide"]');
        const loadingLabel = dom.window.document.querySelector('[dj-form-pending="show"]');

        // Idle: idle visible, loading hidden
        expect(idleLabel.hasAttribute('hidden')).toBe(false);
        expect(loadingLabel.hasAttribute('hidden')).toBe(true);

        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Pending: idle hidden, loading visible
        expect(idleLabel.hasAttribute('hidden')).toBe(true);
        expect(loadingLabel.hasAttribute('hidden')).toBe(false);

        await nativeSleep(200);

        // Resolved: back to idle state
        expect(idleLabel.hasAttribute('hidden')).toBe(false);
        expect(loadingLabel.hasAttribute('hidden')).toBe(true);
    });

    it('disables form controls with dj-form-pending="disabled" during submit', async () => {
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <input name="email" dj-form-pending="disabled" value="test@example.com" />
                    <button type="submit">Save</button>
                </form>
            </div>`,
            { fetchDelayMs: 50 },
        );
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        const lockedInput = dom.window.document.querySelector('[dj-form-pending="disabled"]');

        expect(lockedInput.disabled).toBe(false);

        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Pending: input disabled
        expect(lockedInput.disabled).toBe(true);

        await nativeSleep(200);

        // Resolved: input re-enabled
        expect(lockedInput.disabled).toBe(false);
    });

    it('preserves originally-disabled state on dj-form-pending="disabled" elements', async () => {
        // If the user already disabled the input themselves (e.g. <input disabled>),
        // re-enabling on submit-resolve must NOT enable it. We track the
        // original disabled state in `data-djust-form-pending-was-disabled`
        // so the cleanup phase restores it correctly.
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <input name="locked" dj-form-pending="disabled" disabled value="x" />
                    <button type="submit">Save</button>
                </form>
            </div>`,
            { fetchDelayMs: 50 },
        );
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        const lockedInput = dom.window.document.querySelector('[dj-form-pending="disabled"]');

        expect(lockedInput.disabled).toBe(true); // user-disabled

        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Pending: input still disabled (no behavior change)
        expect(lockedInput.disabled).toBe(true);

        await nativeSleep(200);

        // Resolved: STILL disabled — we don't re-enable user-disabled elements.
        expect(lockedInput.disabled).toBe(true);
    });

    it('does not affect forms without dj-submit', () => {
        // Regression: only `dj-submit` forms should engage the pending state.
        // A plain HTML form unrelated to djust should be untouched.
        const dom = createTestEnv(
            `<form action="/plain">
                <input name="x" />
                <span dj-form-pending="hide">label</span>
            </form>`,
        );
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        const label = dom.window.document.querySelector('[dj-form-pending]');

        // Plain form submit: djust does nothing. Use preventDefault so the
        // test environment doesn't try to navigate.
        form.addEventListener('submit', e => e.preventDefault());
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // No djust state changes — `data-djust-form-pending` not set, label
        // not toggled.
        expect(form.hasAttribute('data-djust-form-pending')).toBe(false);
        expect(label.hasAttribute('hidden')).toBe(false);
    });

    it('only affects descendants of the submitting form (scope isolation)', async () => {
        // If the page has two `<form dj-submit>` forms, only the one being
        // submitted should engage pending state. Locks the scope contract.
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form id="form-a" dj-submit="save_a">
                    <input name="x" value="a" />
                    <span dj-form-pending="hide" id="label-a">A label</span>
                    <button type="submit">A</button>
                </form>
                <form id="form-b" dj-submit="save_b">
                    <input name="y" value="b" />
                    <span dj-form-pending="hide" id="label-b">B label</span>
                    <button type="submit">B</button>
                </form>
            </div>`,
            { fetchDelayMs: 50 },
        );
        initClient(dom);

        const formA = dom.window.document.getElementById('form-a');
        const formB = dom.window.document.getElementById('form-b');
        const labelA = dom.window.document.getElementById('label-a');
        const labelB = dom.window.document.getElementById('label-b');

        formA.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Only form A is pending. Form B is untouched.
        expect(formA.hasAttribute('data-djust-form-pending')).toBe(true);
        expect(formB.hasAttribute('data-djust-form-pending')).toBe(false);
        expect(labelA.hasAttribute('hidden')).toBe(true);
        expect(labelB.hasAttribute('hidden')).toBe(false);

        await nativeSleep(200);

        // Both forms idle.
        expect(formA.hasAttribute('data-djust-form-pending')).toBe(false);
        expect(labelA.hasAttribute('hidden')).toBe(false);
    });

    it('clears pending state on submit error', async () => {
        // If the fetch rejects (server error, network down), the pending
        // state must still clear — the `finally` block in _handleDjSubmit
        // is the load-bearing detail.
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <span dj-form-pending="hide" id="label">Save</span>
                    <button type="submit">Submit</button>
                </form>
            </div>`,
        );
        initClient(dom);

        // Override fetch to reject
        dom.window.fetch = async () => {
            throw new Error('network down');
        };

        const form = dom.window.document.querySelector('form');
        const label = dom.window.document.getElementById('label');

        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Synchronously: pending engaged
        expect(form.getAttribute('data-djust-form-pending')).toBe('true');
        expect(label.hasAttribute('hidden')).toBe(true);

        // After error propagates, finally block clears pending state
        await nativeSleep(100);

        expect(form.hasAttribute('data-djust-form-pending')).toBe(false);
        expect(label.hasAttribute('hidden')).toBe(false);
    });

    describe('WebSocket path (#1315)', () => {
        /**
         * On the WebSocket path, sendEvent returns a Promise that resolves
         * when the server's response (patch/noop/error) arrives. This keeps
         * the dj-form-pending state visible for the full network round-trip.
         *
         * Before #1315, sendEvent returned true synchronously, so
         * handleEvent resolved immediately and _setFormPending(false)
         * fired before any browser repaint — the user never saw it.
         */

        function createWSEnv(bodyHtml) {
            const dom = new JSDOM(
                `<!DOCTYPE html><html><body data-dj-view="app.FormView">${bodyHtml}</body></html>`,
                { runScripts: 'dangerously', url: 'http://localhost/' },
            );

            const sentMessages = [];
            dom.window.eval(`
                window.DJUST_USE_WEBSOCKET = true;
                window.WebSocket = class {
                    static get CONNECTING() { return 0; }
                    static get OPEN() { return 1; }
                    static get CLOSING() { return 2; }
                    static get CLOSED() { return 3; }
                    constructor() {
                        this.readyState = WebSocket.OPEN;
                        this.onopen = null;
                        this.onclose = null;
                        this.onmessage = null;
                        this.onerror = null;
                    }
                    send(data) {
                        window._testSentMessages.push(JSON.parse(data));
                    }
                    close() {}
                };
                window._testSentMessages = [];
                Object.defineProperty(document, 'hidden', {
                    value: false, writable: true, configurable: true
                });
            `);

            return dom;
        }

        it('sendEvent returns a Promise that resolves on server response', async () => {
            // Unit test: verify sendEvent returns a Promise (not true) and
            // that it resolves when handleMessage receives a matching ref.
            const dom = createWSEnv(
                `<form dj-submit="save"><input name="x"/></form>`,
            );
            dom.window.eval(clientCode);

            const ws = new dom.window.djust.LiveViewWebSocket();
            ws.enabled = true;
            ws.ws = new dom.window.WebSocket();
            ws.ws.readyState = dom.window.WebSocket.OPEN;
            ws.viewMounted = true;

            // #1315: sendEvent must return a Promise, not boolean
            const result = ws.sendEvent('save', { x: '1' });
            expect(result).toBeInstanceOf(dom.window.Promise);

            // The Promise should not resolve synchronously
            let resolved = false;
            result.then(() => { resolved = true; });
            await nativeSleep(10);
            expect(resolved).toBe(false);

            // Simulate server response with matching ref
            await ws.handleMessage({ type: 'noop', ref: 1 });

            // Now the Promise should be resolved
            await nativeSleep(10);
            expect(resolved).toBe(true);
        });

        it('sendEvent returns false when WS is down (backward compat)', () => {
            const dom = createWSEnv('<form dj-submit="save"><input name="x"/></form>');
            dom.window.eval(clientCode);

            const ws = new dom.window.djust.LiveViewWebSocket();
            ws.enabled = false;  // simulate WS disabled

            // When WS is down, sendEvent returns false (not a Promise),
            // so callers can fall back to HTTP.
            const result = ws.sendEvent('save', {});
            expect(result).toBe(false);
        });
    });

    it('ignores unknown dj-form-pending modes (forward-compat)', async () => {
        // Future-proof: an unrecognized mode like "future-mode" must be
        // silently ignored, not crash the submit handler.
        const dom = createTestEnv(
            `<div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <span dj-form-pending="future-mode" id="span">x</span>
                    <button type="submit">Save</button>
                </form>
            </div>`,
            { fetchDelayMs: 50 },
        );
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        const span = dom.window.document.getElementById('span');

        // Should not throw
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        // Form pending state still engages (only the mode handler is a no-op)
        expect(form.getAttribute('data-djust-form-pending')).toBe('true');
        // Span unchanged
        expect(span.hasAttribute('hidden')).toBe(false);

        await nativeSleep(200);

        expect(form.hasAttribute('data-djust-form-pending')).toBe(false);
    });
});
