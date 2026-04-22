/**
 * Tests for the v0.5.0 service-worker features:
 *   - Instant page shell (shell/main split)
 *   - WebSocket reconnection bridge (buffer + drain)
 *
 * The SW script itself (python/djust/static/djust/service-worker.js) is
 * evaluated in a mock worker environment that exposes the listeners it
 * registers via `self.addEventListener`. We then fire synthetic events at
 * those listeners to exercise the handlers.
 *
 * The client-side registration module (33-sw-registration.js) is exercised
 * via the bundled client.js inside a JSDOM window.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';
import vm from 'vm';

const SW_SRC = fs.readFileSync(
    './python/djust/static/djust/service-worker.js',
    'utf-8'
);
const CLIENT_SRC = fs.readFileSync(
    './python/djust/static/djust/client.js',
    'utf-8'
);

// ---------------------------------------------------------------------------
// SW harness — run the SW source in a sandbox with a mock `self`.
// ---------------------------------------------------------------------------

function loadSw() {
    const listeners = {};
    const sandbox = {
        self: {
            addEventListener(name, fn) {
                listeners[name] = fn;
            },
            skipWaiting: () => Promise.resolve(),
            clients: {
                claim: () => Promise.resolve(),
                matchAll: () => Promise.resolve([]),
            },
        },
        caches: {
            open: async () => ({
                match: async () => undefined,
                put: async () => undefined,
                delete: async () => undefined,
            }),
        },
        fetch: async () => new Response('', { status: 200 }),
        Response: global.Response || class {},
        Headers: global.Headers || class {},
        Request: global.Request || class {},
        module: { exports: {} },
        setTimeout,
        clearTimeout,
        console: { log: () => {}, warn: () => {}, error: () => {} },
    };
    vm.createContext(sandbox);
    vm.runInContext(SW_SRC, sandbox);
    return { listeners, exports: sandbox.module.exports, sandbox };
}

describe('service-worker: shell/main split', () => {
    it('splitShellAndMain extracts main inner HTML and replaces with placeholder', () => {
        const { exports } = loadSw();
        const { shell, main } = exports.splitShellAndMain(
            '<html><body><nav>N</nav><main class="x">INNER</main><footer>F</footer></body></html>'
        );
        expect(main).toBe('INNER');
        expect(shell).toContain('data-djust-shell-placeholder="1"');
        expect(shell).not.toContain('INNER');
        expect(shell).toContain('<nav>N</nav>');
        expect(shell).toContain('<footer>F</footer>');
    });

    it('splitShellAndMain returns nulls when no <main> tag', () => {
        const { exports } = loadSw();
        const { shell, main } = exports.splitShellAndMain(
            '<html><body><div>no main</div></body></html>'
        );
        expect(shell).toBeNull();
        expect(main).toBeNull();
    });
});

describe('service-worker: reconnection-bridge buffer', () => {
    function fireMessage(listeners, data, source) {
        const event = {
            data: data,
            source: source || { postMessage: vi.fn() },
        };
        listeners.message(event);
        return event;
    }

    it('DJUST_BUFFER appends per-connection messages', () => {
        const { listeners, exports } = loadSw();
        const src = { postMessage: vi.fn() };
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'c1', payload: 'msg-1' },
            src
        );
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'c1', payload: 'msg-2' },
            src
        );
        const buf = exports._internal.RECONNECT_BUFFER.get('c1');
        expect(buf).toEqual(['msg-1', 'msg-2']);
    });

    it('buffer is capped at BUFFER_CAP (50) — oldest dropped', () => {
        const { listeners, exports } = loadSw();
        const src = { postMessage: vi.fn() };
        for (let i = 0; i < 75; i++) {
            fireMessage(
                listeners,
                { type: 'DJUST_BUFFER', connectionId: 'cap', payload: 'm' + i },
                src
            );
        }
        const buf = exports._internal.RECONNECT_BUFFER.get('cap');
        expect(buf.length).toBe(50);
        expect(buf[0]).toBe('m25'); // first 25 dropped
        expect(buf[49]).toBe('m74');
    });

    it('DJUST_DRAIN posts buffered messages back and empties the buffer', () => {
        const { listeners, exports } = loadSw();
        const src = { postMessage: vi.fn() };
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'c2', payload: 'a' },
            src
        );
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'c2', payload: 'b' },
            src
        );
        fireMessage(listeners, { type: 'DJUST_DRAIN', connectionId: 'c2' }, src);
        expect(src.postMessage).toHaveBeenCalledWith({
            type: 'DJUST_DRAIN_REPLY',
            connectionId: 'c2',
            messages: ['a', 'b'],
        });
        expect(exports._internal.RECONNECT_BUFFER.has('c2')).toBe(false);
    });

    it('DJUST_DRAIN on empty connection returns empty messages array', () => {
        const { listeners } = loadSw();
        const src = { postMessage: vi.fn() };
        fireMessage(listeners, { type: 'DJUST_DRAIN', connectionId: 'empty' }, src);
        expect(src.postMessage).toHaveBeenCalledWith({
            type: 'DJUST_DRAIN_REPLY',
            connectionId: 'empty',
            messages: [],
        });
    });

    it('multi-connection buffers are isolated', () => {
        const { listeners, exports } = loadSw();
        const src = { postMessage: vi.fn() };
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'conn-A', payload: 'A1' },
            src
        );
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'conn-B', payload: 'B1' },
            src
        );
        fireMessage(
            listeners,
            { type: 'DJUST_BUFFER', connectionId: 'conn-A', payload: 'A2' },
            src
        );
        expect(exports._internal.RECONNECT_BUFFER.get('conn-A')).toEqual([
            'A1',
            'A2',
        ]);
        expect(exports._internal.RECONNECT_BUFFER.get('conn-B')).toEqual(['B1']);
    });

    it('unknown message types are ignored', () => {
        const { listeners, exports } = loadSw();
        const src = { postMessage: vi.fn() };
        fireMessage(listeners, { type: 'NOT_A_DJUST_MSG', foo: 'bar' }, src);
        fireMessage(listeners, null, src);
        fireMessage(listeners, { no_type: true }, src);
        expect(exports._internal.RECONNECT_BUFFER.size).toBe(0);
        expect(src.postMessage).not.toHaveBeenCalled();
    });
});

// ---------------------------------------------------------------------------
// Client-side registration module (33-sw-registration.js)
// ---------------------------------------------------------------------------

describe('client: djust.registerServiceWorker', () => {
    function createEnv() {
        const dom = new JSDOM(
            `<!DOCTYPE html><html><body><main data-djust-shell-placeholder="1"></main></body></html>`,
            {
                url: 'http://localhost:8000/',
                runScripts: 'dangerously',
                pretendToBeVisual: true,
            }
        );
        const { window } = dom;
        window.console = {
            log: () => {},
            warn: () => {},
            error: () => {},
            debug: () => {},
            info: () => {},
        };
        // Mock history to avoid JSDOM errors
        window.history.pushState = () => {};
        window.history.replaceState = () => {};
        try {
            window.eval(CLIENT_SRC);
        } catch (e) {
            /* some client modules touch APIs JSDOM lacks — ignore */
        }
        return { window };
    }

    it('exposes registerServiceWorker on window.djust', () => {
        const { window } = createEnv();
        expect(typeof window.djust.registerServiceWorker).toBe('function');
    });

    it('resolves to null and logs nothing when navigator.serviceWorker absent', async () => {
        const { window } = createEnv();
        // JSDOM has no navigator.serviceWorker by default — perfect.
        expect('serviceWorker' in window.navigator).toBe(false);
        const reg = await window.djust.registerServiceWorker({
            instantShell: true,
            reconnectionBridge: true,
        });
        expect(reg).toBeNull();
    });

    it('returns the cached promise on repeat calls (idempotency — #829)', async () => {
        const { window } = createEnv();

        // Stub navigator.serviceWorker so register() resolves.
        let registerCallCount = 0;
        const fakeRegistration = { scope: '/', active: null };
        Object.defineProperty(window.navigator, 'serviceWorker', {
            configurable: true,
            value: {
                register: async () => {
                    registerCallCount += 1;
                    return fakeRegistration;
                },
                addEventListener: () => {},
                controller: null,
            },
        });

        const first = await window.djust.registerServiceWorker({
            instantShell: false,
            reconnectionBridge: false,
        });
        const second = await window.djust.registerServiceWorker({
            instantShell: false,
            reconnectionBridge: false,
        });

        // navigator.serviceWorker.register() must have been called exactly once.
        expect(registerCallCount).toBe(1);
        // Both calls return the same cached registration.
        expect(first).toBe(fakeRegistration);
        expect(second).toBe(fakeRegistration);
    });
});
