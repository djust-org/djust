/**
 * Tests for the client-side `RemoveSubtree` + `InsertSubtree` patch
 * dispatcher — Foundation 2 of #1358 (Iter 2).
 *
 * The server's Rust VDOM differ (Iter 3 of v0.9.4-1) will emit these
 * patch types when `{% if %}` block conditionals flip; the client
 * needs to know how to apply them. This file is the doc-claim-verbatim
 * test pack for the dispatcher (per CLAUDE.md Action #1196 — every
 * dispatch branch gets at least one positive + one edge-case test).
 *
 * Patch shapes (Iter 2 wire format):
 *   RemoveSubtree: {type: 'RemoveSubtree', id: 'if-<8hex>-N'}
 *   InsertSubtree: {type: 'InsertSubtree', id: '...', html: '<!--dj-if-->...<!--/dj-if-->',
 *                   path: [parent path], index: N}
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
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

const {
    _applySinglePatch,
    _applyRemoveSubtree,
    _applyInsertSubtree,
    _findDjIfOpenMarker,
    _findDjIfCloseMarker,
    _extractDjIfMarkerId,
} = dom.window.djust;
const document = dom.window.document;

describe('extractDjIfMarkerId helper', () => {
    it('extracts id from boundary-open marker', () => {
        expect(_extractDjIfMarkerId('dj-if id="if-abc-0"')).toBe('if-abc-0');
    });

    it('extracts prefixed id', () => {
        expect(_extractDjIfMarkerId('dj-if id="if-a3b1c2d4-0"')).toBe('if-a3b1c2d4-0');
    });

    it('returns null for legacy bare dj-if', () => {
        expect(_extractDjIfMarkerId('dj-if')).toBeNull();
    });

    it('returns null for the close marker', () => {
        expect(_extractDjIfMarkerId('/dj-if')).toBeNull();
    });

    it('returns null for non-string input', () => {
        expect(_extractDjIfMarkerId(null)).toBeNull();
        expect(_extractDjIfMarkerId(42)).toBeNull();
    });
});

describe('findDjIfOpenMarker / findDjIfCloseMarker', () => {
    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('finds an open marker by id and its matching close', () => {
        const wrap = document.createElement('div');
        wrap.appendChild(document.createComment('dj-if id="if-x-0"'));
        const span = document.createElement('span');
        span.textContent = 'inner';
        wrap.appendChild(span);
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        const open = _findDjIfOpenMarker('if-x-0');
        expect(open).not.toBeNull();
        expect(open.textContent.trim()).toBe('dj-if id="if-x-0"');

        const close = _findDjIfCloseMarker(open);
        expect(close).not.toBeNull();
        expect(close.textContent.trim()).toBe('/dj-if');
    });

    it('returns null when the id is not present', () => {
        const wrap = document.createElement('div');
        wrap.appendChild(document.createComment('dj-if id="if-real-0"'));
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        expect(_findDjIfOpenMarker('if-missing-0')).toBeNull();
    });

    it('matches the OUTER close on nested pairs (depth counter)', () => {
        const wrap = document.createElement('div');
        wrap.appendChild(document.createComment('dj-if id="outer-0"'));
        wrap.appendChild(document.createComment('dj-if id="inner-0"'));
        const innerSpan = document.createElement('span');
        innerSpan.textContent = 'inner';
        wrap.appendChild(innerSpan);
        wrap.appendChild(document.createComment('/dj-if'));
        const outerSpan = document.createElement('span');
        outerSpan.textContent = 'outer-after-inner';
        wrap.appendChild(outerSpan);
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        const outerOpen = _findDjIfOpenMarker('outer-0');
        const outerClose = _findDjIfCloseMarker(outerOpen);
        // The outer close is the LAST comment; if the depth counter were
        // broken, the inner close would have matched first.
        expect(outerClose).toBe(wrap.lastChild);
    });

    it('matches the INNER close when called on inner open', () => {
        const wrap = document.createElement('div');
        wrap.appendChild(document.createComment('dj-if id="outer-0"'));
        wrap.appendChild(document.createComment('dj-if id="inner-0"'));
        wrap.appendChild(document.createComment('/dj-if'));   // inner close
        wrap.appendChild(document.createComment('/dj-if'));   // outer close
        document.body.appendChild(wrap);

        const innerOpen = _findDjIfOpenMarker('inner-0');
        const innerClose = _findDjIfCloseMarker(innerOpen);
        // The inner close is comments[2]; the outer close is comments[3].
        const comments = Array.from(wrap.childNodes).filter(n => n.nodeType === 8);
        expect(innerClose).toBe(comments[2]);
    });

    it('ignores legacy bare "dj-if" placeholder when matching pairs', () => {
        // Legacy `dj-if` (no space, no id) is a single-comment placeholder
        // from #295; it is NOT part of the open/close pairing.
        const wrap = document.createElement('div');
        wrap.appendChild(document.createComment('dj-if id="if-real-0"'));
        wrap.appendChild(document.createComment('dj-if'));        // legacy, ignored
        const sp = document.createElement('span');
        sp.textContent = 'content';
        wrap.appendChild(sp);
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        const open = _findDjIfOpenMarker('if-real-0');
        const close = _findDjIfCloseMarker(open);
        expect(close).not.toBeNull();
        expect(close.textContent.trim()).toBe('/dj-if');
    });
});

describe('RemoveSubtree patch', () => {
    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('removes the marker pair + inner content', () => {
        const wrap = document.createElement('div');
        wrap.id = 'wrap';
        wrap.appendChild(document.createComment('dj-if id="if-abc-0"'));
        const inner = document.createElement('span');
        inner.id = 'kept-out';   // smoke marker we keep elsewhere
        inner.textContent = 'x';
        wrap.appendChild(inner);
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        const result = _applyRemoveSubtree({type: 'RemoveSubtree', id: 'if-abc-0'});
        expect(result).toBe(true);

        // The wrap div remains; but its children (markers + span) are gone.
        expect(document.getElementById('wrap')).not.toBeNull();
        expect(document.getElementById('wrap').childNodes.length).toBe(0);
    });

    it('handles the empty-inner-content placeholder pair', () => {
        const wrap = document.createElement('div');
        wrap.id = 'wrap-empty';
        wrap.appendChild(document.createComment('dj-if id="if-empty-0"'));
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        const result = _applyRemoveSubtree({type: 'RemoveSubtree', id: 'if-empty-0'});
        expect(result).toBe(true);
        expect(document.getElementById('wrap-empty').childNodes.length).toBe(0);
    });

    it('removes the OUTER pair when called on outer id (swallows inner)', () => {
        const wrap = document.createElement('div');
        wrap.id = 'wrap-nest';
        wrap.appendChild(document.createComment('dj-if id="outer-0"'));
        const before = document.createElement('span');
        before.textContent = 'before-inner';
        wrap.appendChild(before);
        wrap.appendChild(document.createComment('dj-if id="inner-0"'));
        const innerSpan = document.createElement('span');
        innerSpan.textContent = 'inner-content';
        wrap.appendChild(innerSpan);
        wrap.appendChild(document.createComment('/dj-if'));
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        _applyRemoveSubtree({type: 'RemoveSubtree', id: 'outer-0'});
        expect(document.getElementById('wrap-nest').childNodes.length).toBe(0);
    });

    it('removes ONLY the inner pair when called on inner id (outer survives)', () => {
        const wrap = document.createElement('div');
        wrap.id = 'wrap-nest2';
        wrap.appendChild(document.createComment('dj-if id="outer-0"'));
        wrap.appendChild(document.createComment('dj-if id="inner-0"'));
        const innerSpan = document.createElement('span');
        innerSpan.id = 'inner-content-span';
        innerSpan.textContent = 'inner-content';
        wrap.appendChild(innerSpan);
        wrap.appendChild(document.createComment('/dj-if'));
        const outerSpan = document.createElement('span');
        outerSpan.id = 'outer-content-span';
        outerSpan.textContent = 'outer-after';
        wrap.appendChild(outerSpan);
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        _applyRemoveSubtree({type: 'RemoveSubtree', id: 'inner-0'});
        // Inner content gone; outer content still there.
        expect(document.getElementById('inner-content-span')).toBeNull();
        expect(document.getElementById('outer-content-span')).not.toBeNull();
        // The outer markers also remain — verify both edges.
        const wrapNow = document.getElementById('wrap-nest2');
        const comments = Array.from(wrapNow.childNodes).filter(n => n.nodeType === 8);
        expect(comments.length).toBe(2);
        expect(comments[0].textContent.trim()).toBe('dj-if id="outer-0"');
        expect(comments[1].textContent.trim()).toBe('/dj-if');
    });

    it('returns true (idempotent no-op) when the marker id is not present (#1370 rc8)', () => {
        // Empty DOM, no markers anywhere. RemoveSubtree is inherently
        // idempotent — removing something already gone is semantically a
        // no-op. Return true so the client does not trigger recovery-HTML
        // fallback for a scenario where the desired end-state is achieved.
        const result = _applyRemoveSubtree({type: 'RemoveSubtree', id: 'if-ghost-0'});
        expect(result).toBe(true);
    });

    it('handles a marker pair directly under document.body (no wrapper)', () => {
        document.body.appendChild(document.createComment('dj-if id="if-root-0"'));
        const sp = document.createElement('span');
        sp.id = 'root-content';
        document.body.appendChild(sp);
        document.body.appendChild(document.createComment('/dj-if'));
        // Add an unrelated marker AFTER to verify the close-finder doesn't
        // overshoot. (Without a depth counter this would still work, but
        // it's a useful safety check.)
        const after = document.createElement('span');
        after.id = 'after-marker';
        document.body.appendChild(after);

        _applyRemoveSubtree({type: 'RemoveSubtree', id: 'if-root-0'});
        expect(document.getElementById('root-content')).toBeNull();
        expect(document.getElementById('after-marker')).not.toBeNull();
    });

    it('returns false when patch is missing id', () => {
        const result = _applyRemoveSubtree({type: 'RemoveSubtree'});
        expect(result).toBe(false);
    });
});

describe('InsertSubtree patch', () => {
    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('parses the html fragment and inserts at parent[index]', () => {
        const parent = document.createElement('div');
        parent.id = 'parent';
        parent.setAttribute('dj-id', 'parent-id');
        const existing = document.createElement('span');
        existing.id = 'existing';
        existing.textContent = 'existing';
        parent.appendChild(existing);
        document.body.appendChild(parent);

        const html = '<!--dj-if id="if-new-0"--><span id="inserted">inserted-content</span><!--/dj-if-->';
        const result = _applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'if-new-0',
            html,
            path: [],
            d: 'parent-id',
            index: 0,
        });
        expect(result).toBe(true);

        // Inserted content lives before the existing span.
        const inserted = document.getElementById('inserted');
        expect(inserted).not.toBeNull();
        // Verify the marker pair is present (the open comment is the first
        // child of parent, then the span, then the close comment).
        const childNodes = Array.from(parent.childNodes);
        expect(childNodes[0].nodeType).toBe(8); // open marker
        expect(childNodes[0].textContent.trim()).toBe('dj-if id="if-new-0"');
        expect(childNodes[1]).toBe(inserted);
        expect(childNodes[2].nodeType).toBe(8); // close marker
        expect(childNodes[2].textContent.trim()).toBe('/dj-if');
        expect(childNodes[3]).toBe(existing);
    });

    it('appends to the end when index is out of range', () => {
        const parent = document.createElement('div');
        parent.id = 'parent2';
        parent.setAttribute('dj-id', 'parent2-id');
        document.body.appendChild(parent);

        const html = '<!--dj-if id="if-end-0"--><span id="end-inserted">end</span><!--/dj-if-->';
        _applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'if-end-0',
            html,
            path: [],
            d: 'parent2-id',
            index: 99,
        });
        expect(document.getElementById('end-inserted')).not.toBeNull();
        expect(parent.childNodes.length).toBe(3); // open + span + close
    });

    it('does NOT execute <script> tags inside the html fragment (template parsing keeps them inert)', () => {
        const parent = document.createElement('div');
        parent.id = 'parent-script';
        parent.setAttribute('dj-id', 'parent-script-id');
        document.body.appendChild(parent);

        // Script body sets a sentinel on window — if the script ran, the
        // sentinel would be present.
        dom.window.__inertSentinel = false;
        const html = '<!--dj-if id="if-s-0"--><script>window.__inertSentinel = true;</script><!--/dj-if-->';

        _applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'if-s-0',
            html,
            path: [],
            d: 'parent-script-id',
            index: 0,
        });
        // The script element is in the DOM but did NOT execute (template
        // parsing — children of <template>.content are inert).
        expect(parent.querySelector('script')).not.toBeNull();
        expect(dom.window.__inertSentinel).toBe(false);
    });

    it('returns false when html is missing', () => {
        const parent = document.createElement('div');
        parent.id = 'parent-nohtml';
        parent.setAttribute('dj-id', 'parent-nohtml-id');
        document.body.appendChild(parent);

        const result = _applyInsertSubtree({type: 'InsertSubtree', id: 'x', path: [], d: 'parent-nohtml-id'});
        expect(result).toBe(false);
    });

    it('returns true (idempotent no-op) when marker id is already present in DOM (#1370 rc8)', () => {
        // Set up a DOM that already contains the marker pair for id="if-dup-0".
        // A second InsertSubtree for the same id should be a no-op rather than
        // duplicating content. Symmetric to RemoveSubtree's idempotent check.
        const parent = document.createElement('div');
        parent.id = 'parent-dup';
        parent.setAttribute('dj-id', 'parent-dup-id');
        parent.appendChild(document.createComment('dj-if id="if-dup-0"'));
        const existing = document.createElement('span');
        existing.id = 'dup-existing';
        existing.textContent = 'already here';
        parent.appendChild(existing);
        parent.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(parent);

        const result = _applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'if-dup-0',
            html: '<!--dj-if id="if-dup-0"--><span id="dup-new">new</span><!--/dj-if-->',
            path: [],
            d: 'parent-dup-id',
            index: 0,
        });
        expect(result).toBe(true);
        // Original content preserved; no new span was inserted.
        expect(document.getElementById('dup-existing')).not.toBeNull();
        expect(document.getElementById('dup-new')).toBeNull();
    });

    it('returns false when parent cannot be resolved (deep unresolvable path)', () => {
        // path:[] + unresolvable d falls back to the live-view root (not
        // null), so it does NOT exercise the not-found branch. Use a deep
        // path that overshoots child counts to force null.
        const result = _applyInsertSubtree({
            type: 'InsertSubtree',
            id: 'x',
            html: '<!--dj-if id="x"--><!--/dj-if-->',
            path: [99, 99, 99],
            d: 'never-existed',
        });
        expect(result).toBe(false);
    });
});

describe('applySinglePatch dispatch wiring', () => {
    afterEach(() => {
        document.body.innerHTML = '';
    });

    it('routes RemoveSubtree through applySinglePatch', () => {
        const wrap = document.createElement('div');
        wrap.id = 'wrap-dispatch';
        wrap.appendChild(document.createComment('dj-if id="if-disp-0"'));
        const sp = document.createElement('span');
        sp.id = 'disp-inner';
        wrap.appendChild(sp);
        wrap.appendChild(document.createComment('/dj-if'));
        document.body.appendChild(wrap);

        const result = _applySinglePatch({type: 'RemoveSubtree', id: 'if-disp-0'});
        expect(result).toBe(true);
        expect(document.getElementById('disp-inner')).toBeNull();
    });

    it('routes InsertSubtree through applySinglePatch', () => {
        const parent = document.createElement('div');
        parent.id = 'parent-disp';
        parent.setAttribute('dj-id', 'parent-disp-id');
        document.body.appendChild(parent);

        const result = _applySinglePatch({
            type: 'InsertSubtree',
            id: 'if-ins-0',
            html: '<!--dj-if id="if-ins-0"--><span id="disp-inserted">x</span><!--/dj-if-->',
            path: [],
            d: 'parent-disp-id',
            index: 0,
        });
        expect(result).toBe(true);
        expect(document.getElementById('disp-inserted')).not.toBeNull();
    });

    it('returns true (idempotent no-op) when RemoveSubtree id is unknown via dispatch (#1370 rc8)', () => {
        const result = _applySinglePatch({type: 'RemoveSubtree', id: 'if-ghost-disp-0'});
        expect(result).toBe(true);
    });
});
