/**
 * #4164 item 1: the turn serial. Reply and mount frames carry `state_serial`
 * (the serial of the state the page is shown); a reconnecting page view sends
 * the last one back with its mount frame, on both transports, so the server
 * can tell when it restored an older state. The notice it then sends
 * (`state_not_restored`, transient) is shown like a deferred-save status.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const VIEW = 'test.views.Page';

async function loadPage() {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="${VIEW}" dj-root><p>content</p></div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/page/' });
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; this.sent = []; }
            send(d) { this.sent.push(d); }
            close() {}
        };
        window.EventSource = class { constructor() {} close() {} };
        window.requestAnimationFrame = function(cb) { return setTimeout(cb, 0); };
        window.scrollTo = function() {};
    `);
    for (const k of ['log', 'warn', 'error', 'debug', 'info']) dom.window.console[k] = () => {};
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const ws = dom.window.djust.liveViewInstance;
    ws.ws.sent.length = 0;
    return { dom, ws };
}

async function shown(dom, frame) {
    // What a frame from the server leaves behind (02c-frame-dispatch.js ->
    // storeSignedSnapshot), through the socket's own message handler.
    const ws = dom.window.djust.liveViewInstance;
    ws.primaryViewPath = VIEW;
    try {
        await ws.handleMessage(frame, ws.ws);
    } catch (_e) {
        // The rest of the frame's handling may need a fuller page; the
        // serial is recorded before it runs.
    }
}

function wsMount(dom, ws) {
    ws.mount(VIEW, {}, { primary: true });
    return ws.ws.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'mount').pop();
}

function sseMount(dom) {
    const sse = new dom.window.djust.LiveViewSSE();
    sse.enabled = true;
    const sent = [];
    sse.sendMessage = (frame) => { sent.push(frame); return true; };
    sse._sendMountFrame(VIEW, {});
    return sent[0];
}

describe.each([
    ['WebSocket', wsMount],
    ['SSE', (dom) => sseMount(dom)],
])('the %s mount frame after a reconnect', (_name, mount) => {
    it('carries the serial of the last state a frame showed', async () => {
        const { dom, ws } = await loadPage();
        await shown(dom, { type: 'mount', view: VIEW, state_serial: 2 });
        await shown(dom, { type: 'patch', source: 'event', view: VIEW, state_serial: 5 });
        dom.window.djust._isReconnect = true;
        expect(mount(dom, ws).state_serial).toBe(5);
    });

    it('carries none on a first mount', async () => {
        const { dom, ws } = await loadPage();
        await shown(dom, { type: 'patch', source: 'event', view: VIEW, state_serial: 5 });
        dom.window.djust._isReconnect = false;
        expect(mount(dom, ws).state_serial).toBeUndefined();
    });

    it('carries none when no frame named a serial, or it was 0', async () => {
        const { dom, ws } = await loadPage();
        dom.window.djust._isReconnect = true;
        expect(mount(dom, ws).state_serial).toBeUndefined();
        await shown(dom, { type: 'mount', view: VIEW, state_serial: 0 });
        expect(mount(dom, ws).state_serial).toBeUndefined();
    });
});

describe('what records a serial', () => {
    it('ignores error frames, child frames and frames of a view beside the page', async () => {
        const { dom } = await loadPage();
        await shown(dom, { type: 'patch', source: 'event', view: VIEW, state_serial: 3 });
        await shown(dom, { type: 'error', view: VIEW, state_snapshot_signed: null, state_serial: 9 });
        await shown(dom, { type: 'patch', source: 'event', view: VIEW, target_id: 'side', state_serial: 9 });
        await shown(dom, { type: 'patch', source: 'event', view: 'other.View', state_serial: 9 });
        expect(dom.window.djust._stateSerial[VIEW]).toBe(3);
    });

    it('ignores a serial that is not a whole number', async () => {
        const { dom } = await loadPage();
        await shown(dom, { type: 'mount', view: VIEW, state_serial: '7' });
        await shown(dom, { type: 'mount', view: VIEW, state_serial: 1.5 });
        expect(dom.window.djust._stateSerial).toBeUndefined();
    });
});

describe('the not-restored notice', () => {
    it('is shown as a status, text only, in every mode', async () => {
        const { dom } = await loadPage();
        dom.window.dispatchEvent(new dom.window.CustomEvent('djust:error', {
            detail: {
                error: 'Your most recent changes could not be restored.',
                code: 'state_not_restored',
                transient: true,
                view: VIEW,
            },
        }));
        const status = dom.window.document.getElementById('djust-save-status');
        expect(status).not.toBeNull();
        expect(status.textContent).toBe('Your most recent changes could not be restored.');
    });
});
