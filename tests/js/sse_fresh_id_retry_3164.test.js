/**
 * #3164 review: the server refuses (409) a stream GET whose session id is
 * live under another owner — e.g. a tab reconnecting after a login or logout
 * in another tab rotated its session. EventSource never retries a non-200, so
 * the client retries ONCE with a fresh session id when its stream closes
 * before the first `sse_connect` ack.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function setup() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><head></head><body></body></html>',
        { url: 'http://localhost:8000/search/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    const streams = [];
    const warnings = [];
    class EventSourceStub {
        constructor(url) {
            this.url = url;
            this.readyState = 0;
            streams.push(this);
        }
        close() { this.readyState = 2; }
    }
    EventSourceStub.CONNECTING = 0;
    EventSourceStub.OPEN = 1;
    EventSourceStub.CLOSED = 2;
    window.EventSource = EventSourceStub;
    window.DJUST_USE_WEBSOCKET = false;
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...a) => warnings.push(a),
    };
    window.eval(clientCode);
    const sse = new window.djust.LiveViewSSE();
    return { window, sse, streams, warnings };
}

const idOf = (stream) => stream.url.match(/sse\/([0-9a-f-]+)\//)[1];

function refuse(stream) {
    // What EventSource does on a 409: fail the connection, readyState CLOSED.
    stream.readyState = 2;
    stream.onerror(new Error('409'));
}

describe('#3164 SSE fresh-id retry before the first ack', () => {
    it('reconnects once with a new session id when refused before the ack', () => {
        const { sse, streams } = setup();
        sse.connect('app.views.SearchView', { q: 'x' });
        expect(streams).toHaveLength(1);
        refuse(streams[0]);

        expect(streams).toHaveLength(2);
        expect(idOf(streams[1])).not.toBe(idOf(streams[0]));
        expect(sse.sessionId).toBe(idOf(streams[1]));
        expect(streams[1].url).toContain('view=app.views.SearchView');
        expect(streams[1].url).toContain('q=x');
        expect(sse.enabled).toBe(true);
    });

    it('retries only once: a second refusal disables the transport', () => {
        const { sse, streams, warnings } = setup();
        sse.connect('app.views.SearchView', {});
        refuse(streams[0]);
        refuse(streams[1]);

        expect(streams).toHaveLength(2);
        expect(sse.enabled).toBe(false);
        expect(warnings.some((w) => String(w[0]).includes('EventSource closed unexpectedly'))).toBe(true);
    });

    it("the review's scenario: an acked stream drops, and its reconnect is refused", () => {
        // Tab A's stream was acked long ago. Its connection blips (EventSource
        // reports an error while CONNECTING and re-requests the same URL);
        // meanwhile a login in tab B rotated the owner, so that reconnect gets
        // a 409 and EventSource gives up (CLOSED). The page must not go dead.
        const { sse, streams } = setup();
        sse.connect('app.views.SearchView', {});
        const ack = (s) => s.onmessage({ data: JSON.stringify({ type: 'sse_connect', session_id: idOf(s) }) });
        ack(streams[0]);
        streams[0].readyState = 0; // the drop: EventSource reconnects itself
        streams[0].onerror(new Error('network'));
        expect(streams).toHaveLength(1);
        refuse(streams[0]); // the reconnect's 409

        expect(streams).toHaveLength(2);
        expect(idOf(streams[1])).not.toBe(idOf(streams[0]));
        expect(sse.enabled).toBe(true);
    });

    it('does not regenerate the id when an acked connection closes without a reconnect', () => {
        const { sse, streams } = setup();
        sse.connect('app.views.SearchView', {});
        streams[0].onmessage({ data: JSON.stringify({ type: 'sse_connect', session_id: idOf(streams[0]) }) });
        refuse(streams[0]);

        expect(streams).toHaveLength(1);
        expect(sse.enabled).toBe(false);
    });

    it('an ack re-arms the one retry for a later owner change', () => {
        const { sse, streams } = setup();
        sse.connect('app.views.SearchView', {});
        refuse(streams[0]); // fresh id -> streams[1]
        streams[1].onmessage({ data: JSON.stringify({ type: 'sse_connect', session_id: idOf(streams[1]) }) });
        expect(sse._freshIdRetried).toBe(false);
    });

    it('a transient error while EventSource is still reconnecting is left to it', () => {
        const { sse, streams } = setup();
        sse.connect('app.views.SearchView', {});
        streams[0].readyState = 0; // CONNECTING: EventSource retries by itself
        streams[0].onerror(new Error('blip'));
        expect(streams).toHaveLength(1);
        expect(sse.enabled).toBe(true);
    });
});
