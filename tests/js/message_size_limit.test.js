/**
 * The client refuses to send a frame over the server's max_message_size.
 *
 * The server announces its limit in the `connect` frame. A frame over it is
 * not sent (the server would refuse it anyway); the client reports it the way
 * the server reports a refused frame: an `error` with code
 * `message_too_large`, which logs, fires `djust:error`, and settles the
 * event's pending request and loading state.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createHarness() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><head></head><body>'
        + '<div dj-view="test.V" dj-root><button id="btn" dj-click="save">go</button></div>'
        + '</body></html>',
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    const sent = [];
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
        }
        send(message) {
            sent.push(JSON.parse(message));
        }
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    const errors = [];
    dom.window.console = {
        log: () => {}, warn: () => {}, debug: () => {}, info: () => {},
        error: (...args) => errors.push(args.map(String).join(' ')),
    };
    try {
        dom.window.eval(clientCode);
    } catch (_) { /* ignore missing DOM APIs during init */ }
    try {
        dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    } catch (_) { /* noop */ }

    const lv = dom.window.djust.liveViewInstance;
    if (!lv) throw new Error('harness: liveViewInstance missing — bundle did not init');
    if (!lv.ws) lv.ws = new MockWebSocket();
    lv.enabled = true;
    lv.viewMounted = true;
    const djustErrors = [];
    dom.window.addEventListener('djust:error', (e) => djustErrors.push(e.detail));
    return { window: dom.window, lv, sent, errors, djustErrors };
}

const events = (sent) => sent.filter((m) => m.type === 'event');

describe('max_message_size on the client', () => {
    it('does not send an event frame over the announced limit, and reports it', async () => {
        const { lv, sent, errors, djustErrors } = createHarness();
        await lv.handleMessage({ type: 'connect', session_id: 's1', max_message_size: 1000 });
        lv.viewMounted = true;

        const settled = lv.sendEvent('save', { blob: 'x'.repeat(5000) });
        await settled;
        await lv._inflight;

        expect(events(sent)).toHaveLength(0);
        expect(djustErrors).toHaveLength(1);
        expect(djustErrors[0].code).toBe('message_too_large');
        expect(djustErrors[0].event).toBe('save');
        expect(djustErrors[0].error).toMatch(/^Message too large \(\d+ bytes\)/);
        expect(djustErrors[0].error).toMatch(/1000/);
        expect(djustErrors[0].error).toMatch(/UploadMixin/);
        expect(errors.join('\n')).toMatch(/message_too_large|too large/i);
    });

    it('counts UTF-8 bytes, not UTF-16 code units', async () => {
        const { lv, sent, djustErrors } = createHarness();
        await lv.handleMessage({ type: 'connect', session_id: 's1', max_message_size: 1000 });
        lv.viewMounted = true;

        // 400 characters, 1200 bytes in UTF-8.
        await lv.sendEvent('save', { text: '€'.repeat(400) });
        await lv._inflight;

        expect(events(sent)).toHaveLength(0);
        expect(djustErrors.map((d) => d.code)).toEqual(['message_too_large']);
    });

    it('sends a frame under the limit', async () => {
        const { lv, sent, djustErrors } = createHarness();
        await lv.handleMessage({ type: 'connect', session_id: 's1', max_message_size: 1000 });
        lv.viewMounted = true;

        lv.sendEvent('save', { text: 'short' });

        expect(events(sent)).toHaveLength(1);
        expect(djustErrors).toHaveLength(0);
    });

    it('sends anything when the server announced no limit', async () => {
        const { lv, sent } = createHarness();
        await lv.handleMessage({ type: 'connect', session_id: 's1' });
        lv.viewMounted = true;

        lv.sendEvent('save', { blob: 'x'.repeat(100000) });

        expect(events(sent)).toHaveLength(1);
    });
});
