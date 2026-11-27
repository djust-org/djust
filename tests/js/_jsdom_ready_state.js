/**
 * jsdom 31 compatibility shim (#4247).
 *
 * jsdom <= 30 reported `document.readyState === 'loading'` for a freshly built
 * `new JSDOM(html, { runScripts: 'dangerously' })` until the (async) load
 * finished. jsdom 31 reports 'interactive' straight away. The client bundle
 * (14-init.js) picks its init path from `readyState`: while 'loading' it binds
 * djustInit to DOMContentLoaded (which the harnesses then dispatch by hand);
 * otherwise it defers djustInit to a microtask (#2185), which runs only after
 * the synchronous harness has already looked for the initialised client.
 *
 * Browsers still give a parsing document 'loading', so that is the state the
 * harnesses mean to simulate. Report 'loading' until DOMContentLoaded (or the
 * window's `load` event), then fall back to jsdom's own value.
 */

/**
 * Make a fresh JSDOM document report 'loading' until DOMContentLoaded / load.
 * Shared by every setup file that mocks 'jsdom' (vi.mock replaces, never
 * composes, so a second mock would otherwise silently drop the shim).
 */
export function installLoadingReadyState(window) {
    const doc = window.document;
    if (doc.readyState === 'complete') return;
    const loading = () => 'loading';
    Object.defineProperty(doc, 'readyState', { configurable: true, get: loading });
    // A browser leaves 'loading' at DOMContentLoaded, so code that
    // runs after it (a lazily loaded chunk, #4320) never waits for a
    // DOMContentLoaded that already fired.
    const done = () => {
        // A test may have installed its own readyState; leave that alone.
        const own = Object.getOwnPropertyDescriptor(doc, 'readyState');
        if (own && own.get === loading) delete doc.readyState;
    };
    doc.addEventListener('DOMContentLoaded', done, { once: true });
    window.addEventListener('load', done, { once: true });
}
