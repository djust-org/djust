/**
 * #1655: `isSignificantChild` is the single source of truth for "does this
 * child count toward VDOM child indices", shared by getNodeByPath (path
 * walker) and getSignificantChildren (index resolver). Before #1655 the two
 * had independently-written copies of the rule; the copy in getSignificantChildren
 * drifted to count ALL comments, causing the #1640 index mismatch. Locking the
 * predicate here makes a future re-divergence a test failure.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { runScripts: 'dangerously' });
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) dom.window.CSS.escape = (v) => String(v);
dom.window.eval(clientCode);

const { isSignificantChild, getSignificantChildren } = dom.window.djust;
const document = dom.window.document;

function el(tag) { return document.createElement(tag); }
function txt(s) { return document.createTextNode(s); }
function cmt(s) { return document.createComment(s); }

describe('isSignificantChild shared predicate (#1655)', () => {
    it('counts elements', () => {
        expect(isSignificantChild(el('div'))).toBe(true);
    });

    it('counts non-whitespace text, drops ASCII-whitespace-only text', () => {
        expect(isSignificantChild(txt('hello'))).toBe(true);
        expect(isSignificantChild(txt('   \n\t '))).toBe(false);
    });

    it('treats a non-breaking space as significant', () => {
        expect(isSignificantChild(txt(' '))).toBe(true);
    });

    it('keeps ALL text when preserveWhitespace=true (pre/code/textarea)', () => {
        expect(isSignificantChild(txt('   \n  '), true)).toBe(true);
    });

    it('counts ONLY dj-if-family comments', () => {
        expect(isSignificantChild(cmt('dj-if'))).toBe(true);
        expect(isSignificantChild(cmt('/dj-if'))).toBe(true);
        expect(isSignificantChild(cmt('dj-if id="if-x-1"'))).toBe(true);
        expect(isSignificantChild(cmt(' a regular comment '))).toBe(false);
        expect(isSignificantChild(cmt('dj-iffy'))).toBe(false);
    });

    it('getSignificantChildren is consistent with the predicate', () => {
        // The exported resolver must agree with the predicate node-for-node.
        const parent = el('div');
        parent.innerHTML =
            '<i>0</i><!-- x --><b>1</b><!--dj-if--><u>2</u><!--/dj-if-->';
        const viaResolver = getSignificantChildren(parent);
        const viaPredicate = Array.from(parent.childNodes).filter((c) =>
            isSignificantChild(c, false)
        );
        expect(viaResolver).toEqual(viaPredicate);
        // i, b, dj-if, u, /dj-if = 5 (the regular comment dropped).
        expect(viaResolver.length).toBe(5);
    });
});
