/**
 * Back has to tell "different view" from "different query parameters".
 *
 * `popstate` fires after the address bar has already changed, so the handler
 * cannot see where it came from. It used to read a `redirect` flag off the
 * history entry. That flag is absent on the entry the BROWSER created for the
 * original page load, so the first back after a navigation took the patch
 * branch: the URL moved and the old view stayed on screen.
 *
 * Stamping that entry fixes the symptom and breaks `dj-patch`. A patch pushes
 * `{djust: true}` with no `redirect`, so back from a patched URL onto a
 * stamped load entry would take the REMOUNT branch and throw away scroll,
 * inputs and view state where a cheap `url_change` was correct.
 *
 * So the decision is the pathname, and these pin both directions.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

async function load(startPath) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="test.views.IndexView" dj-root><p>content</p></div>
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
        // jsdom has neither; the navigation path scrolls after a redirect.
        window.requestAnimationFrame = function(cb) { return setTimeout(cb, 0); };
        window.scrollTo = function() {};
    `);
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    dom.window.djust._routeMap = {
        '/items/': 'test.views.IndexView',
        '/items/detail/': 'test.views.DetailView',
    };
    const ws = dom.window.djust.liveViewInstance;
    ws.viewMounted = true;
    ws.ws.sent.length = 0;
    // Whatever the load entry ends up holding is what a real back onto it
    // would deliver. Capturing it here keeps these tests faithful to any
    // implementation, including one that stamps the entry.
    const loadEntryState = dom.window.history.state;
    return { dom, ws, loadEntryState };
}

/** What the browser does on back: change the URL, then fire popstate. */
async function goBack(dom, toPath, state) {
    dom.window.history.replaceState(state, '', toPath);
    const event = new dom.window.PopStateEvent('popstate', { state });
    dom.window.dispatchEvent(event);
    await new Promise((resolve) => setTimeout(resolve, 30));
}

function sentTypes(ws) {
    return ws.ws.sent.map((raw) => {
        try {
            return JSON.parse(raw).type;
        } catch (_e) {
            return 'unparseable';
        }
    });
}

describe('popstate decides by pathname', () => {
    it('leaves the load entry alone instead of forging state onto it', async () => {
        const { dom } = await load('/items/');
        expect(dom.window.history.state).toBeNull();
    });

    it('back to a different path remounts the view', async () => {
        const { dom, ws, loadEntryState } = await load('/items/');
        // A dj-navigate took us to the detail page.
        dom.window.djust.navigation.handleNavigation({
            type: 'navigation',
            action: 'live_redirect',
            path: '/items/detail/',
        });
        await new Promise((resolve) => setTimeout(resolve, 30));
        ws.ws.sent.length = 0;

        await goBack(dom, '/items/', loadEntryState);

        expect(sentTypes(ws)).toContain('live_redirect_mount');
    });

    it('back to the same path with different params patches instead', async () => {
        const { dom, ws, loadEntryState } = await load('/items/');
        // A dj-patch changed only the query.
        dom.window.djust.navigation.handleNavigation({
            type: 'navigation',
            action: 'live_patch',
            path: '/items/',
            params: { page: '2' },
        });
        await new Promise((resolve) => setTimeout(resolve, 30));
        ws.ws.sent.length = 0;

        await goBack(dom, '/items/', loadEntryState);

        const types = sentTypes(ws);
        expect(types).toContain('url_change');
        expect(types).not.toContain('live_redirect_mount');
    });

    it('still remounts for an explicit same-path redirect', async () => {
        const { dom, ws } = await load('/items/');
        ws.ws.sent.length = 0;

        await goBack(dom, '/items/', { djust: true, redirect: true });

        expect(sentTypes(ws)).toContain('live_redirect_mount');
    });
});
