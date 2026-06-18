/**
 * JS-CLIENT apply coverage for dj-if `MoveSubtree` — #1826 follow-up.
 *
 * PR #1828 fixed the Rust `diff_html` so a `{% if %}`-wrapped `<tr>` in a
 * `{% for %}` loop emits 0 spurious `MoveSubtree` for later boundaries. But
 * #1828 added only SERVER-side tests (Rust + a Python `diff_html` pack). The
 * ORIGINAL symptom was CLIENT-side: `12-vdom-patch.js`'s `applyMoveSubtree` →
 * `_findDjIfCloseMarker` returning null → `console.warn('MoveSubtree: close
 * marker not found id=%s')` → 15/22 patches fail → `html_recovery` morph drops
 * a row per toggle.
 *
 * The Rust `apply_all` harness is NOT faithful to the JS client: Rust
 * `match_close_idx` pairs within the PARENT's child slice; the JS client walks
 * the WHOLE document with a depth-counting `TreeWalker`
 * (`_findDjIfCloseMarker`, `12-vdom-patch.js:1290`). So GENUINE `MoveSubtree`
 * emissions were unverified against the real client. This file closes that gap.
 *
 * REPRODUCTION FIDELITY (CLAUDE.md #1724):
 *   - imports the REAL `applyMoveSubtree` / `_findDjIfCloseMarker` /
 *     `_applySinglePatch` / `_sortPatches` from
 *     `python/djust/static/djust/src/12-vdom-patch.js` — NOT a reimplementation;
 *   - builds the OLD DOM via `container.innerHTML = "..."` WITH the
 *     inter-element whitespace a real SSR page carries (NOT
 *     `appendChild`/`createElement`, which omit insignificant whitespace text
 *     nodes — that omission hides the real misalignment bug);
 *   - models the dj-if markers as real `<!--dj-if id="...">` / `<!--/dj-if-->`
 *     comment nodes parsed from that HTML;
 *   - applies the patch stream the CORRECTED `diff_html` actually emits for each
 *     transition (captured from `djust._rust.diff_html` against the exact
 *     strings, then transcribed here), via the same sort+dispatch the real
 *     `_applyPatchesInner` ≤10 path runs.
 *
 * RESULT: the real client PASSES all four shapes — 0 "close marker not found"
 * warnings, DOM correct (all rows present, boundary positioned right). No client
 * change was needed; these tests ARE the coverage that closes the JS-apply gap
 * the server-only #1828 tests left open. The `_sortPatches`/`_applySinglePatch`
 * gate makes any future client regression (e.g. a `_findDjIfCloseMarker`
 * scoping change that re-pairs the wrong close marker) fail-fast here.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

// Load JUST the real VDOM-patch source module (not the bundled client.js).
// 12-vdom-patch.js is self-contained for the dj-if marker logic — every helper
// (sanitizeIdForLog, isDjIfComment, getNodeByPath, getSignificantChildren,
// _findDjIfOpenMarker, _findDjIfCloseMarker, applyMoveSubtree, applyInsertSubtree,
// applyRemoveSubtree, _applySinglePatch, _sortPatches) is defined in this file.
// Its only external dependency is the `window.djust` namespace object (normally
// created by 00-namespace.js), which we seed below.
const vdomPatchCode = readFileSync(
    './python/djust/static/djust/src/12-vdom-patch.js',
    'utf-8'
);

let dom;
let document;
let warnSpy;

beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        runScripts: 'dangerously',
    });
    // Seed the namespace object 00-namespace.js would otherwise create, plus the
    // CSS.escape shim other JSDOM tests rely on.
    dom.window.eval('window.djust = window.djust || {};');
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) {
        dom.window.CSS.escape = (s) => String(s).replace(/([^\w-])/g, '\\$1');
    }
    dom.window.eval(vdomPatchCode);
    document = dom.window.document;

    // Spy on console.warn so we can assert the "close marker not found" warning
    // (the #1826 client symptom) is NEVER emitted.
    warnSpy = [];
    dom.window.console.warn = (...args) => {
        warnSpy.push(args.map((a) => String(a)).join(' '));
    };
});

afterEach(() => {
    document.body.innerHTML = '';
});

/**
 * Apply a patch stream the way the real `_applyPatchesInner` does for the small
 * (≤10) case: sort into 4-phase order via the real `_sortPatches`, then dispatch
 * each patch via the real `_applySinglePatch`. Both are the production functions
 * exported on `window.djust` — no reimplementation. Returns the failed-patch
 * count (what `_applyPatchesInner` uses to decide whether to trigger
 * `html_recovery`).
 */
function applyStream(patches, rootEl = null) {
    const { _sortPatches, _applySinglePatch } = dom.window.djust;
    _sortPatches(patches);
    let failed = 0;
    for (const patch of patches) {
        if (!_applySinglePatch(patch, rootEl)) failed++;
    }
    return failed;
}

/** Significant-child kinds (comments rendered as `<!--text-->`, elements as tag). */
function significantKinds(el) {
    return Array.from(el.childNodes)
        .filter((n) => {
            if (n.nodeType === 3) return (n.textContent || '').trim() !== '';
            return true;
        })
        .map((n) => (n.nodeType === 8 ? `<!--${n.textContent}-->` : n.tagName));
}

function closeMarkerWarnings() {
    return warnSpy.filter((w) => w.includes('close marker not found'));
}

describe('dj-if MoveSubtree — JS-client apply (#1826 follow-up)', () => {
    // ------------------------------------------------------------------
    // Shape 1 — the reporter's all-fill case.
    // {% if %}<tr> in a 3-iteration loop, empty -> filled. The corrected
    // diff_html emits 0 MoveSubtree (3 plain InsertChild). Asserts a clean
    // apply with all 3 detail rows present inside their own boundaries.
    //
    // Production container is <table><tbody>; we use that exact ancestor so
    // <tr> survives html5ever (the bare-<tbody> #text foster-parenting of
    // #1827 is an environment artifact, not the production path).
    // ------------------------------------------------------------------
    it('Shape 1: all-fill <tr>-in-loop emits 0 MoveSubtree and applies clean (all 3 rows)', () => {
        const table = document.createElement('table');
        document.body.appendChild(table);
        // innerHTML WITH the inter-element whitespace a real SSR page carries.
        table.innerHTML =
            '<tbody dj-id="0">\n' +
            '  <tr class="main" dj-id="1"><td>a</td></tr>\n' +
            '  <!--dj-if id="if-a-0"--><!--/dj-if-->\n' +
            '  <tr class="main" dj-id="3"><td>b</td></tr>\n' +
            '  <!--dj-if id="if-b-0"--><!--/dj-if-->\n' +
            '  <tr class="main" dj-id="5"><td>c</td></tr>\n' +
            '  <!--dj-if id="if-c-0"--><!--/dj-if-->\n' +
            '</tbody>';
        const tbody = table.querySelector('tbody');

        // Corrected diff_html stream: 3 InsertChild at significant indices 2,6,10
        // (the body of each empty boundary). NO MoveSubtree (the #1826 fix).
        const mkRow = (index, djId, td_id, text) => ({
            type: 'InsertChild',
            path: [0],
            d: '0',
            index,
            node: {
                tag: 'tr',
                attrs: { 'dj-id': djId, class: 'detail' },
                children: [
                    {
                        tag: 'td',
                        attrs: { 'dj-id': td_id },
                        children: [{ tag: '#text', attrs: {}, children: [], text, key: null }],
                        text: null,
                        key: null,
                        djust_id: td_id,
                    },
                ],
                text: null,
                key: null,
                djust_id: djId,
            },
        });
        const patches = [
            mkRow(2, '4', '5', 'detail a'),
            mkRow(6, '8', '9', 'detail b'),
            mkRow(10, '12', '13', 'detail c'),
        ];

        const failed = applyStream(patches, null);

        expect(closeMarkerWarnings()).toEqual([]);
        expect(warnSpy).toEqual([]);
        expect(failed).toBe(0);

        // All three detail rows present, each inside its OWN boundary.
        expect(significantKinds(tbody)).toEqual([
            'TR',                       // main a
            '<!--dj-if id="if-a-0"-->',
            'TR',                       // detail a
            '<!--/dj-if-->',
            'TR',                       // main b
            '<!--dj-if id="if-b-0"-->',
            'TR',                       // detail b
            '<!--/dj-if-->',
            'TR',                       // main c
            '<!--dj-if id="if-c-0"-->',
            'TR',                       // detail c
            '<!--/dj-if-->',
        ]);
        const text = tbody.textContent.replace(/\s+/g, ' ').trim();
        for (const want of ['detail a', 'detail b', 'detail c']) {
            expect(text).toContain(want);
        }
    });

    // ------------------------------------------------------------------
    // Shape 2 — genuine reposition.
    // A real <section> inserted BEFORE a matched (empty) boundary so the
    // boundary genuinely shifts. The corrected diff_html DOES emit
    // MoveSubtree if-a-0 (index 1). Assert the client pairs the close marker
    // and repositions correctly, no warning.
    // ------------------------------------------------------------------
    it('Shape 2: genuine reposition — client pairs close marker + moves boundary, no warning', () => {
        const root = document.createElement('div');
        root.setAttribute('dj-id', '0');
        document.body.appendChild(root);
        root.innerHTML =
            '\n  <!--dj-if id="if-a-0"--><!--/dj-if-->\n' +
            '  <p dj-id="2">keep</p>\n';

        // Corrected stream (real reposition): InsertChild(<section> @0) shifts the
        // boundary's non-boundary-sibling count 0->1 -> MoveSubtree if-a-0 @1.
        const insertSection = {
            type: 'InsertChild',
            path: [],
            d: '0',
            index: 0,
            node: {
                tag: 'section',
                attrs: { 'dj-id': '1' },
                children: [{ tag: '#text', attrs: {}, children: [], text: 'new', key: null }],
                text: null,
                key: null,
                djust_id: '1',
            },
        };
        const moveBoundary = { type: 'MoveSubtree', id: 'if-a-0', path: [], d: '0', index: 1 };

        const failed = applyStream([insertSection, moveBoundary], null);

        expect(closeMarkerWarnings()).toEqual([]);
        expect(warnSpy).toEqual([]);
        expect(failed).toBe(0);
        // <section> first, then the boundary span (at significant index 1), then <p>.
        expect(significantKinds(root)).toEqual([
            'SECTION',
            '<!--dj-if id="if-a-0"-->',
            '<!--/dj-if-->',
            'P',
        ]);
    });

    // ------------------------------------------------------------------
    // Shape 3 — the 🟡 redundant-move shape (from the #1828 review).
    // A boundary REMOVED before a matched empty boundary: diff_html emits
    // RemoveSubtree if-x-0 PLUS a redundant MoveSubtree if-a-0 (the sibling
    // shift already relocates it). Assert the real client still pairs both
    // cleanly — no "close marker not found" — and the DOM is correct.
    // ------------------------------------------------------------------
    it('Shape 3: redundant move (remove-boundary-before-matched-empty) applies clean', () => {
        const root = document.createElement('div');
        root.setAttribute('dj-id', '0');
        document.body.appendChild(root);
        root.innerHTML =
            '\n  <!--dj-if id="if-x-0"--><span dj-id="1">x</span><!--/dj-if-->\n' +
            '  <!--dj-if id="if-a-0"--><!--/dj-if-->\n' +
            '  <p dj-id="2">keep</p>\n';

        // Corrected stream: RemoveSubtree if-x-0 (phase -2) + redundant
        // MoveSubtree if-a-0 @0 (phase 3). The redundant move must still pair
        // its close marker against the post-removal DOM.
        const removeX = { type: 'RemoveSubtree', id: 'if-x-0' };
        const moveA = { type: 'MoveSubtree', id: 'if-a-0', path: [], d: '0', index: 0 };

        const failed = applyStream([removeX, moveA], null);

        expect(closeMarkerWarnings()).toEqual([]);
        expect(warnSpy).toEqual([]);
        expect(failed).toBe(0);
        // if-x gone; if-a boundary + <p keep> remain.
        expect(significantKinds(root)).toEqual([
            '<!--dj-if id="if-a-0"-->',
            '<!--/dj-if-->',
            'P',
        ]);
        expect(root.querySelector('span')).toBeNull(); // the removed boundary's body
        expect(root.querySelector('p').textContent).toBe('keep');
    });

    // ------------------------------------------------------------------
    // Shape 4 — nested dj-if boundaries with a move.
    // An OUTER boundary whose body contains an INNER boundary. A real
    // <section> inserted before the outer moves it (MoveSubtree if-outer-0).
    // The document-wide depth-walk in `_findDjIfCloseMarker` must pair the
    // OUTER open with the OUTER close (skipping the inner pair via the depth
    // counter) — NOT stop at the inner close. If it stopped early, the move
    // range would be wrong and rows would be lost / the inner boundary
    // orphaned.
    // ------------------------------------------------------------------
    it('Shape 4: nested boundaries — depth-walk pairs the RIGHT (outer) close marker on move', () => {
        const root = document.createElement('div');
        root.setAttribute('dj-id', '0');
        document.body.appendChild(root);
        root.innerHTML =
            '\n  <!--dj-if id="if-outer-0"-->' +
            '<span dj-id="1">o</span>' +
            '<!--dj-if id="if-inner-0"--><em dj-id="2">i</em><!--/dj-if-->' +
            '<!--/dj-if-->\n' +
            '  <p dj-id="3">keep</p>\n';

        // Sanity: the depth-walk pairs the OUTER open with the OUTER (second)
        // /dj-if, not the inner one. Exercise the real helper directly.
        const { _findDjIfOpenMarker, _findDjIfCloseMarker } = dom.window.djust;
        const outerOpen = _findDjIfOpenMarker('if-outer-0', null);
        expect(outerOpen).not.toBeNull();
        const outerClose = _findDjIfCloseMarker(outerOpen);
        expect(outerClose).not.toBeNull();
        // The outer close must come AFTER the inner close in document order:
        // walking from outerOpen, the LAST /dj-if before <p> is the outer close.
        // Verify by counting /dj-if comments between open and the returned close —
        // there must be exactly one inner /dj-if strictly before it (depth handled).
        let innerClosesBefore = 0;
        let cur = outerOpen.nextSibling;
        while (cur && cur !== outerClose) {
            if (cur.nodeType === 8 && (cur.textContent || '').trim() === '/dj-if') {
                innerClosesBefore += 1;
            }
            cur = cur.nextSibling;
        }
        expect(innerClosesBefore).toBe(1); // the inner /dj-if is inside the outer span

        // Corrected stream: InsertChild(<section> @0) + MoveSubtree if-outer-0 @1.
        const insertSection = {
            type: 'InsertChild',
            path: [],
            d: '0',
            index: 0,
            node: {
                tag: 'section',
                attrs: { 'dj-id': '4' },
                children: [{ tag: '#text', attrs: {}, children: [], text: 'new', key: null }],
                text: null,
                key: null,
                djust_id: '4',
            },
        };
        const moveOuter = { type: 'MoveSubtree', id: 'if-outer-0', path: [], d: '0', index: 1 };

        const failed = applyStream([insertSection, moveOuter], null);

        expect(closeMarkerWarnings()).toEqual([]);
        expect(warnSpy).toEqual([]);
        expect(failed).toBe(0);

        // The WHOLE outer span (with the nested inner boundary + <em> intact)
        // moved as one unit to significant index 1 — between <section> and <p>.
        expect(significantKinds(root)).toEqual([
            'SECTION',
            '<!--dj-if id="if-outer-0"-->',
            'SPAN',
            '<!--dj-if id="if-inner-0"-->',
            'EM',
            '<!--/dj-if-->',
            '<!--/dj-if-->',
            'P',
        ]);
        // Inner boundary + content preserved INSIDE the moved outer span.
        expect(root.querySelector('em').textContent).toBe('i');
        expect(root.querySelector('span').textContent).toBe('o');
        expect(root.querySelector('p').textContent).toBe('keep');
    });
});
