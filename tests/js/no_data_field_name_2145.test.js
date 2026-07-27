/**
 * #2145 — the form-event path does not read `data-*`.
 *
 * `frameworks.py` used to emit `data-field_name="<name>"` on every rendered
 * form field, next to `dj-change="validate_field"`, commented "so event
 * handler knows which field changed". Nothing read it. These tests are the
 * executable record of why, so the next person does not have to re-derive it
 * (and so the claim goes red if the client ever changes).
 *
 * The chain:
 *   - `buildFormEventParams` (09-event-binding.js:525) — used by dj-change /
 *     dj-input / dj-blur / dj-focus — hardcodes the key `field`, sources it
 *     from `getFieldName` (`data-field` -> `name` -> `id`), and merges only
 *     `dj-value-*`. It never touches `data-*`.
 *   - `extractTypedParams` (08-event-parsing.js:248) is the function that DOES
 *     collect `data-*`, and it runs on dj-click / dj-poll / dj-mounted /
 *     dj-click-away / dj-shortcut / dj-window-* / dj-document-* only.
 *   - `frameworks.py` emits none of those, so the collector never ran on the
 *     element carrying the attribute.
 *
 * Case 3 below is the contrast that makes the trap legible: the SAME attribute
 * on an element bound with `dj-click` DOES reach the handler. The attribute was
 * never the mechanism — the directive is.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
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

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function findCall(dom, eventName) {
    return dom.window._testFetchCalls.find(c => c.eventName === eventName);
}

describe('#2145 data-field_name is unreadable on the form-event path', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('post-fix markup: the field name arrives as `field`, sourced from `name`', async () => {
        // Exactly what frameworks.py `_render_input` emits after #2145.
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input name="email" id="id_email" dj-change="validate_field" type="email" value="" />
            </div>
        `);

        const input = dom.window.document.querySelector('input[name="email"]');
        input.value = 'new@example.com';
        input.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
        await nativeSleep(200);

        const call = findCall(dom, 'validate_field');
        expect(call).toBeDefined();
        expect(call.body.field).toBe('email');
        expect(call.body.value).toBe('new@example.com');
        // `getFieldName` fell through `data-field` (absent) to `name`.
        expect(call.body.field_name).toBeUndefined();
    });

    it('re-adding data-field_name changes nothing — dj-change never reads data-*', async () => {
        // Byte-for-byte the pre-#2145 markup. If `buildFormEventParams` ever
        // started merging data-*, this goes red and the deletion's premise is
        // no longer true.
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input name="email" id="id_email" dj-change="validate_field"
                       data-field_name="email" type="email" value="" />
            </div>
        `);

        const input = dom.window.document.querySelector('input[name="email"]');
        input.value = 'new@example.com';
        input.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
        await nativeSleep(200);

        const call = findCall(dom, 'validate_field');
        expect(call).toBeDefined();
        expect(call.body.field_name).toBeUndefined();
        // The name still arrives — via `name`, as it always did.
        expect(call.body.field).toBe('email');
    });

    it('the same attribute DOES reach the handler under dj-click', async () => {
        // The contrast that explains why the attribute looked load-bearing:
        // `extractTypedParams` collects every data-* on the click path. This is
        // the escape hatch a caller can still open by passing a click-family
        // `dom_event=` or by putting a directive in `widget.attrs`.
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <input name="email" id="id_email" dj-click="validate_field"
                       data-field_name="email" type="email" value="" />
            </div>
        `);

        const input = dom.window.document.querySelector('input[name="email"]');
        input.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
        await nativeSleep(200);

        const call = findCall(dom, 'validate_field');
        expect(call).toBeDefined();
        expect(call.body.field_name).toBe('email');
        // ...and note what it does NOT carry: the click path has no `value`
        // and no `field`, so this was never a working substitute.
        expect(call.body.field).toBeUndefined();
    });
});
