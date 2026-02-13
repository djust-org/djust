/**
 * Tests for #258: Auto-stamp dj-root and dj-liveview-root
 * on elements that only have dj-view.
 *
 * Verifies that developers only need to write dj-view="..."
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

    // Poll for djustInitialized flag instead of fixed timeout (avoids flakiness on slow CI)
    for (let i = 0; i < 50; i++) {
        if (dom.window.djustInitialized) break;
        await new Promise(r => setTimeout(r, 5));
    }

    return dom;
}

describe('#258: Auto-stamp root attributes on dj-view elements', () => {
    it('adds dj-root to element with only dj-view', async () => {
        const dom = await createDom(
            '<div dj-view="test.views.MyView"><p>hi</p></div>'
        );
        const el = dom.window.document.querySelector('[dj-view]');
        expect(el.hasAttribute('dj-root')).toBe(true);
    });

    it('adds dj-liveview-root to element with only dj-view', async () => {
        const dom = await createDom(
            '<div dj-view="test.views.MyView"><p>hi</p></div>'
        );
        const el = dom.window.document.querySelector('[dj-view]');
        expect(el.hasAttribute('dj-liveview-root')).toBe(true);
    });

    it('does not duplicate attributes if already present', async () => {
        const dom = await createDom(
            '<div dj-root dj-liveview-root dj-view="test.views.MyView"><p>hi</p></div>'
        );
        const el = dom.window.document.querySelector('[dj-view]');
        expect(el.hasAttribute('dj-root')).toBe(true);
        expect(el.hasAttribute('dj-liveview-root')).toBe(true);
    });

    it('stamps multiple containers independently', async () => {
        const dom = await createDom(
            '<div dj-view="test.views.ViewA"><p>A</p></div>' +
            '<div dj-view="test.views.ViewB"><p>B</p></div>'
        );
        const els = dom.window.document.querySelectorAll('[dj-view]');
        expect(els.length).toBe(2);
        els.forEach(el => {
            expect(el.hasAttribute('dj-root')).toBe(true);
            expect(el.hasAttribute('dj-liveview-root')).toBe(true);
        });
    });

    it('getLiveViewRoot() returns dj-view element', async () => {
        const dom = await createDom(
            '<div dj-view="test.views.MyView"><p>hi</p></div>'
        );
        const root = dom.window.document.querySelector('[dj-view]');
        expect(root).not.toBeNull();
        expect(root.getAttribute('dj-view')).toBe('test.views.MyView');
        // After auto-stamping, it should also have the other attributes
        expect(root.hasAttribute('dj-root')).toBe(true);
    });
});
