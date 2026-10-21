/**
 * dj-poll on the page root itself, and dj-poll inside an embedded child view.
 *
 * 1. The bind pass scanned the root's DESCENDANTS only, so
 *    `<div dj-root dj-poll="tick">` never polled.
 * 2. Once the root's own poll is bound, a live_redirect must not keep it: the
 *    mount replaces only the root's content, so the departing view's root
 *    attributes would stay and poll a view that has no such handler. The
 *    mount frame names the destination root's poll (`root_poll`), and the
 *    client applies it to the root, on the WebSocket and the SSE path.
 * 3. A poll inside a `{% live_render %}` child was sent without the child's
 *    view_id (the legacy binding attached no owner context), so it reached
 *    the parent view. A component's poll keeps reaching its host view.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

let dom;
afterEach(() => {
    if (dom) dom.window.close();
    dom = null;
});

function setup(bodyHtml) {
    dom = new JSDOM(`<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`, {
        url: 'http://localhost/',
        runScripts: 'dangerously',
    });
    dom.window.eval(`
        window._sent = [];
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; }
            send(msg) { window._sent.push(String(msg)); }
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', { value: false, writable: true, configurable: true });
        window._calls = [];
        window.fetch = async function(url, opts) {
            var name = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch (e) {}
            window._calls.push({ name: name, body: body });
            return { ok: true, json: async function() { return { patches: [], version: 1 }; } };
        };
    `);
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom.window;
}

const sends = (win, name) => win._calls.filter((c) => c.name === name);

async function sendsAfter(win, ms) {
    win._calls.length = 0;
    await nativeSleep(ms);
    return win._calls;
}

const ROOT_POLL_PAGE = `
<div dj-view="app.DashboardView" dj-root dj-poll="tick" dj-poll-interval="30">
  <p dj-id="p1">dashboard</p>
</div>`;

async function wsRedirectMount(win, frame) {
    const ws = new win.djust.LiveViewWebSocket();
    ws.connect('ws://localhost/ws/live/');
    ws.ws.onopen({ type: 'open' });
    await ws.handleMessage({ type: 'connect', session_id: 's' });
    ws.liveRedirectMount({ type: 'live_redirect_mount', view: frame.view, params: {}, url: '/next/' });
    ws.skipMountHtml = false;
    await ws.handleMessage({
        type: 'mount',
        html: '<main dj-id="m1"><p>next</p></main>',
        version: 1,
        has_ids: true,
        ...frame,
    });
    return ws;
}

describe('dj-poll on the [dj-root] element itself', () => {
    it('is bound and polls', async () => {
        const win = setup(ROOT_POLL_PAGE);
        const root = win.document.querySelector('[dj-root]');
        expect(root._djustPollIntervalId).toBeTruthy();
        const calls = await sendsAfter(win, 150);
        expect(calls.filter((c) => c.name === 'tick').length).toBeGreaterThan(0);
    });

    it('a live_redirect to a view whose root has no poll stops it (WebSocket)', async () => {
        const win = setup(ROOT_POLL_PAGE);
        const root = win.document.querySelector('[dj-root]');
        expect(root._djustPollIntervalId).toBeTruthy();
        await wsRedirectMount(win, { view: 'app.ActivityView', root_poll: {} });
        expect(root.hasAttribute('dj-poll')).toBe(false);
        expect(root.hasAttribute('dj-poll-interval')).toBe(false);
        expect(root._djustPollIntervalId).toBeFalsy();
        const calls = await sendsAfter(win, 150);
        expect(calls.filter((c) => c.name === 'tick')).toEqual([]);
    });

    it('a live_redirect to a view with its own root poll swaps the handler (WebSocket)', async () => {
        const win = setup(ROOT_POLL_PAGE);
        await wsRedirectMount(win, {
            view: 'app.ActivityView',
            root_poll: { 'dj-poll': 'refresh_feed', 'dj-poll-interval': '30' },
        });
        const root = win.document.querySelector('[dj-root]');
        expect(root.getAttribute('dj-poll')).toBe('refresh_feed');
        const calls = await sendsAfter(win, 150);
        expect(calls.filter((c) => c.name === 'tick')).toEqual([]);
        expect(calls.filter((c) => c.name === 'refresh_feed').length).toBeGreaterThan(0);
    });

    it('a mount of the same view keeps the poll\'s phase (WebSocket reconnect)', async () => {
        const win = setup(ROOT_POLL_PAGE);
        const root = win.document.querySelector('[dj-root]');
        const intervalId = root._djustPollIntervalId;
        expect(intervalId).toBeTruthy();
        const ws = new win.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        ws.ws.onopen({ type: 'open' });
        await ws.handleMessage({ type: 'connect', session_id: 's' });
        ws.primaryViewPath = 'app.DashboardView';
        ws.skipMountHtml = true;
        await ws.handleMessage({
            type: 'mount', view: 'app.DashboardView', version: 1, has_ids: true,
            html: '<p dj-id="p1">dashboard</p>',
            root_poll: { 'dj-poll': 'tick', 'dj-poll-interval': '30' },
        });
        expect(root._djustPollIntervalId).toBe(intervalId);
    });

    it('a mount frame without root_poll leaves the root alone', async () => {
        const win = setup(ROOT_POLL_PAGE);
        await wsRedirectMount(win, { view: 'app.DashboardView' });
        const root = win.document.querySelector('[dj-root]');
        expect(root.getAttribute('dj-poll')).toBe('tick');
        expect(root._djustPollIntervalId).toBeTruthy();
    });

    it('a mount on the SSE path applies root_poll too', async () => {
        const win = setup(ROOT_POLL_PAGE);
        const sse = new win.djust.LiveViewSSE();
        await sse.handleMessage({
            type: 'mount', view: 'app.ActivityView', version: 1,
            html: '<main dj-id="m1"><p>next</p></main>',
            root_poll: {},
        });
        const root = win.document.querySelector('[dj-root]');
        expect(root.hasAttribute('dj-poll')).toBe(false);
        expect(root._djustPollIntervalId).toBeFalsy();
        const calls = await sendsAfter(win, 150);
        expect(calls.filter((c) => c.name === 'tick')).toEqual([]);
    });
});

describe('dj-poll inside an embedded child view', () => {
    it('carries the child\'s view_id, so the child receives it', async () => {
        const win = setup(`
<div dj-view="app.ParentView" dj-root>
  <div dj-view data-djust-embedded="child-1">
    <div dj-poll="child_tick" dj-poll-interval="30" data-room="7"></div>
  </div>
</div>`);
        await nativeSleep(150);
        const calls = sends(win, 'child_tick');
        expect(calls.length).toBeGreaterThan(0);
        expect(calls[0].body.view_id).toBe('child-1');
        expect(calls[0].body.room).toBe('7');
    });

    it('a poll in the page view itself carries no view_id', async () => {
        const win = setup(`
<div dj-view="app.ParentView" dj-root>
  <div dj-poll="page_tick" dj-poll-interval="30"></div>
</div>`);
        await nativeSleep(150);
        const calls = sends(win, 'page_tick');
        expect(calls.length).toBeGreaterThan(0);
        expect(calls[0].body.view_id).toBeUndefined();
    });

    it('a component\'s poll still reaches its host view (no component_id)', async () => {
        const win = setup(`
<div dj-view="app.ParentView" dj-root>
  <div data-component-id="cmp-1">
    <div dj-poll="host_tick" dj-poll-interval="30"></div>
  </div>
</div>`);
        await nativeSleep(150);
        const calls = sends(win, 'host_tick');
        expect(calls.length).toBeGreaterThan(0);
        expect(calls[0].body.component_id).toBeUndefined();
        expect(calls[0].body.view_id).toBeUndefined();
    });
});
