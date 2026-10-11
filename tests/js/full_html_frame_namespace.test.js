/**
 * The full-HTML paths (`html_update`, `html_recovery`, and the morph of a
 * prerendered page on mount) parse the dj-root's inner HTML as the page
 * parses the root's children. A dj-root on an SVG or MathML element keeps
 * its children SVG / MathML, as in the full render, instead of turning
 * them into HTML elements. An HTML root parses as before.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'node:fs';

const code = fs.readFileSync('./python/djust/static/djust/client.js', 'utf8');

const HTML = 'http://www.w3.org/1999/xhtml';
const SVG = 'http://www.w3.org/2000/svg';
const MATH = 'http://www.w3.org/1998/Math/MathML';

let dom;
afterEach(() => dom?.window.close());

function setup(body) {
    dom = new JSDOM('<!DOCTYPE html><html><body>' + body + '</body></html>', {
        url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true,
    });
    if (!dom.window.CSS) dom.window.CSS = {};
    if (!dom.window.CSS.escape) dom.window.CSS.escape = (v) => String(v).replace(/([^\w-])/g, '\\$1');
    dom.window.eval(code);
    const doc = dom.window.document;
    return { api: dom.window.djust, doc, root: doc.querySelector('[dj-root]') };
}

/** `ns:name` of every element below `node`, depth first. */
function names(node) {
    const prefix = { [HTML]: '', [SVG]: 'svg:', [MATH]: 'math:' };
    const out = [];
    const walk = (n) => {
        if (n.nodeType !== 1) return;
        out.push(prefix[n.namespaceURI] + n.localName);
        Array.from(n.childNodes).forEach(walk);
    };
    Array.from(node.childNodes).forEach(walk);
    return out;
}

const ROOTS = {
    svg: {
        body: '<svg><g dj-root dj-view="app.V"><g>one</g><g>tail</g></g></svg>',
        html: '<iframe src="about:blank"></iframe><a href="#x">x</a><g>tail</g>',
        expected: ['svg:iframe', 'svg:a', 'svg:g'],
    },
    math: {
        body: '<math><mrow dj-root dj-view="app.V"><mi>one</mi><mi>tail</mi></mrow></math>',
        html: '<a href="#x">x</a><form action="#f"></form><mi>tail</mi>',
        expected: ['math:a', 'math:form', 'math:mi'],
    },
};

async function sendFrame(api, frame) {
    const ws = new api.LiveViewWebSocket();
    ws.viewMounted = true;
    await ws.handleMessage(frame);
}

describe('full-HTML frames keep an SVG / MathML root\'s namespace', () => {
    for (const [kind, c] of Object.entries(ROOTS)) {
        it(`html_update: ${kind} root`, async () => {
            const { api, root } = setup(c.body);
            await sendFrame(api, { type: 'html_update', html: c.html, version: 2 });
            expect(names(root)).toEqual(c.expected);
        });

        it(`html_recovery: ${kind} root`, async () => {
            const { api, root } = setup(c.body);
            await sendFrame(api, { type: 'html_recovery', html: c.html, version: 2 });
            expect(names(root)).toEqual(c.expected);
        });

        it(`prerendered mount morph: ${kind} root`, () => {
            const { api, root } = setup(c.body);
            const temp = api._parseRootHtmlLive(c.html, root);
            api.morphChildren(root, temp);
            expect(names(root)).toEqual(c.expected);
        });
    }

    it('an HTML root parses as before', async () => {
        const { api, root } = setup('<div dj-root dj-view="app.V"><p>one</p></div>');
        await sendFrame(api, { type: 'html_update', html: '<svg><g></g></svg><iframe></iframe>', version: 2 });
        expect(names(root)).toEqual(['svg:svg', 'svg:g', 'iframe']);
        const temp = api._parseRootHtmlLive('<math><mi>x</mi></math><p>y</p>', root);
        expect(temp.localName).toBe('div');
        expect(temp.namespaceURI).toBe(HTML);
        expect(names(temp)).toEqual(['math:math', 'math:mi', 'p']);
    });

    it('an HTML integration point root (foreignObject) parses HTML', async () => {
        const { api, root } = setup('<svg><foreignObject dj-root dj-view="app.V"><p>one</p></foreignObject></svg>');
        await sendFrame(api, { type: 'html_update', html: '<p>two</p><iframe></iframe>', version: 2 });
        expect(names(root)).toEqual(['p', 'iframe']);
    });

    it('a [dj-root] element in the frame is used, as before', () => {
        const { api, root } = setup(ROOTS.svg.body);
        const parsed = api._parseRootFrameHtml('<g dj-root><circle></circle></g>', root);
        expect(parsed.localName).toBe('g');
        expect(names(parsed)).toEqual(['svg:circle']);
    });
});
