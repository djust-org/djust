/**
 * Tests for the client-side `MoveSubtree` patch dispatcher — #1666.
 *
 * When a `{% if %}` boundary is matched across a render but its position
 * among the parent's significant children shifts (siblings change around
 * it), the server emits `MoveSubtree { id, path, d, index }`. The client
 * locates the `<!--dj-if id="X"-->...<!--/dj-if-->` marker pair by id,
 * detaches the whole range, and re-inserts it at `index` among the parent's
 * significant children — preserving inner node identity.
 *
 * Applied in phase 3 (after RemoveChild/MoveChild/InsertChild) so the
 * surrounding siblings are already in their final positions.
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

const { _applyMoveSubtree, _applySinglePatch, _sortPatches } = dom.window.djust;
const document = dom.window.document;

function boundary(parent, id, bodyHtml = '') {
    parent.appendChild(document.createComment(`dj-if id="${id}"`));
    if (bodyHtml) {
        const tpl = document.createElement('template');
        tpl.innerHTML = bodyHtml;
        parent.appendChild(tpl.content);
    }
    parent.appendChild(document.createComment('/dj-if'));
}

describe('applyMoveSubtree', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    it('moves a matched boundary span to a new index (the #1666 case)', () => {
        // <div dj-id=root>[ <!--if-a-->..<!--/if-a-->, <p keep> ]
        // move the boundary to index 1 (after a freshly-inserted <section>).
        const root = document.createElement('div');
        root.setAttribute('dj-id', 'root');
        boundary(root, 'if-a', '<span>body</span>');
        const p = document.createElement('p');
        p.setAttribute('dj-id', 'keep');
        p.textContent = 'x';
        root.appendChild(p);
        document.body.appendChild(root);

        // Simulate the sibling already settled: a <section> at the front, so the
        // boundary should move to significant index 1.
        const section = document.createElement('section');
        section.setAttribute('dj-id', 'sec');
        root.insertBefore(section, root.firstChild);
        // Now children: [section, <!--if-a-->, <span>, <!--/if-a-->, <p>]
        // We want the boundary AFTER section already — to test the move, first
        // put the boundary back at the front:
        root.insertBefore(root.childNodes[1], root.firstChild); // open
        root.insertBefore(root.childNodes[2], root.childNodes[1]); // span
        root.insertBefore(root.childNodes[3], root.childNodes[2]); // close
        // children now: [<!--if-a-->, <span>, <!--/if-a-->, section, <p>]

        const ok = _applyMoveSubtree(
            { type: 'MoveSubtree', id: 'if-a', path: [], d: 'root', index: 1 },
            root
        );
        expect(ok).toBe(true);

        // The marker span (open, span, close) should now sit at significant
        // index 1 — i.e. after <section>, before <p>.
        const kinds = Array.from(root.childNodes).map((n) =>
            n.nodeType === 8 ? `<!--${n.textContent}-->` : n.tagName
        );
        expect(kinds).toEqual([
            'SECTION',
            '<!--dj-if id="if-a"-->',
            'SPAN',
            '<!--/dj-if-->',
            'P',
        ]);
        // Inner node identity preserved (same <span> element instance moved).
        expect(root.querySelector('span').textContent).toBe('body');
    });

    it('is an idempotent no-op when the marker is absent', () => {
        const root = document.createElement('div');
        root.setAttribute('dj-id', 'root');
        root.innerHTML = '<p dj-id="p1">x</p>';
        document.body.appendChild(root);
        const ok = _applyMoveSubtree(
            { type: 'MoveSubtree', id: 'if-missing', path: [], d: 'root', index: 0 },
            root
        );
        expect(ok).toBe(true); // no-op, not a failure (no recovery-HTML fallback)
        expect(root.querySelector('p')).not.toBeNull();
    });

    it('routes through applySinglePatch by type', () => {
        const root = document.createElement('div');
        root.setAttribute('dj-id', 'root');
        boundary(root, 'if-b');
        const a = document.createElement('a');
        a.setAttribute('dj-id', 'a1');
        root.insertBefore(a, root.firstChild); // [<a>, <!--if-b-->, <!--/if-b-->]
        document.body.appendChild(root);
        const ok = _applySinglePatch(
            { type: 'MoveSubtree', id: 'if-b', path: [], d: 'root', index: 0 },
            root
        );
        expect(ok).toBe(true);
        // boundary moved to index 0 (before <a>).
        expect(root.firstChild.nodeType).toBe(8);
        expect(root.firstChild.textContent).toBe('dj-if id="if-b"');
    });

    it('_sortPatches places MoveSubtree after InsertChild, before SetText', () => {
        const patches = [
            { type: 'SetText', path: [0], text: 't' },
            { type: 'MoveSubtree', id: 'if-a', path: [], index: 0 },
            { type: 'InsertChild', path: [], index: 0 },
            { type: 'RemoveSubtree', id: 'if-z' },
        ];
        const sorted = _sortPatches(patches.slice()).map((p) => p.type);
        expect(sorted).toEqual(['RemoveSubtree', 'InsertChild', 'MoveSubtree', 'SetText']);
    });
});
