/**
 * dj-poll timers must never outlive their element.
 *
 * A page view with a `[dj-poll]` nested inside dj-root kept firing that poll
 * after a live_redirect mounted a different view: the mount replaced the
 * view's region, and every removal path cleared the interval only on the node
 * it replaced itself — never on a `[dj-poll]` DESCENDANT. The detached element
 * kept its setInterval, firePoll checked only `document.hidden`, and the
 * server logged "Handler not found" on every tick.
 *
 * Each case removes the poll's ANCESTOR (not the poll element itself) by a
 * different path, then runs real timers and asserts the old handler is never
 * sent again and the interval is cleared.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { setTimeout as nativeSleep } from 'node:timers/promises';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const POLL_PAGE = `
<div dj-view="app.DashboardView" dj-root>
  <section dj-id="s1" id="region">
    <div id="poller" hidden dj-poll="rr_poll" dj-poll-interval="30"></div>
  </section>
  <p dj-id="p1" id="keep">keep</p>
</div>`;

let dom;
afterEach(() => {
    if (dom) dom.window.close();
    dom = null;
});

function setup(bodyHtml = POLL_PAGE) {
    dom = new JSDOM(`<!DOCTYPE html><html><head></head><body>${bodyHtml}</body></html>`, {
        url: 'http://localhost/',
        runScripts: 'dangerously',
    });
    dom.window.eval(`
        window._sent = [];
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; }
            send(msg) { window._sent.push(String(msg)); }
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', { value: false, writable: true, configurable: true });
        window.fetch = async function(url, opts) {
            var name = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            window._sent.push("fetch:" + name);
            return { ok: true, json: async function() { return { patches: [], version: 1 }; } };
        };
    `);
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom.window;
}

const pollSends = (win) => win._sent.filter((m) => m.includes('rr_poll')).length;

/** The poll is live before the removal — so a zero afterwards means something. */
async function expectPolling(win) {
    await nativeSleep(120);
    expect(pollSends(win)).toBeGreaterThan(0);
}

async function expectNoMorePolls(win, oldEl) {
    expect(oldEl.isConnected).toBe(false);
    expect(oldEl._djustPollIntervalId).toBeFalsy();
    expect(oldEl._djustPollVisibilityHandler).toBeFalsy();
    win._sent.length = 0;
    await nativeSleep(150);
    // A visibility flip must not resurrect it either.
    win.document.dispatchEvent(new win.Event('visibilitychange'));
    expect(pollSends(win)).toBe(0);
}

async function mountOtherView(win, { prerendered }) {
    const ws = new win.djust.LiveViewWebSocket();
    ws.connect('ws://localhost/ws/live/');
    ws.ws.onopen({ type: 'open' });
    await ws.handleMessage({ type: 'connect', session_id: 's' });
    ws.skipMountHtml = prerendered;
    await ws.handleMessage({
        type: 'mount',
        view: 'app.ActivityView',
        html: '<main dj-id="m1"><p>activity</p></main>',
        version: 1,
        has_ids: true,
    });
}

describe('dj-poll teardown when an ancestor is removed', () => {
    it('live_redirect mount (server HTML replaces the view region)', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        await expectPolling(win);
        await mountOtherView(win, { prerendered: false });
        await expectNoMorePolls(win, oldEl);
    });

    it('mount morph (prerendered branch) drops the ancestor', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        await expectPolling(win);
        await mountOtherView(win, { prerendered: true });
        await expectNoMorePolls(win, oldEl);
    });

    it('VDOM Replace patch on the ancestor', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        await expectPolling(win);
        const ok = await win.djust.applyPatches([
            { type: 'Replace', path: [0], d: 's1', node: { tag: 'aside', attrs: {}, children: [] } },
        ]);
        expect(ok).toBe(true);
        await expectNoMorePolls(win, oldEl);
    });

    it('VDOM RemoveChild patch on the ancestor', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        await expectPolling(win);
        const root = win.document.querySelector('[dj-root]');
        root.setAttribute('dj-id', 'root');
        const ok = await win.djust.applyPatches([
            { type: 'RemoveChild', path: [], d: 'root', index: 0, child_d: 's1' },
        ]);
        expect(ok).toBe(true);
        await expectNoMorePolls(win, oldEl);
    });

    it('morph tag mismatch on the ancestor', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        await expectPolling(win);
        const region = win.document.getElementById('region');
        const desired = win.document.createElement('article');
        win.djust.morphElement(region, desired);
        await expectNoMorePolls(win, oldEl);
    });

    // The two mount tests above also pass through the bind pass's orphan
    // sweep; these pin the removal helpers themselves, with no re-bind after.
    it('replaceContainerHtml tears down nested polls, keeps the container\'s own', async () => {
        const win = setup(`<div dj-view="app.DashboardView" dj-root><div id="box" dj-poll="box_poll" dj-poll-interval="30">
            <section><div id="poller" dj-poll="rr_poll" dj-poll-interval="30"></div></section></div></div>`);
        const oldEl = win.document.getElementById('poller');
        const root = win.document.getElementById('box');
        const rootInterval = root._djustPollIntervalId;
        expect(rootInterval).toBeTruthy();
        win.djust.replaceContainerHtml(root, '<p>activity</p>');
        expect(oldEl._djustPollIntervalId).toBeFalsy();
        expect(root._djustPollIntervalId).toBe(rootInterval);
        await expectNoMorePolls(win, oldEl);
    });

    it('morphChildren removing the ancestor tears its polls down', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        const root = win.document.querySelector('[dj-root]');
        const desired = win.document.createElement('div');
        desired.innerHTML = '<p dj-id="p1" id="keep">keep</p>';
        win.djust.morphChildren(root, desired);
        expect(oldEl._djustPollIntervalId).toBeFalsy();
        await expectNoMorePolls(win, oldEl);
    });

    it('a removal path that does not tear down: firePoll stops itself once detached', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        await expectPolling(win);
        // Third-party / unknown removal: nothing djust-owned runs.
        const region = win.document.getElementById('region');
        region.parentNode.removeChild(region);
        win._sent.length = 0;
        await nativeSleep(150);
        expect(pollSends(win)).toBe(0);
        expect(oldEl._djustPollIntervalId).toBeFalsy();
    });

    it('a dj-poll element re-attached after a self-teardown is re-bound by the next scan', async () => {
        const win = setup();
        const oldEl = win.document.getElementById('poller');
        const region = win.document.getElementById('region');
        const parent = region.parentNode;
        parent.removeChild(region);
        await nativeSleep(100); // firePoll tears itself down while detached
        expect(oldEl._djustPollIntervalId).toBeFalsy();
        parent.insertBefore(region, parent.firstChild);
        win.djust.reinitAfterDOMUpdate();
        expect(oldEl._djustPollIntervalId).toBeTruthy();
        win._sent.length = 0;
        await expectPolling(win);
    });
});

describe('dj-poll on a surviving element (#2858)', () => {
    it('a patch elsewhere keeps the same interval (no phase restart)', async () => {
        const win = setup();
        const el = win.document.getElementById('poller');
        const before = el._djustPollIntervalId;
        expect(before).toBeTruthy();
        const ok = await win.djust.applyPatches([
            { type: 'Replace', path: [1], d: 'p1', node: { tag: 'span', attrs: {}, children: [] } },
        ]);
        expect(ok).toBe(true);
        win.djust.reinitAfterDOMUpdate();
        expect(el.isConnected).toBe(true);
        expect(el._djustPollIntervalId).toBe(before);
        await expectPolling(win);
    });

    it('dropping the dj-poll attribute from a surviving element stops the poll', async () => {
        const win = setup();
        const el = win.document.getElementById('poller');
        await expectPolling(win);
        el.removeAttribute('dj-poll');
        win.djust.reinitAfterDOMUpdate();
        expect(el._djustPollIntervalId).toBeFalsy();
        win._sent.length = 0;
        await nativeSleep(150);
        expect(pollSends(win)).toBe(0);
    });
});
