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

function mountPage({ meta = null, url = 'http://localhost:8000/app/search/', preInit = null, controlledTimers = false } = {}) {
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
    // A controllable timer queue (the reconnect backoff uses the page's
    // setTimeout): the test drives each scheduled callback explicitly.
    const timers = [];
    if (controlledTimers) {
        window.setTimeout = (fn) => { timers.push(fn); return timers.length; };
        window.clearTimeout = () => {};
    }
    const runTimers = () => { while (timers.length) timers.shift()(); };
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...a) => warnings.push(a),
    };
    if (preInit) preInit(window);
    window.eval(clientCode);
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    return { window, opened, sockets, warnings, runTimers };
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

describe('#3186 upgrade path: a provisional fallback to /ws/live/', () => {
    const P = 'ws://localhost:8000/app/ws/live/';
    const R = 'ws://localhost:8000/ws/live/';
    // A socket that never opened and closed: the handshake failed.
    const failHandshake = (ws) => { ws.readyState = 3; ws.onclose({ code: 1006 }); };
    const last = (sockets) => sockets[sockets.length - 1];

    it('tries the host root once, immediately, and warns', () => {
        const { window, opened, sockets, warnings } = mountPage({ meta: '/app/ws/live/', controlledTimers: true });
        expect(opened).toEqual([P]);
        failHandshake(sockets[0]);

        expect(opened).toEqual([P, R]);
        // djust.wsPath is not rewritten by the attempt.
        expect(window.djust.wsPath).toBe('/app/ws/live/');
        const warn = warnings.find((w) => String(w[0]).includes('failed before opening'));
        expect(warn).toBeTruthy();
        expect(warn[0]).toContain('%s'); // parameterized, not interpolated
        expect(warn[1]).toBe(P);
        expect(warn[0]).toContain('DJUST_WS_PATH');
    });

    it('a transient failure does not strand the page at the root: retries go back to the prefix', () => {
        // The re-review's probe: prefixed fails (a restart), the root attempt
        // fails too, then the backoff. Before the fix every retry went to /ws/live/.
        const { window, opened, sockets, runTimers } = mountPage({ meta: '/app/ws/live/', controlledTimers: true });
        failHandshake(sockets[0]); // prefixed fails -> provisional root
        failHandshake(sockets[1]); // root fails -> backoff scheduled
        expect(opened).toEqual([P, R]);
        for (let i = 0; i < 4; i++) {
            runTimers();
            failHandshake(last(sockets));
        }
        expect(opened).toEqual([P, R, P, P, P, P]);
        expect(window.djust.wsPath).toBe('/app/ws/live/');

        // The deployment comes back: the prefixed socket opens and stays.
        runTimers();
        expect(last(sockets).url).toBe(P);
        last(sockets).readyState = 1;
        last(sockets).onopen({});
        expect(window.document.body.classList.contains('dj-connected')).toBe(true);
    });

    it('adopts the root only when that socket opens, and later reconnects keep it', () => {
        const { opened, sockets, warnings, runTimers } = mountPage({ meta: '/app/ws/live/', controlledTimers: true });
        failHandshake(sockets[0]);
        sockets[1].readyState = 1;
        sockets[1].onopen({}); // the root answered
        expect(warnings.some((w) => String(w[0]).includes('keeps using it'))).toBe(true);
        // A later drop of the adopted socket reconnects at the root.
        sockets[1].readyState = 3;
        sockets[1].onclose({ code: 1006 });
        runTimers();
        expect(opened).toEqual([P, R, R]);
    });

    it('falls back only once per page', () => {
        const { opened, sockets, runTimers } = mountPage({ meta: '/app/ws/live/', controlledTimers: true });
        failHandshake(sockets[0]);
        failHandshake(sockets[1]);
        runTimers();
        failHandshake(last(sockets)); // prefixed again: no second root attempt
        expect(opened).toEqual([P, R, P]);
    });

    it('does not fall back after the prefixed socket has opened once', () => {
        const { opened, sockets, runTimers } = mountPage({ meta: '/app/ws/live/', controlledTimers: true });
        sockets[0].readyState = 1;
        sockets[0].onopen({});
        failHandshake(sockets[0]);
        runTimers();
        expect(opened).toEqual([P, P]);
    });

    it('does not fall back when the path already is /ws/live/', () => {
        const { opened, sockets, warnings } = mountPage({ meta: '/ws/live/', controlledTimers: true });
        failHandshake(sockets[0]);
        expect(opened).toEqual([R]);
        expect(warnings.some((w) => String(w[0]).includes('failed before opening'))).toBe(false);
    });

    it('does not fall back for an explicit connect(url), and its retries keep that URL', () => {
        const { window, opened, sockets, runTimers } = mountPage({ meta: '/app/ws/live/', controlledTimers: true });
        const ws = new window.djust.LiveViewWebSocket();
        ws.connect('ws://localhost:8000/custom/');
        failHandshake(last(sockets));
        runTimers();
        expect(opened.filter((u) => u === R)).toHaveLength(0);
        expect(opened.filter((u) => u === 'ws://localhost:8000/custom/').length).toBeGreaterThanOrEqual(2);
    });

    it('warns once when it ignores a djust.wsPath that is not root-relative', () => {
        const { opened, warnings } = mountPage({ meta: '//evil.example/ws/' });
        expect(opened).toContain(R);
        const ignored = warnings.filter((w) => String(w[0]).includes('Ignoring djust.wsPath'));
        expect(ignored).toHaveLength(1);
        expect(ignored[0][1]).toBe('//evil.example/ws/');
    });
});
