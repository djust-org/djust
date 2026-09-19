/**
 * The entry the document was loaded on needs a djust history state.
 *
 * A live_redirect pushes an entry stamped {djust, redirect}, and the popstate
 * handler reads that stamp to choose between re-mounting a view and patching
 * the current one. The browser gives the original page load a null state, so
 * going back from the first dj-navigate was read as a same-page parameter
 * change: the URL moved back and the content stayed on the page the reader
 * had navigated to. A back button that changes the address bar and nothing
 * else is worse than a full reload, which at least arrives.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

async function load(initialState) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="test.views.TestView" dj-root><p>content</p></div>
         </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/start/' },
    );
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; }
            send() {} close() {}
        };
        window.location.reload = function() {};
    `);
    if (initialState !== undefined) {
        dom.window.history.replaceState(initialState, '', dom.window.location.href);
    }
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 50));
    return dom;
}

describe('the initial history entry', () => {
    it('is stamped so a later back is recognised as a view change', async () => {
        const dom = await load();
        expect(dom.window.history.state).toEqual({ djust: true, redirect: true });
    });

    it('keeps the url it was loaded on', async () => {
        const dom = await load();
        expect(dom.window.location.pathname).toBe('/start/');
    });

    it("leaves an application's own history state alone", async () => {
        const dom = await load({ mine: 'keep this' });
        expect(dom.window.history.state).toEqual({ mine: 'keep this' });
    });

    it('is safe to run twice', async () => {
        const dom = await load();
        dom.window.djust.navigation.stampInitialHistoryEntry();
        expect(dom.window.history.state).toEqual({ djust: true, redirect: true });
    });
});
