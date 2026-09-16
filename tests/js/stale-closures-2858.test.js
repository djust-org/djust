/**
 * Regression tests for #2858 — the three remaining stale-closure sites of
 * the #2845 class:
 *   1. dj-poll        (09-event-binding.js)  — setInterval closure captured
 *      the handler name, the interval, and bind-time params.
 *   2. _bindModel     (20-model-binding.js)  — change/input closure captured
 *      field/lazy/debounce parsed at bind time.
 *   3. bindUploadHandlers (15-uploads.js)    — change/drop closures captured
 *      the upload slot name at bind time.
 *
 * Same defect shape as #2855 (dj-shortcut / dj-click-away): an element that
 * SURVIVES a morphdom patch keeps both its listener and its bind marker, so
 * the marker alone cannot justify the skip — only an unchanged value can.
 *
 * dj-poll carries one EXTRA invariant (#2858): an unchanged value must NOT
 * restart the poll phase — the bind loop runs on every patch and a restart
 * would reset the interval timer on every morph. Pinned by the interval-ID
 * identity test below.
 *
 * Harness: identical to stale-scoped-listeners-2845.test.js — the built
 * client is evaluated into JSDOM, bind passes run explicitly, and the
 * assertions observe real dispatches (HTTP fallback fetch calls) or real
 * DOM side effects, not internal method calls.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        // JSDOM defaults to hidden=true (visibilityState="prerender") —
        // the poll loop skips ticks while hidden.
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

const getFetchCalls = (dom) => dom.window._testFetchCalls;
const eventNames = (dom) => getFetchCalls(dom).map((c) => c.eventName);
const bodyStrings = (dom) => getFetchCalls(dom).map((c) => JSON.stringify(c.body));
const flush = () => new Promise((r) => setTimeout(r, 80));

describe('#2858 dj-poll: a VALUE change on a surviving element must rebuild the phase', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('dispatches the NEW handler after the attribute value changes (old one stops)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.PollView"><div id="p" dj-poll="refresh" dj-poll-interval="50"></div></div>'
        );
        initClient(dom);

        // Stimulus check: the poll fires under the OLD value.
        await nativeSleep(250);
        expect(eventNames(dom).filter((n) => n === 'refresh').length).toBeGreaterThanOrEqual(1);

        // Server re-render changes the VALUE; morphdom keeps the element and
        // only mutates the attribute. A bind pass must rebuild the interval.
        const before = eventNames(dom).length;
        dom.window.document.getElementById('p').setAttribute('dj-poll', 'renamed');
        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(250);

        const after = eventNames(dom).slice(before);
        // Pre-fix this kept dispatching `refresh` (stale interval) and never
        // `renamed`. The post-rebind slice must contain ONLY new-name ticks.
        expect(after.length).toBeGreaterThanOrEqual(1);
        expect(after.every((n) => n === 'renamed')).toBe(true);
    });

    it('an UNCHANGED value on a surviving element does NOT restart the poll phase', async () => {
        // The #2855 rebuild rule must NOT be ported naively: the bind loop
        // runs on every patch, and restarting the interval on every bind
        // would reset the timer each time so a poll never fires on schedule.
        // Deterministic assertion: the interval identity is stable across
        // bind passes (a restart allocates a new timer ID).
        const dom = createTestEnv(
            '<div dj-view="app.PollView"><div id="p" dj-poll="refresh" dj-poll-interval="50"></div></div>'
        );
        initClient(dom);

        const el = dom.window.document.getElementById('p');
        const originalId = el._djustPollIntervalId;
        expect(originalId).toBeDefined();

        // Several re-binds with the value unchanged (e.g. unrelated patches).
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        expect(el._djustPollIntervalId).toBe(originalId);

        // The surviving phase must still be alive and firing.
        await nativeSleep(180);
        expect(eventNames(dom).length).toBeGreaterThanOrEqual(1);
    });

    it('an INTERVAL change on a surviving element rebuilds at the new cadence', async () => {
        // dj-poll-interval is baked into setInterval, so the rebuild key must
        // cover it: 200ms -> 20ms. Post-rebind, 250ms yields ~12 ticks; a
        // phase that never rebuilt yields at most 1.
        const dom = createTestEnv(
            '<div dj-view="app.PollView"><div id="p" dj-poll="refresh" dj-poll-interval="200"></div></div>'
        );
        initClient(dom);

        const el = dom.window.document.getElementById('p');
        const originalId = el._djustPollIntervalId;

        const before = eventNames(dom).length;
        el.setAttribute('dj-poll-interval', '20');
        dom.window.djust.bindLiveViewEvents();
        await nativeSleep(250);

        const after = eventNames(dom).slice(before);
        expect(el._djustPollIntervalId).not.toBe(originalId);
        expect(after.length).toBeGreaterThanOrEqual(5);
    });

    it('dispatches with FRESH data-* params after they change on the surviving element', async () => {
        // The old closure snapshotted params at bind time; dj-click and
        // dj-change read theirs at fire time. The poll phase must too.
        const dom = createTestEnv(
            '<div dj-view="app.PollView"><div id="p" dj-poll="refresh" dj-poll-interval="50" data-period="month"></div></div>'
        );
        initClient(dom);

        await nativeSleep(130);
        dom.window.document.getElementById('p').setAttribute('data-period', 'day');
        await nativeSleep(130);

        const bodies = bodyStrings(dom);
        expect(bodies.some((b) => b.includes('month'))).toBe(true);
        expect(bodies.some((b) => b.includes('day'))).toBe(true);
    });
});

describe('#2858 dj-model: an attribute change on a surviving element must rebuild the binding', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    const fireInput = (dom, el) =>
        el.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    const fireChange = (dom, el) =>
        el.dispatchEvent(new dom.window.Event('change', { bubbles: true }));

    it('dispatches update_model for the NEW field after the attribute value changes', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><input id="m" dj-model="field_a" value=""></div>'
        );
        initClient(dom);
        const el = dom.window.document.getElementById('m');
        dom.window.djust.bindModelElements();

        fireInput(dom, el);
        await flush();
        expect(eventNames(dom)).toEqual(['update_model']);
        expect(bodyStrings(dom)[0]).toContain('"field_a"');

        // The template re-points the directive; the input survives the morph.
        el.setAttribute('dj-model', 'field_b');
        dom.window.djust.bindModelElements();

        fireInput(dom, el);
        await flush();
        // Pre-fix this dispatched field_a a SECOND time (stale closure).
        expect(eventNames(dom)).toEqual(['update_model', 'update_model']);
        expect(bodyStrings(dom)[1]).toContain('"field_b"');
        expect(bodyStrings(dom)[1]).not.toContain('"field_a"');
    });

    it('switching dj-model to dj-model.lazy rebinds from input onto change', async () => {
        // lazy/debounce are parsed from the attribute NAME and captured by
        // the closure, so they are part of the rebuild key.
        const dom = createTestEnv(
            '<div dj-view="app.V"><input id="m" dj-model="q" value=""></div>'
        );
        initClient(dom);
        const el = dom.window.document.getElementById('m');
        dom.window.djust.bindModelElements();

        fireInput(dom, el);
        await flush();
        expect(eventNames(dom)).toEqual(['update_model']);

        el.setAttribute('dj-model.lazy', 'q');
        el.removeAttribute('dj-model');
        dom.window.djust.bindModelElements();

        fireInput(dom, el);
        await flush();
        // Pre-fix the old input listener kept firing (stale event type).
        expect(eventNames(dom)).toEqual(['update_model']);

        fireChange(dom, el);
        await flush();
        expect(eventNames(dom)).toEqual(['update_model', 'update_model']);
    });

    it('an unchanged attribute tuple does NOT double-bind (one dispatch per input)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><input id="m" dj-model="field_a" value=""></div>'
        );
        initClient(dom);
        const el = dom.window.document.getElementById('m');

        // Several re-binds with the attrs unchanged.
        dom.window.djust.bindModelElements();
        dom.window.djust.bindModelElements();
        dom.window.djust.bindModelElements();

        fireInput(dom, el);
        await flush();

        expect(eventNames(dom)).toEqual(['update_model']);
    });
});

describe('#2858 dj-upload: a slot-name change on a surviving element must rebuild the binding', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('re-applies the NEW slot config after the attribute value changes', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><input id="u" type="file" dj-upload="slot_a"></div>'
        );
        initClient(dom);
        const el = dom.window.document.getElementById('u');

        dom.window.djust.uploads.setConfigs({
            slot_a: { accept: 'image/*' },
            slot_b: { accept: 'application/pdf' },
        });

        // The template re-points the directive at a different slot.
        el.setAttribute('dj-upload', 'slot_b');
        dom.window.djust.uploads.bindHandlers();

        // The change closure captures the slot name, and the rebuild path
        // re-runs the config-derived attributes — pre-fix `accept` was never
        // applied because the stale marker skipped the rebind.
        expect(el.getAttribute('accept')).toBe('application/pdf');
    });

    it('routes drops to the NEW slot preview container after the value changes', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V">' +
                '<div id="zone" dj-upload-drop="drop_a"></div>' +
                '<div id="prev_a" dj-upload-preview="drop_a"></div>' +
                '<div id="prev_b" dj-upload-preview="drop_b"></div>' +
            '</div>'
        );
        initClient(dom);
        const zone = dom.window.document.getElementById('zone');
        const prevA = dom.window.document.getElementById('prev_a');
        const prevB = dom.window.document.getElementById('prev_b');

        const drop = () => {
            const file = new dom.window.File(['x'], 'a.png', { type: 'image/png' });
            const ev = new dom.window.Event('drop', { bubbles: true });
            ev.dataTransfer = { files: [file] };
            zone.dispatchEvent(ev);
        };

        drop();
        await flush();
        // Stimulus check: the first slot preview was populated.
        expect(prevA.children.length).toBeGreaterThan(0);

        const countA = prevA.children.length;
        zone.setAttribute('dj-upload-drop', 'drop_b');
        dom.window.djust.uploads.bindHandlers();

        drop();
        await flush();
        // Pre-fix the stale closure kept writing previews into drop_a and
        // never touched drop_b.
        expect(prevB.children.length).toBeGreaterThan(0);
        expect(prevA.children.length).toBe(countA);
    });

    it('an unchanged slot name does NOT double-bind (one drop dispatch per drop event)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V">' +
                '<div id="zone" dj-upload-drop="drop_a"></div>' +
                '<div id="prev_a" dj-upload-preview="drop_a"></div>' +
            '</div>'
        );
        initClient(dom);
        const zone = dom.window.document.getElementById('zone');

        // Count dispatches through the real path: with WS disconnected each
        // handler run logs exactly one "[Upload] WebSocket not connected".
        const errors = [];
        dom.window.console.error = function() {
            errors.push(Array.prototype.join.call(arguments, ' '));
        };

        dom.window.djust.uploads.bindHandlers();
        dom.window.djust.uploads.bindHandlers();
        dom.window.djust.uploads.bindHandlers();

        const file = new dom.window.File(['x'], 'a.png', { type: 'image/png' });
        const ev = new dom.window.Event('drop', { bubbles: true });
        ev.dataTransfer = { files: [file] };
        zone.dispatchEvent(ev);
        await flush();

        const notConnected = errors.filter((e) => e.includes('WebSocket not connected'));
        expect(notConnected.length).toBe(1);
    });
});
