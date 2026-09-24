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
 *
 * The seeded fixtures (keyed lists; {% if %}/{% for %} shapes) come from the
 * same generator and are checked the same way after every step.
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

for (const [title, file] of [
    ['keyed list reorders across separators', 'vdom_diff_keyed_list_fuzz_2999.json'],
    // Review H1/H2: {% if %} boundaries and {% for %} loops next to kept
    // spaces, nested ifs, a text value that empties and refills.
    ['{% if %}/{% for %} shapes next to kept spaces', 'vdom_diff_if_for_fuzz_2999.json'],
]) describe(`#2999 ${title}: real server patches`, () => {
    const fuzz = JSON.parse(fs.readFileSync(`./tests/js/fixtures/${file}`, 'utf-8'));

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

    if (file === 'vdom_diff_keyed_list_fuzz_2999.json') it('the fixture exercises forward moves (the case that used to land early)', () => {
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

describe('#2999 placements (_applyPatchBatch)', () => {
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

describe('#2999 review H1: an error message that clears and comes back', () => {
    const fuzz = JSON.parse(
        fs.readFileSync('./tests/js/fixtures/vdom_diff_if_for_fuzz_2999.json', 'utf-8')
    );
    const sub = fuzz.shapes.error_message;

    it('shows "Required" -> "" -> "Invalid email" in the client DOM', async () => {
        const { window, logs } = createHarnessDom(sub.initial_html);
        const { document } = window;
        const seen = [];
        for (const step of sub.steps.slice(0, 4)) {
            await window.djust.applyPatches(step.patches, document.querySelector('.ws-root'));
            seen.push(document.querySelector('.error').textContent);
        }
        // step 1 is random; steps 2-4 pin the review's sequence.
        expect(seen.slice(1)).toEqual(['Required', '', 'Invalid email']);
        const issues = logs.filter(([lvl]) => lvl === 'warn' || lvl === 'error');
        expect(issues).toEqual([]);
    });

    it('every step targets a node the server tree has (no SetText into nothing)', () => {
        // The server's own expected HTML carries the message at every step.
        for (const [i, step] of sub.steps.slice(1, 4).entries()) {
            const want = ['Required', '', 'Invalid email'][i];
            expect(step.expected_html).toContain(`<span class="error" dj-id=`);
            expect(step.expected_html.includes(`>${want}</span>`)).toBe(true);
        }
    });
});

describe('#2999 normalizer corpus: parsed by jsdom, counted by the client', () => {
    // Review M1. Each case carries the server VDOM's HTML (the truth), the
    // normalized raw render() output (initial GET) and the normalized VDOM
    // HTML (WS frame). Parsed by a real HTML5 parser and walked with the
    // client's own getSignificantChildren, both must equal the truth. This
    // is the check `diff_html` could not make: it re-parses with the server
    // parser, which silently drops a " " the normalizer wrongly kept.
    const corpus = JSON.parse(
        fs.readFileSync('./tests/js/fixtures/vdom_normalizer_corpus_2999.json', 'utf-8')
    );
    const { window } = createHarnessDom('<div></div>');
    const { document } = window;
    const tree = (html) => {
        const t = document.createElement('div');
        t.innerHTML = html;
        const root = t.querySelector('[dj-root]');
        const walk = (n) => (n.nodeType === 1
            ? [n.tagName.toLowerCase(), window.djust.getSignificantChildren(n).map(walk)]
            : n.nodeType === 8 ? ['#comment', n.textContent.trim()]
                // Node KIND, not content: a kept " " vs a text run. The
                // normalizer collapses whitespace inside text runs (and a
                // stripped comment can leave a leading space on one), which
                // moves no index.
                : ['#text', n.textContent === ' ' ? 'space' : 'text']);
        return window.djust.getSignificantChildren(root).map(walk);
    };

    it('has hand-picked and random cases', () => {
        expect(corpus.cases.length).toBeGreaterThan(300);
    });

    it('normalized VDOM HTML (the WS frame) keeps exactly the server nodes', () => {
        const bad = corpus.cases.filter((c) =>
            JSON.stringify(tree(c.norm_vdom)) !== JSON.stringify(tree(c.vdom_html)));
        expect(bad.map((c) => c.body)).toEqual([]);
    });

    it('normalized raw HTML (the initial GET) matches the server VDOM', () => {
        const bad = corpus.cases.filter((c) =>
            JSON.stringify(tree(c.norm_raw)) !== JSON.stringify(tree(c.vdom_html)));
        // Known gap, documented in the PR: foster parenting moves content out
        // of a <table> in the tree builder, which a string normalizer can't
        // see. The mount morph repairs it. None of the corpus hits it.
        expect(bad.map((c) => c.body)).toEqual([]);
    });

    it('a " " kept between two blocks WOULD be caught', () => {
        const truth = '<div dj-root=""><div>a</div><div>b</div></div>';
        expect(JSON.stringify(tree('<div dj-root=""><div>a</div> <div>b</div></div>')))
            .not.toBe(JSON.stringify(tree(truth)));
    });
});
