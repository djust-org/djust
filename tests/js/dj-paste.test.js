/**
 * Tests for dj-paste — paste event handling (src/09-event-binding.js).
 *
 * `dj-paste` fires a server event when the user pastes into a bound
 * element. The client extracts `text/plain`, `text/html`, and any
 * `files` from the ClipboardEvent and sends them as structured params.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root dj-view="test.View">
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Mock fetch so handleEvent uses the HTTP fallback path and we can
    // observe what got sent.
    const fetchCalls = [];
    window.fetch = vi.fn().mockImplementation(async (url, opts) => {
        let eventName = '';
        let params = {};
        try {
            eventName = (opts && opts.headers && opts.headers['X-Djust-Event']) || '';
        } catch (_) {}
        try {
            params = opts && opts.body ? JSON.parse(opts.body) : {};
        } catch (_) {}
        fetchCalls.push({ url, eventName, params });
        return { ok: true, json: async () => ({ patches: [], version: 1 }) };
    });

    // Force HTTP-only so handleEvent uses fetch (deterministic).
    window.DJUST_USE_WEBSOCKET = false;

    try {
        window.eval(clientCode);
    } catch (_) {
        // client.js may throw on missing DOM APIs; tests still work
    }

    return { window, document: dom.window.document, fetchCalls };
}

/**
 * Build a minimal ClipboardEvent. JSDOM doesn't implement ClipboardEvent
 * natively, so we dispatch a vanilla Event with a synthetic clipboardData.
 */
function makePasteEvent(window, { text = '', html = '', files = [] } = {}) {
    const clipboardData = {
        getData: (kind) => {
            if (kind === 'text/plain') return text;
            if (kind === 'text/html') return html;
            return '';
        },
        files: files,
    };
    const event = new window.Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: clipboardData });
    return event;
}

describe('dj-paste', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('extracts text/plain and fires the server event', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<textarea dj-paste="handle_paste"></textarea>'
        );

        window.djust.bindLiveViewEvents();

        const ta = document.querySelector('[dj-paste]');
        const event = makePasteEvent(window, { text: 'hello world' });
        ta.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const pasteCalls = fetchCalls.filter((c) => c.eventName === 'handle_paste');
        expect(pasteCalls.length).toBe(1);
        expect(pasteCalls[0].params.text).toBe('hello world');
        expect(pasteCalls[0].params.has_files).toBe(false);
    });

    it('extracts text/html when present', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<div dj-paste="paste_rich"></div>'
        );

        window.djust.bindLiveViewEvents();

        const el = document.querySelector('[dj-paste]');
        const event = makePasteEvent(window, {
            text: 'plain',
            html: '<p>rich <b>html</b></p>',
        });
        el.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'paste_rich');
        expect(calls.length).toBe(1);
        expect(calls[0].params.text).toBe('plain');
        expect(calls[0].params.html).toBe('<p>rich <b>html</b></p>');
    });

    it('extracts file metadata when clipboardData.files is non-empty', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<div dj-paste="handle_drop"></div>'
        );

        window.djust.bindLiveViewEvents();

        const el = document.querySelector('[dj-paste]');
        const fakeFile = { name: 'image.png', type: 'image/png', size: 12345 };
        const event = makePasteEvent(window, { files: [fakeFile] });
        el.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'handle_drop');
        expect(calls.length).toBe(1);
        expect(calls[0].params.has_files).toBe(true);
        expect(calls[0].params.files.length).toBe(1);
        expect(calls[0].params.files[0].name).toBe('image.png');
        expect(calls[0].params.files[0].type).toBe('image/png');
        expect(calls[0].params.files[0].size).toBe(12345);
    });

    it('does not preventDefault unless dj-paste-suppress is present', async () => {
        const { window, document } = createEnv(
            '<textarea dj-paste="handle_paste"></textarea>'
        );

        window.djust.bindLiveViewEvents();

        const ta = document.querySelector('[dj-paste]');
        const event = makePasteEvent(window, { text: 'hi' });
        ta.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        expect(event.defaultPrevented).toBe(false);
    });

    it('preventDefault when dj-paste-suppress is set', async () => {
        const { window, document } = createEnv(
            '<textarea dj-paste="handle_paste" dj-paste-suppress></textarea>'
        );

        window.djust.bindLiveViewEvents();

        const ta = document.querySelector('[dj-paste]');
        const event = makePasteEvent(window, { text: 'hi' });
        ta.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        expect(event.defaultPrevented).toBe(true);
    });

    it('does nothing when clipboardData is absent', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<textarea dj-paste="handle_paste"></textarea>'
        );

        window.djust.bindLiveViewEvents();

        const ta = document.querySelector('[dj-paste]');
        // Dispatch a paste event with no clipboardData — handler should bail.
        const event = new window.Event('paste', { bubbles: true, cancelable: true });
        ta.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'handle_paste');
        expect(calls.length).toBe(0);
    });

    it('only binds once (WeakMap prevents double-bind)', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<textarea dj-paste="handle_paste"></textarea>'
        );

        window.djust.bindLiveViewEvents();
        window.djust.bindLiveViewEvents();

        const ta = document.querySelector('[dj-paste]');
        const event = makePasteEvent(window, { text: 'hi' });
        ta.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'handle_paste');
        expect(calls.length).toBe(1);
    });

    it('forwards positional args from dj-paste="handler(arg1, arg2)"', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<div dj-paste="handle_paste(42, \'chat\')"></div>'
        );

        window.djust.bindLiveViewEvents();

        const el = document.querySelector('[dj-paste]');
        const event = makePasteEvent(window, { text: 'hi' });
        el.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'handle_paste');
        expect(calls.length).toBe(1);
        expect(calls[0].params._args).toEqual([42, 'chat']);
    });

    it('routes clipboard files through the upload pipeline when dj-upload is set', async () => {
        const { window, document } = createEnv(
            '<div dj-paste="handle_paste" dj-upload="chat"></div>'
        );

        window.djust.bindLiveViewEvents();

        // Mock the upload pipeline BEFORE dispatch.
        const queueSpy = vi.fn().mockResolvedValue(undefined);
        if (!window.djust.uploads) window.djust.uploads = {};
        window.djust.uploads.queueClipboardFiles = queueSpy;

        const el = document.querySelector('[dj-paste]');
        const fakeFile = { name: 'pic.png', type: 'image/png', size: 99 };
        const event = makePasteEvent(window, { files: [fakeFile] });
        el.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 20));

        expect(queueSpy).toHaveBeenCalledTimes(1);
        expect(queueSpy.mock.calls[0][0]).toBe(el);
    });

    it('skips upload routing when dj-upload is absent', async () => {
        const { window, document } = createEnv(
            '<div dj-paste="handle_paste"></div>'
        );

        window.djust.bindLiveViewEvents();

        const queueSpy = vi.fn().mockResolvedValue(undefined);
        if (!window.djust.uploads) window.djust.uploads = {};
        window.djust.uploads.queueClipboardFiles = queueSpy;

        const el = document.querySelector('[dj-paste]');
        const fakeFile = { name: 'pic.png', type: 'image/png', size: 99 };
        const event = makePasteEvent(window, { files: [fakeFile] });
        el.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 20));

        expect(queueSpy).not.toHaveBeenCalled();
    });

    it('handles missing text/html gracefully (falls back to empty strings)', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<div dj-paste="handle_paste"></div>'
        );

        window.djust.bindLiveViewEvents();

        const el = document.querySelector('[dj-paste]');
        // clipboardData with getData that throws for text/html
        const clipboardData = {
            getData: (kind) => {
                if (kind === 'text/plain') return 'ok';
                if (kind === 'text/html') throw new Error('unsupported');
                return '';
            },
            files: [],
        };
        const event = new window.Event('paste', { bubbles: true, cancelable: true });
        Object.defineProperty(event, 'clipboardData', { value: clipboardData });
        el.dispatchEvent(event);

        await new Promise((r) => setTimeout(r, 10));

        const calls = fetchCalls.filter((c) => c.eventName === 'handle_paste');
        expect(calls.length).toBe(1);
        expect(calls[0].params.text).toBe('ok');
        expect(calls[0].params.html).toBe('');
    });
});
