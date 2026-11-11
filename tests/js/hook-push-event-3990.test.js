/**
 * #3990: a hook's pushEvent goes through the transport's event path, so its
 * frame carries a `ref`, its reply resolves ITS promise, and the reply can no
 * longer resolve an unrelated pending event.
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
  <div dj-view="${VIEW}" dj-root><div id="widget" dj-hook="Widget"></div></div>
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
        window.__hook = null;
        window.djust = window.djust || {};
        window.djust.hooks = { Widget: { mounted() { window.__hook = this; } } };
    `);
    for (const k of ['log', 'warn', 'error', 'debug', 'info']) dom.window.console[k] = () => {};
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const ws = dom.window.djust.liveViewInstance;
    ws.viewMounted = true;
    dom.window.djust.mountHooks(dom.window.document);
    ws.ws.sent.length = 0;
    return { dom, ws, hook: dom.window.__hook };
}

const events = (ws) => ws.ws.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'event');
const settled = async (promise) => {
    let state = 'pending';
    promise.then((v) => { state = v; });
    await new Promise((resolve) => setTimeout(resolve, 0));
    return state;
};

describe('hook pushEvent (#3990)', () => {
    it('sends the event with a ref, like every other event', async () => {
        const { ws, hook } = await loadPage();
        hook.pushEvent('picked', { id: 7 });
        const [frame] = events(ws);
        expect(frame.event).toBe('picked');
        expect(frame.params).toEqual({ id: 7 });
        expect(Number.isInteger(frame.ref)).toBe(true);
    });

    it('returns a promise that resolves with the reply to ITS ref', async () => {
        const { ws, hook } = await loadPage();
        const promise = hook.pushEvent('picked', {});
        expect(typeof promise.then).toBe('function');
        const { ref } = events(ws)[0];
        expect(await settled(promise)).toBe('pending');

        await ws.handleMessage({ type: 'noop', source: 'event', view: VIEW, ref });

        const reply = await settled(promise);
        expect(reply).toMatchObject({ type: 'noop', ref });
    });

    it('its reply does not resolve an unrelated pending event', async () => {
        const { ws, hook } = await loadPage();
        const other = ws.sendEvent('save', {});
        const pushed = hook.pushEvent('picked', {});
        const [saveFrame, pickedFrame] = events(ws);
        expect(saveFrame.ref).not.toBe(pickedFrame.ref);

        await ws.handleMessage({ type: 'noop', source: 'event', view: VIEW, ref: pickedFrame.ref });

        expect(await settled(pushed)).toMatchObject({ ref: pickedFrame.ref });
        expect(await settled(other)).toBe('pending');
    });

    it('resolves null, with a warning, when there is no live connection', async () => {
        const { dom, ws, hook } = await loadPage();
        const warnings = [];
        dom.window.console.warn = (...args) => warnings.push(args.join(' '));
        ws.viewMounted = false;
        const promise = hook.pushEvent('picked', {});
        expect(await promise).toBeNull();
        expect(events(ws)).toEqual([]);
        expect(warnings.some((w) => w.includes('Cannot pushEvent "picked"'))).toBe(true);
    });
});
