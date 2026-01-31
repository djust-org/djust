/**
 * Tests for patch grouping functions used in batched DOM updates.
 *
 * Verifies that groupPatchesByParent and groupConsecutiveInserts
 * correctly separate patches targeting different parent containers.
 * Regression test for Issue #142 (sibling parent mis-grouping).
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

const { _groupPatchesByParent, _groupConsecutiveInserts } = dom.window.djust;

describe('groupPatchesByParent', () => {
    it('should group child-op patches by full path (not grandparent)', () => {
        const patches = [
            { type: 'RemoveChild', path: [0, 0], d: 'messages', index: 0 },
            { type: 'InsertChild', path: [0, 0], d: 'messages', index: 0, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'messages', index: 1, node: {} },
        ];

        const groups = _groupPatchesByParent(patches);
        expect(groups.size).toBe(1);
        expect(groups.has('0/0')).toBe(true);
        expect(groups.get('0/0').length).toBe(3);
    });

    it('should separate InsertChild patches targeting different sibling parents', () => {
        const patches = [
            { type: 'RemoveChild', path: [0, 0], d: 'messages', index: 0 },
            { type: 'InsertChild', path: [0, 0], d: 'messages', index: 0, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'messages', index: 1, node: {} },
            // This targets a sibling container — must NOT merge with above
            { type: 'InsertChild', path: [0, 1], d: 'chat-input', index: 0, node: {} },
        ];

        const groups = _groupPatchesByParent(patches);
        expect(groups.size).toBe(2);
        expect(groups.get('0/0').length).toBe(3);
        expect(groups.get('0/1').length).toBe(1);
        expect(groups.get('0/1')[0].d).toBe('chat-input');
    });

    it('should use slice(0,-1) for node-targeting patches like SetAttribute', () => {
        const patches = [
            { type: 'SetAttribute', path: [0, 0, 1], d: 'child-node', name: 'class', value: 'new' },
            { type: 'SetAttribute', path: [0, 0, 2], d: 'other-child', name: 'class', value: 'new' },
        ];

        const groups = _groupPatchesByParent(patches);
        // Both target nodes under parent [0,0], so grouped under '0/0'
        expect(groups.size).toBe(1);
        expect(groups.has('0/0')).toBe(true);
        expect(groups.get('0/0').length).toBe(2);
    });
});

describe('groupConsecutiveInserts', () => {
    it('should not batch consecutive inserts from different parents', () => {
        const inserts = [
            { type: 'InsertChild', path: [0, 0], d: 'messages', index: 0, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'messages', index: 1, node: {} },
            // Different parent, consecutive index — must NOT merge
            { type: 'InsertChild', path: [0, 1], d: 'chat-input', index: 2, node: {} },
        ];

        const groups = _groupConsecutiveInserts(inserts);
        expect(groups.length).toBe(2);
        expect(groups[0].length).toBe(2);
        expect(groups[0][0].d).toBe('messages');
        expect(groups[1].length).toBe(1);
        expect(groups[1][0].d).toBe('chat-input');
    });

    it('should batch consecutive inserts from the same parent', () => {
        const inserts = [
            { type: 'InsertChild', path: [0, 0], d: 'container', index: 0, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'container', index: 1, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'container', index: 2, node: {} },
        ];

        const groups = _groupConsecutiveInserts(inserts);
        expect(groups.length).toBe(1);
        expect(groups[0].length).toBe(3);
    });

    it('should split non-consecutive indices even from same parent', () => {
        const inserts = [
            { type: 'InsertChild', path: [0, 0], d: 'container', index: 0, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'container', index: 1, node: {} },
            { type: 'InsertChild', path: [0, 0], d: 'container', index: 5, node: {} },
        ];

        const groups = _groupConsecutiveInserts(inserts);
        expect(groups.length).toBe(2);
        expect(groups[0].length).toBe(2);
        expect(groups[1].length).toBe(1);
    });
});
