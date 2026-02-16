/**
 * Tests for loading state management:
 * - dj-loading.for attribute (explicit event scoping)
 * - async_pending flag (keeps loading active during background work)
 * - scanAndRegister cleanup of stale elements
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    // Polyfill CSS.escape
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Stub WebSocket
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
    `);

    dom.window.eval(clientCode);
    return dom;
}

describe('globalLoadingManager', () => {
    describe('dj-loading.for', () => {
        it('registers element with explicit event name from dj-loading.for', () => {
            const dom = createDom(`
                <div dj-loading.show dj-loading.for="generate" style="display:none">Loading...</div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            // Element should be registered for 'generate' event
            const el = dom.window.document.querySelector('[dj-loading\\.show]');
            const config = mgr.registeredElements.get(el);
            expect(config).toBeDefined();
            expect(config.eventName).toBe('generate');
        });

        it('prefers dj-loading.for over implicit dj-click event name', () => {
            const dom = createDom(`
                <button dj-click="save" dj-loading.disable dj-loading.for="generate">Save</button>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            const btn = dom.window.document.querySelector('button');
            const config = mgr.registeredElements.get(btn);
            expect(config).toBeDefined();
            expect(config.eventName).toBe('generate');
        });

        it('falls back to implicit dj-click event when no dj-loading.for', () => {
            const dom = createDom(`
                <button dj-click="save" dj-loading.disable>Save</button>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            const btn = dom.window.document.querySelector('button');
            const config = mgr.registeredElements.get(btn);
            expect(config).toBeDefined();
            expect(config.eventName).toBe('save');
        });

        it('show/hide elements react to dj-loading.for event', () => {
            const dom = createDom(`
                <button dj-click="generate">Go</button>
                <div id="spinner" dj-loading.show dj-loading.for="generate" style="display:none">Loading...</div>
                <div id="content" dj-loading.hide dj-loading.for="generate">Content here</div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            const spinner = dom.window.document.getElementById('spinner');
            const content = dom.window.document.getElementById('content');

            // Start loading
            mgr.startLoading('generate');
            expect(spinner.style.display).toBe('block');
            expect(content.style.display).toBe('none');

            // Stop loading
            mgr.stopLoading('generate');
            expect(spinner.style.display).toBe('none');
            // content should be restored
            expect(content.style.display).not.toBe('none');
        });
    });

    describe('scanAndRegister cleanup', () => {
        it('skips already-registered elements to preserve originalState', () => {
            const dom = createDom(`
                <div id="spinner" dj-loading.show dj-loading.for="save" style="display:none">Saving...</div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            const el = dom.window.document.getElementById('spinner');
            const config1 = mgr.registeredElements.get(el);
            expect(config1.originalState.display).toBe('none');

            // Simulate loading — element is now display:block
            mgr.startLoading('save');
            expect(el.style.display).toBe('block');

            // Re-scan should NOT re-register (would capture 'block' as original)
            mgr.scanAndRegister();
            const config2 = mgr.registeredElements.get(el);
            expect(config2.originalState.display).toBe('none'); // Still the original

            // Stop loading should restore to original hidden state
            mgr.stopLoading('save');
            expect(el.style.display).toBe('none');
        });

        it('removes entries for disconnected elements', () => {
            const dom = createDom(`
                <div id="container">
                    <div id="spinner" dj-loading.show dj-loading.for="save" style="display:none">Saving...</div>
                </div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();
            expect(mgr.registeredElements.size).toBe(1);

            // Remove the element from DOM
            const spinner = dom.window.document.getElementById('spinner');
            spinner.remove();

            // Re-scan should clean up the stale entry
            mgr.scanAndRegister();
            expect(mgr.registeredElements.size).toBe(0);
        });
    });

    describe('async_pending loading lifecycle', () => {
        it('loading persists when stopLoading is not called (simulating async_pending)', () => {
            // When server sends async_pending: true, the client skips stopLoading.
            // This test verifies loading state persists until explicitly stopped.
            const dom = createDom(`
                <button dj-click="generate" dj-loading.disable>Go</button>
                <div id="spinner" dj-loading.show dj-loading.for="generate" style="display:none">Loading...</div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            const btn = dom.window.document.querySelector('button');
            const spinner = dom.window.document.getElementById('spinner');

            // Start loading (event fired)
            mgr.startLoading('generate', btn);
            expect(mgr.pendingEvents.has('generate')).toBe(true);
            expect(spinner.style.display).toBe('block');
            expect(btn.disabled).toBe(true);

            // Server response with async_pending=true: client does NOT call stopLoading
            // Loading should remain active
            expect(mgr.pendingEvents.has('generate')).toBe(true);
            expect(spinner.style.display).toBe('block');

            // Async work completes: server sends final response, client calls stopLoading
            mgr.stopLoading('generate', btn);
            expect(mgr.pendingEvents.has('generate')).toBe(false);
            expect(spinner.style.display).toBe('none');
            expect(btn.disabled).toBe(false);
        });

        it('stopLoading clears loading for the correct event only', () => {
            const dom = createDom(`
                <div id="s1" dj-loading.show dj-loading.for="generate" style="display:none">Generating...</div>
                <div id="s2" dj-loading.show dj-loading.for="export" style="display:none">Exporting...</div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            // Start both loading states
            mgr.startLoading('generate');
            mgr.startLoading('export');

            const s1 = dom.window.document.getElementById('s1');
            const s2 = dom.window.document.getElementById('s2');
            expect(s1.style.display).toBe('block');
            expect(s2.style.display).toBe('block');

            // Stop only 'generate' (async completion)
            mgr.stopLoading('generate');
            expect(s1.style.display).toBe('none');
            expect(s2.style.display).toBe('block'); // Still loading

            // Stop 'export'
            mgr.stopLoading('export');
            expect(s2.style.display).toBe('none');
        });

        it('pendingEvents tracks active events correctly', () => {
            const dom = createDom(`
                <div dj-loading.show dj-loading.for="save" style="display:none">Saving...</div>
            `);
            const mgr = dom.window.djust.globalLoadingManager;
            mgr.scanAndRegister();

            expect(mgr.pendingEvents.size).toBe(0);

            mgr.startLoading('save');
            expect(mgr.pendingEvents.has('save')).toBe(true);

            mgr.stopLoading('save');
            expect(mgr.pendingEvents.has('save')).toBe(false);
        });
    });
});
