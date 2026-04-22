/**
 * Tests for dj-layout — runtime layout swap (v0.6.0).
 *
 * The module exposes `window.djust.djLayout.applyLayout(payload)` which
 * swaps the document body with a new layout's body while physically
 * moving the existing [data-djust-root] / [dj-root] element into the
 * new layout, preserving its identity and inner state.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '<div data-djust-root></div>') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );
    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {} close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

describe('dj-layout', () => {
    let dom;

    it('swaps the document body and preserves the live dj-root identity', () => {
        dom = createDom(`
            <header id="old-header">OLD</header>
            <div data-djust-root>
                <input id="msg" value="unsent" />
            </div>
        `);
        const originalRoot = dom.window.document.querySelector('[data-djust-root]');
        // Tag the live root so we can verify it's the SAME element after swap,
        // not a re-parse of the server-sent HTML (which would lose inner state).
        originalRoot._djTag = 'alive';

        dom.window.djust.djLayout.applyLayout({
            path: 'layouts/fullscreen.html',
            html: `<!doctype html><html><head></head><body>
                <header id="new-header">NEW</header>
                <div data-djust-root>REPLACE_ME</div>
                <footer id="new-footer">NEW</footer>
            </body></html>`,
        });

        const newHeader = dom.window.document.getElementById('new-header');
        const newFooter = dom.window.document.getElementById('new-footer');
        const oldHeader = dom.window.document.getElementById('old-header');
        expect(newHeader).toBeTruthy();
        expect(newHeader.textContent).toBe('NEW');
        expect(newFooter).toBeTruthy();
        expect(oldHeader).toBeNull();  // old layout gone

        const afterRoot = dom.window.document.querySelector('[data-djust-root]');
        expect(afterRoot).toBeTruthy();
        expect(afterRoot._djTag).toBe('alive');  // identity preserved
        // Inner state preserved: the <input value="unsent"> is still there.
        expect(afterRoot.querySelector('#msg').value).toBe('unsent');
    });

    it('dispatches djust:layout-changed on document', () => {
        dom = createDom('<div data-djust-root></div>');
        const events = [];
        dom.window.document.addEventListener('djust:layout-changed', (e) => events.push(e.detail));

        dom.window.djust.djLayout.applyLayout({
            path: 'layouts/app.html',
            html: '<!doctype html><html><head></head><body><div data-djust-root></div></body></html>',
        });

        expect(events.length).toBe(1);
        expect(events[0].path).toBe('layouts/app.html');
    });

    it('ignores the swap when the incoming html has no dj-root', () => {
        dom = createDom('<div data-djust-root><p>keep me</p></div>');
        const before = dom.window.document.body.innerHTML;

        dom.window.djust.djLayout.applyLayout({
            path: 'layouts/broken.html',
            html: '<!doctype html><html><head></head><body><div id="no-root"></div></body></html>',
        });

        // Body unchanged because the swap was refused.
        expect(dom.window.document.body.innerHTML.includes('keep me')).toBe(true);
        expect(dom.window.document.getElementById('no-root')).toBeNull();
    });

    it('ignores empty html payload', () => {
        dom = createDom('<div data-djust-root></div>');
        const before = dom.window.document.body.innerHTML;

        dom.window.djust.djLayout.applyLayout({ path: 'x', html: '' });
        dom.window.djust.djLayout.applyLayout({ path: 'x' });  // no html key

        expect(dom.window.document.body.innerHTML).toBe(before);
    });

    it('supports [dj-root] as well as [data-djust-root]', () => {
        dom = createDom('<div dj-root><p id="keep">live</p></div>');

        dom.window.djust.djLayout.applyLayout({
            path: 'layouts/app.html',
            html: '<!doctype html><html><head></head><body><nav>N</nav><div dj-root></div></body></html>',
        });

        expect(dom.window.document.querySelector('nav').textContent).toBe('N');
        expect(dom.window.document.getElementById('keep').textContent).toBe('live');
    });

    it('exposes applyLayout on window.djust.djLayout', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.djLayout.applyLayout).toBe('function');
    });
});
