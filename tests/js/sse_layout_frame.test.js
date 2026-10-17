/**
 * A `layout` frame (LayoutMixin.set_layout) is applied on every transport.
 *
 * The server sends it from the shared turn end (`ViewRuntime._flush_all_pending`
 * -> `_flush_pending_layout`), so an SSE page receives it too; the SSE
 * transport had no `layout` case and dropped it as an unknown frame.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><head></head><body><header id="old">OLD</header>' +
        '<div dj-root><input id="msg" value="unsent"></div></body></html>',
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(clientCode);
    return dom;
}

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s layout frame', (transportName) => {
    it('swaps the layout and keeps the live root', async () => {
        const dom = createDom();
        try {
            const { window } = dom;
            const root = window.document.querySelector('[dj-root]');
            const transport = new window.djust[transportName]();
            await transport.handleMessage({
                type: 'layout',
                path: 'layouts/fullscreen.html',
                html: '<!doctype html><html><head></head><body>' +
                    '<header id="new">NEW</header><div dj-root></div></body></html>',
            });
            expect(window.document.getElementById('new')).not.toBeNull();
            expect(window.document.getElementById('old')).toBeNull();
            expect(window.document.querySelector('[dj-root]')).toBe(root);
            expect(window.document.getElementById('msg').value).toBe('unsent');
        } finally {
            dom.window.close();
        }
    });
});
