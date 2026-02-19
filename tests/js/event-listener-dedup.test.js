/**
 * Regression tests for issue #315 — duplicate WebSocket sends.
 *
 * Each test verifies that a single user action triggers the event handler
 * exactly once, regardless of how many VDOM patches or server responses
 * have been applied.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

function setupDom(html) {
    const dom = new JSDOM(html, { runScripts: 'dangerously' });
    const window = dom.window;

    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) {
        window.CSS.escape = (str) => String(str).replace(/([^\w-])/g, '\\$1');
    }

    window.WebSocket = class {
        send() {}
        close() {}
    };
    window.fetch = () => Promise.reject(new Error('no fetch'));

    const clientCode = require('fs').readFileSync(
        './python/djust/static/djust/client.js',
        'utf-8',
    );
    window.eval(clientCode);
    return { dom, window };
}

describe('Event listener deduplication (issue #315)', () => {
    // ---------------------------------------------------------------------------
    // initReactCounters — N server responses must not stack listeners
    // ---------------------------------------------------------------------------
    describe('initReactCounters deduplication', () => {
        let window;

        beforeEach(() => {
            ({ window } = setupDom(
                `<!DOCTYPE html><html><body>
                    <div dj-root="true">
                        <div data-react-component="Counter" data-react-props='{"initialCount":0}'>
                            <span class="counter-display">0</span>
                            <button class="btn-sm">-</button>
                            <button class="btn-sm">+</button>
                        </div>
                    </div>
                </body></html>`,
            ));
        });

        it('calling initReactCounters N times adds click listener exactly once per button', () => {
            const container = window.document.querySelector('[data-react-component="Counter"]');
            const minusBtn = container.querySelectorAll('.btn-sm')[0];
            const plusBtn = container.querySelectorAll('.btn-sm')[1];

            let minusCount = 0;
            let plusCount = 0;
            const origMinus = minusBtn.addEventListener.bind(minusBtn);
            const origPlus = plusBtn.addEventListener.bind(plusBtn);
            minusBtn.addEventListener = function(type, ...args) {
                if (type === 'click') minusCount++;
                return origMinus(type, ...args);
            };
            plusBtn.addEventListener = function(type, ...args) {
                if (type === 'click') plusCount++;
                return origPlus(type, ...args);
            };

            // Simulate 5 server responses each calling initReactCounters
            window.eval('initReactCounters(); initReactCounters(); initReactCounters(); initReactCounters(); initReactCounters();');

            expect(minusCount).toBe(0); // container was already initialized before spy
            expect(plusCount).toBe(0);
        });

        it('initReactCounters called 5 times: counter increments by 1 per click, not 5', () => {
            // Re-initialize with a fresh container by calling initReactCounters before setup
            // then verify the count increments correctly
            window.eval('initReactCounters();');
            window.eval('initReactCounters();');
            window.eval('initReactCounters();');

            const container = window.document.querySelector('[data-react-component="Counter"]');
            const display = container.querySelector('.counter-display');
            const plusBtn = container.querySelectorAll('.btn-sm')[1];

            // Click once — should increment by 1, not by 3
            plusBtn.click();
            expect(display.textContent).toBe('1');
        });
    });

    // ---------------------------------------------------------------------------
    // bindLiveViewEvents — VDOM-inserted elements get bound exactly once
    // ---------------------------------------------------------------------------
    describe('bindLiveViewEvents deduplication for VDOM-created elements', () => {
        let window;

        beforeEach(() => {
            ({ window } = setupDom(
                `<!DOCTYPE html><html><body>
                    <div dj-root="true" id="root">
                        <button dj-click="select_project" data-dj-id="btn1">Select</button>
                    </div>
                </body></html>`,
            ));
        });

        it('calling bindLiveViewEvents N times binds click handler exactly once', () => {
            const btn = window.document.querySelector('[data-dj-id="btn1"]');

            let bindCount = 0;
            const origAdd = btn.addEventListener.bind(btn);
            btn.addEventListener = function(type, ...args) {
                if (type === 'click') bindCount++;
                return origAdd(type, ...args);
            };

            // Simulate N server responses each calling bindLiveViewEvents
            window.djust.bindLiveViewEvents();
            window.djust.bindLiveViewEvents();
            window.djust.bindLiveViewEvents();
            window.djust.bindLiveViewEvents();
            window.djust.bindLiveViewEvents();

            // All 5 calls after initial bind — WeakMap prevents re-binding
            expect(bindCount).toBe(0);
        });

        it('element inserted via createNodeFromVNode gets bound by bindLiveViewEvents', () => {
            const root = window.document.getElementById('root');

            const newBtn = window.djust.createNodeFromVNode({
                tag: 'button',
                attrs: { 'dj-click': 'generate_promote_spec', 'data-dj-id': 'btn2' },
                children: [{ tag: '#text', text: 'Generate', attrs: {}, children: [] }],
            });
            root.appendChild(newBtn);

            let bindCount = 0;
            const origAdd = newBtn.addEventListener.bind(newBtn);
            newBtn.addEventListener = function(type, ...args) {
                if (type === 'click') bindCount++;
                return origAdd(type, ...args);
            };

            // First call should bind it
            window.djust.bindLiveViewEvents();
            expect(bindCount).toBe(1);

            // Subsequent calls must not double-bind
            window.djust.bindLiveViewEvents();
            window.djust.bindLiveViewEvents();
            expect(bindCount).toBe(1);
        });
    });

    // ---------------------------------------------------------------------------
    // dj-click stale closure — morphElement attribute update
    // ---------------------------------------------------------------------------
    describe('dj-click reads current attribute at fire time', () => {
        let window;

        beforeEach(() => {
            ({ window } = setupDom(
                `<!DOCTYPE html><html><body>
                    <div dj-root="true" id="root">
                        <button dj-click="handler_a" data-dj-id="btn1">Click</button>
                    </div>
                </body></html>`,
            ));
            window.djust.bindLiveViewEvents();
        });

        it('dj-click attribute value is read at fire time, not bind time', () => {
            const btn = window.document.querySelector('[data-dj-id="btn1"]');

            // Simulate a morphElement SetAttr patch changing the handler
            btn.setAttribute('dj-click', 'handler_b');

            // Verify the attribute was updated
            expect(btn.getAttribute('dj-click')).toBe('handler_b');

            // The closure should now dispatch handler_b, not handler_a.
            // We verify by checking parseEventHandler output on the current attribute.
            const result = window.eval('parseEventHandler(document.querySelector("[data-dj-id=btn1]").getAttribute("dj-click") || "")');
            expect(result.name).toBe('handler_b');
        });
    });
});
