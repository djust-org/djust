/**
 * #4322 — what a hook's `pushEvent(event, payload, {outcome: true})` resolves
 * with, one test per outcome of "Event outcomes" in docs/website/guides/hooks.md,
 * over the WebSocket, the SSE transport and the HTTP-only page POST.
 *
 * Every outcome is matched to its request by `ref` (transport plan D2): a
 * reply, error or refusal for another event never settles this one.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const VIEW = 'test.views.Page';
const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

function newDom(setup) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="${VIEW}" dj-root><div id="widget" dj-hook="Widget"></div></div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/page/' });
    const win = dom.window;
    if (!win.CSS) win.CSS = {};
    if (!win.CSS.escape) win.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    win.requestAnimationFrame = (cb) => setTimeout(cb, 0);
    win.scrollTo = () => {};
    win.eval(`
        window.__hook = null;
        window.__pushed = [];
        window.djust = window.djust || {};
        window.djust.hooks = {
            Widget: {
                mounted() {
                    window.__hook = this;
                    this.handleEvent('saved', (p) => window.__pushed.push(p));
                },
            },
        };
    `);
    win.__warnings = [];
    win.console = {
        log() {}, debug() {}, info() {}, error() {},
        warn: (...args) => win.__warnings.push(args.join(' ')),
    };
    setup(win);
    win.eval(clientCode);
    return { dom, win };
}

// ---------------------------------------------------------------- WebSocket

async function wsPage() {
    const { win } = newDom((w) => {
        w.eval(`
            window.WebSocket = class {
                static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
                constructor() { this.readyState = 1; this.sent = []; }
                send(d) { this.sent.push(d); }
                close() { this.readyState = 3; }
            };
        `);
    });
    await tick(50);
    const ws = win.djust.liveViewInstance;
    ws.viewMounted = true;
    win.djust.mountHooks(win.document);
    ws.ws.sent.length = 0;
    const events = () => ws.ws.sent.map((s) => JSON.parse(s)).filter((m) => m.type === 'event');
    return { win, ws, hook: win.__hook, events, lastRef: () => events().at(-1).ref };
}

const settled = async (promise) => {
    let state = 'pending';
    promise.then((v) => { state = v; });
    await tick(5);
    return state;
};

describe('pushEvent outcomes over the WebSocket (#4322)', () => {
    it('ok: resolves with the reply, after its effects were applied', async () => {
        const { ws, hook, win, lastRef } = await wsPage();
        const p = hook.pushEvent('save', { id: 1 }, { outcome: true });
        const ref = lastRef();
        expect(await settled(p)).toBe('pending');
        await ws.handleMessage({
            type: 'render', source: 'event', view: VIEW, ref,
            effects: [{ kind: 'push_event', event: 'saved', payload: { id: 1 } }],
        });
        const outcome = await p;
        expect(outcome.status).toBe('ok');
        expect(outcome.reply).toMatchObject({ type: 'render', ref });
        expect(outcome.error).toBeNull();
        // The reply's effect reached the hook before the outcome resolved.
        expect(win.__pushed).toEqual([{ id: 1 }]);
    });

    it('error: a server error frame for its ref, with the message and code', async () => {
        const { ws, hook, lastRef } = await wsPage();
        const other = ws.sendEvent('other', {});
        const p = hook.pushEvent('save', {}, { outcome: true });
        await ws.handleMessage({ type: 'error', error: 'Boom', code: 'event_error', ref: lastRef() });
        expect(await p).toEqual({
            status: 'error', reply: null,
            error: { message: 'Boom', code: 'event_error', validation_details: null },
        });
        // The other event is still waiting for its own answer.
        expect(await settled(other)).toBe('pending');
    });

    it('rejected: the handler contract refused the parameters (validation_details)', async () => {
        const { ws, hook, lastRef } = await wsPage();
        const p = hook.pushEvent('save', { n: 'x' }, { outcome: true });
        const details = { expected_params: ['n'], provided_params: ['n'], missing_params: [], type_errors: [{ param: 'n' }] };
        await ws.handleMessage({ type: 'error', error: 'Invalid parameters', validation_details: details, ref: lastRef() });
        const outcome = await p;
        expect(outcome.status).toBe('rejected');
        expect(outcome.error.validation_details).toEqual(details);
    });

    it('rate_limited: a rate_limit_exceeded frame, or an error coded rate_limited', async () => {
        const { ws, hook, lastRef } = await wsPage();
        const a = hook.pushEvent('save', {}, { outcome: true });
        await ws.handleMessage({ type: 'rate_limit_exceeded', message: 'slow down', ref: lastRef() });
        expect((await a).status).toBe('rate_limited');
        const b = hook.pushEvent('save', {}, { outcome: true });
        await ws.handleMessage({ type: 'error', error: 'Rate limit exceeded, event dropped', code: 'rate_limited', ref: lastRef() });
        expect((await b).status).toBe('rate_limited');
    });

    it('cancelled: the client closed the connection (a navigation)', async () => {
        const { ws, hook } = await wsPage();
        const p = hook.pushEvent('save', {}, { outcome: true });
        ws.disconnect();
        expect((await p).status).toBe('cancelled');
    });

    it('disconnected: the connection dropped while the event was pending', async () => {
        const { ws, hook } = await wsPage();
        const p = hook.pushEvent('save', {}, { outcome: true });
        ws.ws.onclose({ code: 1006 });
        expect((await p).status).toBe('disconnected');
    });

    it('disconnected: no connection to send on; nothing is sent', async () => {
        const { ws, hook, events } = await wsPage();
        ws.viewMounted = false;
        expect(await hook.pushEvent('save', {}, { outcome: true })).toEqual({ status: 'disconnected', reply: null, error: null });
        expect(events()).toEqual([]);
    });

    it('a reply to another event never settles it', async () => {
        const { ws, hook, events } = await wsPage();
        const other = ws.sendEvent('other', {});
        const p = hook.pushEvent('save', {}, { outcome: true });
        const [otherFrame] = events();
        await ws.handleMessage({ type: 'render', source: 'event', view: VIEW, ref: otherFrame.ref });
        expect(await settled(other)).toMatchObject({ ref: otherFrame.ref });
        expect(await settled(p)).toBe('pending');
    });

    it('without {outcome: true}: the reply frame, or null when it did not succeed', async () => {
        const { ws, hook, lastRef } = await wsPage();
        const ok = hook.pushEvent('save', {});
        await ws.handleMessage({ type: 'render', source: 'event', view: VIEW, ref: lastRef() });
        expect(await ok).toMatchObject({ type: 'render' });
        const failed = hook.pushEvent('save', {});
        await ws.handleMessage({ type: 'error', error: 'Boom', ref: lastRef() });
        expect(await failed).toBeNull();
        // A refused turn acknowledges with a frame; plain pushEvent still
        // resolves null, as over HTTP (review of #4322).
        const refused = hook.pushEvent('save', {});
        await ws.handleMessage({ type: 'render', source: 'refusal', view: VIEW, ref: lastRef() });
        expect(await refused).toBeNull();
    });

    it('never rejects when the socket refuses the send', async () => {
        const { ws, hook } = await wsPage();
        ws.ws.send = () => { throw new Error('InvalidStateError'); };
        expect(await hook.pushEvent('save', {}, { outcome: true })).toEqual({ status: 'disconnected', reply: null, error: null });
        expect(await hook.pushEvent('save', {})).toBeNull();
    });
});

// ---------------------------------------------------------------- SSE

async function ssePage() {
    const es = { onopen: null, onmessage: null, onerror: null, readyState: 1, close: vi.fn() };
    const posts = [];
    let answer = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    const { win } = newDom((w) => {
        w.DJUST_USE_WEBSOCKET = false;
        w.EventSource = vi.fn(function () { return es; });
        w.EventSource.CLOSED = 2;
        w.EventSource.OPEN = 1;
        w.fetch = vi.fn((url, init) => {
            const body = JSON.parse(init.body);
            posts.push(body);
            return body.type === 'event' ? answer(body) : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
        });
    });
    await tick(20);
    es.onopen();
    es.onmessage({ data: JSON.stringify({ type: 'mount', view: VIEW, version: 1, html: '<div id="widget" dj-hook="Widget" dj-id="1"></div>', has_ids: true }) });
    await tick(20);
    win.djust.mountHooks(win.document);
    const serve = async (frame) => { es.onmessage({ data: JSON.stringify(frame) }); await tick(10); };
    const lastRef = () => posts.filter((p) => p.type === 'event').at(-1).ref;
    return { win, es, hook: win.__hook, serve, lastRef, setAnswer: (fn) => { answer = fn; } };
}

describe('pushEvent outcomes over SSE (#4322)', () => {
    it('ok and error are matched by ref, as over the WebSocket', async () => {
        const { hook, serve, lastRef } = await ssePage();
        const ok = hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        await serve({ type: 'render', source: 'event', view: VIEW, ref: lastRef() });
        expect((await ok).status).toBe('ok');
        const failed = hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        await serve({ type: 'error', error: 'Boom', ref: lastRef() });
        expect((await failed).status).toBe('error');
    });

    it('rate_limited: the event POST answered 429', async () => {
        const { hook, setAnswer } = await ssePage();
        setAnswer(() => Promise.resolve({ ok: false, status: 429, clone() { return this; }, json: () => Promise.resolve({}) }));
        expect((await hook.pushEvent('save', {}, { outcome: true })).status).toBe('rate_limited');
    });

    it('disconnected: the event POST never reached the server', async () => {
        const { hook, setAnswer } = await ssePage();
        setAnswer(() => Promise.reject(new TypeError('Failed to fetch')));
        expect((await hook.pushEvent('save', {}, { outcome: true })).status).toBe('disconnected');
    });

    it('rate_limited: a turn-policy rate_limit_exceeded frame names its ref', async () => {
        const { hook, serve, lastRef } = await ssePage();
        const other = hook.pushEvent('other', {}, { outcome: true });
        await tick(5);
        const otherRef = lastRef();
        const p = hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        const ref = lastRef();
        await serve({ type: 'rate_limit_exceeded', message: 'slow down', ref });
        // The refusal acknowledgement that follows changes nothing.
        await serve({ type: 'render', source: 'refusal', view: VIEW, ref });
        const outcome = await p;
        expect(outcome.status).toBe('rate_limited');
        expect(outcome.error.code).toBe('rate_limited');
        expect(await settled(other)).toBe('pending');
        await serve({ type: 'render', source: 'event', view: VIEW, ref: otherRef });
        expect((await other).status).toBe('ok');
    });

    it('disconnected: the stream dropped and EventSource is reconnecting', async () => {
        const { hook, es } = await ssePage();
        const p = hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        es.readyState = 0; // CONNECTING: EventSource retries by itself
        es.onerror({});
        expect((await p).status).toBe('disconnected');
    });

    it('disconnected: the stream reopened on a new server session', async () => {
        const { hook, es } = await ssePage();
        const p = hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        es.onopen(); // a reconnect, no onerror seen first
        expect((await p).status).toBe('disconnected');
    });

    it('disconnected: an onerror that drops the stream for a fresh one', async () => {
        // A fresh-id retry (#3164): the stream never acked.
        const retry = await ssePage();
        const a = retry.hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        retry.win.djust.liveViewInstance._streamAcked = false;
        retry.win.djust.liveViewInstance._freshIdRetried = false;
        retry.es.readyState = retry.win.EventSource.CLOSED;
        retry.es.onerror({});
        expect((await a).status).toBe('disconnected');
        // The page URL changed since the stream opened (SPA navigation).
        const moved = await ssePage();
        const b = moved.hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        moved.win.history.pushState({}, '', '/elsewhere/');
        moved.es.onerror({});
        expect((await b).status).toBe('disconnected');
    });

    it('cancelled: an intentional disconnect()', async () => {
        const { hook, win } = await ssePage();
        const p = hook.pushEvent('save', {}, { outcome: true });
        await tick(5);
        win.djust.liveViewInstance.disconnect();
        expect((await p).status).toBe('cancelled');
    });

    it('disconnected: the stream closed while the event was pending', async () => {
        const { hook, es, win } = await ssePage();
        const p = hook.pushEvent('save', {}, { outcome: true });
        es.readyState = win.EventSource.CLOSED;
        win.djust.liveViewInstance._streamAcked = true;
        es.onerror({});
        expect((await p).status).toBe('disconnected');
    });
});

// ---------------------------------------------------------------- HTTP only

async function httpPage(respond) {
    const posts = [];
    const { win } = newDom((w) => {
        w.DJUST_USE_WEBSOCKET = false;
        w.EventSource = undefined;
        w.fetch = vi.fn((url, init) => {
            posts.push({ url, init, body: JSON.parse(init.body) });
            return respond(init);
        });
    });
    await tick(20);
    win.djust.mountHooks(win.document);
    return { win, hook: win.__hook, posts };
}

const jsonResponse = (status, body) => Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: () => Promise.resolve(body),
});

describe('pushEvent outcomes on an HTTP-only page (#4322)', () => {
    it('a hook event is a page POST; ok resolves with the answer, after it is applied', async () => {
        const { win, hook, posts } = await httpPage(() => jsonResponse(200, {
            type: 'render', effects: [{ kind: 'push_event', event: 'saved', payload: { via: 'http' } }],
        }));
        expect(win.djust.liveViewInstance.httpOnly).toBe(true);
        const outcome = await hook.pushEvent('save', { id: 3 }, { outcome: true });
        expect(posts).toHaveLength(1);
        expect(posts[0].init.headers['X-Djust-Event']).toBe('save');
        expect(posts[0].body).toEqual({ id: 3 });
        expect(outcome.status).toBe('ok');
        expect(outcome.reply).toMatchObject({ type: 'render' });
        expect(win.__pushed).toEqual([{ via: 'http' }]);
        expect(win.__warnings.some((w) => w.includes('Cannot pushEvent'))).toBe(false);
    });

    it('rejected: 400 with validation_details', async () => {
        const { hook } = await httpPage(() => jsonResponse(400, {
            error: 'Invalid parameters', validation_details: { missing_params: ['n'] },
        }));
        const outcome = await hook.pushEvent('save', {}, { outcome: true });
        expect(outcome.status).toBe('rejected');
        expect(outcome.error.validation_details).toEqual({ missing_params: ['n'] });
    });

    it('rate_limited: 429', async () => {
        const { hook } = await httpPage(() => jsonResponse(429, { error: 'Rate limit exceeded', code: 'rate_limited' }));
        expect((await hook.pushEvent('save', {}, { outcome: true })).status).toBe('rate_limited');
    });

    it('error: any other refusal or failure', async () => {
        const { hook } = await httpPage(() => jsonResponse(500, { error: 'Server error' }));
        const outcome = await hook.pushEvent('save', {}, { outcome: true });
        expect(outcome.status).toBe('error');
        expect(outcome.error.message).toBe('Server error');
    });

    it('disconnected: the POST never reached the server', async () => {
        const { hook } = await httpPage(() => Promise.reject(new TypeError('Failed to fetch')));
        expect((await hook.pushEvent('save', {}, { outcome: true })).status).toBe('disconnected');
    });

    it('error, not ok: an answer that fails to apply', async () => {
        const answer = { type: 'render' };
        Object.defineProperty(answer, 'effects', { get() { throw new Error('bad answer'); } });
        const { hook } = await httpPage(() => Promise.resolve({
            ok: true, status: 200, headers: { get: () => null }, json: () => Promise.resolve(answer),
        }));
        const outcome = await hook.pushEvent('save', {}, { outcome: true });
        expect(outcome.status).toBe('error');
        expect(outcome.error.message).toBe('bad answer');
    });

    it('without {outcome: true}: the answer, or null', async () => {
        const ok = await httpPage(() => jsonResponse(200, { type: 'render' }));
        expect(await ok.hook.pushEvent('save', {})).toMatchObject({ type: 'render' });
        const failed = await httpPage(() => jsonResponse(500, { error: 'x' }));
        expect(await failed.hook.pushEvent('save', {})).toBeNull();
    });
});
