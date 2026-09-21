/** Applied DOM frames, not receipt, own render-contract refresh. */
import {describe, expect, it} from 'vitest';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const seam = 'window.djust.collectDjValues = collectDjValues;';
const reinit = 'function reinitAfterDOMUpdate(scope) {';
for (const marker of [seam, reinit]) {
    if (source.split(marker).length !== 2) throw new Error('Missing render-contract test seam');
}
const manifest = type => ({version: 1, owners: [
    {view_id: null, component_id: null, handlers: {choose: {
        policy: 'strict', coerce_types: true, parameters: [{
            name: 'value', type, kind: 'positional_or_keyword', required: true,
            reduced_checking: false,
        }],
    }}},
]});
function setup(name) {
    const dom = new JSDOM('<div dj-root dj-view="app.Page"><span>old</span><div data-djust-embedded="child"></div></div>',
        {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.CSS ??= {};
    dom.window.CSS.escape ??= value => value;
    dom.window.eval(source.replace(seam, seam +
        '\nwindow.lookupContract = _lookupParameterContract;\nwindow.registerRequest = registerEventRequest;')
        .replace(reinit, reinit + '\nif (window.onReinit) window.onReinit();'));
    const transport = new dom.window.djust[name]();
    transport.primaryViewPath = 'app.Page';
    const lookup = (path = 'app.Page') => dom.window.lookupContract(transport, path, null, null, 'choose');
    const frame = (extra = {}) => ({type: 'html_update', version: 2,
        html: '<div dj-root dj-view="app.Page"><span>new</span></div>',
        parameter_contract_view: 'app.Page', parameter_contracts: manifest('str'), ...extra});
    const mount = () => transport.handleMessage({type: 'mount', view: 'app.Page',
        version: 1, parameter_contracts: manifest('int')});
    return {dom, transport, lookup, frame, mount};
}

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s applied contracts', name => {
    it('does not let a queued mount consume a later response receipt', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            const mounting = mount();
            const updating = transport.handleMessage(frame());
            await Promise.all([mounting, updating]);
            expect(lookup().parameters[0].type).toBe('str');
        } finally { dom.window.close(); }
    });
    it('installs the matching HTML snapshot before reinitializing bindings', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            const seen = [];
            dom.window.onReinit = () => seen.push([
                dom.window.document.querySelector('span').textContent,
                lookup().parameters[0].type,
            ]);
            await transport.handleMessage(frame());
            expect(lookup().parameters[0].type).toBe('str');
            expect(seen).toContainEqual(['new', 'str']);
        } finally { dom.window.close(); }
    });

    it('refreshes empty patches and honors explicit legacy clears', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            await transport.handleMessage(frame({type: 'patch', patches: [], html: undefined}));
            expect(lookup().parameters[0].type).toBe('str');
            await transport.handleMessage(frame({version: 3, parameter_contracts: null}));
            expect(lookup()).toBe(null);
        } finally { dom.window.close(); }
    });

    it('does not install a snapshot on a rejected version', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            await transport.handleMessage(frame({version: 8}));
            expect(lookup().parameters[0].type).toBe('int');
            expect(dom.window.document.querySelector('span').textContent).toBe('old');
        } finally { dom.window.close(); }
    });

    it('invalidates missing or malformed snapshots instead of retaining stale strict rules', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            const missing = frame();
            delete missing.parameter_contracts;
            await transport.handleMessage(missing);
            expect(() => lookup()).toThrow('Invalid public parameter contracts');
            await transport.handleMessage(frame({version: 3, parameter_contracts: {version: 99}}));
            expect(() => lookup()).toThrow('Invalid public parameter contracts');
        } finally { dom.window.close(); }
    });

    it('refreshes child frames before child bindings initialize', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            const seen = [];
            dom.window.onReinit = () => seen.push(lookup().parameters[0].type);
            await transport.handleMessage(frame({type: 'embedded_update', view_id: 'child',
                html: '<span>child new</span>', source: 'async'}));
            expect(lookup().parameters[0].type).toBe('str');
            expect(seen).toContain('str');
        } finally { dom.window.close(); }
    });

    it('settles a child reply even when its metadata is invalid', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            const request = dom.window.registerRequest(transport, 'choose', null);
            await transport.handleMessage(frame({type: 'embedded_update', view_id: 'child',
                ref: request.ref, html: '<span>child new</span>',
                parameter_contracts: {version: 99}}));
            expect(() => lookup()).toThrow('Invalid public parameter contracts');
            expect(dom.window.djust._getEventSeqState().pendingEventRefs).toEqual([]);
        } finally { dom.window.close(); }
    });

    it('does not install metadata for a missing child DOM owner', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            await transport.handleMessage(frame({type: 'embedded_update', view_id: 'removed',
                html: '<span>child new</span>', source: 'async'}));
            expect(lookup().parameters[0].type).toBe('int');
        } finally { dom.window.close(); }
    });

    it('invalidates a partially failed patch without publishing its snapshot', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            await transport.handleMessage(frame({type: 'patch', html: undefined, patches: [
                {type: 'SetText', path: [0, 0], text: 'partial'},
                {type: 'SetText', path: [99], text: 'invalid'},
            ]}));
            expect(() => lookup()).toThrow('Invalid public parameter contracts');
        } finally { dom.window.close(); }
    });

    it('rejects a snapshot claiming a different mount', async () => {
        const {dom, transport, lookup, frame, mount} = setup(name);
        try {
            await mount();
            await transport.handleMessage(frame({parameter_contract_view: 'app.Foreign'}));
            expect(() => lookup()).toThrow('Invalid public parameter contracts');
            expect(() => lookup('app.Foreign')).toThrow('Unknown parameter contract mount');
        } finally { dom.window.close(); }
    });
});

it('preserves additional mount contracts when refreshing a primary WS mount', async () => {
    const {dom, transport, lookup, frame, mount} = setup('LiveViewWebSocket');
    try {
        await mount();
        await transport.handleMessage({type: 'mount', view: 'app.Extra', parameter_contracts: manifest('bool')});
        await transport.handleMessage(frame());
        expect(lookup('app.Extra').parameters[0].type).toBe('bool');
        expect(lookup().parameters[0].type).toBe('str');
    } finally { dom.window.close(); }
});

it.each([true, false])('refreshes recovery HTML without retaining stale rules (snapshot=%s)', async supplied => {
    const {dom, transport, lookup, frame, mount} = setup('LiveViewWebSocket');
    try {
        await mount();
        const recovery = frame({type: 'html_recovery'});
        if (!supplied) delete recovery.parameter_contracts;
        await transport.handleMessage(recovery);
        expect(dom.window.document.querySelector('span').textContent).toBe('new');
        if (supplied) expect(lookup().parameters[0].type).toBe('str');
        else expect(() => lookup()).toThrow('Invalid public parameter contracts');
    } finally { dom.window.close(); }
});

it('keeps buffered snapshots on the originating transport until replay', async () => {
    const {dom, transport, lookup, frame, mount} = setup('LiveViewWebSocket');
    try {
        await mount();
        const request = dom.window.registerRequest(transport, 'choose', null);
        await transport.handleMessage(frame({type: 'patch', patches: [], html: undefined, source: 'tick'}));
        expect(lookup().parameters[0].type).toBe('int');
        await transport.handleMessage({type: 'noop', ref: request.ref});
        expect(lookup().parameters[0].type).toBe('str');
    } finally { dom.window.close(); }
});

it.each(['root', 'child'])('does not regress a newer %s snapshot when replaying an older buffered frame', async target => {
    const {dom, transport, lookup, frame, mount} = setup('LiveViewWebSocket');
    try {
        await mount();
        const request = dom.window.registerRequest(transport, 'choose', null);
        await transport.handleMessage(frame({type: 'patch', patches: [], html: undefined, source: 'tick'}));
        const newer = target === 'root'
            ? frame({type: 'patch', patches: [], html: undefined, version: 3, ref: request.ref,
                parameter_contracts: manifest('bool')})
            : frame({type: 'embedded_update', view_id: 'child', version: undefined,
                html: '<span>child new</span>', ref: request.ref, parameter_contracts: manifest('bool')});
        await transport.handleMessage(newer);
        expect(lookup().parameters[0].type).toBe('bool');
        expect(dom.window.djust._getEventSeqState().tickBufferLength).toBe(0);
    } finally { dom.window.close(); }
});

it('does not let an older buffered snapshot replace a newer legacy render', async () => {
    const {dom, transport, lookup, frame} = setup('LiveViewWebSocket');
    try {
        await transport.handleMessage({type: 'mount', view: 'app.Page', version: 1});
        const request = dom.window.registerRequest(transport, 'choose', null);
        await transport.handleMessage(frame({type: 'patch', patches: [], html: undefined, source: 'tick'}));
        await transport.handleMessage({type: 'patch', version: 3, patches: [], ref: request.ref});
        expect(lookup()).toBe(null);
    } finally { dom.window.close(); }
});
