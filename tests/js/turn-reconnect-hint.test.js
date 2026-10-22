/**
 * ADR-044: a WebSocket mount sent after a reconnect carries `reconnect: true`,
 * so the server runs it as a `reconnect` turn. A hint only: the server runs
 * the same turn-middleware chain for mount and reconnect.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

async function loadWsPage() {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.Page" dj-root><p>content</p></div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/page/' });
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; this.sent = []; }
            send(d) { this.sent.push(d); }
            close() {}
        };
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

function mountFrames(ws) {
    return ws.ws.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'mount');
}

describe('reconnect hint on the mount frame (ADR-044)', () => {
    it('a mount after a reconnect says so', async () => {
        const { dom, ws } = await loadWsPage();
        dom.window.djust._isReconnect = true;
        ws.mount('test.views.Page', {}, { primary: true });
        expect(mountFrames(ws)[0].reconnect).toBe(true);
    });

    it('a slot remounted after a reconnect says so too', async () => {
        const { dom, ws } = await loadWsPage();
        dom.window.djust._isReconnect = true;
        ws.mount('test.views.Other', {}, { targetId: 'lazy-1' });
        const [frame] = mountFrames(ws);
        expect(frame.reconnect).toBe(true);
        expect(frame.target_id).toBe('lazy-1');
    });

    it('a first mount carries no hint', async () => {
        const { dom, ws } = await loadWsPage();
        dom.window.djust._isReconnect = false;
        ws.mount('test.views.Page', {}, { primary: true });
        expect('reconnect' in mountFrames(ws)[0]).toBe(false);
    });
});
