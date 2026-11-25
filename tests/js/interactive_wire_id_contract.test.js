/** #4046: an interactive component's tagged wire id (`cmp_<id>.<tag>`) resolves its
 * parameter contract, and its dot never reaches a selector. */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const seam = 'window.djust._installPageParameterContracts = _installPageParameterContracts;';
if (source.split(seam).length !== 2) throw new Error('Missing page contract test seam');

// The shape the server renders: data-component-id, popover id and manifest key
// all carry the same tagged id (python/djust/_component_state.py wire_id).
const WIRE = 'cmp_' + 'a'.repeat(32) + '.' + '0123456789abcdef'.repeat(2);
const select = {policy: 'strict', coerce_types: false, parameters: [
    {name: 'value', type: 'str', kind: 'positional_or_keyword', required: true, reduced_checking: false},
]};
const contracts = {view: 'app.Page', contracts: {version: 1, owners: [
    {view_id: null, component_id: null, handlers: {}},
    {view_id: null, component_id: WIRE, handlers: {select, toggle: {policy: 'strict', coerce_types: false, parameters: []}}},
]}};
const page = `<div dj-root dj-view="app.Page">
  <div class="dj-dropdown-menu" data-component-id="${WIRE}">
    <button id="trigger" type="button" popovertarget="popover-${WIRE}">Help</button>
    <div id="popover-${WIRE}" popover="auto"><button id="item" type="button" dj-click="select" dj-value-value="edit">Edit</button></div>
    <button id="toggle" type="button" dj-click="toggle">T</button>
  </div>
</div>
<script type="application/json" data-djust-parameter-contracts>${JSON.stringify(contracts)}</script>`;

function setup() {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${page}</body></html>`,
        {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._sent = [];
        window.fetch = async function(url, opts) {
            window._sent.push({headers: opts.headers, body: JSON.parse(opts.body)});
            return {ok: true, status: 200, json: async () => ({patches: []})};
        };
    `);
    dom.window.eval(source.replace(seam, seam + '\nwindow.resolveContract = _resolveParameterContract;'));
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

describe('interactive component wire id (#4046)', () => {
    it('resolves the strict contract the manifest keys by the wire id', () => {
        const dom = setup();
        try {
            const doc = dom.window.document;
            const item = dom.window.resolveContract(doc.getElementById('item'), 'select');
            expect(item.contract.policy).toBe('strict');
            expect(item.componentId).toBe(WIRE);
            expect(dom.window.resolveContract(doc.getElementById('toggle'), 'toggle').contract.policy).toBe('strict');
        } finally { dom.window.close(); }
    });

    it('sends the wire id unchanged and finds the popover by its dotted id', async () => {
        const dom = setup();
        try {
            const doc = dom.window.document;
            // getElementById / popovertarget take the id literally: no selector parsing.
            expect(doc.getElementById('popover-' + WIRE)).not.toBeNull();
            // A selector built from the id must escape the dot.
            expect(doc.querySelector('[data-component-id="' + WIRE + '"]')).not.toBeNull();
            expect(doc.querySelector('#' + dom.window.CSS.escape('popover-' + WIRE))).not.toBeNull();
            doc.getElementById('item').dispatchEvent(
                new dom.window.MouseEvent('click', {bubbles: true, cancelable: true}));
            await new Promise(r => setTimeout(r, 30));
            const sent = dom.window._sent.map(s => s.body);
            expect(sent.length).toBe(1);
            expect(sent[0].component_id).toBe(WIRE);
            expect(sent[0].value).toBe('edit');
        } finally { dom.window.close(); }
    });
});
