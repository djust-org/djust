/** ADR-036: initial-page and HTTP-fallback contract scope, and binder owner resolution. */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { packManifest } from './_parameter_manifest.js';
// The raw bundle: the seam below is a literal source line.
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const seam = 'window.djust._installPageParameterContracts = _installPageParameterContracts;';
if (source.split(seam).length !== 2) throw new Error('Missing page contract test seam');

const strict = {policy: 'strict', coerce_types: true, parameters: [
    {name: 'value', type: 'int', kind: 'positional_or_keyword', required: true, reduced_checking: false},
]};
const other = {policy: 'strict', coerce_types: true, parameters: [
    {name: 'other', type: 'str', kind: 'positional_or_keyword', required: true, reduced_checking: false},
]};
const owners = () => ({version: 1, owners: [
    {view_id: null, component_id: null, handlers: {choose: strict, plain: other}},
    {view_id: null, component_id: 'menu', handlers: {choose: other}},
    {view_id: 'child', component_id: null, handlers: {choose: strict}},
]});
const manifest = () => packManifest(owners());
const block = contracts =>
    `<script type="application/json" data-djust-parameter-contracts>${JSON.stringify(contracts)}</script>`;
const page = `<div dj-root dj-view="app.Page">
    <button id="root" dj-click="choose"></button>
    <div data-component-id="menu"><button id="component" dj-click="choose"></button>
        <div data-djust-embedded="child"><button id="child" dj-click="choose"></button></div>
    </div>
</div>`;

function setup(extra = '', responses = []) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${page}${extra}</body></html>`,
        {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._fetches = [];
        window._responses = ${JSON.stringify(responses)};
        window.fetch = async function(url, opts) {
            window._fetches.push(opts.headers);
            const body = window._responses.shift() || {patches: []};
            return {ok: true, json: async () => body};
        };
    `);
    dom.window.eval(source.replace(seam, seam +
        '\nwindow.resolveContract = _resolveParameterContract;\nwindow.pageTransport = _localEventTransport;' +
        '\nwindow.setSocket = socket => { liveViewWS = socket; };'));
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    const resolve = (id, event = 'choose') =>
        dom.window.resolveContract(dom.window.document.getElementById(id), event);
    return {dom, resolve};
}

describe('initial page contract scope', () => {
    it('installs the rendered root contracts and resolves owners like server routing', () => {
        const {dom, resolve} = setup(block({view: 'app.Page', contracts: manifest()}));
        try {
            expect(Array.isArray(resolve('root').contract.parameters)).toBe(true);
            expect(resolve('root').contract.parameters[0].name).toBe('value');
            expect(resolve('root', 'plain').contract.parameters[0].name).toBe('other');
            expect(resolve('component').contract.parameters[0].name).toBe('other');
            // An embedded child's view_id wins over an enclosing component.
            expect(resolve('child')).toMatchObject({viewId: 'child', componentId: null});
            expect(Array.isArray(resolve('child').contract.parameters)).toBe(true);
            // Not declared by this mount: declares nothing, the server validates it.
            expect(resolve('root', 'misspelled').contract).toBeNull();
            expect(resolve('root').transport).toBe(dom.window.pageTransport);
        } finally { dom.window.close(); }
    });

    it('treats a page without a contract block as a tree with no handlers', () => {
        const {dom, resolve} = setup();
        try {
            expect(resolve('root').contract).toBeNull();
        } finally { dom.window.close(); }
    });

    it('leaves another view\'s block unknown instead of applying stale rules', () => {
        const {dom, resolve} = setup(block({view: 'app.Other', contracts: manifest()}));
        try {
            expect(resolve('root').contract).toBeNull();
        } finally { dom.window.close(); }
    });

    it.each([
        ['discovery failure', block({view: 'app.Page', contracts: false})],
        ['malformed manifest', block({view: 'app.Page', contracts: {version: 9, owners: []}})],
        ['unparseable block', '<script type="application/json" data-djust-parameter-contracts>{</script>'],
    ])('fails closed on %s', (_label, html) => {
        const {dom, resolve} = setup(html);
        try {
            expect(() => resolve('root')).toThrow('Invalid public parameter contracts');
        } finally { dom.window.close(); }
    });
});

describe('HTTP fallback contract scope', () => {
    const send = async dom => {
        await dom.window.djust.handleEvent('plain', {});
    };

    it('asks for explicit clears only while its scope is strict, and applies them', async () => {
        const cleared = {patches: [], parameter_contracts: null, parameter_contract_view: 'app.Page'};
        const {dom, resolve} = setup(block({view: 'app.Page', contracts: manifest()}), [cleared]);
        try {
            await send(dom);
            expect(dom.window._fetches[0]['X-Djust-Parameter-Contracts']).toBe('1');
            expect(resolve('root').contract).toBeNull();
            await send(dom);
            expect(dom.window._fetches[1]['X-Djust-Parameter-Contracts']).toBeUndefined();
        } finally { dom.window.close(); }
    });

    it('never sends the header from a page with no handlers', async () => {
        const {dom} = setup();
        try {
            await send(dom);
            expect(dom.window._fetches[0]['X-Djust-Parameter-Contracts']).toBeUndefined();
        } finally { dom.window.close(); }
    });

    it('installs a render snapshot delivered with the DOM update', async () => {
        const raw = owners();
        raw.owners[0].handlers.plain = strict;
        const replaced = packManifest(raw);
        const {dom, resolve} = setup(block({view: 'app.Page', contracts: manifest()}),
            [{patches: [], parameter_contracts: replaced, parameter_contract_view: 'app.Page'}]);
        try {
            expect(resolve('root', 'plain').contract.parameters[0].name).toBe('other');
            await send(dom);
            expect(resolve('root', 'plain').contract.parameters[0].name).toBe('value');
        } finally { dom.window.close(); }
    });

    it('invalidates a strict scope when a render response omits its snapshot', async () => {
        const {dom, resolve} = setup(block({view: 'app.Page', contracts: manifest()}), [{patches: []}]);
        try {
            await send(dom);
            expect(() => resolve('root')).toThrow('Invalid public parameter contracts');
        } finally { dom.window.close(); }
    });
});

describe('transport selection', () => {
    it('resolves through a mounted socket rather than the page scope', () => {
        const {dom, resolve} = setup(block({view: 'app.Page', contracts: manifest()}));
        try {
            dom.window.eval(`
                window.setSocket({enabled: true, viewMounted: true, ws: {readyState: WebSocket.OPEN},
                    _parameterContracts: new Map([['app.Page', null]])});
            `);
            const resolved = resolve('root');
            expect(resolved.contract).toBeNull();
            expect(resolved.transport).not.toBe(dom.window.pageTransport);
        } finally { dom.window.close(); }
    });
});
