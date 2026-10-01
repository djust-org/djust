/**
 * #3310 -- live preset switching must not create an un-nonced <style>.
 *
 * `setPresetWithoutReload` builds `<style id="djust-theme-css">` when there is
 * no theme element to update in place (critical-CSS mode) or when the theme was
 * a `<link>`. Under a CSP with `style-src` and no 'unsafe-inline' that style is
 * blocked unless it carries the page's nonce. `theme_head` hands the nonce to
 * theme.js as `window.__djust_theme_nonce` (set by the nonced anti-FOUC script).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const THEME_JS_PATH = './python/djust/theming/static/djust_theming/js/theme.js';
const themeJs = fs.readFileSync(THEME_JS_PATH, 'utf-8');

function createDom(headHtml, nonce) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head>${headHtml}</head><body></body></html>`,
        { runScripts: 'outside-only', url: 'http://localhost/', pretendToBeVisual: true }
    );
    if (nonce !== undefined) {
        dom.window.__djust_theme_nonce = nonce;
    }
    dom.window.matchMedia = () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
    });
    dom.window.requestAnimationFrame = (cb) => { cb(0); return 1; };
    dom.window.cancelAnimationFrame = () => {};
    dom.window.eval(themeJs);
    return dom;
}

const CSS = ':root{--primary:1 2 3}';

describe('#3310 -- nonce on the style a live preset switch creates', () => {
    it('creates the style with the nonce from window.__djust_theme_nonce', () => {
        const dom = createDom('', 'abc123');
        dom.window.djustTheme.setPresetWithoutReload('rose', CSS);
        const style = dom.window.document.querySelector('style#djust-theme-css');
        expect(style).not.toBeNull();
        expect(style.getAttribute('nonce')).toBe('abc123');
        expect(style.textContent).toBe(CSS);
    });

    it('nonces the <style> that replaces a <link> theme element', () => {
        const dom = createDom('<link rel="stylesheet" href="/t.css" id="djust-theme-css" data-djust-theme>', 'n-link');
        dom.window.djustTheme.setPresetWithoutReload('rose', CSS);
        const doc = dom.window.document;
        expect(doc.querySelector('link#djust-theme-css')).toBeNull();
        expect(doc.querySelector('style#djust-theme-css').getAttribute('nonce')).toBe('n-link');
    });

    it('falls back to the nonce of an existing theme <style> when the global is absent', () => {
        const dom = createDom('<style data-djust-theme-critical nonce="from-critical">a{}</style>');
        dom.window.djustTheme.setPresetWithoutReload('rose', CSS);
        const style = dom.window.document.querySelector('style#djust-theme-css');
        expect(style.getAttribute('nonce')).toBe('from-critical');
    });

    it('reuses the server-rendered nonced <style id> in place (no second element)', () => {
        const dom = createDom('<style id="djust-theme-css" data-djust-theme nonce="n1">old{}</style>', 'n1');
        dom.window.djustTheme.setPresetWithoutReload('rose', CSS);
        const styles = dom.window.document.querySelectorAll('style#djust-theme-css');
        expect(styles.length).toBe(1);
        expect(styles[0].getAttribute('nonce')).toBe('n1');
        expect(styles[0].textContent).toBe(CSS);
    });

    it('adds no nonce attribute when the page has none (no CSP)', () => {
        const dom = createDom('');
        dom.window.djustTheme.setPresetWithoutReload('rose', CSS);
        const style = dom.window.document.querySelector('style#djust-theme-css');
        expect(style.hasAttribute('nonce')).toBe(false);
    });
});
