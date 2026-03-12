/**
 * Tests for 22-prefetch.js — link hover prefetch
 *
 * Regression test for: TypeError: event.target.closest is not a function
 * when pointerenter fires on a text node (non-Element target).
 *
 * Issue: https://github.com/djust-org/djust/issues/264
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    // Minimal stubs so client.js initialises without errors
    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};

        // JSDOM does not implement PointerEvent — polyfill as MouseEvent subclass
        // so the capture listener registered in 22-prefetch.js can be triggered.
        if (typeof PointerEvent === 'undefined') {
            window.PointerEvent = class PointerEvent extends MouseEvent {
                constructor(type, init) { super(type, init); }
            };
        }
    `);

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));

    return dom;
}

/**
 * Fire a pointerenter event on `target` and capture it on document.
 * We dispatch via dom.window.eval so the event runs in the JSDOM context
 * where target resolution works correctly.
 *
 * For text/comment nodes we store them as a global then reference from eval.
 */
function firePointerEnterOnElement(dom, cssSel) {
    dom.window.eval(`
        (function() {
            var target = document.querySelector(${JSON.stringify(cssSel)});
            var evt = new PointerEvent('pointerenter', { bubbles: true, cancelable: false });
            target.dispatchEvent(evt);
        })();
    `);
}

/**
 * Fire pointerenter where the target is a text node child of the given selector.
 * Text nodes can't be dispatched to directly via querySelector, so we store a
 * reference in a window global and dispatch on document with that target manually.
 * The capture listener checks instanceof Element and must not throw.
 */
function firePointerEnterOnTextNode(dom, parentCssSel) {
    // Store the text node globally, then call the capture listener directly
    // by dispatching a synthetic event on document with overridden target.
    dom.window.eval(`
        (function() {
            var parent = document.querySelector(${JSON.stringify(parentCssSel)});
            var textNode = parent ? parent.firstChild : document.createTextNode('test');
            // Simulate the browser firing pointerenter on a text node by
            // invoking all capture listeners on document with that target.
            // We do this by creating a plain Event and using Object.defineProperty
            // so the handler sees event.target === textNode.
            var evt = new Event('pointerenter', { bubbles: false });
            Object.defineProperty(evt, 'target', { value: textNode, configurable: true });
            document.dispatchEvent(evt);
        })();
    `);
}

describe('prefetch — text node guard (issue #264)', () => {
    it('does not throw when pointerenter fires on a text node', () => {
        const dom = createTestEnv('<a href="/about">About us</a>');
        expect(() => firePointerEnterOnTextNode(dom, 'a')).not.toThrow();
    });

    it('does not throw when pointerenter fires on document itself (no closest)', () => {
        const dom = createTestEnv('<p>Text</p>');
        expect(() => {
            dom.window.eval(`
                var evt = new Event('pointerenter', { bubbles: false });
                Object.defineProperty(evt, 'target', { value: document, configurable: true });
                document.dispatchEvent(evt);
            `);
        }).not.toThrow();
    });

    it('injects a prefetch link when hovering over an anchor element', () => {
        const dom = createTestEnv('<a href="/about">About</a>');
        firePointerEnterOnElement(dom, 'a');

        const prefetchLinks = dom.window.document.querySelectorAll('link[rel="prefetch"]');
        expect(prefetchLinks.length).toBe(1);
        expect(prefetchLinks[0].href).toBe('http://localhost/about');
    });

    it('does not inject prefetch for hash-only hrefs', () => {
        const dom = createTestEnv('<a href="#section">Jump</a>');
        firePointerEnterOnElement(dom, 'a');

        const prefetchLinks = dom.window.document.querySelectorAll('link[rel="prefetch"]');
        expect(prefetchLinks.length).toBe(0);
    });

    it('does not inject prefetch for javascript: hrefs', () => {
        const dom = createTestEnv('<a href="javascript:void(0)">Click</a>');
        firePointerEnterOnElement(dom, 'a');

        const prefetchLinks = dom.window.document.querySelectorAll('link[rel="prefetch"]');
        expect(prefetchLinks.length).toBe(0);
    });

    it('does not inject duplicate prefetch links on repeated hover', () => {
        const dom = createTestEnv('<a href="/page">Page</a>');
        firePointerEnterOnElement(dom, 'a');
        firePointerEnterOnElement(dom, 'a');
        firePointerEnterOnElement(dom, 'a');

        const prefetchLinks = dom.window.document.querySelectorAll('link[rel="prefetch"]');
        expect(prefetchLinks.length).toBe(1);
    });

    it('does not inject prefetch for cross-origin hrefs', () => {
        const dom = createTestEnv('<a href="https://example.com/page">External</a>');
        firePointerEnterOnElement(dom, 'a');

        const prefetchLinks = dom.window.document.querySelectorAll('link[rel="prefetch"]');
        expect(prefetchLinks.length).toBe(0);
    });

    it('prefetches the href of the closest ancestor anchor when hovering a child span', () => {
        const dom = createTestEnv('<a href="/docs"><span>Read docs</span></a>');
        firePointerEnterOnElement(dom, 'span');

        const prefetchLinks = dom.window.document.querySelectorAll('link[rel="prefetch"]');
        expect(prefetchLinks.length).toBe(1);
        expect(prefetchLinks[0].href).toBe('http://localhost/docs');
    });
});
