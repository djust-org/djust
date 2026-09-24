/**
 * #3014: a patch batch finds its dj-if markers through ONE map, not one
 * full-document scan per MoveSubtree / InsertSubtree / RemoveSubtree.
 *
 * The map must stay right while the batch mutates the DOM: a marker removed
 * earlier in the batch reads as absent, a marker inserted earlier in the batch
 * is found (so a duplicate insert stays an idempotent no-op), and moved spans
 * keep their identity.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (str) => String(str).replace(/([^\w-])/g, '\\$1');
}
dom.window.eval(clientCode);

const { _applyPatchBatch } = dom.window.djust;
const document = dom.window.document;

/** A <ul dj-id="list"> holding `n` dj-if spans if-0 .. if-(n-1). Placement
 * indices count significant children, and dj-if markers are significant, so
 * span k starts at index 3k. */
function list(n) {
    const ul = document.createElement('ul');
    ul.setAttribute('dj-id', 'list');
    for (let i = 0; i < n; i++) {
        ul.appendChild(document.createComment(`dj-if id="if-${i}"`));
        const li = document.createElement('li');
        li.textContent = `item ${i}`;
        ul.appendChild(li);
        ul.appendChild(document.createComment('/dj-if'));
    }
    document.body.appendChild(ul);
    return ul;
}

function texts(ul) {
    return Array.from(ul.querySelectorAll('li')).map((li) => li.textContent);
}

/** Count full-document comment scans (TreeWalkers rooted at the scope, not
 * anchored at a marker). */
function countScans(fn) {
    const real = document.createTreeWalker.bind(document);
    let scans = 0;
    document.createTreeWalker = (root, what, filter) => {
        const walker = real(root, what, filter);
        if (root === document.body) {
            // _findDjIfCloseMarker also roots at body but re-anchors
            // currentNode at the open marker; count only walkers that start
            // at the root, i.e. whose first move is from the root itself.
            const next = walker.nextNode.bind(walker);
            let first = true;
            walker.nextNode = () => {
                if (first && walker.currentNode === root) scans++;
                first = false;
                return next();
            };
        }
        return walker;
    };
    try {
        fn();
    } finally {
        document.createTreeWalker = real;
    }
    return scans;
}

describe('dj-if marker map per patch batch (#3014)', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    it('moves many spans with one document scan', () => {
        const ul = list(50);
        // Reverse the list: every span moves.
        const patches = [];
        for (let i = 0; i < 50; i++) {
            patches.push({ type: 'MoveSubtree', id: `if-${49 - i}`, path: [], d: 'list', index: 3 * i });
        }
        let result;
        const scans = countScans(() => { result = _applyPatchBatch(patches, null); });
        expect(result.failed).toBe(0);
        expect(texts(ul)).toEqual(Array.from({ length: 50 }, (_, i) => `item ${49 - i}`));
        expect(scans).toBe(1);
    });

    it('prepends new spans with one document scan', () => {
        const ul = list(30);
        const patches = [];
        for (let i = 0; i < 5; i++) {
            patches.push({
                type: 'InsertSubtree', id: `new-${i}`, path: [], d: 'list', index: 3 * i,
                html: `<!--dj-if id="new-${i}"--><li>new ${i}</li><!--/dj-if-->`,
            });
        }
        let result;
        const scans = countScans(() => { result = _applyPatchBatch(patches, null); });
        expect(result.failed).toBe(0);
        expect(texts(ul).slice(0, 6)).toEqual(['new 0', 'new 1', 'new 2', 'new 3', 'new 4', 'item 0']);
        expect(scans).toBe(1);
    });

    it('a span removed earlier in the batch reads as absent, and can be re-inserted', () => {
        const ul = list(3);
        const result = _applyPatchBatch([
            { type: 'RemoveSubtree', id: 'if-1' },
            {
                type: 'InsertSubtree', id: 'if-1', path: [], d: 'list', index: 6,
                html: '<!--dj-if id="if-1"--><li>fresh 1</li><!--/dj-if-->',
            },
        ], null);
        expect(result.failed).toBe(0);
        expect(texts(ul)).toEqual(['item 0', 'item 2', 'fresh 1']);
    });

    it('a duplicate InsertSubtree in one batch stays an idempotent no-op', () => {
        const ul = list(1);
        const insert = {
            type: 'InsertSubtree', id: 'dup', path: [], d: 'list', index: 3,
            html: '<!--dj-if id="dup"--><li>dup</li><!--/dj-if-->',
        };
        const result = _applyPatchBatch([insert, { ...insert }], null);
        expect(result.failed).toBe(0);
        expect(texts(ul)).toEqual(['item 0', 'dup']);
    });

    it('a MoveSubtree for a marker nobody has is still an idempotent success', () => {
        const ul = list(2);
        const result = _applyPatchBatch([
            { type: 'MoveSubtree', id: 'gone', path: [], d: 'list', index: 0 },
        ], null);
        expect(result.failed).toBe(0);
        expect(texts(ul)).toEqual(['item 0', 'item 1']);
    });

    it('the map is dropped after the batch: a later single patch scans the live DOM', () => {
        const ul = list(2);
        _applyPatchBatch([{ type: 'RemoveSubtree', id: 'if-0' }], null);
        // Re-add if-0 outside any batch; a stale map would miss it.
        ul.appendChild(document.createComment('dj-if id="if-0"'));
        ul.appendChild(document.createComment('/dj-if'));
        expect(dom.window.djust._applyRemoveSubtree({ type: 'RemoveSubtree', id: 'if-0' })).toBe(true);
        expect(Array.from(ul.childNodes).filter((n) => n.nodeType === 8).length).toBe(2);
    });
});
