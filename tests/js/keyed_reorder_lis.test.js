/**
 * Regression tests for LIS-minimized keyed reorders via MoveChild.
 *
 * These tests exercise the invariant that when Rust's diff_keyed_children
 * emits MoveChild patches for a keyed list reorder, applying them in order
 * yields the correct final DOM — specifically the subtle case where
 * multiple sequential MoveChild patches must correctly handle `patch.to`
 * being computed against the NEW ordering while applied against the
 * CURRENT (mid-batch) DOM state.
 *
 * Why the current code is correct:
 *   - diff_keyed_children computes the longest increasing subsequence (LIS)
 *     of old-indices-in-new-order and keeps those nodes in place (no Move).
 *   - Non-LIS nodes are moved in NEW-order via MoveChild(from, to).
 *   - The client does `insertBefore(moved, children[to])` against the
 *     current DOM.
 *   - This works because iterating non-LIS moves in new-order, each
 *     `insertBefore` shifts the remaining LIS anchors rightward by exactly
 *     the right amount — they end up at their target positions as a side
 *     effect of the prior moves.
 *
 * The JS-side fixture here ports the Rust LIS + keyed diff algorithm so
 * the emitted patch sequence matches exactly what crates/djust_vdom/src/
 * diff.rs would emit for each (old, new) pair. If either side changes
 * its algorithm, these tests will flag the drift.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

let dom;
let document;
let applyPatches;
let applySinglePatch;

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
    dom.window.eval(clientCode);
    applyPatches = dom.window.djust._applyPatches || dom.window.djust.applyPatches;
    applySinglePatch = dom.window.djust._applySinglePatch;
});

/**
 * Build a parent div with the given keyed children, attach to [dj-root],
 * and return the parent node.
 */
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

function currentOrder(parent) {
    return Array.from(parent.children).map(c => c.getAttribute('dj-id'));
}

/**
 * Compute the LIS of a sequence (indices).  Port of the Rust algorithm
 * from crates/djust_vdom/src/lis.rs so we can exactly reproduce what
 * diff_keyed_children would emit.
 */
function lis(seq) {
    if (seq.length === 0) return [];
    const n = seq.length;
    const tails = [];
    const indicesAt = [];
    const predecessor = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
        const val = seq[i];
        // partition_point: leftmost pos where tails[pos] >= val
        let lo = 0, hi = tails.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (tails[mid] < val) lo = mid + 1;
            else hi = mid;
        }
        const pos = lo;

        if (pos === tails.length) {
            tails.push(val);
            indicesAt.push(i);
        } else {
            tails[pos] = val;
            indicesAt[pos] = i;
        }

        predecessor[i] = pos > 0 ? indicesAt[pos - 1] : null;
    }

    const lisLen = tails.length;
    if (lisLen === 0) return [];

    const result = new Array(lisLen);
    let current = indicesAt[lisLen - 1];
    for (let i = lisLen - 1; i >= 0; i--) {
        result[i] = current;
        current = predecessor[current];
    }
    return result;
}

/**
 * Generate the MoveChild patches that diff_keyed_children would emit
 * for reordering `oldIds` into `newIds`. Assumes no inserts/removes
 * (same set of keys, just reordered).
 */
function buildReorderPatches(oldIds, newIds) {
    // Map: old id -> old index
    const oldIndexByKey = new Map();
    oldIds.forEach((id, i) => oldIndexByKey.set(id, i));

    // Build surviving mapping
    const newIdxForSurviving = [];
    const oldIndicesInNewOrder = [];
    newIds.forEach((id, newIdx) => {
        if (oldIndexByKey.has(id)) {
            newIdxForSurviving.push(newIdx);
            oldIndicesInNewOrder.push(oldIndexByKey.get(id));
        }
    });

    // LIS of old indices in new order
    const lisPositions = lis(oldIndicesInNewOrder);
    const lisSet = new Set(lisPositions.map(p => newIdxForSurviving[p]));

    // Emit moves for non-LIS nodes in new order
    const patches = [];
    newIds.forEach((id, newIdx) => {
        const oldIdx = oldIndexByKey.get(id);
        if (oldIdx === undefined) return; // new keyed insert — not handled here
        if (lisSet.has(newIdx)) return; // in LIS, stays
        patches.push({
            type: 'MoveChild',
            path: [0], // root is [dj-root] > P(dj-id=P)
            d: 'P',
            from: oldIdx,
            to: newIdx,
            child_d: id,
        });
    });

    return patches;
}

describe('Keyed reorder via MoveChild — LIS-minimized', () => {
    /**
     * Sanity check: the simple case from my original hand-trace.
     * [A,B,C,D] → [C,D,A,B]
     *
     * LIS of oldIndicesInNewOrder [2,3,0,1] has length 2 (either [0,1] or [2,3]).
     * The patience-sort algorithm picks [2,3] → nodes at new_idx 2 and 3 stay
     * (A and B). Moves: C(from=2,to=0), D(from=3,to=1).
     *
     * Apply:
     *   Move C to=0: children[0]=A, insertBefore(C,A) → [C,A,B,D]
     *   Move D to=1: children[1]=A, insertBefore(D,A) → [C,D,A,B]
     * Expected final: [C,D,A,B] ✓
     */
    it('[A,B,C,D] → [C,D,A,B] (2 moves, LIS keeps A,B)', () => {
        const parent = buildParent(['A', 'B', 'C', 'D']);
        const patches = buildReorderPatches(['A','B','C','D'], ['C','D','A','B']);

        // Verify what the Rust diff would actually emit
        expect(patches).toEqual([
            { type: 'MoveChild', path: [0], d: 'P', from: 2, to: 0, child_d: 'C' },
            { type: 'MoveChild', path: [0], d: 'P', from: 3, to: 1, child_d: 'D' },
        ]);

        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['C','D','A','B']);
    });

    it('[A,B,C] → [C,A,B] (1 move)', () => {
        const parent = buildParent(['A', 'B', 'C']);
        const patches = buildReorderPatches(['A','B','C'], ['C','A','B']);
        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['C','A','B']);
    });

    it('[A,B,C,D] → [B,A,D,C] (two swaps, independent)', () => {
        const parent = buildParent(['A','B','C','D']);
        const patches = buildReorderPatches(['A','B','C','D'], ['B','A','D','C']);
        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['B','A','D','C']);
    });

    it('[A,B,C,D,E] → [C,E,A,D,B] (complex scramble)', () => {
        const parent = buildParent(['A','B','C','D','E']);
        const patches = buildReorderPatches(['A','B','C','D','E'], ['C','E','A','D','B']);
        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['C','E','A','D','B']);
    });

    it('[A,B,C,D] → [D,C,B,A] (full reverse)', () => {
        const parent = buildParent(['A','B','C','D']);
        const patches = buildReorderPatches(['A','B','C','D'], ['D','C','B','A']);
        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['D','C','B','A']);
    });

    it('[A,B,C,D,E,F,G,H] → [H,G,F,E,D,C,B,A] (large reverse)', () => {
        const parent = buildParent(['A','B','C','D','E','F','G','H']);
        const patches = buildReorderPatches(
            ['A','B','C','D','E','F','G','H'],
            ['H','G','F','E','D','C','B','A']
        );
        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['H','G','F','E','D','C','B','A']);
    });

    it('[A,B,C,D,E] → [E,D,A,B,C] (partial rotation)', () => {
        const parent = buildParent(['A','B','C','D','E']);
        const patches = buildReorderPatches(['A','B','C','D','E'], ['E','D','A','B','C']);
        for (const p of patches) applySinglePatch(p);
        expect(currentOrder(parent)).toEqual(['E','D','A','B','C']);
    });
});
