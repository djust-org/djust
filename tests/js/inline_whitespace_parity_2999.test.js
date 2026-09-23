/**
 * #2999 — the space between two inline siblings (`<b>A</b> <i>B</i>`).
 *
 * The server VDOM keeps that whitespace as a text node collapsed to exactly
 * " " (it used to drop it, so the page read "AB"). The client must count the
 * same node when it resolves patch paths/indices, or every patch after such a
 * space lands one sibling off.
 *
 * Part 1 applies REAL server patch batches (generated from a live view by
 * scripts/gen_vdom_diff_fixtures.py — tests/livefixtures/inline_whitespace_view.py)
 * in jsdom and checks, after every step, that
 *   - the significant-child tree equals a fresh render of the new state, and
 *   - the rendered TEXT equals it too (so a missing or misplaced space fails
 *     even where the tree shape would hide it), and
 *   - the patcher logged no warnings/errors.
 *
 * Part 2 pins the client predicate directly.
 */

import { describe, it, expect } from 'vitest';
import fs from 'fs';
import { createHarnessDom, normalizeSignificantTree } from './_significant_tree.js';

const fx = JSON.parse(
    fs.readFileSync('./tests/js/fixtures/vdom_diff_inline_whitespace_2999.json', 'utf-8')
);

/** The text a reader sees: textContent with whitespace runs collapsed. */
function visibleText(el) {
    return el.textContent.replace(/[ \t\n\r\f]+/g, ' ').trim();
}

describe('#2999 inline whitespace: real server patches in jsdom', () => {
    it('initial render keeps the spaces between inline siblings', () => {
        const { window } = createHarnessDom(fx.initial_html);
        const root = window.document.querySelector(fx.root_selector);
        expect(visibleText(root.querySelector('.lead'))).toBe('Lead. x = 1 done');
        expect(visibleText(root.querySelector('.md'))).toBe('Filters. {% load app_tags %}');
    });

    it('client DOM matches a fresh server render after every step', async () => {
        const { window, logs } = createHarnessDom(fx.initial_html);
        const { document } = window;
        const sel = fx.root_selector;

        for (let i = 0; i < fx.steps.length; i++) {
            const step = fx.steps[i];
            const rootEl = document.querySelector(sel);
            await window.djust.applyPatches(step.patches, rootEl);

            const tmp = document.createElement('div');
            tmp.innerHTML = step.expected_html;
            const expectedRoot = tmp.querySelector(sel);
            const actualRoot = document.querySelector(sel);

            expect(
                normalizeSignificantTree(actualRoot, window),
                `step ${i + 1} [${step.label}]: significant-child tree`
            ).toEqual(normalizeSignificantTree(expectedRoot, window));

            for (const block of ['.lead', '.tags', '.chips', '.cond', '.md']) {
                expect(
                    visibleText(actualRoot.querySelector(block)),
                    `step ${i + 1} [${step.label}]: text of ${block}`
                ).toBe(visibleText(expectedRoot.querySelector(block)));
                // Exact DOM text (not whitespace-collapsed): a doubled or
                // displaced " " would render the same but leave the client
                // one node away from the server.
                expect(
                    actualRoot.querySelector(block).textContent,
                    `step ${i + 1} [${step.label}]: raw text of ${block}`
                ).toBe(expectedRoot.querySelector(block).textContent);
            }
        }

        const issues = logs.filter(([lvl]) => lvl === 'warn' || lvl === 'error');
        expect(issues.map(([lvl, msg]) => `${lvl}: ${msg}`)).toEqual([]);
    });

    it('the first step patches text AFTER a kept space by path', () => {
        // Guard that the fixture really exercises path resolution across a
        // kept " ": the SetText patches carry no dj-id, only a path whose
        // index counts the space (strong=0, " "=1, code=2, " "=3, em=4).
        const paths = fx.steps[0].patches.map((p) => [p.type, p.d, p.path.join('/')]);
        expect(paths).toEqual([
            ['SetText', undefined, '0/0/0'],
            ['SetText', undefined, '0/2/0'],
            ['SetText', undefined, '0/4/0'],
        ]);
    });
});

describe('#2999 isSignificantChild', () => {
    const { window } = createHarnessDom('<div id="x"></div>');
    const { document } = window;
    const { isSignificantChild, getSignificantChildren } = window.djust;

    it('counts a text node that is exactly one space', () => {
        expect(isSignificantChild(document.createTextNode(' '))).toBe(true);
    });

    it('does not count indentation or other whitespace-only runs', () => {
        for (const t of ['\n', '\n    ', '  ', '\t', ' \n']) {
            expect(isSignificantChild(document.createTextNode(t)), JSON.stringify(t)).toBe(false);
        }
    });

    it('is a property of the node, not of its neighbours', () => {
        // The same " " between two BLOCK siblings still counts: the server
        // never emits one there, and deciding from neighbours would flip while
        // a patch batch moves them (removes, then moves, then inserts).
        const div = document.createElement('div');
        div.innerHTML = '<div>a</div> <div>b</div>';
        expect(getSignificantChildren(div).length).toBe(3);
        div.innerHTML = '<b>a</b> <i>b</i>';
        expect(getSignificantChildren(div).length).toBe(3);
        div.innerHTML = '<b>a</b>\n  <i>b</i>';
        expect(getSignificantChildren(div).length).toBe(2);
    });
});

describe('#2999 morph around whitespace', () => {
    it('keeps elements in place when the prerender has an extra " " between blocks', () => {
        const { window } = createHarnessDom(
            '<div dj-root><section id="s1"><div>a</div> <div>b</div></section></div>'
        );
        const { document } = window;
        const existing = document.querySelector('#s1');
        const [a, b] = existing.querySelectorAll('div');
        const desired = document.createElement('section');
        desired.innerHTML = '<div>a</div><div>b</div>';
        window.djust.morphChildren(existing, desired);
        const after = existing.querySelectorAll('div');
        expect(after[0]).toBe(a);
        expect(after[1]).toBe(b);
        expect(existing.childNodes.length).toBe(2);
    });

    it('inserts a desired " " between inline siblings', () => {
        const { window } = createHarnessDom('<div dj-root><p id="p"><b>A</b><i>B</i></p></div>');
        const { document } = window;
        const existing = document.querySelector('#p');
        const desired = document.createElement('p');
        desired.innerHTML = '<b>A</b> <i>B</i>';
        window.djust.morphChildren(existing, desired);
        expect(existing.textContent).toBe('A B');
        expect(window.djust.getSignificantChildren(existing).length).toBe(3);
    });
});

describe('#2999 keyed list reorders across separators: real server patches', () => {
    const fuzz = JSON.parse(
        fs.readFileSync('./tests/js/fixtures/vdom_diff_keyed_list_fuzz_2999.json', 'utf-8')
    );

    for (const [shape, sub] of Object.entries(fuzz.shapes)) {
        it(`${shape}: every step reproduces a fresh render`, async () => {
            const { window, logs } = createHarnessDom(sub.initial_html);
            const { document } = window;
            const sel = fuzz.root_selector;

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

    it('the fixture exercises forward moves (the case that used to land early)', () => {
        let forward = 0;
        for (const sub of Object.values(fuzz.shapes)) {
            for (const step of sub.steps) {
                for (const p of step.patches) {
                    if (p.type === 'MoveChild' && p.to > p.from) forward++;
                }
            }
        }
        expect(forward).toBeGreaterThan(20);
    });
});

describe('#2999 _applyChildPlacements', () => {
    it('plain keyed rotation [A..E] -> [C,D,E,A,B] (was "CADBE")', async () => {
        const { window } = createHarnessDom(
            '<div dj-root><ul dj-id="1">' +
            'ABCDE'.split('').map((k, i) => `<li dj-id="${i + 2}">${k}</li>`).join('') +
            '</ul></div>'
        );
        const root = window.document.querySelector('[dj-root]');
        // Exactly what the Rust differ emits for this rotation.
        await window.djust.applyPatches([
            { type: 'MoveChild', path: [0], d: '1', from: 0, to: 3, child_d: '2' },
            { type: 'MoveChild', path: [0], d: '1', from: 1, to: 4, child_d: '3' },
        ], root);
        expect(root.querySelector('ul').textContent).toBe('CDEAB');
    });

    it('plain keyed forward move [a,b,c] -> [b,c,a] (was "bac")', async () => {
        const { window } = createHarnessDom(
            '<div dj-root><ul dj-id="1"><li dj-id="2">a</li><li dj-id="3">b</li><li dj-id="4">c</li></ul></div>'
        );
        const root = window.document.querySelector('[dj-root]');
        await window.djust.applyPatches([
            { type: 'MoveChild', path: [0], d: '1', from: 0, to: 2, child_d: '2' },
        ], root);
        expect(root.querySelector('ul').textContent).toBe('bca');
    });
});
