/**
 * Investigation: does the batching optimization in applyPatches apply
 * InsertChild patches BEFORE RemoveChild patches in the same parent group,
 * violating the intended phase order?
 *
 * Hypothesis (Bug A in plan): when patches.length > 10 and a parent group
 * has >= 3 consecutive InsertChild patches, client.js:1379-1440 filters
 * inserts out and processes them via DocumentFragment BEFORE iterating
 * the group for remaining (non-insert) patches. This means inserts can
 * land at positions that still contain yet-to-be-removed nodes.
 *
 * These tests construct > 10-patch batches that mix removes and inserts
 * on the same parent and verify the final DOM is correct.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

let dom;
let document;
let applyPatches;

beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        runScripts: 'dangerously',
    });
    document = dom.window.document;

    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (s) => String(s).replace(/([^\w-])/g, '\\$1');
    }

    const clientCode = fs.readFileSync(
        './python/djust/static/djust/client.js',
        'utf-8'
    );
    // Append a test-only export of applyPatches so we can call it directly.
    // applyPatches is declared at module scope in client.js but not exposed
    // on window.djust; expose it here without modifying the shipped bundle.
    dom.window.eval(clientCode + '\nwindow.djust._applyPatches = applyPatches;');
    applyPatches = dom.window.djust._applyPatches;
});

function buildParent(childIds) {
    const root = document.createElement('div');
    root.setAttribute('dj-root', '');
    const parent = document.createElement('div');
    parent.setAttribute('dj-id', 'P');
    for (const id of childIds) {
        const child = document.createElement('div');
        child.setAttribute('dj-id', id);
        child.textContent = id;
        parent.appendChild(child);
    }
    root.appendChild(parent);
    document.body.appendChild(root);
    return parent;
}

function currentIds(parent) {
    return Array.from(parent.children).map(c => c.getAttribute('dj-id'));
}

function currentText(parent) {
    return Array.from(parent.children).map(c => c.textContent);
}

function makeVNode(tag, djId, text) {
    return {
        tag,
        djust_id: djId,
        attrs: { 'dj-id': djId },
        children: text ? [{
            tag: '#text',
            djust_id: null,
            attrs: {},
            children: [],
            text,
        }] : [],
    };
}

describe('Batch insert vs. remove ordering (>10 patches, same parent)', () => {
    /**
     * Simulates the PR #642 scenario: replace_all_children style.
     * Old = 8 cards, New = 7 docs. 8 RemoveChild + 7 InsertChild = 15 patches.
     *
     * With monotonic IDs (current behavior), new docs get IDs 'x1'..'x7'
     * which do NOT collide with cards '1'..'8'. If the batching reorders
     * so inserts run before removes, removes still find cards by ID.
     * Final DOM should be the 7 new docs.
     */
    it('15-patch batch: 8 card removes + 7 doc inserts (non-colliding IDs)', () => {
        const parent = buildParent(['1','2','3','4','5','6','7','8']);

        const patches = [];
        // 8 RemoveChild for cards, in descending index order (as _sortPatches would)
        for (let i = 7; i >= 0; i--) {
            patches.push({
                type: 'RemoveChild',
                path: [0],
                d: 'P',
                index: i,
                child_d: String(i + 1),
                ref_d: null,
            });
        }
        // 7 InsertChild for docs, in ascending index order
        for (let i = 0; i < 7; i++) {
            patches.push({
                type: 'InsertChild',
                path: [0],
                d: 'P',
                index: i,
                node: makeVNode('div', 'x' + (i + 1), 'doc' + (i + 1)),
                ref_d: null,
            });
        }

        const ok = applyPatches(patches);
        expect(ok).toBe(true);
        expect(currentIds(parent)).toEqual(['x1','x2','x3','x4','x5','x6','x7']);
    });

    /**
     * Post-fix invariant: the batching path applies RemoveChild patches
     * BEFORE the batched inserts, so even if new children reuse IDs that
     * old children currently hold, the old ones are gone by the time the
     * new ones are created and no collision can occur at insert time.
     *
     * Before the fix (#641 / #642), client.js:1379-1440 ran batched
     * inserts ahead of the removes in the same parent group. With
     * colliding IDs, the new docs were inserted in front of the
     * still-present old cards, and the subsequent querySelector-based
     * removes found the new docs first and deleted them, leaving
     * corrupted state.
     */
    it('colliding IDs still apply correctly (remove-before-insert batching order)', () => {
        const parent = buildParent(['1','2','3','4','5','6','7','8']);
        for (let i = 0; i < 8; i++) {
            parent.children[i].textContent = 'card' + (i + 1);
        }

        const patches = [];
        for (let i = 7; i >= 0; i--) {
            patches.push({
                type: 'RemoveChild',
                path: [0],
                d: 'P',
                index: i,
                child_d: String(i + 1),
                ref_d: null,
            });
        }
        // New children reuse IDs 1..7 that old children currently hold
        for (let i = 0; i < 7; i++) {
            patches.push({
                type: 'InsertChild',
                path: [0],
                d: 'P',
                index: i,
                node: makeVNode('div', String(i + 1), 'doc' + (i + 1)),
                ref_d: null,
            });
        }

        applyPatches(patches);

        const texts = currentText(parent);
        // Post-fix: all 8 old cards are removed first, then the 7 new
        // docs are batch-inserted into the empty parent — no collision.
        expect(texts).toEqual(['doc1','doc2','doc3','doc4','doc5','doc6','doc7']);
    });

    /**
     * Interleaved scenario: not a pure replace, but a partial update
     * that removes 5 old children and inserts 6 new children at positions
     * that interleave with surviving nodes.
     *
     * Old: [a,b,c,d,e,f,g,h,i,j,k] (11 children)
     * New: [a,X,b,Y,c,Z,d,W,e,V,U] (11 children: a-e kept, f-k removed, X-U inserted)
     *
     * This simulates what might happen with a mixed update.
     */
    it('interleaved: 6 inserts + 6 removes in a single batch', () => {
        const parent = buildParent(['a','b','c','d','e','f','g','h','i','j','k']);

        // Using the Rust diff_indexed_children algorithm:
        // common = 11. Diff pairwise:
        //   old[0]=a, new[0]=a → no patch
        //   old[1]=b, new[1]=X → tag match (all div) → recurse; content differs → SetText on text child
        //   ...
        // That would emit SetText patches, not structural. So interleaved
        // updates with same tags produce SetText, not Remove+Insert.
        //
        // For a true structural mix, we need tag differences or extras.
        // Let's instead simulate a keyed diff that produces 5 removes + 6 inserts:
        const patches = [];
        // Remove f, g, h, i, j, k (indices 5-10, descending)
        for (let i = 10; i >= 5; i--) {
            patches.push({
                type: 'RemoveChild',
                path: [0],
                d: 'P',
                index: i,
                child_d: parent.children[i].getAttribute('dj-id'),
                ref_d: null,
            });
        }
        // Insert X, Y, Z, W, V at positions 5-9 (consecutive)
        for (let i = 0; i < 5; i++) {
            patches.push({
                type: 'InsertChild',
                path: [0],
                d: 'P',
                index: 5 + i,
                node: makeVNode('div', 'new' + i, 'new' + i),
                ref_d: null,
            });
        }

        const ok = applyPatches(patches);
        expect(ok).toBe(true);
        expect(currentIds(parent)).toEqual([
            'a','b','c','d','e',
            'new0','new1','new2','new3','new4',
        ]);
    });

    /**
     * 13 patches on the same parent: forces the batching path
     * (> 10 patches) with 3+ consecutive inserts on one parent.
     * Old: [a,b,c,d,e,f]
     * New: [a,b,c,X,Y,Z,W] (keep a,b,c; remove d,e,f; insert X,Y,Z,W at 3)
     *
     * 3 RemoveChild + 4 InsertChild = 7 patches on this parent. Pad with
     * SetAttr on surviving nodes to push past 10 and trigger batching.
     */
    it('batching threshold (>10 patches): removes + inserts + attrs', () => {
        const parent = buildParent(['a','b','c','d','e','f']);

        const patches = [];
        // 3 removes (d,e,f), descending
        for (let i = 5; i >= 3; i--) {
            patches.push({
                type: 'RemoveChild',
                path: [0],
                d: 'P',
                index: i,
                child_d: parent.children[i].getAttribute('dj-id'),
                ref_d: null,
            });
        }
        // 4 consecutive inserts at positions 3..6
        for (let i = 0; i < 4; i++) {
            patches.push({
                type: 'InsertChild',
                path: [0],
                d: 'P',
                index: 3 + i,
                node: makeVNode('div', ['X','Y','Z','W'][i], ['X','Y','Z','W'][i]),
                ref_d: null,
            });
        }
        // Pad with SetAttr on a,b,c to exceed threshold
        for (const id of ['a','b','c']) {
            patches.push({
                type: 'SetAttr',
                path: [0, 0],  // doesn't matter, uses d
                d: id,
                key: 'data-marker',
                value: id + '-marked',
                child_d: null,
                ref_d: null,
            });
        }
        // Total: 3 + 4 + 3 = 10. Need 11+ to trigger batching.
        patches.push({
            type: 'SetAttr',
            path: [0, 0],
            d: 'a',
            key: 'data-extra',
            value: 'yes',
        });

        expect(patches.length).toBeGreaterThan(10);

        const ok = applyPatches(patches);
        expect(ok).toBe(true);
        expect(currentIds(parent)).toEqual(['a','b','c','X','Y','Z','W']);
        expect(parent.querySelector('[dj-id="a"]').getAttribute('data-marker')).toBe('a-marked');
        expect(parent.querySelector('[dj-id="a"]').getAttribute('data-extra')).toBe('yes');
    });
});
