/**
 * #4129: a `reset_form` on a `noop` frame resets the event's form.
 *
 * `reset_submitted_form()` in a handler that changes no rendered state (an
 * explicit-exposure view whose handler only wrote the database) answers with
 * a `noop`, not a render. The server now puts `reset_form` on that frame; each
 * transport's noop path must apply it: the socket, SSE, and the HTTP fallback.
 */
import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { loadPage } from './_http_fallback_replay.js';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const FORM = '<form id="f"><input id="txt" value=""><button id="go">Go</button></form>';

function env() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root dj-liveview-root dj-view="t.V">${FORM}</div></body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    const sent = [];
    window.WebSocket = class {
        constructor() { this.readyState = 1; Promise.resolve().then(() => this.onopen && this.onopen({})); }
        send(data) { sent.push(JSON.parse(data)); }
        close() {}
    };
    Object.assign(window.WebSocket, { OPEN: 1, CONNECTING: 0, CLOSING: 2, CLOSED: 3 });
    window.EventSource = vi.fn(function () { return { close: vi.fn(), readyState: 1 }; });
    window.EventSource.CLOSED = 2;
    window.EventSource.OPEN = 1;
    window.eval(client);
    return { window, sent, input: window.document.getElementById('txt') };
}

describe('reset_form on a noop frame', () => {
    it('resets the form over the socket', async () => {
        const { window, sent, input } = env();
        await new Promise((r) => setTimeout(r, 10));
        const ws = window.djust.liveViewInstance;
        ws.viewMounted = true;
        input.value = 'typed';
        ws.sendEvent('add', {}, window.document.getElementById('go'));
        const frame = sent.filter((m) => m.type === 'event').pop();
        await ws.handleMessage({ type: 'noop', source: 'event', event_name: 'add', ref: frame.ref, reset_form: true });
        expect(input.value).toBe('');
    });

    it('leaves the form alone on a noop without it (control)', async () => {
        const { window, sent, input } = env();
        await new Promise((r) => setTimeout(r, 10));
        const ws = window.djust.liveViewInstance;
        ws.viewMounted = true;
        input.value = 'typed';
        ws.sendEvent('add', {}, window.document.getElementById('go'));
        const frame = sent.filter((m) => m.type === 'event').pop();
        await ws.handleMessage({ type: 'noop', source: 'event', event_name: 'add', ref: frame.ref });
        expect(input.value).toBe('typed');
    });

    it('resets the form over SSE', async () => {
        const { window, input } = env();
        const sse = new window.djust.LiveViewSSE();
        input.value = 'typed';
        await sse.handleMessage({ type: 'noop', source: 'event', event_name: 'add', reset_form: true });
        expect(input.value).toBe('');
    });

    it('resets the form over the HTTP fallback', async () => {
        const page = await loadPage(
            `<!doctype html><html><head></head><body><div dj-root dj-view="app.View">${FORM}</div></body></html>`);
        try {
            const input = page.win.document.getElementById('txt');
            input.value = 'typed';
            await page.send('add', {}, { type: 'noop', source: 'event', event_name: 'add', reset_form: true },
                page.win.document.getElementById('go'));
            expect(input.value).toBe('');
        } finally { page.close(); }
    });
});
