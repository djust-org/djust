// #3501: the server sends `presence_event` frames (broadcast_to_presence,
// LiveCursorMixin's cursor_move) and the client's frame switch had no case
// for them, so every one was silently dropped. The client now dispatches a
// `djust:presence` CustomEvent on window with `{event, payload}` as detail.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const doms = [];

afterEach(() => {
    while (doms.length) doms.pop().window.close();
});

function wsClient() {
    const dom = new JSDOM('<!DOCTYPE html><html><body><div dj-root dj-view="t.V"></div></body></html>', {
        url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true,
    });
    doms.push(dom);
    const { window } = dom;
    window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    class FakeWebSocket {
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    }
    FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSED = 3;
    window.WebSocket = FakeWebSocket;
    window.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
    window.eval(client);
    const ws = new window.djust.LiveViewWebSocket();
    ws.connect();
    ws.ws.readyState = 1;
    const send = async frame => {
        ws.ws.onmessage({ data: JSON.stringify(frame) });
        await ws._inflight;
    };
    return { window, send };
}

describe('#3501 presence_event frames reach the page', () => {
    it('dispatches djust:presence on window with event and payload', async () => {
        const c = wsClient();
        const seen = [];
        c.window.addEventListener('djust:presence', e => seen.push(e.detail));
        await c.send({ type: 'presence_event', event: 'cursor_move', payload: { user_id: 'u1', x: 4, y: 9 } });
        expect(seen).toEqual([{ event: 'cursor_move', payload: { user_id: 'u1', x: 4, y: 9 } }]);
    });

    it('dispatches one event per frame, in order', async () => {
        const c = wsClient();
        const seen = [];
        c.window.addEventListener('djust:presence', e => seen.push(e.detail.event));
        await c.send({ type: 'presence_event', event: 'wave', payload: {} });
        await c.send({ type: 'presence_event', event: 'typing', payload: { on: true } });
        expect(seen).toEqual(['wave', 'typing']);
    });

    it('is not delivered as a djust:push_event', async () => {
        const c = wsClient();
        const pushed = [];
        c.window.addEventListener('djust:push_event', e => pushed.push(e.detail));
        await c.send({ type: 'presence_event', event: 'wave', payload: {} });
        expect(pushed).toEqual([]);
    });
});
