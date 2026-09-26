/**
 * #3186 — the client WebSocket URL honors FORCE_SCRIPT_NAME / SCRIPT_NAME.
 *
 * `{% djust_client_config %}` emits `<meta name="djust-ws-path">` from the
 * script prefix (server half: `python/djust/tests/test_client_config_tag.py`).
 * `connect()` must open its socket at that path, and fall back to
 * `/ws/live/` when the meta tag is absent.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function mountPage({ meta = null, url = 'http://localhost:8000/app/search/', preInit = null } = {}) {
    const head = meta === null ? '' : `<meta name="djust-ws-path" content="${meta}">`;
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${head}</head><body>
            <div dj-root dj-view="app.views.SearchView"></div>
        </body></html>`,
        { url, runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    const opened = [];
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(wsUrl) {
            opened.push(wsUrl);
            this.url = wsUrl;
            this.readyState = MockWebSocket.CONNECTING;
        }
        send() {}
        close() {}
    }
    window.WebSocket = MockWebSocket;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    if (preInit) preInit(window);
    window.eval(clientCode);
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    return { window, opened };
}

describe('#3186 WebSocket path follows the script prefix', () => {
    it('connects to the path emitted by {% djust_client_config %}', () => {
        const { window, opened } = mountPage({ meta: '/app/ws/live/' });
        expect(window.djust.wsPath).toBe('/app/ws/live/');
        expect(opened).toContain('ws://localhost:8000/app/ws/live/');
        expect(opened).not.toContain('ws://localhost:8000/ws/live/');
    });

    it('uses wss: on an https page', () => {
        const { opened } = mountPage({ meta: '/app/ws/live/', url: 'https://example.com/app/' });
        expect(opened).toContain('wss://example.com/app/ws/live/');
    });

    it('falls back to /ws/live/ when the meta tag is absent', () => {
        const { window, opened } = mountPage();
        expect(window.djust.wsPath).toBe('/ws/live/');
        expect(opened).toContain('ws://localhost:8000/ws/live/');
    });

    it('an explicit window.djust.wsPath set before the bundle wins over the meta', () => {
        const { opened } = mountPage({
            meta: '/from-meta/ws/live/',
            preInit: (w) => { w.djust = { wsPath: '/custom/ws/live/' }; },
        });
        expect(opened).toContain('ws://localhost:8000/custom/ws/live/');
    });

    it('never leaves the page host: a protocol-relative or absolute value falls back', () => {
        const a = mountPage({ meta: '//evil.example/ws/live/' });
        expect(a.opened).toContain('ws://localhost:8000/ws/live/');
        const b = mountPage({ meta: 'wss://evil.example/ws/live/' });
        expect(b.opened).toContain('ws://localhost:8000/ws/live/');
    });

    it('an explicit connect(url) argument still wins', () => {
        const { window, opened } = mountPage({ meta: '/app/ws/live/' });
        const ws = new window.djust.LiveViewWebSocket();
        ws.connect('ws://other.local/custom/');
        expect(opened).toContain('ws://other.local/custom/');
    });
});
