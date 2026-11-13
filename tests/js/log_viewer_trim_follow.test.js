/**
 * LogViewer keeps following a stream whose old rows are trimmed (max_lines).
 *
 * Trimming shrinks the content and the browser may clamp scrollTop to match, a
 * decrease nobody asked for. Its scroll event arrives a frame late, after more
 * rows were appended, so it used to read as the reader scrolling up: the log
 * stopped following from the third burst on and never resumed.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const script = fs.readFileSync('./python/djust/components/static/djust_components/log-viewer.js', 'utf-8');

const MARKUP =
    '<div class="dj-log-viewer" dj-hook="LogViewer" data-stream-event="new_logs" data-auto-scroll="true" data-line-numbers="true" data-max-lines="4" role="log" aria-live="polite">' +
    '<div class="dj-log-viewer__body"></div></div>';

function boot() {
    const dom = new JSDOM(`<!DOCTYPE html><body><div dj-root>${MARKUP}</div></body>`, {
        url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true,
    });
    const { window } = dom;
    window.console = { log() {}, error() {}, debug() {}, info() {}, warn() {} };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    try { window.eval(clientCode); } catch (_e) { /* client.js may throw on DOM APIs jsdom lacks */ }
    const body = window.document.querySelector('.dj-log-viewer__body');
    // Layout model: a row whose text holds T<n> is tall (200 px), any other 50 px.
    // Reading the height lays out, which clamps scrollTop to the new maximum
    // like a browser does.
    const st = { top: 0, reads: 0 };
    const height = () => Array.from(body.children).reduce((n, r) => n + (/T\d/.test(r.textContent) ? 200 : 50), 0);
    const clamp = () => { st.top = Math.min(st.top, Math.max(0, height() - 100)); };
    Object.defineProperty(body, 'scrollHeight', { get: () => { st.reads += 1; clamp(); return height(); }, configurable: true });
    Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
    Object.defineProperty(body, 'scrollTop', { get: () => { clamp(); return st.top; }, set: (v) => { st.top = v; }, configurable: true });
    window.eval(script);
    window.djust.mountHooks();
    const push = (lines) => window.djust.dispatchPushEventToHooks('new_logs', { lines });
    const bottom = () => Math.max(0, height() - 100);
    const scroll = (top) => {
        if (top < body.scrollTop) body.dispatchEvent(new window.Event('wheel', { bubbles: true })); // the reader moves up
        st.top = top; body.dispatchEvent(new window.Event('scroll'));
    };
    return { window, body, st, push, bottom, scroll, top: () => body.scrollTop };
}

const frame = (window) => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));

describe('LogViewer with max_lines', () => {
    it('keeps following across many trimmed bursts whose clamping scroll events arrive late', async () => {
        const { window, body, push, bottom, scroll, top } = boot();
        push(['T1', 'T2', 'T3', 'T4']); // four tall rows
        await frame(window);
        scroll(bottom());
        for (let round = 0; round < 6; round++) {
            push(['a', 'b', 'c', 'd', 'e', 'f']); // trims the tall rows: the browser clamps scrollTop at the next layout
            await frame(window);
            push(['T5', 'T6']); // more (tall) rows arrive before the clamp's scroll event is delivered
            body.dispatchEvent(new window.Event('scroll'));
            await frame(window);
            expect(top()).toBe(bottom());
        }
    });

    it('a reader who scrolls up during a trimmed stream (appends every frame) stays up, and resuming at the bottom follows again', async () => {
        const { window, st, push, bottom, scroll, top } = boot();
        push(['T1', 'T2', 'T3', 'T4']);
        await frame(window);
        scroll(bottom());
        st.reads = 0;
        for (let i = 0; i < 20; i++) push(['a' + i, 'b' + i]); // trimming on every append...
        expect(st.reads).toBe(0); // ...without measuring per append
        scroll(0); // the reader scrolls up before the frame
        await frame(window);
        expect(top()).toBe(0);
        for (let round = 0; round < 5; round++) {
            push(['x' + round, 'y' + round, 'z' + round]); // still trimming
            await frame(window);
            expect(top()).toBe(0);
        }
        scroll(bottom());
        push(['T9']);
        await frame(window);
        expect(top()).toBe(bottom());
    });

    it('the same, when the clamp\'s own scroll event has been delivered first', async () => {
        const { window, push, bottom, scroll, top } = boot();
        push(['T1', 'T2', 'T3', 'T4']);
        await frame(window);
        scroll(bottom());
        push(['a', 'b', 'c']);
        await frame(window);
        scroll(top()); // the clamp's (late) scroll event
        scroll(10); // then the reader
        push(['d']);
        await frame(window);
        expect(top()).toBe(10);
    });

    it('an append that trims nothing leaves the reader\'s scroll-up detection alone', async () => {
        const { window, push, bottom, scroll, top } = boot();
        push(['T1', 'T2']);
        await frame(window);
        scroll(bottom());
        push(['b']); // 3 rows: below max_lines
        scroll(0); // the reader scrolls up before the frame
        await frame(window);
        expect(top()).toBe(0);
    });
});

describe('LogViewer: only the reader leaves the bottom (input, not the drop alone)', () => {
    const drop = (body, st, window, top) => { st.top = top; body.dispatchEvent(new window.Event('scroll')); }; // no input
    const input = (window, el, type) => el.dispatchEvent(new window.Event(type, { bubbles: true }));
    const follows = async (window, push, top, bottom) => {
        push(['T7']);
        await frame(window);
        return top() === bottom();
    };

    it('a drop with no reader input (a browser clamp) does not unpin', async () => {
        const { window, body, st, push, bottom, top, scroll } = boot();
        push(['T1', 'T2', 'T3', 'T4']);
        await frame(window);
        scroll(bottom());
        drop(body, st, window, 100);
        expect(await follows(window, push, top, bottom)).toBe(true);
    });

    for (const type of ['wheel', 'touchmove', 'keydown']) {
        it(`a ${type} on the log, then a drop, unpins`, async () => {
            const { window, body, st, push, bottom, top, scroll } = boot();
            push(['T1', 'T2', 'T3', 'T4']);
            await frame(window);
            scroll(bottom());
            input(window, body, type);
            drop(body, st, window, 100);
            expect(await follows(window, push, top, bottom)).toBe(false);
            expect(top()).toBe(100);
        });
    }

    const stamped = (window, type, ts) => {
        const e = new window.Event(type, { bubbles: true });
        Object.defineProperty(e, 'timeStamp', { value: ts });
        return e;
    };
    const dropAt = (body, st, window, top, ts) => { st.top = top; body.dispatchEvent(stamped(window, 'scroll', ts)); };

    it('a drop more than 600 ms after the last input does not unpin', async () => {
        const { window, body, st, push, bottom, top, scroll } = boot();
        push(['T1', 'T2', 'T3', 'T4']);
        await frame(window);
        scroll(bottom());
        body.dispatchEvent(stamped(window, 'wheel', 1000));
        await frame(window);
        dropAt(body, st, window, 100, 1700);
        expect(await follows(window, push, top, bottom)).toBe(true);
    });


    it('a main-thread stall between the input and the scroll handlers does not separate them (event times, not the clock at handling)', async () => {
        const { window, body, st, push, bottom, top, scroll } = boot();
        const t = { now: 1000 };
        window.Date.now = () => t.now;
        push(['T1', 'T2', 'T3', 'T4']);
        await frame(window);
        scroll(bottom());
        body.dispatchEvent(stamped(window, 'wheel', 1000));
        await frame(window); // a frame has gone by: only the events' times can tie the two together
        t.now += 5000; // the page was busy for 5 s before the scroll event was handled
        dropAt(body, st, window, 100, 1030); // but the scroll happened 30 ms after the wheel
        expect(await follows(window, push, top, bottom)).toBe(false);
    });

    it('a held pointer (a scrollbar drag) counts however long it is held', async () => {
        const { window, body, st, push, bottom, top, scroll } = boot();
        push(['T1', 'T2', 'T3', 'T4']);
        await frame(window);
        scroll(bottom());
        body.dispatchEvent(stamped(window, 'pointerdown', 1000));
        dropAt(body, st, window, 100, 6000);
        expect(await follows(window, push, top, bottom)).toBe(false);
    });

    it('destroying the hook removes every listener it added (element and document)', () => {
        const { window, body } = boot();
        // boot() mounted already; count by wrapping before a second mount is not possible,
        // so check behaviourally on the real registry: listeners added after tracking, removed on destroy
        const doc = window.document;
        const root = doc.querySelector('.dj-log-viewer');
        const live = [];
        const wrap = (target) => {
            const add = target.addEventListener.bind(target);
            const remove = target.removeEventListener.bind(target);
            target.addEventListener = (type, fn, opts) => { live.push({ target, type, fn, cap: !!(opts === true || (opts && opts.capture)) }); return add(type, fn, opts); };
            target.removeEventListener = (type, fn, opts) => {
                const cap = !!(opts === true || (opts && opts.capture));
                const i = live.findIndex((l) => l.target === target && l.type === type && l.fn === fn && l.cap === cap);
                if (i >= 0) live.splice(i, 1);
                return remove(type, fn, opts);
            };
        };
        wrap(doc);
        wrap(root);
        window.djust.destroyAllHooks();
        window.djust.mountHooks(); // adds its listeners through the wrapped targets
        expect(live.some((l) => l.target === doc && l.type === 'pointerup')).toBe(true);
        expect(live.some((l) => l.target === root && l.type === 'wheel')).toBe(true);
        window.djust.destroyAllHooks();
        expect(live.map((l) => `${l.target === doc ? 'document' : 'root'}:${l.type}`)).toEqual([]);
    });
});
