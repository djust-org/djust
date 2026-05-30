/**
 * #1674: when events degrade to full-page HTTP re-renders (WebSocket
 * unavailable, or the view's mount was rejected — e.g. its module is missing
 * from LIVEVIEW_ALLOWED_MODULES), the client must emit ONE actionable,
 * NON-debug-gated warning pointing the developer at the allowlist — not the
 * old debug-gated `console.log` that was silent by default.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function freshClient() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>'
        + '<div dj-root dj-liveview-root dj-view="test.View"><p>x</p></div>'
        + '</body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously' }
    );
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    // No WebSocket → liveViewWS stays null → handleEvent takes the HTTP fallback.
    // Mock fetch so the fallback's request resolves cleanly.
    dom.window.fetch = async () => ({
        ok: true,
        status: 200,
        headers: { get: () => 'text/html' },
        text: async () => '<html><body></body></html>',
        json: async () => ({}),
    });
    dom.window.eval(clientCode);
    return dom;
}

describe('HTTP-fallback actionable warning (#1674)', () => {
    let dom, warnings;

    beforeEach(() => {
        dom = freshClient();
        warnings = [];
        dom.window.console.warn = (...args) => warnings.push(args.join(' '));
        // Ensure debug is OFF (the old line was gated behind this).
        dom.window.djustDebug = false;
        globalThis.djustDebug = false;
    });

    function allowlistWarnings() {
        return warnings.filter((w) => w.includes('LIVEVIEW_ALLOWED_MODULES'));
    }

    it('emits an actionable allowlist warning on HTTP fallback even with djustDebug off', async () => {
        await dom.window.djust.handleEvent('increment', {});
        const hits = allowlistWarnings();
        expect(hits.length).toBe(1);
        expect(hits[0]).toContain('HTTP');
        expect(hits[0]).toContain('LIVEVIEW_ALLOWED_MODULES');
    });

    it('warns only ONCE per session, not on every degraded event', async () => {
        await dom.window.djust.handleEvent('increment', {});
        await dom.window.djust.handleEvent('decrement', {});
        await dom.window.djust.handleEvent('increment', {});
        expect(allowlistWarnings().length).toBe(1);
    });
});
