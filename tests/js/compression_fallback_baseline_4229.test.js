/**
 * After a patch-compression `html_update`, the next event is a `patch` (#4229).
 *
 * The server keeps the tree it serialized the fallback frame from as its diff
 * baseline, and the frame's version is the successor of the one before, so the
 * client must accept the frame, then apply the following patch (addressed
 * against the DOM the frame installed) without a `request_html` recovery or a
 * reload, ending with the DOM of a fresh render. Frame shapes are the ones
 * `python/djust/tests/test_compression_fallback_baseline_4229.py` receives over
 * a real WebSocket.
 */
import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const ROWS = 150;

function inner(offset, note) {
    let html = '<ul dj-id="1">';
    for (let i = 0; i < ROWS; i++) html += `<li dj-id="${(i + 2).toString(36)}">${offset + i}</li>`;
    return html + `</ul><p class="note" dj-id="${(ROWS + 2).toString(36)}">${note}</p>`;
}

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html><html><body><div dj-root dj-view="t.Grid" dj-id="0">${inner(0, 'start')}</div></body></html>`,
        { runScripts: 'dangerously' });
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (value) => String(value).replace(/([^\w-])/g, '\\$1');
    }
    const sent = [];
    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send(data) { sent.push(JSON.parse(data)); }
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.eval(clientCode);
    return { dom, sent };
}

describe('compression-fallback html_update keeps the baseline (#4229)', () => {
    it('applies the patch that follows the fallback frame, with no recovery', async () => {
        const { dom, sent } = createDom();
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.enabled = true;
        ws.ws = new dom.window.WebSocket();
        ws.viewMounted = true;
        await ws.handleMessage({ type: 'mount', view: 't.Grid', version: 1, html: inner(0, 'start') });

        await ws.handleMessage({
            type: 'html_update', html: inner(1000, 'start'), version: 2, event_name: 'shift', source: 'event',
        });
        await ws.handleMessage({
            type: 'patch', patches: [{ type: 'SetText', path: [1, 0], text: 'after' }],
            version: 3, event_name: 'touch', source: 'event',
        });
        await ws.handleMessage({
            type: 'patch', patches: [{ type: 'SetText', path: [1, 0], text: 'again' }],
            version: 4, event_name: 'touch', source: 'event',
        });

        expect(sent.filter((m) => m.type === 'request_html')).toEqual([]);
        const root = dom.window.document.querySelector('[dj-root]');
        const fresh = new JSDOM(`<div>${inner(1000, 'again')}</div>`).window.document.body.firstChild;
        const text = (el) => [...el.querySelectorAll('li, p')].map((n) => n.textContent);
        expect(text(root)).toEqual(text(fresh));
        expect(root.querySelector('li').getAttribute('dj-id')).toBe('2');
    });

    it('gate-off: a restarted version after the fallback frame asks for recovery', async () => {
        const { dom, sent } = createDom();
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.enabled = true;
        ws.ws = new dom.window.WebSocket();
        ws.viewMounted = true;
        await ws.handleMessage({ type: 'mount', view: 't.Grid', version: 1, html: inner(0, 'start') });
        await ws.handleMessage({ type: 'html_update', html: inner(1000, 'start'), version: 2, source: 'event' });
        expect(sent.filter((m) => m.type === 'request_html')).toEqual([]);
        // What a reset Rust counter put on the wire over SSE before the fix.
        await ws.handleMessage({ type: 'html_update', html: inner(1000, 'after'), version: 1, source: 'event' });
        expect(sent.filter((m) => m.type === 'request_html').length).toBeGreaterThan(0);
    });
});
