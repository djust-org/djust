/**
 * #4307: the client installs wire-v2 parameter-contract manifests (each
 * distinct contract and handler table once, owners name their table) and
 * resolves a manifest the server sends as its digest from a small
 * content-addressed cache. A digest it never received fails closed.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { packManifest } from './_parameter_manifest.js';
// The raw bundle: the seams below are literal source lines.
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const seam = 'window.djust.collectDjValues = collectDjValues;';
const pageSeam = 'window.djust._installPageParameterContracts = _installPageParameterContracts;';
for (const marker of [seam, pageSeam]) {
    if (source.split(marker).length !== 2) throw new Error('Missing manifest test seam');
}

const contract = (name, type = 'str') => ({policy: 'strict', coerce_types: true, parameters: [
    {name, type, kind: 'positional_or_keyword', required: true, reduced_checking: false},
]});
const rows = (count, type = 'str') => packManifest({version: 1, owners: [
    {view_id: null, component_id: null, handlers: {note: contract('kwargs')}},
    ...Array.from({length: count}, (_, i) => ({view_id: null, component_id: `row-${i}`,
        handlers: {select: contract('value', type), toggle: contract('open', 'bool')}})),
]});

function setup() {
    const dom = new JSDOM('<div dj-root dj-view="app.Page"><span>old</span></div>',
        {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.CSS ??= {};
    dom.window.CSS.escape ??= value => value;
    dom.window.eval(source.replace(seam, seam + `
        window.t = {
            install: _installParameterContracts, lookup: _lookupParameterContract,
            receive: _recordParameterContractFrame, refresh: _refreshRenderParameterContracts,
        };`));
    const transport = {primaryViewPath: 'app.Page'};
    const t = dom.window.t;
    const lookup = (component, event) => t.lookup(transport, 'app.Page', null, component, event);
    // A frame through receipt, then its DOM application's refresh.
    const receive = data => { t.receive(transport, data); return data; };
    const render = manifest => receive({type: 'patch', patches: [], parameter_contracts: manifest,
        parameter_contract_view: 'app.Page'});
    return {dom, t, transport, lookup, receive, render};
}

describe('wire v2 install', () => {
    it('resolves every row of a 500-row manifest that shares one table', () => {
        const {dom, t, transport, lookup} = setup();
        try {
            const manifest = rows(500);
            expect(manifest.handlers.length).toBe(2);
            t.install(transport, manifest, 'app.Page');
            expect(lookup('row-0', 'select').parameters[0].name).toBe('value');
            expect(lookup('row-499', 'toggle').parameters[0].type).toBe('bool');
            expect(lookup(null, 'note').parameters[0].name).toBe('kwargs');
            expect(transport._parameterContractDigest.get('app.Page')).toBe(manifest.digest);
        } finally { dom.window.close(); }
    });

    const bad = {
        'a v1 manifest': () => ({version: 1, owners: [{view_id: null, component_id: null, handlers: {}}]}),
        'no digest': () => { const m = rows(1); delete m.digest; return m; },
        'an out-of-range contract index': () => { const m = rows(1); m.handlers[0].note = 9; return m; },
        'an out-of-range table index': () => { const m = rows(1); m.owners[1][2] = 9; return m; },
        'a duplicate owner row': () => { const m = rows(1); m.owners.push([...m.owners[1]]); return m; },
        'a row of length 2': () => { const m = rows(1); m.owners[1] = [null, 'row-0']; return m; },
        'a bad handler name': () => { const m = rows(1); m.handlers[0]['1bad'] = 0; return m; },
        'a missing root': () => { const m = rows(1); m.owners.shift(); return m; },
        'more than 10,000 owners': () => {
            const m = rows(1);
            for (let i = 0; i < 10001; i++) m.owners.push([null, `x${i}`, 1]);
            return m;
        },
        'more than 1,024 contracts': () => {
            const m = rows(1);
            for (let i = 0; i < 1025; i++) m.contracts.push(m.contracts[0]);
            return m;
        },
    };
    it.each(Object.keys(bad))('records an invalid scope for %s', label => {
        const {dom, t, transport, lookup} = setup();
        try {
            expect(() => t.install(transport, bad[label](), 'app.Page')).toThrow('Invalid public parameter contracts');
            expect(() => lookup(null, 'note')).toThrow('Invalid public parameter contracts');
            expect(transport._parameterContractDigest.has('app.Page')).toBe(false);
        } finally { dom.window.close(); }
    });
});

describe('a manifest sent as its digest', () => {
    it('keeps the scope when the digest names the manifest already received', () => {
        const {dom, t, transport, lookup, render} = setup();
        try {
            t.install(transport, rows(1, 'int'), 'app.Page');  // the mount
            const manifest = rows(3);
            t.refresh(transport, render(manifest));
            const elided = render(manifest.digest);
            expect(elided.parameter_contracts).toBe(manifest);
            t.refresh(transport, elided);
            expect(lookup('row-2', 'select').parameters[0].name).toBe('value');
        } finally { dom.window.close(); }
    });

    it('fails closed on a digest it never received', () => {
        const {dom, t, transport, lookup, render} = setup();
        try {
            t.install(transport, rows(1, 'int'), 'app.Page');  // the mount
            t.refresh(transport, render(rows(3)));
            const unknown = render('0123456789abcdef');
            expect(unknown.parameter_contracts).toBe(false);
            t.refresh(transport, unknown);
            expect(() => lookup('row-0', 'select')).toThrow('Invalid public parameter contracts');
            expect(transport._parameterContractDigest.has('app.Page')).toBe(false);
        } finally { dom.window.close(); }
    });

    it('resolves at receipt, so a later frame applied first still installs', () => {
        const {dom, t, transport, lookup, render} = setup();
        try {
            t.install(transport, rows(1, 'int'), 'app.Page');
            const manifest = rows(2);
            const first = render(manifest);
            const second = render(manifest.digest);
            t.refresh(transport, second);
            t.refresh(transport, first);  // older: does not replace the newer snapshot
            expect(lookup('row-1', 'select').parameters[0].type).toBe('str');
        } finally { dom.window.close(); }
    });

    it('holds the last four manifests', () => {
        const {dom, transport, receive} = setup();
        try {
            const manifests = [1, 2, 3, 4, 5].map(n => rows(n));
            for (const manifest of manifests) receive({parameter_contracts: manifest});
            expect(receive({parameter_contracts: manifests[0].digest}).parameter_contracts).toBe(false);
            for (const manifest of manifests.slice(1)) {
                expect(receive({parameter_contracts: manifest.digest}).parameter_contracts).toBe(manifest);
            }
            expect(transport._parameterManifests.size).toBe(4);
        } finally { dom.window.close(); }
    });
});

describe('HTTP fallback names the manifest it holds', () => {
    const page = '<div dj-root dj-view="app.Page"><button id="go" dj-click="note"></button></div>';
    const block = contracts =>
        `<script type="application/json" data-djust-parameter-contracts>${JSON.stringify(contracts)}</script>`;

    function pageSetup(extra = '', responses = []) {
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
        dom.window.eval(source.replace(pageSeam, pageSeam +
            '\nwindow.resolveContract = _resolveParameterContract;\nwindow.pageTransport = _localEventTransport;' +
            '\nwindow.invalidate = _invalidateRenderParameterContracts;'));
        dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
        const send = () => dom.window.djust.handleEvent('note', {});
        const header = i => dom.window._fetches[i]['X-Djust-Parameter-Contracts'];
        const resolve = () => dom.window.resolveContract(dom.window.document.getElementById('go'), 'note');
        return {dom, send, header, resolve};
    }

    it('sends the digest, then keeps the scope on a digest answer', async () => {
        const manifest = rows(2);
        const answer = {patches: [], parameter_contracts: manifest.digest, parameter_contract_view: 'app.Page'};
        const {dom, send, header, resolve} = pageSetup(block({view: 'app.Page', contracts: manifest}), [answer]);
        try {
            await send();
            expect(header(0)).toBe(manifest.digest);
            expect(resolve().contract.parameters[0].name).toBe('kwargs');
            await send();
            expect(header(1)).toBe(manifest.digest);
        } finally { dom.window.close(); }
    });

    it('sends 1 for an invalid scope and nothing for a page with no handlers', async () => {
        const invalid = pageSetup(block({view: 'app.Page', contracts: rows(1)}));
        try {
            invalid.dom.window.invalidate(invalid.dom.window.pageTransport, {});
            await invalid.send();
            expect(invalid.header(0)).toBe('1');
        } finally { invalid.dom.window.close(); }
        const bare = pageSetup();
        try {
            await bare.send();
            expect(bare.header(0)).toBeUndefined();
        } finally { bare.dom.window.close(); }
    });

    it('resolves a digest answer from a manifest a socket mount mirrored', async () => {
        const manifest = rows(3);
        const answer = {patches: [], parameter_contracts: manifest.digest, parameter_contract_view: 'app.Page'};
        const {dom, send, header, resolve} = pageSetup('', [answer]);
        try {
            dom.window.djust._mirrorPageParameterContracts(manifest, 'app.Page');
            await send();
            expect(header(0)).toBe(manifest.digest);
            expect(resolve().contract.parameters[0].name).toBe('kwargs');
        } finally { dom.window.close(); }
    });
});
