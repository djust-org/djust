/**
 * Tests for the client-side `dj-if` comment predicate (`isDjIfComment`)
 * and the `getNodeByPath` path-fallback that uses it.
 *
 * Foundation 1 of #1358 — Stage 11 SHOULD-FIX #3 on PR #1363. The
 * server's `crates/djust_vdom/src/parser.rs:494-499` preserves three
 * shapes of `dj-if` comment as VDOM children:
 *   - exact `dj-if` (legacy single-comment placeholder, #295)
 *   - `dj-if<space-or-tab>...` (boundary opening marker, #1358 Iter 1)
 *   - `/dj-if` (boundary closing marker)
 *
 * The client's path-fallback filter must mirror this predicate so the
 * client and server count the same comment nodes when traversing.
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
    dom.window.CSS.escape = (str) => str.replace(/([^\w-])/g, '\\$1');
}

dom.window.eval(clientCode);

const { _isDjIfComment, _getNodeByPath } = dom.window.djust;
const document = dom.window.document;

describe('isDjIfComment predicate (#1358 Iter 1)', () => {
    describe('positive matches', () => {
        it('matches exact "dj-if" (legacy placeholder, #295)', () => {
            expect(_isDjIfComment('dj-if')).toBe(true);
        });

        it('matches "/dj-if" (boundary close)', () => {
            expect(_isDjIfComment('/dj-if')).toBe(true);
        });

        it('matches "dj-if id=\\"if-0\\"" (boundary open with id)', () => {
            expect(_isDjIfComment('dj-if id="if-0"')).toBe(true);
        });

        it('matches "dj-if id=\\"if-a3b1c2d4-0\\"" (prefixed id)', () => {
            // After Stage 11 fix on PR #1363, IDs include a per-source
            // prefix. The predicate must accept the longer form.
            expect(_isDjIfComment('dj-if id="if-a3b1c2d4-0"')).toBe(true);
        });

        it('matches "dj-if<TAB>id=..." (tab separator)', () => {
            expect(_isDjIfComment('dj-if\tid="if-0"')).toBe(true);
        });

        it('tolerates leading whitespace', () => {
            expect(_isDjIfComment(' dj-if')).toBe(true);
            expect(_isDjIfComment('  dj-if id="if-0"')).toBe(true);
        });

        it('tolerates trailing whitespace', () => {
            expect(_isDjIfComment('dj-if ')).toBe(true);
            expect(_isDjIfComment('dj-if id="if-0" ')).toBe(true);
            expect(_isDjIfComment('/dj-if ')).toBe(true);
        });
    });

    describe('negative matches (lookalikes must NOT match)', () => {
        it('rejects "dj-iffy"', () => {
            // Looks similar but is a different identifier — comments
            // with this body must not be treated as dj-if markers.
            expect(_isDjIfComment('dj-iffy')).toBe(false);
        });

        it('rejects "dj-if-extra"', () => {
            expect(_isDjIfComment('dj-if-extra')).toBe(false);
        });

        it('rejects "dj-ifid=..." (no separator)', () => {
            expect(_isDjIfComment('dj-ifid="if-0"')).toBe(false);
        });

        it('rejects "/dj-iffy" (close-marker lookalike)', () => {
            expect(_isDjIfComment('/dj-iffy')).toBe(false);
        });

        it('rejects "/dj-if-extra"', () => {
            expect(_isDjIfComment('/dj-if-extra')).toBe(false);
        });

        it('rejects empty string', () => {
            expect(_isDjIfComment('')).toBe(false);
        });

        it('rejects unrelated comments', () => {
            expect(_isDjIfComment('Hero Section')).toBe(false);
            expect(_isDjIfComment(' regular comment ')).toBe(false);
        });

        it('rejects non-string input', () => {
            expect(_isDjIfComment(null)).toBe(false);
            expect(_isDjIfComment(undefined)).toBe(false);
            expect(_isDjIfComment(42)).toBe(false);
        });
    });
});

describe('getNodeByPath - dj-if-family comments counted in path fallback (#1358)', () => {
    let root;

    beforeEach(() => {
        root = document.createElement('div');
        root.setAttribute('dj-root', '');
        document.body.appendChild(root);
    });

    afterEach(() => {
        root.remove();
    });

    it('counts dj-if boundary-open marker as a sibling', () => {
        // Server-emitted shape:
        //   <span>A</span><!--dj-if id="if-0"--><div>X</div><!--/dj-if--><span>B</span>
        // Significant children: span(0), comment(1), div(2), comment(3), span(4)
        root.innerHTML = '<span>A</span><!--dj-if id="if-0"--><div>X</div><!--/dj-if--><span>B</span>';

        const idx0 = _getNodeByPath([0], null);
        expect(idx0?.tagName).toBe('SPAN');
        expect(idx0?.textContent).toBe('A');

        const idx1 = _getNodeByPath([1], null);
        expect(idx1?.nodeType).toBe(dom.window.Node.COMMENT_NODE);
        expect(idx1?.textContent).toContain('dj-if');

        const idx2 = _getNodeByPath([2], null);
        expect(idx2?.tagName).toBe('DIV');
        expect(idx2?.textContent).toBe('X');

        const idx3 = _getNodeByPath([3], null);
        expect(idx3?.nodeType).toBe(dom.window.Node.COMMENT_NODE);
        expect(idx3?.textContent).toBe('/dj-if');

        const idx4 = _getNodeByPath([4], null);
        expect(idx4?.tagName).toBe('SPAN');
        expect(idx4?.textContent).toBe('B');
    });

    it('counts prefixed-id dj-if boundary-open marker', () => {
        // After Stage 11 prefix fix, IDs are `if-<8hex>-N`.
        root.innerHTML =
            '<span>A</span><!--dj-if id="if-deadbeef-0"--><div>X</div><!--/dj-if-->';

        const idx0 = _getNodeByPath([0], null);
        expect(idx0?.tagName).toBe('SPAN');
        const idx1 = _getNodeByPath([1], null);
        expect(idx1?.nodeType).toBe(dom.window.Node.COMMENT_NODE);
        expect(idx1?.textContent).toContain('if-deadbeef-0');
        const idx2 = _getNodeByPath([2], null);
        expect(idx2?.tagName).toBe('DIV');
    });

    it('does NOT count regular comments in path traversal', () => {
        // <!-- Hero Section --> is a regular comment that the server's
        // VDOM parser drops. The client must skip it too, otherwise
        // path indices would drift.
        root.innerHTML =
            '<span>A</span><!-- Hero Section --><span>B</span><span>C</span>';

        // Significant children (server-side): span(A), span(B), span(C).
        const idx1 = _getNodeByPath([1], null);
        expect(idx1?.tagName).toBe('SPAN');
        expect(idx1?.textContent).toBe('B');
    });

    it('does NOT count dj-if lookalikes', () => {
        // <!--dj-iffy--> is a lookalike — predicate rejects, path
        // traversal must skip it.
        root.innerHTML =
            '<span>A</span><!--dj-iffy--><span>B</span>';

        const idx1 = _getNodeByPath([1], null);
        expect(idx1?.tagName).toBe('SPAN');
        expect(idx1?.textContent).toBe('B');
    });

    it('still counts the legacy <!--dj-if--> placeholder', () => {
        // Issue #295 / DJE-053: the legacy single-comment placeholder
        // for false-no-else pure-text conditionals is preserved on both
        // client and server.
        root.innerHTML =
            '<span>A</span><!--dj-if--><span>B</span>';

        const idx0 = _getNodeByPath([0], null);
        expect(idx0?.tagName).toBe('SPAN');
        const idx1 = _getNodeByPath([1], null);
        expect(idx1?.nodeType).toBe(dom.window.Node.COMMENT_NODE);
        expect(idx1?.textContent).toBe('dj-if');
        const idx2 = _getNodeByPath([2], null);
        expect(idx2?.tagName).toBe('SPAN');
    });
});
