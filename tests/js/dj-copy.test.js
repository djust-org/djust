/**
 * Tests for dj-copy — client-side clipboard copy binding (src/09-event-binding.js)
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

    // Mock navigator.clipboard.writeText
    window.navigator.clipboard = {
        writeText: vi.fn().mockResolvedValue(undefined),
    };

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document };
}

describe('dj-copy', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('copies the dj-copy attribute value to clipboard on click', async () => {
        const { window, document } = createEnv(
            '<button dj-copy="hello world">Copy</button>'
        );

        // Trigger binding
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-copy]');
        btn.click();

        // Allow the promise microtask to resolve
        await new Promise(r => setTimeout(r, 0));

        expect(window.navigator.clipboard.writeText).toHaveBeenCalledWith('hello world');
    });

    it('shows "Copied!" feedback text after successful copy', async () => {
        const { window, document } = createEnv(
            '<button dj-copy="some-text">Copy me</button>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-copy]');
        btn.click();

        // Allow the clipboard promise to resolve
        await new Promise(r => setTimeout(r, 0));

        expect(btn.textContent).toBe('Copied!');
    });

    it('restores original text after 1500ms', async () => {
        vi.useFakeTimers();

        const { window, document } = createEnv(
            '<button dj-copy="value">Original Text</button>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-copy]');
        btn.click();

        // Flush the clipboard promise microtask
        await vi.advanceTimersByTimeAsync(0);

        expect(btn.textContent).toBe('Copied!');

        // Advance past the 1500ms restore timeout
        vi.advanceTimersByTime(1500);

        expect(btn.textContent).toBe('Original Text');

        vi.useRealTimers();
    });

    it('prevents default click behavior', () => {
        const { window, document } = createEnv(
            '<a href="/somewhere" dj-copy="link-text">Copy Link</a>'
        );

        window.djust.bindLiveViewEvents();

        const link = document.querySelector('[dj-copy]');

        let defaultPrevented = false;
        link.addEventListener('click', (e) => {
            defaultPrevented = e.defaultPrevented;
        });

        link.click();

        expect(defaultPrevented).toBe(true);
    });

    it('does not re-bind if already bound (liveviewCopyBound flag)', () => {
        const { window, document } = createEnv(
            '<button dj-copy="text">Copy</button>'
        );

        window.djust.bindLiveViewEvents();
        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-copy]');
        btn.click();

        // If it were bound twice, writeText would be called twice
        expect(window.navigator.clipboard.writeText).toHaveBeenCalledTimes(1);
    });

    it('reads attribute at click time, not bind time (handles morph updates)', async () => {
        const { window, document } = createEnv(
            '<button dj-copy="initial-value">Copy</button>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-copy]');

        // Simulate a morph updating the attribute value after binding
        btn.setAttribute('dj-copy', 'updated-value');

        btn.click();

        await new Promise(r => setTimeout(r, 0));

        expect(window.navigator.clipboard.writeText).toHaveBeenCalledWith('updated-value');
    });

    it('does nothing if dj-copy attribute is empty at click time', async () => {
        const { window, document } = createEnv(
            '<button dj-copy="something">Copy</button>'
        );

        window.djust.bindLiveViewEvents();

        const btn = document.querySelector('[dj-copy]');

        // Clear the attribute value after binding to simulate empty at click time
        btn.setAttribute('dj-copy', '');

        btn.click();

        await new Promise(r => setTimeout(r, 0));

        expect(window.navigator.clipboard.writeText).not.toHaveBeenCalled();
    });
});
