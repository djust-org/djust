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
import { vi } from 'vitest';
import { installLoadingReadyState } from './_jsdom_ready_state.js';

vi.mock('jsdom', async (importOriginal) => {
    const actual = await importOriginal();
    class JSDOM extends actual.JSDOM {
        constructor(...args) {
            super(...args);
            installLoadingReadyState(this.window);
        }
    }
    return { ...actual, JSDOM, default: { ...(actual.default || actual), JSDOM } };
});
