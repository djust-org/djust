/**
 * #3320 (3) -- the preset selector must keep showing the active preset after a
 * light/dark toggle.
 *
 * `theme_preset_selector` renders the ACTIVE preset as the selected option (the
 * server resolved it: cookie, session, pack, or the configured default). A mode
 * toggle runs `updateUIState`, which used to overwrite every `.theme-preset-select`
 * with `getPreset()` -- localStorage only, falling back to 'default'. A preset
 * the visitor never picked on this browser (a project configured with
 * `preset: "legal"`, or a theme pack) has no localStorage entry, so the selector
 * flipped to "Default" while the page still wore `legal`.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const themeJs = fs.readFileSync('./python/djust/theming/static/djust_theming/js/theme.js', 'utf-8');

const SELECT =
    '<select class="theme-preset-select" data-theme-preset-select>' +
    '<option value="default">Default</option>' +
    '<option value="legal" selected>Legal</option>' +
    '<option value="rose">Rose</option>' +
    '</select>';

function createDom() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${SELECT}</body></html>`,
        { runScripts: 'outside-only', url: 'http://localhost/', pretendToBeVisual: true }
    );
    dom.window.matchMedia = () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} });
    dom.window.eval(themeJs);
    return dom;
}

// updateUIState debounces with setTimeout(16) then requestAnimationFrame.
const flush = (dom) => new Promise((r) => dom.window.setTimeout(() => dom.window.requestAnimationFrame(() => r()), 60));

describe('#3320 -- preset selector across a mode toggle', () => {
    it('keeps the server-rendered selection when the visitor never picked a preset', async () => {
        const dom = createDom();
        const select = dom.window.document.querySelector('.theme-preset-select');
        expect(select.value).toBe('legal');

        dom.window.djustTheme.toggle();
        await flush(dom);

        expect(select.value).toBe('legal');
    });

    it('keeps it across a second toggle too (light -> dark -> light)', async () => {
        const dom = createDom();
        const select = dom.window.document.querySelector('.theme-preset-select');
        dom.window.djustTheme.toggle();
        await flush(dom);
        dom.window.djustTheme.toggle();
        await flush(dom);
        expect(select.value).toBe('legal');
    });

    it('still follows a live preset switch', async () => {
        const dom = createDom();
        const select = dom.window.document.querySelector('.theme-preset-select');
        dom.window.djustTheme.setPresetWithoutReload('rose', ':root{--primary:1 2 3}');
        await flush(dom);
        expect(select.value).toBe('rose');
    });

    it('keeps the live-switched preset across a later mode toggle', async () => {
        const dom = createDom();
        const select = dom.window.document.querySelector('.theme-preset-select');
        dom.window.djustTheme.setPresetWithoutReload('rose', ':root{--primary:1 2 3}');
        await flush(dom);
        dom.window.djustTheme.setMode('dark');
        await flush(dom);
        expect(select.value).toBe('rose');
    });
});
