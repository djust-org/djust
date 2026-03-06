/**
 * Integration test for WebSocket send-count deduplication.
 *
 * Regression test for #315 — verifies each user action sends exactly one
 * WebSocket message even when the server has delivered N patch responses,
 * each of which triggers bindLiveViewEvents() and could accumulate stale
 * handlers without the WeakMap dedup mechanism.
 *
 * The existing double_bind tests verify addEventListener counts; this test
 * goes further by simulating a real response cycle and asserting on the
 * WebSocket send path (via liveViewInstance.sendMessage spy).
 *
 * Setup note: JSDOM fires DOMContentLoaded asynchronously after construction.
 * Because client.js registers djustInit as the DOMContentLoaded handler (when
 * readyState is 'loading' at eval time), beforeEach must await that event to
 * obtain the liveViewInstance before running assertions.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'fs';
import { JSDOM } from 'jsdom';

const CLIENT_JS = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

describe('WebSocket send-count dedup after N server responses (#315)', () => {
    let dom, window, sendCount, mockWsInstance;

    beforeEach(async () => {
        dom = new JSDOM(
            `<!DOCTYPE html><html><body>
                <div dj-root="true" dj-view="myapp.views.Counter" id="root">
                    <button dj-click="increment" data-dj-id="btn1">Increment</button>
                </div>
            </body></html>`,
            { runScripts: 'dangerously' },
        );
        window = dom.window;

        if (!window.CSS) window.CSS = {};
        if (!window.CSS.escape) {
            window.CSS.escape = (str) => String(str).replace(/([^\w-])/g, '\\$1');
        }

        sendCount = 0;
        mockWsInstance = null;

        // Mock WebSocket — capture instance, start as OPEN so sendEvent() passes
        window.WebSocket = class MockWebSocket {
            constructor() {
                this.readyState = 1; // OPEN
                mockWsInstance = this;
            }
            send() {}
            close() {}
        };
        window.WebSocket.OPEN = 1;
        window.WebSocket.CONNECTING = 0;
        window.WebSocket.CLOSING = 2;
        window.WebSocket.CLOSED = 3;

        window.fetch = () => Promise.reject(new Error('no fetch'));

        // Eval client.js. JSDOM's readyState is still 'loading' at this point,
        // so client.js registers djustInit as a DOMContentLoaded listener rather
        // than calling it immediately.
        window.eval(CLIENT_JS);

        // Wait for JSDOM's own DOMContentLoaded to fire (it fires asynchronously
        // after construction). This triggers djustInit exactly once and avoids the
        // double-init bug that occurs when we dispatch the event manually.
        if (!window.djust.liveViewInstance) {
            await new Promise((resolve) => {
                window.document.addEventListener('DOMContentLoaded', resolve);
            });
        }

        // Get the live view WS instance created by djustInit
        const liveViewWS = window.djust.liveViewInstance;
        if (!liveViewWS) {
            throw new Error('djustInit did not create liveViewInstance');
        }

        // viewMounted is normally set by the WS onopen handler. In tests the mock
        // WS is immediately OPEN but onopen is never invoked, so set it manually.
        liveViewWS.viewMounted = true;
        liveViewWS.enabled = true;

        // Spy on sendMessage — the single choke-point all WS sends go through.
        // This is equivalent to counting ws.send() calls (sendMessage calls ws.send
        // exactly once per invocation) and is resilient to any WS state checks.
        const origSendMessage = liveViewWS.sendMessage.bind(liveViewWS);
        liveViewWS.sendMessage = function (data) {
            if (data && data.type === 'event') {
                sendCount++;
            }
            origSendMessage(data);
        };

        // Ignore any messages sent during init
        sendCount = 0;
    });

    it('sendMessage is called exactly once after N server patch responses and one click', async () => {
        const liveViewWS = window.djust.liveViewInstance;
        expect(liveViewWS).toBeTruthy();

        // Simulate N server patch responses arriving via the real onmessage handler.
        // Each response triggers handleServerResponse() → applyPatches() →
        // bindLiveViewEvents(), which is the scenario where stale handlers would
        // accumulate without WeakMap dedup.
        const N = 5;
        for (let i = 0; i < N; i++) {
            const patchMsg = JSON.stringify({ type: 'patch', patches: [] });
            if (mockWsInstance && mockWsInstance.onmessage) {
                mockWsInstance.onmessage({ data: patchMsg });
            }
        }

        // Drain any pending microtasks so liveViewInstance isn't replaced after reset
        await new Promise((r) => setTimeout(r, 0));

        // Reset counter — only count sends triggered by the user click below
        sendCount = 0;

        // Simulate one user click
        const btn = window.document.querySelector('[data-dj-id="btn1"]');
        btn.click();

        // Despite N server responses having triggered N rebind cycles,
        // a single click must produce exactly one WS event send.
        expect(sendCount).toBe(1);
    });

    it('sendMessage is called exactly once when bindLiveViewEvents is called N times before click', async () => {
        const liveViewWS = window.djust.liveViewInstance;
        expect(liveViewWS).toBeTruthy();

        // Directly call bindLiveViewEvents N times — the most minimal reproduction
        // of accumulated rebinds without going through the full response parser.
        const N = 10;
        for (let i = 0; i < N; i++) {
            window.djust.bindLiveViewEvents();
        }

        await new Promise((r) => setTimeout(r, 0));
        sendCount = 0;

        const btn = window.document.querySelector('[data-dj-id="btn1"]');
        btn.click();

        // Exactly one WS event message regardless of how many rebind cycles ran.
        expect(sendCount).toBe(1);
    });
});
