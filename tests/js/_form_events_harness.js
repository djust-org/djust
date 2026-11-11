/**
 * Shared harness for the v1.3.0-19 form-event suites (#3958, #3959, #3963):
 * boots the BUNDLED client in JSDOM over the HTTP fallback, with an ADR-036
 * parameter-contract manifest, and records what would reach the server.
 *
 * The DOM is built with `innerHTML` (real inter-element whitespace), the way a
 * server-rendered page reaches the browser; events are real DOM events.
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

export const param = (name, type, kind = 'positional_or_keyword', required = true) =>
    ({name, type, kind, required, reduced_checking: type === 'Any'});
export const strict = (...parameters) => ({policy: 'strict', coerce_types: true, parameters});
export const legacy = () => ({policy: 'legacy'});

/**
 * @param {string} body markup inside the dj-view root
 * @param {object} handlers handler name -> contract (strict(...) / legacy())
 * @param {(request) => object} respond builds the JSON response body for each
 *        request ({event, params, calls, window}); default is an empty patch list.
 */
export function setup(body, handlers = {}, respond = () => ({patches: []})) {
    const contracts = {version: 1, owners: [{view_id: null, component_id: null, handlers}]};
    const block = `<script type="application/json" data-djust-parameter-contracts>${
        JSON.stringify({view: 'app.Page', contracts})}</script>`;
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-view="app.Page">${body}</div>${block}</body></html>`,
        {runScripts: 'dangerously', url: 'http://localhost/'});
    const win = dom.window;
    win._sent = [];
    win._errors = [];
    win._respond = (event, params, calls) => respond({event, params, calls, window: win});
    win.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', {value: false, writable: true, configurable: true});
        window.addEventListener('djust:error', e => window._errors.push(e.detail));
        window._version = 0;
        window.fetch = async function(url, opts) {
            const event = opts.headers['X-Djust-Event'];
            const params = JSON.parse(opts.body);
            window._sent.push({event, params});
            const body = window._respond(event, params, window._sent.length);
            window._version++;
            // Every render response restates the contracts, as the server's do.
            const frame = {version: window._version, parameter_contracts: ${JSON.stringify(contracts)},
                parameter_contract_view: 'app.Page'};
            return {ok: true, json: async () => Object.assign(frame, body)};
        };
    `);
    win.console.error = () => {};
    win.eval(source);
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    const $ = selector => win.document.querySelector(selector);
    const settle = (ms = 30) => new Promise(resolve => setTimeout(resolve, ms));
    return {dom, win, $, settle, sent: () => win._sent, errors: () => win._errors};
}

/** Press a submit button the way the browser does: a real `submit` event whose `submitter` is the button. */
export function submit(win, form, submitter) {
    form.requestSubmit(submitter);
}

/** Toggle a checkbox the way a click does: set `checked`, then fire a bubbling `change`. */
export function toggle(win, box, checked = !box.checked) {
    box.checked = checked;
    box.dispatchEvent(new win.Event('change', {bubbles: true}));
}
