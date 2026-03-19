/**
 * Tests for dj-debounce / dj-throttle HTML attributes on dj-* event elements
 *
 * These are the dj- prefixed equivalents of data-debounce / data-throttle,
 * providing consistent naming within the djust attribute namespace.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

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

function getFetchCalls(dom) {
    return dom.window._testFetchCalls;
}

describe('dj-debounce on dj-click', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('delays handler by specified ms', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <button dj-click="do_something" dj-debounce="300">Click</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('[dj-click]');
        btn.click();

        // Should not fire immediately
        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(0);

        // Should fire after 300ms
        await vi.advanceTimersByTimeAsync(200);
        expect(getFetchCalls(dom).length).toBe(1);
        expect(getFetchCalls(dom)[0].eventName).toBe('do_something');
    });

    it('resets timer on rapid clicks', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <button dj-click="do_something" dj-debounce="300">Click</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('[dj-click]');
        btn.click();
        await vi.advanceTimersByTimeAsync(200);
        btn.click(); // Reset timer

        await vi.advanceTimersByTimeAsync(200);
        expect(getFetchCalls(dom).length).toBe(0); // Still waiting

        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(1); // Fired once after second debounce
    });
});

describe('dj-throttle on dj-click', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('limits to one call per throttle window', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <button dj-click="do_something" dj-throttle="500">Click</button>
            </div>
        `);
        initClient(dom);

        const btn = dom.window.document.querySelector('[dj-click]');

        // First click fires immediately
        btn.click();
        await vi.advanceTimersByTimeAsync(0);
        expect(getFetchCalls(dom).length).toBe(1);

        // Second click within window is dropped
        btn.click();
        await vi.advanceTimersByTimeAsync(0);
        expect(getFetchCalls(dom).length).toBe(1);

        // After throttle window, next click goes through
        await vi.advanceTimersByTimeAsync(500);
        btn.click();
        await vi.advanceTimersByTimeAsync(0);
        expect(getFetchCalls(dom).length).toBe(2);
    });
});

describe('dj-debounce on dj-change', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('debounces change events', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <select dj-change="filter" dj-debounce="200">
                    <option value="a">A</option>
                    <option value="b">B</option>
                </select>
            </div>
        `);
        initClient(dom);

        const select = dom.window.document.querySelector('[dj-change]');
        select.value = 'b';
        select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));

        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(0);

        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(1);
    });
});

describe('dj-debounce precedence over data-debounce', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('dj-debounce takes precedence over data-debounce on dj-input', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input dj-input="search" dj-debounce="100" data-debounce="500" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('[dj-input]');
        input.value = 'test';
        input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // Should use dj-debounce=100, not data-debounce=500
        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(1);
    });
});

describe('dj-debounce="0" disables default debounce on dj-input', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('fires immediately with dj-debounce="0"', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input dj-input="search" dj-debounce="0" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('[dj-input]');
        input.value = 'test';
        input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        // With debounce=0, should fire ~immediately
        await vi.advanceTimersByTimeAsync(0);
        expect(getFetchCalls(dom).length).toBe(1);
    });
});

describe('dj-debounce="blur"', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('defers event until element loses focus', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input dj-input="search" dj-debounce="blur" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('[dj-input]');
        input.value = 'test';
        input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        await vi.advanceTimersByTimeAsync(1000);
        // Should NOT fire — waiting for blur
        expect(getFetchCalls(dom).length).toBe(0);

        // Trigger blur
        input.dispatchEvent(new dom.window.Event('blur', { bubbles: true }));
        await vi.advanceTimersByTimeAsync(0);
        expect(getFetchCalls(dom).length).toBe(1);
    });
});

describe('independent per-element debounce', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('each element gets its own debounce timer', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <button id="a" dj-click="action_a" dj-debounce="200">A</button>
                <button id="b" dj-click="action_b" dj-debounce="200">B</button>
            </div>
        `);
        initClient(dom);

        const btnA = dom.window.document.querySelector('#a');
        const btnB = dom.window.document.querySelector('#b');

        btnA.click();
        await vi.advanceTimersByTimeAsync(100);
        btnB.click();

        // After 200ms from A's click, A should fire
        await vi.advanceTimersByTimeAsync(100);
        const calls = getFetchCalls(dom);
        expect(calls.length).toBe(1);
        expect(calls[0].eventName).toBe('action_a');

        // After 200ms from B's click, B should fire
        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(2);
        expect(getFetchCalls(dom)[1].eventName).toBe('action_b');
    });
});

describe('backward compatibility: data-debounce still works', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
    });

    it('data-debounce works on dj-input when dj-debounce is absent', async () => {
        const dom = createTestEnv(`
            <div dj-root dj-view="test.View">
                <input dj-input="search" data-debounce="200" />
            </div>
        `);
        initClient(dom);

        const input = dom.window.document.querySelector('[dj-input]');
        input.value = 'test';
        input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));

        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(0);

        await vi.advanceTimersByTimeAsync(100);
        expect(getFetchCalls(dom).length).toBe(1);
    });
});
