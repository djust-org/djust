/**
 * Tests for flash messages — 23-flash.js
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { url: 'http://localhost:8000/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    try {
        window.eval(clientCode);
    } catch (e) {
        // client.js may throw on missing DOM APIs
    }

    return { window, dom, document: dom.window.document };
}

describe('flash message rendering', () => {
    it('renders a flash message in the container', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'success', message: 'Saved!' });

        const flash = document.querySelector('.dj-flash');
        expect(flash).not.toBeNull();
        expect(flash.textContent).toBe('Saved!');
        expect(flash.classList.contains('dj-flash-success')).toBe(true);
        expect(flash.getAttribute('role')).toBe('alert');
        expect(flash.getAttribute('data-dj-flash-level')).toBe('success');
    });

    it('renders multiple flash messages', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Info' });
        window.djust.flash.handleFlash({ action: 'put', level: 'error', message: 'Error' });

        const flashes = document.querySelectorAll('.dj-flash');
        expect(flashes.length).toBe(2);
        expect(flashes[0].textContent).toBe('Info');
        expect(flashes[1].textContent).toBe('Error');
    });

    it('applies correct level CSS classes', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'warning', message: 'Warn' });

        const flash = document.querySelector('.dj-flash');
        expect(flash.classList.contains('dj-flash')).toBe(true);
        expect(flash.classList.contains('dj-flash-warning')).toBe(true);
    });

    it('skips rendering when no container exists', () => {
        const { window, document } = createEnv('');

        // Should not throw
        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Hello' });

        const flash = document.querySelector('.dj-flash');
        expect(flash).toBeNull();
    });
});

describe('flash auto-dismiss', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('auto-dismisses after default timeout', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Bye' });
        expect(document.querySelectorAll('.dj-flash').length).toBe(1);

        // Advance past auto-dismiss (default 5000ms)
        vi.advanceTimersByTime(5000);
        // Should have the removing class
        const flash = document.querySelector('.dj-flash');
        expect(flash.classList.contains('dj-flash-removing')).toBe(true);

        // Advance past removal transition (300ms)
        vi.advanceTimersByTime(300);
        expect(document.querySelectorAll('.dj-flash').length).toBe(0);
    });

    it('respects custom auto-dismiss from container attribute', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="2000"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Quick' });

        // Should still be visible at 1999ms
        vi.advanceTimersByTime(1999);
        expect(document.querySelectorAll('.dj-flash').length).toBe(1);
        expect(document.querySelector('.dj-flash').classList.contains('dj-flash-removing')).toBe(false);

        // Should start removing at 2000ms
        vi.advanceTimersByTime(1);
        expect(document.querySelector('.dj-flash').classList.contains('dj-flash-removing')).toBe(true);

        // Should be gone after transition
        vi.advanceTimersByTime(300);
        expect(document.querySelectorAll('.dj-flash').length).toBe(0);
    });

    it('does not auto-dismiss when timeout is 0', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Sticky' });

        vi.advanceTimersByTime(60000);
        expect(document.querySelectorAll('.dj-flash').length).toBe(1);
        expect(document.querySelector('.dj-flash').classList.contains('dj-flash-removing')).toBe(false);
    });
});

describe('flash clear command', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('clears all flash messages', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'A' });
        window.djust.flash.handleFlash({ action: 'put', level: 'error', message: 'B' });

        window.djust.flash.handleFlash({ action: 'clear' });

        // Messages should get removing class immediately
        const flashes = document.querySelectorAll('.dj-flash');
        flashes.forEach(el => {
            expect(el.classList.contains('dj-flash-removing')).toBe(true);
        });

        // After transition, they should be removed
        vi.advanceTimersByTime(300);
        expect(document.querySelectorAll('.dj-flash').length).toBe(0);
    });

    it('clears only messages of a specific level', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Keep' });
        window.djust.flash.handleFlash({ action: 'put', level: 'error', message: 'Remove' });
        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Also keep' });

        window.djust.flash.handleFlash({ action: 'clear', level: 'error' });

        vi.advanceTimersByTime(300);

        const remaining = document.querySelectorAll('.dj-flash');
        expect(remaining.length).toBe(2);
        remaining.forEach(el => {
            expect(el.getAttribute('data-dj-flash-level')).toBe('info');
        });
    });
});

describe('flash debug logging', () => {
    it('logs when djustDebug is enabled', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        const logs = [];
        window.console = { log: (...args) => logs.push(args.join(' ')), error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
        window.djustDebug = true;

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Test' });

        expect(logs.some(l => l.includes('flash'))).toBe(true);
    });

    it('does not log when djustDebug is not set', () => {
        const { window, document } = createEnv(
            '<div id="dj-flash-container" data-dj-auto-dismiss="0"></div>'
        );

        const logs = [];
        window.console = { log: (...args) => logs.push(args.join(' ')), error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
        // djustDebug is not set (undefined/falsy)

        window.djust.flash.handleFlash({ action: 'put', level: 'info', message: 'Test' });

        expect(logs.some(l => l.includes('flash'))).toBe(false);
    });
});
