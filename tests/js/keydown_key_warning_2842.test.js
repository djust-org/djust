/**
 * #2842 — an unknown dotted keyboard modifier is silently inert.
 *
 * `_normalizeKeyName` looks the dotted suffix up in a fixed map and falls
 * back to the RAW name (`keyMap[lower] || name`). The raw fallback is the
 * right runtime behaviour — it is what lets `.a` and correctly-cased DOM key
 * names (`.PageUp`, `.F1`) fire — but a multi-character ALL-LOWERCASE suffix
 * can never equal any KeyboardEvent.key (every DOM key name is either one
 * character or UpperCamelCase), so `.f1` / `.esc` / `.pagedown` are inert
 * forever, with no diagnostic — the same silent-trap class as #2831.
 *
 * The fix extends `_warnUnrecognizedDjModifiers` (debug-only, #1999's
 * warning channel — no new mechanism) with a keyboard pass. Tests assert:
 *   - inert spellings warn and name the attribute (`dj-keydown.f1`,
 *     `dj-keydown.esc`, `dj-window-keydown.bogus`)
 *   - recognized names do NOT warn (`dj-keydown.enter`, `.escape`, `.right`,
 *     `dj-keyup.arrowdown`)
 *   - single characters do NOT warn (`.a`, `.A`) — raw fallback fires them
 *   - `.PageUp` / `.F1` DO warn: HTML lowercases attribute names, so casing
 *     cannot rescue a multi-char suffix — `.pageup` never equals "PageUp"
 *   - `.lazy` on a keyboard directive still gets the #1999 message, not
 *     doubled warnings
 *   - the warning is debug-gated and fires once per bind (not per keystroke)
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml, { debug = true } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    dom.window.eval(`
        window.WebSocket = class { constructor(){ this.readyState = 0; } send(){} close(){} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function(){};
        window.djustDebug = ${debug ? 'true' : 'false'};
        window.__warns = [];
        console.warn = function(){ window.__warns.push(Array.prototype.join.call(arguments, ' ')); };
    `);
    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

const keyWarns = (dom) =>
    dom.window.__warns.filter((w) => w.includes('Unrecognized keyboard modifier'));

describe('#2842 unknown keyboard modifier warning', () => {
    it('warns for dj-keydown.f1 (lowercase f never matches e.key "F1")', () => {
        const dom = createEnv(`<div dj-root dj-view="t.V"><input dj-keydown.f1="go"></div>`);
        initClient(dom);
        const hit = keyWarns(dom).filter((w) => w.includes('dj-keydown.f1'));
        expect(hit.length).toBeGreaterThan(0);
        // The message names the offending suffix so the developer can act on it.
        expect(hit[0]).toContain('f1');
    });

    it('warns for dj-keydown.esc (the map knows "escape", not "esc")', () => {
        const dom = createEnv(`<div dj-root dj-view="t.V"><input dj-keydown.esc="go"></div>`);
        initClient(dom);
        expect(keyWarns(dom).some((w) => w.includes('dj-keydown.esc'))).toBe(true);
    });

    it('warns for the scoped dj-window-keydown.bogus', () => {
        const dom = createEnv(
            `<div dj-root dj-view="t.V"><div dj-window-keydown.bogus="go"></div></div>`
        );
        initClient(dom);
        expect(keyWarns(dom).some((w) => w.includes('dj-window-keydown.bogus'))).toBe(true);
    });

    it('warns for a lowercase multi-char name outside the map (dj-keydown.pagedown)', () => {
        const dom = createEnv(`<div dj-root dj-view="t.V"><input dj-keydown.pagedown="go"></div>`);
        initClient(dom);
        expect(keyWarns(dom).some((w) => w.includes('dj-keydown.pagedown'))).toBe(true);
    });

    it('does NOT warn for recognized map names', () => {
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <input dj-keydown.enter="go">
                <input dj-keydown.escape="cancel">
                <div dj-window-keydown.escape="close"></div>
                <input dj-keyup.arrowdown="next">
                <input dj-keydown.right="skip">
            </div>
        `);
        initClient(dom);
        expect(keyWarns(dom).length).toBe(0);
    });

    it('does NOT warn for single-character modifiers (raw fallback fires them)', () => {
        // `.A` reaches the DOM lowercased as `.a` — HTML attribute names are
        // case-insensitive — so both spellings fire for the lowercase 'a' key.
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <input dj-keydown.a="go">
                <input dj-keydown.A="go">
            </div>
        `);
        initClient(dom);
        expect(keyWarns(dom).length).toBe(0);
    });

    it('warns for .PageUp / .F1 too — HTML lowercases attribute names', () => {
        // A first draft of the fix skipped mixed-case names, assuming the raw
        // fallback could match a correctly-cased key name. It cannot: the HTML
        // parser lowercases attribute names BEFORE the DOM sees them, so
        // `dj-keydown.PageUp` is `dj-keydown.pageup` at dispatch time and
        // `e.key` is "PageUp" — inert, exactly like `.esc`. The test PROVED
        // this by failing on the carve-out (2 warnings fired).
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <input dj-keydown.PageUp="prev">
                <input dj-keydown.F1="help">
            </div>
        `);
        initClient(dom);
        // The DOM hands us the LOWERCASED names — that is the whole point,
        // and it is what the developer sees in the console.
        expect(keyWarns(dom).some((w) => w.includes('dj-keydown.pageup'))).toBe(true);
        expect(keyWarns(dom).some((w) => w.includes('dj-keydown.f1'))).toBe(true);
    });

    it('does NOT warn for non-keyboard dotted directives (dj-loading.class)', () => {
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <button dj-click="save" dj-loading.class="busy">Save</button>
            </div>
        `);
        initClient(dom);
        expect(keyWarns(dom).length).toBe(0);
    });

    it('.lazy on a keyboard directive still reports the #1999 message (no doubling)', () => {
        const dom = createEnv(`<div dj-root dj-view="t.V"><input dj-keydown.lazy="go"></div>`);
        initClient(dom);
        expect(keyWarns(dom).length).toBe(0);
        const modelMsgs = dom.window.__warns.filter((w) =>
            w.includes('Unrecognized modifier suffix')
        );
        expect(modelMsgs.length).toBe(1);
    });

    it('is debug-gated — silent when djustDebug is off', () => {
        const dom = createEnv(
            `<div dj-root dj-view="t.V"><input dj-keydown.esc="go"></div>`,
            { debug: false }
        );
        initClient(dom);
        expect(dom.window.__warns.length).toBe(0);
    });

    it('warns once per bind — pressing keys does not add warnings', () => {
        const dom = createEnv(
            `<div dj-root dj-view="t.V"><input dj-keydown.esc="go"></div>`
        );
        initClient(dom);
        const before = keyWarns(dom).length;
        expect(before).toBe(1);
        // Dispatch real keydown/keyup events — the warning must not live on
        // the event path, only the bind path.
        const input = dom.window.document.querySelector('input');
        input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        input.dispatchEvent(new dom.window.KeyboardEvent('keyup', { key: 'Escape', bubbles: true }));
        expect(keyWarns(dom).length).toBe(before);
    });
});
