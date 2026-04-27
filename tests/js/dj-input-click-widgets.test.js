/**
 * Tests for dj-input rate-limiting defaults on click-fired widgets.
 *
 * Issue #1154: DEFAULT_RATE_LIMITS was missing entries for radio /
 * checkbox / select-one / select-multiple. The fallback
 * (`{ type: 'debounce', ms: 300 }`) applied to anything not in the
 * map, so `wizard_input_event = "dj-input"` — recommended by #1095 —
 * silently introduced a 300ms delay between a radio click and the
 * WS event being sent.
 *
 * These tests lock in the fix: click-fired widgets fire their handler
 * synchronously (passthrough), while text fields keep their 300ms
 * debounce.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

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

function getFetchCalls(dom) {
    return dom.window._testFetchCalls;
}

describe('dj-input on click-fired widgets: passthrough (no debounce)', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('radio input fires synchronously, not after 300ms', () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="radio" name="choice" value="yes"
                       dj-input="validate_field" />
                <input type="radio" name="choice" value="no"
                       dj-input="validate_field" />
            </div>
        `);
        initClient(dom);

        const yesRadio = dom.window.document.querySelector('input[value="yes"]');
        yesRadio.checked = true;
        yesRadio.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // Radios must fire on the next microtask, not after 300ms.
        // Without the fix, the call would not register until the timers advance.
        expect(getFetchCalls(dom).length).toBe(1);
        expect(getFetchCalls(dom)[0].eventName).toBe('validate_field');
    });

    it('checkbox fires synchronously', () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="checkbox" name="agree"
                       dj-input="set_agreed" />
            </div>
        `);
        initClient(dom);

        const cb = dom.window.document.querySelector('input[type="checkbox"]');
        cb.checked = true;
        cb.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        expect(getFetchCalls(dom).length).toBe(1);
        expect(getFetchCalls(dom)[0].eventName).toBe('set_agreed');
    });

    it('select-one fires synchronously', () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <select name="borough" dj-input="validate_field">
                    <option value="">--</option>
                    <option value="brooklyn">Brooklyn</option>
                    <option value="queens">Queens</option>
                </select>
            </div>
        `);
        initClient(dom);

        const sel = dom.window.document.querySelector('select');
        sel.value = 'brooklyn';
        sel.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        expect(getFetchCalls(dom).length).toBe(1);
        expect(getFetchCalls(dom)[0].eventName).toBe('validate_field');
    });

    it('text input still debounces 300ms (unchanged behavior)', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="text" name="first_name"
                       dj-input="validate_field" />
            </div>
        `);
        initClient(dom);

        const inp = dom.window.document.querySelector('input[type="text"]');
        inp.value = 'a';
        inp.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // Must NOT fire immediately
        expect(getFetchCalls(dom).length).toBe(0);

        await vi.advanceTimersByTimeAsync(200);
        expect(getFetchCalls(dom).length).toBe(0);

        await vi.advanceTimersByTimeAsync(200);
        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('textarea still debounces 300ms', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <textarea name="notes" dj-input="validate_field"></textarea>
            </div>
        `);
        initClient(dom);

        const ta = dom.window.document.querySelector('textarea');
        ta.value = 'hello';
        ta.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        expect(getFetchCalls(dom).length).toBe(0);

        await vi.advanceTimersByTimeAsync(400);
        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('rapid radio clicks each produce an event (no debounce coalescing)', () => {
        // Users can flip Yes/No multiple times; each flip must register.
        // Before the fix, only the last of a burst would send after 300ms idle.
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="radio" name="choice" value="yes"
                       dj-input="validate_field" />
                <input type="radio" name="choice" value="no"
                       dj-input="validate_field" />
            </div>
        `);
        initClient(dom);

        const yes = dom.window.document.querySelector('input[value="yes"]');
        const no = dom.window.document.querySelector('input[value="no"]');

        yes.checked = true;
        yes.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        no.checked = true;
        no.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        yes.checked = true;
        yes.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // All three clicks dispatched: no timer coalescing.
        expect(getFetchCalls(dom).length).toBe(3);
    });

    it('dj-debounce override still applies to a radio if set explicitly', async () => {
        // A user who explicitly opts into debouncing on a radio should
        // still get it. Passthrough is the default, not an override.
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input type="radio" name="choice" value="yes"
                       dj-input="validate_field" dj-debounce="200" />
            </div>
        `);
        initClient(dom);

        const yes = dom.window.document.querySelector('input[value="yes"]');
        yes.checked = true;
        yes.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        expect(getFetchCalls(dom).length).toBe(0);
        await vi.advanceTimersByTimeAsync(250);
        expect(getFetchCalls(dom).length).toBe(1);
    });
});
