/**
 * Tests for the dev-mode error overlay (v0.5.1 P2).
 *
 * The overlay is driven by the `djust:error` CustomEvent that
 * 03-websocket.js / 03b-sse.js dispatch when the server sends
 * `type: "error"`. The overlay only renders when `window.DEBUG_MODE`
 * is true.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom({ debug = true } = {}) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root></div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {} close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.DEBUG_MODE = debug;

    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function dispatchError(dom, detail) {
    const event = new dom.window.CustomEvent('djust:error', { detail });
    dom.window.dispatchEvent(event);
}

describe('error overlay (dev mode)', () => {
    let dom;

    it('renders an overlay when djust:error fires in DEBUG mode', () => {
        dom = createDom();
        dispatchError(dom, { error: 'boom', event: 'save', traceback: 'File "x.py"...' });
        const overlay = dom.window.document.getElementById('djust-error-overlay');
        expect(overlay).not.toBeNull();
        expect(overlay.textContent).toContain('boom');
        expect(overlay.textContent).toContain('save');
    });

    it('does not render the overlay when DEBUG_MODE is false', () => {
        dom = createDom({ debug: false });
        dispatchError(dom, { error: 'boom' });
        const overlay = dom.window.document.getElementById('djust-error-overlay');
        expect(overlay).toBeNull();
    });

    it('escapes HTML in the error message to prevent XSS', () => {
        dom = createDom();
        dispatchError(dom, { error: '<script>alert(1)</script>' });
        const overlay = dom.window.document.getElementById('djust-error-overlay');
        expect(overlay).not.toBeNull();
        // The literal <script> tag must NOT appear as an executable element.
        expect(overlay.querySelector('script')).toBeNull();
        expect(overlay.innerHTML).toContain('&lt;script&gt;');
    });

    it('replaces the overlay on a second error rather than stacking', () => {
        dom = createDom();
        dispatchError(dom, { error: 'first' });
        dispatchError(dom, { error: 'second' });
        const all = dom.window.document.querySelectorAll('#djust-error-overlay');
        expect(all.length).toBe(1);
        expect(all[0].textContent).toContain('second');
        expect(all[0].textContent).not.toContain('first');
    });

    it('dismisses on Escape key', () => {
        dom = createDom();
        dispatchError(dom, { error: 'boom' });
        expect(dom.window.document.getElementById('djust-error-overlay')).not.toBeNull();
        const e = new dom.window.KeyboardEvent('keydown', { key: 'Escape' });
        dom.window.dispatchEvent(e);
        expect(dom.window.document.getElementById('djust-error-overlay')).toBeNull();
    });

    it('dismisses on close-button click', () => {
        dom = createDom();
        dispatchError(dom, { error: 'boom' });
        const btn = dom.window.document.querySelector('#djust-error-overlay .djust-eo-close');
        expect(btn).not.toBeNull();
        btn.click();
        expect(dom.window.document.getElementById('djust-error-overlay')).toBeNull();
    });

    it('dismisses on backdrop click but not on panel click', () => {
        dom = createDom();
        dispatchError(dom, { error: 'boom' });
        const panel = dom.window.document.querySelector('#djust-error-overlay .djust-eo-panel');
        panel.click();
        // Panel click: overlay still present.
        expect(dom.window.document.getElementById('djust-error-overlay')).not.toBeNull();
        // Backdrop click: dismissed.
        const overlay = dom.window.document.getElementById('djust-error-overlay');
        overlay.click();
        expect(dom.window.document.getElementById('djust-error-overlay')).toBeNull();
    });

    it('shows hint and validation sections when provided', () => {
        dom = createDom();
        dispatchError(dom, {
            error: 'validation failed',
            hint: 'did you forget required=False?',
            validation_details: { field_a: ['This field is required.'] },
        });
        const overlay = dom.window.document.getElementById('djust-error-overlay');
        expect(overlay.textContent).toContain('did you forget');
        expect(overlay.textContent).toContain('field_a');
    });

    it('omits debug_detail section when it duplicates the error message', () => {
        dom = createDom();
        dispatchError(dom, { error: 'same', debug_detail: 'same' });
        const overlay = dom.window.document.getElementById('djust-error-overlay');
        // "Detail" section-title should not appear when detail == error.
        expect(overlay.textContent).not.toContain('Detail');
    });

    it('exposes window.djustErrorOverlay.show / dismiss for manual triggering', () => {
        dom = createDom();
        expect(typeof dom.window.djustErrorOverlay.show).toBe('function');
        dom.window.djustErrorOverlay.show({ error: 'manual' });
        expect(dom.window.document.getElementById('djust-error-overlay')).not.toBeNull();
        dom.window.djustErrorOverlay.dismiss();
        expect(dom.window.document.getElementById('djust-error-overlay')).toBeNull();
    });
});
