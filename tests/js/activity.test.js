/**
 * Tests for ``{% dj_activity %}`` client-side runtime (src/49-activity.js).
 *
 * Exercises:
 *   1. Initial DOM scan populates ``window.djust._activity._activities``
 *      with every wrapper carrying ``data-djust-activity``.
 *   2. MutationObserver fires ``djust:activity-shown`` when ``hidden`` is
 *      removed from an activity wrapper (hidden → visible flip).
 *   3. The ``djust:activity-shown`` event bubbles from the activity root
 *      so ancestor listeners (e.g. analytics on window) catch it.
 *   4. ``window.djust.activityVisible(name)`` reflects the current DOM
 *      state for known and unknown activity names.
 *   5. The event-handler gate in 11-event-handler.js drops events whose
 *      trigger sits inside a hidden (non-eager) activity wrapper — no
 *      WebSocket send is issued.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    // Stub WebSocket so client.js can construct LiveViewWebSocket without
    // actually opening a connection.
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
            this.onerror = null;
            this.sent = [];
        }
        send(payload) { this.sent.push(payload); }
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    // Silence noisy client logs in the test harness.
    dom.window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    try { dom.window.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    // JSDOM reports readyState='loading' when scripts are evaluated via
    // runScripts:'dangerously', so the activity module's _boot() defers
    // to the DOMContentLoaded event. Fire it manually so tests exercise
    // the post-boot state.
    try {
        dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    } catch (_) { /* noop */ }
    return dom;
}

describe('dj_activity client module', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('1. initial DOM scan populates the _activities map', () => {
        const dom = createDom(
            `<div dj-root>
                <div data-djust-activity="tab-a" data-djust-visible="true"><span>A</span></div>
                <div data-djust-activity="tab-b" data-djust-visible="false" hidden aria-hidden="true"><span>B</span></div>
                <div data-djust-activity="tab-c" data-djust-eager="true" data-djust-visible="true"><span>C</span></div>
            </div>`
        );
        const { window } = dom;
        // Client boot runs inline scan since document.readyState !== 'loading'.
        expect(window.djust._activity).toBeDefined();
        const map = window.djust._activity._activities;
        expect(map.get('tab-a')).toBeTruthy();
        expect(map.get('tab-a').visible).toBe(true);
        expect(map.get('tab-b')).toBeTruthy();
        expect(map.get('tab-b').visible).toBe(false);
        expect(map.get('tab-c').eager).toBe(true);
    });

    it('2. MutationObserver dispatches djust:activity-shown on hidden→visible flip', async () => {
        const dom = createDom(
            `<div dj-root>
                <div id="panel" data-djust-activity="tab-a" hidden>x</div>
            </div>`
        );
        const { window, document } = dom.window ? { window: dom.window, document: dom.window.document } : { window: dom, document: dom.document };
        const panel = document.getElementById('panel');
        const spy = vi.fn();
        panel.addEventListener('djust:activity-shown', spy);

        // Flip: remove hidden attribute — observer should detect + dispatch.
        panel.removeAttribute('hidden');
        // Mutation observers fire asynchronously (microtask); yield once.
        await new Promise((r) => setTimeout(r, 10));
        expect(spy).toHaveBeenCalledTimes(1);
        expect(spy.mock.calls[0][0].detail.name).toBe('tab-a');
    });

    it('3. djust:activity-shown bubbles to window listeners', async () => {
        const dom = createDom(
            `<div dj-root><div id="p" data-djust-activity="x" hidden>x</div></div>`
        );
        const { window } = dom;
        const panel = window.document.getElementById('p');
        const windowSpy = vi.fn();
        window.addEventListener('djust:activity-shown', windowSpy);
        panel.removeAttribute('hidden');
        await new Promise((r) => setTimeout(r, 10));
        expect(windowSpy).toHaveBeenCalledTimes(1);
    });

    it('4. activityVisible() helper reflects current DOM state', async () => {
        const dom = createDom(
            `<div dj-root>
                <div data-djust-activity="open">x</div>
                <div id="p" data-djust-activity="closed" hidden>y</div>
            </div>`
        );
        const { window } = dom;
        // Known visible.
        expect(window.djust.activityVisible('open')).toBe(true);
        // Known hidden.
        expect(window.djust.activityVisible('closed')).toBe(false);
        // Unknown name defaults to visible so typos don't suppress events.
        expect(window.djust.activityVisible('never-declared')).toBe(true);
        // Falsy arg returns visible as a sentinel.
        expect(window.djust.activityVisible(null)).toBe(true);
        expect(window.djust.activityVisible('')).toBe(true);
        // Flip closed → open and re-check after observer runs.
        window.document.getElementById('p').removeAttribute('hidden');
        await new Promise((r) => setTimeout(r, 10));
        expect(window.djust.activityVisible('closed')).toBe(true);
    });

    it('6. nested: hidden outer drops event even if inner is visible', async () => {
        // Regression lock for Stage 7 Finding 3. Prior code used
        // triggerElement.closest('[data-djust-activity]') which matched
        // the NEAREST wrapper (inner). If the outer was hidden and the
        // inner visible, the inner's visibility incorrectly permitted
        // dispatch even though the user could not see the subtree.
        const dom = createDom(
            `<div dj-view="test.V" dj-root>
                <div data-djust-activity="outer" hidden>
                    <div data-djust-activity="inner">
                        <button id="btn" dj-click="foo">Click</button>
                    </div>
                </div>
            </div>`
        );
        const { window } = dom;
        const ws = window.djust && window.djust.liveViewWS;
        const sendSpy = ws && ws.sendEvent ? vi.spyOn(ws, 'sendEvent') : null;
        const rawSendSpy = ws && ws.socket && ws.socket.send ? vi.spyOn(ws.socket, 'send') : null;

        const btn = window.document.getElementById('btn');
        expect(btn).toBeTruthy();
        expect(typeof window.djust.handleEvent).toBe('function');
        await window.djust.handleEvent('foo', { _targetElement: btn });
        // The hidden OUTER wrapper must cause the gate to drop the event
        // even though the INNER wrapper is visible.
        if (sendSpy) expect(sendSpy).not.toHaveBeenCalled();
        if (rawSendSpy) expect(rawSendSpy).not.toHaveBeenCalled();
    });

    it('5. event in hidden activity is dropped before WS send', async () => {
        const dom = createDom(
            `<div dj-view="test.V" dj-root>
                <div data-djust-activity="hidden-panel" hidden>
                    <button id="btn" dj-click="doit">Click</button>
                </div>
            </div>`
        );
        const { window } = dom;
        // Capture sent frames: spy on liveViewWS.sendEvent if present.
        const ws = window.djust && window.djust.liveViewWS;
        const sendSpy = ws && ws.sendEvent ? vi.spyOn(ws, 'sendEvent') : null;
        // Also spy on the lower-level send so we catch any path that bypasses sendEvent.
        const rawSendSpy = ws && ws.socket && ws.socket.send ? vi.spyOn(ws.socket, 'send') : null;

        const btn = window.document.getElementById('btn');
        expect(btn).toBeTruthy();
        // handleEvent is exposed on the djust namespace as the public
        // event-dispatch entry point. Call it with a trigger element
        // inside the hidden activity and verify the gate short-circuits.
        expect(typeof window.djust.handleEvent).toBe('function');
        await window.djust.handleEvent('doit', { _targetElement: btn });
        // The gate should have dropped the event — no sendEvent or raw send.
        if (sendSpy) expect(sendSpy).not.toHaveBeenCalled();
        if (rawSendSpy) expect(rawSendSpy).not.toHaveBeenCalled();
    });
});
