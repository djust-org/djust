/**
 * A server `reset_form` resets the form the event came from.
 *
 * The client used to reset `document.querySelector('[dj-root] form')`: the
 * first form of the page, whatever form the server had reset. Opening a form
 * from a button (its handler calls `reset_form()`) cleared an unrelated draft
 * in a composer form higher up the page. Now: the triggering form, or the form
 * holding the triggering element; else the root's form only when it is the
 * only one.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
    url: 'http://localhost/',
});
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (value) => String(value).replace(/([^\w-])/g, '\\$1');
}
dom.window.eval(clientCode);

const { formToReset } = dom.window.djust;
const document = dom.window.document;

function page(html) {
    document.body.innerHTML = `<div dj-root>${html}</div>`;
    return document.querySelector('[dj-root]');
}

describe('formToReset', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    it('is the triggering form itself (dj-submit)', () => {
        page('<form id="composer"><textarea></textarea></form><form id="run"><input></form>');
        const run = document.getElementById('run');
        expect(formToReset(run, null)).toBe(run);
    });

    it('is the form holding the triggering element', () => {
        page('<form id="composer"><textarea></textarea></form><form id="run"><button id="b">x</button></form>');
        expect(formToReset(document.getElementById('b'), null).id).toBe('run');
    });

    it('is no form when the trigger is outside every form and the page has several', () => {
        page('<form id="composer"><textarea></textarea></form><button id="open">Open</button><form id="run"><input></form>');
        expect(formToReset(document.getElementById('open'), null)).toBe(null);
    });

    it('is the only form when the trigger is outside it', () => {
        page('<form id="only"><input></form><button id="clear">Clear</button>');
        expect(formToReset(document.getElementById('clear'), null).id).toBe('only');
        // No trigger at all (a URL change): the same rule.
        expect(formToReset(null, null).id).toBe('only');
    });

    it('ignores a trigger the render removed', () => {
        page('<form id="only"><button id="gone">x</button></form>');
        const gone = document.getElementById('gone');
        gone.remove();
        expect(formToReset(gone, null).id).toBe('only');
    });

    it('looks inside the given view root, not the page', () => {
        page('<form id="page-form"><input></form><div id="slot"><form id="slot-form"><input></form></div>');
        expect(formToReset(null, document.getElementById('slot')).id).toBe('slot-form');
    });
});

function socketPage(html) {
    const sock = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root dj-liveview-root dj-view="test.View">${html}</div></body></html>`,
        { url: 'http://localhost', runScripts: 'dangerously' },
    );
    if (!sock.window.CSS) sock.window.CSS = {};
    if (!sock.window.CSS.escape) {
        sock.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }
    const sent = [];
    sock.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            Promise.resolve().then(() => this.onopen && this.onopen({}));
        }
        send(data) { sent.push(JSON.parse(data)); }
        close() {}
    };
    Object.assign(sock.window.WebSocket, { OPEN: 1, CONNECTING: 0, CLOSING: 2, CLOSED: 3 });
    sock.window.console = { log() {}, warn() {}, debug() {}, info() {}, error: console.error };
    sock.window.eval(clientCode);
    return { sock, sent };
}

describe('reset_form frame over the socket', () => {
    it('clears the form the event came from and keeps the draft in the other one', async () => {
        const { sock, sent } = socketPage(
            '<form id="composer"><textarea id="draft"></textarea></form>' +
            '<button id="open">Open</button>' +
            '<form id="run"><input id="reason" value=""></form>'
        );
        await new Promise((r) => setTimeout(r, 10));
        const ws = sock.window.djust.liveViewInstance;
        const doc = sock.window.document;
        const draft = doc.getElementById('draft');
        const reason = doc.getElementById('reason');
        draft.value = 'my unsent message';
        reason.value = 'typed';

        ws.viewMounted = true;
        let version = 0;
        async function answer(eventName, trigger) {
            ws.sendEvent(eventName, {}, trigger);
            const frame = sent.filter((m) => m.type === 'event').pop();
            version += 1;
            await ws.handleMessage({ type: 'patch', patches: [], version, ref: frame.ref, reset_form: true });
        }

        // A button outside every form: no way to tell which form, so neither.
        await answer('open_run', doc.getElementById('open'));
        expect(draft.value).toBe('my unsent message');
        expect(reason.value).toBe('typed');

        // A submit from the run form resets that form only.
        await answer('submit_form', doc.getElementById('run'));
        expect(reason.value).toBe('');
        expect(draft.value).toBe('my unsent message');
    });
});
