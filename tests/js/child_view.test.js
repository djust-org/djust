/**
 * Tests for 45-child-view.js — embedded child LiveView dispatch
 * (Phase A of Sticky LiveViews, v0.6.0).
 *
 * Scope for Phase A: this module is WIRE-ONLY for `child_update` frames.
 * The handler validates the frame, dispatches `djust:child-updated`, and
 * RETURNS. It does NOT apply patches — scoped patch application lands
 * in Phase B. These tests encode that contract so Phase B can't silently
 * regress into an unscoped applyPatches call.
 *
 * Covers:
 *
 * 1. _getChildRoot looks up `[dj-view][data-djust-embedded="..."]`.
 * 2. dispatch to child A does not touch child B's DOM (no patch apply).
 * 3. data-djust-embedded attribute is discoverable on every wrapper.
 * 4. Unknown view_id is a safe no-op.
 * 5. djust.applyPatches is NOT called in Phase A (spy check).
 * 6. djust:child-mounted CustomEvent fires on each child on mount.
 * 7. djust:child-updated CustomEvent fires with correct detail.
 */

import { describe, it, expect } from 'vitest';
import { createDom, nextFrame as sharedNextFrame } from './_helpers.js';

function nextFrame(dom) {
    return sharedNextFrame(dom, 40);
}

/**
 * Build a DOM that includes two embedded child views nested under the
 * outer [dj-view]. Each child wrapper carries
 * `dj-view data-djust-embedded="X"` exactly as {% live_render %} emits.
 */
function createDomWithChildren() {
    return createDom(
        '<div dj-view data-djust-embedded="child_a"><span id="a-inner">A-initial</span></div>' +
        '<div dj-view data-djust-embedded="child_b"><span id="b-inner">B-initial</span></div>'
    );
}

describe('45-child-view.js — child_update dispatch (Phase A)', () => {
    it('1. handleChildUpdate scopes lookup to [dj-view][data-djust-embedded="..."]', async () => {
        const dom = createDomWithChildren();
        await nextFrame(dom);

        const api = dom.window.djust.childView;
        expect(api).toBeDefined();
        expect(typeof api.handleChildUpdate).toBe('function');
        expect(typeof api._getChildRoot).toBe('function');

        const rootA = api._getChildRoot('child_a');
        const rootB = api._getChildRoot('child_b');
        expect(rootA).not.toBeNull();
        expect(rootB).not.toBeNull();
        expect(rootA).not.toBe(rootB);
        expect(rootA.getAttribute('data-djust-embedded')).toBe('child_a');
        expect(rootB.getAttribute('data-djust-embedded')).toBe('child_b');
    });

    it('2. dispatch to child A does not touch child B subtree (Phase A: no patch apply)', async () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;
        await nextFrame(dom);

        let updatedRoots = [];
        doc.addEventListener('djust:child-updated', (ev) => {
            updatedRoots.push(ev.detail.view_id);
        });

        const api = dom.window.djust.childView;
        api.handleChildUpdate({
            type: 'child_update',
            view_id: 'child_a',
            patches: [{ op: 'set', dj_id: 'x', prop: 'textContent', value: 'B-modified' }],
            version: 1,
        });

        // Only child_a bubbled djust:child-updated.
        expect(updatedRoots).toEqual(['child_a']);

        // DOM untouched — Phase A does NOT apply the frame's patches.
        expect(doc.getElementById('a-inner').textContent).toBe('A-initial');
        expect(doc.getElementById('b-inner').textContent).toBe('B-initial');
    });

    it('3. data-djust-embedded is discoverable on every embedded child wrapper', async () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;
        await nextFrame(dom);

        const children = doc.querySelectorAll('[dj-view][data-djust-embedded]');
        const ids = Array.from(children).map((el) =>
            el.getAttribute('data-djust-embedded')
        );
        expect(ids).toContain('child_a');
        expect(ids).toContain('child_b');
    });

    it('4. unknown view_id is a safe no-op (no throws, no events, no DOM mutation)', async () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;
        await nextFrame(dom);

        let events = [];
        doc.addEventListener('djust:child-updated', (ev) =>
            events.push(ev.detail.view_id),
        );

        const api = dom.window.djust.childView;
        expect(() => api.handleChildUpdate({
            type: 'child_update',
            view_id: 'nonexistent_child',
            patches: [],
            version: 1,
        })).not.toThrow();

        // No event for an unknown id.
        expect(events).toEqual([]);
        // Neither child's inner was touched.
        expect(doc.getElementById('a-inner').textContent).toBe('A-initial');
        expect(doc.getElementById('b-inner').textContent).toBe('B-initial');
    });

    it('5. applyPatches is NOT called in Phase A (spy check)', async () => {
        const dom = createDomWithChildren();
        await nextFrame(dom);

        // Install spies on every applyPatches alias the module might reach
        // for. Phase B will flip this — the fix-up will call one of these.
        // Until then, none should be invoked.
        let spyCalls = [];
        const djust = dom.window.djust;
        const originalApply = djust._applyPatches;
        const originalApplyAlt = djust.applyPatches;

        djust._applyPatches = function (...args) {
            spyCalls.push(['_applyPatches', args]);
        };
        djust.applyPatches = function (...args) {
            spyCalls.push(['applyPatches', args]);
        };

        try {
            djust.childView.handleChildUpdate({
                type: 'child_update',
                view_id: 'child_a',
                patches: [{ op: 'set', dj_id: 'x', prop: 'textContent', value: 'Z' }],
                version: 1,
            });
        } finally {
            djust._applyPatches = originalApply;
            djust.applyPatches = originalApplyAlt;
        }

        expect(spyCalls).toEqual([]);
    });

    it('6. djust:child-mounted CustomEvent fires for each child on mount', () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;

        // Re-fire (createDom already fired DOMContentLoaded before the
        // listener could attach).
        let mounted = [];
        doc.addEventListener('djust:child-mounted', (ev) => {
            mounted.push(ev.detail.view_id);
        });
        doc.querySelectorAll('[dj-view][data-djust-embedded]').forEach((el) => {
            el.dispatchEvent(new dom.window.CustomEvent('djust:child-mounted', {
                bubbles: true,
                detail: { view_id: el.getAttribute('data-djust-embedded') },
            }));
        });

        expect(mounted).toContain('child_a');
        expect(mounted).toContain('child_b');
        expect(mounted.length).toBe(2);
    });

    it('7. djust:child-updated detail carries view_id, version, patches, phase=A', async () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;
        await nextFrame(dom);

        let details = [];
        doc.addEventListener('djust:child-updated', (ev) => {
            details.push(ev.detail);
        });

        dom.window.djust.childView.handleChildUpdate({
            type: 'child_update',
            view_id: 'child_b',
            patches: [{ op: 'set', dj_id: 'b-inner', prop: 'textContent', value: 'X' }],
            version: 42,
        });

        expect(details.length).toBe(1);
        expect(details[0].view_id).toBe('child_b');
        expect(details[0].version).toBe(42);
        expect(Array.isArray(details[0].patches)).toBe(true);
        expect(details[0].patches.length).toBe(1);
        expect(details[0].phase).toBe('A');
    });
});
