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
    const sockets = [];
    const warnings = [];
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor(wsUrl) {
            opened.push(wsUrl);
            this.url = wsUrl;
            this.readyState = MockWebSocket.CONNECTING;
            sockets.push(this);
        }
        send() {}
        close() {}
    }
    window.WebSocket = MockWebSocket;
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...a) => warnings.push(a),
    };
    if (preInit) preInit(window);
    window.eval(clientCode);
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    return { window, opened, sockets, warnings };
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

describe('#3186 upgrade path: one fallback to /ws/live/ when the prefixed handshake fails', () => {
    // A socket that never opened and closed: the handshake failed.
    const failHandshake = (ws) => { ws.readyState = 3; ws.onclose({ code: 1006 }); };

    it('retries once at /ws/live/ and warns, before any backoff', () => {
        const { window, opened, sockets, warnings } = mountPage({ meta: '/app/ws/live/' });
        expect(opened).toEqual(['ws://localhost:8000/app/ws/live/']);
        failHandshake(sockets[0]);

        expect(opened).toEqual(['ws://localhost:8000/app/ws/live/', 'ws://localhost:8000/ws/live/']);
        expect(window.djust.wsPath).toBe('/ws/live/');
        const warn = warnings.find((w) => String(w[0]).includes('failed before opening'));
        expect(warn).toBeTruthy();
        // Parameterized (%s), not interpolated into the format string.
        expect(warn[0]).toContain('%s');
        expect(warn[1]).toBe('/app/ws/live/');
        expect(warn[0]).toContain('DJUST_WS_PATH');
    });

    it('falls back only once: a failed /ws/live/ goes to the normal backoff', () => {
        const { opened, sockets } = mountPage({ meta: '/app/ws/live/' });
        failHandshake(sockets[0]);
        failHandshake(sockets[1]);
        // No immediate third socket: the retry is the scheduled reconnect.
        expect(opened).toHaveLength(2);
    });

    it('does not fall back after the prefixed socket has opened once', () => {
        const { opened, sockets } = mountPage({ meta: '/app/ws/live/' });
        sockets[0].readyState = 1;
        sockets[0].onopen({});
        failHandshake(sockets[0]);
        expect(opened).toEqual(['ws://localhost:8000/app/ws/live/']);
    });

    it('does not fall back when the path already is /ws/live/', () => {
        const { opened, sockets, warnings } = mountPage({ meta: '/ws/live/' });
        failHandshake(sockets[0]);
        expect(opened).toEqual(['ws://localhost:8000/ws/live/']);
        expect(warnings.some((w) => String(w[0]).includes('failed before opening'))).toBe(false);
    });

    it('does not fall back for an explicit connect(url)', () => {
        const { window, opened, sockets } = mountPage({ meta: '/app/ws/live/' });
        const ws = new window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost:8000/custom/');
        failHandshake(sockets[sockets.length - 1]);
        expect(opened[opened.length - 1]).toBe('ws://localhost:8000/custom/');
        expect(opened.filter((u) => u === 'ws://localhost:8000/ws/live/')).toHaveLength(0);
    });
});
