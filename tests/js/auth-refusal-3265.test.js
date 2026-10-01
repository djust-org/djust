/* eslint-disable no-script-url -- deliberate unsafe navigation inputs */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed local source names below
const source = name => fs.readFileSync(`python/djust/static/djust/src/${name}`, 'utf8');
const doms = [];
afterEach(() => { for (const dom of doms.splice(0)) dom.window.close(); });

// Real transport callbacks and queued message dispatcher, with only browser
// I/O and unrelated sibling-module effects supplied by the harness.
function harness() {
    const dom = new JSDOM('<body></body>', { url: 'https://app.example/' });
    doms.push(dom);
    const window = {
        location: { origin: 'https://app.example', protocol: 'https:', host: 'app.example', href: '' },
        addEventListener: dom.window.addEventListener.bind(dom.window),
        dispatchEvent: dom.window.dispatchEvent.bind(dom.window),
    };
    const sockets = [];
    class WebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
        send() {}
        close() { this.readyState = 3; }
    }
    const timers = [];
    const scope = {
        window, document: dom.window.document, WebSocket, URL,
        CustomEvent: dom.window.CustomEvent,
        globalThis: { djustDebug: false }, console: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
        setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout: () => {},
        notifyHooksDisconnected: vi.fn(), notifyHooksReconnected: vi.fn(),
        cancelPendingRateLimits: vi.fn(), flushPendingRateLimits: vi.fn(),
        optimisticUpdates: new Map(), pendingEvents: new Map(),
        cancelEventRequests: vi.fn(), takeServerUpdates: vi.fn(), clearOptimisticPending: vi.fn(),
        stripClientOwnedFrameFlags: () => {}, _recordParameterContractFrame: () => {},
        storeSignedSnapshot: () => {},
    };
    for (const name of ['02b-safe-nav.js', '03-websocket.js']) {
        // eslint-disable-next-line no-new-func
        new Function(...Object.keys(scope), source(name))(...Object.values(scope));
    }
    const client = new window.djust.LiveViewWebSocket();
    client.onTransportFailed = vi.fn();
    window.djust.stickyPreserve = { clearStash: vi.fn() };
    const refused = [];
    window.addEventListener('djust:auth-refused', event => refused.push(event.detail));
    const close = (socket, code) => { socket.readyState = 3; socket.onclose({ code, reason: 'denied' }); };
    const receive = async data => {
        client.ws.onmessage({ data: JSON.stringify(data) });
        await client._inflight;
        expect(scope.console.error).not.toHaveBeenCalled();
    };
    return { ...scope, client, sockets, timers, refused, close, receive };
}

describe('#3265 authentication refusal', () => {
    it.each([4401, 4403])('stops reconnect and fallback on %i, cancelling pending edits', code => {
        const h = harness();
        h.client.connect();
        h.client.ws.readyState = 1;
        h.client.ws.onopen({});
        h.close(h.client.ws, code);
        expect(h.client.enabled).toBe(false);
        expect(h.client.viewMounted).toBe(false);
        expect(h.timers).toHaveLength(0);
        expect(h.cancelPendingRateLimits).toHaveBeenCalledOnce();
        expect(h.flushPendingRateLimits).not.toHaveBeenCalled();
        expect(h.cancelEventRequests).toHaveBeenCalledOnce();
        expect(h.clearOptimisticPending).toHaveBeenCalledOnce();
        expect(h.window.djust.stickyPreserve.clearStash).toHaveBeenCalledOnce();
        expect(h.refused).toEqual([{ code, reason: 'denied', error_code: null }]);
        expect(h.client.onTransportFailed).not.toHaveBeenCalled();
        h.client.connect();
        expect(h.sockets).toHaveLength(1);
    });

    it('does not try the host-root fallback after a prefixed auth refusal', () => {
        const h = harness();
        h.window.djust.wsUrl = () => 'wss://app.example/prefix/ws/live/';
        h.client.connect();
        h.close(h.client.ws, 4403);
        expect(h.sockets).toHaveLength(1);
        expect(h.timers).toHaveLength(0);
    });

    it('retains backoff and edit flushing for an ordinary network close', () => {
        const h = harness();
        h.client.connect();
        h.close(h.client.ws, 1006);
        expect(h.client.enabled).toBe(true);
        expect(h.timers).toHaveLength(1);
        expect(h.flushPendingRateLimits).toHaveBeenCalledOnce();
        expect(h.refused).toHaveLength(0);
        h.timers.shift()();
        expect(h.sockets).toHaveLength(2);
    });

    it('ignores an old socket refusal after a new connection is installed', () => {
        const h = harness();
        h.client.connect();
        const old = h.client.ws;
        old.readyState = 3;
        h.client.connect();
        h.close(old, 4403);
        expect(h.client.enabled).toBe(true);
        expect(h.refused).toHaveLength(0);
        expect(h.cancelPendingRateLimits).not.toHaveBeenCalled();
    });

    it.each(['/accounts/login/?next=%2Fprivate', 'https://auth.example/login'])('navigates to %s through actual socket dispatch', async target => {
        const h = harness();
        h.client.connect();
        await h.receive({ type: 'navigate', to: target });
        expect(h.window.location.href).toBe(target);
    });

    it.each(['reconnect', 'navigation'])('drops a queued old-socket redirect after %s', async mode => {
        const h = harness();
        h.client.connect();
        let release;
        h.client._inflight = new Promise(resolve => { release = resolve; });
        h.client.ws.onmessage({ data: JSON.stringify({ type: 'navigate', to: '/old-login/' }) });
        if (mode === 'navigation') h.client.disconnect();
        else h.client.ws.readyState = 3;
        h.client.connect();
        release();
        await h.client._inflight;
        expect(h.window.location.href).toBe('');
        await h.receive({ type: 'navigate', to: '/current-login/' });
        expect(h.window.location.href).toBe('/current-login/');
    });

    it('ignores an old socket callback before parsing or recording the frame', async () => {
        const h = harness();
        h.client.connect();
        const old = h.client.ws;
        old.readyState = 3;
        h.client.connect();
        old.onmessage({ data: JSON.stringify({ type: 'navigate', to: '/old-login/' }) });
        await h.client._inflight;
        expect(h.client.stats.received).toBe(0);
        expect(h.window.location.href).toBe('');
        expect(h.console.error).not.toHaveBeenCalled();
    });

    it('drops an old socket frame held by debug latency before enqueue', async () => {
        const h = harness();
        h.window.DEBUG_MODE = true;
        h.window.djust._simulatedLatency = 100;
        h.client.connect();
        h.client.ws.onmessage({ data: JSON.stringify({ type: 'navigate', to: '/old-login/' }) });
        expect(h.timers).toHaveLength(1);
        h.client.ws.readyState = 3;
        h.client.connect();
        h.timers.shift()();
        await h.client._inflight;
        expect(h.window.location.href).toBe('');
        expect(h.console.error).not.toHaveBeenCalled();
    });

    it('still applies a queued auth redirect when close arrives before its drain', async () => {
        const h = harness();
        h.client.connect();
        h.client.ws.onmessage({ data: JSON.stringify({ type: 'navigate', to: '/login/' }) });
        h.close(h.client.ws, 4403);
        await h.client._inflight;
        expect(h.window.location.href).toBe('/login/');
        expect(h.refused).toHaveLength(1);
        expect(h.timers).toHaveLength(0);
        expect(h.console.error).not.toHaveBeenCalled();
    });

    it.each(['javascript:alert(1)', 'data:text/html,bad', '//evil.example/', '/\\evil.example/', '/\t/evil.example/', null])('rejects unsafe target %s', async target => {
        const h = harness();
        h.client.connect();
        await h.receive({ type: 'navigate', to: target });
        expect(h.window.location.href).toBe('');
    });
});
