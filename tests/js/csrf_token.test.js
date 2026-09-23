/**
 * Tests for CSRF token fallback to cookie when no form input is present (#204)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv({ formToken, cookie, head = '' } = {}) {
    const formInput = formToken
        ? `<input type="hidden" name="csrfmiddlewaretoken" value="${formToken}">`
        : '';

    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${head}</head><body>
            <div dj-root>
                ${formInput}
                <button dj-click="my_action">Click</button>
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );

    const { window } = dom;

    if (cookie) {
        Object.defineProperty(window.document, 'cookie', {
            get: () => cookie,
            configurable: true,
        });
    }

    // Make WebSocket unavailable so handleEvent falls back to HTTP
    delete window.WebSocket;

    // Capture fetch calls
    const fetchCalls = [];
    window.fetch = vi.fn(async (url, opts) => {
        fetchCalls.push({ url, opts });
        return { ok: true, json: async () => ({ patches: [] }) };
    });

    // Suppress console noise
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Execute client code in JSDOM
    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs; the key parts we need still get defined
    }

    return { window, dom, fetchCalls };
}

describe('CSRF token resolution', () => {
    it('reads token from cookie when no form input exists', async () => {
        const { window, fetchCalls } = createEnv({
            cookie: 'csrftoken=COOKIE_TOKEN_VALUE',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls.length).toBe(1);
        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('COOKIE_TOKEN_VALUE');
    });

    it('prefers form input over cookie', async () => {
        const { window, fetchCalls } = createEnv({
            formToken: 'FORM_TOKEN',
            cookie: 'csrftoken=COOKIE_TOKEN',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls.length).toBe(1);
        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('FORM_TOKEN');
    });

    it('extracts token from multi-cookie string', async () => {
        const { window, fetchCalls } = createEnv({
            cookie: 'sessionid=abc123; csrftoken=MIDDLE_TOKEN; other=xyz',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls.length).toBe(1);
        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('MIDDLE_TOKEN');
    });

    it('ignores cookies whose name ends with csrftoken', async () => {
        const { window, fetchCalls } = createEnv({
            cookie: 'notcsrftoken=WRONG',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls.length).toBe(1);
        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('');
    });

    it('sends empty string when neither form input nor cookie exists', async () => {
        const { window, fetchCalls } = createEnv();

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls.length).toBe(1);
        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('');
    });
});

describe('CSRF token from server-emitted meta tags', () => {
    const cookieMeta = name => `<meta name="djust-csrf-cookie" content="${name}">`;
    const tokenMeta = token => `<meta name="djust-csrf-token" content="${token}">`;

    it('reads the cookie under the configured CSRF_COOKIE_NAME', async () => {
        const { window, fetchCalls } = createEnv({
            head: cookieMeta('djust_demo_csrftoken'),
            cookie: 'djust_demo_sessionid=abc; djust_demo_csrftoken=RENAMED_COOKIE_TOKEN',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('RENAMED_COOKIE_TOKEN');
    });

    it('uses the meta token when the cookie is not readable (HttpOnly / session storage)', async () => {
        const { window, fetchCalls } = createEnv({
            head: cookieMeta('csrftoken') + tokenMeta('META_TOKEN'),
            cookie: 'sessionid=abc',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('META_TOKEN');
    });

    it('prefers the live cookie over the meta token, which can be stale after login', async () => {
        const { window, fetchCalls } = createEnv({
            head: cookieMeta('csrftoken') + tokenMeta('STALE_META_TOKEN'),
            cookie: 'csrftoken=ROTATED_COOKIE_TOKEN',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('ROTATED_COOKIE_TOKEN');
    });

    it('prefers a rendered form input over both', async () => {
        const { window, fetchCalls } = createEnv({
            formToken: 'FORM_TOKEN',
            head: cookieMeta('csrftoken') + tokenMeta('META_TOKEN'),
            cookie: 'csrftoken=COOKIE_TOKEN',
        });

        await window.djust.handleEvent('my_action', {});

        expect(fetchCalls[0].opts.headers['X-CSRFToken']).toBe('FORM_TOKEN');
    });

    it('djust.call uses the same resolver', () => {
        const { window } = createEnv({
            head: cookieMeta('djust_demo_csrftoken'),
            cookie: 'djust_demo_csrftoken=SHARED_TOKEN',
        });

        expect(window.djust.csrfToken()).toBe('SHARED_TOKEN');
    });
});
