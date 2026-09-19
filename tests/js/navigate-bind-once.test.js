/**
 * A dj-navigate link gets exactly one click listener, however often the
 * directives are rebound.
 *
 * The bound-marker used to be a data attribute. Server HTML never carries it,
 * so a morph or a patch that reuses the DOM node stripped the marker while
 * the listener it recorded stayed attached, and the next bind pass added a
 * SECOND listener to the same element. One click then pushed two identical
 * history entries, which reads as a dead back button: the first press only
 * steps between duplicates of the page the reader is already on, and every
 * further patch adds one more press before anything moves.
 *
 * The listener count is the defect, so that is what these assert. Binding is
 * re-run from `reinitAfterDOMUpdate`, which fires on the initial load and on
 * every mount and patch, so this runs often on a busy page.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

async function createDom(markup) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
           <div dj-view="test.views.TestView" dj-root>${markup}</div>
         </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' },
    );
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; this.onopen = null; this.onclose = null; this.onmessage = null; this.onerror = null; }
            send() {} close() {}
        };
        window.location.reload = function() {};
    `);
    dom.window.eval(clientCode);
    // The client initialises against the [dj-view] container, which is what
    // binds the directives the first time.
    await new Promise((resolve) => setTimeout(resolve, 50));
    return dom;
}

/** What a morph against server HTML does: the node stays, every attribute the
 *  server did not send goes. */
function stripDataAttributes(el) {
    [...el.attributes]
        .filter((a) => a.name.startsWith('data-'))
        .forEach((a) => el.removeAttribute(a.name));
}

function countClickListeners(el) {
    const spy = vi.spyOn(el, 'addEventListener');
    return () => spy.mock.calls.filter((c) => c[0] === 'click').length;
}

describe('dj-navigate binds once', () => {
    it('adds no second listener when the marker is stripped', async () => {
        const dom = await createDom('<a href="/t/" dj-navigate="/t/" id="link">T</a>');
        const link = dom.window.document.getElementById('link');
        const clicksBound = countClickListeners(link);

        stripDataAttributes(link);
        dom.window.djust.navigation.bindDirectives();

        expect(clicksBound()).toBe(0);
    });

    it('adds no listeners across many strip-and-rebind rounds', async () => {
        const dom = await createDom('<a href="/t/" dj-navigate="/t/" id="link">T</a>');
        const link = dom.window.document.getElementById('link');
        const clicksBound = countClickListeners(link);

        for (let i = 0; i < 8; i++) {
            stripDataAttributes(link);
            dom.window.djust.navigation.bindDirectives();
        }

        expect(clicksBound()).toBe(0);
    });

    it('binds a node the morph genuinely replaced exactly once', async () => {
        const dom = await createDom('<a href="/t/" dj-navigate="/t/" id="link">T</a>');
        const root = dom.window.document.querySelector('[dj-root]');
        root.innerHTML = '<a href="/t/" dj-navigate="/t/" id="link">T</a>';
        const fresh = dom.window.document.getElementById('link');
        const clicksBound = countClickListeners(fresh);

        dom.window.djust.navigation.bindDirectives();
        dom.window.djust.navigation.bindDirectives();

        expect(clicksBound()).toBe(1);
    });

    it('treats dj-patch the same way', async () => {
        const dom = await createDom('<a href="?tab=x" dj-patch id="link">T</a>');
        const link = dom.window.document.getElementById('link');
        const clicksBound = countClickListeners(link);

        for (let i = 0; i < 3; i++) {
            stripDataAttributes(link);
            dom.window.djust.navigation.bindDirectives();
        }

        expect(clicksBound()).toBe(0);
    });
});
