import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

// A WebSocket live_redirect to another view class mounts that class, but the
// page container kept the old dj-view; a reconnect (autoMount reads dj-view)
// then mounted the old view at the new URL. The SSE branch already wrote
// data.view onto the container; the WebSocket mount now does too.

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

async function setup(initialView) {
    const dom = new JSDOM(`<div dj-root dj-view="${initialView}"><p>x</p></div>`, {
        runScripts: 'dangerously', url: 'http://localhost/next',
    });
    const w = dom.window;
    w.DJUST_USE_WEBSOCKET = false;
    w.fetch = async () => ({ ok: true, json: async () => ({ patches: [], version: 1 }) });
    w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
    w.eval(client);
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    await Promise.resolve();
    const socket = new w.djust.LiveViewWebSocket();
    return { dom, w, socket };
}

describe('WebSocket mount keeps the page container dj-view on the mounted view', () => {
    for (const prerendered of [true, false]) {
        it(`a page mount of another class rewrites dj-view (prerendered=${prerendered})`, async () => {
            const { dom, w, socket } = await setup('app.views.ListView');
            try {
                // As after live_redirect('/next') to DetailView.
                socket.primaryViewPath = 'app.views.DetailView';
                socket.skipMountHtml = prerendered;
                await socket.handleMessage({ type: 'mount', view: 'app.views.DetailView', html: '<p>y</p>', has_ids: true });
                const root = w.document.querySelector('[dj-root]');
                expect(root.getAttribute('dj-view')).toBe('app.views.DetailView');
            } finally {
                dom.window.close();
            }
        });
    }

    it('a mount that is not the page view leaves the page container alone', async () => {
        const { dom, w, socket } = await setup('app.views.ListView');
        try {
            socket.primaryViewPath = 'app.views.ListView';
            socket.skipMountHtml = true;
            await socket.handleMessage({ type: 'mount', view: 'app.views.Sidebar', html: '<p>z</p>', has_ids: true });
            expect(w.document.querySelector('[dj-root]').getAttribute('dj-view')).toBe('app.views.ListView');
        } finally {
            dom.window.close();
        }
    });
});
