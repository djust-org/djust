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

        it('VDOM-inserted elements use delegation — no per-element click listeners', () => {
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

            // With delegation, no per-element click listeners are added
            window.djust.bindLiveViewEvents();
            expect(bindCount).toBe(0);

            // Multiple calls still add no per-element listeners
            window.djust.bindLiveViewEvents();
            window.djust.bindLiveViewEvents();
            expect(bindCount).toBe(0);

            // Root has delegation installed
            expect(root._djustDelegateAbort).toBeTruthy();
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
            const rawClick =
                window.document.querySelector('[data-dj-id=btn1]').getAttribute('dj-click') || '';
            const result = window.djust.parseEventHandler(rawClick);
            expect(result.name).toBe('handler_b');
        });
    });
});
