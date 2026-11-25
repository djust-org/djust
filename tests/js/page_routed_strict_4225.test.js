/**
 * #4225: directives the server routes to the page view resolve the page view's
 * strict parameter contract, wherever the element sits.
 *
 * dj-viewport-top / dj-viewport-bottom, dj-mutation, dj-copy-event,
 * dj-dialog-close-event and the JS-command `push` send no component_id or
 * view_id, so the server runs them on the page's view (runtime.py: no
 * `view_id` -> no child routing, no `component_id` -> no component routing).
 * Inside a `data-component-id` wrapper or an embedded child the client used to
 * look the handler up under that owner, whose contract differs from the page
 * handler's, and sent the wrong arguments.
 *
 * Bindings that do carry owner context (dj-click) keep the nearest owner's
 * contract. Drives the bundled client over the HTTP fallback and records what
 * would reach the server.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

const param = (name, type) => ({name, type, kind: 'positional_or_keyword', required: true,
    reduced_checking: false});
const strict = (...parameters) => ({policy: 'strict', coerce_types: true, parameters});
// Takes everything: resolving under the wrong owner would send every generated
// value the page's handler does not declare.
const anything = strict({name: 'rest', type: 'Any', kind: 'var_keyword', required: false,
    reduced_checking: true});

// The page view's strict handlers.
const page = {
    load_more: strict(),
    mutated: strict(param('mutation', 'str')),
    copied: strict(),
    closed: strict(param('reason', 'str')),
    pushed: strict(),
    clicked: strict(param('n', 'str')),
};
// The same names under the component and the embedded child, with another
// contract there, so resolving under the wrong owner sends something else.
const nested = {
    load_more: anything, mutated: anything, copied: anything, closed: anything, pushed: anything,
    clicked: strict(param('n', 'int')),
};
const manifest = {version: 1, owners: [
    {view_id: null, component_id: null, handlers: page},
    {view_id: null, component_id: 'cmp1', handlers: nested},
    {view_id: 'child1', component_id: null, handlers: nested},
]};

function setup(inner) {
    const block = '<script type="application/json" data-djust-parameter-contracts>' +
        JSON.stringify({view: 'app.Page', contracts: manifest}) + '</script>';
    const dom = new JSDOM(`<!DOCTYPE html><html><body><div dj-view="app.Page" dj-root>${inner}</div>` +
        `${block}</body></html>`, {runScripts: 'dangerously', url: 'http://localhost/'});
    const win = dom.window;
    win.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._sent = [];
        window.fetch = async function(url, opts) {
            const params = JSON.parse(opts.body);
            delete params._cacheRequestId;
            window._sent.push({event: opts.headers['X-Djust-Event'], params});
            return {ok: true, json: async () => ({patches: [], parameter_contracts: ${JSON.stringify(manifest)},
                parameter_contract_view: 'app.Page'})};
        };
    `);
    const observers = [];
    win.IntersectionObserver = class {
        constructor(cb) { this.cb = cb; this.targets = new Set(); observers.push(this); }
        observe(el) { this.targets.add(el); }
        unobserve(el) { this.targets.delete(el); }
        disconnect() { this.targets.clear(); }
    };
    Object.defineProperty(win.navigator, 'clipboard', {configurable: true,
        value: {writeText: () => Promise.resolve()}});
    const dialog = win.HTMLDialogElement.prototype;
    dialog.showModal = function () { this.setAttribute('open', ''); };
    dialog.close = function () { this.removeAttribute('open'); };
    win.console.error = () => {};
    win.eval(source);
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    const settle = () => new Promise(resolve => setTimeout(resolve, 30));
    const intersect = target => {
        for (const o of observers) if (o.targets.has(target)) o.cb([{target, isIntersecting: true}]);
    };
    return {dom, win, settle, intersect, sent: () => JSON.parse(JSON.stringify(win._sent))};
}

// Each directive: its markup, how to fire it, and what the page's strict
// handler is sent.
const directives = {
    'dj-viewport-top': {
        html: '<div id="t" dj-viewport-top="load_more"><p id="first">a</p><p>b</p></div>',
        fire: async ({win, intersect}) => intersect(win.document.getElementById('first')),
        expected: {event: 'load_more', params: {}},
    },
    'dj-viewport-bottom': {
        html: '<div id="t" dj-viewport-bottom="load_more"><p>a</p><p id="last">b</p></div>',
        fire: async ({win, intersect}) => intersect(win.document.getElementById('last')),
        expected: {event: 'load_more', params: {}},
    },
    'dj-mutation': {
        html: '<div id="t" dj-mutation="mutated" dj-mutation-attr="class" dj-mutation-debounce="0"></div>',
        fire: async ({win, settle}) => {
            win.document.getElementById('t').className = 'changed';
            await settle();
        },
        expected: {event: 'mutated', params: {mutation: 'attributes'}},
    },
    'dj-copy-event': {
        html: '<button id="t" dj-copy="secret text" dj-copy-event="copied">Copy</button>',
        fire: async ({win}) => win.document.getElementById('t').click(),
        expected: {event: 'copied', params: {}},
    },
    'dj-dialog-close-event': {
        html: '<dialog id="t" dj-dialog="open" dj-dialog-close-event="closed" dj-value-reason="esc"></dialog>',
        fire: async ({win}) => win.document.getElementById('t').dispatchEvent(new win.Event('close')),
        expected: {event: 'closed', params: {reason: 'esc'}},
    },
    'push': {
        html: '<button id="t" dj-click=\'[["push",{"event":"pushed","target":"#t"}]]\'>Go</button>',
        fire: async ({win}) => win.document.getElementById('t').click(),
        expected: {event: 'pushed', params: {}},
    },
};

const owners = {
    'the page': html => html,
    'a component wrapper': html => `<div data-component-id="cmp1">${html}</div>`,
    'an embedded child view': html => `<div dj-view="app.Child" data-djust-embedded="child1">${html}</div>`,
};

describe('directives routed to the page view use its strict contract', () => {
    for (const [name, directive] of Object.entries(directives)) {
        for (const [where, wrap] of Object.entries(owners)) {
            it(`${name} inside ${where}`, async () => {
                const harness = setup(wrap(directive.html));
                try {
                    await harness.settle();
                    await directive.fire(harness);
                    await harness.settle();
                    expect(harness.sent()).toEqual([directive.expected]);
                } finally { harness.dom.window.close(); }
            });
        }
    }
});

describe('a binding with owner context keeps the nearest owner\'s contract', () => {
    it('dj-click inside a component resolves the component\'s strict handler', async () => {
        const harness = setup('<div data-component-id="cmp1">' +
            '<button id="t" dj-click="clicked" dj-value-n:int="4" data-extra="x">Go</button></div>');
        try {
            await harness.settle();
            harness.win.document.getElementById('t').click();
            await harness.settle();
            expect(harness.sent()).toEqual([{event: 'clicked', params: {n: 4, component_id: 'cmp1'}}]);
        } finally { harness.dom.window.close(); }
    });

    it('dj-click inside an embedded child resolves the child\'s strict handler', async () => {
        const harness = setup('<div dj-view="app.Child" data-djust-embedded="child1">' +
            '<button id="t" dj-click="clicked" dj-value-n:int="4" data-extra="x">Go</button></div>');
        try {
            await harness.settle();
            harness.win.document.getElementById('t').click();
            await harness.settle();
            expect(harness.sent()).toEqual([{event: 'clicked', params: {n: 4, view_id: 'child1'}}]);
        } finally { harness.dom.window.close(); }
    });
});
