/**
 * #2781 — `TableComponent`'s selectable checkboxes must carry row identity as
 * `dj-value-row-id`, never `data-row-id`.
 *
 * Symptom-up (see the Playwright regression at
 * `tests/playwright/test_table_select.py` for the full browser trace):
 * checking many rows failed, unchecking a row never cleared the header, and
 * checking every row never set it. The server side (`toggle_row`,
 * `toggle_all`, `_all_selected()`) was already correct and covered by
 * `python/djust/tests/test_table_selection_2779.py`'s WebSocket tests — the
 * break was entirely in what left the browser.
 *
 * Mechanism: `dj-change` (and `dj-input`/`dj-blur`/`dj-focus`) run
 * `buildFormEventParams` (09-event-binding.js:525), which sends only
 * `value`, `field`, `component_id`/`view_id` (`addEventContext`) and the
 * element's `dj-value-*` (`collectDjValues`) — it never reads `data-*`.
 * Only `dj-click` runs `extractTypedParams` (08-event-parsing.js:248), which
 * DOES collect `data-*`. The first cut of the row checkbox rendered
 * `dj-change="toggle_row" data-component-id="…" data-row-id="7"`: every
 * click reached the server as `toggle_row(row_id="")` — a defaulted, empty
 * identity — so `selected_rows` only ever grew/shrank the empty string,
 * `_all_selected()` (which requires every REAL row id present) could never
 * be satisfied, and unchecking a row that never registered could never
 * clear the header. This is the same trap
 * `tests/js/no_data_field_name_2145.test.js` already documents for a
 * different component; this file is the table-checkbox instance of it.
 *
 * The fix moved the identity to `dj-value-row-id`, which `collectDjValues`
 * DOES merge into `dj-change` params. No client code changed — the bug was
 * entirely in which attribute the component authored, not in the client's
 * contract, so this file is a characterization test of the CLIENT contract,
 * pinning the exact behavior the fix now relies on.
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
    return dom.window._testFetchCalls.find((c) => c.eventName === eventName);
}

describe('#2781 TableComponent row checkbox identity must ride dj-value-*', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('post-fix markup: row_id arrives via dj-value-row-id on dj-change', async () => {
        // The current TableComponent shape (python/djust/components/data/table.py
        // `_row_checkbox_attr`, post-#2781).
        const dom = createTestEnv(`
            <div dj-view="app.TableView">
                <input type="checkbox" dj-change="toggle_row"
                       data-component-id="tablecomponent_1"
                       dj-value-row-id="7" aria-label="Select row" />
            </div>
        `);

        const checkbox = dom.window.document.querySelector('input[type="checkbox"]');
        checkbox.checked = true;
        checkbox.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
        await nativeSleep(200);

        const call = findCall(dom, 'toggle_row');
        expect(call).toBeDefined();
        expect(call.body.row_id).toBe('7');
        expect(call.body.component_id).toBe('tablecomponent_1');
        expect(call.body.value).toBe(true);
    });

    it('the pre-#2781 shape (data-row-id) never reaches the handler — the exact regression', async () => {
        // Byte-for-byte the pre-fix markup. If `buildFormEventParams` ever
        // started merging data-*, this goes red and the bug's premise no
        // longer holds — same discipline as #2145's contrast test.
        const dom = createTestEnv(`
            <div dj-view="app.TableView">
                <input type="checkbox" dj-change="toggle_row"
                       data-component-id="tablecomponent_1"
                       data-row-id="7" aria-label="Select row" />
            </div>
        `);

        const checkbox = dom.window.document.querySelector('input[type="checkbox"]');
        checkbox.checked = true;
        checkbox.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
        await nativeSleep(200);

        const call = findCall(dom, 'toggle_row');
        expect(call).toBeDefined();
        expect(call.body.row_id).toBeUndefined();
        expect(call.body.component_id).toBe('tablecomponent_1');
        // The server-side handler defaults `row_id=""` for this exact
        // payload — see toggle_row's signature in table.py.
    });

    it('the same data-row-id DOES reach a dj-click handler (the contrast)', async () => {
        // Explains why data-row-id looked plausible: it works for dj-click,
        // just not for the dj-change the checkbox actually uses.
        const dom = createTestEnv(`
            <div dj-view="app.TableView">
                <button dj-click="toggle_row" data-component-id="tablecomponent_1"
                        data-row-id="7">Select</button>
            </div>
        `);

        const button = dom.window.document.querySelector('button');
        button.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
        await nativeSleep(200);

        const call = findCall(dom, 'toggle_row');
        expect(call).toBeDefined();
        expect(call.body.row_id).toBe('7');
    });
});
