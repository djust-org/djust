/**
 * Going back is not a connection error.
 *
 * A page is only eligible for the browser's back/forward cache with no open
 * WebSocket, so the browser closes ours on the way out and reports the close
 * as a connection failure. The console then showed "[LiveView] WebSocket
 * error" followed by a backoff, which reads as a broken page for what is an
 * ordinary back navigation — the socket reconnects and the view remounts.
 *
 * So: stay quiet while the page is cached, and reconnect at once on restore
 * rather than serving out an exponential delay the page never earned.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="test.views.TestView" dj-root><span>content</span></div>
         </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' },
    );

    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0;
            static OPEN = 1;
            static CLOSING = 2;
            static CLOSED = 3;
            constructor() {
                this.readyState = 1;
                this.onopen = null; this.onclose = null;
                this.onmessage = null; this.onerror = null;
            }
            send() {}
            close() {}
        };
        window.location.reload = function() {};
    `);
    dom.window.eval(clientCode);
    return dom;
}

function pageEvent(dom, type, persisted) {
    const event = new dom.window.Event(type);
    Object.defineProperty(event, 'persisted', { value: persisted });
    dom.window.dispatchEvent(event);
}

describe('Back/forward cache', () => {
    it('a socket closed by the cache is not logged as an error', () => {
        const dom = createDom();
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        const spy = vi.spyOn(dom.window.console, 'error').mockImplementation(() => {});

        pageEvent(dom, 'pagehide', true);
        ws.ws.onerror(new dom.window.Event('error'));

        expect(spy).not.toHaveBeenCalled();
    });

    it('an ordinary socket error is still reported', () => {
        const dom = createDom();
        const ws = new dom.window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost/ws/live/');
        const spy = vi.spyOn(dom.window.console, 'error').mockImplementation(() => {});

        ws.ws.onerror(new dom.window.Event('error'));

        expect(spy).toHaveBeenCalled();
    });

    it('a page that was never cached does not set the flag', () => {
        const dom = createDom();
        pageEvent(dom, 'pagehide', false);
        expect(dom.window.djust._inBackForwardCache).toBeFalsy();
    });

    it('restoring clears the flag so later errors are reported again', () => {
        const dom = createDom();
        pageEvent(dom, 'pagehide', true);
        expect(dom.window.djust._inBackForwardCache).toBe(true);
        pageEvent(dom, 'pageshow', true);
        expect(dom.window.djust._inBackForwardCache).toBe(false);
    });
});
