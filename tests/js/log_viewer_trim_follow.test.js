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
    const st = { height: 1000, top: 900 };
    Object.defineProperty(body, 'scrollHeight', { get: () => st.height, configurable: true });
    Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
    Object.defineProperty(body, 'scrollTop', { get: () => st.top, set: (v) => { st.top = v; }, configurable: true });
    window.eval(script);
    window.djust.mountHooks();
    return { window, body, st };
}

const frame = (window) => new Promise((resolve) => window.requestAnimationFrame(() => resolve()));

describe('LogViewer with max_lines', () => {
    it('keeps following across many trimmed bursts whose clamping scroll events arrive late', async () => {
        const { window, body, st } = boot();
        body.scrollTop = 900;
        body.dispatchEvent(new window.Event('scroll'));
        for (let burst = 0; burst < 6; burst++) {
            window.djust.dispatchPushEventToHooks('new_logs', { lines: ['a', 'b', 'c', 'd', 'e', 'f'] });
            st.top -= 40; // the clamp for the shorter content
            st.height += 300; // more rows arrived before its scroll event
            body.dispatchEvent(new window.Event('scroll'));
            await frame(window);
            expect(st.top).toBe(st.height);
        }
    });

    it('a reader who really scrolled up still stays up', async () => {
        const { window, body, st } = boot();
        body.scrollTop = 900;
        body.dispatchEvent(new window.Event('scroll'));
        window.djust.dispatchPushEventToHooks('new_logs', { lines: ['a', 'b', 'c', 'd', 'e', 'f'] });
        await frame(window);
        // the first scroll event after a trim is only a baseline; a later one is the reader
        body.dispatchEvent(new window.Event('scroll'));
        st.top = 200;
        body.dispatchEvent(new window.Event('scroll'));
        window.djust.dispatchPushEventToHooks('new_logs', { lines: ['g'] });
        await frame(window);
        expect(st.top).toBe(200);
    });

    it('an append that trims nothing leaves the reader\'s scroll-up detection alone', async () => {
        const { window, body, st } = boot();
        body.scrollTop = 900;
        body.dispatchEvent(new window.Event('scroll'));
        window.djust.dispatchPushEventToHooks('new_logs', { line: 'a' }); // 1 row: below max_lines
        st.top = 200; // the reader scrolls up before the frame
        body.dispatchEvent(new window.Event('scroll'));
        await frame(window);
        expect(st.top).toBe(200);
    });
});
