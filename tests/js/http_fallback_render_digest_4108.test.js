/**
 * The page-POST fallback's render token and patch answers, client side (#4108).
 *
 * The server answers a fallback POST with patches only after matching the
 * token "<digest>.<shape>": the digest of the render the page shows, and the
 * shape of the client's DOM. These tests drive the built client's real
 * `handleEvent` fallback path with one answer at a time.
 */
import {describe, it, expect, beforeAll} from 'vitest';
import {readFileSync} from 'node:fs';
import {loadPage, recordedServer, finishExits, domDifference} from './_http_fallback_replay.js';

const fixture = JSON.parse(
    readFileSync('./tests/js/fixtures/http_fallback_replay_4108.json', 'utf8'));

const BODY = '<body>\n<div dj-root dj-view="app.View">\n'
    + '<p id="n">0</p>\n<input id="note" name="note" value="">\n'
    + '<details id="more"><summary>More</summary><p>Body</p></details>\n'
    + '<button id="go" dj-click="bump">Go</button>\n</div>\n</body></html>';
// The shape of that page, as the client computes it (the server's `dom_shape`
// of the same tree is the same value: http_fallback_replay_4108.test.js).
let SHAPE;
beforeAll(async () => {
    const page = await loadPage('<!doctype html><html><head></head>' + BODY);
    SHAPE = page.shape();
    page.close();
    expect(SHAPE).toMatch(/^[0-9a-f]{16}$/);
});
const token = (digest) => `${digest}.${SHAPE}`;
const PAGE = (digest) => '<!doctype html><html><head>'
    + (digest ? `<meta name="djust-render-digest" content="${token(digest)}">` : '')
    + '</head>' + BODY;

// The root as the server renders it (significant children: p, input, details, button).
const rootHtml = (n) => `<div dj-root=""><p id="n">${n}</p><input id="note" name="note" value="" />`
    + '<details id="more"><summary>More</summary><p>Body</p></details>'
    + '<button dj-click="bump" id="go">Go</button></div>';
const whole = (n, digest) => ({html: rootHtml(n), version: 1, ...(digest ? {render_digest: token(digest)} : {})});
// No `d` hint and no `version`: what the server sends on this transport.
const patch = (n, digest) => ({
    patches: [{type: 'SetText', path: [0, 0], text: String(n)}],
    ...(digest ? {render_digest: token(digest)} : {}),
});

async function bump(page, answer) {
    return page.send('bump', {}, answer, page.win.document.getElementById('go'));
}
const shown = (page) => page.win.document.getElementById('n').textContent;

describe('render token', () => {
    it('sends the digest the page GET carried, then the one each answer names, with the DOM\'s shape', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            expect((await bump(page, patch(1, 'd1'))).token).toBe(token('d0'));
            expect((await bump(page, whole(2, 'd2'))).token).toBe(token('d1'));
            expect((await bump(page, patch(3, 'd3'))).token).toBe(token('d2'));
            expect(shown(page)).toBe('3');
        } finally { page.close(); }
    });

    it('sends no token when the page carried none', async () => {
        const page = await loadPage(PAGE(null));
        try {
            expect((await bump(page, whole(1, 'd1'))).token).toBe(null);
            // The answer named one: the next request sends it.
            expect((await bump(page, patch(2, 'd2'))).token).toBe(token('d1'));
        } finally { page.close(); }
    });

    it('ignores a token that is not two halves', async () => {
        for (const content of ['d0', 'd0.', '.abc', 'a.b.c']) {
            const page = await loadPage('<!doctype html><html><head>'
                + `<meta name="djust-render-digest" content="${content}"></head>` + BODY);
            try {
                expect((await bump(page, whole(1, null))).token, content).toBe(null);
            } finally { page.close(); }
        }
    });

    it('forgets the token when an answer changes the DOM without naming one', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await bump(page, whole(1, null));
            expect((await bump(page, whole(2, 'd2'))).token).toBe(null);
        } finally { page.close(); }
    });

    it('keeps the token across an answer that changes nothing', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await bump(page, {patches: []});
            await bump(page, {type: 'noop'});
            expect((await bump(page, patch(1, 'd1'))).token).toBe(token('d0'));
        } finally { page.close(); }
    });

    it('forgets the token when a socket mounts the page view', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            page.win.djust._mirrorPageParameterContracts(null, 'app.View');
            expect((await bump(page, whole(1, 'd1'))).token).toBe(null);
        } finally { page.close(); }
    });

    it('withholds the token after two whole-page answers in a row, then offers it again', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            const sent = [];
            for (let i = 1; i <= 12; i++) {
                sent.push((await bump(page, whole(i, `d${i}`))).digest);
            }
            // Two misses, eight events without a token, then one more offer;
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

describe('the shape of the DOM', () => {
    const shapeOf = async (inner) => {
        const page = await loadPage(`<!doctype html><html><head></head><body><div dj-root>${inner}</div></body></html>`);
        try { return page.shape(); } finally { page.close(); }
    };

    it('ignores text, classes, values and indentation, and sees every structural change', async () => {
        const base = await shapeOf('<h1>Rows</h1><ul><li>a</li><li>b</li></ul>');
        expect(await shapeOf('\n  <h1 class="x">Other</h1>\n<!-- note -->  <ul>\n<li>c</li>\n<li>d</li></ul>\n')).toBe(base);
        const different = [
            '<ul><li>a</li><li>b</li></ul>',
            '<h1>Rows</h1><p>new</p><ul><li>a</li><li>b</li></ul>',
            '<h1>Rows</h1><ul><li>a</li></ul>',
            '<h2>Rows</h2><ul><li>a</li><li>b</li></ul>',
            '<h1></h1><ul><li>a</li><li>b</li></ul>',
            '<h1>Rows</h1><ul><li>a</li></ul><li>b</li>',
            '<h1 id="t">Rows</h1><ul><li>a</li><li>b</li></ul>',
            '<h1>Rows</h1><ul><li data-key="1">a</li><li>b</li></ul>',
            '<h1>Rows</h1><!--dj-if id="if-1"--><!--/dj-if--><ul><li>a</li><li>b</li></ul>',
        ];
        const shapes = [];
        for (const inner of different) shapes.push(await shapeOf(inner));
        expect(new Set([base, ...shapes]).size).toBe(different.length + 1);
    });

    it('is the value the server computes for the same tree', async () => {
        // Each scenario's GET document against the shape half of the token the
        // server put in it (python: _render_digest.dom_shape).
        for (const [name, scenario] of Object.entries(fixture.scenarios)) {
            const page = await loadPage(scenario.get, scenario.url, {hooks: {Widget: {}}});
            try {
                const meta = page.win.document.querySelector('meta[name="djust-render-digest"]');
                expect(page.shape(), name).toBe(meta.getAttribute('content').split('.')[1]);
            } finally { page.close(); }
        }
    });

    it('sends no token while the DOM is not laid out as the last render, and sends it again when it is', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            const doc = page.win.document;
            const extra = doc.createElement('aside');
            doc.querySelector('[dj-root]').insertBefore(extra, doc.getElementById('n'));
            expect((await bump(page, {patches: []})).token).toBe(null);
            extra.remove();
            expect((await bump(page, patch(1, 'd1'))).token).toBe(token('d0'));
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

// A patch answer is addressed by path. These are the ways a DOM stops being
// the tree the server diffed against; in each, a patch must not be applied to
// whatever node the path now reaches. The server here is the recorded one:
// patches for the token it expects, the whole page otherwise.
describe('a DOM that is not the tree the server rendered', () => {
    // The rows that are not on their way out.
    const rows = (win) => [...win.document.querySelectorAll('li')]
        .filter(li => !win.djust.djRemove._pendingRemovals.has(li)).map(li => li.textContent.trim());

    it('dj-remove: a row still playing its exit does not shift the next patch onto its neighbour', async () => {
        const scenario = fixture.scenarios.leaving;
        const page = await loadPage(scenario.get, scenario.url);
        const server = recordedServer(scenario);
        try {
            const {win} = page;
            const root = win.document.querySelector('[dj-root]');
            // Event 1 removes "Item 1": a patch answer; the row stays, fading.
            const first = await page.send('remove', {row: 1}, server, root);
            expect(first.token).toBe(scenario.sent_digests[0]);
            expect(win.document.querySelectorAll('li').length).toBe(3);
            expect(rows(win)).toEqual(['Item 2', 'Item 3']);
            // Event 2, while it fades, renames "Item 3". The patch for it is
            // SetText at the path of the second of TWO rows; in this DOM that
            // path is "Item 2". No token is sent, so no such patch comes back.
            const second = await page.send('rename', {row: 3}, server, root);
            expect(second.token).toBe(null);
            expect(rows(win)).toEqual(['Item 2', 'Item 3*']);
            await page.send('rename', {row: 2}, server, root);
            finishExits(win);
            expect(rows(win)).toEqual(['Item 2*', 'Item 3*']);
            expect(domDifference(win, scenario.expected_dom)).toBe(null);
            expect(page.reloads()).toBe(0);
        } finally { page.close(); }
    });

    it('a hook inserts an element: the class meant for the button does not land on the list', async () => {
        const scenario = fixture.scenarios.hooked;
        const hooks = {Widget: {mounted() {
            const extra = this.el.ownerDocument.createElement('div');
            extra.className = 'from-a-library';
            this.el.parentNode.insertBefore(extra, this.el);
        }}};
        const page = await loadPage(scenario.get, scenario.url, {hooks});
        const server = recordedServer(scenario);
        try {
            const {win} = page;
            expect(win.document.querySelector('.from-a-library')).not.toBe(null);
            const button = () => win.document.querySelector('button');
            const request = await page.send('toggle', {}, server, button());
            expect(request.token).toBe(null);
            expect(button().className).toBe('active');
            expect(win.document.querySelector('ul').className).toBe('');
            expect(page.reloads()).toBe(0);
        } finally { page.close(); }
    });

    it('a script removes an element: the patch does not land one node further on', async () => {
        const scenario = fixture.scenarios.hooked;
        const page = await loadPage(scenario.get, scenario.url, {hooks: {Widget: {}}});
        const server = recordedServer(scenario);
        try {
            const {win} = page;
            win.document.querySelector('.host').remove();
            const request = await page.send('toggle', {}, server, win.document.querySelector('button'));
            expect(request.token).toBe(null);
            // The whole page: the button is active, and the removed host is back.
            expect(win.document.querySelector('button').className).toBe('active');
            expect(win.document.querySelector('.host')).not.toBe(null);
            expect(domDifference(win, scenario.answers_whole[0].html)).toBe(null);
        } finally { page.close(); }
    });

    it('the DOM changes shape while the request is out: no patch is applied, the page is resynced', async () => {
        const scenario = fixture.scenarios.hooked;
        const page = await loadPage(scenario.get, scenario.url, {hooks: {Widget: {}}});
        const recorded = recordedServer(scenario);
        try {
            const {win} = page;
            const server = (request) => {
                if (!request.resync) {
                    // After the request left with a good token, before its answer.
                    const extra = win.document.createElement('div');
                    extra.className = 'late';
                    win.document.querySelector('[dj-root]').insertBefore(extra, win.document.querySelector('ul'));
                }
                return recorded(request);
            };
            await page.send('toggle', {}, server, win.document.querySelector('button'));
            expect(page.requests.map(r => [r.method, r.resync, r.token !== null])).toEqual([
                ['POST', false, true],   // the event: answered with patches
                ['POST', true, false],   // the resync: no event, no token
            ]);
            // Not one patch landed on the shifted tree; the page is the server's.
            expect(win.document.querySelector('ul').className).toBe('');
            expect(win.document.querySelector('button').className).toBe('active');
            expect(domDifference(win, scenario.answers_whole[0].html)).toBe(null);
            expect(page.reloads()).toBe(0);
        } finally { page.close(); }
    });
});

describe('a patch answer that cannot be applied', () => {
    // A path that resolves to nothing although the shape matched.
    const unfit = () => ({patches: [{type: 'SetText', path: [9, 9, 9], text: 'x'}], render_digest: token('bad')});

    it('asks for the page with a POST that carries no event, and morphs it in: the event\'s state is kept', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            const doc = page.win.document;
            const button = doc.getElementById('go');
            // The server ran the event (its state says 7); the resync request
            // restores that state and renders it. No page GET, which would
            // mount the view again and save that.
            await page.send('bump', {}, (request) => request.resync ? whole(7, 'fresh') : unfit(), button);
            expect(page.requests.map(r => [r.method, r.resync, r.event])).toEqual([
                ['POST', false, 'bump'],
                ['POST', true, null],
            ]);
            expect(page.requests[1].body).toBe('{}');
            expect(shown(page)).toBe('7');
            // Morphed, not replaced: the button is the same node.
            expect(doc.getElementById('go')).toBe(button);
            expect(page.reloads()).toBe(0);
            // The resync answer's token is the one sent next.
            expect((await bump(page, patch(8, 'd8'))).token).toBe(token('fresh'));
            expect(shown(page)).toBe('8');
        } finally { page.close(); }
    });

    it('reloads when the resync request fails', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            await page.send('bump', {}, (request) => {
                if (!request.resync) return unfit();
                throw new Error('offline');
            }, page.win.document.getElementById('go'));
            expect(page.reloads()).toBe(1);
        } finally { page.close(); }
    });
});
