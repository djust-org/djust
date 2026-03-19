/**
 * Tests for dj-lock — prevent concurrent execution of event handlers.
 *
 * <button dj-click="save" dj-lock>Save</button>
 * Disables element until server response arrives, preventing rapid double-clicks.
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

function getFetchCalls(dom) {
    return dom.window._testFetchCalls;
}

describe('dj-lock', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('locks button element on click and sets data-djust-locked', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <button dj-click="save" dj-lock>Save</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');
        btn.click();

        // Should be locked immediately
        expect(btn.hasAttribute('data-djust-locked')).toBe(true);
        expect(btn.disabled).toBe(true);
    });

    it('ignores subsequent clicks while locked', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <button dj-click="save" dj-lock>Save</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');

        // First click — locks the element
        btn.click();

        // Second click immediately — should be ignored because data-djust-locked is set
        // (no sleep so response hasn't arrived to unlock yet)
        btn.click();

        await nativeSleep(200);

        // Only one event should have been sent
        expect(getFetchCalls(dom).length).toBe(1);
    });

    it('unlocks element after server response', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <button dj-click="save" dj-lock>Save</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');
        btn.click();

        expect(btn.hasAttribute('data-djust-locked')).toBe(true);
        expect(btn.disabled).toBe(true);

        // Wait for response
        await nativeSleep(200);

        expect(btn.hasAttribute('data-djust-locked')).toBe(false);
        expect(btn.disabled).toBe(false);
    });

    it('applies CSS class djust-locked on non-button elements', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div dj-click="toggle" dj-lock>Toggle</div>
            </div>
        `);
        initClient(dom);

        const div = dom.window.document.querySelector('div[dj-click]');
        div.click();

        expect(div.hasAttribute('data-djust-locked')).toBe(true);
        expect(div.classList.contains('djust-locked')).toBe(true);
        // Non-form elements don't use disabled property
        expect(div.disabled).toBeUndefined();
    });

    it('removes CSS class from non-button elements after response', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <div dj-click="toggle" dj-lock>Toggle</div>
            </div>
        `);
        initClient(dom);

        const div = dom.window.document.querySelector('div[dj-click]');
        div.click();

        expect(div.classList.contains('djust-locked')).toBe(true);

        await nativeSleep(200);

        expect(div.classList.contains('djust-locked')).toBe(false);
        expect(div.hasAttribute('data-djust-locked')).toBe(false);
    });

    it('does not lock element without dj-lock attribute', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <button dj-click="save">Save</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('button[dj-click]');
        btn.click();

        expect(btn.hasAttribute('data-djust-locked')).toBe(false);
        expect(btn.disabled).toBe(false);
    });

    it('locks form submit buttons with dj-lock on form', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <form dj-submit="save" dj-lock>
                    <input name="title" value="Test" />
                    <button type="submit">Save</button>
                </form>
            </div>
        `);
        initClient(dom);

        const form = dom.window.document.querySelector('form');
        form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));

        expect(form.hasAttribute('data-djust-locked')).toBe(true);

        await nativeSleep(200);

        expect(form.hasAttribute('data-djust-locked')).toBe(false);
    });

    it('locks dj-change element with dj-lock', async () => {
        const dom = createTestEnv(`
            <div dj-view="app.View">
                <select dj-change="update_filter" dj-lock>
                    <option value="a">A</option>
                    <option value="b">B</option>
                </select>
            </div>
        `);
        initClient(dom);

        const select = dom.window.document.querySelector('select');
        select.value = 'b';
        select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));

        expect(select.hasAttribute('data-djust-locked')).toBe(true);
        expect(select.disabled).toBe(true);

        await nativeSleep(200);

        expect(select.hasAttribute('data-djust-locked')).toBe(false);
        expect(select.disabled).toBe(false);
    });
});
