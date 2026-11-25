/**
 * #4186: the framework's own markup passes event arguments as dj-value-*, so a
 * strict handler (ADR-036) receives them.
 *
 * The elements come from tests/js/fixtures/framework_dj_value_4186.json, which
 * python/djust/tests/test_framework_dj_value_4186.py pins to what the framework
 * renders now. Each one is fired through the bundled client, as a browser would,
 * under a strict contract and no contract (a lazy or unmounted root, where
 * the client sends the dj-value-* arguments only): the arguments the Python
 * side binds to the handler are exactly what the client sends.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { packManifest } from './_parameter_manifest.js';
const { readFileSync } = await vi.importActual('node:fs');

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const dataTableScript = readFileSync(
    './python/djust/components/static/djust_components/data-table.js', 'utf8');
const fixture = JSON.parse(readFileSync('./tests/js/fixtures/framework_dj_value_4186.json', 'utf8'));

const param = (name, type = 'str', kind = 'positional_or_keyword') =>
    ({name, type, kind, required: kind !== 'var_keyword', reduced_checking: type === 'Any'});
const strict = names => ({policy: 'strict', coerce_types: true, parameters: names.map(n => param(n))});

function setup(body, handlers) {
    const contracts = handlers === undefined ? undefined :
        packManifest({version: 1, owners: [{view_id: null, component_id: null, handlers}]});
    const block = contracts === undefined ? '' :
        `<script type="application/json" data-djust-parameter-contracts>${JSON.stringify({view: 'app.Page', contracts})}</script>`;
    const dom = new JSDOM(`<!DOCTYPE html><html><body><div dj-view="app.Page">${body}</div>${block}</body></html>`,
        {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._sent = [];
        window._errors = [];
        window.addEventListener('djust:error', e => window._errors.push(e.detail));
        window.fetch = async function(url, opts) {
            window._sent.push({event: opts.headers['X-Djust-Event'], params: JSON.parse(opts.body)});
            return {ok: true, json: async () => ({patches: [], parameter_contracts: ${JSON.stringify(contracts ?? null)},
                parameter_contract_view: 'app.Page'})};
        };
    `);
    dom.window.console.error = () => {};
    dom.window.eval(client);
    return dom;
}

// The element, built from its attributes: a <th> or <tr> outside a table is
// still a real element to the DOM API (the HTML parser would drop it).
function place(dom, entry) {
    const el = dom.window.document.createElement(entry.tag);
    for (const [name, value] of entry.attrs) el.setAttribute(name, value);
    if (entry.tag === 'input') el.setAttribute('name', 'filter');
    dom.window.document.querySelector('[dj-view]').appendChild(el);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return el;
}

function fire(dom, el, entry) {
    if (entry.form_value !== undefined) {
        el.value = entry.form_value;
        el.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
    } else {
        el.dispatchEvent(new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
    }
}

const settle = (ms = 400) => new Promise(resolve => setTimeout(resolve, ms));
const sent = dom => dom.window._sent;
// What the client generates itself for a form-field binding (value, field).
const expected = entry => entry.form_value === undefined ? entry.params :
    {...entry.params, value: entry.form_value};

describe('framework elements send their dj-value-* arguments', () => {
    it('covers every component family in the fixture', () => {
        expect(fixture.elements.length).toBeGreaterThanOrEqual(14);
        for (const entry of fixture.elements) {
            expect(entry.attrs.some(([n]) => n.startsWith('dj-value-')), entry.id).toBe(true);
        }
    });

    it.each(fixture.elements.map(e => [e.id, e]))('%s: a strict handler receives exactly its arguments', async (_id, entry) => {
        const declared = Object.keys(expected(entry));
        const dom = setup('', {[entry.event]: strict(declared)});
        try {
            fire(dom, place(dom, entry), entry);
            await settle();
            expect(dom.window._errors).toEqual([]);
            expect(sent(dom)).toEqual([{event: entry.event, params: expected(entry)}]);
        } finally { dom.window.close(); }
    });

    it.each(fixture.elements.map(e => [e.id, e]))('%s: with no contract the client sends its dj-value-* arguments only', async (_id, entry) => {
        const dom = setup('', undefined);
        try {
            fire(dom, place(dom, entry), entry);
            await settle();
            // A framework-marked optional key (a component's identity `name`) is
            // sent only when the handler declares it.
            const optional = ((entry.attrs.find(([n]) => n === 'data-djust-optional') || [])[1] || '').split(/\s+/);
            const params = Object.fromEntries(Object.entries(entry.params).filter(([k]) => !optional.includes(k)));
            expect(sent(dom)).toEqual([{event: entry.event, params}]);
        } finally { dom.window.close(); }
    });
});

describe('a data-table trigger filled by data-table.js', () => {
    const page = fixture.pages.find(p => p.id === 'data_table.visibility');

    async function toggleRoleColumn(handlers) {
        const dom = setup(page.html, handlers);
        dom.window.eval(dataTableScript);
        dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
        const box = dom.window.document.querySelector(".data-table-visibility-menu input[data-col-key='role']");
        box.checked = false;
        box.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
        await settle();
        return dom;
    }

    it('reaches a strict on_table_visibility as value', async () => {
        const dom = await toggleRoleColumn({
            on_table_visibility: {policy: 'strict', coerce_types: true,
                parameters: [param('value', 'Any'), param('kwargs', 'Any', 'var_keyword')]},
        });
        try {
            expect(sent(dom)).toEqual([{event: 'on_table_visibility', params: {value: 'name'}}]);
        } finally { dom.window.close(); }
    });
});
