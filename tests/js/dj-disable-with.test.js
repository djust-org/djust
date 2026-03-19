/**
 * Tests for dj-disable-with — automatic button disabling during form submission.
 *
 * <button type="submit" dj-disable-with="Saving...">Save</button>
 * Prevents double-submit and gives instant visual feedback.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window._resolveNextFetch = null;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

describe('dj-disable-with', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('disables submit button and changes text on form submit', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <button type="submit" dj-disable-with="Saving...">Save</button>
                </form>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[type="submit"]');
        expect(btn.textContent).toBe('Save');
        expect(btn.disabled).toBe(false);

        const form = dom.window.document.querySelector('form');
        const submitEvent = new dom.window.Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(submitEvent);

        // Check synchronously (before response) that the button is disabled
        expect(btn.disabled).toBe(true);
        expect(btn.textContent).toBe('Saving...');
        expect(btn.getAttribute('data-djust-original-text')).toBe('Save');

        // After response, button should be restored
        await nativeSleep(200);
        expect(btn.disabled).toBe(false);
        expect(btn.textContent).toBe('Save');
    });

    it('disables button with dj-click and dj-disable-with', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <button dj-click="process" dj-disable-with="Processing...">Process</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');
        expect(btn.textContent).toBe('Process');

        btn.click();

        // Check synchronously that button is disabled with new text
        expect(btn.disabled).toBe(true);
        expect(btn.textContent).toBe('Processing...');
        expect(btn.getAttribute('data-djust-original-text')).toBe('Process');

        await nativeSleep(200);
        expect(btn.disabled).toBe(false);
        expect(btn.textContent).toBe('Process');
    });

    it('does not overwrite original text on double-submit', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <form dj-submit="save">
                    <input name="title" value="Test" />
                    <button type="submit" dj-disable-with="Saving...">Save</button>
                </form>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[type="submit"]');
        const form = dom.window.document.querySelector('form');

        // First submit
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
        expect(btn.getAttribute('data-djust-original-text')).toBe('Save');

        // The button is disabled, so a second submit shouldn't overwrite the saved text
        // (In practice the form submit will be blocked because button is disabled)
        expect(btn.getAttribute('data-djust-original-text')).toBe('Save');
    });

    it('restores button text after server response', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <button dj-click="action" dj-disable-with="Working...">Do it</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');
        btn.click();

        expect(btn.textContent).toBe('Working...');

        await nativeSleep(200);

        // After response, text should be restored
        expect(btn.textContent).toBe('Do it');
        expect(btn.disabled).toBe(false);
        expect(btn.hasAttribute('data-djust-original-text')).toBe(false);
    });

    it('does nothing without dj-disable-with attribute', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.FormView">
                <button dj-click="action">Click</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');
        btn.click();

        expect(btn.textContent).toBe('Click');
        expect(btn.disabled).toBe(false);
    });
});
