/**
 * #2831 — a dotted `dj-keydown.<key>` must actually fire its handler.
 *
 * The framework documents the dotted form as legitimate in two places: the
 * `_warnUnrecognizedDjModifiers` comment in `09-event-binding.js` explicitly
 * lists `dj-keydown.enter` among the "legitimate dotted conventions … which are
 * real key / loading-state modifiers", and `dj-input-modifier-warning-1999`
 * asserts the form produces NO warning. Both are true — and the directive
 * never fires, so the trap is silent twice over.
 *
 * Mechanism: the delegated listener uses `closest('[dj-keydown]')` and the
 * handler reads `getAttribute('dj-keydown')`. A dot is a legal attribute-name
 * character, so `dj-keydown.enter` is ONE literal attribute that neither
 * matches. And because the value read is then always dot-free, the
 * `requiredKey` modifier block beneath is dead code by construction,
 * not merely unreached by one input.
 *
 * The undotted spelling (`dj-keydown="go"`) works, and is asserted here as the
 * control: a fix that broke it would be worse than the bug.
 *
 * `dj-key` is deliberately NOT consulted as a required key. It is the
 * framework's VNode list-identity attribute — the heading
 * "`dj-key` / `data-key` — Stable List Identity" is in
 * `docs/website/advanced/vdom-architecture.md:187`, and the separate gloss
 * "Analogous to React `key`" is in
 * `docs/website/guides/template-cheatsheet.md:495` (two different files; an
 * earlier draft of this comment joined them with an ellipsis and credited the
 * second to the first). Reading it as a keyboard filter silenced handlers on
 * keyed list rows (`<li dj-key="42" dj-keydown="select">`) — a regression the
 * first revision of this fix introduced. The spelling `dj-key="Enter"` occurs
 * nowhere but `tests/js/dj-confirm.test.js`, which passes only because it
 * presses Enter anyway.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const clientCode = readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createTestEnv(bodyHtml) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${bodyHtml}</body></html>`, {
        runScripts: 'dangerously',
        url: 'http://localhost/',
    });

    dom.window.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._testFetchCalls = [];
        window._mockVersion = 0;
        window.fetch = async function(url, opts) {
            window._mockVersion++;
            var eventName = (opts && opts.headers && opts.headers["X-Djust-Event"]) || "";
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch(e) {}
            window._testFetchCalls.push({ eventName: eventName, body: body });
            return { ok: true, json: async function() {
                return { patches: [], version: window._mockVersion };
            } };
        };
    `);
    return dom;
}

function initClient(dom) {
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
}

/** Dispatch a keydown ON the element, as a browser does for the focused one. */
async function press(dom, selector, key) {
    const el = dom.window.document.querySelector(selector);
    el.dispatchEvent(
        new dom.window.KeyboardEvent('keydown', { key, code: key, bubbles: true }),
    );
    // The handler is async (it awaits the fetch); let it settle.
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
}

async function release(dom, selector, key) {
    const el = dom.window.document.querySelector(selector);
    el.dispatchEvent(
        new dom.window.KeyboardEvent('keyup', { key, code: key, bubbles: true }),
    );
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
}

const firedEvents = (dom) => dom.window._testFetchCalls.map((c) => c.eventName);

describe('#2831 dotted dj-keydown modifiers', () => {
    it('dj-keydown.enter fires the handler on Enter', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="name" dj-keydown.enter="go"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#name', 'Enter');

        expect(
            firedEvents(dom),
            'the dotted form is documented as a legitimate convention but never fires',
        ).toContain('go');
    });

    it('dj-keydown.enter does NOT fire on a different key', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="name" dj-keydown.enter="go"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#name', 'a');

        expect(firedEvents(dom)).not.toContain('go');
    });

    it('the undotted control spelling still fires (dj-keydown)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="name" dj-keydown="go"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#name', 'Enter');

        expect(firedEvents(dom)).toContain('go');
    });
    // ── dj-key is NOT a keyboard filter: it is the VNode list-identity
    // attribute (`docs/guides/lists.md`, `schema.py`, the Rust parser). Reading
    // it as a required key silently killed handlers on keyed list rows — a
    // regression the first version of this fix introduced (#2831 review).
    it('a keyed list row still fires (dj-key is list identity, not a key filter)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><ul><li id="row" dj-key="42" dj-keydown="select" tabindex="0"></li></ul></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#row', 'Enter');

        expect(
            firedEvents(dom),
            'dj-key="42" must not be treated as the required key \u2014 the handler has to fire',
        ).toContain('select');
    });

    it('dj-key does not restrict an undotted handler (fires on any key, as before)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="name" dj-keydown="go" dj-key="Enter"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#name', 'a');

        expect(firedEvents(dom)).toContain('go');
    });

    // ── Several dotted bindings on ONE element: both must be reachable. The
    // `dj-keydown.enter` + `dj-keydown.escape` pair is documented in three
    // places, and dispatching only the first attribute left it half-working.
    it('two dotted bindings on one element are both reachable', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="name" dj-keydown.enter="submit" dj-keydown.escape="cancel"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#name', 'Enter');
        expect(firedEvents(dom), 'the .enter binding must fire').toContain('submit');

        await press(dom, '#name', 'Escape');
        expect(firedEvents(dom), 'the .escape binding must fire too').toContain('cancel');
    });

    // ── A binding whose key does not match must not swallow the event: a
    // container-level handler still sees it.
    it('a non-matching dotted binding does not block an outer undotted handler', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView" id="wrap" dj-keydown="parent_any">'
            + '<input id="child" dj-keydown.escape="cancel"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#child', 'a');

        expect(
            firedEvents(dom),
            'the outer catch-all must still receive a key the inner binding does not claim',
        ).toContain('parent_any');
    });

    // ── The rate-limit wrapper caches per element; the matched attribute NAME
    // must be part of that identity, or a morphdom attribute swap on a surviving
    // node dispatches the stale binding and goes permanently dead.
    it('a morph that swaps the binding form does not strand the handler', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="name" dj-keydown.enter="first"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        const el = dom.window.document.querySelector('#name');

        await press(dom, '#name', 'Enter');            // primes the per-element cache
        expect(firedEvents(dom)).toContain('first');

        // morphdom patches a SURVIVING node's attributes rather than replacing it
        el.removeAttribute('dj-keydown.enter');
        el.setAttribute('dj-keydown', 'second');

        await press(dom, '#name', 'a');
        expect(
            firedEvents(dom),
            'after the swap the undotted binding must fire \u2014 the cached name must not stick',
        ).toContain('second');
    });
    // ── Dispatch must match the scoped `dj-window-keydown` delegation, which
    // fires EVERY matching registry entry. A first-match-wins loop made a
    // descendant's binding suppress a container handler that origin/main did
    // fire, and let a bare binding shadow a dotted sibling on its own element.
    it('a matching dotted descendant does not suppress an ancestor handler', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView" dj-keydown="parent_any">'
            + '<input id="child" dj-keydown.enter="child_enter"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#child', 'Enter');

        const fired = firedEvents(dom);
        expect(fired, 'the child\u2019s dotted binding must fire').toContain('child_enter');
        expect(
            fired,
            'origin/main fired parent_any here \u2014 the descendant must not swallow it',
        ).toContain('parent_any');
    });

    it('a bare binding does not shadow a dotted sibling on the same element', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="x" dj-keydown="bare" dj-keydown.enter="dotted"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#x', 'Enter');
        const fired = firedEvents(dom);
        expect(fired, 'the bare catch-all matches Enter').toContain('bare');
        expect(
            fired,
            'a bare binding matches every key, so first-match-wins left the dotted one permanently dead',
        ).toContain('dotted');

        const dom2 = createTestEnv(
            '<div dj-view="app.TestView"><input id="x" dj-keydown="bare" dj-keydown.enter="dotted"></div>',
        );
        initClient(dom2);
        dom2.window.djust.bindLiveViewEvents();
        await press(dom2, '#x', 'a');
        expect(firedEvents(dom2), 'and the dotted one must still filter on other keys').toEqual(['bare']);
    });
    // ── `docs/website/guides/tutorials.md` documents
    // `dj-keydown.right="skip_tutorial"`, and the lookup fell through to the raw
    // name while `e.key` is `ArrowRight` — so the documented modifier was inert.
    it('the documented bare direction word resolves (dj-keydown.right)', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView">'
            + '<div id="tour" dj-keydown.escape="cancel_tour" dj-keydown.right="skip_tour"></div></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        await press(dom, '#tour', 'ArrowRight');
        expect(firedEvents(dom), 'the modifier the tutorial documents must fire').toContain('skip_tour');

        await press(dom, '#tour', 'Escape');
        expect(firedEvents(dom), 'its sibling on the same element must fire too').toContain('cancel_tour');
    });
    // ── The wrapper OWNS rate-limit state: `debounce()` keeps its timer inside
    // the closure it returns, so a cache that rebuilds the wrapper per keystroke
    // defeats `dj-debounce` / `dj-throttle` entirely. An element carrying two
    // bindings alternated variants on every keystroke and did exactly that — N
    // server events for N keystrokes (#2831 review, round 3). The cache is keyed
    // by (element, variant) so each binding keeps ONE persistent wrapper.
    it('dj-debounce still coalesces when an element carries two bindings', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView">'
            + '<input id="x" dj-keydown="filter" dj-keydown.enter="submit" dj-debounce="60"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();

        const el = dom.window.document.querySelector('#x');
        for (let i = 0; i < 3; i++) {
            el.dispatchEvent(new dom.window.KeyboardEvent(
                'keydown', { key: 'Enter', code: 'Enter', bubbles: true },
            ));
        }
        // Let the 60ms debounce window close and the handler settle.
        for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 10));

        const fired = firedEvents(dom);
        expect(
            fired.filter((n) => n === 'filter').length,
            'three keystrokes inside one debounce window must produce ONE filter event',
        ).toBe(1);
        expect(
            fired.filter((n) => n === 'submit').length,
            'and the sibling binding must coalesce to ONE submit event',
        ).toBe(1);
    });
    // ── The two joint-cases the reviewer's round-4 probes covered and the
    // cases above do not. Both pin behaviour that a future change could quietly
    // lose, in the two directions that matter for the (element, variant) cache.
    it('an oscillating morph reuses the cache AND dispatches the fresh value', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="x" dj-keydown.enter="first"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        const el = dom.window.document.querySelector('#x');

        await press(dom, '#x', 'Enter');
        expect(firedEvents(dom), 'the dotted binding fires').toContain('first');

        // enter -> bare: a DIFFERENT variant, so a fresh wrapper is created.
        el.removeAttribute('dj-keydown.enter');
        el.setAttribute('dj-keydown', 'second');
        await press(dom, '#x', 'a');
        expect(firedEvents(dom), 'the bare form fires on any key').toContain('second');

        // bare -> enter: back to the ORIGINAL variant, whose cache entry still
        // exists. Reusing it must not strand the handler at the old value.
        el.removeAttribute('dj-keydown');
        el.setAttribute('dj-keydown.enter', 'third');
        await press(dom, '#x', 'Enter');
        expect(
            firedEvents(dom),
            'the reused wrapper must read the attribute at fire time, not replay its old value',
        ).toContain('third');
    });

    it('dj-debounce="blur" coalesces per binding and does not leak listeners', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView">'
            + '<input id="x" dj-keydown="filter" dj-keydown.enter="submit" dj-debounce="blur"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        const el = dom.window.document.querySelector('#x');

        for (let i = 0; i < 3; i++) {
            el.dispatchEvent(new dom.window.KeyboardEvent(
                'keydown', { key: 'Enter', code: 'Enter', bubbles: true },
            ));
        }
        await new Promise((r) => setTimeout(r, 40));
        expect(
            firedEvents(dom),
            'blur-deferred bindings must not dispatch before the blur',
        ).toEqual([]);

        el.dispatchEvent(new dom.window.Event('blur'));
        for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 10));

        const fired = firedEvents(dom);
        expect(fired.filter((n) => n === 'filter').length, 'one filter, not one per keystroke').toBe(1);
        expect(fired.filter((n) => n === 'submit').length, 'one submit, not one per keystroke').toBe(1);
    });

    it('the keyup path survives the same morph swap as keydown', async () => {
        const dom = createTestEnv(
            '<div dj-view="app.TestView"><input id="x" dj-keyup.enter="first"></div>',
        );
        initClient(dom);
        dom.window.djust.bindLiveViewEvents();
        const el = dom.window.document.querySelector('#x');

        await release(dom, '#x', 'Enter');
        expect(firedEvents(dom), 'the dotted keyup binding fires').toContain('first');

        el.removeAttribute('dj-keyup.enter');
        el.setAttribute('dj-keyup', 'second');
        await release(dom, '#x', 'a');
        expect(
            firedEvents(dom),
            'the keyup twin must not be left dispatching the stale binding',
        ).toContain('second');
    });
});
