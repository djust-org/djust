/**
 * Finding #4 (CWE-345 → CWE-915) — signed state-snapshot client round-trip.
 *
 * The server now emits an OPAQUE signed blob (`state_snapshot_signed`, a
 * Django TimestampSigner string). The client MUST:
 *   1. store that blob verbatim on `window.djust._clientState[view]`
 *      (03-websocket.js mount handler), and
 *   2. echo it back BYTE-FOR-BYTE on the next before-navigate capture
 *      (46-state-snapshot.js `_serialize`) — never JSON.stringify it.
 *
 * Re-serializing would strip the signature and the server would (correctly)
 * reject the snapshot, so "verbatim echo" is a security property, not just an
 * implementation detail. These tests pin it against the built client bundle.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const CLIENT_SRC = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

// A representative TimestampSigner blob shape: <base64-payload>:<ts>:<sig>.
// The colons + base64url chars are exactly what must survive untouched.
const SIGNED_BLOB =
    'eyJzbHVnIjoiYXBwLnZpZXdzLk9yZGVycyIsInNpZCI6InNlc3MtMSIsInN0YXRlIjoie1wicm9sZVwiOlwidXNlclwifSJ9' +
    ':1abcDEF:gH7-_kLmNoPqRsTuVwXyZ0123456789';

function createEnv() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-view="app.views.Orders"></div>
        </body></html>`,
        {
            url: 'http://localhost:8000/orders',
            runScripts: 'dangerously',
            pretendToBeVisual: true,
        }
    );
    const { window } = dom;
    window.console = {
        log: () => {}, warn: () => {}, error: () => {}, debug: () => {}, info: () => {},
    };
    window.history.pushState = () => {};
    window.history.replaceState = () => {};
    if (typeof window.CSS === 'undefined') {
        window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&') };
    }
    try { window.eval(CLIENT_SRC); } catch (_e) { /* ignore */ }
    return { window };
}

describe('signed state-snapshot: client stores + echoes the blob verbatim', () => {
    it('mount handler stores the opaque signed blob as a string', async () => {
        const { window } = createEnv();
        const handler = new window.LiveViewWebSocket();
        await handler.handleMessage({
            type: 'mount',
            session_id: 's-1',
            view: 'app.views.Orders',
            version: 1,
            state_snapshot_signed: SIGNED_BLOB,
        });
        expect(window.djust._clientState['app.views.Orders']).toBe(SIGNED_BLOB);
        // typeof is string — never an object (which would have come from a
        // legacy public_state stash + re-serialize round-trip).
        expect(typeof window.djust._clientState['app.views.Orders']).toBe('string');
    });

    it('_serialize echoes the stored blob byte-for-byte', () => {
        const { window } = createEnv();
        window.djust._clientState = { 'app.views.Orders': SIGNED_BLOB };
        const out = window.djust._stateSnapshot._serialize('app.views.Orders');
        expect(out).toBe(SIGNED_BLOB);
    });

    it('before-navigate capture forwards the blob verbatim to the SW bridge', () => {
        const { window } = createEnv();
        window.djust._clientState = { 'app.views.Orders': SIGNED_BLOB };
        window.djust._routeMap = { '/orders': 'app.views.Orders' };
        const calls = [];
        window.djust._sw = window.djust._sw || {};
        window.djust._sw.captureState = (url, slug, json) => {
            calls.push({ url, slug, json });
        };
        window.dispatchEvent(new window.CustomEvent('djust:before-navigate', {
            detail: { fromUrl: '/orders', toUrl: '/inbox' },
        }));
        expect(calls.length).toBe(1);
        expect(calls[0].json).toBe(SIGNED_BLOB);
    });

    it('mount handler does NOT cache legacy public_state', async () => {
        const { window } = createEnv();
        const handler = new window.LiveViewWebSocket();
        await handler.handleMessage({
            type: 'mount',
            session_id: 's-1',
            view: 'app.views.Orders',
            version: 1,
            public_state: { role: 'admin' },  // legacy unsigned field
        });
        const cached = (window.djust._clientState || {})['app.views.Orders'];
        expect(cached).toBeUndefined();
    });

    it('_serialize returns null for a non-string (legacy object) cache value', () => {
        const { window } = createEnv();
        window.djust._clientState = { 'app.views.Orders': { role: 'user' } };
        const out = window.djust._stateSnapshot._serialize('app.views.Orders');
        expect(out).toBeNull();
    });
});
