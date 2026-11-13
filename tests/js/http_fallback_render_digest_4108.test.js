/**
 * The page-POST fallback's render digest and patch answers, client side (#4108).
 *
 * The server answers a fallback POST with patches only after matching the
 * digest of the render the page shows. These tests drive the built client's
 * real `handleEvent` fallback path with one answer at a time.
 */
import {describe, it, expect} from 'vitest';
import {loadPage} from './_http_fallback_replay.js';

const PAGE = (digest) => '<!doctype html><html><head>'
    + (digest ? `<meta name="djust-render-digest" content="${digest}">` : '')
    + '</head><body>\n<div dj-root dj-view="app.View">\n'
    + '<p id="n">0</p>\n<input id="note" name="note" value="">\n'
    + '<details id="more"><summary>More</summary><p>Body</p></details>\n'
    + '<button id="go" dj-click="bump">Go</button>\n</div>\n</body></html>';

// The root as the server renders it (significant children: p, input, details, button).
const rootHtml = (n) => `<div dj-root=""><p id="n">${n}</p><input id="note" name="note" value="" />`
    + '<details id="more"><summary>More</summary><p>Body</p></details>'
    + '<button dj-click="bump" id="go">Go</button></div>';
const whole = (n, digest) => ({html: rootHtml(n), version: 1, ...(digest ? {render_digest: digest} : {})});
// No `d` hint and no `version`: what the server sends on this transport.
const patch = (n, digest) => ({
    patches: [{type: 'SetText', path: [0, 0], text: String(n)}],
    ...(digest ? {render_digest: digest} : {}),
});

async function bump(page, answer) {
    return page.send('bump', {}, answer, page.win.document.getElementById('go'));
}
const shown = (page) => page.win.document.getElementById('n').textContent;

describe('render digest', () => {
    it('echoes the digest the page GET carried, then the one each answer names', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            expect((await bump(page, patch(1, 'd1'))).digest).toBe('d0');
            expect((await bump(page, whole(2, 'd2'))).digest).toBe('d1');
            expect((await bump(page, patch(3, 'd3'))).digest).toBe('d2');
            expect(shown(page)).toBe('3');
        } finally { page.close(); }
    });

    it('sends no digest when the page carried none', async () => {
        const page = await loadPage(PAGE(null));
        try {
            expect((await bump(page, whole(1, 'd1'))).digest).toBe(null);
            // The answer named one: the next request echoes it.
            expect((await bump(page, patch(2, 'd2'))).digest).toBe('d1');
        } finally { page.close(); }
    });

    it('forgets the digest when an answer changes the DOM without naming one', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await bump(page, whole(1, null));
            expect((await bump(page, whole(2, 'd2'))).digest).toBe(null);
        } finally { page.close(); }
    });

    it('keeps the digest across an answer that changes nothing', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await bump(page, {patches: []});
            await bump(page, {type: 'noop'});
            expect((await bump(page, patch(1, 'd1'))).digest).toBe('d0');
        } finally { page.close(); }
    });

    it('forgets the digest when a socket mounts the page view', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            page.win.djust._mirrorPageParameterContracts(null, 'app.View');
            expect((await bump(page, whole(1, 'd1'))).digest).toBe(null);
        } finally { page.close(); }
    });

    it('withholds the digest after two whole-page answers in a row, then offers it again', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            const sent = [];
            for (let i = 1; i <= 12; i++) {
                sent.push((await bump(page, whole(i, `d${i}`))).digest);
            }
            // Two misses, eight events without a digest, then one more offer;
            // a third miss holds it back again.
            expect(sent).toEqual(['d0', 'd1', null, null, null, null, null, null, null, null, 'd10', null]);
            expect(shown(page)).toBe('12');
        } finally { page.close(); }
    });

    it('a patch answer between two whole pages starts the miss count again', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await bump(page, whole(1, 'd1'));
            await bump(page, patch(2, 'd2'));
            await bump(page, whole(3, 'd3'));
            expect((await bump(page, whole(4, 'd4'))).digest).toBe('d3');
        } finally { page.close(); }
    });
});

describe('patch answers on the fallback', () => {
    it('applies patch answers with no version, one after another, without reloading', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            for (const n of [1, 2, 3]) {
                await bump(page, patch(n, `d${n}`));
                expect(shown(page)).toBe(String(n));
            }
            expect(page.reloads()).toBe(0);
            expect(page.problems()).toEqual([]);
        } finally { page.close(); }
    });

    it('applies a patch answer after a whole-page answer re-based the cursor', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await bump(page, whole(1, 'd1'));
            await bump(page, patch(2, 'd2'));
            await bump(page, whole(3, 'd3'));
            await bump(page, patch(4, 'd4'));
            expect(shown(page)).toBe('4');
            expect(page.reloads()).toBe(0);
        } finally { page.close(); }
    });

    it('keeps what a whole-page answer resets: an unfocused value, an opened <details>, a client-set class', async () => {
        const touch = (page) => {
            const doc = page.win.document;
            doc.getElementById('note').value = 'typed, not sent';
            doc.getElementById('more').setAttribute('open', '');
            doc.getElementById('go').classList.add('client-set');
        };
        const state = (page) => {
            const doc = page.win.document;
            return {
                note: doc.getElementById('note').value,
                open: doc.getElementById('more').hasAttribute('open'),
                marked: doc.getElementById('go').classList.contains('client-set'),
            };
        };
        const patched = await loadPage(PAGE('d0'));
        const morphed = await loadPage(PAGE('d0'));
        try {
            touch(patched);
            await bump(patched, patch(1, 'd1'));
            expect(shown(patched)).toBe('1');
            expect(state(patched)).toEqual({note: 'typed, not sent', open: true, marked: true});

            // The same event answered with the whole page: the known cost.
            touch(morphed);
            await bump(morphed, whole(1, 'd1'));
            expect(shown(morphed)).toBe('1');
            expect(state(morphed)).toEqual({note: '', open: false, marked: false});
        } finally { patched.close(); morphed.close(); }
    });
});

describe('a patch answer that does not fit the DOM', () => {
    // A path that resolves to nothing: the page's structure is not the server's.
    const unfit = {patches: [{type: 'SetText', path: [9, 9, 9], text: 'x'}], render_digest: 'bad'};
    const freshPage = '<!doctype html><html><head><meta name="djust-render-digest" content="fresh">'
        + '</head><body><div dj-root dj-view="app.View"><p id="n">7</p>'
        + '<input id="note" name="note" value=""><details id="more"><summary>More</summary><p>Body</p></details>'
        + '<button id="go" dj-click="bump">Go</button></div></body></html>';

    it('fetches the page and morphs its root in, instead of reloading', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            const doc = page.win.document;
            doc.getElementById('go').classList.add('client-set');
            const button = doc.getElementById('go');
            await page.send('bump', {}, (request) => request.method === 'POST' ? unfit : freshPage, button);
            expect(page.requests.map(r => r.method)).toEqual(['POST', 'GET']);
            expect(shown(page)).toBe('7');
            // Morphed, not replaced: the button is the same node.
            expect(doc.getElementById('go')).toBe(button);
            expect(page.reloads()).toBe(0);
            // The fetched page's digest is the one echoed next.
            expect((await bump(page, patch(8, 'd8'))).digest).toBe('fresh');
            expect(shown(page)).toBe('8');
        } finally { page.close(); }
    });

    it('reloads when the page cannot be fetched', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await page.send('bump', {}, (request) => {
                if (request.method === 'POST') return unfit;
                throw new Error('offline');
            }, page.win.document.getElementById('go'));
            expect(page.reloads()).toBe(1);
        } finally { page.close(); }
    });
});
