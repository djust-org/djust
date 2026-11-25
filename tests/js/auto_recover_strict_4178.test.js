/**
 * #4178: dj-auto-recover under the strict parameter policy.
 *
 * `form_values` and `data_attrs` are generated values (ADR-036 Q1): a strict
 * handler receives the ones it declares, plus the container's dj-value-*
 * arguments, through the strict collector's limits. An unlisted handler
 * declares nothing, so only its dj-value-* arguments are sent. Drives the
 * bundled client over the HTTP fallback and records what would reach the server.
 */
import { describe, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
const { readFileSync } = await vi.importActual('node:fs');

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

const param = (name, type, kind = 'positional_or_keyword', required = true) =>
    ({name, type, kind, required, reduced_checking: type.includes('Any')});
const strict = (...parameters) => ({policy: 'strict', coerce_types: true, parameters});

const handlers = {
    both: strict(param('form_values', 'dict[str, Any]'), param('data_attrs', 'dict[str, str]')),
    data_only: strict(param('data_attrs', 'dict[str, str]')),
    open: strict(param('rest', 'Any', 'var_keyword', false)),
    with_page: strict(param('form_values', 'dict[str, Any]'), param('page', 'int')),
};
const manifest = {version: 1, owners: [{view_id: null, component_id: null, handlers}]};

const FORM = `
    <input name="title" value="Draft">
    <input type="checkbox" name="tags" value="a" checked><input type="checkbox" name="tags" value="b">
    <input type="checkbox" name="urgent" checked>
    <input type="password" name="secret" value="hunter2">`;

function setup(container, contracts = manifest) {
    const block = contracts === undefined ? '' :
        `<script type="application/json" data-djust-parameter-contracts>${JSON.stringify({view: 'app.Page', contracts})}</script>`;
    const dom = new JSDOM(`<!DOCTYPE html><html><body><div dj-view="app.Page">${container}</div>${block}</body></html>`,
        {runScripts: 'dangerously', url: 'http://localhost/'});
    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._sent = [];
        window._errors = [];
        window.addEventListener('djust:error', e => window._errors.push(e.detail));
        window.fetch = async function(url, opts) {
            const params = JSON.parse(opts.body);
            delete params._cacheRequestId;
            window._sent.push({event: opts.headers['X-Djust-Event'], params});
            return {ok: true, json: async () => ({patches: [], parameter_contracts: ${JSON.stringify(contracts ?? null)},
                parameter_contract_view: 'app.Page'})};
        };
    `);
    dom.window.console.error = () => {};
    dom.window.eval(source);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    const recover = async () => {
        dom.window.djust._isReconnect = true;
        dom.window.djust._processAutoRecover();
        await new Promise(resolve => setTimeout(resolve, 20));
    };
    return {dom, recover, sent: () => dom.window._sent, errors: () => dom.window._errors};
}

const FORM_VALUES = {title: 'Draft', tags: ['a'], urgent: true};

describe('dj-auto-recover, strict handler', () => {
    it('sends form_values and data_attrs under their public names', async () => {
        const {dom, recover, sent} = setup(`<form dj-auto-recover="both" data-step="2" data-mode="edit">${FORM}</form>`);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'both', params: {
                form_values: FORM_VALUES, data_attrs: {step: '2', mode: 'edit'}}}]);
        } finally { dom.window.close(); }
    });

    it('sends only the generated values the handler declares', async () => {
        const {dom, recover, sent} = setup(`<form dj-auto-recover="data_only" data-step="2">${FORM}</form>`);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'data_only', params: {data_attrs: {step: '2'}}}]);
        } finally { dom.window.close(); }
    });

    it('sends both to a ** catch-all', async () => {
        const {dom, recover, sent} = setup(`<form dj-auto-recover="open" data-step="2">${FORM}</form>`);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'open', params: {form_values: FORM_VALUES, data_attrs: {step: '2'}}}]);
        } finally { dom.window.close(); }
    });

    it('adds the container\'s dj-value-* arguments', async () => {
        const {dom, recover, sent} = setup(`<form dj-auto-recover="with_page" dj-value-page:int="3">${FORM}</form>`);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'with_page', params: {form_values: FORM_VALUES, page: 3}}]);
        } finally { dom.window.close(); }
    });

    it('refuses a dj-value-* that reuses a generated name, value-free', async () => {
        const {dom, recover, sent, errors} = setup(
            `<form dj-auto-recover="both" dj-value-form-values:json="{}">${FORM}</form>`);
        try {
            await recover();
            expect(sent()).toEqual([]);
            expect(errors()).toEqual([expect.objectContaining({event: 'both', error: 'Invalid event arguments for this handler.'})]);
        } finally { dom.window.close(); }
    });

    it('refuses form values past the strict text limit before sending', async () => {
        const big = 'x'.repeat(65537);
        const {dom, recover, sent, errors} = setup(
            `<form dj-auto-recover="both"><textarea name="body">${big}</textarea></form>`);
        try {
            await recover();
            expect(sent()).toEqual([]);
            expect(errors()).toHaveLength(1);
        } finally { dom.window.close(); }
    });
});

describe('dj-auto-recover inside a component or an embedded child view', () => {
    // The event carries no owner context, so the server routes it to the page's
    // view: the client must use the page view's contract, not the nearest owner's
    // (which does not list the handler).
    const owned = {version: 1, owners: [
        {view_id: null, component_id: null, handlers},
        {view_id: null, component_id: 'cmp1', handlers: {}},
        {view_id: 'child1', component_id: null, handlers: {other: strict()}},
    ]};

    it('sends strict values from a component wrapper', async () => {
        const {dom, recover, sent} = setup(
            `<div data-component-id="cmp1"><form dj-auto-recover="both" data-step="2">${FORM}</form></div>`, owned);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'both', params: {
                form_values: FORM_VALUES, data_attrs: {step: '2'}}}]);
        } finally { dom.window.close(); }
    });

    it('sends strict values from an embedded child view', async () => {
        const {dom, recover, sent} = setup(
            `<div dj-view="app.Child" data-djust-embedded="child1">` +
            `<form dj-auto-recover="both" data-step="2">${FORM}</form></div>`, owned);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'both', params: {
                form_values: FORM_VALUES, data_attrs: {step: '2'}}}]);
        } finally { dom.window.close(); }
    });
});

describe('dj-auto-recover, unlisted handler', () => {
    it('sends only dj-value-* arguments for a handler the mount does not list', async () => {
        const {dom, recover, sent} = setup(
            `<form dj-auto-recover="unlisted" dj-value-step="2" data-step="2">${FORM}</form>`);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'unlisted', params: {step: '2'}}]);
        } finally { dom.window.close(); }
    });

    it('sends no envelope for a page that delivered no contract', async () => {
        const {dom, recover, sent} = setup(`<form dj-auto-recover="anything" data-step="2">${FORM}</form>`, undefined);
        try {
            await recover();
            expect(sent()).toEqual([{event: 'anything', params: {}}]);
        } finally { dom.window.close(); }
    });
});
