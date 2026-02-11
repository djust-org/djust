/**
 * Tests for #258: Auto-stamp data-djust-root and data-liveview-root
 * on elements that only have data-djust-view.
 *
 * Verifies that developers only need to write data-djust-view="..."
 * and the client auto-adds the other attributes at init time.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * Create a JSDOM environment with configurable container attributes.
 * Returns a promise that resolves after DOMContentLoaded fires.
 */
async function createDom(containerHTML) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${containerHTML}</body></html>`,
        { url: 'http://localhost', runScripts: 'dangerously' }
    );

    // Polyfill CSS.escape
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    }

    // Mock WebSocket
    dom.window.WebSocket = class MockWebSocket {
        constructor() {
            this.readyState = 1;
            Promise.resolve().then(() => {
                if (this.onopen) this.onopen({});
            });
        }
        send() {}
        close() {}
    };
    dom.window.WebSocket.OPEN = 1;
    dom.window.WebSocket.CONNECTING = 0;
    dom.window.WebSocket.CLOSING = 2;
    dom.window.WebSocket.CLOSED = 3;

    // Suppress console noise
    const origConsole = dom.window.console;
    dom.window.console = {
        log: () => {},
        error: origConsole.error.bind(origConsole),
        warn: () => {},
        debug: () => {},
        info: () => {},
    };

    dom.window.eval(clientCode);

    // Wait for DOMContentLoaded to fire (djustInit deferred)
    await new Promise(r => setTimeout(r, 20));

    return dom;
}

describe('#258: Auto-stamp root attributes on data-djust-view elements', () => {
    it('adds data-djust-root to element with only data-djust-view', async () => {
        const dom = await createDom(
            '<div data-djust-view="test.views.MyView"><p>hi</p></div>'
        );
        const el = dom.window.document.querySelector('[data-djust-view]');
        expect(el.hasAttribute('data-djust-root')).toBe(true);
    });

    it('adds data-liveview-root to element with only data-djust-view', async () => {
        const dom = await createDom(
            '<div data-djust-view="test.views.MyView"><p>hi</p></div>'
        );
        const el = dom.window.document.querySelector('[data-djust-view]');
        expect(el.hasAttribute('data-liveview-root')).toBe(true);
    });

    it('does not duplicate attributes if already present', async () => {
        const dom = await createDom(
            '<div data-djust-root data-liveview-root data-djust-view="test.views.MyView"><p>hi</p></div>'
        );
        const el = dom.window.document.querySelector('[data-djust-view]');
        expect(el.hasAttribute('data-djust-root')).toBe(true);
        expect(el.hasAttribute('data-liveview-root')).toBe(true);
    });

    it('stamps multiple containers independently', async () => {
        const dom = await createDom(
            '<div data-djust-view="test.views.ViewA"><p>A</p></div>' +
            '<div data-djust-view="test.views.ViewB"><p>B</p></div>'
        );
        const els = dom.window.document.querySelectorAll('[data-djust-view]');
        expect(els.length).toBe(2);
        els.forEach(el => {
            expect(el.hasAttribute('data-djust-root')).toBe(true);
            expect(el.hasAttribute('data-liveview-root')).toBe(true);
        });
    });

    it('getLiveViewRoot() returns data-djust-view element', async () => {
        const dom = await createDom(
            '<div data-djust-view="test.views.MyView"><p>hi</p></div>'
        );
        const root = dom.window.document.querySelector('[data-djust-view]');
        expect(root).not.toBeNull();
        expect(root.getAttribute('data-djust-view')).toBe('test.views.MyView');
        // After auto-stamping, it should also have the other attributes
        expect(root.hasAttribute('data-djust-root')).toBe(true);
    });
});
