/**
 * Tests for the djust:exec auto-executor (src/27-exec-listener.js).
 *
 * The auto-executor is the client-side half of ADR-002 Phase 1a. When the
 * server calls `self.push_commands(chain)`, the chain is sent as a djust:exec
 * push event. The client dispatches that as a global `djust:push_event`
 * CustomEvent on `window` (see src/03-websocket.js case 'push_event'). The
 * auto-executor listens for those CustomEvents, filters for `event === 'djust:exec'`,
 * and runs the payload's ops via `window.djust.js._executeOps`.
 *
 * These tests simulate the CustomEvent directly instead of going through the
 * WebSocket message path, which is the same approach other push-event tests
 * in this repo use.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root dj-view="test.View">
                ${bodyHtml}
            </div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true }
    );
    const { window } = dom;

    // Suppress console
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };

    try {
        window.eval(clientCode);
    } catch (_) {
        // client.js may throw on missing DOM APIs; harmless for these tests
    }

    return { window, document: dom.window.document };
}

function fireDjustExec(window, ops) {
    const event = new window.CustomEvent('djust:push_event', {
        detail: { event: 'djust:exec', payload: { ops } },
    });
    window.dispatchEvent(event);
}

describe('djust:exec auto-executor registration', () => {
    it('registers a window listener for djust:push_event', () => {
        const { window } = createEnv();
        // The listener should be attached. We can verify indirectly by
        // firing an event and observing that djust.js._executeOps gets called.
        expect(typeof window.djust._execListener).toBe('object');
        expect(typeof window.djust._execListener.handleDjustExec).toBe('function');
    });
});

describe('djust:exec executes JS Command chains', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('runs a single show op on a matching element', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display:none">Modal</div>'
        );

        fireDjustExec(window, [['show', { to: '#modal' }]]);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('modal').style.display).toBe('');
    });

    it('runs a multi-op chain in order', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display:none"></div><div id="overlay"></div>'
        );

        fireDjustExec(window, [
            ['show', { to: '#modal' }],
            ['add_class', { to: '#overlay', names: 'active' }],
        ]);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('modal').style.display).toBe('');
        expect(document.getElementById('overlay').classList.contains('active')).toBe(true);
    });

    it('runs add_class with multiple classes', async () => {
        const { window, document } = createEnv('<div id="target"></div>');

        fireDjustExec(window, [['add_class', { to: '#target', names: 'a b c' }]]);

        await new Promise((r) => setTimeout(r, 10));
        const cl = document.getElementById('target').classList;
        expect(cl.contains('a')).toBe(true);
        expect(cl.contains('b')).toBe(true);
        expect(cl.contains('c')).toBe(true);
    });

    it('runs focus op', async () => {
        const { window, document } = createEnv('<input id="name">');

        fireDjustExec(window, [['focus', { to: '#name' }]]);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.activeElement).toBe(document.getElementById('name'));
    });

    it('runs dispatch op with detail', async () => {
        const { window, document } = createEnv('<div id="target"></div>');
        let captured = null;
        document.getElementById('target').addEventListener('tour:step', (e) => {
            captured = e.detail;
        });

        fireDjustExec(window, [
            ['dispatch', { to: '#target', event: 'tour:step', detail: { step: 3 }, bubbles: true }],
        ]);

        await new Promise((r) => setTimeout(r, 10));
        expect(captured).toEqual({ step: 3 });
    });
});

describe('djust:exec payload validation', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('ignores djust:push_event for other event names', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display:none"></div>'
        );

        // Fire an unrelated push event — should NOT run any ops
        const unrelated = new window.CustomEvent('djust:push_event', {
            detail: {
                event: 'flash',
                payload: { message: 'saved', type: 'success' },
            },
        });
        window.dispatchEvent(unrelated);

        await new Promise((r) => setTimeout(r, 10));
        // The modal should still be hidden because no show op ran
        expect(document.getElementById('modal').style.display).toBe('none');
    });

    it('ignores payload without an ops array', async () => {
        const { window, document } = createEnv('<div id="target"></div>');

        const malformed = new window.CustomEvent('djust:push_event', {
            detail: { event: 'djust:exec', payload: { notOps: [] } },
        });
        window.dispatchEvent(malformed);

        await new Promise((r) => setTimeout(r, 10));
        // Nothing should have happened
        expect(document.getElementById('target').classList.length).toBe(0);
    });

    it('ignores payload with ops set to non-array value', async () => {
        const { window, document } = createEnv('<div id="target"></div>');

        const malformed = new window.CustomEvent('djust:push_event', {
            detail: { event: 'djust:exec', payload: { ops: 'not an array' } },
        });
        window.dispatchEvent(malformed);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('target').classList.length).toBe(0);
    });

    it('ignores event with missing detail', async () => {
        const { window, document } = createEnv('<div id="target"></div>');

        // Dispatch an event with no detail at all
        const bare = new window.CustomEvent('djust:push_event');
        window.dispatchEvent(bare);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('target').classList.length).toBe(0);
    });
});

describe('djust:exec error resilience', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('swallows errors from a single bad op and continues the chain', async () => {
        const { window, document } = createEnv(
            '<div id="target"></div><div id="also"></div>'
        );

        // First op references an invalid selector that should fail silently;
        // second op should still run on #also.
        fireDjustExec(window, [
            ['add_class', { to: '<<invalid>>', names: 'first' }],
            ['add_class', { to: '#also', names: 'second' }],
        ]);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('also').classList.contains('second')).toBe(true);
    });

    it('multiple djust:push_event fires all execute independently', async () => {
        const { window, document } = createEnv(
            '<div id="a"></div><div id="b"></div>'
        );

        fireDjustExec(window, [['add_class', { to: '#a', names: 'first' }]]);
        fireDjustExec(window, [['add_class', { to: '#b', names: 'second' }]]);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('a').classList.contains('first')).toBe(true);
        expect(document.getElementById('b').classList.contains('second')).toBe(true);
    });
});

describe('integration with JS Commands chain factory', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('executes chains built via the fluent window.djust.js factory', async () => {
        const { window, document } = createEnv(
            '<div id="modal" style="display:none"></div><div id="overlay"></div>'
        );

        // Build a chain via the client-side fluent API, extract its ops,
        // and fire them as a djust:exec push event. Proves that server-built
        // chains (which use the same ops shape) will execute correctly.
        const chain = window.djust.js
            .show('#modal')
            .addClass('active', { to: '#overlay' });
        const ops = JSON.parse(chain.toString());

        fireDjustExec(window, ops);

        await new Promise((r) => setTimeout(r, 10));
        expect(document.getElementById('modal').style.display).toBe('');
        expect(document.getElementById('overlay').classList.contains('active')).toBe(true);
    });
});
