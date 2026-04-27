/**
 * Tests for #1147 — CSP-nonce-aware lazy-fill activator.
 *
 * The server emits the lazy-fill envelope as:
 *
 *   <dj-lazy-slot data-id="X" data-trigger="flush" nonce="abc123"></dj-lazy-slot>
 *   ...body, body close...
 *   <template id="djl-fill-X" data-status="ok" nonce="abc123">...</template>
 *   <script nonce="abc123">window.djust.lazyFill("X")</script>
 *
 * The script's nonce is consumed by the browser's CSP enforcer at parse
 * time (no client work needed). This test asserts that:
 *
 *   1. The lazy-fill activator correctly handles a nonce-bearing
 *      <dj-lazy-slot> placeholder — i.e. the `nonce=` attribute does
 *      not break the slot lookup or the replaceWith path.
 *   2. The `nonce=` attribute on `<dj-lazy-slot>` is available for any
 *      future client-side code that needs to dynamically append scripts
 *      under the same CSP policy.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');


function installIOStub(win) {
    const observers = [];
    win.IntersectionObserver = class {
        constructor(cb) {
            this.cb = cb;
            this.targets = new Set();
            observers.push(this);
        }
        observe(el) { this.targets.add(el); }
        unobserve(el) { this.targets.delete(el); }
        disconnect() { this.targets.clear(); }
    };
    win.__observers = observers;
}


function setup(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'outside-only',
    });
    const { window } = dom;
    installIOStub(window);
    window.eval(clientCode);
    return { dom, window };
}


describe('lazy-fill CSP: nonce attribute on placeholder', () => {
    it('replaceWith works when <dj-lazy-slot> carries a nonce attribute', () => {
        // The server-emitted placeholder includes nonce="..." when CSP
        // is in use. The activator must still find and replace it.
        const { window } = setup(`
            <dj-lazy-slot data-id="csp-A" data-trigger="flush" nonce="abc123"></dj-lazy-slot>
            <template id="djl-fill-csp-A" data-target="csp-A" data-status="ok" nonce="abc123">
                <div class="filled-csp-A">CSP-friendly content</div>
            </template>
        `);

        window.djust.lazyFill('csp-A');

        expect(window.document.querySelector('dj-lazy-slot[data-id="csp-A"]')).toBeNull();
        const filled = window.document.querySelector('.filled-csp-A');
        expect(filled).not.toBeNull();
        expect(filled.textContent).toContain('CSP-friendly content');
    });

    it('exposes the nonce on the placeholder for client-side reads', () => {
        // The placeholder's nonce attr is server-emitted; the activator
        // must not strip it. This test asserts it's reachable via
        // getAttribute() for any future client code that needs to
        // append scripts under the same CSP policy.
        const { window } = setup(`
            <dj-lazy-slot data-id="csp-B" data-trigger="flush" nonce="xyz789"></dj-lazy-slot>
            <template id="djl-fill-csp-B" data-target="csp-B" data-status="ok" nonce="xyz789">
                <div class="filled-csp-B">B</div>
            </template>
        `);

        const slot = window.document.querySelector('dj-lazy-slot[data-id="csp-B"]');
        expect(slot.getAttribute('nonce')).toBe('xyz789');
    });
});


describe('lazy-fill CSP: backward-compatibility', () => {
    it('placeholders without nonce work exactly as before (no regression)', () => {
        // Sites without django-csp middleware get no nonce attribute.
        // The activator must still work for them.
        const { window } = setup(`
            <dj-lazy-slot data-id="no-csp" data-trigger="flush"></dj-lazy-slot>
            <template id="djl-fill-no-csp" data-target="no-csp" data-status="ok">
                <div class="filled-no-csp">classic</div>
            </template>
        `);

        window.djust.lazyFill('no-csp');

        const filled = window.document.querySelector('.filled-no-csp');
        expect(filled).not.toBeNull();
        expect(filled.textContent).toContain('classic');
    });
});
