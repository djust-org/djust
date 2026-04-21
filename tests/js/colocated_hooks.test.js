/**
 * Tests for colocated JS hooks — <script type="djust/hook"> tags emitted by
 * the {% colocated_hook %} template tag are extracted and registered into
 * window.djust.hooks at init time and after each VDOM morph.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${bodyHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

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

    dom.window.eval(clientCode);
    // Ensure djustInit() runs: jsdom defaults to readyState='loading' so
    // client.js registers DOMContentLoaded; fire it explicitly.
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

describe('colocated hooks', () => {
    let dom;

    it('exposes window.djust.extractColocatedHooks after init', () => {
        dom = createDom('');
        expect(typeof dom.window.djust.extractColocatedHooks).toBe('function');
    });

    it('auto-registers hooks present at page load', () => {
        dom = createDom(
            '<script type="djust/hook" data-hook="Chart">' +
            'hook.mounted = function() { return 1; };' +
            '</script>'
        );
        expect(dom.window.djust.hooks).toBeTruthy();
        expect(typeof dom.window.djust.hooks.Chart).toBe('object');
        expect(typeof dom.window.djust.hooks.Chart.mounted).toBe('function');
        expect(dom.window.djust.hooks.Chart.mounted()).toBe(1);
    });

    it('is idempotent — rerunning extraction does not re-register', () => {
        dom = createDom(
            '<script type="djust/hook" data-hook="Widget">' +
            'hook.mounted = function() { return "first"; };' +
            '</script>'
        );
        // Mutate the registered hook; rerunning extraction must NOT overwrite.
        dom.window.djust.hooks.Widget.mounted = function () { return 'mutated'; };
        dom.window.djust.extractColocatedHooks(dom.window.document);
        expect(dom.window.djust.hooks.Widget.mounted()).toBe('mutated');

        // Sentinel attribute marks registered scripts
        const scriptEl = dom.window.document.querySelector('script[type="djust/hook"]');
        expect(scriptEl.dataset.djustHookRegistered).toBe('1');
    });

    it('malformed hook body does not crash the page', () => {
        dom = createDom(
            '<script type="djust/hook" data-hook="Broken">' +
            'hook.mounted = function() { syntax error here };' +
            '</script>'
        );
        // Init should have completed; sentinel reflects the error.
        const scriptEl = dom.window.document.querySelector('script[type="djust/hook"]');
        expect(scriptEl.dataset.djustHookRegistered).toBe('error');
        // Page still initialized.
        expect(dom.window.djustInitialized).toBe(true);
    });

    it('<script type="djust/hook"> without data-hook is skipped safely', () => {
        dom = createDom(
            '<script type="djust/hook">' +
            'hook.mounted = function() { return 1; };' +
            '</script>'
        );
        // No crash; the anonymous script is marked as processed but nothing
        // is registered under an undefined/empty key.
        const hooks = dom.window.djust.hooks || {};
        expect(hooks['']).toBeUndefined();
        expect(hooks.undefined).toBeUndefined();
        // Page initialized successfully.
        expect(dom.window.djustInitialized).toBe(true);
        // Script sentinel set so we don't re-scan.
        const scriptEl = dom.window.document.querySelector('script[type="djust/hook"]');
        expect(scriptEl.dataset.djustHookRegistered).toBe('1');
    });

    it('namespaced hook name (data-hook with dots) registers under exact key', () => {
        dom = createDom(
            '<script type="djust/hook" data-hook="myapp.views.DashboardView.Chart">' +
            'hook.mounted = function() { return "ns"; };' +
            '</script>'
        );
        expect(dom.window.djust.hooks['myapp.views.DashboardView.Chart']).toBeTruthy();
        expect(
            dom.window.djust.hooks['myapp.views.DashboardView.Chart'].mounted()
        ).toBe('ns');
    });

    it('picks up hooks inserted after init (simulated DOM morph)', () => {
        dom = createDom('');
        const doc = dom.window.document;
        // Before morph: LateHook is not registered (framework built-ins like
        // CursorOverlay may be present — assert absence of our specific key).
        expect(dom.window.djust.hooks && dom.window.djust.hooks.LateHook).toBeFalsy();

        // Simulate a VDOM patch adding a colocated hook <script> block
        const container = doc.querySelector('[dj-view]');
        const script = doc.createElement('script');
        script.setAttribute('type', 'djust/hook');
        script.setAttribute('data-hook', 'LateHook');
        script.textContent = 'hook.mounted = function() { return "late"; };';
        container.appendChild(script);

        // reinitAfterDOMUpdate() calls extractColocatedHooks() internally
        dom.window.djust.reinitAfterDOMUpdate(container);
        expect(dom.window.djust.hooks.LateHook).toBeTruthy();
        expect(dom.window.djust.hooks.LateHook.mounted()).toBe('late');
    });
});
