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
});
