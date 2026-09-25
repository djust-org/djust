/**
 * ADR-036 P2 (b): native binders collect strict handler arguments from the
 * owner-scoped public contract (decisions Q1/Q2/N1), and legacy bindings keep
 * their existing payloads. Drives real DOM events through the bundled client
 * over the HTTP fallback, recording what would reach the server.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

const param = (name, type, kind = 'positional_or_keyword', required = true) =>
    ({name, type, kind, required, reduced_checking: type === 'Any'});
const strict = (...parameters) => ({policy: 'strict', coerce_types: true, parameters});

const rootHandlers = {
    pick: strict(param('item_id', 'str')),
    count: strict(param('n', 'int')),
    label: strict(param('name', 'str')),
    choose: strict(param('value', 'int'), param('extra', 'str', 'positional_or_keyword', false)),
    search: strict(param('value', 'str')),
    search_field: strict(param('value', 'str'), param('field', 'str')),
    search_open: strict(param('value', 'str'), param('rest', 'Any', 'var_keyword', false)),
    toggle: strict(param('value', 'bool')),
    save: strict(param('title', 'str')),
    save_all: strict(param('form', 'Any', 'var_keyword', false)),
    on_key: strict(param('key', 'str')),
    old_click: {policy: 'legacy'},
    old_input: {policy: 'legacy'},
    old_save: {policy: 'legacy'},
};
const manifest = {version: 1, owners: [
    {view_id: null, component_id: null, handlers: rootHandlers},
    {view_id: null, component_id: 'menu', handlers: {pick: strict(param('item_id', 'int'))}},
]};

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
const input = (dom, el, value, type = 'input') => {
    el.value = value;
    el.dispatchEvent(new dom.window.Event(type, {bubbles: true}));
};

describe('strict dj-click', () => {
    it('sends only dj-value-* arguments, never data-* or legacy namespaces', async () => {
        const {dom, $, settle, sent} = setup(
            '<button dj-click="pick" dj-value-item-id="7" data-item-id="9" data-other="x">Go</button>');
        try {
            click(dom, $('button'));
            await settle();
            expect(sent()).toEqual([{event: 'pick', params: {item_id: '7'}}]);
        } finally { dom.window.close(); }
    });

    it('keeps legacy precedence and namespaces for a legacy handler on the same page', async () => {
        const {dom, $, settle, sent} = setup(
            '<button dj-click="old_click" dj-value-item-id="7" data-other="x">Go</button>');
        try {
            click(dom, $('button'));
            await settle();
            expect(sent()[0].params).toMatchObject({item_id: '7', other: 'x'});
        } finally { dom.window.close(); }
    });

    it.each([
        ['a partial typed literal', '<button dj-click="count" dj-value-n:int="12abc" dj-disable-with="Wait" dj-lock>Go</button>'],
        ['a wire hint that conflicts with the declared type', '<button dj-click="label" dj-value-name:int="3" dj-disable-with="Wait" dj-lock>Go</button>'],
        ['a value supplied positionally and by name', '<button dj-click="choose(3)" dj-value-value="4" dj-disable-with="Wait" dj-lock>Go</button>'],
    ])('rejects %s before any lock, disable-with or send', async (_label, html) => {
        const {dom, $, settle, sent, errors} = setup(html);
        try {
            click(dom, $('button'));
            await settle();
            expect(sent()).toEqual([]);
            expect(errors()).toEqual([{error: 'Invalid event arguments for this handler.',
                traceback: null, event: $('button').getAttribute('dj-click').split('(')[0], validation_details: null}]);
            expect($('button').textContent).toBe('Go');
            expect($('button').hasAttribute('data-djust-locked')).toBe(false);
            expect(JSON.stringify(errors())).not.toMatch(/12abc|"3"|"4"/);
        } finally { dom.window.close(); }
    });

    it('resolves a component owner and attaches its routing context', async () => {
        const {dom, $, settle, sent} = setup(
            '<div data-component-id="menu"><button dj-click="pick" dj-value-item-id:int="5">Go</button></div>');
        try {
            click(dom, $('button'));
            await settle();
            expect(sent()).toEqual([{event: 'pick', params: {item_id: 5, component_id: 'menu'}}]);
        } finally { dom.window.close(); }
    });

    it('fails closed on an invalid page scope, for every handler', async () => {
        const {dom, $, settle, sent, errors} = setup(
            '<button id="a" dj-click="pick">A</button><button id="b" dj-click="old_click">B</button>', false);
        try {
            click(dom, $('#a'));
            click(dom, $('#b'));
            await settle();
            expect(sent()).toEqual([]);
            expect(errors()).toHaveLength(2);
        } finally { dom.window.close(); }
    });

    it('uses legacy collection for a handler the mount does not list', async () => {
        const {dom, $, settle, sent} = setup('<button dj-click="unlisted" data-other="x">Go</button>');
        try {
            click(dom, $('button'));
            await settle();
            expect(sent()[0].params).toMatchObject({other: 'x'});
        } finally { dom.window.close(); }
    });
});

describe('generated values follow the declared contract (Q1) and omit _target (Q2)', () => {
    it.each([
        ['search', {value: 'abc'}],
        ['search_field', {value: 'abc', field: 'q'}],
        ['search_open', {value: 'abc', field: 'q'}],
    ])('dj-input to %s', async (handler, expected) => {
        const {dom, $, settle, sent} = setup(`<input name="q" dj-input="${handler}">`);
        try {
            input(dom, $('input'), 'abc');
            await settle(400);
            expect(sent()).toEqual([{event: handler, params: expected}]);
        } finally { dom.window.close(); }
    });

    it('keeps field and _target for a legacy dj-input', async () => {
        const {dom, $, settle, sent} = setup('<input name="q" dj-input="old_input">');
        try {
            input(dom, $('input'), 'abc');
            await settle(400);
            expect(sent()[0].params).toMatchObject({value: 'abc', field: 'q', _target: 'q'});
        } finally { dom.window.close(); }
    });

    it('rejects a dj-value-* key that collides with a generated value', async () => {
        const {dom, $, settle, sent, errors} = setup('<input name="q" dj-input="search" dj-value-field="x">');
        try {
            input(dom, $('input'), 'abc');
            await settle(400);
            expect(sent()).toEqual([]);
            expect(errors()).toHaveLength(1);
        } finally { dom.window.close(); }
    });

    it('sends a checkbox dj-change as a boolean', async () => {
        const {dom, $, settle, sent} = setup('<input type="checkbox" name="done" dj-change="toggle">');
        try {
            $('input').checked = true;
            $('input').dispatchEvent(new dom.window.Event('change', {bubbles: true}));
            await settle();
            expect(sent()).toEqual([{event: 'toggle', params: {value: true}}]);
        } finally { dom.window.close(); }
    });

    it('sends only the key a strict keyboard handler declares', async () => {
        const {dom, $, settle, sent} = setup('<input name="q" dj-keydown="on_key">');
        try {
            $('input').dispatchEvent(new dom.window.KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
            await settle();
            expect(sent()).toEqual([{event: 'on_key', params: {key: 'Enter'}}]);
        } finally { dom.window.close(); }
    });

    const form = handler => `<form dj-submit="${handler}"><input name="title" value="T">` +
        '<input name="other" value="O"><button type="submit" name="go">Go</button></form>';
    const submit = (dom, $) => $('form').dispatchEvent(new dom.window.Event('submit', {bubbles: true, cancelable: true}));

    it.each([
        ['save', {title: 'T'}],
        ['save_all', {title: 'T', other: 'O'}],
    ])('dj-submit to %s sends declared fields, or all for **form_data', async (handler, expected) => {
        const {dom, $, settle, sent} = setup(form(handler));
        try {
            submit(dom, $);
            await settle();
            expect(sent()).toEqual([{event: handler, params: expected}]);
        } finally { dom.window.close(); }
    });

    it('keeps every field and _target for a legacy dj-submit', async () => {
        const {dom, $, settle, sent} = setup(form('old_save'));
        try {
            submit(dom, $);
            await settle();
            expect(sent()[0].params).toMatchObject({title: 'T', other: 'O'});
            expect(Object.hasOwn(sent()[0].params, '_target')).toBe(true);
        } finally { dom.window.close(); }
    });
});
