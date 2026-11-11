/**
 * #3988: a reconnecting page view sends its signed client-state token back with
 * the mount frame, on both transports. Without it a `state(persist="client")`
 * field reset on every reconnect, because the server restores only from a token
 * the mount frame carries.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const VIEW = 'test.views.Page';
const TOKEN = 'signed.opaque.token';

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

function storeToken(dom, view = VIEW, token = TOKEN) {
    // What a mount or an acknowledged event leaves behind (storeSignedSnapshot).
    dom.window.eval(`
        if (!window.djust._clientState) window.djust._clientState = Object.create(null);
        window.djust._clientState[${JSON.stringify(view)}] = ${JSON.stringify(token)};
    `);
}

function wsMount(ws, view, options) {
    ws.mount(view, {}, options);
    return ws.ws.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'mount').pop();
}

function sseMount(dom, view) {
    const sse = new dom.window.djust.LiveViewSSE();
    sse.enabled = true;
    const sent = [];
    sse.sendMessage = (frame) => { sent.push(frame); return true; };
    sse._sendMountFrame(view, {});
    return sent[0];
}

const TRANSPORTS = [
    ['WebSocket', (dom, ws, view) => wsMount(ws, view, { primary: true })],
    ['SSE', (dom, ws, view) => sseMount(dom, view)],
];

describe.each(TRANSPORTS)('the %s mount frame after a reconnect (#3988)', (_name, mount) => {
    it('carries the page view\'s token, verbatim', async () => {
        const { dom, ws } = await loadPage();
        storeToken(dom);
        dom.window.djust._isReconnect = true;
        expect(mount(dom, ws, VIEW).state_snapshot).toEqual({ view_slug: VIEW, state_json: TOKEN });
    });

    it('carries nothing on a first mount, even with a token stored', async () => {
        const { dom, ws } = await loadPage();
        storeToken(dom);
        dom.window.djust._isReconnect = false;
        expect(mount(dom, ws, VIEW)).not.toHaveProperty('state_snapshot');
    });

    it('carries nothing when no token was stored', async () => {
        const { dom, ws } = await loadPage();
        dom.window.djust._isReconnect = true;
        expect(mount(dom, ws, VIEW)).not.toHaveProperty('state_snapshot');
    });

    it('does not send another view\'s token', async () => {
        const { dom, ws } = await loadPage();
        storeToken(dom, 'test.views.Other');
        dom.window.djust._isReconnect = true;
        expect(mount(dom, ws, VIEW)).not.toHaveProperty('state_snapshot');
    });

    it('does not read a token off the prototype chain', async () => {
        const { dom, ws } = await loadPage();
        dom.window.eval('window.djust._clientState = {};');
        dom.window.djust._isReconnect = true;
        expect(mount(dom, ws, 'constructor')).not.toHaveProperty('state_snapshot');
    });
});

describe('a view mounted beside the page view (#3988)', () => {
    it('never carries the page view\'s token on the WebSocket', async () => {
        const { dom, ws } = await loadPage();
        storeToken(dom);
        dom.window.djust._isReconnect = true;
        const frame = wsMount(ws, VIEW, { targetId: 'lazy-1' });
        expect(frame.target_id).toBe('lazy-1');
        expect(frame).not.toHaveProperty('state_snapshot');
    });
});
