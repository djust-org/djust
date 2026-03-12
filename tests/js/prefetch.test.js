/**
 * Tests for prefetch-on-hover (src/22-prefetch.js)
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const prefetchCode = fs.readFileSync(
    './python/djust/static/djust/src/22-prefetch.js',
    'utf-8'
);

function createEnv(opts = {}) {
    const {
        hasSW = true,
        saveData = false,
        bodyHtml = ''
    } = opts;

    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: vi.fn(), error: vi.fn(), warn: vi.fn(), debug: vi.fn(), info: vi.fn() };

    // Mock navigator.serviceWorker
    const postMessageFn = vi.fn();
    if (hasSW) {
        Object.defineProperty(window.navigator, 'serviceWorker', {
            value: {
                controller: { postMessage: postMessageFn }
            },
            configurable: true
        });
    } else {
        Object.defineProperty(window.navigator, 'serviceWorker', {
            value: { controller: null },
            configurable: true
        });
    }

    // Mock navigator.connection
    Object.defineProperty(window.navigator, 'connection', {
        value: { saveData: saveData },
        configurable: true
    });

    // Run the prefetch module
    window.eval(prefetchCode);

    return { window, dom, document: dom.window.document, postMessageFn };
}

function hoverLink(document, link) {
    // JSDOM lacks PointerEvent; use Event with bubbles to simulate pointerenter
    const event = new document.defaultView.Event('pointerenter', {
        bubbles: true,
        composed: true
    });
    link.dispatchEvent(event);
}

// ============================================================================
// Basic Prefetch Behavior
// ============================================================================

describe('prefetch-on-hover', () => {
    it('should prefetch same-origin link on hover', () => {
        const { document, postMessageFn } = createEnv({
            bodyHtml: '<a href="http://localhost:8000/about/">About</a>'
        });

        const link = document.querySelector('a');
        hoverLink(document, link);

        expect(postMessageFn).toHaveBeenCalledWith({
            type: 'PREFETCH',
            url: 'http://localhost:8000/about/'
        });
    });

    it('should NOT prefetch cross-origin links', () => {
        const { document, postMessageFn } = createEnv({
            bodyHtml: '<a href="https://external.com/page">External</a>'
        });

        const link = document.querySelector('a');
        hoverLink(document, link);

        expect(postMessageFn).not.toHaveBeenCalled();
    });

    it('should deduplicate URLs (only prefetch once)', () => {
        const { document, postMessageFn } = createEnv({
            bodyHtml: '<a href="http://localhost:8000/about/">About</a>'
        });

        const link = document.querySelector('a');
        hoverLink(document, link);
        hoverLink(document, link);
        hoverLink(document, link);

        expect(postMessageFn).toHaveBeenCalledTimes(1);
    });

    it('should respect data-no-prefetch attribute', () => {
        const { document, postMessageFn } = createEnv({
            bodyHtml: '<a href="http://localhost:8000/about/" data-no-prefetch>About</a>'
        });

        const link = document.querySelector('a');
        hoverLink(document, link);

        expect(postMessageFn).not.toHaveBeenCalled();
    });

    it('should respect saveData preference', () => {
        const { document, postMessageFn } = createEnv({
            saveData: true,
            bodyHtml: '<a href="http://localhost:8000/about/">About</a>'
        });

        const link = document.querySelector('a');
        hoverLink(document, link);

        expect(postMessageFn).not.toHaveBeenCalled();
    });

    it('should no-op without SW controller', () => {
        const { document, postMessageFn } = createEnv({
            hasSW: false,
            bodyHtml: '<a href="http://localhost:8000/about/">About</a>'
        });

        const link = document.querySelector('a');
        hoverLink(document, link);

        expect(postMessageFn).not.toHaveBeenCalled();
    });

    it('should prefetch multiple different URLs', () => {
        const { document, postMessageFn } = createEnv({
            bodyHtml: `
                <a href="http://localhost:8000/page1/">Page 1</a>
                <a href="http://localhost:8000/page2/">Page 2</a>
            `
        });

        const links = document.querySelectorAll('a');
        hoverLink(document, links[0]);
        hoverLink(document, links[1]);

        expect(postMessageFn).toHaveBeenCalledTimes(2);
    });

    it('should expose _prefetch on window.djust', () => {
        const { window } = createEnv();
        expect(window.djust._prefetch).toBeDefined();
        expect(window.djust._prefetch._prefetched).toBeInstanceOf(window.Set);
        expect(typeof window.djust._prefetch._shouldPrefetch).toBe('function');
    });
});
