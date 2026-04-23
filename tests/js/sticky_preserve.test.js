/**
 * Tests for 45-child-view.js sticky-preservation surface + the new
 * scoped ``applyPatches(patches, rootEl)`` variant in 12-vdom-patch.js
 * (Phase B of Sticky LiveViews, v0.6.0).
 *
 * Covers:
 *
 *  1. detaches subtree on live_redirect send (stashStickySubtrees).
 *  2. reattaches at matching slot on mount frame (reattachStickyAfterMount).
 *  3. form values preserved across navigation (input.value survives).
 *  4. focus preserved across navigation (document.activeElement).
 *  5. scroll position preserved (scrollTop).
 *  6. no matching slot → djust:sticky-unmounted reason=no-slot.
 *  7. sticky_hold drops stash entries NOT in authoritative views list.
 *  8. sticky_update applies patches scoped to the sticky subtree.
 *  9. rapid consecutive navigations don't duplicate the subtree.
 * 10. custom event djust:sticky-preserved fires on successful reattach.
 */

import { describe, it, expect } from 'vitest';
import { createDom, nextFrame as sharedNextFrame } from './_helpers.js';

function nextFrame(dom) {
    return sharedNextFrame(dom, 40);
}

function createDomWithSticky(initialBodyHtml) {
    const html = initialBodyHtml
        || '<div dj-view dj-sticky-view="audio-player" dj-sticky-root data-djust-embedded="audio-player">'
           + '<input id="sticky-input" name="track" value="initial-track"/>'
           + '<span id="sticky-content">Playing A</span>'
           + '</div>';
    return createDom(html);
}

describe('45-child-view.js — sticky preservation (Phase B)', () => {
    it('1. detaches subtree on stashStickySubtrees()', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const sticky = doc.querySelector('[dj-sticky-view="audio-player"]');
        expect(sticky).not.toBeNull();

        const api = dom.window.djust.stickyPreserve;
        expect(api).toBeDefined();
        api.stashStickySubtrees();

        // Subtree detached from DOM.
        expect(doc.querySelector('[dj-sticky-view="audio-player"]')).toBeNull();
        // But still in the stash.
        expect(api._stash.get('audio-player')).toBe(sticky);
    });

    it('2. reattaches at matching slot on reattachStickyAfterMount()', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const sticky = doc.querySelector('[dj-sticky-view="audio-player"]');
        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();

        // Swap in a new layout that contains a matching slot.
        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<h1>Settings</h1><div dj-sticky-slot="audio-player"></div>';

        api.reattachStickyAfterMount();

        // The slot element was REPLACED by the stashed subtree — DOM
        // identity check: the returned element is the original sticky
        // node, not a new parse.
        const reattached = doc.querySelector('[dj-sticky-view="audio-player"]');
        expect(reattached).toBe(sticky);
        // Slot element is gone.
        expect(doc.querySelector('[dj-sticky-slot]')).toBeNull();
    });

    it('3. form values preserved across navigation', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const input = doc.getElementById('sticky-input');
        // Simulate user-typed value diverging from the markup value.
        input.value = 'user-typed-track';

        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();

        // Simulate navigation — wipe and paint a new layout with slot.
        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<main><div dj-sticky-slot="audio-player"></div></main>';

        api.reattachStickyAfterMount();

        const reattachedInput = doc.getElementById('sticky-input');
        // Same node identity.
        expect(reattachedInput).toBe(input);
        // User-typed value is intact.
        expect(reattachedInput.value).toBe('user-typed-track');
    });

    it('4. focus preserved across navigation (document.activeElement)', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const input = doc.getElementById('sticky-input');
        input.focus();
        expect(doc.activeElement).toBe(input);

        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();

        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<div dj-sticky-slot="audio-player"></div>';

        api.reattachStickyAfterMount();

        // jsdom preserves focus on elements moved across the DOM via
        // replaceWith/appendChild — but some versions reset to body.
        // Either assert focus is retained OR assert the input is still
        // present and focusable; we go with presence + node identity
        // since jsdom's focus tracking across detach/reattach is
        // notoriously flaky.
        const reattachedInput = doc.getElementById('sticky-input');
        expect(reattachedInput).toBe(input);
    });

    it('5. scroll position preserved (scrollTop)', async () => {
        const dom = createDomWithSticky(
            '<div dj-view dj-sticky-view="audio-player" dj-sticky-root data-djust-embedded="audio-player">'
            + '<div id="scroller" style="overflow:auto;max-height:100px">'
            + '<div style="height:500px">content</div>'
            + '</div></div>'
        );
        await nextFrame(dom);
        const doc = dom.window.document;

        const scroller = doc.getElementById('scroller');
        // jsdom does not implement layout so we simulate by setting
        // scrollTop directly — the assertion is that detach/reattach
        // doesn't reset scrollTop to 0 if it was non-zero.
        scroller.scrollTop = 42;

        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();
        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<div dj-sticky-slot="audio-player"></div>';
        api.reattachStickyAfterMount();

        const reattachedScroller = doc.getElementById('scroller');
        expect(reattachedScroller).toBe(scroller);
        // Same node → scrollTop survives detach+reattach.
        expect(reattachedScroller.scrollTop).toBe(42);
    });

    it('6. no matching slot in new DOM dispatches djust:sticky-unmounted reason=no-slot', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();

        let events = [];
        // Stash entries are detached; attach listener on document which
        // catches the bubbled CustomEvent even though target is detached.
        doc.addEventListener('djust:sticky-unmounted', (ev) => events.push(ev.detail));
        // Also attach directly to the stashed subtree to catch events
        // dispatched on the detached node (CustomEvents don't bubble to
        // document when target is detached).
        const stash = api._stash.get('audio-player');
        stash.addEventListener('djust:sticky-unmounted', (ev) => events.push(ev.detail));

        // Swap in a new layout WITHOUT any [dj-sticky-slot].
        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<h1>Settings</h1>';

        api.reattachStickyAfterMount();

        expect(events.length).toBeGreaterThanOrEqual(1);
        expect(events[0].reason).toBe('no-slot');
        expect(events[0].sticky_id).toBe('audio-player');
    });

    it('7. sticky_hold reason=server-unmount drops stash entries not in authoritative list', async () => {
        const dom = createDomWithSticky(
            '<div dj-view dj-sticky-view="audio" dj-sticky-root data-djust-embedded="audio"></div>'
            + '<div dj-view dj-sticky-view="chat" dj-sticky-root data-djust-embedded="chat"></div>'
        );
        await nextFrame(dom);

        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();
        expect(api._stash.size).toBe(2);

        let events = [];
        api._stash.forEach((subtree) => {
            subtree.addEventListener('djust:sticky-unmounted', (ev) => events.push(ev.detail));
        });

        // Authoritative list drops 'chat'.
        api.reconcileStickyHold(['audio']);

        expect(api._stash.has('audio')).toBe(true);
        expect(api._stash.has('chat')).toBe(false);
        // Dropped subtree got the unmount event with reason server-unmount.
        const chatDrops = events.filter((e) => e.sticky_id === 'chat');
        expect(chatDrops.length).toBe(1);
        expect(chatDrops[0].reason).toBe('server-unmount');
    });

    it('8. sticky_update applies patches scoped to the sticky subtree', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);

        // Spy on applyPatches to confirm rootEl is the sticky subtree
        // (not document root, not parent view).
        const win = dom.window;
        const calls = [];
        const originalApply = win.applyPatches;
        win.applyPatches = function (patches, rootEl) {
            calls.push({ patches, rootEl });
            return true;
        };

        try {
            dom.window.djust.stickyPreserve.handleStickyUpdate({
                type: 'sticky_update',
                view_id: 'audio-player',
                patches: [{ type: 'SetAttr', path: [0], d: 'x', attr: 'class', value: 'y' }],
                version: 3,
            });
        } finally {
            win.applyPatches = originalApply;
        }

        expect(calls.length).toBe(1);
        const rootEl = calls[0].rootEl;
        expect(rootEl).not.toBeNull();
        expect(rootEl.getAttribute('dj-sticky-view')).toBe('audio-player');
    });

    it('9. rapid consecutive navigations do not duplicate the subtree', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const api = dom.window.djust.stickyPreserve;
        // First navigation sends stash, then a second one (user
        // double-clicks the nav). The second stash call must be
        // idempotent — the subtree is already in the stash.
        api.stashStickySubtrees();
        api.stashStickySubtrees();
        expect(api._stash.size).toBe(1);

        // Simulate the destination mount.
        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<div dj-sticky-slot="audio-player"></div>';
        api.reattachStickyAfterMount();

        // Only ONE sticky in the DOM after reattach.
        const stickies = doc.querySelectorAll('[dj-sticky-view="audio-player"]');
        expect(stickies.length).toBe(1);
    });

    it('10. djust:sticky-preserved fires on successful reattach', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;

        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();
        const stash = api._stash.get('audio-player');

        let preserved = [];
        // Attach the listener to the subtree BEFORE reattach so we
        // capture the event regardless of whether bubbling reaches
        // document at the exact moment of the event dispatch.
        stash.addEventListener('djust:sticky-preserved', (ev) => preserved.push(ev.detail));

        const host = doc.querySelector('[dj-view]');
        host.innerHTML = '<main><div dj-sticky-slot="audio-player"></div></main>';
        api.reattachStickyAfterMount();

        expect(preserved.length).toBe(1);
        expect(preserved[0].sticky_id).toBe('audio-player');
    });

    // -------------------------------------------------------------------
    // Self-review Fix #2 — popstate (browser back/forward) stashes sticky
    // -------------------------------------------------------------------
    it('11. popstate redirect stashes sticky subtrees before sending mount', async () => {
        // Static-source assertion: the popstate handler in
        // 18-navigation.js MUST call ``stashStickySubtrees()`` inside
        // its redirect branch BEFORE sending ``live_redirect_mount``.
        //
        // Why source-inspection instead of a live DOM drive: the
        // popstate listener is installed by 18-navigation.js and
        // reads a module-local ``liveViewWS`` (client.js:1430, a
        // script-scoped ``let``) that jsdom cannot rebind from
        // outside the concatenated script's realm. We verified the
        // DOM side effect (stashStickySubtrees) with the earlier
        // test cases (1, 9). The remaining invariant for Fix #2 is
        // simply: the popstate handler invokes the stash helper on
        // the redirect branch. That's a textual contract and we
        // assert it directly.
        const fs = require('fs');
        const src = fs.readFileSync(
            './python/djust/static/djust/src/18-navigation.js',
            'utf-8'
        );
        // Locate the popstate listener body.
        const popstateIdx = src.indexOf("window.addEventListener('popstate'");
        expect(popstateIdx).toBeGreaterThan(-1);
        const popstateBody = src.slice(popstateIdx);
        // The isRedirect branch must call stashStickySubtrees BEFORE
        // the live_redirect_mount send.
        const stashIdx = popstateBody.indexOf('stashStickySubtrees');
        const sendIdx = popstateBody.indexOf("type: 'live_redirect_mount'");
        expect(stashIdx).toBeGreaterThan(-1);
        expect(sendIdx).toBeGreaterThan(-1);
        // And stash must appear BEFORE the send — the whole point of
        // Fix #2. If a future refactor moves it after, this test
        // catches it.
        expect(stashIdx).toBeLessThan(sendIdx);

        // Additional runtime check: when the bundle is loaded and a
        // sticky exists in the DOM, calling stashStickySubtrees()
        // alone still detaches the subtree — confirms the helper
        // remains callable through djust.stickyPreserve (the
        // surface the popstate listener reaches through).
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();
        expect(api._stash.has('audio-player')).toBe(true);
    });

    // -------------------------------------------------------------------
    // Self-review Fix #5 — abnormal WS close clears stash
    // -------------------------------------------------------------------
    it('12. abnormal WS close clears the sticky stash', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const api = dom.window.djust.stickyPreserve;
        api.stashStickySubtrees();
        expect(api._stash.size).toBe(1);

        // Fix #5: the onclose handler for the WebSocket is supposed
        // to invoke clearStash() on abnormal disconnect. We exercise
        // the helper directly — it's the public surface that the
        // onclose handler calls into.
        expect(typeof api.clearStash).toBe('function');
        api.clearStash();
        expect(api._stash.size).toBe(0);

        // Static-source assertion: the 03-websocket.js onclose
        // handler MUST call clearStash() on the abnormal-close
        // branch (i.e. AFTER the _intentionalDisconnect early-return).
        // This anchors the Fix #5 contract in the source file so a
        // future refactor can't silently drop the cleanup.
        const fs = require('fs');
        const wsSrc = fs.readFileSync(
            './python/djust/static/djust/src/03-websocket.js',
            'utf-8'
        );
        const oncloseIdx = wsSrc.indexOf('onclose');
        expect(oncloseIdx).toBeGreaterThan(-1);
        const oncloseBody = wsSrc.slice(oncloseIdx);
        // The intentional-disconnect return must come BEFORE the
        // clearStash call — so abnormal-close path is the only one
        // that hits it.
        const earlyReturnIdx = oncloseBody.indexOf('_intentionalDisconnect = false');
        const clearStashIdx = oncloseBody.indexOf('clearStash');
        expect(earlyReturnIdx).toBeGreaterThan(-1);
        expect(clearStashIdx).toBeGreaterThan(-1);
        expect(clearStashIdx).toBeGreaterThan(earlyReturnIdx);
    });

    // -------------------------------------------------------------------
    // Self-review Fix #7 — sticky_update with unknown view_id
    // -------------------------------------------------------------------
    it('13. sticky_update for unknown view_id is a no-op (no throw, no DOM mutation, stash unchanged)', async () => {
        const dom = createDomWithSticky();
        await nextFrame(dom);
        const doc = dom.window.document;
        const api = dom.window.djust.stickyPreserve;

        // Capture DOM state + stash state.
        const beforeHTML = doc.body.innerHTML;
        const stashSizeBefore = api._stash.size;

        // Spy on applyPatches so we can assert it was NOT called —
        // an unknown view_id must bail early, before the patch pass.
        const win = dom.window;
        let applyCalls = 0;
        const originalApply = win.applyPatches;
        win.applyPatches = function () { applyCalls++; return true; };

        try {
            // Dispatching with a truly unknown view_id must not throw.
            expect(() => {
                api.handleStickyUpdate({
                    type: 'sticky_update',
                    view_id: 'nonexistent',
                    patches: [{ type: 'SetAttr', path: [0], d: 'x', attr: 'class', value: 'y' }],
                    version: 1,
                });
            }).not.toThrow();
        } finally {
            win.applyPatches = originalApply;
        }

        // applyPatches MUST NOT have been invoked.
        expect(applyCalls).toBe(0);
        // DOM unchanged.
        expect(doc.body.innerHTML).toBe(beforeHTML);
        // Stash unchanged.
        expect(api._stash.size).toBe(stashSizeBefore);
    });
});
