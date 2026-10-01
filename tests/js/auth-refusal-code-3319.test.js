// #3319: a refusal's stable `code` reaches the page without parsing message
// text. WebSocket and SSE error frames put it on `djust:error`, the HTTP
// fallback does the same from the 403 body, and the 4401/4403 close that
// follows a refusal frame reports it as `error_code` on `djust:auth-refused`.
import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const doms = [];

function env() {
    const dom = new JSDOM('<!DOCTYPE html><html><body><div dj-root dj-view="t.V"></div></body></html>', {
        url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true,
    });
    doms.push(dom);
    const { window } = dom;
    window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    const sockets = [];
    class FakeWebSocket {
        constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
        send() {}
        close() { this.readyState = 3; }
    }
    FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSED = 3;
    window.WebSocket = FakeWebSocket;
    window.EventSource = vi.fn(function () { return { close: vi.fn(), readyState: 1 }; });
    window.EventSource.CLOSED = 2;
    window.EventSource.OPEN = 1;
    window.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
    window.eval(client);
    const errors = [];
    const refused = [];
    window.addEventListener('djust:error', e => errors.push(e.detail));
    window.addEventListener('djust:auth-refused', e => refused.push(e.detail));
    return { window, sockets, errors, refused };
}

async function wsClient() {
    const e = env();
    const ws = new e.window.djust.LiveViewWebSocket();
    ws.connect();
    ws.ws.readyState = 1;
    const send = async frame => {
        ws.ws.onmessage({ data: JSON.stringify(frame) });
        await ws._inflight;
    };
    const close = code => { ws.ws.readyState = 3; ws.ws.onclose({ code, reason: 'denied' }); };
    return { ...e, ws, send, close };
}

describe('#3319 refusal code on the WebSocket client', () => {
    it('puts the frame code on djust:error', async () => {
        const c = await wsClient();
        await c.send({ type: 'error', error: 'Permission denied', code: 'permission_denied' });
        expect(c.errors.map(e => [e.error, e.code])).toEqual([['Permission denied', 'permission_denied']]);
    });

    it('an error frame without a code reports null', async () => {
        const c = await wsClient();
        await c.send({ type: 'error', error: 'boom' });
        expect(c.errors[0].code).toBeNull();
    });

    it.each([4401, 4403])('the %i close after a refusal frame reports error_code', async code => {
        const c = await wsClient();
        await c.send({ type: 'error', error: 'Event authorization failed. Please reload the page.', code: 'permission_denied' });
        c.close(code);
        expect(c.refused).toEqual([{ code, reason: 'denied', error_code: 'permission_denied' }]);
    });

    it('a close with no refusal frame reports a null error_code', async () => {
        const c = await wsClient();
        c.close(4403);
        expect(c.refused).toEqual([{ code: 4403, reason: 'denied', error_code: null }]);
    });

    it('a stale refusal does not label a later close', async () => {
        const c = await wsClient();
        await c.send({ type: 'error', error: 'Permission denied', code: 'permission_denied' });
        await c.send({ type: 'noop' });
        c.close(4403);
        expect(c.refused[0].error_code).toBeNull();
    });

    it('a non-refusal error code never becomes the refusal code', async () => {
        const c = await wsClient();
        await c.send({ type: 'error', error: 'State unavailable.', code: 'state_error' });
        c.close(4403);
        expect(c.refused[0].error_code).toBeNull();
    });
});

describe('#3319 refusal code on SSE and the HTTP fallback', () => {
    it('SSE puts the frame code on djust:error', async () => {
        const e = env();
        const sse = new e.window.djust.LiveViewSSE();
        await sse.handleMessage({ type: 'error', error: 'Permission denied', code: 'permission_denied' });
        expect(e.errors.map(x => [x.error, x.code])).toEqual([['Permission denied', 'permission_denied']]);
    });

    it('the HTTP 403 body code reaches djust:error', async () => {
        const e = env();
        e.window.DJUST_USE_WEBSOCKET = false;
        e.window.fetch = async () => ({
            ok: false, status: 403,
            json: async () => ({ error: 'Permission denied', code: 'permission_denied' }),
        });
        await e.window.djust.handleEvent('purge', {});
        expect(e.errors.map(x => [x.error, x.code])).toEqual([['Permission denied', 'permission_denied']]);
    });
});
