/**
 * Tests for 4-phase patch sorting in applyPatches.
 *
 * Verifies that patches are ordered: RemoveChild → MoveChild → InsertChild → SetText/SetAttr
 * and that RemoveChild patches on the same parent are sorted in descending index order.
 * Regression test for Issue #142, #198.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const clientCode = await import('fs').then(fs =>
    fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8')
);

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});

dom.window.eval(clientCode);

const { _sortPatches } = dom.window.djust;

describe('Patch sorting — 4-phase ordering', () => {
    it('should order RemoveChild before InsertChild before SetText', () => {
        const patches = [
            { type: 'SetText', path: [0, 1, 0], text: 'hello' },
            { type: 'InsertChild', path: [0, 0], index: 0, node: {} },
            { type: 'RemoveChild', path: [0, 0], index: 2 },
        ];

        _sortPatches(patches);
        expect(patches.map(p => p.type)).toEqual([
            'RemoveChild', 'InsertChild', 'SetText'
        ]);
    });

    it('should order MoveChild between RemoveChild and InsertChild', () => {
        const patches = [
            { type: 'InsertChild', path: [0], index: 0, node: {} },
            { type: 'MoveChild', path: [0], from: 1, to: 3 },
            { type: 'RemoveChild', path: [0], index: 5 },
        ];

        _sortPatches(patches);
        expect(patches.map(p => p.type)).toEqual([
            'RemoveChild', 'MoveChild', 'InsertChild'
        ]);
    });

    it('should sort same-parent RemoveChild in descending index order', () => {
        const patches = [
            { type: 'RemoveChild', path: [0, 0], index: 1 },
            { type: 'RemoveChild', path: [0, 0], index: 3 },
            { type: 'RemoveChild', path: [0, 0], index: 0 },
        ];

        _sortPatches(patches);
        expect(patches.map(p => p.index)).toEqual([3, 1, 0]);
    });

    it('should not reorder RemoveChild across different parents', () => {
        const patches = [
            { type: 'RemoveChild', path: [0, 1], index: 0 },
            { type: 'RemoveChild', path: [0, 0], index: 2 },
            { type: 'RemoveChild', path: [0, 0], index: 0 },
        ];

        _sortPatches(patches);
        // [0,0] patches should be descending; [0,1] stays in relative position
        const p00 = patches.filter(p => JSON.stringify(p.path) === '[0,0]');
        expect(p00.map(p => p.index)).toEqual([2, 0]);
    });

    it('should place SetAttribute after all child mutations', () => {
        const patches = [
            { type: 'SetAttribute', path: [0, 0, 1], name: 'class', value: 'active' },
            { type: 'RemoveChild', path: [0, 0], index: 0 },
            { type: 'InsertChild', path: [0, 0], index: 0, node: {} },
        ];

        _sortPatches(patches);
        expect(patches[2].type).toBe('SetAttribute');
    });

    it('should handle mixed patch types from a real todo-delete scenario', () => {
        // Simulates: delete last todo → remove <li>, update counter text
        const patches = [
            { type: 'SetText', path: [0, 1, 0], text: '0 items left' },
            { type: 'RemoveChild', path: [0, 0], index: 2 },
            { type: 'RemoveChild', path: [0, 0], index: 1 },
            { type: 'RemoveChild', path: [0, 0], index: 0 },
        ];

        _sortPatches(patches);
        // All RemoveChild first (descending), then SetText last
        expect(patches.map(p => p.type)).toEqual([
            'RemoveChild', 'RemoveChild', 'RemoveChild', 'SetText'
        ]);
        expect(patches[0].index).toBe(2);
        expect(patches[1].index).toBe(1);
        expect(patches[2].index).toBe(0);
    });

    it('should order RemoveSubtree BEFORE path-based child ops (#1370)', () => {
        // #1370 reproduction: server emits a batch mixing id-based subtree
        // ops and path-based child ops. The path-based indices reflect the
        // NEW tree's positions (after subtree ops applied). If RemoveChild
        // runs before RemoveSubtree, it targets the still-old DOM and
        // removes the wrong child. The #1370 corruption guard: RemoveSubtree
        // (tear-down) must run before any path-based child op.
        //
        // NOTE (#1678): InsertSubtree is NO LONGER bundled directly after
        // RemoveSubtree. It moved to the boundary-span phase (with MoveSubtree)
        // AFTER the child ops, applied by ascending index — see the dedicated
        // #1678 test below and the _sortPatches phase doc. The #1370 invariant
        // here (RemoveSubtree before RemoveChild) is unaffected, and the child
        // ops resolve their parent by `d` (dj-id), so they are robust to the
        // InsertSubtree position.
        const patches = [
            { type: 'SetAttr', path: [1, 2, 4, 0, 1], d: '2B', key: 'class', value: 'x' },
            { type: 'RemoveSubtree', id: 'if-290fa3f1-100' },
            { type: 'RemoveChild', path: [1, 2, 4, 1], d: '2I', index: 6, child_d: '1fh' },
            { type: 'InsertChild', path: [1, 2, 4, 1], d: '2I', index: 2, node: {} },
            { type: 'InsertSubtree', id: 'if-290fa3f1-71', path: [1, 2, 4], d: '2I', index: 0, html: '' },
            { type: 'SetAttr', path: [1, 2, 4, 0, 2], d: '2E', key: 'class', value: 'y' },
        ];

        _sortPatches(patches);
        const types = patches.map(p => p.type);
        // The #1370 corruption guard: RemoveSubtree first, before RemoveChild.
        expect(types[0]).toBe('RemoveSubtree');
        expect(types.indexOf('RemoveSubtree')).toBeLessThan(types.indexOf('RemoveChild'));
        // RemoveChild before InsertChild (4-phase child ordering).
        expect(types.indexOf('RemoveChild')).toBeLessThan(types.indexOf('InsertChild'));
        // #1678: InsertSubtree (boundary-span phase) now runs AFTER the child ops.
        expect(types.indexOf('InsertChild')).toBeLessThan(types.indexOf('InsertSubtree'));
        // SetAttr (node-targeting) last.
        expect(types.slice(-2).every(t => t === 'SetAttr')).toBe(true);
    });

    it('should interleave MoveSubtree + InsertSubtree by ascending index, after child ops (#1678)', () => {
        // #1678: a tab activates whose body is a NESTED conditional. The differ
        // emits MoveSubtree(outer boundary) + InsertSubtree(inner boundary)
        // where the inner index assumes the outer is already at its final
        // position. Both must be in the same (boundary-span) phase AFTER child
        // ops, ordered by ASCENDING target index, so the outer boundary is
        // repositioned before the nested insert lands inside it. Running
        // InsertSubtree first put the inner span as a SIBLING of the outer →
        // marker drift → html_recovery on every kanban move.
        const patches = [
            { type: 'InsertSubtree', id: 'if-x-13', path: [], d: '0', index: 7, html: '' },
            { type: 'RemoveChild', path: [], d: '0', index: 1, child_d: '1' },
            { type: 'MoveSubtree', id: 'if-x-12', path: [], d: '0', index: 6 },
            { type: 'SetText', path: [8, 0, 0, 1, 0], text: '1' },
        ];

        _sortPatches(patches);
        const types = patches.map(p => p.type);
        // RemoveChild (child op) before the boundary-span ops.
        expect(types.indexOf('RemoveChild')).toBeLessThan(types.indexOf('MoveSubtree'));
        // Boundary-span ops ascending by index: MoveSubtree(6) before InsertSubtree(7).
        expect(types.indexOf('MoveSubtree')).toBeLessThan(types.indexOf('InsertSubtree'));
        // SetText (node-targeting) last — after the structure is final.
        expect(types[types.length - 1]).toBe('SetText');
    });
});
