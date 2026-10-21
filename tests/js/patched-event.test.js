/**
 * `djust:patched` fires after every frame that changed the page.
 *
 * `djust:rendered` confirms this owner's own state, which is what the
 * deferred-save status needs, so it skips `server_push` broadcast frames. Client
 * code that must re-sync after any patch (a counter, an `aria-describedby`, a
 * lock that a pushed render toggles) had no event for a pushed render and fell
 * back to a MutationObserver. `djust:patched` fires for every applied frame,
 * with its `source`, after the DOM is updated.
 */
import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');

function setup(name) {
    const dom = new JSDOM(
        '<!doctype html><html><body><div dj-root dj-view="app.views.Page"><p id="t">old</p></div></body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously' },
    );
    const { window } = dom;
    window.CSS ??= {};
    window.CSS.escape ??= value => String(value).replace(/([^\w-])/g, '\\$1');
    window.eval(client);
    const transport = new window.djust[name]();
    transport.viewMounted = true;
    transport.primaryViewPath = 'app.views.Page';  // what a mount sets
    if (name === 'LiveViewWebSocket') {
        transport.ws = { readyState: window.WebSocket.OPEN, send: () => {} };
    }
    const patched = [];
    const rendered = [];
    window.addEventListener('djust:patched', e => {
        patched.push({ ...e.detail, text: window.document.getElementById('t')?.textContent });
    });
    window.addEventListener('djust:rendered', e => rendered.push(e.detail));
    return { dom, transport, patched, rendered };
}

describe.each(['LiveViewWebSocket', 'LiveViewSSE'])('%s djust:patched', name => {
    it.each(['broadcast', 'tick', 'async'])('fires for a %s frame, after the DOM changed', async source => {
        const { dom, transport, patched, rendered } = setup(name);
        try {
            await transport.handleMessage({
                type: 'html_update', html: '<p id="t">new</p>', source, broadcast: source === 'broadcast',
            });
            expect(patched).toHaveLength(1);
            expect(patched[0].source).toBe(source);
            expect(patched[0].text).toBe('new');
            expect(patched[0].view).toBe('app.views.Page');
            // The documented event keeps its meaning: no broadcast frame.
            expect(rendered).toHaveLength(source === 'broadcast' ? 0 : 1);
        } finally { dom.window.close(); }
    });

    it('fires for the response to the page\'s own event, with rendered', async () => {
        const { dom, transport, patched, rendered } = setup(name);
        try {
            await transport.handleMessage({ type: 'html_update', html: '<p id="t">mine</p>' });
            expect(patched).toHaveLength(1);
            expect(patched[0].source).toBe('event');
            expect(patched[0].text).toBe('mine');
            expect(rendered).toHaveLength(1);
        } finally { dom.window.close(); }
    });

    it('does not fire for a frame that changed nothing', async () => {
        const { dom, transport, patched } = setup(name);
        try {
            await transport.handleMessage({ type: 'noop' });
            expect(patched).toHaveLength(0);
        } finally { dom.window.close(); }
    });
});
