/**
 * #3992: the first hook sweep runs before a later script (an official adapter,
 * or the app's own deferred script) has registered its hook. It used to warn
 * "No hook registered" on every load and leave the element unmounted.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

async function boot(readyState) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.Page" dj-root><div id="late" dj-hook="Late"></div></div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/page/' });
    dom.window.eval(`
        window.WebSocket = class {
            static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor() { this.readyState = 1; }
            send() {}
            close() {}
        };
        window.requestAnimationFrame = function(cb) { return setTimeout(cb, 0); };
        window.scrollTo = function() {};
        window.__mounted = 0;
    `);
    const warnings = [];
    for (const k of ['log', 'error', 'debug', 'info']) dom.window.console[k] = () => {};
    dom.window.console.warn = (...args) => warnings.push(args.join(' '));
    // The state a deferred client script runs in: parsed, deferred scripts not all run yet.
    Object.defineProperty(dom.window.document, 'readyState', { configurable: true, get: () => readyState.value });
    dom.window.eval(clientCode);
    await new Promise((resolve) => setTimeout(resolve, 20));
    const missing = () => warnings.filter((w) => w.includes('No hook registered for "Late"'));
    const register = () => dom.window.eval(
        'window.djust.hooks = window.djust.hooks || {};' +
        'window.djust.hooks.Late = { mounted() { window.__mounted += 1; } };'
    );
    const fire = (target, type) => target.dispatchEvent(new dom.window.Event(type));
    return { dom, missing, register, fire };
}

describe('a hook registered after the first sweep (#3992)', () => {
    it('is not warned about during load, and is mounted once the deferred scripts have run', async () => {
        const readyState = { value: 'interactive' };
        const { dom, missing, register, fire } = await boot(readyState);
        expect(missing()).toEqual([]);
        expect(dom.window.__mounted).toBe(0);

        register(); // what an adapter script does when it runs
        fire(dom.window.document, 'DOMContentLoaded');

        expect(dom.window.__mounted).toBe(1);
        expect(missing()).toEqual([]);
    });

    it('is warned about once, after the deferred scripts have run, when it never registers', async () => {
        const readyState = { value: 'interactive' };
        const { dom, missing, fire } = await boot(readyState);
        expect(missing()).toEqual([]);

        fire(dom.window.document, 'DOMContentLoaded');
        fire(dom.window, 'load'); // the second trigger must not sweep or warn again

        expect(missing().length).toBe(1);
    });

    it('is retried on load when DOMContentLoaded had already fired', async () => {
        const readyState = { value: 'interactive' };
        const { dom, missing, register, fire } = await boot(readyState);
        register();
        fire(dom.window, 'load');
        expect(dom.window.__mounted).toBe(1);
        expect(missing()).toEqual([]);
    });

    it('is warned about at once when the document has finished loading', async () => {
        const readyState = { value: 'complete' };
        const { missing } = await boot(readyState);
        expect(missing().length).toBe(1);
    });

    it('a direct mountHooks() call still warns at once', async () => {
        const readyState = { value: 'interactive' };
        const { dom, missing } = await boot(readyState);
        dom.window.djust.mountHooks();
        expect(missing().length).toBe(1);
    });
});
