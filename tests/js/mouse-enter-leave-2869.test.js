/**
 * Integration tests for #2869: dj-mouseenter / dj-mouseleave.
 *
 * These two events DO NOT BUBBLE, so the implementation attaches DIRECT
 * per-element listeners through the scoped-listener machinery
 * (_addScopedListener with the element itself as target) instead of the
 * delegated document-level shape dj-click uses. Everything below runs
 * against the BUILT bundle (client.js) with real DOM event dispatch.
 *
 * Harness: identical to stale-scoped-listeners-2845.test.js — the client
 * runs in JSDOM, DJUST_USE_WEBSOCKET=false forces the HTTP fallback, and
 * the mocked fetch captures every outgoing event frame for assertion.
 *
 * Non-bubbling note: dispatchEvent(new MouseEvent('mouseleave')) on a CHILD
 * can never reach a listener on its PARENT — without bubbling there is no
 * propagation path — which is exactly the platform guarantee the direct-
 * listener route inherits. The nesting test pins the negative: synthetic
 * mouseover/mouseout traffic (what a delegated implementation would key on)
 * and child-targeted mouseleave both leave the parent's handlers silent.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body>${bodyHtml}</body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' }
    );

    dom.window.eval(`
        window.WebSocket = class {
            constructor() { this.readyState = 0; }
            send() {}
            close() {}
        };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        Object.defineProperty(document, 'hidden', {
            value: false, writable: true, configurable: true
        });

        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() { return { patches: [], version: window._mockVersion }; } };
        };
    `);

    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    dom.window.djust.bindLiveViewEvents();
}

const getFetchCalls = (dom) => dom.window._testFetchCalls;
const eventNames = (dom) => getFetchCalls(dom).map((c) => c.eventName);
const flush = () => new Promise((r) => setTimeout(r, 50));

const fireMouse = (dom, el, type) => {
    el.dispatchEvent(new dom.window.MouseEvent(type));
};

describe('#2869: dj-mouseenter / dj-mouseleave wiring', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('fires the enter handler when mouseenter is dispatched on the element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="card" dj-mouseenter="highlight">card</div></div>'
        );
        initClient(dom);

        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        await flush();

        expect(eventNames(dom)).toEqual(['highlight']);
    });

    it('fires the leave handler when mouseleave is dispatched on the element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="card" dj-mouseleave="dim">card</div></div>'
        );
        initClient(dom);

        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseleave');
        await flush();

        expect(eventNames(dom)).toEqual(['dim']);
    });

    it('carries data-* params and inline args in the frame', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V">'
            + '<div id="card" dj-mouseenter="preview(7)" data-id:int="42" data-section="alpha">card</div>'
            + '</div>'
        );
        initClient(dom);

        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        await flush();

        expect(getFetchCalls(dom).length).toBe(1);
        expect(getFetchCalls(dom)[0].body._args).toEqual([7]);
        expect(getFetchCalls(dom)[0].body.id).toBe(42);
        expect(getFetchCalls(dom)[0].body.section).toBe('alpha');
    });

    it('routes the event to the embedded child view (view_id from the stamp)', async () => {
        // An element carrying dj-mouseenter INSIDE an embedded child — the
        // data-djust-embedded attribute is what {% live_render %} stamps on
        // event-bearing elements (the stamp this PR keeps truthful).
        const dom = createTestEnv(
            '<div dj-view="app.Parent">'
            + '<div dj-view="app.Child" data-djust-embedded="child_1">'
            + '<div id="row" dj-mouseenter="highlight_row">row</div>'
            + '</div>'
            + '</div>'
        );
        initClient(dom);

        fireMouse(dom, dom.window.document.getElementById('row'), 'mouseenter');
        await flush();

        expect(eventNames(dom)).toEqual(['highlight_row']);
        expect(getFetchCalls(dom)[0].body.view_id).toBe('child_1');
    });

    it('does NOT double-fire across repeated bind passes', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="card" dj-mouseenter="highlight" dj-mouseleave="dim">card</div></div>'
        );
        initClient(dom);

        // Bind passes run on every patch; three in a row must not stack
        // listeners. Idempotency comes from the #2845 skip-on-unchanged-value
        // rule, the same shape dj-shortcut / dj-click-away use.
        dom.window.djust.bindLiveViewEvents();
        dom.window.djust.bindLiveViewEvents();

        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseleave');
        await flush();

        expect(eventNames(dom)).toEqual(['highlight', 'dim']);
    });

    it('rebuilds on a value change under a surviving element (#2845 rule)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="card" dj-mouseenter="highlight">card</div></div>'
        );
        initClient(dom);
        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        await flush();
        expect(eventNames(dom)).toEqual(['highlight']);

        // Server re-render rewrites the VALUE on the surviving node.
        dom.window.document.getElementById('card').setAttribute('dj-mouseenter', 'renamed');
        dom.window.djust.bindLiveViewEvents();

        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        await flush();

        // Pre-rebuild this dispatched highlight a second time (stale closure).
        expect(eventNames(dom)).toEqual(['highlight', 'renamed']);
    });

    it('stops firing after the template drops the attribute (sweep eviction)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V"><div id="card" dj-mouseenter="highlight">card</div></div>'
        );
        initClient(dom);
        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        await flush();
        expect(eventNames(dom)).toEqual(['highlight']);

        dom.window.document.getElementById('card').removeAttribute('dj-mouseenter');
        dom.window.djust.bindLiveViewEvents();

        fireMouse(dom, dom.window.document.getElementById('card'), 'mouseenter');
        await flush();

        // The #2832 sweep judged the listener stale (attribute dropped) and
        // detached it — nothing may fire.
        expect(eventNames(dom)).toEqual(['highlight']);
    });

    it('nesting: a child transition never fires the parent directives', async () => {
        // The parent carries BOTH directives; the child carries none. Simulate
        // the event traffic a real browser produces when the pointer moves
        // from the parent into the child — mouseover/mouseout DO fire (and
        // bubble) on that transition, mouseleave on the parent does NOT —
        // and assert no parent frame appears. A delegated mouseover/mouseout
        // implementation would fail the mouseover/mouseout assertions here.
        const dom = createTestEnv(
            '<div dj-view="app.V">'
            + '<div id="parent" dj-mouseenter="parent_enter" dj-mouseleave="parent_leave">'
            + '<div id="child">child</div>'
            + '</div>'
            + '</div>'
        );
        initClient(dom);

        const parent = dom.window.document.getElementById('parent');
        const child = dom.window.document.getElementById('child');

        // Pointer crosses into the child: browser fires mouseout on parent
        // (bubbling) and mouseover on child. Neither directive may fire —
        // the pointer has not LEFT the parent for enter/leave purposes, and
        // the client registers no mouseover/mouseout listeners (a delegated
        // implementation would fail these two dispatches).
        child.dispatchEvent(new dom.window.MouseEvent('mouseover', { bubbles: true }));
        parent.dispatchEvent(new dom.window.MouseEvent('mouseout', { bubbles: true }));
        // The UA dispatches mouseleave NON-bubbling, so a child-targeted
        // mouseleave has no propagation path to the parent's listener —
        // constructed here exactly as the UA emits it (bubbles: false,
        // the MouseEvent default).
        child.dispatchEvent(new dom.window.MouseEvent('mouseleave'));
        await flush();

        expect(eventNames(dom)).toEqual([]);

        // The pointer genuinely leaves the parent: the real event fires once.
        fireMouse(dom, parent, 'mouseleave');
        await flush();
        expect(eventNames(dom)).toEqual(['parent_leave']);
    });

    it('nested declaring elements each dispatch their own handler', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.V">'
            + '<div id="parent" dj-mouseenter="parent_enter">'
            + '<div id="child" dj-mouseenter="child_enter">child</div>'
            + '</div>'
            + '</div>'
        );
        initClient(dom);

        // Entering the child fires only the child's enter (non-bubbling).
        fireMouse(dom, dom.window.document.getElementById('child'), 'mouseenter');
        await flush();
        expect(eventNames(dom)).toEqual(['child_enter']);

        // Entering the parent fires only the parent's.
        fireMouse(dom, dom.window.document.getElementById('parent'), 'mouseenter');
        await flush();
        expect(eventNames(dom)).toEqual(['child_enter', 'parent_enter']);
    });
});
