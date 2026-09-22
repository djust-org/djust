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

describe.each(['websocket', 'sse'])('explicit event snapshots (%s)', (transport) => {
    function makeHandler(window) {
        const handler = transport === 'websocket'
            ? new window.LiveViewWebSocket() : new window.djust.LiveViewSSE();
        handler.primaryViewPath = 'app.views.Orders';
        handler._pendingViewPath = 'app.views.Orders';
        return handler;
    }
    for (const type of ['noop', 'patch', 'html_update']) {
        it(type + ' refreshes the opaque token for navigation', async () => {
            const { window } = createEnv();
            const handler = makeHandler(window);
            window.djust._clientState = { 'app.views.Orders': 'old-token' };
            await handler.handleMessage({
                type, source: 'event', view: 'app.views.Orders',
                state_snapshot_signed: SIGNED_BLOB, patches: [], version: 1,
            });
            expect(window.djust._stateSnapshot._serialize('app.views.Orders')).toBe(SIGNED_BLOB);
            window.djust._routeMap = { '/orders': 'app.views.Orders' };
            const captures = [];
            window.djust._sw = { captureState: (url, slug, token) => captures.push(token) };
            window.dispatchEvent(new window.CustomEvent('djust:before-navigate', {
                detail: { fromUrl: '/orders', toUrl: '/inbox' },
            }));
            expect(captures).toEqual([SIGNED_BLOB]);
        });
    }

    it.each(['mount', 'noop', 'patch', 'html_update'])('%s null invalidates the previously cached token', async (type) => {
        const { window } = createEnv();
        const handler = makeHandler(window);
        window.djust._clientState = { 'app.views.Orders': SIGNED_BLOB };
        await handler.handleMessage({
            type, source: 'event', view: 'app.views.Orders',
            state_snapshot_signed: null, patches: [], version: 1,
        });
        expect(window.djust._stateSnapshot._serialize('app.views.Orders')).toBeNull();
        const forgotten = [];
        window.djust._sw = {
            captureState: () => { throw new Error('Must not capture stale state'); },
            forgetState: (url) => forgotten.push(url),
        };
        window.djust._routeMap = { '/orders': 'app.views.Orders' };
        window.dispatchEvent(new window.CustomEvent('djust:before-navigate', {
            detail: { fromUrl: '/orders', toUrl: '/inbox' },
        }));
        expect(forgotten).toEqual(['/orders']);
    });

    // ADR-038 E3: a turn whose explicit save failed withholds its success
    // frame and sends a state_error that carries a null revocation. An error
    // may only ever *remove* the primary view's token, never store one (the
    // { type: 'error' } case below keeps that pinned).
    it('an error frame null revokes the primary view token', async () => {
        const { window } = createEnv();
        const handler = makeHandler(window);
        window.djust._clientState = { 'app.views.Orders': SIGNED_BLOB };
        await handler.handleMessage({
            type: 'error', code: 'state_error', source: 'event',
            view: 'app.views.Orders', state_snapshot_signed: null,
            error: 'State unavailable. Please reload the page.',
        });
        expect(window.djust._stateSnapshot._serialize('app.views.Orders')).toBeNull();
    });

    it('an error frame null for another view leaves the primary token', async () => {
        const { window } = createEnv();
        const handler = makeHandler(window);
        window.djust._clientState = { 'app.views.Orders': SIGNED_BLOB };
        await handler.handleMessage({
            type: 'error', code: 'state_error', view: 'app.views.Other',
            state_snapshot_signed: null, error: 'x',
        });
        expect(window.djust._stateSnapshot._serialize('app.views.Orders')).toBe(SIGNED_BLOB);
    });

    for (const override of [
        { type: 'error' }, { source: 'async' }, { view: 'app.views.Other' },
        { view: '__proto__' }, { state_snapshot_signed: {} },
    ]) {
        it('ignores nonmatching or invalid snapshot metadata ' + JSON.stringify(override), async () => {
            const { window } = createEnv();
            const handler = makeHandler(window);
            window.djust._clientState = { 'app.views.Orders': SIGNED_BLOB };
            await handler.handleMessage({
                type: 'noop', source: 'event', view: 'app.views.Orders',
                state_snapshot_signed: 'replacement', ...override,
            });
            expect(window.djust._stateSnapshot._serialize('app.views.Orders')).toBe(SIGNED_BLOB);
        });
    }
});

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
