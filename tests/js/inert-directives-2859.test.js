/**
 * Regression tests for #2859 — silently-inert directives, de-silenced via
 * the #1999/#2842 debug-only warning pass:
 *
 *   1. dj-shortcut key NAMES in the attribute VALUE (`pageup:handler`)
 *      resolve through `_normalizeKeyName` like the dotted keyboard
 *      directives. An all-lowercase multi-character name can never equal a
 *      KeyboardEvent.key, so the shortcut is dead on arrival — and nothing
 *      warned. Unlike attribute NAMES (which the HTML parser lowercases),
 *      attribute VALUES keep their casing, so the correctly-cased raw
 *      spelling (`PageUp:handler`) genuinely fires — the warning must NOT
 *      fire for it, and the positive integration case pins that.
 *
 *   2. dj-document-scroll / dj-document-resize are recognised by
 *      _scanScopedElements but the document-level listener is deliberately
 *      never installed (`resize` never fires on `document`), so the
 *      attributes parse and do nothing. The warning points at the
 *      documented dj-window-* twins.
 *
 * Gate-off sentinel: neutering the new warning branches turns the "warns"
 * tests RED; the "does not warn" tests are the false-positive guards.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

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

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            window._testFetchCalls.push({ eventName: eventName });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);
    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

const shortcutNameWarns = (dom) =>
    dom.window.__warns.filter((w) => w.includes('Unrecognized keyboard name in dj-shortcut value'));
const unsupportedWarns = (dom) =>
    dom.window.__warns.filter((w) => w.includes('Unsupported directive on attribute'));

describe('#2859 dj-shortcut inert key-name warning', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('warns (debug) when the value spells an all-lowercase multi-char key name', () => {
        const dom = createEnv(
            '<div dj-view="t.V"><div dj-shortcut="pageup:page_handler"></div></div>'
        );
        initClient(dom);

        const hits = shortcutNameWarns(dom);
        expect(hits.length).toBeGreaterThan(0);
        // Message names the dead spelling and the working raw-cased spelling.
        expect(hits[0]).toContain('pageup');
        expect(hits[0]).toContain('PageUp');
        expect(hits[0]).toContain('page_handler');
    });

    it('does NOT warn for a correctly-cased raw key name — it genuinely fires', async () => {
        // Positive control for the predicate: attribute VALUES keep their
        // casing, so `PageUp:handler` matches KeyboardEvent.key "PageUp".
        const dom = createEnv(
            '<div dj-view="t.V"><div id="sc" dj-shortcut="PageUp:page_handler"></div></div>'
        );
        initClient(dom);

        expect(shortcutNameWarns(dom)).toEqual([]);

        dom.window.document.body.dispatchEvent(
            new dom.window.KeyboardEvent('keydown', { key: 'PageUp', code: 'PageUp', bubbles: true })
        );
        await nativeSleep(60);

        expect(dom.window._testFetchCalls.map((c) => c.eventName)).toEqual(['page_handler']);
    });

    it('does NOT warn for mapped names, modifier combos on mapped keys, or single characters', () => {
        const dom = createEnv(
            '<div dj-view="t.V">' +
                '<div dj-shortcut="escape:close"></div>' +
                '<div dj-shortcut="ctrl+k:palette"></div>' +
                '<div dj-shortcut="k:do_thing"></div>' +
            '</div>'
        );
        initClient(dom);

        expect(shortcutNameWarns(dom)).toEqual([]);
    });

    it('deduplicates per bind pass: the same dead name warns once', () => {
        const dom = createEnv(
            '<div dj-view="t.V">' +
                '<div dj-shortcut="pageup:one,pageup:two"></div>' +
                '<div dj-shortcut="pagedown:three"></div>' +
                '<div dj-shortcut="pageup:four"></div>' +
            '</div>'
        );
        initClient(dom);

        const hits = shortcutNameWarns(dom);
        expect(hits.length).toBe(2); // pageup once, pagedown once
        expect(hits[0]).toContain('pageup');
        expect(hits[1]).toContain('pagedown');
    });

    it('is debug-gated (silent when djustDebug is off)', () => {
        const dom = createEnv(
            '<div dj-view="t.V"><div dj-shortcut="pageup:handler"></div></div>',
            { debug: false }
        );
        initClient(dom);

        expect(shortcutNameWarns(dom)).toEqual([]);
        expect(dom.window.__warns).toEqual([]);
    });
});

describe('#2859 dj-document-scroll / dj-document-resize unsupported warning', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('warns for dj-document-scroll and points at the dj-window-scroll twin', () => {
        const dom = createEnv(
            '<div dj-view="t.V"><div dj-document-scroll="on_scroll"></div></div>'
        );
        initClient(dom);

        const hits = unsupportedWarns(dom);
        expect(hits.length).toBeGreaterThan(0);
        expect(hits[0]).toContain('dj-document-scroll');
        expect(hits[0]).toContain('dj-window-scroll');
    });

    it('warns for dj-document-resize (a resize event never targets document)', () => {
        const dom = createEnv(
            '<div dj-view="t.V"><div dj-document-resize="on_resize"></div></div>'
        );
        initClient(dom);

        const hits = unsupportedWarns(dom);
        expect(hits.length).toBeGreaterThan(0);
        expect(hits[0]).toContain('dj-document-resize');
        expect(hits[0]).toContain('dj-window-resize');
    });

    it('does NOT warn for the supported dj-window-* twins', () => {
        const dom = createEnv(
            '<div dj-view="t.V">' +
                '<div dj-window-scroll="on_scroll"></div>' +
                '<div dj-window-resize="on_resize"></div>' +
            '</div>'
        );
        initClient(dom);

        expect(unsupportedWarns(dom)).toEqual([]);
        expect(dom.window.__warns).toEqual([]);
    });

    it('is debug-gated (silent when djustDebug is off)', () => {
        const dom = createEnv(
            '<div dj-view="t.V"><div dj-document-scroll="on_scroll"></div></div>',
            { debug: false }
        );
        initClient(dom);

        expect(unsupportedWarns(dom)).toEqual([]);
    });
});

describe('#2859 the pre-existing warning passes are unchanged', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('#1999 model-modifier and #2842 inert-key cases still warn; legit dotted forms still silent', () => {
        const dom = createEnv(
            '<div dj-view="t.V">' +
                '<input dj-input.debounce-200="s">' +       // #1999: warns
                '<div dj-keydown.esc="x"></div>' +          // #2842: warns
                '<div dj-keydown.escape="x"></div>' +       // legit: silent
                '<div dj-window-keydown.escape="x"></div>' + // legit: silent
                '<input dj-model.debounce-300="q">' +        // legit: silent
            '</div>'
        );
        initClient(dom);

        const all = dom.window.__warns.join('\n');
        expect(all).toContain('Unrecognized modifier suffix');
        expect(all).toContain('Unrecognized keyboard modifier');
        // The legit dotted forms must produce no warning at all.
        expect(dom.window.__warns.some((w) => w.includes('dj-keydown.escape'))).toBe(false);
        expect(dom.window.__warns.some((w) => w.includes('dj-window-keydown.escape'))).toBe(false);
        expect(dom.window.__warns.some((w) => w.includes('dj-model.debounce-300'))).toBe(false);
    });
});
