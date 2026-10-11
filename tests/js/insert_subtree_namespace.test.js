/**
 * An `InsertSubtree` payload is parsed as children of the element it goes
 * into, in that element's namespace: under `<svg>` / `<math>` the payload's
 * elements are SVG / MathML, as a full page parse makes them, not HTML.
 * HTML parents (and the HTML integration points `foreignObject`, `desc`,
 * `title` and HTML `annotation-xml`) parse it as HTML, as before.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';

const fs = await import('fs');
const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
});
if (!dom.window.CSS) dom.window.CSS = {};
if (!dom.window.CSS.escape) {
    dom.window.CSS.escape = (str) => String(str).replace(/([^\w-])/g, '\\$1');
}
dom.window.eval(clientCode);

const { _parseSubtreeHtml, _applyInsertSubtree, _applySinglePatch } = dom.window.djust;
const document = dom.window.document;

const HTML = 'http://www.w3.org/1999/xhtml';
const SVG = 'http://www.w3.org/2000/svg';
const MATH = 'http://www.w3.org/1998/Math/MathML';

/** `ns:name` of every element in `nodes`, depth first. */
function names(nodes) {
    const out = [];
    const prefix = { [HTML]: '', [SVG]: 'svg:', [MATH]: 'math:' };
    const walk = (n) => {
        if (n.nodeType !== 1) return;
        out.push(prefix[n.namespaceURI] + n.localName);
        Array.from(n.childNodes).forEach(walk);
    };
    Array.from(nodes).forEach(walk);
    return out;
}

/** Parse `bodyHtml` as a page body and return the element matching `selector`. */
function parentIn(bodyHtml, selector) {
    document.body.innerHTML = bodyHtml;
    return document.body.querySelector(selector);
}

describe('_parseSubtreeHtml: the parent decides the namespace', () => {
    it('parses as HTML without a parent or under an HTML parent', () => {
        const markup = '<!--dj-if id="if-1"--><iframe></iframe><a href="#">x</a><!--/dj-if-->';
        expect(names(_parseSubtreeHtml(markup).childNodes)).toEqual(['iframe', 'a']);
        const div = parentIn('<div id="p"></div>', '#p');
        expect(names(_parseSubtreeHtml(markup, div).childNodes)).toEqual(['iframe', 'a']);
    });

    it('parses SVG children as SVG', () => {
        const g = parentIn('<svg><g id="p"></g></svg>', '#p');
        const fragment = _parseSubtreeHtml(
            '<!--dj-if id="if-1"--><iframe src="x"></iframe><a href="#">x</a><image href="x"></image><rect></rect><!--/dj-if-->',
            g
        );
        expect(names(fragment.childNodes)).toEqual(['svg:iframe', 'svg:a', 'svg:image', 'svg:rect']);
        // The dj-if markers come through, first and last, with no wrapper left.
        expect(fragment.firstChild.nodeType).toBe(8);
        expect(fragment.lastChild.nodeType).toBe(8);
        expect(fragment.childNodes.length).toBe(6);
    });

    it('parses MathML children as MathML', () => {
        const mrow = parentIn('<math><mrow id="p"></mrow></math>', '#p');
        const fragment = _parseSubtreeHtml('<a href="#">x</a><mi>y</mi><iframe></iframe>', mrow);
        expect(names(fragment.childNodes)).toEqual(['math:a', 'math:mi', 'math:iframe']);
    });

    it('reads foreign <style> and <noscript> content as markup, escaped text as text', () => {
        const svg = parentIn('<svg id="p"></svg>', '#p');
        const fragment = _parseSubtreeHtml(
            '<style>&lt;img src=x&gt;</style><noscript>&lt;b&gt;</noscript>',
            svg
        );
        expect(names(fragment.childNodes)).toEqual(['svg:style', 'svg:noscript']);
        expect(fragment.childNodes[0].textContent).toBe('<img src=x>');
        expect(fragment.childNodes[1].textContent).toBe('<b>');
    });

    it('parses HTML integration points as HTML', () => {
        for (const [body, selector] of [
            ['<svg><foreignObject id="p"></foreignObject></svg>', '#p'],
            ['<svg><desc id="p"></desc></svg>', '#p'],
            ['<svg><title id="p"></title></svg>', '#p'],
            ['<math><annotation-xml id="p" encoding="text/html"></annotation-xml></math>', '#p'],
            ['<math><annotation-xml id="p" encoding="Application/XHTML+XML"></annotation-xml></math>', '#p'],
        ]) {
            const parent = parentIn(body, selector);
            const fragment = _parseSubtreeHtml('<p>x</p><svg><rect></rect></svg>', parent);
            expect(names(fragment.childNodes)).toEqual(['p', 'svg:svg', 'svg:rect']);
        }
    });

    it('keeps the MathML text integration point rules', () => {
        const mi = parentIn('<math><mi id="p"></mi></math>', '#p');
        const fragment = _parseSubtreeHtml('<b>x</b><mglyph></mglyph>', mi);
        expect(names(fragment.childNodes)).toEqual(['b', 'math:mglyph']);
    });

    it('parses SVG inside a non-HTML annotation-xml as SVG', () => {
        const ann = parentIn('<math><annotation-xml id="p"></annotation-xml></math>', '#p');
        const fragment = _parseSubtreeHtml('<svg><rect></rect></svg><mi>x</mi>', ann);
        expect(names(fragment.childNodes)).toEqual(['svg:svg', 'svg:rect', 'math:mi']);
    });
});

describe('InsertSubtree applies the payload in the parent namespace', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    it('inserts SVG elements under an SVG parent (single patch)', () => {
        document.body.innerHTML =
            '<div dj-root dj-id="0"><svg dj-id="1"><g dj-id="2">tail</g></svg></div>';
        const root = document.body.querySelector('[dj-root]');
        const ok = _applyInsertSubtree(
            {
                type: 'InsertSubtree',
                id: 'if-1',
                path: [0],
                d: '1',
                index: 0,
                html: '<!--dj-if id="if-1"--><a href="#">x</a><!--/dj-if-->',
            },
            root
        );
        expect(ok).toBe(true);
        const svg = root.querySelector('svg');
        expect(names(svg.childNodes)).toEqual(['svg:a', 'svg:g']);
    });

    it('inserts SVG elements under an SVG parent (batched placements)', async () => {
        document.body.innerHTML =
            '<div dj-root dj-id="0"><svg dj-id="1"><g dj-id="2">tail</g></svg></div>';
        const root = document.body.querySelector('[dj-root]');
        const ok = await dom.window.djust.applyPatches(
            [
                {
                    type: 'InsertSubtree',
                    id: 'if-1',
                    path: [0],
                    d: '1',
                    index: 0,
                    html: '<!--dj-if id="if-1"--><iframe src="x"></iframe><!--/dj-if-->',
                },
                {
                    type: 'InsertSubtree',
                    id: 'if-2',
                    path: [0],
                    d: '1',
                    // Final index: after if-1's span (3 nodes) and the g.
                    index: 4,
                    html: '<!--dj-if id="if-2"--><image href="x"></image><!--/dj-if-->',
                },
            ],
            root
        );
        expect(ok).toBe(true);
        const svg = root.querySelector('svg');
        expect(names(svg.childNodes)).toEqual(['svg:iframe', 'svg:g', 'svg:image']);
    });

    it('inserts HTML elements under an HTML parent as before', () => {
        document.body.innerHTML = '<div dj-root dj-id="0"><p dj-id="1">x</p></div>';
        const root = document.body.querySelector('[dj-root]');
        const ok = _applySinglePatch(
            {
                type: 'InsertSubtree',
                id: 'if-1',
                path: [],
                d: '0',
                index: 0,
                html: '<!--dj-if id="if-1"--><a href="#">x</a><!--/dj-if-->',
            },
            root
        );
        expect(ok).toBe(true);
        expect(names(root.childNodes)).toEqual(['a', 'p']);
    });
});

describe('InsertChild creates the node in the parent namespace', () => {
    const vnode = (tag, attrs = {}, children = []) => ({ tag, attrs, children });

    it('creates an allow-listed HTML name under <svg> as an inert SVG element', () => {
        const g = parentIn('<svg><g id="p"></g></svg>', '#p');
        const node = dom.window.djust.createNodeFromVNode(
            vnode('iframe', { src: 'x' }, [vnode('a', { href: '#' })]),
            g
        );
        expect(names([node])).toEqual(['svg:iframe', 'svg:a']);
    });

    it('creates MathML elements under <math>, HTML below a text integration point', () => {
        const math = parentIn('<math id="p"></math>', '#p');
        const node = dom.window.djust.createNodeFromVNode(
            vnode('mrow', {}, [vnode('mi', {}, [vnode('b'), vnode('mglyph')]), vnode('iframe')]),
            math
        );
        expect(names([node])).toEqual(['math:mrow', 'math:mi', 'b', 'math:mglyph', 'math:iframe']);
    });

    it('creates <math> under an HTML parent as MathML and HTML below foreignObject', () => {
        const div = parentIn('<div id="p"></div>', '#p');
        expect(names([dom.window.djust.createNodeFromVNode(vnode('math', {}, [vnode('mi')]), div)]))
            .toEqual(['math:math', 'math:mi']);
        const fo = parentIn('<svg><foreignObject id="p"></foreignObject></svg>', '#p');
        expect(names([dom.window.djust.createNodeFromVNode(vnode('iframe'), fo)])).toEqual(['iframe']);
        const svg = parentIn('<svg id="p"></svg>', '#p');
        expect(names([dom.window.djust.createNodeFromVNode(vnode('foreignObject', {}, [vnode('p')]), svg)]))
            .toEqual(['svg:foreignObject', 'p']);
    });

    it('still blocks names outside the allow lists', () => {
        const svg = parentIn('<svg id="p"></svg>', '#p');
        expect(names([dom.window.djust.createNodeFromVNode(vnode('script'), svg)])).toEqual(['span']);
        const math = parentIn('<math id="p"></math>', '#p');
        expect(names([dom.window.djust.createNodeFromVNode(vnode('script'), math)])).toEqual(['span']);
    });

    it('creates the children of a placeholder in its foreign namespace', () => {
        // `filter` / `mstack` are outside the allow lists and become a <span>;
        // the full render has svg:filter > svg:iframe and math:mstack > math:a,
        // so the children stay SVG / MathML, not HTML below the span.
        const g = parentIn('<svg><g id="p"></g></svg>', '#p');
        const filter = dom.window.djust.createNodeFromVNode(
            vnode('filter', {}, [vnode('iframe', { src: 'x' }), vnode('feBlend', {}, [vnode('a', { href: '#' })])]),
            g
        );
        expect(names([filter])).toEqual(['span', 'svg:iframe', 'span', 'svg:a']);
        const mrow = parentIn('<math><mrow id="p"></mrow></math>', '#p');
        const mstack = dom.window.djust.createNodeFromVNode(
            vnode('mstack', {}, [vnode('a', { href: '#' }), vnode('form'), vnode('svg', {}, [vnode('iframe')])]),
            mrow
        );
        expect(names([mstack])).toEqual(['span', 'math:a', 'math:form', 'math:svg', 'math:iframe']);
    });

    it('applies an InsertChild patch with a placeholder under <svg>', () => {
        document.body.innerHTML = '<div dj-root dj-id="0"><svg dj-id="1"><g dj-id="2">tail</g></svg></div>';
        const root = document.body.querySelector('[dj-root]');
        const ok = _applySinglePatch(
            { type: 'InsertChild', path: [0], d: '1', index: 0,
              node: vnode('filter', {}, [vnode('iframe', { src: 'x' }, [])]) },
            root
        );
        expect(ok).toBe(true);
        expect(names(root.querySelector('svg').childNodes)).toEqual(['span', 'svg:iframe', 'svg:g']);
    });

    it('applies an InsertChild patch under <svg> in the SVG namespace', () => {
        document.body.innerHTML = '<div dj-root dj-id="0"><svg dj-id="1"><g dj-id="2">tail</g></svg></div>';
        const root = document.body.querySelector('[dj-root]');
        const ok = _applySinglePatch(
            { type: 'InsertChild', path: [0], d: '1', index: 0, node: vnode('a', { href: '#' }, []) },
            root
        );
        expect(ok).toBe(true);
        expect(names(root.querySelector('svg').childNodes)).toEqual(['svg:a', 'svg:g']);
    });

    it('keeps the old name-based choice for a boolean context', () => {
        expect(names([dom.window.djust.createNodeFromVNode(vnode('iframe'), true)])).toEqual(['iframe']);
        expect(names([dom.window.djust.createNodeFromVNode(vnode('rect'), false)])).toEqual(['svg:rect']);
    });
});
