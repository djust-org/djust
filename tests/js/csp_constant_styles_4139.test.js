/**
 * #4139 -- what the server hides with the `hidden` attribute, the client shows by
 * toggling that attribute, never an inline `display` the server no longer writes.
 *
 * A strict Content-Security-Policy refuses an inline `style="display:none"`, so the
 * components and the theme templates render `hidden` instead. A script that still
 * cleared `style.display` would leave every one of these elements hidden for good:
 * the scroll-to-top button, the connection-status bar, the theme modal and dropdown.
 * Each test loads the REAL script into a page holding the markup the server renders
 * (python/djust/tests/test_csp_constant_styles_4139.py pins that markup) and drives
 * it. The ttyd hook registers from a static module (`register.js`) instead of an
 * inline module script; its test imports that module.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(f, 'utf-8');

async function page(body, script, { scrollY = 0 } = {}) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${body}</body></html>`, {
        url: 'http://localhost/',
        runScripts: 'outside-only',
        pretendToBeVisual: true,
    });
    const { window } = dom;
    Object.defineProperty(window, 'scrollY', { value: scrollY, writable: true, configurable: true });
    window.scrollTo = () => {};
    window.eval(read(script));
    // The scripts initialise on DOMContentLoaded while the document is still loading.
    if (window.document.readyState === 'loading') {
        await new Promise((resolve) => window.document.addEventListener('DOMContentLoaded', resolve));
    }
    return window;
}

describe('scroll-to-top.js', () => {
    const BUTTON =
        '<button class="dj-scroll-to-top" data-threshold="300" aria-label="Top" title="Top" hidden><svg></svg></button>';

    it('stays hidden above the threshold', async () => {
        const window = await page(BUTTON, DIR + 'scroll-to-top.js', { scrollY: 0 });
        const btn = window.document.querySelector('.dj-scroll-to-top');
        expect(btn.hidden).toBe(true);
        expect(btn.classList.contains('dj-scroll-to-top--visible')).toBe(false);
    });

    it('removes the server\'s hidden attribute past the threshold, and fades out with the class', async () => {
        const window = await page(BUTTON, DIR + 'scroll-to-top.js', { scrollY: 0 });
        const btn = window.document.querySelector('.dj-scroll-to-top');
        window.scrollY = 500;
        window.dispatchEvent(new window.Event('scroll'));
        expect(btn.hidden).toBe(false);
        expect(btn.classList.contains('dj-scroll-to-top--visible')).toBe(true);
        expect(btn.getAttribute('style')).toBe(null); // nothing inline
        window.scrollY = 0;
        window.dispatchEvent(new window.Event('scroll'));
        expect(btn.classList.contains('dj-scroll-to-top--visible')).toBe(false);
    });
});

describe('connection-status.js', () => {
    const BAR =
        '<div class="dj-connection-status" data-reconnecting-text="Re…" data-connected-text="Back" ' +
        'role="status" aria-live="polite" hidden><span class="dj-connection-status__text">Re…</span></div>';

    it('shows the bar on disconnect and hides it again after the reconnect flash', async () => {
        const window = await page(BAR, DIR + 'connection-status.js');
        const el = window.document.querySelector('.dj-connection-status');
        expect(el.hidden).toBe(true);
        window.document.dispatchEvent(new window.Event('djust:disconnected'));
        expect(el.hidden).toBe(false);
        expect(el.classList.contains('dj-connection-status--reconnecting')).toBe(true);
        window.document.dispatchEvent(new window.Event('djust:reconnected'));
        expect(el.hidden).toBe(false);
        expect(el.querySelector('.dj-connection-status__text').textContent).toBe('Back');
        await new Promise((resolve) => window.setTimeout(resolve, 2100));
        expect(el.hidden).toBe(true);
        expect(el.classList.contains('dj-connection-status--connected')).toBe(false);
        expect(el.getAttribute('style')).toBe(null);
    });
});

describe('djust_theming/js/components.js (the plain-page fallback)', () => {
    const THEME = './python/djust/theming/static/djust_theming/js/components.js';
    // As djust_theming/components/modal.html and dropdown.html render them closed.
    const MODAL =
        '<button data-theme-modal-open="m">Open</button>' +
        '<div class="modal-backdrop" data-theme-modal="m" hidden>' +
        '<div class="modal" role="dialog" aria-modal="true">' +
        '<button class="modal-close" data-theme-modal-close>x</button></div></div>';
    const DROPDOWN =
        '<div class="dropdown" data-theme-dropdown="d">' +
        '<button class="dropdown-trigger" aria-haspopup="true" aria-expanded="false">Menu</button>' +
        '<div class="dropdown-menu" role="menu" hidden><a class="dropdown-item" role="menuitem" href="#">A</a></div></div>';

    it('opens and closes the modal through the hidden attribute', async () => {
        const window = await page(MODAL, THEME);
        const doc = window.document;
        const backdrop = doc.querySelector('[data-theme-modal]');
        doc.querySelector('[data-theme-modal-open]').click();
        expect(backdrop.hidden).toBe(false);
        doc.querySelector('[data-theme-modal-close]').click();
        expect(backdrop.hidden).toBe(true);
        doc.querySelector('[data-theme-modal-open]').click();
        doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
        expect(backdrop.hidden).toBe(true);
        doc.querySelector('[data-theme-modal-open]').click();
        backdrop.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
        expect(backdrop.hidden).toBe(true);
        expect(backdrop.getAttribute('style')).toBe(null);
    });

    it('Escape closes the open modal, not a later one the server rendered hidden', async () => {
        const window = await page(MODAL + MODAL.replace(/"m"/g, '"n"'), THEME);
        const doc = window.document;
        doc.querySelector('[data-theme-modal-open="m"]').click();
        doc.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
        expect(doc.querySelector('[data-theme-modal="m"]').hidden).toBe(true);
    });

    it('toggles the dropdown menu through the hidden attribute', async () => {
        const window = await page(DROPDOWN, THEME);
        const doc = window.document;
        const trigger = doc.querySelector('.dropdown-trigger');
        const menu = doc.querySelector('[role="menu"]');
        trigger.click();
        expect(menu.hidden).toBe(false);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        trigger.click();
        expect(menu.hidden).toBe(true);
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
        trigger.click();
        doc.body.click(); // outside
        expect(menu.hidden).toBe(true);
        trigger.click();
        menu.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        expect(menu.hidden).toBe(true);
        expect(menu.getAttribute('style')).toBe(null);
    });
});

describe('ttyd register.js', () => {
    it('registers the TtydTerminal hook from the module next to it', async () => {
        const src = pathToFileURL(path.resolve(DIR + 'ttyd/register.js')).href;
        const hook = pathToFileURL(path.resolve(DIR + 'ttyd/ttyd_terminal.js')).href;
        globalThis.window = globalThis.window || globalThis;
        delete globalThis.window.djust;
        await import(/* @vite-ignore */ src);
        const { TtydTerminalHook } = await import(/* @vite-ignore */ hook);
        expect(globalThis.window.djust.hooks.TtydTerminal).toBe(TtydTerminalHook);
    });
});
