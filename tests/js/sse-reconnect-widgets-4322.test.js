/**
 * #4322 — an SSE reconnect keeps the page's widgets, as a WebSocket reconnect
 * does: the reconnect's mount of the same view is morphed into the page, so a
 * hook keeps its instance and the DOM it owns (dj-update="ignore"), and gets
 * disconnected() / reconnected() rather than destroyed() / mounted().
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const VIEW = 'test.views.Page';
const tick = (ms = 15) => new Promise((resolve) => setTimeout(resolve, ms));

const MOUNT_HTML = (n) =>
    `<span id="count" dj-id="1">${n}</span>` +
    '<div id="w" dj-hook="Widget" dj-id="2"><div id="w-host" dj-update="ignore" dj-id="3"></div></div>';

async function ssePage() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-view="${VIEW}" dj-root>${MOUNT_HTML(0)}</div></body></html>`,
        { url: 'http://localhost/page/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const win = dom.window;
    if (!win.CSS) win.CSS = {};
    if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    win.DJUST_USE_WEBSOCKET = false;
    win.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    const es = { onopen: null, onmessage: null, onerror: null, readyState: 1, close: vi.fn() };
    win.EventSource = vi.fn(function () { return es; });
    win.EventSource.CLOSED = 2;
    win.EventSource.OPEN = 1;
    win.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }));
    win.eval(`
        window.__log = [];
        window.djust = window.djust || {};
        window.djust.hooks = { Widget: {
            mounted() { this.el.querySelector('#w-host').textContent = 'owned by the widget'; window.__log.push('mounted'); },
            destroyed() { window.__log.push('destroyed'); },
            disconnected() { window.__log.push('disconnected'); },
            reconnected() { window.__log.push('reconnected'); },
        } };
    `);
    win.eval(clientCode);
    await tick();
    const serve = async (frame) => { es.onmessage({ data: JSON.stringify(frame) }); await tick(); };
    es.onopen();
    await serve({ type: 'mount', view: VIEW, version: 1, html: MOUNT_HTML(0), has_ids: true });
    return { win, es, serve };
}

describe('SSE reconnect keeps widgets (#4322)', () => {
    it('morphs the same view back in: the hook instance and its owned DOM stay', async () => {
        const { win, es, serve } = await ssePage();
        const el = win.document.getElementById('w');
        const instance = win.djust.getHook(el);
        expect(instance).toBeTruthy();
        win.__log.length = 0;

        // The stream drops and EventSource opens it again.
        es.readyState = 0;
        es.onerror({});
        es.readyState = 1;
        es.onopen();
        await serve({ type: 'mount', view: VIEW, version: 1, html: MOUNT_HTML(5), has_ids: true });

        expect(win.document.getElementById('count').textContent).toBe('5');
        expect(win.document.getElementById('w')).toBe(el);
        expect(win.djust.getHook(el)).toBe(instance);
        expect(win.document.getElementById('w-host').textContent).toBe('owned by the widget');
        expect(win.__log).toEqual(['disconnected', 'reconnected']);
    });
});
