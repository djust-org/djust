/**
 * Shared JSDOM test helpers.
 *
 * Consolidates the boilerplate that several test files independently
 * re-implemented (#953):
 *   - `createDom(html)` — returns a JSDOM instance with `client.js` evaluated
 *     inside it, a `MockWebSocket` wired up, and `DOMContentLoaded` fired.
 *   - `nextFrame(dom?)` — 20 ms (or caller-chosen) await to let
 *     requestAnimationFrame-backed work settle under jsdom.
 *   - `fireDomContentLoaded(dom)` — dispatches a synthetic `DOMContentLoaded`
 *     `Event` for modules whose observers only install on that signal.
 *   - `makeMessageEvent(source, data)` — builds a MessageEvent-shaped plain
 *     object with `source`/`type`/`url` back-filled (matches the pattern
 *     service-worker tests rely on after PR #925).
 *   - `mountAndWait(html, ms?)` — convenience wrapper: createDom +
 *     fireDomContentLoaded (already done inside createDom) + `nextFrame()`.
 *
 * IMPORTANT: this file lives under `tests/js/` and starts with an
 * underscore, so it is not concatenated into the production client bundle
 * (`scripts/build-client.sh` globs `src/[0-9]*.js` only).
 */

import { JSDOM } from 'jsdom';
import fs from 'fs';

const CLIENT_SRC_PATH = './python/djust/static/djust/client.js';

// Cache the client source so we don't re-read it on every createDom() call.
// vitest spins up multiple describe blocks per file — in aggregate the
// redundant reads add up.
let _clientCodeCache = null;
function getClientCode() {
    if (_clientCodeCache == null) {
        _clientCodeCache = fs.readFileSync(CLIENT_SRC_PATH, 'utf-8');
    }
    return _clientCodeCache;
}

/**
 * Build a JSDOM instance with `client.js` evaluated inside it, a minimal
 * MockWebSocket class installed, and `DOMContentLoaded` fired.
 *
 * @param {string} bodyHtml — optional markup injected inside the
 *   `[dj-view][dj-root]` host element.
 * @param {object} [opts]
 * @param {string} [opts.url] — override document URL (default
 *   `http://localhost/`).
 * @param {string} [opts.view] — override the `dj-view` attribute value
 *   (default `test.V`).
 * @returns {JSDOM}
 */
export function createDom(bodyHtml = '', opts = {}) {
    const url = opts.url || 'http://localhost/';
    const view = opts.view || 'test.V';
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
            <div dj-view="${view}" dj-root>${bodyHtml}</div>
        </body></html>`,
        { runScripts: 'dangerously', url }
    );
    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(getClientCode());
    fireDomContentLoaded(dom);
    return dom;
}

/**
 * Dispatch a synthetic `DOMContentLoaded` event on the document. Some
 * client modules install their observers only on this signal; in tests
 * we fire it manually once the window's been wired up.
 *
 * @param {JSDOM} dom
 */
export function fireDomContentLoaded(dom) {
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

/**
 * Wait long enough for rAF-backed work to flush under jsdom.
 *
 * jsdom backs requestAnimationFrame via `setTimeout(cb, 16)`, and under
 * vitest's parallel load that 16 ms can stretch. 20 ms is the shortest
 * reliably-flushing default; pass a larger `ms` if your test does
 * multi-phase work (see `dj-transition` tests that use 60 ms).
 *
 * @param {JSDOM} [dom] — pass a jsdom instance to use its timers;
 *   omitting it uses the ambient `setTimeout`.
 * @param {number} [ms=20]
 */
export function nextFrame(dom, ms = 20) {
    const timer = dom && dom.window ? dom.window.setTimeout : setTimeout;
    return new Promise((resolve) => timer(resolve, ms));
}

/**
 * Build a MessageEvent-shaped plain object. Back-fills the SW-style
 * `type`/`url` fields on the source so tests written after PR #925's
 * origin check don't have to repeat that boilerplate.
 *
 * @param {object} source — the `event.source` (typically a mocked
 *   WindowClient with `postMessage`). Pass `null` to simulate an
 *   origin-less message.
 * @param {any} data — the `event.data` payload.
 * @returns {{ data: any, source: object|null }}
 */
export function makeMessageEvent(source, data) {
    if (source && typeof source === 'object') {
        if (!source.type) source.type = 'window';
        if (!source.url) source.url = 'http://localhost:8000/page';
    }
    return { data, source };
}

/**
 * One-liner for the common flow: mount the DOM, fire DOMContentLoaded
 * (already done by createDom), then await one frame so module observers
 * have processed the initial tree.
 *
 * @param {string} bodyHtml
 * @param {number} [ms=20]
 * @returns {Promise<JSDOM>}
 */
export async function mountAndWait(bodyHtml = '', ms = 20) {
    const dom = createDom(bodyHtml);
    await nextFrame(dom, ms);
    return dom;
}
