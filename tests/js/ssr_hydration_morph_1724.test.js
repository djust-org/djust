/**
 * Regression test for #1724 — SSR→hydration must MORPH the dj-view root's
 * top-level children in place, not remove+re-add them.
 *
 * Symptom (reporter): on the first WebSocket hydration of a server-rendered
 * LiveView page, the dj-view root's direct children are torn down and rebuilt
 * (MutationObserver records REMOVED + ADDED for each child) instead of being
 * morphed. This causes a full visible re-render on every navigation and
 * destroys client-side widget state mounted on those nodes (e.g. a Chart.js
 * <canvas> goes blank because its containing subtree is replaced).
 *
 * CORRECTED ROOT CAUSE — whitespace text-node misalignment:
 *   Real SSR HTML parsed into the DOM via innerHTML carries inter-element
 *   whitespace text nodes (the newlines/indentation between sibling elements).
 *   The hydrated WS-mount HTML (data.html, Rust-rendered) carries `dj-id` on
 *   every element (parser.rs:420) — NOT a standard `id` — and may have
 *   different inter-element whitespace. In morphChildren, when the desired
 *   node is an element but the positional existing node lands on an
 *   insignificant whitespace text node, every element-matching strategy is
 *   skipped (they require nodeType === ELEMENT_NODE) and the code falls to
 *   Strategy 3 (clone+insert) + removes the real existing element in the
 *   unmatched-cleanup loop. That is the wholesale remove+add.
 *
 * Fidelity note (CLAUDE.md "Reproduction fidelity" #1650/#1638/#1637): the
 * existing side is built via innerHTML WITH inter-element whitespace, and the
 * desired side carries `dj-id` (what the Rust renderer actually emits), NOT a
 * synthetic standard `id`. Building the existing side with appendChild (no
 * whitespace) or using `id` would NOT exercise the real path.
 *
 * This test loads the REAL morphChildren from the built client bundle.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
    url: 'http://localhost/',
});

if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = function (value) {
        return String(value).replace(/([^\w-])/g, '\\$1');
    };
}

dom.window.eval(clientCode);

const { morphChildren } = dom.window.djust;
const document = dom.window.document;

/**
 * Build a parent <div dj-view="X"> via innerHTML so the DOM contains the
 * inter-element whitespace text nodes a real SSR page has. The .main-wrapper
 * child holds a Chart.js <canvas> + an instance marker that must survive.
 */
function buildSsrExisting() {
    const root = document.createElement('div');
    root.setAttribute('dj-view', 'app.views.UnitsView');
    // innerHTML preserves inter-element whitespace text nodes (real SSR shape).
    root.innerHTML =
        '<div class="mobile-header">SSR header</div>\n' +
        '  <div class="main-wrapper">\n' +
        '    <canvas class="chart"></canvas>\n' +
        '  </div>\n';

    const header = root.querySelector('.mobile-header');
    const wrapper = root.querySelector('.main-wrapper');
    const canvas = root.querySelector('canvas');
    // Simulate a mounted Chart.js instance on the canvas.
    canvas._chartInstance = { id: 'chart-42' };
    return { root, header, wrapper, canvas };
}

/**
 * Build the desired (hydrated WS-mount) fragment mirroring the Rust renderer:
 * every element carries `dj-id` (NOT a standard `id`). `wsWhitespace` controls
 * whether the desired side has matching inter-element whitespace or not, to
 * exercise both the differing-whitespace and same-whitespace shapes.
 */
function buildDesired({ wsWhitespace }) {
    const temp = document.createElement('div');
    if (wsWhitespace) {
        temp.innerHTML =
            '<div class="mobile-header" dj-id="e1">WS header</div>\n' +
            '  <div class="main-wrapper" dj-id="e2">\n' +
            '    <canvas class="chart" dj-id="e3"></canvas>\n' +
            '  </div>\n';
    } else {
        // No inter-element whitespace on the desired side — the misalignment
        // case that broke pre-fix even when the existing side has whitespace.
        temp.innerHTML =
            '<div class="mobile-header" dj-id="e1">WS header</div>' +
            '<div class="main-wrapper" dj-id="e2"><canvas class="chart" dj-id="e3"></canvas></div>';
    }
    return temp;
}

describe('#1724 SSR→hydration morphs whitespace-separated root children in place', () => {
    it('preserves element identity + Chart.js canvas when desired whitespace differs (the bug)', () => {
        const { root, header, wrapper, canvas } = buildSsrExisting();
        const desired = buildDesired({ wsWhitespace: false });

        morphChildren(root, desired);

        const newHeader = root.querySelector('.mobile-header');
        const newWrapper = root.querySelector('.main-wrapper');
        const newCanvas = root.querySelector('canvas');

        // Element IDENTITY must be preserved — same DOM nodes, not clones.
        expect(newHeader).toBe(header);
        expect(newWrapper).toBe(wrapper);
        // The Chart.js canvas (and its mounted instance) must survive.
        expect(newCanvas).toBe(canvas);
        expect(newCanvas._chartInstance).toBeDefined();
        expect(newCanvas._chartInstance.id).toBe('chart-42');
        // dj-id must have been adopted onto the SAME nodes.
        expect(newHeader.getAttribute('dj-id')).toBe('e1');
        expect(newWrapper.getAttribute('dj-id')).toBe('e2');
        expect(newCanvas.getAttribute('dj-id')).toBe('e3');
        // Updated text content reaches the user.
        expect(newHeader.textContent).toBe('WS header');
    });

    it('preserves element identity + canvas when whitespace matches both sides', () => {
        const { root, header, wrapper, canvas } = buildSsrExisting();
        const desired = buildDesired({ wsWhitespace: true });

        morphChildren(root, desired);

        expect(root.querySelector('.mobile-header')).toBe(header);
        expect(root.querySelector('.main-wrapper')).toBe(wrapper);
        expect(root.querySelector('canvas')).toBe(canvas);
        expect(root.querySelector('canvas')._chartInstance).toBeDefined();
    });

    it('does NOT remove+re-add the element children (no MutationObserver churn)', () => {
        const { root, header, wrapper } = buildSsrExisting();
        const desired = buildDesired({ wsWhitespace: false });

        const beforeElements = Array.from(root.children);
        morphChildren(root, desired);
        const afterElements = Array.from(root.children);

        // Same element node objects in the same order — no teardown/rebuild.
        expect(afterElements.length).toBe(2);
        expect(afterElements[0]).toBe(beforeElements[0]);
        expect(afterElements[1]).toBe(beforeElements[1]);
        expect(afterElements[0]).toBe(header);
        expect(afterElements[1]).toBe(wrapper);
    });

    it('does NOT skip significant whitespace inside a <pre> (whitespace-preserving guard)', () => {
        // Inside <pre>/<code>/<textarea> ALL text is significant. The
        // whitespace-skip must NOT fire there, or code-block content corrupts.
        const pre = document.createElement('pre');
        pre.innerHTML = 'line1\n  line2'; // single text node with significant ws
        const origText = pre.childNodes[0];

        const desired = document.createElement('pre');
        desired.innerHTML = 'line1\n  line2';

        morphChildren(pre, desired);

        expect(pre.childNodes.length).toBe(1);
        expect(pre.childNodes[0]).toBe(origText);
        expect(pre.textContent).toBe('line1\n  line2');
    });

    it('does NOT skip a dj-if boundary comment marker (conditional-subtree guard, #1678)', () => {
        // dj-if comment markers are significant children — the whitespace-skip
        // must not advance past them when aligning an element.
        const root = document.createElement('div');
        root.setAttribute('dj-view', 'X');
        root.innerHTML =
            '<!--dj-if id="if-abc12345-0"-->\n' +
            '  <div class="cond">SSR</div>\n' +
            '<!--/dj-if-->\n' +
            '  <div class="after">A</div>\n';
        const cond = root.querySelector('.cond');
        const after = root.querySelector('.after');

        const desired = document.createElement('div');
        desired.innerHTML =
            '<!--dj-if id="if-abc12345-0"-->\n' +
            '  <div class="cond" dj-id="e1">WS</div>\n' +
            '<!--/dj-if-->\n' +
            '  <div class="after" dj-id="e2">A</div>\n';

        morphChildren(root, desired);

        // The dj-if open/close comment markers must still be present.
        const comments = Array.from(root.childNodes).filter(n => n.nodeType === 8);
        const hasOpen = comments.some(c => /dj-if id=/.test(c.textContent));
        const hasClose = comments.some(c => /\/dj-if/.test(c.textContent));
        expect(hasOpen).toBe(true);
        expect(hasClose).toBe(true);
        // Element identity preserved (not torn down across the marker).
        expect(root.querySelector('.cond')).toBe(cond);
        expect(root.querySelector('.after')).toBe(after);
    });

    // #1737 — symptom-level: with the SERVER fix in place the initial-GET
    // dj-root is normalized identically to the first WS frame (comments
    // stripped, inter-element whitespace collapsed, dj-if markers + <pre>
    // preserved), differing only by the dj-id attrs the client stamps.
    // morphChildren over (SSR-normalized, WS-frame) must produce ZERO churn:
    // the same element nodes survive in place, so there is no flash. The
    // SSR-side strings below mirror what python `_strip_comments_and_whitespace`
    // now emits for the GET dj-root (the byte-equivalence the Python tests in
    // python/djust/tests/test_ssr_render_normalization_1737.py assert).
    it('#1737: normalized SSR root morphs into the WS frame with zero element churn', () => {
        // SSR side = normalized server output: NO comment nodes, NO
        // inter-element whitespace text nodes, NO dj-id (client stamps it).
        const root = document.createElement('div');
        root.setAttribute('dj-view', 'app.views.UnitsView');
        root.innerHTML =
            '<div class="mobile-header">SSR header</div>' +
            '<div class="main-wrapper"><canvas class="chart"></canvas></div>';
        const header = root.querySelector('.mobile-header');
        const wrapper = root.querySelector('.main-wrapper');
        const canvas = root.querySelector('canvas');
        canvas._chartInstance = { id: 'chart-99' };

        // WS frame = same structure, dj-id stamped, updated text.
        const desired = document.createElement('div');
        desired.innerHTML =
            '<div class="mobile-header" dj-id="e1">WS header</div>' +
            '<div class="main-wrapper" dj-id="e2"><canvas class="chart" dj-id="e3"></canvas></div>';

        const beforeChildren = Array.from(root.children);
        morphChildren(root, desired);
        const afterChildren = Array.from(root.children);

        // Zero element churn: identical node objects, identical order.
        expect(afterChildren.length).toBe(2);
        expect(afterChildren[0]).toBe(beforeChildren[0]);
        expect(afterChildren[1]).toBe(beforeChildren[1]);
        expect(afterChildren[0]).toBe(header);
        expect(afterChildren[1]).toBe(wrapper);
        // The Chart.js canvas instance survives (no teardown = no flash).
        expect(root.querySelector('canvas')).toBe(canvas);
        expect(canvas._chartInstance.id).toBe('chart-99');
        // dj-id adopted onto the SAME nodes; updated text reaches the user.
        expect(header.getAttribute('dj-id')).toBe('e1');
        expect(header.textContent).toBe('WS header');
    });

    it('still REPLACES when existing children carry a different id (keyed reorder/replace not broken)', () => {
        // Guard against over-reach: when the existing positional node already
        // carries an id that differs from the desired id, this is a legitimate
        // keyed replace — it must NOT be reused in place.
        const root = document.createElement('div');
        root.setAttribute('dj-view', 'X');
        root.innerHTML = '<div class="card" id="old-card">OLD</div>';
        const oldCard = root.querySelector('#old-card');
        oldCard._marker = 'old';

        const desired = document.createElement('div');
        desired.innerHTML = '<div class="card" id="new-card">NEW</div>';

        morphChildren(root, desired);

        const after = root.querySelector('.card');
        expect(after.id).toBe('new-card');
        expect(after).not.toBe(oldCard);
        expect(after._marker).toBeUndefined();
        expect(root.querySelector('#old-card')).toBeNull();
    });
});
