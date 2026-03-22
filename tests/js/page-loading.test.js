/**
 * Tests for dj-page-loading — NProgress-style loading bar
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    <span>content</span>
  </div>
</body>
</html>`, { runScripts: 'dangerously' });

    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
            this.onerror = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;

    // Mock requestAnimationFrame
    dom.window.requestAnimationFrame = (cb) => { cb(); return 1; };

    dom.window.eval(clientCode);

    return { dom };
}

describe('Page Loading Bar', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('exposes djust.pageLoading with start() and finish() methods', () => {
        const { dom } = createDom();

        expect(dom.window.djust.pageLoading).toBeDefined();
        expect(typeof dom.window.djust.pageLoading.start).toBe('function');
        expect(typeof dom.window.djust.pageLoading.finish).toBe('function');
    });

    it('start() creates a loading bar element', () => {
        const { dom } = createDom();
        const doc = dom.window.document;

        dom.window.djust.pageLoading.start();

        const bar = doc.getElementById('djust-page-loading-bar');
        expect(bar).not.toBeNull();
    });

    it('finish() cleans up the loading bar', () => {
        const { dom } = createDom();

        dom.window.djust.pageLoading.start();
        // finish() sets opacity to 0 and schedules removal
        expect(() => {
            dom.window.djust.pageLoading.finish();
        }).not.toThrow();
    });

    it('rapid start/finish does not leave orphaned bars', () => {
        const { dom } = createDom();
        const doc = dom.window.document;

        dom.window.djust.pageLoading.start();
        dom.window.djust.pageLoading.start(); // double start should clean up first
        dom.window.djust.pageLoading.finish();

        const bars = doc.querySelectorAll('#djust-page-loading-bar');
        expect(bars.length).toBeLessThanOrEqual(1);
    });

    it('finish() without start() does not throw', () => {
        const { dom } = createDom();

        expect(() => {
            dom.window.djust.pageLoading.finish();
        }).not.toThrow();
    });

    it('injects loading bar CSS styles', () => {
        const { dom } = createDom();
        const doc = dom.window.document;

        const styles = doc.querySelectorAll('style');
        let found = false;
        for (const style of styles) {
            if (style.textContent.includes('djust-page-loading-bar')) {
                found = true;
                break;
            }
        }
        expect(found).toBe(true);
    });
});

describe('Navigation lifecycle events', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dispatches djust:navigate-start on start()', () => {
        const { dom } = createDom();
        const doc = dom.window.document;
        const handler = vi.fn();
        doc.addEventListener('djust:navigate-start', handler);

        dom.window.djust.pageLoading.start();

        expect(handler).toHaveBeenCalledTimes(1);
    });

    it('dispatches djust:navigate-end on finish() after start()', () => {
        const { dom } = createDom();
        const doc = dom.window.document;
        const handler = vi.fn();
        doc.addEventListener('djust:navigate-end', handler);

        dom.window.djust.pageLoading.start();
        dom.window.djust.pageLoading.finish();

        expect(handler).toHaveBeenCalledTimes(1);
    });

    it('does not dispatch djust:navigate-end on finish() without prior start()', () => {
        const { dom } = createDom();
        const doc = dom.window.document;
        const handler = vi.fn();
        doc.addEventListener('djust:navigate-end', handler);

        dom.window.djust.pageLoading.finish();

        expect(handler).not.toHaveBeenCalled();
    });
});

describe('Navigation CSS class', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('adds .djust-navigating to [dj-root] on start()', () => {
        const { dom } = createDom();
        const root = dom.window.document.querySelector('[dj-root]');

        expect(root.classList.contains('djust-navigating')).toBe(false);
        dom.window.djust.pageLoading.start();
        expect(root.classList.contains('djust-navigating')).toBe(true);
    });

    it('removes .djust-navigating from [dj-root] on finish()', () => {
        const { dom } = createDom();
        const root = dom.window.document.querySelector('[dj-root]');

        dom.window.djust.pageLoading.start();
        expect(root.classList.contains('djust-navigating')).toBe(true);

        dom.window.djust.pageLoading.finish();
        expect(root.classList.contains('djust-navigating')).toBe(false);
    });

    it('does not touch .djust-navigating on finish() without prior start()', () => {
        const { dom } = createDom();
        const root = dom.window.document.querySelector('[dj-root]');

        expect(root.classList.contains('djust-navigating')).toBe(false);
        dom.window.djust.pageLoading.finish();
        expect(root.classList.contains('djust-navigating')).toBe(false);
    });
});
