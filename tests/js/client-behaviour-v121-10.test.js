/**
 * v1.2.1-10 — client JS behaviour.
 *
 * - #2965: push(page_loading=True) finishes the page-loading bar, and so do
 *   the aborted live_redirect paths that called the same missing stop().
 * - #2971: a data-draft-clear flag that arrives in a patch clears the draft.
 * - #2949: state snapshots (and the VDOM fast-paint cache) are keyed by
 *   pathname + query, so two queries on one path never share an entry.
 *
 * All run against the BUILT client.js.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function quietConsole(window) {
    window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
}

// ---------------------------------------------------------------------------
// #2965 — the page-loading bar finishes
// ---------------------------------------------------------------------------

function createHttpEnv(bodyHtml = '') {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>
            <div dj-root dj-view="test.View">${bodyHtml}</div>
        </body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    quietConsole(window);
    window.fetch = vi.fn().mockImplementation(async () => ({
        ok: true,
        json: async () => ({ patches: [], version: 1 }),
    }));
    window.DJUST_USE_WEBSOCKET = false;
    window.requestAnimationFrame = (cb) => { cb(); return 1; };
    try { window.eval(clientCode); } catch (_) { /* partial DOM APIs */ }
    return { window, document: window.document };
}

describe('#2965 page_loading push', () => {
    it('finishes the bar after the pushed event', async () => {
        const { window, document } = createHttpEnv('<div id="origin"></div>');
        const finish = vi.spyOn(window.djust.pageLoading, 'finish');
        const start = vi.spyOn(window.djust.pageLoading, 'start');
        await window.djust.js
            .push('save', { page_loading: true })
            .exec(document.getElementById('origin'));
        await new Promise((r) => setTimeout(r, 10));
        expect(start).toHaveBeenCalledTimes(1);
        expect(finish).toHaveBeenCalledTimes(1);
    });

    it('does not touch the bar without page_loading', async () => {
        const { window, document } = createHttpEnv('<div id="origin"></div>');
        const finish = vi.spyOn(window.djust.pageLoading, 'finish');
        await window.djust.js.push('save').exec(document.getElementById('origin'));
        await new Promise((r) => setTimeout(r, 10));
        expect(finish).not.toHaveBeenCalled();
    });
});

describe('#2965 live_redirect abort paths', () => {
    it('an unsafe cross-origin target finishes the bar it started', () => {
        const { window } = createHttpEnv();
        const finish = vi.spyOn(window.djust.pageLoading, 'finish');
        window.djust.navigation.handleNavigation({
            type: 'navigation',
            action: 'live_redirect',
            // eslint-disable-next-line no-script-url -- the unsafe target under test
            path: 'javascript:alert(1)',
        });
        expect(finish).toHaveBeenCalled();
    });
});

// ---------------------------------------------------------------------------
// #2971 — data-draft-clear applied from a patch
// ---------------------------------------------------------------------------

describe('#2971 clear_draft from an event', () => {
    function draftEnv() {
        const { window, document } = createHttpEnv(
            '<form id="f" data-draft-enabled data-draft-key="post_1">' +
            '<input name="title" data-draft="true" value=""></form>',
        );
        window.localStorage.setItem(
            'djust_draft_post_1',
            JSON.stringify({ data: { title: 'unsent' }, timestamp: Date.now() }),
        );
        return { window, document };
    }

    it('a patched data-draft-clear clears localStorage and keeps the server-owned flag', () => {
        const { window, document } = draftEnv();
        const form = document.getElementById('f');
        // The server's next render carried data-draft-clear (a patch).
        form.setAttribute('data-draft-clear', '');
        window.djust.reinitAfterDOMUpdate();
        expect(window.localStorage.getItem('djust_draft_post_1')).toBeNull();
        // Left for the server's VDOM to remove, so a later render that carries
        // it again still matches the DOM.
        expect(form.hasAttribute('data-draft-clear')).toBe(true);
    });

    it('a later unrelated DOM update does not wipe a draft started after the clear', () => {
        const { window, document } = draftEnv();
        const form = document.getElementById('f');
        form.setAttribute('data-draft-clear', '');
        window.djust.reinitAfterDOMUpdate();
        // The user starts a new draft; an unrelated update arrives while the
        // flag is still on the page.
        window.localStorage.setItem('djust_draft_post_1', '{"data":{"title":"new"}}');
        window.djust.reinitAfterDOMUpdate();
        expect(window.localStorage.getItem('djust_draft_post_1')).not.toBeNull();
        // The server's next render drops the flag; a later one sets it again.
        form.removeAttribute('data-draft-clear');
        window.djust.reinitAfterDOMUpdate();
        form.setAttribute('data-draft-clear', '');
        window.djust.reinitAfterDOMUpdate();
        expect(window.localStorage.getItem('djust_draft_post_1')).toBeNull();
    });

    it('the djust:draft-clear push event clears the named draft', () => {
        const { window } = draftEnv();
        window.dispatchEvent(new window.CustomEvent('djust:push_event', {
            detail: { event: 'djust:draft-clear', payload: { key: 'post_1' } },
        }));
        expect(window.localStorage.getItem('djust_draft_post_1')).toBeNull();
    });

    it('ignores other push events and malformed payloads', () => {
        const { window } = draftEnv();
        window.dispatchEvent(new window.CustomEvent('djust:push_event', {
            detail: { event: 'other', payload: { key: 'post_1' } },
        }));
        window.dispatchEvent(new window.CustomEvent('djust:push_event', {
            detail: { event: 'djust:draft-clear', payload: { key: 42 } },
        }));
        expect(window.localStorage.getItem('djust_draft_post_1')).not.toBeNull();
    });

    it('leaves the draft alone without the flag', () => {
        const { window } = draftEnv();
        window.djust.reinitAfterDOMUpdate();
        expect(window.localStorage.getItem('djust_draft_post_1')).not.toBeNull();
    });
});

// ---------------------------------------------------------------------------
// #2949 — snapshot keys carry the query string
// ---------------------------------------------------------------------------

async function loadWs(startPath) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="test.views.Orders" dj-root><p>content</p></div>
         </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost' + startPath },
    );
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; this.sent = []; }
            send(d) { this.sent.push(d); }
            close() {}
        };
        window.location.reload = function() { window.__reloaded = true; };
        window.requestAnimationFrame = function(cb) { return setTimeout(cb, 0); };
        window.scrollTo = function() {};
    `);
    quietConsole(dom.window);
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    dom.window.djust._routeMap = {
        '/orders/': 'test.views.Orders',
        '/inbox/': 'test.views.Inbox',
    };
    const ws = dom.window.djust.liveViewInstance;
    ws.viewMounted = true;
    ws.ws.sent.length = 0;
    return { dom, ws };
}

describe('#2949 state snapshot keys', () => {
    it('live_redirect captures under pathname + query and resolves the view by pathname', async () => {
        const { dom } = await loadWs('/orders/?page=2');
        const captured = [];
        dom.window.djust._clientState = { 'test.views.Orders': 'signed-blob' };
        dom.window.djust._sw = dom.window.djust._sw || {};
        dom.window.djust._sw.captureState = (url, slug, json) => captured.push({ url, slug, json });
        dom.window.djust.navigation.handleNavigation({
            type: 'navigation',
            action: 'live_redirect',
            path: '/inbox/',
        });
        expect(captured).toEqual([
            { url: '/orders/?page=2', slug: 'test.views.Orders', json: 'signed-blob' },
        ]);
    });

    it('two queries on one path keep separate keys', async () => {
        const { dom } = await loadWs('/orders/?page=1');
        const lookups = [];
        dom.window.djust._sw = dom.window.djust._sw || {};
        dom.window.djust._sw.lookupState = (url) => {
            lookups.push(url);
            return Promise.resolve({ hit: false });
        };
        dom.window.djust._sw.lookupVdom = (url) => {
            lookups.push('vdom:' + url);
            return Promise.resolve({ hit: false });
        };
        // Back onto the page=2 entry of the other view's path.
        dom.window.history.replaceState({ djust: true, redirect: true }, '', '/inbox/?tab=b');
        dom.window.dispatchEvent(new dom.window.PopStateEvent('popstate', {
            state: { djust: true, redirect: true },
        }));
        await new Promise((resolve) => setTimeout(resolve, 30));
        expect(lookups).toContain('/inbox/?tab=b');
        expect(lookups).toContain('vdom:/inbox/?tab=b');
        expect(lookups).not.toContain('/inbox/');
    });

    it('a capture without fromUrl falls back to pathname + query', async () => {
        const { dom } = await loadWs('/orders/?page=3');
        const captured = [];
        dom.window.djust._clientState = { 'test.views.Orders': 'blob' };
        dom.window.djust._sw = dom.window.djust._sw || {};
        dom.window.djust._sw.captureState = (url, slug) => captured.push({ url, slug });
        dom.window.djust._stateSnapshot._capture(null);
        expect(captured).toEqual([{ url: '/orders/?page=3', slug: 'test.views.Orders' }]);
    });
});
