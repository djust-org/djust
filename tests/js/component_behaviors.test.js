/**
 * Component behaviours that replaced inline event handlers
 * (src/53-component-behaviors.js).
 *
 * rich_select, combobox and theme_toggle used to open, close, filter or switch
 * the colour mode through onclick=/onkeydown=/oninput= attributes, which a
 * Content-Security-Policy without 'unsafe-inline' refuses. The markup now
 * carries none; the client bundle's delegated listeners on `document` do the
 * work. The markup below is what the component tags render.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const themeJs = fs.readFileSync('./python/djust/theming/static/djust_theming/js/theme.js', 'utf-8');

const RICH_SELECT = `
<div class="rich-select" id="rs-pick">
  <input type="hidden" name="pick" value="">
  <div class="rich-select-trigger" tabindex="0" role="combobox" aria-controls="rs-pick-listbox"
       aria-expanded="false" aria-haspopup="listbox"><span class="rich-select-placeholder">Select...</span></div>
  <div class="rich-select-dropdown" role="listbox" id="rs-pick-listbox">
    <div class="rich-select-search"><input type="text" class="rich-select-search-input" aria-label="Search options"></div>
    <div class="rich-select-option" data-value="a" role="option" aria-selected="false">Alpha</div>
    <div class="rich-select-option" data-value="b" role="option" aria-selected="false">Beta</div>
  </div>
</div>
<div class="rich-select rich-select--disabled" id="rs-off">
  <div class="rich-select-trigger" tabindex="-1" role="combobox" aria-expanded="false" aria-disabled="true">x</div>
</div>
<p id="elsewhere">elsewhere</p>`;

const COMBOBOX = `
<div class="combobox" id="city-combobox">
  <input class="combobox-input form-input" type="text" id="city-input" name="city" role="combobox"
         aria-autocomplete="list" aria-expanded="false" aria-controls="city-listbox" autocomplete="off">
  <div class="combobox-dropdown" id="city-listbox" role="listbox">
    <div class="combobox-option" role="option" aria-selected="false" data-value="a">Alpha</div>
  </div>
</div>`;

const THEME_TOGGLE = `
<div class="dj-theme-toggle" data-current="system" role="radiogroup" aria-label="Color theme">
  <button type="button" class="dj-theme-toggle__btn" role="radio" data-theme-mode="light" aria-checked="false">L</button>
  <button type="button" class="dj-theme-toggle__btn" role="radio" data-theme-mode="dark" aria-checked="false">D</button>
  <button type="button" class="dj-theme-toggle__btn dj-theme-toggle__btn--active" role="radio" data-theme-mode="system" aria-checked="true">S</button>
</div>`;

function createDom(bodyHtml, { withThemeJs = false, storedMode = null } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
            <div dj-root dj-view="app.View">${bodyHtml}</div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    if (storedMode) dom.window.localStorage.setItem('djust-theme-mode', storedMode);
    dom.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
    class MockWebSocket {
        constructor() { this.readyState = 1; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    if (withThemeJs) dom.window.eval(themeJs);
    try { dom.window.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    // jsdom stays at readyState 'loading' after an eval; a browser fires this.
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function click(dom, el) {
    el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

function key(dom, el, k) {
    const ev = new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev;
}

describe('rich_select', () => {
    it('opens and closes on a trigger click, mirroring aria-expanded', () => {
        const dom = createDom(RICH_SELECT);
        const doc = dom.window.document;
        const root = doc.getElementById('rs-pick');
        const trigger = root.querySelector('.rich-select-trigger');
        click(dom, trigger);
        expect(root.classList.contains('rich-select--open')).toBe(true);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        click(dom, trigger);
        expect(root.classList.contains('rich-select--open')).toBe(false);
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
    });

    it('toggles on Enter and Space, closes on Escape', () => {
        const dom = createDom(RICH_SELECT);
        const root = dom.window.document.getElementById('rs-pick');
        const trigger = root.querySelector('.rich-select-trigger');
        const enter = key(dom, trigger, 'Enter');
        expect(enter.defaultPrevented).toBe(true);
        expect(root.classList.contains('rich-select--open')).toBe(true);
        key(dom, trigger, 'Escape');
        expect(root.classList.contains('rich-select--open')).toBe(false);
        key(dom, trigger, ' ');
        expect(root.classList.contains('rich-select--open')).toBe(true);
    });

    it('closes on an option pick and on a click elsewhere', () => {
        const dom = createDom(RICH_SELECT);
        const doc = dom.window.document;
        const root = doc.getElementById('rs-pick');
        const trigger = root.querySelector('.rich-select-trigger');
        click(dom, trigger);
        click(dom, root.querySelector('[data-value="b"]'));
        expect(root.classList.contains('rich-select--open')).toBe(false);
        click(dom, trigger);
        click(dom, doc.getElementById('elsewhere'));
        expect(root.classList.contains('rich-select--open')).toBe(false);
    });

    it('stays open while typing in its search box, which filters options', () => {
        const dom = createDom(RICH_SELECT);
        const root = dom.window.document.getElementById('rs-pick');
        click(dom, root.querySelector('.rich-select-trigger'));
        const search = root.querySelector('.rich-select-search-input');
        click(dom, search);
        expect(root.classList.contains('rich-select--open')).toBe(true);
        search.value = 'bet';
        search.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        expect(root.querySelector('[data-value="a"]').style.display).toBe('none');
        expect(root.querySelector('[data-value="b"]').style.display).toBe('');
    });

    it('a disabled picker does not open', () => {
        const dom = createDom(RICH_SELECT);
        const off = dom.window.document.getElementById('rs-off');
        click(dom, off.querySelector('.rich-select-trigger'));
        key(dom, off.querySelector('.rich-select-trigger'), 'Enter');
        expect(off.classList.contains('rich-select--open')).toBe(false);
    });
});

describe('combobox', () => {
    it('keeps focus in the input while an option is pressed', () => {
        const dom = createDom(COMBOBOX);
        const option = dom.window.document.querySelector('.combobox-option');
        const down = new dom.window.MouseEvent('mousedown', { bubbles: true, cancelable: true });
        option.dispatchEvent(down);
        expect(down.defaultPrevented).toBe(true);
    });

    it('reflects the open list in aria-expanded and closes after the pick', () => {
        const dom = createDom(COMBOBOX);
        const doc = dom.window.document;
        const input = doc.getElementById('city-input');
        input.focus();
        expect(doc.activeElement).toBe(input);
        expect(input.getAttribute('aria-expanded')).toBe('true');
        click(dom, doc.querySelector('.combobox-option'));
        expect(doc.activeElement).not.toBe(input);
        expect(input.getAttribute('aria-expanded')).toBe('false');
    });
});

describe('theme_toggle', () => {
    it('switches the mode without theme.js and stores it under djust.theming\'s key', () => {
        const dom = createDom(THEME_TOGGLE);
        const doc = dom.window.document;
        click(dom, doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]'));
        expect(doc.documentElement.getAttribute('data-theme')).toBe('dark');
        expect(doc.documentElement.getAttribute('data-theme-mode')).toBe('dark');
        expect(dom.window.localStorage.getItem('djust-theme-mode')).toBe('dark');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]').getAttribute('aria-checked')).toBe('true');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="system"]').getAttribute('aria-checked')).toBe('false');
        expect(doc.querySelector('.dj-theme-toggle').getAttribute('data-current')).toBe('dark');
    });

    it('shows the stored mode at load', () => {
        const dom = createDom(THEME_TOGGLE, { storedMode: 'light' });
        const doc = dom.window.document;
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="light"]').getAttribute('aria-checked')).toBe('true');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="system"]').getAttribute('aria-checked')).toBe('false');
    });

    it('with theme.js on the page (theme_head), theme.js owns the switch', async () => {
        const dom = createDom(THEME_TOGGLE, { withThemeJs: true });
        const win = dom.window;
        const doc = win.document;
        expect(win.djustTheme).toBeTruthy();
        let setModeCalls = 0;
        const realSetMode = win.djustTheme.setMode.bind(win.djustTheme);
        win.djustTheme.setMode = (mode) => { setModeCalls += 1; realSetMode(mode); };
        click(dom, doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]'));
        expect(setModeCalls).toBe(1);
        expect(win.localStorage.getItem('djust-theme-mode')).toBe('dark');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]').getAttribute('aria-checked')).toBe('true');
    });

    it('theme.js shows a mode set elsewhere (server push) on the toggle', async () => {
        const dom = createDom(THEME_TOGGLE, { withThemeJs: true });
        const win = dom.window;
        // Let the page finish loading first: jsdom fires its own
        // DOMContentLoaded asynchronously, and the load-time sync of the
        // stored mode must not be what this test observes.
        if (win.document.readyState !== 'complete') {
            await new Promise((r) => win.addEventListener('load', r));
        }
        await new Promise((r) => setTimeout(r, 30));
        const light = win.document.querySelector('.dj-theme-toggle [data-theme-mode="light"]');
        expect(light.getAttribute('aria-checked')).toBe('false');
        win.djustTheme.setMode('light');
        await new Promise((r) => setTimeout(r, 60));
        expect(win.document.querySelector('.dj-theme-toggle [data-theme-mode="light"]').getAttribute('aria-checked')).toBe('true');
        expect(win.document.querySelector('.dj-theme-toggle [data-theme-mode="system"]').getAttribute('aria-checked')).toBe('false');
    });
});
