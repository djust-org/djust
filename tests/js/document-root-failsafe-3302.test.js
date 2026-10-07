/**
 * `dj-view` / `dj-root` on `<html>` or `<head>` is unsupported and ignored (#3302):
 * the client finds no container there, mounts nothing, stamps nothing and warns
 * once, so the page stays a plain HTTP page instead of replacing the document
 * element's content with a fragment (which used to throw a TypeError and leave
 * the header only).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

async function load(html) {
    const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
    const w = dom.window;
    if (!w.CSS) w.CSS = {};
    if (!w.CSS.escape) w.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    const warnings = [];
    w.console.warn = (...args) => warnings.push(args.join(' '));
    w.DJUST_USE_WEBSOCKET = false; // never open a socket from the test
    w.eval(clientCode);
    // The client initialises in a microtask once the bundle has run.
    await new Promise((resolve) => setTimeout(resolve, 0));
    return { w, warnings };
}

const PAGE = (htmlAttrs, headAttrs, bodyAttrs) =>
    `<!DOCTYPE html><html ${htmlAttrs}><head ${headAttrs}><title>t</title></head>` +
    `<body ${bodyAttrs}><header id="hd">H</header><main id="mn">M</main><footer id="ft">F</footer></body></html>`;

describe('a root on <html> or <head> is ignored', async () => {
    for (const [name, htmlAttrs, headAttrs] of [
        ['<html dj-view>', 'dj-view="a.B"', ''],
        ['<html dj-root>', 'dj-root', ''],
        ['<head dj-view>', '', 'dj-view="a.B"'],
        ['<head dj-root>', '', 'dj-root'],
    ]) {
        it(`${name}: no page container, nothing stamped`, async () => {
            const { w } = await load(PAGE(htmlAttrs, headAttrs, ''));
            // No container was found for the page, so no transport exists.
            expect(w.djust.liveViewInstance).toBeFalsy();
            w.djust._autoStampRootAttributes();
            expect(w.document.documentElement.hasAttribute('dj-liveview-root')).toBe(false);
            expect(w.document.head.hasAttribute('dj-liveview-root')).toBe(false);
            // The page itself is untouched.
            expect(Array.from(w.document.body.children).map((e) => e.id)).toEqual(['hd', 'mn', 'ft']);
        });

        it(`${name}: warns once, naming the fix`, async () => {
            const { w, warnings } = await load(PAGE(htmlAttrs, headAttrs, ''));
            w.djust._autoStampRootAttributes();
            w.djust._autoStampRootAttributes();
            const mine = warnings.filter((m) => m.includes('not supported and is ignored'));
            expect(mine.length).toBe(1);
            expect(mine[0]).toContain('Put dj-view on <body> or a <div>');
            expect(mine[0]).toContain(name.includes('head') ? '<head>' : '<html>');
        });
    }

    it('mounts nothing: no transport is created for the page', async () => {
        const { w } = await load(PAGE('dj-view="a.B"', '', ''));
        expect(w.djust.liveViewInstance).toBeFalsy();
    });

    it('control: the same page with the attribute on <body> does create one', async () => {
        const { w } = await load(PAGE('', '', 'dj-view="a.B"'));
        expect(w.djust.liveViewInstance).toBeTruthy();
    });

    it('a <body> root beside an ignored <html> attribute is still mounted', async () => {
        const { w } = await load(PAGE('dj-root', '', 'dj-view="a.B"'));
        expect(w.djust.liveViewInstance).toBeTruthy();
        expect(w.document.body.hasAttribute('dj-liveview-root')).toBe(true);
        expect(w.document.documentElement.hasAttribute('dj-liveview-root')).toBe(false);
    });

    it('a normal page is not warned about', async () => {
        const { w, warnings } = await load(PAGE('', '', 'dj-root'));
        w.djust._autoStampRootAttributes();
        expect(warnings.filter((m) => m.includes('not supported and is ignored'))).toEqual([]);
    });
});
