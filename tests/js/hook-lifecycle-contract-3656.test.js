/**
 * #3656 — the dj-hook lifecycle contract, one describe block per rule of
 * "The lifecycle contract" in docs/website/guides/hooks.md.
 *
 * Updates go through the production paths: a WebSocket `patch` frame
 * (incremental VDOM patches) or `html_update` frame (full morph) handed to
 * LiveViewWebSocket.handleMessage, and the connection callbacks through the
 * transports' own onopen / onclose / onerror. Runs against the full bundle and,
 * in the `split-build` vitest project, against the core bundle plus chunks.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const VIEW = 'test.views.HookView';

function createEnv(inner) {
    const html = `<!DOCTYPE html><html><body><div dj-view="${VIEW}" dj-root>${inner}</div></body></html>`;
    const dom = new JSDOM(html, {
        url: 'http://localhost:8000/hooks/',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
    });
    const { window } = dom;
    const errors = [];
    window.console = {
        log() {}, info() {}, debug() {}, warn() {},
        error: (...args) => errors.push(args.map(String).join(' ')),
        groupCollapsed() {}, groupEnd() {},
    };
    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    const sockets = [];
    class MockWebSocket {
        constructor(url) {
            this.url = url;
            this.readyState = MockWebSocket.CONNECTING;
            this.onopen = this.onclose = this.onmessage = this.onerror = null;
            sockets.push(this);
        }
        send() {}
        close() { this.readyState = MockWebSocket.CLOSED; }
    }
    MockWebSocket.CONNECTING = 0;
    MockWebSocket.OPEN = 1;
    MockWebSocket.CLOSING = 2;
    MockWebSocket.CLOSED = 3;
    window.WebSocket = MockWebSocket;
    window.eval(clientCode);
    const log = [];
    return { window, document: window.document, djust: window.djust, log, errors, sockets };
}

/** A hook definition that records every callback as "<label>.<callback>". */
function recorder(log, extra = {}) {
    const def = {};
    for (const cb of ['mounted', 'beforeUpdate', 'updated', 'destroyed', 'disconnected', 'reconnected']) {
        def[cb] = function () {
            log.push(`${this.el.getAttribute('data-label') || this.el.id}.${cb}`);
        };
    }
    return Object.assign(def, extra);
}

/** A mounted WebSocket transport, as after the page's mount frame. */
async function mountedWS(env, inner) {
    const ws = new env.djust.LiveViewWebSocket();
    ws.enabled = true;
    ws.ws = new env.window.WebSocket();
    ws.ws.readyState = 1;
    ws.viewMounted = true;
    await ws.handleMessage({
        type: 'mount',
        view: VIEW,
        version: 1,
        html: inner,
    });
    return ws;
}

let version = 1;
function patchFrame(patches) {
    version += 1;
    return { type: 'patch', version, patches };
}
function htmlFrame(inner) {
    version += 1;
    return { type: 'html_update', version, html: `<div dj-view="${VIEW}" dj-root>${inner}</div>` };
}

async function setup(inner, hooks) {
    version = 1;
    const env = createEnv(inner);
    env.ws = await mountedWS(env, inner);
    // Registered once the mount frame has rendered, so every callback the
    // test sees comes from its own updates.
    env.djust.hooks = typeof hooks === 'function' ? hooks(env.log) : hooks;
    env.djust.mountHooks(env.document);
    env.log.length = 0;
    return env;
}

describe('#3656 rule 1: mounted() runs once, and an update that mounts a hook does not also update it', () => {
    it('mounts on the first sweep and never again for the same element', async () => {
        const inner = '<div id="a" dj-id="a" dj-hook="R"></div>';
        const env = createEnv(inner);
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        env.djust.mountHooks(env.document);
        env.djust.updateHooks(env.document);
        expect(env.log).toEqual(['a.mounted', 'a.updated']);
    });

    it('a hook nested in an inserted hook element gets mounted() only (no updated()) in that update', async () => {
        const env = await setup('<div id="host" dj-id="h"></div>', (log) => ({ R: recorder(log) }));
        await env.ws.handleMessage(htmlFrame(
            '<div id="host" dj-id="h"><div id="outer" dj-hook="R"><span id="inner" dj-hook="R"></span></div></div>',
        ));
        expect(env.log).toEqual(['outer.mounted', 'inner.mounted']);
    });

    it('a hook inserted inside an existing hook gets mounted() only; the existing one gets updated()', async () => {
        const env = await setup('<div id="outer" dj-hook="R"></div>', (log) => ({ R: recorder(log) }));
        await env.ws.handleMessage(htmlFrame('<div id="outer" dj-hook="R"><span id="inner" dj-hook="R"></span></div>'));
        expect(env.log).toEqual(['outer.beforeUpdate', 'outer.updated', 'inner.mounted']);
    });

    it('a mounted() that re-enters updateHooks() does not get a later new hook updated() in the same update', () => {
        const env = createEnv('<div id="host"></div>');
        env.djust.hooks = {
            First: recorder(env.log, {
                mounted() { env.log.push('first.mounted'); env.djust.updateHooks(); },
            }),
            R: recorder(env.log),
        };
        env.document.getElementById('host').innerHTML =
            '<i id="first" dj-hook="First"></i><i id="second" dj-hook="R"></i>';
        env.djust.updateHooks();
        expect(env.log).toEqual(['first.mounted', 'second.mounted']);
        env.log.length = 0;
        env.djust.updateHooks();
        expect(env.log).toEqual(['first.updated', 'second.updated']);
    });
});

describe('#3656 rule 2: an instance belongs to one element node', () => {
    it('an attribute patch keeps the instance and this.el; updated() reads the new value', async () => {
        const seen = [];
        const env = await setup(
            '<div id="c" dj-id="c" dj-hook="C" dj-hook-value-n="1"></div>',
            { C: { mounted() { this.mine = 'x'; }, updated() { seen.push([this.el, this.mine, this.values.n]); } } },
        );
        const el = env.document.getElementById('c');
        const before = env.djust.getHook(el);
        await env.ws.handleMessage(patchFrame([{ type: 'SetAttr', path: [0], d: 'c', key: 'dj-hook-value-n', value: '2' }]));
        expect(env.djust.getHook(el)).toBe(before);
        expect(seen).toEqual([[el, 'x', 2]]);
    });

    it('a keyed move keeps the instance and this.el', async () => {
        const env = await setup(
            '<ul dj-id="l"><li id="one" dj-id="1" dj-hook="R"></li><li id="two" dj-id="2" dj-hook="R"></li></ul>',
            (log) => ({ R: recorder(log) }),
        );
        const one = env.document.getElementById('one');
        const inst = env.djust.getHook(one);
        await env.ws.handleMessage(patchFrame([{ type: 'MoveChild', path: [0], d: 'l', child_d: '1', from: 0, to: 1 }]));
        const ids = Array.from(env.document.querySelectorAll('li')).map((li) => li.id);
        expect(ids).toEqual(['two', 'one']);
        expect(env.djust.getHook(one)).toBe(inst);
        expect(inst.el).toBe(one);
        expect(env.log.filter((l) => l.endsWith('.destroyed') || l.endsWith('.mounted'))).toEqual([]);
        expect(env.log).toContain('one.updated');
    });

    it('a replaced node gets a fresh instance: destroyed() on the old, mounted() on the new', async () => {
        const env = await setup('<div id="r" dj-id="r" data-label="old" dj-hook="R"></div>', (log) => ({ R: recorder(log) }));
        const old = env.document.getElementById('r');
        const oldInst = env.djust.getHook(old);
        await env.ws.handleMessage(patchFrame([{
            type: 'Replace', path: [0], d: 'r',
            node: { tag: 'section', attrs: { id: 'r', 'data-label': 'new', 'dj-hook': 'R' }, children: [] },
        }]));
        const now = env.document.getElementById('r');
        expect(now).not.toBe(old);
        expect(env.djust.getHook(old)).toBeNull();
        expect(env.djust.getHook(now)).not.toBe(oldInst);
        expect(env.djust.getHook(now).el).toBe(now);
        expect(env.log).toEqual(['old.beforeUpdate', 'old.destroyed', 'new.mounted']);
    });

    it('changing dj-hook on the same node destroys the old hook and mounts the new one', async () => {
        const env = await setup('<div id="x" dj-id="x" dj-hook="A"></div>', (log) => ({
            A: recorder(log, { destroyed() { log.push('A.destroyed'); } }),
            B: recorder(log, { mounted() { log.push('B.mounted'); } }),
        }));
        await env.ws.handleMessage(patchFrame([{ type: 'SetAttr', path: [0], d: 'x', key: 'dj-hook', value: 'B' }]));
        expect(env.log).toEqual(['x.beforeUpdate', 'A.destroyed', 'B.mounted']);
    });

    it('removing dj-hook from the node destroys the hook', async () => {
        const env = await setup('<div id="x" dj-id="x" dj-hook="R"></div>', (log) => ({ R: recorder(log) }));
        // An incremental RemoveAttr never removes a dj-* attribute; the full
        // morph does.
        await env.ws.handleMessage(htmlFrame('<div id="x" dj-id="x"></div>'));
        expect(env.log).toEqual(['x.beforeUpdate', 'x.destroyed']);
        expect(env.djust.getHook('#x')).toBeNull();
    });
});

describe('#3656 rule 3: beforeUpdate() runs once per update, before any DOM change, inside the updated region', () => {
    it('sees the old DOM, once for a batch of several patches', async () => {
        const seen = [];
        const env = await setup(
            '<div id="c" dj-id="c" dj-hook="C" title="old"><b dj-id="t">x</b></div>',
            { C: { beforeUpdate() { seen.push(this.el.getAttribute('title')); } } },
        );
        await env.ws.handleMessage(patchFrame([
            { type: 'SetAttr', path: [0], d: 'c', key: 'title', value: 'new' },
            { type: 'SetAttr', path: [0, 0], d: 't', key: 'class', value: 'y' },
        ]));
        expect(seen).toEqual(['old']);
    });

    it('a scoped update (morph of one container) skips hooks outside it', () => {
        const env = createEnv('<div id="in-box"><i id="a" dj-hook="R"></i></div><i id="b" dj-hook="R"></i>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        env.log.length = 0;
        const box = env.document.getElementById('in-box');
        const desired = env.document.createElement('div');
        desired.innerHTML = '<i id="a" dj-hook="R" title="t"></i>';
        env.djust.morphChildren(box, desired);
        expect(env.log).toEqual(['a.beforeUpdate']);
    });
});

describe('#3656 rule 4: updated() runs after the DOM change, for every hook still in the document', () => {
    it('sees the new DOM and the rebound dj-* bindings; hooks outside the change are updated too', async () => {
        const seen = [];
        const env = await setup(
            '<div id="c" dj-id="c" dj-hook="C" title="old"></div><div id="o" dj-hook="C"></div>',
            { C: { updated() { seen.push([this.el.id, this.el.getAttribute('title')]); } } },
        );
        await env.ws.handleMessage(patchFrame([{ type: 'SetAttr', path: [0], d: 'c', key: 'title', value: 'new' }]));
        expect(seen).toEqual([['c', 'new'], ['o', null]]);
    });
});

describe('#3656 rules 5 and 6: destroyed() once; destroyed() before mounted()/updated(), then document order', () => {
    it('one update: removed hooks first, then kept and new hooks parents-first', async () => {
        const env = await setup(
            '<div id="p" dj-hook="R"><i id="gone" dj-hook="R"></i></div><div id="q" dj-hook="R"></div>',
            (log) => ({ R: recorder(log) }),
        );
        await env.ws.handleMessage(htmlFrame(
            '<div id="p" dj-hook="R"><span id="kid" dj-hook="R"></span></div><div id="q" dj-hook="R"></div>',
        ));
        expect(env.log).toEqual([
            'p.beforeUpdate', 'gone.beforeUpdate', 'q.beforeUpdate',
            'gone.destroyed',
            'p.updated', 'kid.mounted', 'q.updated',
        ]);
    });

    it('destroyed() runs exactly once, even when it re-enters updateHooks()', async () => {
        let count = 0;
        const env = await setup('<div id="x" dj-hook="D"></div>', {
            D: { destroyed() { count += 1; window.djust.updateHooks(); } },
        });
        const { window } = env;
        await env.ws.handleMessage(htmlFrame('<p>gone</p>'));
        env.djust.updateHooks();
        env.djust.destroyAllHooks();
        expect(count).toBe(1);
        expect(window.djust._activeHooks.size).toBe(0);
    });

    it('a dj-remove exit: destroyed() when the element is finally removed, not at the next update', async () => {
        const env = await setup(
            '<div dj-id="w"><div id="x" dj-id="x" dj-hook="R" dj-remove="fade-out"></div></div>',
            (log) => ({ R: recorder(log) }),
        );
        const el = env.document.getElementById('x');
        await env.ws.handleMessage(patchFrame([{ type: 'RemoveChild', path: [0], d: 'w', child_d: 'x', index: 0 }]));
        // Still playing its exit: still mounted.
        expect(el.isConnected).toBe(true);
        expect(env.log).not.toContain('x.destroyed');
        env.djust.djRemove.finishPendingRemovals(env.document.body);
        expect(el.isConnected).toBe(false);
        expect(env.log.filter((l) => l === 'x.destroyed')).toHaveLength(1);
        expect(env.djust.getHook(el)).toBeNull();
    });
    it('updateHooks(root) for one container leaves the hooks outside it alone', () => {
        const env = createEnv('<div id="box"><i id="a" dj-hook="R"></i></div><i id="b" dj-hook="R"></i>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        env.log.length = 0;
        env.djust.updateHooks(env.document.getElementById('box'));
        expect(env.log).toEqual(['a.updated']);
        expect(env.djust.getHook('#b')).not.toBeNull();
    });
});

describe('#3656 rule 7: dj-update="ignore" regions', () => {
    it('a hook inside an ignored region keeps its instance and node across a full morph', async () => {
        const env = await setup(
            '<div id="z" dj-update="ignore"><canvas id="cv" dj-hook="R"></canvas></div><p dj-id="t">1</p>',
            (log) => ({ R: recorder(log) }),
        );
        const cv = env.document.getElementById('cv');
        const inst = env.djust.getHook(cv);
        await env.ws.handleMessage(htmlFrame('<div id="z" dj-update="ignore"></div><p dj-id="t">2</p>'));
        expect(env.document.getElementById('cv')).toBe(cv);
        expect(env.djust.getHook(cv)).toBe(inst);
        expect(env.log).toEqual(['cv.beforeUpdate', 'cv.updated']);
    });
});

describe('#3656 rule 8: disconnected() / reconnected() pair per instance, over WebSocket and SSE', () => {
    it('WebSocket: one disconnected() for a drop and its failed retries, one reconnected() on open', () => {
        const env = createEnv('<div id="a" dj-hook="R"></div>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        const ws = new env.djust.LiveViewWebSocket();
        ws.enabled = true;
        ws.connect('ws://localhost:8000/ws/live/');
        const first = env.sockets[env.sockets.length - 1];
        first.readyState = 1;
        first.onopen();
        env.log.length = 0;
        first.onclose({ code: 1006 });
        // A failed retry closes again.
        ws.ws = null;
        ws.connect('ws://localhost:8000/ws/live/');
        const retry = env.sockets[env.sockets.length - 1];
        retry.onclose({ code: 1006 });
        expect(env.log).toEqual(['a.disconnected']);
        ws.ws = null;
        ws.connect('ws://localhost:8000/ws/live/');
        const back = env.sockets[env.sockets.length - 1];
        back.readyState = 1;
        back.onopen();
        expect(env.log).toEqual(['a.disconnected', 'a.reconnected']);
        ws.enabled = false;
    });

    function wsEnv() {
        const env = createEnv('<div id="a" dj-hook="R"></div>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        env.log.length = 0;
        const ws = new env.djust.LiveViewWebSocket();
        ws.enabled = true;
        const open = () => {
            ws.ws = null;
            ws.connect('ws://localhost:8000/ws/live/');
            return env.sockets[env.sockets.length - 1];
        };
        return { env, ws, open };
    }

    it('WebSocket: a failed first handshake, then a retry that opens, gives disconnected() then reconnected()', () => {
        const { env, ws, open } = wsEnv();
        open().onclose({ code: 1006 }); // never opened
        const retry = open();
        retry.readyState = 1;
        retry.onopen();
        expect(env.log).toEqual(['a.disconnected', 'a.reconnected']);
        // Not offline any more: a hook mounted now gets no disconnected().
        env.document.body.insertAdjacentHTML('beforeend', '<i id="late" dj-hook="R"></i>');
        env.djust.updateHooks();
        expect(env.log.slice(2)).toEqual(['a.updated', 'late.mounted']);
        ws.enabled = false;
    });

    it('WebSocket: a drop, then a navigation disconnect() and a new socket, gives reconnected()', () => {
        const { env, ws, open } = wsEnv();
        const first = open();
        first.readyState = 1;
        first.onopen();
        first.onclose({ code: 1006 });
        ws.disconnect(); // resets the connection stats, as a navigation does
        ws.enabled = true;
        const next = open();
        next.readyState = 1;
        next.onopen();
        expect(env.log).toEqual(['a.disconnected', 'a.reconnected']);
        ws.enabled = false;
    });

    it('a working HTTP fallback counts as connected: offline hooks get reconnected()', async () => {
        const env = createEnv('<button id="b" dj-click="go">b</button><div id="a" dj-hook="R"></div>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        env.djust.notifyHooksDisconnected();
        env.window.fetch = async () => ({ ok: true, status: 200, json: async () => ({ patches: [] }) });
        env.log.length = 0;
        await env.djust.handleEvent('go', {});
        expect(env.log[0]).toBe('a.reconnected');
        env.log.length = 0;
        env.document.body.insertAdjacentHTML('beforeend', '<i id="late" dj-hook="R"></i>');
        env.djust.updateHooks();
        expect(env.log).toEqual(['a.updated', 'late.mounted']);
    });

    it('a hook mounted while offline gets disconnected() after mounted(), then reconnected()', () => {
        const env = createEnv('<div id="host"></div>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.notifyHooksDisconnected();
        env.document.getElementById('host').innerHTML = '<i id="late" dj-hook="R"></i>';
        env.djust.updateHooks();
        env.djust.notifyHooksReconnected();
        env.djust.notifyHooksReconnected();
        expect(env.log).toEqual(['late.mounted', 'late.disconnected', 'late.reconnected']);
    });

    it('reconnected() is not called on a hook that never got disconnected()', () => {
        const env = createEnv('<div id="a" dj-hook="R"></div>');
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        env.djust.notifyHooksReconnected();
        expect(env.log).toEqual(['a.mounted']);
    });

    it('SSE: a dropped stream calls disconnected(), the reopened stream reconnected()', () => {
        const env = createEnv('<div id="a" dj-hook="R"></div>');
        const { window } = env;
        class ES {
            constructor(url) { this.url = url; this.readyState = 0; ES.last = this; }
            close() { this.readyState = 2; }
        }
        ES.CONNECTING = 0; ES.OPEN = 1; ES.CLOSED = 2;
        window.EventSource = ES;
        env.djust.hooks = { R: recorder(env.log) };
        env.djust.mountHooks(env.document);
        const sse = new env.djust.LiveViewSSE();
        sse._sendMountFrame = () => {};
        sse.connect(VIEW, {});
        const es = ES.last;
        es.readyState = 1;
        es.onopen();
        env.log.length = 0;
        es.readyState = 0; // EventSource retrying by itself
        es.onerror(new window.Event('error'));
        es.onerror(new window.Event('error'));
        es.readyState = 1;
        es.onopen();
        expect(env.log).toEqual(['a.disconnected', 'a.reconnected']);
    });
});

describe('#3656 rule 9: a throwing hook breaks neither the patch nor the other hooks', () => {
    it('a throwing beforeUpdate() / updated() still lets the patch land and the next hook run', async () => {
        const env = await setup(
            '<div id="bad" dj-id="bad" dj-hook="Bad"></div><div id="good" dj-hook="R"></div>',
            (log) => ({
                Bad: { beforeUpdate() { throw new Error('boom-before'); }, updated() { throw new Error('boom-updated'); } },
                R: recorder(log),
            }),
        );
        await env.ws.handleMessage(patchFrame([{ type: 'SetAttr', path: [0], d: 'bad', key: 'title', value: 'set' }]));
        expect(env.document.getElementById('bad').getAttribute('title')).toBe('set');
        expect(env.log).toEqual(['good.beforeUpdate', 'good.updated']);
        expect(env.errors.some((e) => e.includes('Bad.beforeUpdate()') && e.includes('boom-before'))).toBe(true);
        expect(env.errors.some((e) => e.includes('Bad.updated()') && e.includes('boom-updated'))).toBe(true);
    });

    it('a throwing mounted() leaves the hook mounted, so destroyed() can clean up', async () => {
        const env = await setup('<p dj-id="t">1</p>', (log) => ({
            Bad: { mounted() { log.push('bad.mounted'); throw new Error('half-built'); }, destroyed() { log.push('bad.destroyed'); } },
            R: recorder(log),
        }));
        await env.ws.handleMessage(htmlFrame('<div id="bad" dj-hook="Bad"></div><div id="good" dj-hook="R"></div>'));
        expect(env.log).toEqual(['bad.mounted', 'good.mounted']);
        expect(env.djust.getHook('#bad')).not.toBeNull();
        await env.ws.handleMessage(htmlFrame('<p>1</p>'));
        expect(env.log).toContain('bad.destroyed');
    });

    it('a registry entry that is not an object is skipped; the hooks after it still mount', () => {
        const env = createEnv('<div id="n" dj-hook="Num"></div><div id="g" dj-hook="R"></div>');
        env.djust.hooks = { Num: 42, R: recorder(env.log) };
        env.djust.updateHooks();
        expect(env.log).toEqual(['g.mounted']);
    });

    it('a rejected async callback is logged, not left unhandled', async () => {
        const env = createEnv('<div id="a" dj-hook="A"></div>');
        env.djust.hooks = { A: { async mounted() { throw new Error('async-boom'); } } };
        env.djust.mountHooks(env.document);
        await new Promise((r) => setTimeout(r, 0));
        expect(env.errors.some((e) => e.includes('A.mounted()') && e.includes('async-boom'))).toBe(true);
    });
});
