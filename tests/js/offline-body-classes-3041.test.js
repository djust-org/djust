/**
 * #3041: dj-offline-hide / dj-offline-show / dj-offline-disable respond to
 * the network state.
 *
 * The PWA CSS ({% djust_pwa_head %}, {% djust_offline_styles %}) is keyed on
 * body.djust-online / body.djust-offline, and nothing set those classes, so
 * `body:not(.djust-online)` always matched. src/52-offline-state.js now sets
 * them at startup from navigator.onLine and on the window online/offline
 * events.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// The rules {% djust_offline_styles %} emits (templatetags/djust_pwa.py).
const DIRECTIVE_CSS = `
body.djust-offline [dj-offline-hide],
body:not(.djust-online) [dj-offline-hide] { display: none !important; }
body.djust-online [dj-offline-show] { display: none !important; }
body.djust-offline [dj-offline-disable],
body:not(.djust-online) [dj-offline-disable] { pointer-events: none !important; }
`;

function createDom({ online = true } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head><style>${DIRECTIVE_CSS}</style></head><body>
            <div dj-root dj-view="app.View">
                <p id="hide" dj-offline-hide>online only</p>
                <p id="show" dj-offline-show>offline only</p>
                <button id="disable" dj-offline-disable>Save</button>
            </div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    Object.defineProperty(dom.window.navigator, 'onLine', { configurable: true, get: () => online });
    class MockWebSocket {
        constructor() { this.readyState = 1; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    try { dom.window.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    return dom;
}

function display(dom, id) {
    return dom.window.getComputedStyle(dom.window.document.getElementById(id)).display;
}

function pointerEvents(dom, id) {
    return dom.window.getComputedStyle(dom.window.document.getElementById(id)).pointerEvents;
}

describe('online/offline body classes (#3041)', () => {
    it('marks the body online at startup and the directives follow', () => {
        const dom = createDom({ online: true });
        const body = dom.window.document.body;
        expect(body.classList.contains('djust-online')).toBe(true);
        expect(body.classList.contains('djust-offline')).toBe(false);
        expect(display(dom, 'hide')).not.toBe('none');
        expect(display(dom, 'show')).toBe('none');
        expect(pointerEvents(dom, 'disable')).not.toBe('none');
    });

    it('marks the body offline at startup when the browser is offline', () => {
        const dom = createDom({ online: false });
        const body = dom.window.document.body;
        expect(body.classList.contains('djust-offline')).toBe(true);
        expect(body.classList.contains('djust-online')).toBe(false);
        expect(display(dom, 'hide')).toBe('none');
        expect(display(dom, 'show')).not.toBe('none');
        expect(pointerEvents(dom, 'disable')).toBe('none');
    });

    it('flips on the offline and online events', () => {
        const dom = createDom({ online: true });
        const { window } = dom;
        const body = window.document.body;

        window.dispatchEvent(new window.Event('offline'));
        expect(body.classList.contains('djust-offline')).toBe(true);
        expect(body.classList.contains('djust-online')).toBe(false);
        expect(display(dom, 'hide')).toBe('none');
        expect(display(dom, 'show')).not.toBe('none');

        window.dispatchEvent(new window.Event('online'));
        expect(body.classList.contains('djust-online')).toBe(true);
        expect(body.classList.contains('djust-offline')).toBe(false);
        expect(display(dom, 'hide')).not.toBe('none');
        expect(display(dom, 'show')).toBe('none');
    });

    it('re-stamps a body that a layout switch replaced', () => {
        const dom = createDom({ online: true });
        const { window } = dom;
        window.dispatchEvent(new window.Event('offline'));
        const fresh = window.document.createElement('body');
        window.document.body.replaceWith(fresh);
        window.document.dispatchEvent(new window.CustomEvent('djust:layout-changed', { detail: {} }));
        expect(window.document.body.classList.contains('djust-offline')).toBe(true);
    });
});
