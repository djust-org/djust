/**
 * The incremental DOM shape (#4001): kept pieces, same value.
 *
 * domShape keeps each element's piece of the shape text with its CRC-32 and
 * Adler-32 and combines them, so after an edit it rehashes only what the edit
 * touched; a MutationObserver forgets the pieces an edit makes stale. The
 * value must stay the server's (`dom_shape`, python/djust/_render_digest.py),
 * byte for byte.
 *
 * `fixtures/dom_shape_random_4001.json` (written and checked against the
 * server by python/djust/tests/test_dom_shape_random_4001.py) holds seeded
 * random pages and steps of edits, with the server's shape after each step.
 * Here the same edits are made to the DOM and the client's shape, warm (from
 * kept pieces) and cold (from scratch), must equal it. Then the cases the
 * fixture cannot spell: a template's content, which the observer does not see,
 * and an element changed while it was out of the document.
 */
import {describe, it, expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {loadPage} from './_http_fallback_replay.js';

const {pages} = JSON.parse(readFileSync('./tests/js/fixtures/dom_shape_random_4001.json', 'utf8'));

const doc = (root) => `<!doctype html><html><head></head><body>${root}</body></html>`;

function at(root, path) {
    let el = root;
    for (const i of path) el = el.children[i];
    return el;
}

function apply(win, root, op) {
    const el = at(root, op.path);
    if (op.op === 'attr') {
        if (op.value === null) el.removeAttribute(op.name);
        else el.setAttribute(op.name, op.value);
    } else if (op.op === 'text') {
        el.childNodes[op.index].data = op.value;
    } else if (op.op === 'insert') {
        const t = win.document.createElement('template');
        t.innerHTML = op.html;
        el.insertBefore(t.content.firstChild, el.childNodes[op.index] || null);
    } else if (op.op === 'remove') {
        el.remove();
    } else if (op.op === 'detach_attr') {
        const parent = el.parentNode;
        const next = el.nextSibling;
        el.remove();
        el.setAttribute(op.name, op.value);
        parent.insertBefore(el, next);
    } else if (op.op === 'move') {
        el.remove();
        const to = at(root, op.to);
        to.insertBefore(el, to.childNodes[op.index] || null);
    }
}

describe('#4001 the incremental shape is the server\'s on random pages and edits', () => {
    it('covers enough pages and every kind of edit', () => {
        expect(pages.length).toBeGreaterThanOrEqual(40);
        const kinds = new Set(pages.flatMap((p) => p.steps.flatMap((s) => s.ops.map((o) => o.op))));
        expect([...kinds].sort()).toEqual(['attr', 'detach_attr', 'insert', 'move', 'remove', 'text']);
    });

    for (const page of pages) {
        it(page.name, async () => {
            const p = await loadPage(doc(page.html));
            try {
                const root = p.win.document.querySelector('[dj-root]');
                expect(p.shape()).toBe(page.shape);
                page.steps.forEach((step, n) => {
                    step.ops.forEach((op) => apply(p.win, root, op));
                    expect(p.shape(), `${page.name} step ${n} (kept pieces)`).toBe(step.shape);
                    // The same again with nothing kept: the from-scratch path.
                    p.win.djust._domShapeForgetAll();
                    expect(p.shape(), `${page.name} step ${n} (from scratch)`).toBe(step.shape);
                    // Warm again, for the next step.
                    expect(p.shape()).toBe(step.shape);
                });
            } finally { p.close(); }
        });
    }
});

describe('#4001 what the observer cannot see is never kept', () => {
    const fresh = (p) => { p.win.djust._domShapeForgetAll(); return p.shape(); };

    it('an edit inside a template\'s content changes the shape', async () => {
        const p = await loadPage(doc('<div dj-root=""><section><b>x</b><template><p>a</p></template></section><i>z</i></div>'));
        try {
            const before = p.shape();
            const t = p.win.document.querySelector('template');
            t.content.querySelector('p').textContent = 'b';
            const after = p.shape();
            expect(after).not.toBe(before);
            expect(after).toBe(fresh(p));
        } finally { p.close(); }
    });

    it('an element changed while out of the document and put back elsewhere', async () => {
        const p = await loadPage(doc('<div dj-root=""><ul><li>a<b>1</b></li><li>b</li></ul><p>q</p></div>'));
        try {
            p.shape();
            const li = p.win.document.querySelector('li');
            li.remove();
            p.shape();
            li.querySelector('b').textContent = '2';
            li.setAttribute('title', 't');
            p.win.document.querySelector('p').appendChild(li);
            expect(p.shape()).toBe(fresh(p));
        } finally { p.close(); }
    });

    it('dj-disable-with\'s shown text, and a dj-update="ignore" subtree', async () => {
        const p = await loadPage(doc('<div dj-root=""><button>Save<b>!</b></button><div dj-update="ignore"><p>f</p></div></div>'));
        try {
            const before = p.shape();
            const button = p.win.document.querySelector('button');
            button.setAttribute('data-djust-original-text', 'Saving');
            expect(p.shape()).not.toBe(before);
            expect(p.shape()).toBe(fresh(p));
            button.removeAttribute('data-djust-original-text');
            expect(p.shape()).toBe(before);
            p.win.document.querySelector('[dj-update] p').textContent = 'flash';
            expect(p.shape()).toBe(before);
            expect(fresh(p)).toBe(before);
        } finally { p.close(); }
    });

    it('an unchanged page reuses its pieces: one shape walks no element twice', async () => {
        const rows = Array.from({length: 200}, (_, i) => `<tr><td>${i}</td><td>r${i}</td></tr>`).join('');
        const p = await loadPage(doc(`<div dj-root=""><table><tbody>${rows}</tbody></table></div>`));
        try {
            p.shape();
            const proto = p.win.Element.prototype;
            const original = proto.getAttributeNames;
            let walked = 0;
            proto.getAttributeNames = function () { walked += 1; return original.call(this); };
            try {
                p.win.document.querySelectorAll('td')[150].textContent = 'changed';
                p.shape();
            } finally { proto.getAttributeNames = original; }
            // the td, its tr, the tbody and the table: the path, not the page
            expect(walked).toBe(4);
            expect(p.shape()).toBe(fresh(p));
        } finally { p.close(); }
    });
});
