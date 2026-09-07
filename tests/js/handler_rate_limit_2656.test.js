/**
 * Tests for the client half of ``@debounce`` / ``@throttle``
 * (src/05-handler-rate-limit.js, wired into ``handleEvent``) — #2656.
 *
 * Before this module the two decorators stamped metadata nothing read:
 * ``debounceTimers`` / ``throttleState`` were declared in 04-cache.js and
 * only ever CLEARED, and ``window.handlerMetadata`` had zero readers.
 *
 * CLOCK DISCIPLINE (canon #1830). Every timing here runs on a fake clock
 * installed on the JSDOM window and advanced EXPLICITLY by the test. No
 * assertion depends on a real timer winning a race, and none asserts a
 * wall-clock duration — the invariants are ORDERINGS ("nothing has been
 * sent before the window elapses; exactly one thing has been sent after").
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

/**
 * A fully controllable clock: `Date.now` and `setTimeout`/`clearTimeout`
 * are replaced on the JSDOM window, and timers fire only when the test
 * calls `advance()`.
 */
function installClock(win) {
    let now = 1_000_000;
    let seq = 1;
    const timers = new Map(); // id -> {at, fn}

    win.Date.now = () => now;
    win.setTimeout = (fn, delay) => {
        const id = seq++;
        timers.set(id, { at: now + (Number(delay) || 0), fn });
        return id;
    };
    win.clearTimeout = id => {
        timers.delete(id);
    };

    return {
        advance(ms) {
            const target = now + ms;
            for (;;) {
                let dueId = null;
                let dueAt = Infinity;
                timers.forEach((t, id) => {
                    if (t.at <= target && t.at < dueAt) {
                        dueAt = t.at;
                        dueId = id;
                    }
                });
                if (dueId === null) break;
                const t = timers.get(dueId);
                timers.delete(dueId);
                now = t.at;
                t.fn();
            }
            now = target;
        },
        pending: () => timers.size,
    };
}

/**
 * Build a JSDOM running the real bundle, with a recording transport in
 * place of the WebSocket so every outbound event is observable.
 */
function createHarness(handlerConfig) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><head></head><body>'
        + '<div dj-view="test.V" dj-root><button id="btn" dj-click="search">go</button></div>'
        + '</body></html>',
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
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
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.console = {
        log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {},
    };
    try {
        dom.window.eval(clientCode);
    } catch (_) { /* ignore missing DOM APIs during init */ }
    try {
        dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    } catch (_) { /* noop */ }

    const { window } = dom;

    // Record what would leave the client. `handleEvent` reaches the socket
    // through the bundle-scoped `liveViewWS`, so the recorder has to sit on
    // the real object — assigning a replacement to
    // `window.djust.liveViewInstance` does NOT work (`liveViewWS` is a
    // binding, not a property read).
    //
    // It goes on the PROTOTYPE, not the instance: the bundle re-creates
    // `liveViewWS` (01-dom-helpers-turbo.js:115) during init, so an
    // instance-level stub is silently bypassed afterwards. That was the
    // first version of this harness, and it reported "0 events sent" for
    // every deferred dispatch — a false RED that looked exactly like a
    // broken gate.
    const ws = window.djust.liveViewInstance;
    if (!ws) throw new Error('harness: liveViewInstance missing — bundle did not init');
    const proto = Object.getPrototypeOf(ws);
    const sent = [];
    proto.sendEvent = function sendEvent(eventName, params) {
        sent.push({ eventName, params });
        return Promise.resolve(null);
    };

    if (handlerConfig) window.djust.setHandlerConfig(handlerConfig);

    // Installed AFTER bundle init so the client's own start-up timers use
    // the real ones; only the rate-limit gate's timers land in this queue.
    const clock = installClock(window);
    return { dom, window, sent, clock };
}

describe('#2656 — @debounce client gate', () => {
    it('1. collapses a burst of N events into ONE send carrying the LAST payload', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 0.5, max_wait: null } },
        });
        expect(typeof window.djust.applyHandlerRateLimit).toBe('function');

        for (const q of ['a', 'ab', 'abc', 'abcd', 'abcde']) {
            await window.djust.handleEvent('search', { query: q });
        }
        // ORDERING invariant: nothing has left the client while the window
        // is still open, no matter how many events arrived.
        expect(sent).toHaveLength(0);
        expect(clock.pending()).toBe(1);

        clock.advance(500);

        expect(sent).toHaveLength(1);
        expect(sent[0].eventName).toBe('search');
        expect(sent[0].params.query).toBe('abcde'); // last one wins
    });

    it('2. does not send before the wait elapses, and sends exactly once after', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 0.3, max_wait: null } },
        });
        await window.djust.handleEvent('search', { query: 'x' });
        clock.advance(299);
        expect(sent).toHaveLength(0);
        clock.advance(1);
        expect(sent).toHaveLength(1);
        clock.advance(5000);
        expect(sent).toHaveLength(1); // no repeat
    });

    it('3. max_wait forces a send while a burst is still arriving', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 0.5, max_wait: 1.0 } },
        });
        // A caller that never pauses for the full 500 ms wait: without
        // max_wait the send would be postponed forever.
        for (let i = 0; i < 10; i++) {
            await window.djust.handleEvent('search', { query: `q${i}` });
            clock.advance(200);
        }
        // 10 * 200 ms = 2000 ms of continuous typing. max_wait=1.0 s must
        // have forced at least one send; plain debounce would have sent 0.
        expect(sent.length).toBeGreaterThanOrEqual(1);
        // And the forced send carries a payload from the burst, not a stale one.
        expect(sent[0].params.query).toMatch(/^q\d$/);
    });

    it('4. an undecorated handler is untouched — sends immediately', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 0.5, max_wait: null } },
        });
        await window.djust.handleEvent('other_handler', { v: 1 });
        expect(sent).toHaveLength(1);
        expect(clock.pending()).toBe(0);
    });

    it('5. distinct handlers debounce independently', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 0.5, max_wait: null } },
            filter: { debounce: { wait: 0.5, max_wait: null } },
        });
        await window.djust.handleEvent('search', { q: 1 });
        await window.djust.handleEvent('filter', { q: 2 });
        await window.djust.handleEvent('search', { q: 3 });
        expect(sent).toHaveLength(0);
        clock.advance(500);
        expect(sent).toHaveLength(2);
        expect(sent.map(s => s.eventName).sort()).toEqual(['filter', 'search']);
        expect(sent.find(s => s.eventName === 'search').params.q).toBe(3);
    });

    it('6. the bypass flag is a third argument, never a params key on the wire', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 0.2, max_wait: null } },
        });
        await window.djust.handleEvent('search', { query: 'z' });
        clock.advance(200);
        expect(sent).toHaveLength(1);
        // Nothing rate-limit-internal may reach the server payload.
        const keys = Object.keys(sent[0].params);
        expect(keys.some(k => /rate|bypass/i.test(k))).toBe(false);
    });

    it('7. falls back to window.handlerMetadata when no mount frame arrived (HTTP path)', async () => {
        const { window, sent, clock } = createHarness(null);
        window.handlerMetadata = { search: { debounce: { wait: 0.4, max_wait: null } } };
        await window.djust.handleEvent('search', { query: 'a' });
        await window.djust.handleEvent('search', { query: 'b' });
        expect(sent).toHaveLength(0);
        clock.advance(400);
        expect(sent).toHaveLength(1);
        expect(sent[0].params.query).toBe('b');
    });
});

describe('#2656 — @throttle client gate', () => {
    it('8. caps the send rate: 10 events inside one interval produce 2 sends', async () => {
        const { window, sent, clock } = createHarness({
            on_scroll: { throttle: { interval: 1.0, leading: true, trailing: true } },
        });
        // Leading edge sends immediately.
        await window.djust.handleEvent('on_scroll', { y: 0 });
        expect(sent).toHaveLength(1);

        // Nine more inside the same 1 s window collapse to ONE trailing send.
        for (let i = 1; i <= 9; i++) {
            await window.djust.handleEvent('on_scroll', { y: i });
            clock.advance(50);
        }
        // 9 * 50 = 450 ms — still inside the window, trailing not yet due.
        expect(sent).toHaveLength(1);

        clock.advance(1000);
        expect(sent).toHaveLength(2);
        expect(sent[1].params.y).toBe(9); // trailing carries the LAST payload
    });

    it('8b. arms ONE trailing timer per window, not one per in-window event', async () => {
        // The #2700 review's gate-off M10: flipping the scheduling guard at
        // 05-handler-rate-limit.js `if (state.timeoutId === null || ...)` to
        // `if (true)` survived the whole suite. It is not a semantic no-op —
        // it leaks a timer per in-window event, and once the duplicates drain
        // the `else` branch DELETES the state entry, so the NEXT event reads
        // as a fresh leading edge and sends immediately, breaking the cap.
        //
        // Nothing saw it because two mechanisms shadow each other: the
        // `if (pending)` null-guard in the timer callback absorbs every
        // duplicate, so `sent` looks identical either way. This case asserts
        // on the TIMER COUNT instead, which is the only place the scheduling
        // guard is observable — it is the test that makes that mechanism
        // independently reachable (#2135).
        const { window, sent, clock } = createHarness({
            m: { throttle: { interval: 1.0, leading: true, trailing: true } },
        });
        await window.djust.handleEvent('m', { n: 1 }); // leading edge, sends
        expect(sent).toHaveLength(1);
        expect(clock.pending()).toBe(0);

        for (let i = 2; i <= 6; i++) {
            await window.djust.handleEvent('m', { n: i });
            clock.advance(50);
        }
        expect(clock.pending()).toBe(1); // one trailing timer, not five

        clock.advance(1000);
        expect(sent).toHaveLength(2);
        expect(sent[1].params.n).toBe(6);
        expect(clock.pending()).toBe(0);
    });

    it('9. leading edge fires immediately; trailing carries the last payload', async () => {
        const { window, sent, clock } = createHarness({
            move: { throttle: { interval: 0.5, leading: true, trailing: true } },
        });
        await window.djust.handleEvent('move', { n: 1 });
        expect(sent).toHaveLength(1);
        expect(sent[0].params.n).toBe(1);

        await window.djust.handleEvent('move', { n: 2 });
        await window.djust.handleEvent('move', { n: 3 });
        expect(sent).toHaveLength(1); // still capped

        clock.advance(500);
        expect(sent).toHaveLength(2);
        expect(sent[1].params.n).toBe(3);
    });

    it('10. trailing=False drops everything after the leading edge', async () => {
        const { window, sent, clock } = createHarness({
            on_resize: { throttle: { interval: 1.0, leading: true, trailing: false } },
        });
        await window.djust.handleEvent('on_resize', { w: 1 });
        await window.djust.handleEvent('on_resize', { w: 2 });
        await window.djust.handleEvent('on_resize', { w: 3 });
        expect(sent).toHaveLength(1);
        clock.advance(5000);
        expect(sent).toHaveLength(1); // nothing trailing
        // A new window opens once the interval has passed.
        await window.djust.handleEvent('on_resize', { w: 4 });
        expect(sent).toHaveLength(2);
        expect(sent[1].params.w).toBe(4);
    });

    it('11. leading=False suppresses the immediate send and only sends trailing', async () => {
        const { window, sent, clock } = createHarness({
            tick: { throttle: { interval: 0.4, leading: false, trailing: true } },
        });
        await window.djust.handleEvent('tick', { n: 1 });
        expect(sent).toHaveLength(0); // no leading send
        await window.djust.handleEvent('tick', { n: 2 });
        expect(sent).toHaveLength(0);
        clock.advance(400);
        expect(sent).toHaveLength(1);
        expect(sent[0].params.n).toBe(2);
    });

    it('12. @debounce wins when a handler carries both', async () => {
        const { window, sent, clock } = createHarness({
            both: {
                debounce: { wait: 0.5, max_wait: null },
                throttle: { interval: 0.1, leading: true, trailing: true },
            },
        });
        await window.djust.handleEvent('both', { n: 1 });
        // A throttle leading edge would have sent immediately; debounce does not.
        expect(sent).toHaveLength(0);
        clock.advance(500);
        expect(sent).toHaveLength(1);
    });
});

describe('#2656 — mount-frame transport', () => {
    it('15. a mount frame carrying handler_config installs the gate', async () => {
        // The CSP-strict transport: no inline <script>, config rides the
        // mount frame like `cache_config` does. This drives the REAL socket
        // message handler rather than calling setHandlerConfig directly.
        const { window, sent, clock } = createHarness(null);
        const ws = window.djust.liveViewInstance;
        expect(ws && ws.ws && typeof ws.ws.onmessage).toBe('function');

        ws.ws.onmessage({
            data: JSON.stringify({
                type: 'mount',
                version: 1,
                handler_config: { search: { debounce: { wait: 0.5, max_wait: null } } },
            }),
        });
        // `handleMessage` chains `_handleMessageImpl` onto `_inflight`
        // (#1098), so the frame is processed asynchronously.
        await ws._inflight;

        await window.djust.handleEvent('search', { query: 'a' });
        await window.djust.handleEvent('search', { query: 'b' });
        expect(sent).toHaveLength(0); // the mount frame's config took effect
        clock.advance(500);
        expect(sent).toHaveLength(1);
        expect(sent[0].params.query).toBe('b');
    });
});

describe('#2656 — flush and teardown', () => {
    it('13. flushHandlerRateLimit sends the pending debounced payload at once', async () => {
        const { window, sent, clock } = createHarness({
            search: { debounce: { wait: 5.0, max_wait: null } },
        });
        // Several keystrokes then submit: the flush must carry the LAST one,
        // the same last-payload-wins rule the timer path obeys.
        await window.djust.handleEvent('search', { query: 'ty' });
        await window.djust.handleEvent('search', { query: 'typed' });
        await window.djust.handleEvent('search', { query: 'typed-then-submitted' });
        expect(sent).toHaveLength(0);

        window.djust.flushHandlerRateLimit();

        expect(sent).toHaveLength(1);
        expect(sent[0].params.query).toBe('typed-then-submitted');
        // The cancelled timer must not fire a second copy later.
        clock.advance(10_000);
        expect(sent).toHaveLength(1);
    });

    it('14. flush drains a pending throttle trailing payload too', async () => {
        const { window, sent, clock } = createHarness({
            move: { throttle: { interval: 2.0, leading: true, trailing: true } },
        });
        await window.djust.handleEvent('move', { n: 1 }); // leading
        await window.djust.handleEvent('move', { n: 2 }); // parked as trailing
        expect(sent).toHaveLength(1);

        window.djust.flushHandlerRateLimit();

        expect(sent).toHaveLength(2);
        expect(sent[1].params.n).toBe(2);
        clock.advance(10_000);
        expect(sent).toHaveLength(2);
    });
});
