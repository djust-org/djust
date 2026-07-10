/**
 * #1999 — `dj-input.debounce-N` silently fails to bind.
 *
 * A dot is a legal attribute-name character, so `dj-input.debounce-200` is one
 * LITERAL attribute that no `[dj-input]` selector matches — the directive never
 * binds (no handler, no event, no error). Only `dj-model` parses the
 * `.lazy` / `.debounce-N` in-name modifier; every other directive throttles via
 * the standalone `dj-debounce="N"` attribute.
 *
 * This adds a debug-mode warning (`_warnUnrecognizedDjModifiers`) so the trap is
 * no longer silent. Tests assert:
 *   - `dj-input.debounce-200` / `dj-input.lazy` / `dj-change.debounce-100` warn
 *     (the message names the attr + points at the standalone `dj-debounce` fix)
 *   - recognized `dj-model.debounce-300` / `dj-model.lazy` do NOT warn
 *   - other legit dotted conventions (`dj-keydown.enter`,
 *     `dj-window-keydown.escape`, `dj-loading.class`) do NOT warn
 *   - a plain `dj-input` + `dj-debounce` does NOT warn
 *   - the warning is debug-gated (silent when djustDebug is off) — gate-off
 *     sentinel: neutering `_warnUnrecognizedDjModifiers` turns the first test RED
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

const modifierWarns = (dom) =>
    dom.window.__warns.filter((w) => w.includes('Unrecognized modifier suffix'));

describe('#1999 dj-input modifier-suffix warning', () => {
    it('warns (debug) when dj-input carries a .debounce-N suffix', () => {
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <input dj-input.debounce-200="palette_search" value="">
            </div>
        `);
        initClient(dom);

        const hit = modifierWarns(dom).filter((w) => w.includes('dj-input.debounce-200'));
        expect(hit.length).toBeGreaterThan(0);
        // Message points at the real fix and names the only directive that supports it.
        expect(hit[0]).toContain('dj-debounce');
        expect(hit[0]).toContain('dj-model');
    });

    it('warns for dj-input.lazy', () => {
        const dom = createEnv(`<div dj-root dj-view="t.V"><input dj-input.lazy="s"></div>`);
        initClient(dom);
        expect(modifierWarns(dom).some((w) => w.includes('dj-input.lazy'))).toBe(true);
    });

    it('warns for dj-change.debounce-100 (same standalone-dj-debounce mechanism)', () => {
        const dom = createEnv(`<div dj-root dj-view="t.V"><input dj-change.debounce-100="s"></div>`);
        initClient(dom);
        expect(modifierWarns(dom).some((w) => w.includes('dj-change.debounce-100'))).toBe(true);
    });

    it('does NOT warn for recognized dj-model modifiers', () => {
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <input dj-model.debounce-300="q">
                <input dj-model.lazy="r">
            </div>
        `);
        initClient(dom);
        expect(modifierWarns(dom).length).toBe(0);
    });

    it('does NOT warn for other legit dotted conventions (key / loading modifiers)', () => {
        const dom = createEnv(`
            <div dj-root dj-view="t.V">
                <input dj-keydown.enter="go">
                <div dj-window-keydown.escape="close"></div>
                <button dj-click="save" dj-loading.class="busy" dj-loading.disable>Save</button>
            </div>
        `);
        initClient(dom);
        expect(modifierWarns(dom).length).toBe(0);
    });

    it('does NOT warn for a plain dj-input + standalone dj-debounce', () => {
        const dom = createEnv(
            `<div dj-root dj-view="t.V"><input dj-input="s" dj-debounce="200"></div>`
        );
        initClient(dom);
        expect(modifierWarns(dom).length).toBe(0);
    });

    it('is debug-gated — silent when djustDebug is off', () => {
        const dom = createEnv(
            `<div dj-root dj-view="t.V"><input dj-input.debounce-200="s"></div>`,
            { debug: false }
        );
        initClient(dom);
        expect(dom.window.__warns.length).toBe(0);
    });
});
