/**
 * 1.3 deprecation warnings for the client APIs djust 2.0.0 removes:
 * the `dj-params` attribute, the `[data-djust-root]` layout marker, and the
 * `window.LiveViewWebSocket` / `window.setCacheConfig` /
 * `window.globalLoadingManager` aliases. Each warns once per page, says it
 * is removed in 2.0.0, and names its replacement, and keeps working.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function load(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    dom.window.DJUST_USE_WEBSOCKET = false;
    const warn = vi.spyOn(dom.window.console, 'warn').mockImplementation(() => {});
    dom.window.eval(clientCode);
    warn.mockClear();
    return { window: dom.window, warn };
}

const removedIn2 = call => String(call[0]).includes('removed in djust 2.0.0');

describe('dj-params', () => {
    it('warns once, names dj-value-*, and still sends its params', () => {
        const { window, warn } = load();
        const make = () => {
            const el = window.document.createElement('button');
            el.setAttribute('dj-params', '{"todo_id": 4}');
            return el;
        };
        expect(window.djust.extractTypedParams(make()).todo_id).toBe(4);
        window.djust.extractTypedParams(make());
        const calls = warn.mock.calls.filter(removedIn2);
        expect(calls).toHaveLength(1);
        expect(calls[0][0]).toContain('dj-params');
        expect(calls[0][0]).toContain('dj-value-');
    });
});

describe('[data-djust-root] layout marker', () => {
    it('warns once, names dj-root, and still swaps the layout', () => {
        const { window, warn } = load('<div data-djust-root><p id="keep">live</p></div>');
        const html = '<!doctype html><html><head></head><body><nav>N</nav>' +
            '<div data-djust-root></div></body></html>';
        window.djust.djLayout.applyLayout({ path: 'a.html', html });
        window.djust.djLayout.applyLayout({ path: 'b.html', html });
        expect(window.document.querySelector('nav').textContent).toBe('N');
        expect(window.document.getElementById('keep').textContent).toBe('live');
        const calls = warn.mock.calls.filter(removedIn2);
        expect(calls).toHaveLength(1);
        expect(calls[0][0]).toContain('data-djust-root');
        expect(calls[0][0]).toContain('dj-root');
    });

    it('does not warn for dj-root', () => {
        const { window, warn } = load('<div dj-root></div>');
        window.djust.djLayout.applyLayout({
            path: 'a.html',
            html: '<!doctype html><html><head></head><body><div dj-root></div></body></html>',
        });
        expect(warn.mock.calls.filter(removedIn2)).toHaveLength(0);
    });
});

describe.each(['LiveViewWebSocket', 'setCacheConfig', 'globalLoadingManager'])('window.%s', name => {
    it('warns once on use, names window.djust, and is the same object', () => {
        const { window, warn } = load();
        expect(window[name]).toBe(window.djust[name]);
        expect(window[name]).toBe(window.djust[name]);
        const calls = warn.mock.calls.filter(removedIn2);
        expect(calls).toHaveLength(1);
        expect(calls[0][0]).toContain(`window.${name}`);
        expect(calls[0][0]).toContain(`window.djust.${name}`);
    });

    it('does not warn when nothing reads it', () => {
        const { warn } = load();
        expect(warn.mock.calls.filter(removedIn2)).toHaveLength(0);
    });

    it('can still be replaced by assignment', () => {
        const { window } = load();
        const replacement = {};
        window[name] = replacement;
        expect(window[name]).toBe(replacement);
    });
});
