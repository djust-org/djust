/**
 * The DOM shape: one spec, two implementations (#4108).
 *
 * `fixtures/dom_shape_vectors_4108.json` lists rendered roots with the shape
 * the server computes for each (`dom_shape`, python/djust/_render_digest.py,
 * held to the same file by python/djust/tests/test_render_digest_4108.py).
 * The built client must compute the same value from the DOM the browser
 * builds from that markup. Where the file says `differs`, the browser's HTML
 * parser builds another tree than the markup spells out: the two values must
 * then NOT be equal, so that such a page is answered with the whole page.
 */
import {describe, it, expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {loadPage} from './_http_fallback_replay.js';

const {vectors} = JSON.parse(readFileSync('./tests/js/fixtures/dom_shape_vectors_4108.json', 'utf8'));

describe('DOM shape vectors', () => {
    for (const vector of vectors) {
        it(`${vector.name}: the client's shape ${vector.client === 'same' ? 'is' : 'is not'} the server's`, async () => {
            const page = await loadPage(`<!doctype html><html><head></head><body>${vector.html}</body></html>`);
            try {
                expect(page.shape()).toMatch(/^[0-9a-f]{16}$/);
                if (vector.client === 'same') {
                    expect(page.shape()).toBe(vector.shape);
                } else {
                    expect(vector.reason).toBeTruthy();
                    expect(page.shape()).not.toBe(vector.shape);
                }
            } finally { page.close(); }
        });
    }

    it('covers both outcomes', () => {
        expect(vectors.filter(v => v.client === 'same').length).toBeGreaterThan(30);
        expect(vectors.filter(v => v.client === 'differs').length).toBeGreaterThan(3);
    });
});
