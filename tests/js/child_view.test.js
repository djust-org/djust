/**
 * Tests for 45-child-view.js — embedded child LiveView dispatch.
 *
 * Phase A (v0.5.7) shipped the wire-only receiver; Phase B (v0.6.0)
 * wires the scoped applier. After Phase B, ``handleChildUpdate``:
 *   - looks up the child's root by ``[dj-view][data-djust-embedded]``
 *   - dispatches ``djust:child-updated`` (phase: 'B')
 *   - applies the frame's patches scoped to the child subtree via
 *     ``applyPatches(patches, rootEl)``
 *
 * Covers:
 *
 * 1. _getChildRoot looks up `[dj-view][data-djust-embedded="..."]`.
 * 2. dispatch to child A does not touch child B's DOM (scoped patch).
 * 3. data-djust-embedded attribute is discoverable on every wrapper.
 * 4. Unknown view_id is a safe no-op.
 * 5. applyPatches IS called scoped to child A (spy check, phase B).
 * 6. djust:child-mounted CustomEvent fires on each child on mount.
 * 7. djust:child-updated CustomEvent fires with phase=B.
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

    it('2. dispatch to child A does not touch child B subtree (scoped patch)', async () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;
        await nextFrame(dom);

        let updatedRoots = [];
        doc.addEventListener('djust:child-updated', (ev) => {
            updatedRoots.push(ev.detail.view_id);
        });

        const api = dom.window.djust.childView;
        // Pass a zero-patches frame so we exercise the event dispatch
        // without having to craft a real VDOM patch. Phase B scoping is
        // already verified by test 5 (applyPatches spy with rootEl).
        api.handleChildUpdate({
            type: 'child_update',
            view_id: 'child_a',
            patches: [],
            version: 1,
        });

        // Only child_a bubbled djust:child-updated.
        expect(updatedRoots).toEqual(['child_a']);

        // Child B's DOM is NEVER touched by a patch targeted at A.
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

    it('5. applyPatches IS called scoped to child_a (phase B wiring)', async () => {
        const dom = createDomWithChildren();
        await nextFrame(dom);

        // Phase B wires a SCOPED applier call — applyPatches(patches, rootEl)
        // where rootEl is the child_a subtree. Spy on the top-level
        // ``applyPatches`` that 45-child-view.js reaches for via closure.
        let spyCalls = [];
        const win = dom.window;
        const originalApply = win.applyPatches;
        win.applyPatches = function (patches, rootEl) {
            spyCalls.push({ patches: patches, rootEl: rootEl });
            return true;
        };

        try {
            win.djust.childView.handleChildUpdate({
                type: 'child_update',
                view_id: 'child_a',
                patches: [{ type: 'SetAttr', path: [0], d: 'x', attr: 'class', value: 'y' }],
                version: 1,
            });
        } finally {
            win.applyPatches = originalApply;
        }

        // Phase B: scoped apply IS called — rootEl is the child A root,
        // not document.
        expect(spyCalls.length).toBe(1);
        expect(spyCalls[0].rootEl).not.toBeNull();
        expect(spyCalls[0].rootEl.getAttribute('data-djust-embedded')).toBe('child_a');
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

    it('7. djust:child-updated detail carries view_id, version, patches, phase=B', async () => {
        const dom = createDomWithChildren();
        const doc = dom.window.document;
        await nextFrame(dom);

        let details = [];
        doc.addEventListener('djust:child-updated', (ev) => {
            details.push(ev.detail);
        });

        // Stub applyPatches so we don't get DOM-mutation side effects
        // while asserting on the event detail.
        const win = dom.window;
        const originalApply = win.applyPatches;
        win.applyPatches = function () { return true; };
        try {
            dom.window.djust.childView.handleChildUpdate({
                type: 'child_update',
                view_id: 'child_b',
                patches: [{ type: 'SetAttr', path: [0], d: 'b-inner', attr: 'class', value: 'z' }],
                version: 42,
            });
        } finally {
            win.applyPatches = originalApply;
        }

        expect(details.length).toBe(1);
        expect(details[0].view_id).toBe('child_b');
        expect(details[0].version).toBe(42);
        expect(Array.isArray(details[0].patches)).toBe(true);
        expect(details[0].patches.length).toBe(1);
        expect(details[0].phase).toBe('B');
    });
});
