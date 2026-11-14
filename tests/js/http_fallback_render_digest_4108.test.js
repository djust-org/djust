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

const BODY = (n) => '<body>\n<div dj-root dj-view="app.View">\n'
    + `<p id="n">${n}</p>\n<input id="note" name="note" value="">\n`
    + '<details id="more"><summary>More</summary><p>Body</p></details>\n'
    + '<button id="go" dj-click="bump">Go</button>\n</div>\n</body></html>';
// The shape of that page for each text the tests put in it, as the client
// computes it (the server's `dom_shape` of the same tree is the same value:
// dom_shape_vectors_4108.test.js). The shape holds the text, so a token names
// the page WITH a given number in it.
const SHAPES = {};
beforeAll(async () => {
    for (const n of [...Array(13).keys(), 'cached']) {
        const page = await loadPage('<!doctype html><html><head></head>' + BODY(n));
        SHAPES[n] = page.shape();
        page.close();
        expect(SHAPES[n]).toMatch(/^[0-9a-f]{16}$/);
    }
    expect(new Set(Object.values(SHAPES)).size).toBe(14);
});
// The token of the page showing `n` (by default the number in the digest's name).
const token = (digest, n = Number(digest.slice(1))) => `${digest}.${SHAPES[n]}`;
const PAGE = (digest) => '<!doctype html><html><head>'
    + (digest ? `<meta name="djust-render-digest" content="${token(digest, 0)}">` : '')
    + '</head>' + BODY(0);

// The root as the server renders it (significant children: p, input, details, button).
const rootHtml = (n) => `<div dj-root=""><p id="n">${n}</p><input id="note" name="note" value="" />`
    + '<details id="more"><summary>More</summary><p>Body</p></details>'
    + '<button dj-click="bump" id="go">Go</button></div>';
const whole = (n, digest) => ({html: rootHtml(n), version: 1, ...(digest ? {render_digest: token(digest, n)} : {})});
// No `d` hint and no `version`: what the server sends on this transport.
const patch = (n, digest) => ({
    patches: [{type: 'SetText', path: [0, 0], text: String(n)}],
    ...(digest ? {render_digest: token(digest, n)} : {}),
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
                + `<meta name="djust-render-digest" content="${content}"></head>` + BODY(0));
            try {
                expect((await bump(page, whole(1, null))).token, content).toBe(null);
            } finally { page.close(); }
        }
    });

    it('sends no token after an answer that changed the DOM and named none: the DOM is no longer that render', async () => {
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

    it('ignores what client code writes on the server\'s render, and sees everything else', async () => {
        const base = await shapeOf('<h1>Rows</h1><ul><li>a</li><li data-x="2">b</li></ul>');
        // Presentation, widget state, generated ids, indentation, plain comments.
        expect(await shapeOf('\n  <h1 class="x" style="color:red" aria-live="polite" dj-id="9">Rows</h1>\n'
            + '<!-- note -->  <ul hidden data-djust-locked="1">\n<li>a</li>\n<li data-x="2" tabindex="0">b</li></ul>\n')).toBe(base);
        const different = [
            '<ul><li>a</li><li data-x="2">b</li></ul>',
            '<h1>Rows</h1><p>new</p><ul><li>a</li><li data-x="2">b</li></ul>',
            '<h1>Rows</h1><ul><li>a</li></ul>',
            '<h2>Rows</h2><ul><li>a</li><li data-x="2">b</li></ul>',
            '<h1></h1><ul><li>a</li><li data-x="2">b</li></ul>',
            '<h1>Rows</h1><ul><li>a</li></ul><li data-x="2">b</li>',
            '<h1 id="t">Rows</h1><ul><li>a</li><li data-x="2">b</li></ul>',
            '<h1>Rows</h1><ul><li data-key="1">a</li><li data-x="2">b</li></ul>',
            '<h1>Rows</h1><!--dj-if id="if-1"--><!--/dj-if--><ul><li>a</li><li data-x="2">b</li></ul>',
            // The same tree with a text, an attribute value, or the rows' order changed.
            '<h1>Other</h1><ul><li>a</li><li data-x="2">b</li></ul>',
            '<h1>Rows</h1><ul><li>a</li><li data-x="3">b</li></ul>',
            '<h1>Rows</h1><ul><li data-x="2">b</li><li>a</li></ul>',
        ];
        const shapes = [];
        for (const inner of different) shapes.push(await shapeOf(inner));
        expect(new Set([base, ...shapes]).size).toBe(different.length + 1);
    });

    it('reads a dj-disable-with button by the text it will show again', async () => {
        const plain = await shapeOf('<button dj-click="save">Save</button>');
        // While its request is out the button shows other text; djust keeps
        // the original in data-djust-original-text and puts it back.
        expect(await shapeOf('<button dj-click="save" data-djust-original-text="Save" disabled>Saving\u2026</button>'))
            .toBe(plain);
        expect(await shapeOf('<button dj-click="save" data-djust-original-text="Other" disabled>Saving\u2026</button>'))
            .not.toBe(plain);
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
    const unfit = () => ({patches: [{type: 'SetText', path: [9, 9, 9], text: 'x'}], render_digest: token('bad', 0)});

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
            expect((await bump(page, patch(8, 'd8'))).token).toBe(token('fresh', 7));
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

// The shape says the tree is laid out as the render. These change WHICH
// element sits where without changing the layout; a patch addressed by
// position must not be applied to any of them. Over a socket each patch finds
// its node by a generated id that travels with the element; this transport
// has no such id, so the client numbers the elements each time the DOM is the
// server's render and sends no token once one is new or out of order.
describe('elements moved or replaced without changing the layout', () => {
    const LISTS = '<!doctype html><html><head></head><body><div dj-root dj-view="app.View">'
        + '<ul id="l"><li data-x="A">A</li><li data-x="B">B</li></ul>'
        + '<ul id="m"><li data-x="C">C</li><li data-x="D">D</li></ul>'
        + '<p class="same">same</p><p class="same">same</p>'
        + '<button id="go" dj-click="bump">Go</button></div></body></html>';
    const rootOf = (l, m) => '<div dj-root="">'
        + `<ul id="l">${l.map(([x, text]) => `<li data-x="${x}">${text}</li>`).join('')}</ul>`
        + `<ul id="m">${m.map(([x, text]) => `<li data-x="${x}">${text}</li>`).join('')}</ul>`
        + '<p class="same">same</p><p class="same">same</p>'
        + '<button dj-click="bump" id="go">Go</button></div>';
    const M = [['C', 'C'], ['D', 'D']];

    // A page whose token is the one the server would have put in it.
    async function open() {
        const page = await loadPage(LISTS);
        const doc = page.win.document;
        const meta = doc.createElement('meta');
        meta.setAttribute('name', 'djust-render-digest');
        meta.setAttribute('content', `d0.${page.shape()}`);
        doc.head.appendChild(meta);
        page.win.djust._installPageParameterContracts();
        return page;
    }
    const rows = (page, id) => [...page.win.document.querySelectorAll(`#${id} li`)]
        .map(li => [li.getAttribute('data-x'), li.textContent]);
    // The server: the patches for the render it believes is shown when the
    // request brings a token, the whole page of its state otherwise.
    const serverWith = (patches, html) => (request) => request.token
        ? {patches, render_digest: 'd1.0000000000000000'}
        : {html, version: 1};
    const send = (page, server) => page.send('bump', {}, server, page.win.document.getElementById('go'));

    it('control: with nothing moved the token is sent and the patch lands on its row', async () => {
        const page = await open();
        try {
            const request = await send(page, serverWith(
                [{type: 'SetText', path: [0, 0, 0], text: 'A-updated'}], rootOf([['A', 'A-updated'], ['B', 'B']], M)));
            expect(request.token).not.toBe(null);
            expect(rows(page, 'l')).toEqual([['A', 'A-updated'], ['B', 'B']]);
        } finally { page.close(); }
    });

    it('a script reorders two rows: the text meant for row A does not land on row B', async () => {
        const page = await open();
        try {
            const list = page.win.document.getElementById('l');
            list.insertBefore(list.children[1], list.children[0]); // B above A
            const request = await send(page, serverWith(
                [{type: 'SetText', path: [0, 0, 0], text: 'A-updated'}], rootOf([['A', 'A-updated'], ['B', 'B']], M)));
            expect(request.token).toBe(null);
            // Each row's id still goes with its own text.
            expect(rows(page, 'l')).toEqual([['A', 'A-updated'], ['B', 'B']]);
        } finally { page.close(); }
    });

    it('a script reorders two rows: removing row A does not remove row B', async () => {
        const page = await open();
        try {
            const list = page.win.document.getElementById('l');
            list.insertBefore(list.children[1], list.children[0]);
            // The server deleted A: it believes B is what is left.
            const request = await send(page, serverWith(
                [{type: 'RemoveChild', path: [0], index: 0}], rootOf([['B', 'B']], M)));
            expect(request.token).toBe(null);
            expect(rows(page, 'l')).toEqual([['B', 'B']]);
        } finally { page.close(); }
    });

    it('two siblings equal in every attribute and text, swapped: the same page, so the token is sent', async () => {
        const page = await open();
        try {
            const [first, second] = page.win.document.querySelectorAll('p.same');
            first.parentNode.insertBefore(second, first);
            const request = await send(page, serverWith(
                [{type: 'SetText', path: [0, 0, 0], text: 'A-updated'}], rootOf([['A', 'A-updated'], ['B', 'B']], M)));
            expect(request.token).not.toBe(null);
            expect(rows(page, 'l')).toEqual([['A', 'A-updated'], ['B', 'B']]);
        } finally { page.close(); }
    });

    it('a sibling replaced by a copy with other content', async () => {
        const page = await open();
        try {
            const row = page.win.document.querySelector('#l li');
            const copy = row.cloneNode(true);
            copy.setAttribute('data-x', 'Z');
            row.replaceWith(copy);
            expect((await send(page, serverWith([], rootOf([['A', 'A'], ['B', 'B']], M)))).token).toBe(null);
        } finally { page.close(); }
    });

    it('rows exchanged between two parents of one layout', async () => {
        const page = await open();
        try {
            const doc = page.win.document;
            const b = doc.querySelector('#l li:nth-child(2)');
            const c = doc.querySelector('#m li:nth-child(1)');
            doc.getElementById('l').appendChild(c);
            doc.getElementById('m').insertBefore(b, doc.getElementById('m').firstChild);
            const request = await send(page, serverWith(
                [{type: 'SetText', path: [1, 0, 0], text: 'C-updated'}],
                rootOf([['A', 'A'], ['B', 'B']], [['C', 'C-updated'], ['D', 'D']])));
            expect(request.token).toBe(null);
            expect(rows(page, 'm')).toEqual([['C', 'C-updated'], ['D', 'D']]);
        } finally { page.close(); }
    });

    it('two text nodes exchanged around an inline element', async () => {
        const page = await loadPage('<!doctype html><html><head></head><body><div dj-root dj-view="app.View">'
            + '<p id="t">before<b>mid</b>after</p><button id="go" dj-click="bump">Go</button></div></body></html>');
        try {
            const doc = page.win.document;
            const meta = doc.createElement('meta');
            meta.setAttribute('name', 'djust-render-digest');
            meta.setAttribute('content', `d0.${page.shape()}`);
            doc.head.appendChild(meta);
            page.win.djust._installPageParameterContracts();
            const p = doc.getElementById('t');
            p.appendChild(p.firstChild); // "before" goes last
            p.insertBefore(p.lastChild.previousSibling, p.firstChild); // "after" goes first
            expect(p.textContent).toBe('aftermidbefore');
            const request = await send(page, () => ({html: '<div dj-root=""><p id="t">before<b>mid</b>after</p>'
                + '<button dj-click="bump" id="go">Go</button></div>', version: 1}));
            expect(request.token).toBe(null);
        } finally { page.close(); }
    });

    // The shape the client computes is compared with the SERVER's, not with an
    // earlier reading of the same DOM. So it does not matter when the rows
    // were moved: these two are the cases an order remembered by the client
    // cannot see.
    it('rows reordered before the client initialises (a page script\'s DOMContentLoaded listener that runs first)', async () => {
        // The token is the server's, for the page as rendered: A above B.
        const rendered = await loadPage(LISTS);
        const shape = rendered.shape();
        rendered.close();
        const page = await loadPage(
            LISTS.replace('<head></head>', `<head><meta name="djust-render-digest" content="d0.${shape}"></head>`),
            '/',
            {before: (win) => win.document.addEventListener('DOMContentLoaded', () => {
                const list = win.document.getElementById('l');
                list.insertBefore(list.children[1], list.children[0]); // a saved sort order
            })},
        );
        try {
            expect(rows(page, 'l')).toEqual([['B', 'B'], ['A', 'A']]);
            // The server deleted A and believes B is what is left.
            const request = await send(page, serverWith(
                [{type: 'RemoveChild', path: [0], index: 0}], rootOf([['B', 'B']], M)));
            expect(request.token).toBe(null);
            expect(rows(page, 'l')).toEqual([['B', 'B']]);
        } finally { page.close(); }
    });

    for (const [how, arrange] of [
        ['a djust:patched listener', (page, reorder) => page.win.addEventListener('djust:patched', reorder)],
        ['a hook\'s updated()', null],
    ]) {
        it(`rows reordered in a callback after a patch is applied (${how})`, async () => {
            const hooked = arrange === null;
            const html = hooked ? LISTS.replace('<ul id="l">', '<ul id="l" dj-hook="Sorter">') : LISTS;
            const reorder = (doc) => {
                const list = doc.getElementById('l');
                if (list.children[0].getAttribute('data-x') === 'A') {
                    list.insertBefore(list.children[1], list.children[0]);
                }
            };
            const rendered = await loadPage(html, '/', {hooks: {Sorter: {}}});
            const shape = rendered.shape();
            rendered.close();
            const page = await loadPage(
                html.replace('<head></head>', `<head><meta name="djust-render-digest" content="d0.${shape}"></head>`),
                '/',
                {hooks: {Sorter: {updated() { reorder(this.el.ownerDocument); }}}},
            );
            try {
                if (!hooked) arrange(page, () => reorder(page.win.document));
                const attr = hooked ? ' dj-hook="Sorter"' : '';
                const truth = (l) => rootOf(l, M).replace('<ul id="l">', `<ul id="l"${attr}>`);
                // Event 1: a patch answer (text only), naming the token of the
                // page it leaves: the same tree, one text changed.
                const first = await send(page, (request) => {
                    expect(request.token).not.toBe(null);
                    return {patches: [{type: 'SetText', path: [1, 0, 0], text: 'C2'}], render_digest: 'PENDING'};
                });
                expect(first.token).toBe(`d0.${shape}`);
                // The callback ran inside the answer's handling and moved B above A.
                expect(rows(page, 'l')).toEqual([['B', 'B'], ['A', 'A']]);
                // Event 2: the server deleted A. With a token it would answer
                // RemoveChild index 0, which here is B.
                const second = await send(page, serverWith(
                    [{type: 'RemoveChild', path: [0], index: 0}], truth([['B', 'B']])));
                expect(second.token).toBe(null);
                expect(rows(page, 'l')).toEqual([['B', 'B']]);
            } finally { page.close(); }
        });
    }

    it('the DOM is reordered while a View Transition holds the patches back: none is applied', async () => {
        const page = await open();
        try {
            const {win} = page;
            const doc = win.document;
            doc.body.setAttribute('dj-view-transitions', '');
            // As a browser does: the update callback runs in a later microtask.
            doc.startViewTransition = (update) => {
                const done = Promise.resolve().then(() => {
                    // What another script does in between.
                    const list = doc.getElementById('l');
                    list.insertBefore(list.children[1], list.children[0]);
                    update();
                });
                return {updateCallbackDone: done, finished: done, ready: done, skipTransition() {}};
            };
            const server = (request) => request.resync
                ? {html: rootOf([['A', 'A-updated'], ['B', 'B']], M), version: 1}
                : {patches: [{type: 'SetText', path: [0, 0, 0], text: 'A-updated'}],
                    render_digest: 'd1.0000000000000000'};
            const request = await send(page, server);
            // The token was good when the request left ...
            expect(request.token).not.toBe(null);
            // ... and the patch was not applied to the row that had moved up.
            expect(page.requests.map(r => r.resync)).toEqual([false, true]);
            expect(rows(page, 'l')).toEqual([['A', 'A-updated'], ['B', 'B']]);
            expect(page.reloads()).toBe(0);
        } finally { page.close(); }
    });
});

describe('a client cache hit', () => {
    it('changes the DOM with no answer from the server: the token is forgotten', async () => {
        const page = await loadPage(PAGE('d0'));
        try {
            const {win} = page;
            win.djust.setCacheConfig({bump: {ttl: 60}});
            const cached = (request) => ({
                patches: [{type: 'SetText', path: [0, 0], text: 'cached'}],
                cache_request_id: JSON.parse(request.body)._cacheRequestId,
                render_digest: token('d1', 'cached'),
            });
            const first = await bump(page, cached);
            expect(first.token).toBe(token('d0'));
            expect(shown(page)).toBe('cached');
            win.document.getElementById('n').textContent = 'changed since';
            // The same event again: answered from the cache, with no request.
            await bump(page, () => { throw new Error('the cache should have answered'); });
            expect(page.requests.length).toBe(1);
            expect(shown(page)).toBe('cached');
            // Text only, so the DOM's shape is the same; the token still must
            // not be sent, because no server render stands behind this DOM.
            const other = await page.send('other', {}, whole(5, 'd5'), win.document.getElementById('go'));
            expect(other.token).toBe(null);
        } finally { page.close(); }
    });
});
