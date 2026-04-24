/**
 * Tests for djust.call() — the client helper that invokes an
 * @server_function over HTTP (src/48-server-functions.js).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(opts = {}) {
    const head = opts.metaApiPrefix
        ? `<meta name="djust-api-prefix" content="${opts.metaApiPrefix}">`
        : '';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${head}</head><body>${opts.body || ''}</body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    window.history.pushState = () => {};
    window.history.replaceState = () => {};

    // Install the cookie (reader path) before evaluating the bundle so the
    // CSRF resolver finds it if the hidden input isn't present.
    if (opts.cookie) window.document.cookie = opts.cookie;

    // Install a fetch spy so we can assert on request shape.
    window._fetchCalls = [];
    window.fetch = (url, init) => {
        window._fetchCalls.push({ url, init });
        if (opts.respondWith) return opts.respondWith(url, init);
        return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ result: { ok: true } }),
        });
    };

    try { window.eval(clientCode); } catch (e) { /* some DOM APIs may be missing */ }
    return { window, document: window.document };
}

describe('djust.call — HTTP RPC client', () => {
    it('resolves with data.result on a 2xx response', async () => {
        const { window } = createEnv({
            cookie: 'csrftoken=ABC123',
            respondWith: () => Promise.resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve({ result: { hits: [1, 2, 3] } }),
            }),
        });
        const out = await window.djust.call('my.view', 'search', { q: 'foo' });
        expect(out).toEqual({ hits: [1, 2, 3] });
        expect(window._fetchCalls.length).toBe(1);
        expect(window._fetchCalls[0].url).toBe('/djust/api/call/my.view/search/');
    });

    it('rejects with an Error carrying {code, status} on a 4xx response', async () => {
        const { window } = createEnv({
            cookie: 'csrftoken=TOK',
            respondWith: () => Promise.resolve({
                ok: false,
                status: 403,
                json: () => Promise.resolve({ error: 'permission_denied', message: 'nope' }),
            }),
        });
        let caught = null;
        try {
            await window.djust.call('v', 'f', {});
        } catch (e) {
            caught = e;
        }
        expect(caught).toBeInstanceOf(window.Error);
        expect(caught.code).toBe('permission_denied');
        expect(caught.status).toBe(403);
        expect(caught.message).toBe('nope');
    });

    it('test_djust_call_uses_apiUrl_helper: honors meta[name="djust-api-prefix"] under FORCE_SCRIPT_NAME (#987)', async () => {
        // Doc claim: djust.call routes through window.djust.apiUrl, which
        // reads window.djust.apiPrefix seeded from the meta tag at bootstrap.
        // With FORCE_SCRIPT_NAME=/mysite the server-side {% djust_client_config %}
        // emits content="/mysite/djust/api/" — the client must then issue the
        // fetch against that prefix, not the compile-time default.
        const { window } = createEnv({
            cookie: 'csrftoken=ABC',
            metaApiPrefix: '/mysite/djust/api/',
        });
        await window.djust.call('v', 'f', { q: 'x' });
        expect(window._fetchCalls.length).toBe(1);
        const url = window._fetchCalls[0].url;
        expect(url.startsWith('/mysite/djust/api/call/')).toBe(true);
        expect(url).toBe('/mysite/djust/api/call/v/f/');
    });

    it('sets X-CSRFToken header from the csrftoken cookie', async () => {
        const { window } = createEnv({ cookie: 'csrftoken=MYCSRF' });
        await window.djust.call('v', 'f', { n: 1 });
        expect(window._fetchCalls.length).toBe(1);
        const init = window._fetchCalls[0].init;
        expect(init.method).toBe('POST');
        expect(init.headers['X-CSRFToken']).toBe('MYCSRF');
        expect(init.headers['Content-Type']).toBe('application/json');
        expect(init.credentials).toBe('same-origin');
        // Body is the wrapped {params: {...}} shape.
        expect(JSON.parse(init.body)).toEqual({ params: { n: 1 } });
    });
});
