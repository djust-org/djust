// #3658: a 1013 close (the server's outbound buffer overflowed: the client
// read too slowly) takes the ordinary reconnect-and-resync path.
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
        // From 00-namespace.js: publishes the deprecated window global.
        defineDeprecatedGlobal: () => {},
    };
    // The transport and the frame handling it shares with SSE run in one
    // scope, as they do in the bundle.
    for (const names of [['02b-safe-nav.js'], ['02c-frame-dispatch.js', '03-websocket.js']]) {
        // eslint-disable-next-line no-new-func
        new Function(...Object.keys(scope), names.map(source).join('\n'))(...Object.values(scope));
    }
    const client = new window.djust.LiveViewWebSocket();
    client.onTransportFailed = vi.fn();
    window.djust.stickyPreserve = { clearStash: vi.fn() };
    const refused = [];
    window.addEventListener('djust:auth-refused', event => refused.push(event.detail));
    const close = (socket, code) => { socket.readyState = 3; socket.onclose({ code, reason: '' }); };
    return { ...scope, client, sockets, timers, refused, close };
}

describe('#3658 slow-consumer close (1013)', () => {
    it('reconnects with backoff and keeps pending edits, like a network drop', () => {
        const h = harness();
        h.client.connect();
        h.client.ws.readyState = 1;
        h.client.ws.onopen({});
        h.close(h.client.ws, 1013);
        expect(h.client.enabled).toBe(true);
        expect(h.refused).toHaveLength(0);
        expect(h.flushPendingRateLimits).toHaveBeenCalledOnce();
        expect(h.cancelPendingRateLimits).not.toHaveBeenCalled();
        expect(h.client.onTransportFailed).not.toHaveBeenCalled();
        expect(h.timers).toHaveLength(1);
        h.timers.shift()();
        expect(h.sockets).toHaveLength(2);
    });

    it('a 1013 on a reconnected socket schedules another reconnect', () => {
        const h = harness();
        h.client.connect();
        h.close(h.client.ws, 1013);
        h.timers.shift()();
        h.close(h.client.ws, 1013);
        expect(h.client.enabled).toBe(true);
        expect(h.timers).toHaveLength(1);
        h.timers.shift()();
        expect(h.sockets).toHaveLength(3);
    });
});
