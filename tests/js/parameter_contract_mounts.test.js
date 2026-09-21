/** Actual WS/SSE mount reception, with transport/owner-specific public contracts. */
import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const seam = 'window.djust.collectDjValues = collectDjValues;';
if (source.split(seam).length !== 2) throw new Error('Missing contract test seam');
const strict = {policy: 'strict', coerce_types: true, parameters: [
    {name: 'value', type: 'int', kind: 'positional_or_keyword', required: true, reduced_checking: false},
]};
const manifest = () => ({version: 1, owners: [
    {view_id: null, component_id: null, handlers: {choose: strict}},
    {view_id: null, component_id: 'left', handlers: {choose: strict}},
    {view_id: null, component_id: 'right', handlers: {choose: {policy: 'legacy'}}},
    {view_id: 'left', component_id: null, handlers: {choose: {policy: 'legacy'}}},
]});

function setup(name) {
    const dom = new JSDOM('<div dj-root dj-view="app.Page"></div>', {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.CSS ??= {};
    dom.window.CSS.escape ??= value => value;
    dom.window.eval(source.replace(seam, seam + '\nwindow.lookupContract = _lookupParameterContract;'));
    const transport = new dom.window.djust[name]();
    transport.primaryViewPath = 'app.Page';
    const lookup = (view, component, event = 'choose', path = 'app.Page') => dom.window.lookupContract(transport, path, view, component, event);
    const mount = data => transport.handleMessage({type: 'mount', view: 'app.Page', ...data});
    return {dom, transport, lookup, mount};
}

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s public parameter contracts', name => {
    it('keeps equal handler names in distinct root/component/child scopes', async () => {
        const {dom, lookup, mount} = setup(name);
        try {
            await mount({parameter_contracts: manifest()});
            expect(lookup(null, null).policy).toBe('strict');
            expect(lookup(null, 'left').policy).toBe('strict');
            expect(lookup(null, 'right').policy).toBe('legacy');
            expect(lookup('left', null).policy).toBe('legacy');
            expect(() => lookup(null, 'missing')).toThrow('Unknown parameter contract owner');
            expect(() => lookup(null, 'right', 'misspelled')).toThrow();
        } finally { dom.window.close(); }
    });

    it('clears old contracts when remounting a legacy server', async () => {
        const {dom, lookup, mount} = setup(name);
        try {
            await mount({parameter_contracts: manifest()});
            await mount({});
            expect(lookup(null, null)).toBe(null);
        } finally { dom.window.close(); }
    });

    it('rejects malformed replacement metadata instead of retaining old rules', async () => {
        const {dom, lookup, mount} = setup(name);
        try {
            await mount({parameter_contracts: manifest()});
            await mount({parameter_contracts: {version: 99, owners: []}});
            expect(() => lookup(null, null)).toThrow('Invalid public parameter contracts');
        } finally { dom.window.close(); }
    });

    it('copies public fields only and does not retain mutable inbound records', async () => {
        const {dom, lookup, mount} = setup(name);
        try {
            const data = manifest();
            data.owners[0].handlers.choose = JSON.parse(JSON.stringify(strict));
            data.owners[0].handlers.choose.parameters[0].default = 'SECRET_DEFAULT';
            await mount({parameter_contracts: data});
            data.owners[0].handlers.choose.parameters[0].type = 'CHANGED';
            const result = lookup(null, null);
            expect(result.parameters[0].type).toBe('int');
            expect(JSON.stringify(result)).not.toContain('SECRET');
        } finally { dom.window.close(); }
    });

    it('drops the previous primary mount on navigation', async () => {
        const {dom, transport, lookup, mount} = setup(name);
        try {
            await mount({parameter_contracts: manifest()});
            // WS sets this when requesting the new mount; SSE sets it on reply.
            if (name === 'LiveViewWebSocket') transport.primaryViewPath = 'app.Next';
            await mount({view: 'app.Next'});
            expect(() => lookup(null, null)).toThrow('Unknown parameter contract mount');
            expect(lookup(null, null, 'choose', 'app.Next')).toBe(null);
        } finally { dom.window.close(); }
    });
});

it('additional WS mounts and separate connections cannot overwrite primary contracts', async () => {
    const {dom, lookup, mount, transport} = setup('LiveViewWebSocket');
    try {
        await mount({parameter_contracts: manifest()});
        await mount({view: 'app.Extra'});
        expect(lookup(null, null).policy).toBe('strict');
        expect(lookup(null, null, 'choose', 'app.Extra')).toBe(null);
        const other = new dom.window.djust.LiveViewWebSocket();
        other.primaryViewPath = 'app.Page';
        await other.handleMessage({type: 'mount', view: 'app.Page'});
        expect(lookup(null, null).policy).toBe('strict');
        other.disconnect();
        expect(lookup(null, null).policy).toBe('strict');
        transport.disconnect();
        expect(() => lookup(null, null)).toThrow('Unknown parameter contract mount');
    } finally { dom.window.close(); }
});
