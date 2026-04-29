/**
 * Tests for v0.9.0 PR-B `50-lazy-fill.js` — `window.djust.lazyFill(slotId)`
 * client-side reception of `<template id="djl-fill-X">` chunks.
 *
 * Verifies:
 *   1. Default (`data-trigger="flush"`): immediate slot replacement.
 *   2. `data-trigger="visible"`: defers until IntersectionObserver fires.
 *   3. Idempotency: double-fire is a no-op.
 *   4. `data-status="error"` / `"timeout"`: wraps fill in
 *      `<dj-error aria-live="polite">` for screen-reader announcement.
 *   5. Auto-scan on DOMContentLoaded: catches templates that arrived
 *      before the bundle loaded.
 *
 * Per the v0.8.6 retro #1113 rule: stubs that emulate browser callbacks
 * (IntersectionObserver here) MUST `await Promise.resolve()` before
 * invoking the callback so the test exercises the real microtask
 * boundary, not a sync invocation.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');


function installIOStub(win) {
    const observers = [];
    win.IntersectionObserver = class {
        constructor(cb, options) {
            this.cb = cb;
            this.options = options;
            this.targets = new Set();
            observers.push(this);
        }
        observe(el) { this.targets.add(el); }
        unobserve(el) { this.targets.delete(el); }
        disconnect() { this.targets.clear(); }
    };
    win.__observers = observers;
    win.__fireIntersection = async function (target) {
        // Microtask boundary per #1113 retro — the real IO callback
        // fires inside a microtask after the entry is observed.
        await Promise.resolve();
        for (const o of observers) {
            if (o.targets.has(target)) {
                o.cb([{ target, isIntersecting: true }]);
            }
        }
    };
}


function setup(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'outside-only',
    });
    const { window } = dom;
    installIOStub(window);
    // Eval the bundle into the JSDOM window scope.
    window.eval(clientCode);
    return { dom, window };
}


describe('lazy-fill: default flush trigger', () => {
    it('replaces dj-lazy-slot with template contents on call', () => {
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-A" data-trigger="flush"></dj-lazy-slot>
            <template id="djl-fill-slot-A" data-target="slot-A" data-status="ok">
                <div class="filled-A"><p>Lazy A content</p></div>
            </template>
        `);

        window.djust.lazyFill('slot-A');

        // Slot replaced with template's children.
        expect(window.document.querySelector('dj-lazy-slot[data-id="slot-A"]')).toBeNull();
        const filled = window.document.querySelector('.filled-A');
        expect(filled).not.toBeNull();
        expect(filled.textContent).toContain('Lazy A content');
        // Template removed.
        expect(window.document.getElementById('djl-fill-slot-A')).toBeNull();
    });

    it('is idempotent on double-fire', () => {
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-B" data-trigger="flush"></dj-lazy-slot>
            <template id="djl-fill-slot-B" data-target="slot-B" data-status="ok">
                <div class="filled-B">B</div>
            </template>
        `);

        window.djust.lazyFill('slot-B');
        // Second call: template is gone; lazyFill must no-op gracefully.
        expect(() => window.djust.lazyFill('slot-B')).not.toThrow();

        const filled = window.document.querySelectorAll('.filled-B');
        expect(filled).toHaveLength(1);
    });
});


describe('lazy-fill: visible trigger', () => {
    it('defers slot replacement until IntersectionObserver fires', async () => {
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-C" data-trigger="visible"></dj-lazy-slot>
            <template id="djl-fill-slot-C" data-target="slot-C" data-status="ok">
                <div class="filled-C">C</div>
            </template>
        `);

        window.djust.lazyFill('slot-C');

        // Before IO fires, slot is still there, fill not applied.
        expect(window.document.querySelector('dj-lazy-slot[data-id="slot-C"]')).not.toBeNull();
        expect(window.document.querySelector('.filled-C')).toBeNull();

        // Fire intersection (microtask-faithful per #1113).
        const slot = window.document.querySelector('dj-lazy-slot[data-id="slot-C"]');
        await window.__fireIntersection(slot);

        expect(window.document.querySelector('.filled-C')).not.toBeNull();
        expect(window.document.querySelector('dj-lazy-slot[data-id="slot-C"]')).toBeNull();
    });

    it('falls through to immediate fill when IntersectionObserver unavailable', () => {
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-D" data-trigger="visible"></dj-lazy-slot>
            <template id="djl-fill-slot-D" data-target="slot-D" data-status="ok">
                <div class="filled-D">D</div>
            </template>
        `);
        // Wipe the IO stub.
        delete window.IntersectionObserver;

        window.djust.lazyFill('slot-D');

        expect(window.document.querySelector('.filled-D')).not.toBeNull();
    });
});


describe('lazy-fill: error / timeout envelopes', () => {
    it('status="error" wraps fill in <dj-error aria-live="polite">', () => {
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-E" data-trigger="flush"></dj-lazy-slot>
            <template id="djl-fill-slot-E" data-target="slot-E" data-status="error">
                <span>boom</span>
            </template>
        `);

        window.djust.lazyFill('slot-E');

        const err = window.document.querySelector('dj-error');
        expect(err).not.toBeNull();
        expect(err.getAttribute('aria-live')).toBe('polite');
        expect(err.textContent).toContain('boom');
    });

    it('status="timeout" also wraps in <dj-error>', () => {
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-F" data-trigger="flush"></dj-lazy-slot>
            <template id="djl-fill-slot-F" data-target="slot-F" data-status="timeout">
                <span>too slow</span>
            </template>
        `);

        window.djust.lazyFill('slot-F');

        const err = window.document.querySelector('dj-error');
        expect(err).not.toBeNull();
        expect(err.getAttribute('aria-live')).toBe('polite');
    });
});


describe('lazy-fill: auto-scan on DOMContentLoaded', () => {
    it('processes templates that landed before the bundle loaded', () => {
        // 50-lazy-fill.js installs either a DOMContentLoaded listener
        // (document.readyState === 'loading') OR auto-fills inline
        // (already complete). JSDOM with `runScripts: outside-only`
        // lands in 'loading' state when the bundle is eval'd, so
        // we exercise the listener path explicitly.
        const { window } = setup(`
            <dj-lazy-slot data-id="slot-G" data-trigger="flush"></dj-lazy-slot>
            <template id="djl-fill-slot-G" data-target="slot-G" data-status="ok">
                <div class="filled-G">G</div>
            </template>
        `);

        // Fire DOMContentLoaded if the listener path was registered
        // (either branch should result in the same final state).
        window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));

        expect(window.document.querySelector('.filled-G')).not.toBeNull();
        expect(window.document.querySelector('dj-lazy-slot[data-id="slot-G"]')).toBeNull();
    });
});


describe('lazy-fill: missing slot handling', () => {
    it('drops the template when no matching slot exists (template-author error)', () => {
        const { window } = setup(`
            <template id="djl-fill-slot-H" data-target="slot-H" data-status="ok">
                <div class="filled-H">H</div>
            </template>
        `);

        // No <dj-lazy-slot data-id="slot-H"> exists.
        window.djust.lazyFill('slot-H');

        // Template is removed (avoid stale state on re-call).
        expect(window.document.getElementById('djl-fill-slot-H')).toBeNull();
        // Filled fragment was NOT inserted anywhere.
        expect(window.document.querySelector('.filled-H')).toBeNull();
    });
});
