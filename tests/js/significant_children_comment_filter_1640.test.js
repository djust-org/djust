/**
 * Regression: getSignificantChildren must count ONLY dj-if comments, matching
 * getNodeByPath and the Rust VDOM parser (which drops all non-dj-if comments)
 * (#1640).
 *
 * The bug: getSignificantChildren counted ALL comment nodes, while
 * getNodeByPath (path traversal) counts only dj-if-family comments. Index-based
 * patches (InsertChild / RemoveChild / MoveChild) resolve their `index` via
 * getSignificantChildren. When a parent has a *regular* HTML comment among its
 * children, the server's VDOM child indices skip it (Rust drops it) but the
 * client counted it — so the index pointed one slot off.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { runScripts: 'dangerously' });
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
}
dom.window.eval(clientCode);

const { getSignificantChildren } = dom.window.djust;
const document = dom.window.document;
const COMMENT = dom.window.Node.COMMENT_NODE;

describe('getSignificantChildren comment filtering (#1640)', () => {
    it('EXCLUDES a regular (non-dj-if) HTML comment among siblings', () => {
        const parent = document.createElement('div');
        parent.innerHTML = '<span>a</span><!-- regular comment --><span>b</span>';
        const children = getSignificantChildren(parent);
        // Server VDOM (Rust) drops the regular comment → 2 significant children.
        expect(children.length).toBe(2);
        expect(children.map((c) => c.tagName)).toEqual(['SPAN', 'SPAN']);
    });

    it('still INCLUDES dj-if boundary-marker comments', () => {
        const parent = document.createElement('div');
        parent.innerHTML = '<span>a</span><!--dj-if--><!--/dj-if--><span>b</span>';
        const children = getSignificantChildren(parent);
        // span, dj-if, /dj-if, span → 4 (markers ARE counted, like Rust + getNodeByPath).
        expect(children.length).toBe(4);
        expect(children[1].nodeType).toBe(COMMENT);
        expect(children[2].nodeType).toBe(COMMENT);
    });

    it('includes the boundary-open marker form `dj-if id="..."`', () => {
        const parent = document.createElement('div');
        parent.appendChild(document.createComment('dj-if id="if-x-1"'));
        parent.appendChild(document.createElement('span'));
        parent.appendChild(document.createComment('/dj-if'));
        parent.appendChild(document.createComment(' not a marker '));
        const children = getSignificantChildren(parent);
        // open-marker, span, close-marker counted; the trailing regular comment dropped.
        expect(children.length).toBe(3);
    });

    it('agrees with the getNodeByPath comment predicate (the core invariant)', () => {
        // getNodeByPath counts elements + significant text + dj-if comments only.
        // getSignificantChildren must produce the SAME count so index-based and
        // path-based resolution never disagree.
        const parent = document.createElement('div');
        parent.innerHTML =
            '<i>0</i><!-- x --><b>1</b><!--dj-if--><u>2</u><!-- y --><!--/dj-if-->';
        const children = getSignificantChildren(parent);
        // i, b, dj-if, u, /dj-if = 5 (the two regular comments dropped).
        const kinds = children.map((c) => (c.nodeType === COMMENT ? c.textContent.trim() : c.tagName));
        expect(kinds).toEqual(['I', 'B', 'dj-if', 'U', '/dj-if']);
    });
});
