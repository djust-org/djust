/**
 * Tests for dj-confirm — client-side confirmation dialog before event handlers
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Track fetch calls (fallback when WebSocket unavailable)
    const fetchCalls = [];
    window.fetch = vi.fn(async (url, opts) => {
        fetchCalls.push({ url, opts });
        return { ok: true, json: async () => ({ patches: [] }) };
    });

    // Make WebSocket unavailable so handleEvent falls back to HTTP
    delete window.WebSocket;

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document, fetchCalls };
}

describe('dj-confirm', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dj-click: shows confirm dialog and fires event when confirmed', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button dj-click="delete_item" dj-confirm="Are you sure?">Delete</button>'
        );

        window.confirm = vi.fn().mockReturnValue(true);
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-click]');
        btn.click();

        // Allow async handleEvent to run
        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Are you sure?');
        expect(fetchCalls.length).toBeGreaterThan(0); // Event was sent to server
    });

    it('dj-click: suppresses event when cancelled', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button dj-click="delete_item" dj-confirm="Delete?">Delete</button>'
        );

        window.confirm = vi.fn().mockReturnValue(false);
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-click]');
        btn.click();

        // Allow async handleEvent to run (if it runs)
        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Delete?');
        expect(fetchCalls.length).toBe(0); // Event was NOT sent to server
    });

    it('dj-submit: shows confirm dialog and fires event when confirmed', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<form dj-submit="save_data" dj-confirm="Save changes?"><input name="test"></form>'
        );

        window.confirm = vi.fn().mockReturnValue(true);
        window.djust.bindLiveViewEvents();

        const form = document.querySelector('form');
        const submitEvent = new window.Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(submitEvent);

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Save changes?');
        expect(fetchCalls.length).toBeGreaterThan(0);
    });

    it('dj-submit: suppresses event when cancelled', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<form dj-submit="save_data" dj-confirm="Save?"><input name="test"></form>'
        );

        window.confirm = vi.fn().mockReturnValue(false);
        window.djust.bindLiveViewEvents();

        const form = document.querySelector('form');
        const submitEvent = new window.Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(submitEvent);

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Save?');
        expect(fetchCalls.length).toBe(0);
    });

    it('dj-change: shows confirm dialog and fires event when confirmed', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<select dj-change="update_filter" dj-confirm="Change filter?"><option>A</option></select>'
        );

        window.confirm = vi.fn().mockReturnValue(true);
        window.djust.bindLiveViewEvents();

        const select = document.querySelector('select');
        const changeEvent = new window.Event('change', { bubbles: true });
        select.dispatchEvent(changeEvent);

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Change filter?');
        expect(fetchCalls.length).toBeGreaterThan(0);
    });

    it('dj-change: suppresses event when cancelled', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<select dj-change="update_filter" dj-confirm="Change?"><option>A</option></select>'
        );

        window.confirm = vi.fn().mockReturnValue(false);
        window.djust.bindLiveViewEvents();

        const select = document.querySelector('select');
        const changeEvent = new window.Event('change', { bubbles: true });
        select.dispatchEvent(changeEvent);

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Change?');
        expect(fetchCalls.length).toBe(0);
    });

    it('dj-keydown: shows confirm dialog and fires event when confirmed', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<input dj-keydown="handle_enter" dj-key="Enter" dj-confirm="Submit?">'
        );

        window.confirm = vi.fn().mockReturnValue(true);
        window.djust.bindLiveViewEvents();

        const input = document.querySelector('input');
        const keyEvent = new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
        input.dispatchEvent(keyEvent);

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Submit?');
        expect(fetchCalls.length).toBeGreaterThan(0);
    });

    it('dj-keydown: suppresses event when cancelled', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<input dj-keydown="handle_enter" dj-key="Enter" dj-confirm="Submit?">'
        );

        window.confirm = vi.fn().mockReturnValue(false);
        window.djust.bindLiveViewEvents();

        const input = document.querySelector('input');
        const keyEvent = new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
        input.dispatchEvent(keyEvent);

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Submit?');
        expect(fetchCalls.length).toBe(0);
    });

    it('no dj-confirm: event fires normally without dialog', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button dj-click="action">Click me</button>'
        );

        window.confirm = vi.fn();
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-click]');
        btn.click();

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).not.toHaveBeenCalled();
        expect(fetchCalls.length).toBeGreaterThan(0);
    });

    it('empty dj-confirm: event fires normally without dialog', async () => {
        const { window, document, fetchCalls } = createEnv(
            '<button dj-click="action" dj-confirm="">Click me</button>'
        );

        window.confirm = vi.fn();
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-click]');
        btn.click();

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).not.toHaveBeenCalled();
        expect(fetchCalls.length).toBeGreaterThan(0);
    });

    it('dj-confirm message is passed exactly as attribute value', async () => {
        const { window, document } = createEnv(
            '<button dj-click="action" dj-confirm="Delete item #42?">Delete</button>'
        );

        window.confirm = vi.fn().mockReturnValue(true);
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-click]');
        btn.click();

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Delete item #42?');
    });

    it('reads dj-confirm at event time, not bind time (handles morph updates)', async () => {
        const { window, document } = createEnv(
            '<button dj-click="action" dj-confirm="Initial message">Click</button>'
        );

        window.confirm = vi.fn().mockReturnValue(true);
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-click]');

        // Simulate a morph updating the attribute value after binding
        btn.setAttribute('dj-confirm', 'Updated message');

        btn.click();

        await new Promise(r => setTimeout(r, 0));

        expect(window.confirm).toHaveBeenCalledWith('Updated message');
    });
});
