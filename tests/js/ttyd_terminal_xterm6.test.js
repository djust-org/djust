/**
 * The ttyd hook against the vendored xterm bundle (ADR-040, #3147).
 *
 * Loads the real committed ``xterm.mjs`` (xterm 6 + addon-fit 0.11) through the
 * hook's own ``import(el.dataset.xtermSrc)`` and drives the hook's init path
 * with a fake ttyd WebSocket: ctor options, ``loadAddon``, ``open``, fit,
 * ``write`` (stdout frames), ``onData`` (stdin frames), resize frames and
 * ``dispose``. It does not run a PTY; there is no ttyd binary here.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const XTERM_SRC = pathToFileURL(
    path.resolve('python/djust/components/static/djust_components/vendor/xterm/xterm.mjs'),
).href;
const HOOK_SRC = pathToFileURL(
    path.resolve('python/djust/components/static/djust_components/ttyd/ttyd_terminal.js'),
).href;

class FakeWebSocket {
    static OPEN = 1;
    static instances = [];
    constructor(url) {
        this.url = url;
        this.readyState = 0;
        this.sent = [];
        this._listeners = {};
        FakeWebSocket.instances.push(this);
    }
    addEventListener(type, fn) {
        (this._listeners[type] ||= []).push(fn);
    }
    send(buf) {
        this.sent.push(new Uint8Array(buf));
    }
    close() {
        this.readyState = 3;
    }
    emit(type, evt = {}) {
        (this._listeners[type] || []).forEach((fn) => fn(evt));
    }
}

async function mountHook() {
    const { TtydTerminalHook } = await import(/* @vite-ignore */ HOOK_SRC);
    const el = document.createElement('div');
    el.dataset.ttydUrl = 'ws://localhost:7681';
    el.dataset.rows = '10';
    el.dataset.cols = '40';
    el.dataset.theme = JSON.stringify({ background: '#1e1e2e' });
    el.dataset.xtermSrc = XTERM_SRC;
    document.body.appendChild(el);
    const hook = Object.create(TtydTerminalHook);
    hook.el = el;
    hook.pushEvent = vi.fn();
    await hook.mounted();
    return { hook, el };
}

describe('ttyd hook on the vendored xterm 6 bundle (#3147)', () => {
    beforeEach(() => {
        FakeWebSocket.instances = [];
        vi.stubGlobal('WebSocket', FakeWebSocket);
        globalThis.ResizeObserver = class {
            observe() {}
            disconnect() {}
        };
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        document.body.innerHTML = '';
    });

    it('exports Terminal and FitAddon from the committed bundle', async () => {
        const mod = await import(/* @vite-ignore */ XTERM_SRC);
        expect(typeof mod.Terminal).toBe('function');
        expect(typeof mod.FitAddon).toBe('function');
    });

    it('opens the terminal with the data-attribute options and loads the fit addon', async () => {
        const { hook, el } = await mountHook();
        expect(hook._term.options.convertEol).toBe(true);
        expect(hook._term.options.theme.background).toBe('#1e1e2e');
        expect(typeof hook._fit.fit).toBe('function');
        expect(el.querySelector('.xterm')).not.toBeNull();
        hook.destroyed();
    });

    it('writes ttyd stdout frames into the terminal buffer', async () => {
        const { hook } = await mountHook();
        const ws = FakeWebSocket.instances[0];
        const text = new TextEncoder().encode('hello');
        const frame = new Uint8Array(1 + text.length);
        frame[0] = 0;
        frame.set(text, 1);
        ws.emit('message', { data: frame.buffer });
        await new Promise((resolve) => hook._term.write('', resolve));
        const line = hook._term.buffer.active.getLine(0).translateToString(true);
        expect(line).toBe('hello');
        hook.destroyed();
    });

    it('sends a resize frame on open and a stdin frame for onData', async () => {
        const { hook } = await mountHook();
        const ws = FakeWebSocket.instances[0];
        ws.readyState = FakeWebSocket.OPEN;
        ws.emit('open');
        const resize = ws.sent[0];
        expect(resize[0]).toBe(1);
        const payload = JSON.parse(new TextDecoder().decode(resize.slice(1)));
        expect(typeof payload.columns).toBe('number');
        expect(typeof payload.rows).toBe('number');
        expect(hook.pushEvent).toHaveBeenCalledWith('ttyd_connect', expect.any(Object));

        hook._term.input('ls\r');
        const stdin = ws.sent[ws.sent.length - 1];
        expect(stdin[0]).toBe(0);
        expect(new TextDecoder().decode(stdin.slice(1))).toBe('ls\r');
        hook.destroyed();
    });

    it('disposes the terminal and closes the socket on destroy', async () => {
        const { hook, el } = await mountHook();
        const ws = FakeWebSocket.instances[0];
        hook.destroyed();
        expect(ws.readyState).toBe(3);
        expect(el.querySelector('.xterm')).toBeNull();
    });
});
