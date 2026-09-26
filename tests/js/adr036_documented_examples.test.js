/**
 * ADR-036 P3: the events guide's strict examples, driven through the real
 * bundle. Each documented HTML block is mounted under the contract its
 * documented Python signature produces, and the payload the browser sends is
 * the one python/djust/tests/doc_scenarios/adr036.py (strict-policy) then
 * dispatches to the documented handler.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const guide = readFileSync('./docs/website/core-concepts/events.md', 'utf8');
const section = guide.split('## Typed event parameters (strict policy)')[1].split('\n## ')[0];
const htmlBlocks = [...section.matchAll(/```html\n([\s\S]*?)\n```/g)].map(m => m[1]);

const param = (name, type, kind = 'positional_or_keyword', required = true) =>
    ({name, type, kind, required, reduced_checking: type === 'Any'});
// The public contracts of the documented signatures.
const handlers = {
    select_item: {policy: 'strict', coerce_types: true,
        parameters: [param('item_id', 'int'), param('active', 'bool', 'positional_or_keyword', false)]},
    search: {policy: 'strict', coerce_types: true, parameters: [param('value', 'str')]},
    save: {policy: 'strict', coerce_types: true,
        parameters: [param('title', 'str'), param('fields', 'str', 'var_keyword', false)]},
};

function mount(html) {
    const contracts = {version: 1, owners: [{view_id: null, component_id: null, handlers}]};
    const dom = new JSDOM(`<!DOCTYPE html><html><body><div dj-view="docs.Example">${html}</div>
        <script type="application/json" data-djust-parameter-contracts>${JSON.stringify({view: 'docs.Example', contracts})}</script>
        </body></html>`, {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window._sent = [];
        window.fetch = async (url, opts) => {
            window._sent.push([opts.headers['X-Djust-Event'], JSON.parse(opts.body)]);
            return {ok: true, json: async () => ({patches: [], parameter_contracts: ${JSON.stringify(contracts)},
                parameter_contract_view: 'docs.Example'})};
        };
    `);
    dom.window.eval(source);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

const settle = ms => new Promise(resolve => setTimeout(resolve, ms));

describe('documented strict examples', () => {
    it('finds both HTML examples in the guide', () => {
        expect(htmlBlocks).toHaveLength(2);
    });

    it('typed click arguments: sends the dj-value-* arguments', async () => {
        const dom = mount(htmlBlocks[0]);
        try {
            dom.window.document.querySelector('button').dispatchEvent(
                new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
            await settle(20);
            expect(dom.window._sent).toEqual([['select_item', {item_id: '42', active: 'true'}]]);
        } finally { dom.window.close(); }
    });

    it('inputs and forms: sends only declared generated values', async () => {
        const dom = mount(htmlBlocks[1]);
        try {
            const input = dom.window.document.querySelector('input[name="q"]');
            input.value = 'abc';
            input.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
            await settle(400);
            dom.window.document.querySelector('form').dispatchEvent(
                new dom.window.Event('submit', {bubbles: true, cancelable: true}));
            await settle(20);
            expect(dom.window._sent).toEqual([
                ['search', {value: 'abc'}],
                ['save', {title: 'Draft', notes: 'Hello'}],
            ]);
        } finally { dom.window.close(); }
    });
});
