/**
 * #4285: a framework-stamped identity key (data-djust-optional) is sent to a
 * strict handler only when it declares the key or has a ** catch-all.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { packManifest } from './_parameter_manifest.js';
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');


const param = (name, type, kind = 'positional_or_keyword', required = true) =>
    ({name, type, kind, required, reduced_checking: type === 'Any'});
const strict = (...parameters) => ({policy: 'strict', coerce_types: true, parameters});
const manifest = packManifest({version: 1, owners: [{view_id: null, component_id: null, handlers: {
    only_value: strict(param('value', 'str')),
    with_name: strict(param('value', 'str'), param('name', 'str', 'positional_or_keyword', false)),
    open_kw: strict(param('value', 'str'), param('rest', 'Any', 'var_keyword', false)),
}}]});

function setup(body, contracts = manifest) {
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
    dom.window.eval(source);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    const $ = selector => dom.window.document.querySelector(selector);
    // dj-input is debounced by default; wait past the default window.
    const settle = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
    return {dom, $, settle, sent: () => dom.window._sent, errors: () => dom.window._errors};
}


const click = (dom, el) => el.dispatchEvent(new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
const button = (event, extra = '') =>
    `<button dj-click="${event}" dj-value-name="status" dj-value-value="done"${extra}>Go</button>`;
const MARK = ' data-djust-optional="name"';

async function sentFor(html) {
    const {dom, $, settle, sent, errors} = setup(html);
    try {
        click(dom, $('button'));
        await settle();
        return {sent: sent(), errors: errors()};
    } finally { dom.window.close(); }
}

describe('framework-marked optional identity (#4285)', () => {
    it('omits name when the handler does not declare it', async () => {
        const r = await sentFor(button('only_value', MARK));
        expect(r.sent).toEqual([{event: 'only_value', params: {value: 'done'}}]);
    });
    it('sends name when the handler declares it', async () => {
        const r = await sentFor(button('with_name', MARK));
        expect(r.sent[0].params).toEqual({name: 'status', value: 'done'});
    });
    it('sends name to a ** catch-all', async () => {
        const r = await sentFor(button('open_kw', MARK));
        expect(r.sent[0].params).toEqual({name: 'status', value: 'done'});
    });
    it('control: an unmarked hand-written name is still sent (and refused by the server)', async () => {
        const r = await sentFor(button('only_value'));
        expect(r.sent[0].params).toEqual({name: 'status', value: 'done'});
    });
    it('a marked key that is declared still has its hint checked', async () => {
        const r = await sentFor(
            `<button dj-click="with_name" dj-value-name:int="3" dj-value-value="done"${MARK}>Go</button>`);
        expect(r.sent).toEqual([]);
        expect(r.errors.length).toBe(1);
    });
});
