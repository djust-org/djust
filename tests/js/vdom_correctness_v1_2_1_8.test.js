/**
 * v1.2.1-8 — VDOM diff correctness, on REAL server patch batches.
 *
 * Fixtures come from scripts/gen_vdom_diff_fixtures.py
 * (tests/livefixtures/vdom_correctness_views.py). After every step the client
 * DOM must equal a fresh server render of the new state — text included — and
 * the patcher must log no warnings or errors.
 *
 * - #2997: a 69-item keyed list filtered to 6 and restored, re-sorted and
 *   restored (djust.org /themes/).
 * - #2898: text that HTML-escapes, through the parse-skipping text fast paths;
 *   `SetText` carries decoded text, since the client sets `textContent`.
 * - #3012: text patches whose path runs through whitespace-only text inside
 *   <pre>/<code>/<textarea>, which the server counts.
 * - #3639: stream-only operations, and `temporary_assigns` rows under
 *   `dj-update="append"` / `"prepend"` (tests/livefixtures/stream_append_views.py).
 */

import { describe, it, expect } from 'vitest';
import fs from 'fs';
import { createHarnessDom, normalizeSignificantTree } from './_significant_tree.js';

function load(file) {
    return JSON.parse(fs.readFileSync(`./tests/js/fixtures/${file}`, 'utf-8'));
}

const FIXTURES = [
    ['#2997 keyed filter/restore', 'vdom_diff_keyed_filter_restore_2997.json'],
    ['#2898 escaped text through the fast paths', 'vdom_diff_escaped_text_2898.json'],
    ['#3012 whitespace inside pre/code/textarea', 'vdom_diff_preserve_whitespace_3012.json'],
    ['#3639 stream operations and dj-update append/prepend', 'vdom_diff_stream_append_3639.json'],
];

for (const [title, file] of FIXTURES) {
    describe(`${title}: real server patches`, () => {
        const fx = load(file);
        for (const [shape, sub] of Object.entries(fx.shapes)) {
            it(`${shape}: every step reproduces a fresh render`, async () => {
                const { window, logs } = createHarnessDom(sub.initial_html);
                const { document } = window;
                const sel = fx.root_selector;

                for (let i = 0; i < sub.steps.length; i++) {
                    const step = sub.steps[i];
                    await window.djust.applyPatches(step.patches, document.querySelector(sel));

                    const tmp = document.createElement('div');
                    tmp.innerHTML = step.expected_html;
                    const expectedRoot = tmp.querySelector(sel);
                    const actualRoot = document.querySelector(sel);

                    expect(
                        actualRoot.textContent,
                        `${shape} step ${i + 1} [${step.label}]: text`
                    ).toBe(expectedRoot.textContent);
                    expect(
                        normalizeSignificantTree(actualRoot, window),
                        `${shape} step ${i + 1} [${step.label}]: tree`
                    ).toEqual(normalizeSignificantTree(expectedRoot, window));
                }

                const issues = logs.filter(([lvl]) => lvl === 'warn' || lvl === 'error');
                expect(issues.map(([lvl, msg]) => `${lvl}: ${msg}`)).toEqual([]);
            });
        }
    });
}

describe('#2997 the fixture exercises the reported flow', () => {
    it('restores go through moves and inserts around the survivors', () => {
        const fx = load('vdom_diff_keyed_filter_restore_2997.json');
        for (const sub of Object.values(fx.shapes)) {
            const restore = sub.steps[1];
            expect(restore.label).toBe('subset -> all');
            const types = new Set(restore.patches.map((p) => p.type));
            expect(types.has('InsertChild')).toBe(true);
        }
        const moves = Object.values(fx.shapes)
            .flatMap((s) => s.steps)
            .flatMap((s) => s.patches)
            .filter((p) => p.type === 'MoveChild');
        expect(moves.length).toBeGreaterThan(0);
    });
});

describe('#2898 SetText carries decoded text', () => {
    it('no fast-path SetText carries an HTML entity for escaped values', () => {
        const fx = load('vdom_diff_escaped_text_2898.json');
        const texts = fx.shapes.escaped.steps
            .flatMap((s) => s.patches)
            .filter((p) => p.type === 'SetText')
            .map((p) => p.text);
        // The fixture really changes text that escapes.
        expect(texts.some((t) => t.includes('&'))).toBe(true);
        expect(texts.some((t) => t.includes('<'))).toBe(true);
        // `a &amp; b` is the one value whose DECODED text holds an entity-like
        // run (it was typed that way); nothing else may.
        for (const t of texts) {
            expect(t.replace('a &amp; b', ''), JSON.stringify(t)).not.toMatch(/&(amp|lt|gt|quot|#39|#x27);/);
        }
    });
});

describe('#3012 path walking counts whitespace inside pre/code/textarea', () => {
    it('getNodeByPath resolves a text node after a whitespace-only run in <pre>', () => {
        const { window } = createHarnessDom(
            '<div dj-root dj-id="0"><pre><code>a</code>\n<b>b</b></pre></div>'
        );
        const node = window.djust._getNodeByPath([0, 2, 0]);
        expect(node && node.textContent).toBe('b');
    });

    it('matches the server rule: the DIRECT parent decides', () => {
        // Server (parser.rs build_children): text is kept verbatim only when
        // its parent is pre/code/textarea/script/style. A whitespace-only run
        // inside a <span> inside <pre> is dropped like anywhere else.
        const { window } = createHarnessDom(
            '<div dj-root dj-id="0"><pre><span>\n</span>\n<span>  <i>x</i></span></pre></div>'
        );
        const { document } = window;
        const pre = document.querySelector('pre');
        const [s1, s2] = pre.querySelectorAll('span');
        expect(window.djust.getSignificantChildren(pre).length).toBe(3);
        expect(window.djust.getSignificantChildren(s1).length).toBe(0);
        expect(window.djust.getSignificantChildren(s2).length).toBe(1);
        const i = window.djust._getNodeByPath([0, 2, 0, 0]);
        expect(i && i.textContent).toBe('x');
    });
});

describe('#3639 the fixture exercises the reported flow', () => {
    const fx = load('vdom_diff_stream_append_3639.json');

    it('an appended row arrives as an InsertChild and no stored row is removed', () => {
        for (const step of fx.shapes.append.steps) {
            const types = step.patches.map((p) => p.type);
            expect(types, step.label).not.toContain('RemoveChild');
            if (step.label !== 'unrelated event') {
                expect(types, step.label).toContain('InsertChild');
            }
        }
    });

    it('an unrelated event leaves the accumulated rows alone', () => {
        for (const shape of ['append', 'prepend']) {
            const step = fx.shapes[shape].steps.find((s) => s.label === 'unrelated event');
            expect(step.patches.map((p) => p.type), shape).toEqual(['SetText']);
        }
    });

    it('the stream container ends with the rows the stream operations left', async () => {
        const sub = fx.shapes.stream;
        const { window } = createHarnessDom(sub.initial_html);
        const { document } = window;
        for (const step of sub.steps) {
            await window.djust.applyPatches(step.patches, document.querySelector(fx.root_selector));
        }
        const ids = Array.from(document.querySelectorAll('#feed > li')).map((li) => li.id);
        expect(ids).toEqual(['rows-200']);
    });

    it('the append container ends with every stored row plus every assigned one', async () => {
        const sub = fx.shapes.append;
        const { window } = createHarnessDom(sub.initial_html);
        const { document } = window;
        for (const step of sub.steps) {
            await window.djust.applyPatches(step.patches, document.querySelector(fx.root_selector));
        }
        const ids = Array.from(document.querySelectorAll('#list > li')).map((li) => li.id);
        expect(ids).toEqual([
            'rows-0', 'rows-1', 'rows-2', // mounted
            'rows-101', 'rows-200', 'rows-301', 'rows-302', 'rows-103',
        ]);
    });
});
