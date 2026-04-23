/**
 * Tests for dj-flip — FLIP-technique list reorder animations
 * (v0.6.0, phase 2d — animations milestone finale).
 *
 * FLIP = First, Last, Invert, Play. When a [dj-flip] parent sees its
 * children reorder (via a MoveChild VDOM patch, or any direct DOM
 * mutation that preserves child identity), the module:
 *   1. Measures First rects BEFORE the reorder (cached ahead of time).
 *   2. Reads Last rects AFTER the mutation settles.
 *   3. Inverts via transform: translate(-dx, -dy).
 *   4. Plays by clearing the transform on the next animation frame,
 *      triggering a transition.
 *
 * jsdom doesn't do layout — we stub `getBoundingClientRect()` per element
 * via `Element.prototype.getBoundingClientRect`. jsdom also doesn't fire
 * `transitionend`, so assertions look at inline `.style.transform` /
 * `.style.transition` directly.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createDom, nextFrame as sharedNextFrame } from './_helpers.js';

// Like dj_transition_group.test.js — a 60 ms nextFrame budget to
// survive vitest parallel load.
function nextFrame(dom) {
    return sharedNextFrame(dom, 60);
}

// Stub getBoundingClientRect on a specific element to return a fixed
// rect. Returns a disposer that restores the previous state.
function stubRect(el, rect) {
    const prev = el.getBoundingClientRect;
    el.getBoundingClientRect = function () {
        return {
            top: rect.top, left: rect.left,
            right: rect.left + (rect.width || 0),
            bottom: rect.top + (rect.height || 0),
            width: rect.width || 0, height: rect.height || 0,
            x: rect.left, y: rect.top,
        };
    };
    return function restore() { el.getBoundingClientRect = prev; };
}

describe('dj-flip', () => {
    let dom;

    it('initial mount without reorder does not apply transforms', async () => {
        dom = createDom(
            '<ul id="list" dj-flip>' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await nextFrame(dom);
        // No mutation has happened — no transform applied.
        expect(a.style.transform).toBe('');
        expect(b.style.transform).toBe('');
    });

    it('reorder applies inverse transform then clears on next frame', async () => {
        dom = createDom(
            '<ul id="list" dj-flip>' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');

        // Phase 1: initial positions. The install-time snapshot fired
        // inside createDom BEFORE the stubs were applied — it cached
        // jsdom's default zero-rects. Re-snapshot now so the cached
        // "First" rects match the stubbed positions.
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));
        dom.window.djust.flip._snapshotRects(list);

        // Phase 2: swap. Re-stub the rects to their new positions.
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        // Perform the reorder (move b before a).
        list.insertBefore(b, a);

        // Give the MutationObserver one microtask to fire, but NOT a
        // full rAF tick (~16 ms in jsdom). At this point the inverse
        // transform should be pinned but the play-phase transition has
        // not yet kicked in.
        await Promise.resolve();
        await Promise.resolve();

        // The inverse transform should be pinned on BOTH children (they
        // swapped positions, so both moved by a non-zero dy). jsdom's
        // inline .style.transform gets set directly by our FLIP module.
        // The previous `||` assertion was weak — a regression where only
        // one mover got animated would have slipped through.
        const aMoved = a.style.transform && a.style.transform.includes('translate');
        const bMoved = b.style.transform && b.style.transform.includes('translate');
        expect(aMoved).toBe(true);
        expect(bMoved).toBe(true);

        // After one frame, the transform should clear and transition
        // should be populated so the browser animates back to identity
        // — for BOTH movers.
        await nextFrame(dom);
        const aTrans = a.style.transition && a.style.transition.indexOf('transform') !== -1;
        const bTrans = b.style.transition && b.style.transition.indexOf('transform') !== -1;
        expect(aTrans).toBe(true);
        expect(bTrans).toBe(true);
    });

    it('reduced-motion bypass skips transforms entirely', async () => {
        dom = createDom('');
        // Override matchMedia to claim reduced-motion is preferred.
        dom.window.matchMedia = function (query) {
            return {
                matches: query.indexOf('prefers-reduced-motion: reduce') !== -1,
                media: query,
                onchange: null,
                addEventListener: () => {},
                removeEventListener: () => {},
                addListener: () => {},
                removeListener: () => {},
                dispatchEvent: () => false,
            };
        };
        // Build the list AFTER the matchMedia override so the FLIP
        // module's first pass reads the reduced-motion preference.
        const doc = dom.window.document;
        const list = doc.createElement('ul');
        list.id = 'list';
        list.setAttribute('dj-flip', '');
        const a = doc.createElement('li'); a.id = 'a';
        const b = doc.createElement('li'); b.id = 'b';
        list.appendChild(a);
        list.appendChild(b);
        doc.querySelector('[dj-view]').appendChild(list);

        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));

        // Reorder.
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        list.insertBefore(b, a);
        await new Promise((r) => setTimeout(r, 20));

        // Reduced motion: no inline transform ever gets written.
        expect(a.style.transform).toBe('');
        expect(b.style.transform).toBe('');
    });

    it('duration and easing attributes are honored', async () => {
        dom = createDom(
            '<ul id="list" dj-flip dj-flip-duration="500" dj-flip-easing="ease-in">' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        list.insertBefore(b, a);
        await new Promise((r) => setTimeout(r, 20));
        await nextFrame(dom);

        const trans = (a.style.transition || '') + ' ' + (b.style.transition || '');
        expect(trans.indexOf('500ms') !== -1).toBe(true);
        expect(trans.indexOf('ease-in') !== -1).toBe(true);
    });

    it('defaults are 300ms and cubic-bezier easing', async () => {
        dom = createDom(
            '<ul id="list" dj-flip>' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        list.insertBefore(b, a);
        await new Promise((r) => setTimeout(r, 20));
        await nextFrame(dom);
        const trans = (a.style.transition || '') + ' ' + (b.style.transition || '');
        expect(trans.indexOf('300ms') !== -1).toBe(true);
        expect(trans.indexOf('cubic-bezier') !== -1).toBe(true);
    });

    it('nested [dj-flip] parents each have their own observer', async () => {
        dom = createDom(
            '<div id="outer" dj-flip>' +
            '  <div id="inner" dj-flip>' +
            '    <span id="x">X</span>' +
            '  </div>' +
            '</div>'
        );
        await new Promise((r) => setTimeout(r, 20));
        const api = dom.window.djust.flip;
        // Both parents should be tracked.
        expect(api._observerCount()).toBeGreaterThanOrEqual(2);
    });

    it('delete-then-insert (node identity not preserved) no-ops gracefully', async () => {
        dom = createDom(
            '<ul id="list" dj-flip>' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));

        // Delete-then-insert pattern: remove a, then insert a brand-new
        // element at the top. The new element has no cached rect, so
        // FLIP should NOT try to animate it.
        list.removeChild(a);
        const fresh = dom.window.document.createElement('li');
        fresh.id = 'fresh';
        list.insertBefore(fresh, b);
        stubRect(fresh, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));

        // The fresh element should not have a translate transform —
        // it's a brand-new entry, not a reorder.
        expect(fresh.style.transform || '').not.toContain('translate');
    });

    it('author-specified inline transform is restored at cleanup', async () => {
        // Regression for pre-commit finding #1: cleanup must restore
        // BOTH prevTransition AND prevTransform. Without the fix an
        // author-specified ``transform: rotate(5deg)`` on a child would
        // get permanently stomped to ``""`` after the first reorder.
        dom = createDom(
            '<ul id="list" dj-flip dj-flip-duration="50">' +
            '  <li id="a" style="transform: rotate(5deg)">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));
        // The install-time snapshot happened before the stubs were
        // applied — re-snapshot now so the cache has the real "First"
        // rects.
        dom.window.djust.flip._snapshotRects(list);
        // Sanity: the inline transform was applied by JSDOM.
        expect(a.style.transform.indexOf('rotate(5deg)') !== -1).toBe(true);

        // Reorder: swap the two.
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        list.insertBefore(b, a);

        // Wait past the play-phase rAF but NOT past the cleanup timer —
        // at this point FLIP should have overwritten the inline
        // transform with its translate. This is the state the cleanup
        // step later has to undo.
        await nextFrame(dom);
        expect(a.style.transform.indexOf('rotate(5deg)')).toBe(-1);

        // Wait for cleanup timer (duration 50 + 50 ms margin + slack).
        await new Promise((r) => setTimeout(r, 200));

        // After cleanup, the author-specified transform must be back.
        // Without the fix this would be "" (the empty-string overwrite
        // from the play phase persisting indefinitely).
        expect(a.style.transform).toBe('rotate(5deg)');
    });

    it('overlapping reorders do not stomp the rect cache', async () => {
        // Regression for pre-commit finding #2: when a second reorder
        // fires while the first is still animating, the first reorder's
        // cleanup timer must NOT re-snapshot rects (which would capture
        // intermediate positions). Verified indirectly: after two rapid
        // reorders settle, a third reorder must still produce a
        // correct translate on the movers.
        dom = createDom(
            '<ul id="list" dj-flip dj-flip-duration="60">' +
            '  <li id="a">A</li>' +
            '  <li id="b">B</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));

        // Reorder 1 — swap; fires mid-animation still running.
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        list.insertBefore(b, a);
        await new Promise((r) => setTimeout(r, 30));

        // Reorder 2 — fires while reorder 1 is still mid-animation.
        // Swap back. If the first reorder's cleanup timer stomps the
        // cache, reorder 2's diff reads rects that include the
        // in-flight transform offset, producing NaN / wrong translates.
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 20, left: 0, width: 100, height: 20 });
        list.insertBefore(a, b);

        // Wait for both cleanups to complete.
        await new Promise((r) => setTimeout(r, 300));

        // Reorder 3 — verify the module still computes good translates.
        stubRect(a, { top: 20, left: 0, width: 100, height: 20 });
        stubRect(b, { top: 0, left: 0, width: 100, height: 20 });
        list.insertBefore(b, a);
        await Promise.resolve();
        await Promise.resolve();

        // Both children should have a valid (non-NaN, non-empty) translate.
        const aT = a.style.transform || '';
        const bT = b.style.transform || '';
        expect(aT.indexOf('NaN')).toBe(-1);
        expect(bT.indexOf('NaN')).toBe(-1);
        // At least one of the movers must have been translated on reorder 3.
        expect(aT.indexOf('translate') !== -1 || bT.indexOf('translate') !== -1).toBe(true);
    });

    it('nested dj-flip: outer observer does not animate inner\'s children', async () => {
        // Regression for pre-commit finding #3: source comment claims the
        // outer [dj-flip] observer does NOT see inner [dj-flip] children
        // because `subtree: false`. Verify explicitly.
        dom = createDom(
            '<div id="outer" dj-flip>' +
            '  <span id="a">A</span>' +
            '  <div id="inner" dj-flip>' +
            '    <b id="bx">B</b>' +
            '    <b id="cx">C</b>' +
            '  </div>' +
            '  <span id="dx">D</span>' +
            '</div>'
        );
        const inner = dom.window.document.getElementById('inner');
        const a = dom.window.document.getElementById('a');
        const bx = dom.window.document.getElementById('bx');
        const cx = dom.window.document.getElementById('cx');
        const dx = dom.window.document.getElementById('dx');

        // Stub rects. outer-level children sit at 3 different y's; inner's
        // b/c start at y=100/120 and will swap.
        stubRect(a, { top: 0, left: 0, width: 100, height: 20 });
        stubRect(inner, { top: 20, left: 0, width: 100, height: 80 });
        stubRect(dx, { top: 100, left: 0, width: 100, height: 20 });
        stubRect(bx, { top: 30, left: 0, width: 100, height: 20 });
        stubRect(cx, { top: 50, left: 0, width: 100, height: 20 });
        await new Promise((r) => setTimeout(r, 20));

        // Reorder only the INNER parent's children (swap b and c).
        // outer-level children (a, inner, d) do NOT move.
        stubRect(bx, { top: 50, left: 0, width: 100, height: 20 });
        stubRect(cx, { top: 30, left: 0, width: 100, height: 20 });
        inner.insertBefore(cx, bx);
        await Promise.resolve();
        await Promise.resolve();

        // Inner observer fired — b and c each got an inverse transform.
        const bMoved = bx.style.transform && bx.style.transform.indexOf('translate') !== -1;
        const cMoved = cx.style.transform && cx.style.transform.indexOf('translate') !== -1;
        expect(bMoved).toBe(true);
        expect(cMoved).toBe(true);

        // Outer observer did NOT fire on the nested mutation — a and d
        // must have no inline transform written.
        expect(a.style.transform).toBe('');
        expect(dx.style.transform).toBe('');
    });

    it('_parseDuration rejects trailing garbage (no silent prefix parse)', async () => {
        // Regression for pre-commit finding #5: parseInt("300abc", 10)
        // would return 300 silently. Number() + Number.isFinite() rejects
        // the whole string and falls back to the default.
        dom = createDom(
            '<ul id="list" dj-flip>' +
            '  <li id="a">A</li>' +
            '</ul>'
        );
        const list = dom.window.document.getElementById('list');
        const api = dom.window.djust.flip;

        // Valid pure number → parsed.
        list.setAttribute('dj-flip-duration', '500');
        expect(api._parseDuration(list)).toBe(500);

        // Trailing garbage → fallback (300), NOT 300 from parsed prefix.
        list.setAttribute('dj-flip-duration', '500abc');
        expect(api._parseDuration(list)).toBe(300);

        // Pure garbage → fallback.
        list.setAttribute('dj-flip-duration', 'abc');
        expect(api._parseDuration(list)).toBe(300);

        // Empty → fallback.
        list.setAttribute('dj-flip-duration', '');
        expect(api._parseDuration(list)).toBe(300);

        // Negative → fallback.
        list.setAttribute('dj-flip-duration', '-100');
        expect(api._parseDuration(list)).toBe(300);

        // Decimal length is accepted (Number parses fine).
        list.setAttribute('dj-flip-duration', '123.5');
        expect(api._parseDuration(list)).toBe(123.5);

        // Upper clamp still works.
        list.setAttribute('dj-flip-duration', '99999');
        expect(api._parseDuration(list)).toBe(30000);
    });

    it('observer disconnects when parent is removed from the DOM', async () => {
        dom = createDom(
            '<div id="container">' +
            '  <ul id="list" dj-flip>' +
            '    <li id="a">A</li>' +
            '  </ul>' +
            '</div>'
        );
        await new Promise((r) => setTimeout(r, 20));
        const container = dom.window.document.getElementById('container');
        const list = dom.window.document.getElementById('list');
        const api = dom.window.djust.flip;
        const before = api._observerCount();
        expect(before).toBeGreaterThanOrEqual(1);
        container.removeChild(list);
        await new Promise((r) => setTimeout(r, 20));
        // Observer count drops by 1 (the one for #list).
        expect(api._observerCount()).toBeLessThan(before);
    });
});
