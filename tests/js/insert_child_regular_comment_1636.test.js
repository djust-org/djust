/**
 * Regression (#1636): a false→true `{% if %}` add emits an `InsertChild` patch
 * (in the report it carried the conditional block's dj-if marker subtree) that
 * must land at the correct slot even when a *regular* (non-dj-if) HTML comment
 * sits among the parent's children.
 *
 * #1636's `1/N patches failed` was a manifestation of #1640: the index resolver
 * (`getSignificantChildren`) counted ALL comments while the path walker
 * (`getNodeByPath`) and the Rust VDOM parser count only dj-if-family comments.
 * The server therefore emits `InsertChild.index` in regular-comment-EXCLUDED
 * coordinates; a client that counts the regular comment resolves the index one
 * slot early — inserting in the wrong place (mid-insert) or overrunning the
 * child list (logged as "a {% if %} block changed the node count" → patch
 * returns false → the 1/N).
 *
 * #1640 added a predicate-level guard in the `significant_children_comment_filter_1640`
 * suite. THIS file pins the same invariant one layer up — at the APPLIED-PATCH level —
 * for the chronically-reopened if-block cluster (#1358/#1408/#1550/#1552/#1555/
 * #1636): an `InsertChild` whose index points PAST a regular HTML comment lands
 * between the right significant siblings, not one slot early.
 *
 * Gate-off proof (Action #1200/#1468): flipping `isSignificantChild` in
 * `src/12-vdom-patch.js` back to counting all comments and rebuilding makes the
 * `mid-insert` case below fail (X lands before A instead of between A and B).
 * Verified during authoring; see scratch/vdom-1636.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { runScripts: 'dangerously' });
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
}
dom.window.eval(clientCode);

const { _applySinglePatch } = dom.window.djust;
const document = dom.window.document;

function vnode(tag, djId) {
    return { tag, attrs: { 'dj-id': djId }, children: [], text: null, key: null, djust_id: djId };
}

// Significant index (server / Rust coordinates) of the element with the given
// dj-id among its parent's children, EXCLUDING regular HTML comments.
function significantIndexOf(parent, djId) {
    const sig = Array.from(parent.childNodes).filter(
        (n) =>
            n.nodeType === 1 ||
            (n.nodeType === 3 && /\S/.test(n.textContent)) ||
            (n.nodeType === 8 && /^\/?dj-if(\s|$)/.test(n.textContent.trim()))
    );
    return sig.findIndex((n) => n.nodeType === 1 && n.getAttribute('dj-id') === djId);
}

describe('InsertChild lands correctly past a regular HTML comment (#1636)', () => {
    afterEach(() => { document.body.innerHTML = ''; });

    it('mid-insert: a leading regular comment must NOT shift the index (the #1636 trigger)', () => {
        // DOM children: [<!-- banner -->, <a dj-id=A>, <b dj-id=B>]
        // Server VDOM significant children (comment dropped): [A, B].
        // false→true add inserts X at significant index 1 → between A and B.
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'card');
        parent.appendChild(document.createComment(' banner ')); // regular comment — Rust drops it
        const a = document.createElement('a'); a.setAttribute('dj-id', 'A'); a.textContent = '0';
        const b = document.createElement('b'); b.setAttribute('dj-id', 'B'); b.textContent = '1';
        parent.appendChild(a);
        parent.appendChild(b);
        document.body.appendChild(parent);

        const ok = _applySinglePatch(
            { type: 'InsertChild', path: [], d: 'card', index: 1, node: vnode('div', 'X'), ref_d: null },
            parent
        );

        expect(ok).toBe(true); // not a 1/N failure
        // X must sit between A and B in significant coordinates, NOT before A.
        expect(significantIndexOf(parent, 'X')).toBe(1);
        const order = Array.from(parent.childNodes)
            .filter((n) => n.nodeType === 1)
            .map((n) => n.getAttribute('dj-id'));
        expect(order).toEqual(['A', 'X', 'B']);
    });

    it('append-past-comment: index at the significant length appends (no overrun failure)', () => {
        // DOM children: [<a dj-id=A>, <!-- note -->]  → server significant: [A].
        // false→true add appends X at significant index 1 (== length).
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'card');
        const a = document.createElement('a'); a.setAttribute('dj-id', 'A'); a.textContent = '0';
        parent.appendChild(a);
        parent.appendChild(document.createComment(' note ')); // regular comment after A
        document.body.appendChild(parent);

        const ok = _applySinglePatch(
            { type: 'InsertChild', path: [], d: 'card', index: 1, node: vnode('section', 'X'), ref_d: null },
            parent
        );

        expect(ok).toBe(true);
        expect(significantIndexOf(parent, 'X')).toBe(1);
        const order = Array.from(parent.childNodes)
            .filter((n) => n.nodeType === 1)
            .map((n) => n.getAttribute('dj-id'));
        expect(order).toEqual(['A', 'X']);
    });

    it('regular comment among dj-if markers: index still excludes only the regular one', () => {
        // DOM: [<a A>, <!-- x -->, <!--dj-if id="if-q-1"-->, <i body>, <!--/dj-if-->]
        // Server significant (regular comment dropped): [A, dj-if, i, /dj-if].
        // Insert X at index 1 → between A and the dj-if open marker.
        const parent = document.createElement('div');
        parent.setAttribute('dj-id', 'card');
        const a = document.createElement('a'); a.setAttribute('dj-id', 'A');
        parent.appendChild(a);
        parent.appendChild(document.createComment(' x '));                 // regular — dropped
        parent.appendChild(document.createComment('dj-if id="if-q-1"'));   // counted
        const i = document.createElement('i'); i.setAttribute('dj-id', 'body');
        parent.appendChild(i);
        parent.appendChild(document.createComment('/dj-if'));              // counted
        document.body.appendChild(parent);

        const ok = _applySinglePatch(
            { type: 'InsertChild', path: [], d: 'card', index: 1, node: vnode('span', 'X'), ref_d: null },
            parent
        );

        expect(ok).toBe(true);
        // X lands at significant index 1 — immediately before the dj-if open marker.
        expect(significantIndexOf(parent, 'X')).toBe(1);
        // The inserted X must precede the open marker in DOM order.
        const xEl = parent.querySelector('[dj-id="X"]');
        const openMarker = Array.from(parent.childNodes).find(
            (n) => n.nodeType === 8 && n.textContent.includes('dj-if id=')
        );
        expect(xEl.compareDocumentPosition(openMarker) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
});
