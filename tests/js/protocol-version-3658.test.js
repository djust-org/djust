/**
 * #3658 (TC9 slice 3): the wire-protocol version and the self-reload.
 *
 * The client sends its protocol with every mount verb, on the SSE stream GET
 * and as the X-Djust-Protocol header of a fallback POST. When the server names
 * another protocol (connect, sse_connect, mount, the HTTP header, a 409, or a
 * `reload` frame with reason "protocol"), the page reloads once and clears the
 * service-worker caches. A second mismatch within 30 s does not reload: it
 * stops the transport and dispatches `djust:error` with `protocol_mismatch`.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM, VirtualConsole } from 'jsdom';
import fs from 'fs';
import { PROTOCOL } from './_helpers.js';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/** A page with the full bundle; `reloads` counts location.reload() calls. */
function page({ storage = true, websocket = true } = {}) {
    const virtualConsole = new VirtualConsole();
    const reloads = [];
    virtualConsole.on('jsdomError', (e) => {
        if (/navigation/.test(String(e.message))) reloads.push(e.message);
    });
    const dom = new JSDOM(
        '<!DOCTYPE html><html><head></head><body>'
        + '<div dj-view="test.V" dj-root><button id="btn" dj-click="save">go</button></div>'
        + '</body></html>',
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true, virtualConsole }
    );
    const { window } = dom;
    const sent = [];
    const sockets = [];
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(url) {
            this.url = url;
            this.readyState = MockWebSocket.OPEN;
            sockets.push(this);
        }
        send(message) { sent.push(JSON.parse(message)); }
        close() { this.readyState = MockWebSocket.CLOSED; }
    }
    window.WebSocket = MockWebSocket;
    if (!websocket) window.DJUST_USE_WEBSOCKET = false;
    if (!storage) {
        Object.defineProperty(window, 'sessionStorage', {
            configurable: true,
            get() { throw new Error('SecurityError'); },
        });
    }
    window.console = { log() {}, warn() {}, debug() {}, info() {}, error() {} };
    window.eval(clientCode);
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    const clearCaches = vi.fn(() => true);
    window.djust._sw = { clearCaches, applyMountMetadata() {} };
    const errors = [];
    window.addEventListener('djust:error', (e) => errors.push(e.detail));
    return { dom, window, sent, sockets, reloads, clearCaches, errors };
}

describe('the client names its protocol', () => {
    it('is the integer _protocol.py pins', () => {
        const { window } = page();
        expect(window.djust.PROTOCOL).toBe(PROTOCOL);
        expect(Number.isInteger(PROTOCOL)).toBe(true);
    });

    it('on every WebSocket mount verb, and on no other frame', () => {
        const { window, sent } = page();
        const lv = window.djust.liveViewInstance;
        lv.sendMessage({ type: 'mount', view: 'a.V' });
        lv.sendMessage({ type: 'mount_batch', views: [] });
        lv.sendMessage({ type: 'live_redirect_mount', view: 'a.W' });
        lv.sendMessage({ type: 'event', event: 'x', params: {} });
        expect(sent.map((f) => f.protocol)).toEqual([PROTOCOL, PROTOCOL, PROTOCOL, undefined]);
    });

    it('on the SSE stream GET and the SSE message POST', async () => {
        const { window } = page({ websocket: false });
        class EventSourceStub { constructor(url) { this.url = url; EventSourceStub.last = this; } close() {} }
        window.EventSource = EventSourceStub;
        const posts = [];
        window.fetch = vi.fn(async (url, init) => {
            posts.push(init);
            return { ok: true, status: 200, json: async () => ({}) };
        });
        const sse = new window.djust.LiveViewSSE();
        sse.connect('app.V', {});
        expect(new URL(EventSourceStub.last.url, 'http://localhost/').searchParams.get('protocol'))
            .toBe(String(PROTOCOL));
        sse.sendMessage({ type: 'mount', view: 'app.V' });
        await Promise.resolve();
        expect(posts[0].headers['X-Djust-Protocol']).toBe(String(PROTOCOL));
        expect(JSON.parse(posts[0].body).protocol).toBe(PROTOCOL);
    });

    it('as a header on the HTTP fallback POST', async () => {
        const { window } = page({ websocket: false });
        const posts = [];
        window.fetch = vi.fn(async (url, init) => {
            posts.push(init);
            return { ok: true, status: 200, headers: { get: () => null },
                json: async () => ({ type: 'render', effects: [] }) };
        });
        await window.djust.handleEvent('save', {});
        expect(posts[0].headers['X-Djust-Protocol']).toBe(String(PROTOCOL));
    });
});

describe('a server in another protocol', () => {
    it('reloads on a connect frame that names none, clearing the SW caches', async () => {
        const { window, reloads, clearCaches, sent } = page();
        await window.djust.liveViewInstance.handleMessage({ type: 'connect', session_id: 's' });
        expect(reloads).toHaveLength(1);
        expect(clearCaches).toHaveBeenCalledTimes(1);
        // The handshake was not acted on: no mount went out.
        expect(sent.filter((f) => f.type === 'mount')).toHaveLength(0);
    });

    it('reloads on a mount frame that names another protocol', async () => {
        const { window, reloads } = page();
        await window.djust.liveViewInstance.handleMessage(
            { type: 'mount', view: 'test.V', protocol: PROTOCOL + 1, html: '<p>x</p>' });
        expect(reloads).toHaveLength(1);
    });

    it('does not reload on a matching connect or mount', async () => {
        const { window, reloads } = page();
        const lv = window.djust.liveViewInstance;
        await lv.handleMessage({ type: 'connect', protocol: PROTOCOL, session_id: 's' });
        await lv.handleMessage({ type: 'mount', view: 'test.V', protocol: PROTOCOL, version: 1 });
        expect(reloads).toHaveLength(0);
    });

    it('reloads on an sse_connect that names another protocol', async () => {
        const { window, reloads } = page({ websocket: false });
        const sse = new window.djust.LiveViewSSE();
        await sse._handleMessageImpl({ type: 'sse_connect', protocol: PROTOCOL + 1, session_id: 's' });
        expect(reloads).toHaveLength(1);
    });

    it('reloads on an HTTP answer whose header names another protocol, without applying it', async () => {
        const { window, reloads } = page({ websocket: false });
        window.fetch = vi.fn(async () => ({
            ok: true, status: 200,
            headers: { get: (name) => (name === 'X-Djust-Protocol' ? String(PROTOCOL + 1) : null) },
            json: async () => ({ type: 'render', html: '<p id="gone">replaced</p>', effects: [] }),
        }));
        await window.djust.handleEvent('save', {});
        expect(reloads).toHaveLength(1);
        expect(window.document.getElementById('btn')).not.toBeNull();
    });

    it('reloads on the HTTP 409 protocol_mismatch envelope', async () => {
        const { window, reloads, errors } = page({ websocket: false });
        window.fetch = vi.fn(async () => ({
            ok: false, status: 409, headers: { get: () => null },
            json: async () => ({ type: 'error', code: 'protocol_mismatch', recoverable: false,
                error: 'This page is out of date. Reload to continue.' }),
        }));
        await window.djust.handleEvent('save', {});
        expect(reloads).toHaveLength(1);
        expect(errors).toHaveLength(0);
    });

    it('obeys a reload frame with reason "protocol" through the same guard', async () => {
        const { window, reloads, clearCaches } = page();
        await window.djust.liveViewInstance.handleMessage({ type: 'reload', reason: 'protocol', protocol: PROTOCOL + 1 });
        expect(reloads).toHaveLength(1);
        expect(clearCaches).toHaveBeenCalledTimes(1);
    });
});

describe('the loop guard', () => {
    it('a second mismatch within 30 s stops the transport and reports protocol_mismatch', async () => {
        const { window, reloads, errors } = page();
        const lv = window.djust.liveViewInstance;
        // The previous page load reloaded for the protocol a moment ago.
        window.sessionStorage.setItem('djust:protocol-reload', String(Date.now() - 1000));
        await lv.handleMessage({ type: 'connect', session_id: 's' });
        expect(reloads).toHaveLength(0);
        expect(lv.enabled).toBe(false);
        expect(errors.map((e) => e.code)).toEqual(['protocol_mismatch']);
    });

    it('reloads again once 30 s have passed', async () => {
        const { window, reloads } = page();
        window.sessionStorage.setItem('djust:protocol-reload', String(Date.now() - 31000));
        await window.djust.liveViewInstance.handleMessage({ type: 'connect', session_id: 's' });
        expect(reloads).toHaveLength(1);
        expect(Number(window.sessionStorage.getItem('djust:protocol-reload'))).toBeGreaterThan(Date.now() - 5000);
    });

    it('without sessionStorage, allows one reload per page load', async () => {
        const { window, reloads, errors } = page({ storage: false });
        const lv = window.djust.liveViewInstance;
        await lv.handleMessage({ type: 'connect', session_id: 's' });
        expect(reloads).toHaveLength(1);
        await lv.handleMessage({ type: 'connect', session_id: 's' });
        expect(reloads).toHaveLength(1);
        expect(errors.map((e) => e.code)).toEqual(['protocol_mismatch']);
    });
});

describe('close code 4409', () => {
    it('does not reconnect, and is not reported as an auth refusal', () => {
        const { window, sockets } = page();
        const lv = window.djust.liveViewInstance;
        lv.enabled = true;
        lv.ws = null;
        lv.connect();
        const socket = sockets[sockets.length - 1];
        const refusals = [];
        window.addEventListener('djust:auth-refused', (e) => refusals.push(e.detail));
        socket.onclose({ code: 4409, reason: 'protocol' });
        expect(lv.enabled).toBe(false);
        expect(window.document.body.hasAttribute('data-dj-reconnect-attempt')).toBe(false);
        expect(refusals).toHaveLength(0);
        const count = sockets.length;
        lv.connect();
        expect(sockets).toHaveLength(count);
    });

    function connected() {
        const ctx = page();
        const lv = ctx.window.djust.liveViewInstance;
        lv.enabled = true;
        lv.ws = null;
        lv.connect();
        return { ...ctx, lv, socket: ctx.sockets[ctx.sockets.length - 1] };
    }

    it('a bare 4409 (the server breaker tripped) goes through the loop guard: reload', () => {
        const { socket, reloads, errors } = connected();
        socket.onclose({ code: 4409, reason: '' });
        expect(reloads).toHaveLength(1);
        expect(errors).toHaveLength(0);
    });

    it('a bare 4409 soon after a protocol reload reports protocol_mismatch instead', () => {
        const { window, socket, reloads, errors } = connected();
        window.sessionStorage.setItem('djust:protocol-reload', String(Date.now() - 1000));
        socket.onclose({ code: 4409, reason: '' });
        expect(reloads).toHaveLength(0);
        expect(errors.map((e) => e.code)).toEqual(['protocol_mismatch']);
    });

    it('a 4409 after the reload frame on the same socket adds no second verdict', async () => {
        const { lv, socket, reloads, errors } = connected();
        await lv.handleMessage({ type: 'reload', reason: 'protocol', protocol: PROTOCOL });
        socket.onclose({ code: 4409, reason: '' });
        expect(reloads).toHaveLength(1);
        expect(errors).toHaveLength(0);
    });
});
