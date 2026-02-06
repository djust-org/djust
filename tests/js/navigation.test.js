/**
 * Tests for navigation — URL state management (src/18-navigation.js)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div data-djust-root>
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    // Mock history.pushState and replaceState since JSDOM has limited support
    const historyCalls = [];
    const origPushState = window.history.pushState.bind(window.history);
    const origReplaceState = window.history.replaceState.bind(window.history);

    window.history.pushState = vi.fn((state, title, url) => {
        historyCalls.push({ method: 'pushState', state, title, url });
        try { origPushState(state, title, url); } catch (e) { /* JSDOM limitation */ }
    });

    window.history.replaceState = vi.fn((state, title, url) => {
        historyCalls.push({ method: 'replaceState', state, title, url });
        try { origReplaceState(state, title, url); } catch (e) { /* JSDOM limitation */ }
    });

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document, historyCalls };
}

describe('navigation', () => {
    describe('handleNavigation', () => {
        it('live_patch pushes state by default', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                path: '/items/',
                params: { page: '2' },
            });

            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].method).toBe('pushState');
            expect(historyCalls[0].url).toContain('/items/');
            expect(historyCalls[0].url).toContain('page=2');
        });

        it('live_patch with replace uses replaceState', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                path: '/items/',
                params: { page: '3' },
                replace: true,
            });

            expect(historyCalls.length).toBe(1);
            expect(historyCalls[0].method).toBe('replaceState');
        });

        it('live_patch sets query params', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                params: { q: 'search', page: '1' },
            });

            expect(historyCalls.length).toBe(1);
            const url = historyCalls[0].url;
            expect(url).toContain('q=search');
            expect(url).toContain('page=1');
        });

        it('live_patch omits empty params', () => {
            const { window, historyCalls } = createEnv();

            window.djust.navigation.handleNavigation({
                type: 'live_patch',
                params: { q: '', page: '1', empty: null },
            });

            expect(historyCalls.length).toBe(1);
            const url = historyCalls[0].url;
            expect(url).not.toContain('q=');
            expect(url).toContain('page=1');
            expect(url).not.toContain('empty');
        });
    });

    describe('resolveViewPath', () => {
        it('exact match returns view path', () => {
            const { window } = createEnv();

            window.djust._routeMap = {
                '/items/': 'myapp.views.ItemListView',
                '/about/': 'myapp.views.AboutView',
            };

            expect(window.djust.navigation.resolveViewPath('/items/')).toBe('myapp.views.ItemListView');
        });

        it('pattern match with :id works', () => {
            const { window } = createEnv();

            window.djust._routeMap = {
                '/items/:id/': 'myapp.views.ItemDetailView',
            };

            expect(window.djust.navigation.resolveViewPath('/items/42/')).toBe('myapp.views.ItemDetailView');
        });

        it('returns null for unknown path with no container', () => {
            const { window } = createEnv();

            window.djust._routeMap = {
                '/items/': 'myapp.views.ItemListView',
            };

            // Remove any data-djust-view containers
            const containers = window.document.querySelectorAll('[data-djust-view]');
            containers.forEach(c => c.removeAttribute('data-djust-view'));

            expect(window.djust.navigation.resolveViewPath('/unknown/')).toBeNull();
        });

        it('falls back to data-djust-view attribute', () => {
            const { window, document } = createEnv('<div data-djust-view="myapp.views.CurrentView"></div>');

            window.djust._routeMap = {};

            expect(window.djust.navigation.resolveViewPath('/any-path/')).toBe('myapp.views.CurrentView');
        });
    });

    describe('bindDirectives', () => {
        it('binds dj-patch elements', () => {
            const { window, document } = createEnv('<a dj-patch="?page=2">Page 2</a>');

            window.djust.navigation.bindDirectives();

            const link = document.querySelector('[dj-patch]');
            expect(link.dataset.djustPatchBound).toBe('true');
        });

        it('binds dj-navigate elements', () => {
            const { window, document } = createEnv('<a dj-navigate="/about/">About</a>');

            window.djust.navigation.bindDirectives();

            const link = document.querySelector('[dj-navigate]');
            expect(link.dataset.djustNavigateBound).toBe('true');
        });

        it('does not double-bind dj-patch', () => {
            const { window, document } = createEnv('<a dj-patch="?page=2">Page 2</a>');

            window.djust.navigation.bindDirectives();
            window.djust.navigation.bindDirectives();

            const link = document.querySelector('[dj-patch]');
            expect(link.dataset.djustPatchBound).toBe('true');
        });
    });

    describe('_routeMap', () => {
        it('is initialized as an object', () => {
            const { window } = createEnv();
            expect(window.djust._routeMap).toBeDefined();
            expect(typeof window.djust._routeMap).toBe('object');
        });
    });

    describe('exports', () => {
        it('exposes navigation object with expected methods', () => {
            const { window } = createEnv();
            expect(typeof window.djust.navigation.handleNavigation).toBe('function');
            expect(typeof window.djust.navigation.bindDirectives).toBe('function');
            expect(typeof window.djust.navigation.resolveViewPath).toBe('function');
        });
    });
});
