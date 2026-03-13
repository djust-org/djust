/**
 * Tests for prefetch-on-hover (src/22-prefetch.js)
 * and clear-on-SPA-navigation behaviour.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root></div></body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Mock history to avoid JSDOM errors
    window.history.pushState = (s, t, u) => {};
    window.history.replaceState = (s, t, u) => {};

    try { window.eval(clientCode); } catch (e) { /* ignore missing DOM APIs */ }

    return { window };
}

describe('prefetch module', () => {
    it('exposes window.djust._prefetch with _prefetched, _shouldPrefetch, and clear', () => {
        const { window } = createEnv();
        expect(window.djust._prefetch).toBeDefined();
        expect(window.djust._prefetch._prefetched).toBeInstanceOf(window.Set);
        expect(typeof window.djust._prefetch._shouldPrefetch).toBe('function');
        expect(typeof window.djust._prefetch.clear).toBe('function');
    });

    it('_shouldPrefetch returns false when no service worker controller', () => {
        const { window } = createEnv();
        const link = window.document.createElement('a');
        link.href = 'http://localhost:8000/about/';
        // navigator.serviceWorker.controller is null in JSDOM — should return false
        expect(window.djust._prefetch._shouldPrefetch(link)).toBe(false);
    });

    it('clear() empties the _prefetched set', () => {
        const { window } = createEnv();
        const set = window.djust._prefetch._prefetched;
        set.add('http://localhost:8000/about/');
        set.add('http://localhost:8000/contact/');
        expect(set.size).toBe(2);

        window.djust._prefetch.clear();
        expect(set.size).toBe(0);
    });
});

describe('prefetch cleared on SPA navigation', () => {
    it('handleNavigation with live_redirect clears the prefetch set', () => {
        const { window } = createEnv();

        // Seed the prefetch set with URLs from the current view
        window.djust._prefetch._prefetched.add('http://localhost:8000/about/');
        window.djust._prefetch._prefetched.add('http://localhost:8000/contact/');
        expect(window.djust._prefetch._prefetched.size).toBe(2);

        // Trigger a live_redirect navigation — this calls handleLiveRedirect internally,
        // which should invoke window.djust._prefetch.clear()
        window.djust.navigation.handleNavigation({
            type: 'live_redirect',
            path: '/new-view/',
        });

        expect(window.djust._prefetch._prefetched.size).toBe(0);
    });
});
