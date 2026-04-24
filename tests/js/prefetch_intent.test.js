/**
 * Tests for intent-based prefetch (dj-prefetch) in src/22-prefetch.js.
 *
 * These cover the hover-debounce + mouseleave-abort + dedupe + touchstart
 * immediate-fire + opt-out semantics documented in the module header.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    window.history.pushState = () => {};
    window.history.replaceState = () => {};

    try { window.eval(clientCode); } catch (e) { /* ignore missing DOM APIs */ }
    return { window, document: window.document };
}

function fireMouseEnter(window, element) {
    // JSDOM's dispatchEvent for synthetic mouseenter — capture listener receives.
    const ev = new window.Event('mouseenter', { bubbles: false });
    // Pin the target so closest('a[dj-prefetch]') resolves.
    Object.defineProperty(ev, 'target', { value: element, writable: false });
    element.dispatchEvent(ev);
    // The capture listener is attached at document; fire there too so the
    // capture phase hits it (JSDOM doesn't propagate non-bubbling events
    // through the capture phase automatically in all Node versions).
    window.djust._intentPrefetch._onEnter(ev);
}

function fireMouseLeave(window, element) {
    const ev = new window.Event('mouseleave', { bubbles: false });
    Object.defineProperty(ev, 'target', { value: element, writable: false });
    window.djust._intentPrefetch._onLeave(ev);
}

function fireTouchStart(window, element) {
    const ev = new window.Event('touchstart', { bubbles: true });
    Object.defineProperty(ev, 'target', { value: element, writable: false });
    window.djust._intentPrefetch._onTouch(ev);
}

function prefetchLinkCount(document, href) {
    const all = document.querySelectorAll('link[rel="prefetch"]');
    let n = 0;
    for (let i = 0; i < all.length; i++) {
        if (!href || all[i].getAttribute('href') === href) n++;
    }
    return n;
}

describe('intent-prefetch — hover + touch + dedupe + opt-out', () => {
    it('hover triggers prefetch after 65 ms debounce', async () => {
        const { window, document } = createEnv('<a id="l" dj-prefetch href="/about/">About</a>');
        const link = document.getElementById('l');
        expect(window.djust._intentPrefetch).toBeDefined();
        const beforeCount = prefetchLinkCount(document);
        fireMouseEnter(window, link);
        // Immediately after: the debounce timer is pending, no link yet.
        expect(prefetchLinkCount(document)).toBe(beforeCount);
        // Wait past the 65 ms threshold.
        await new Promise((resolve) => setTimeout(resolve, window.djust._intentPrefetch.HOVER_DEBOUNCE_MS + 20));
        // The <link rel=prefetch> should be injected OR the URL should be in the dedupe set.
        const inSet = window.djust._intentPrefetch._prefetched.has('http://localhost:8000/about/');
        expect(inSet).toBe(true);
    });

    it('mouseleave within debounce cancels the scheduled prefetch', async () => {
        const { window, document } = createEnv('<a id="l" dj-prefetch href="/cancel/">Cancel</a>');
        const link = document.getElementById('l');
        fireMouseEnter(window, link);
        // Leave before the debounce fires.
        fireMouseLeave(window, link);
        // Wait past the debounce window.
        await new Promise((resolve) => setTimeout(resolve, window.djust._intentPrefetch.HOVER_DEBOUNCE_MS + 20));
        expect(window.djust._intentPrefetch._prefetched.has('http://localhost:8000/cancel/')).toBe(false);
    });

    it('duplicate URL on second hover is not re-fetched (dedupe)', async () => {
        const { window, document } = createEnv('<a id="l" dj-prefetch href="/dupe/">Dupe</a>');
        const link = document.getElementById('l');
        fireMouseEnter(window, link);
        await new Promise((resolve) => setTimeout(resolve, window.djust._intentPrefetch.HOVER_DEBOUNCE_MS + 20));
        expect(window.djust._intentPrefetch._prefetched.has('http://localhost:8000/dupe/')).toBe(true);
        // Second hover — _shouldIntentPrefetch must now return false because the URL is dedup'd.
        expect(window.djust._intentPrefetch._shouldIntentPrefetch(link)).toBe(false);
    });

    it('touchstart fires prefetch immediately without debounce', () => {
        const { window, document } = createEnv('<a id="l" dj-prefetch href="/touch/">Touch</a>');
        const link = document.getElementById('l');
        expect(window.djust._intentPrefetch._prefetched.has('http://localhost:8000/touch/')).toBe(false);
        fireTouchStart(window, link);
        // No await — the touch path fires synchronously.
        expect(window.djust._intentPrefetch._prefetched.has('http://localhost:8000/touch/')).toBe(true);
    });

    it('dj-prefetch="false" opts out even when attribute present', async () => {
        const { window, document } = createEnv('<a id="l" dj-prefetch="false" href="/nope/">No</a>');
        const link = document.getElementById('l');
        expect(window.djust._intentPrefetch._shouldIntentPrefetch(link)).toBe(false);
        fireMouseEnter(window, link);
        await new Promise((resolve) => setTimeout(resolve, window.djust._intentPrefetch.HOVER_DEBOUNCE_MS + 20));
        expect(window.djust._intentPrefetch._prefetched.has('http://localhost:8000/nope/')).toBe(false);
    });
});
