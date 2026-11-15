/**
 * Tests for dj-style (54-dj-style.js): free-form style values applied
 * through the CSSOM, in strict-CSP mode (#3979).
 *
 * With LIVEVIEW_CONFIG["csp_strict_styles"] the server writes
 * `data-dj-style-<property>="<value>"` (and the bare `data-dj-style` marker)
 * instead of a `style=` attribute, and the page carries
 * `<meta name="djust-styles" content="attrs">`. The module sets each value
 * with `el.style.setProperty`, keeps it in step with morphs and patches,
 * removes it when the attribute goes away, re-checks the text it is handed,
 * and does nothing at all on a page without the meta.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const META = '<meta name="djust-styles" content="attrs">';

function createDom(bodyHtml = '', { meta = true } = {}) {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head>${meta ? META : ''}</head>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${bodyHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously', url: 'http://localhost/' });

    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null; this.onclose = null; this.onmessage = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    // jsdom has constructed stylesheets but no adoptedStyleSheets.
    dom.window.document.adoptedStyleSheets = [];
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

// MutationObserver callbacks are microtasks.
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const S = 'data-dj-style';

describe('dj-style', () => {
    it('applies the values on first paint', () => {
        const dom = createDom(
            `<div id="a" ${S} data-dj-style-width="240px" data-dj-style-min-width="240px"></div>` +
            `<span id="b" ${S} data-dj-style-background-color="#ff0000"></span>`
        );
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        expect(a.style.getPropertyValue('width')).toBe('240px');
        expect(a.style.getPropertyValue('min-width')).toBe('240px');
        expect(b.style.getPropertyValue('background-color')).toBe('rgb(255, 0, 0)');
    });

    it('does nothing on a page that did not opt in (no meta)', async () => {
        const dom = createDom(`<div id="a" ${S} data-dj-style-width="240px"></div>`, { meta: false });
        const a = dom.window.document.getElementById('a');
        expect(a.getAttribute('style')).toBe(null);
        a.setAttribute('data-dj-style-width', '300px');
        dom.window.document.body.insertAdjacentHTML('beforeend', `<p id="c" ${S} data-dj-style-height="9px"></p>`);
        await flush();
        expect(a.getAttribute('style')).toBe(null);
        expect(dom.window.document.getElementById('c').getAttribute('style')).toBe(null);
    });

    it('turns on only for the meta in the head: one in the body does not count', async () => {
        const off = createDom(
            `<meta name="djust-styles" content="attrs"><div id="a" ${S} data-dj-style-width="240px"></div>`,
            { meta: false },
        );
        const a = off.window.document.getElementById('a');
        expect(off.window.document.querySelector('body meta[name="djust-styles"]')).not.toBe(null);
        expect(a.getAttribute('style')).toBe(null);
        a.setAttribute('data-dj-style-width', '300px');
        await flush();
        expect(a.getAttribute('style')).toBe(null);

        const on = createDom(`<div id="b" ${S} data-dj-style-width="240px"></div>`);
        expect(on.window.document.head.querySelector('meta[name="djust-styles"]')).not.toBe(null);
        expect(on.window.document.getElementById('b').style.getPropertyValue('width')).toBe('240px');
    });

    it('handles a change other code makes while the module writes', async () => {
        // A custom element that reacts to its `style` changing by setting a new value:
        // its record arrives while the module's callback runs and must not be dropped.
        const dom = createDom('');
        dom.window.eval(`
            class XEl extends HTMLElement {
                static get observedAttributes() { return ['style']; }
                attributeChangedCallback() {
                    if (!this._done && this.style.getPropertyValue('height') === '10px') {
                        this._done = true;
                        this.setAttribute('data-dj-style-height', '77px');
                    }
                }
            }
            customElements.define('x-el', XEl);
        `);
        dom.window.document.body.insertAdjacentHTML(
            'beforeend', `<x-el id="x" ${S} data-dj-style-height="10px"></x-el>`);
        await flush();
        await flush();
        const x = dom.window.document.getElementById('x');
        expect(x.getAttribute('data-dj-style-height')).toBe('77px');
        expect(x.style.getPropertyValue('height')).toBe('77px');
    });

    it('applies a custom property of its kind', () => {
        const dom = createDom(
            `<div id="a" ${S} data-dj-style---dj-calendar-event-color="#123456" ` +
            'data-dj-style--webkit-line-clamp="3"></div>'
        );
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('--dj-calendar-event-color')).toBe('#123456');
    });

    it('re-checks the text: nothing that loads or reaches anything', () => {
        const dom = createDom(
            `<div id="a" ${S} data-dj-style---dj-calendar-event-color="url(/beacon/x)"></div>` +
            `<div id="b" ${S} data-dj-style---dj-collab-sel-color="var(--x, url(/y))"></div>` +
            `<div id="c" ${S} data-dj-style-background-color="image-set('/z' 1x)"></div>` +
            `<div id="d" ${S} data-dj-style---dj-masonry-columns="3; x"></div>` +
            `<div id="e" ${S} data-dj-style---dj-calendar-event-color="red"></div>`
        );
        const d = dom.window.document;
        expect(d.getElementById('a').style.getPropertyValue('--dj-calendar-event-color')).toBe('');
        expect(d.getElementById('b').style.getPropertyValue('--dj-collab-sel-color')).toBe('');
        expect(d.getElementById('c').style.getPropertyValue('background-color')).toBe('');
        expect(d.getElementById('d').style.getPropertyValue('--dj-masonry-columns')).toBe('');
        expect(d.getElementById('e').style.getPropertyValue('--dj-calendar-event-color')).toBe('red');
        const ok = dom.window.djust.djStyle.ok;
        for (const v of ['url(x)', 'attr(data-x)', 'a\\b', '/* c */', '@import', 'a;b', 'a{b}', '"q"', 'expression(1)']) {
            expect(ok('width', v)).toBe(false);
        }
    });

    it('cannot cover the page: no fixed or absolute position, a bounded z-index', () => {
        const dom = createDom(
            `<a id="a" ${S} data-dj-style-position="fixed" data-dj-style-z-index="2147483647" ` +
            'data-dj-style-background-color="white"></a>' +
            `<div id="b" ${S} data-dj-style-position="sticky" data-dj-style-z-index="10"></div>`
        );
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        expect(a.style.getPropertyValue('position')).toBe('');
        expect(a.style.getPropertyValue('z-index')).toBe('');
        expect(a.style.getPropertyValue('inset')).toBe('');
        expect(b.style.getPropertyValue('position')).toBe('sticky');
        expect(b.style.getPropertyValue('z-index')).toBe('10');
    });

    it('updates a property when the attribute changes', async () => {
        const dom = createDom(`<div id="a" ${S} data-dj-style-height="10px"></div>`);
        const a = dom.window.document.getElementById('a');
        a.setAttribute('data-dj-style-height', '20px');
        await flush();
        expect(a.style.getPropertyValue('height')).toBe('20px');
    });

    it('removes a property when the attribute goes away, or turns hostile', async () => {
        const dom = createDom(`<div id="a" ${S} data-dj-style-height="10px" data-dj-style-width="5px"></div>`);
        const a = dom.window.document.getElementById('a');
        a.removeAttribute('data-dj-style-height');
        a.setAttribute('data-dj-style-width', 'url(x)');
        await flush();
        expect(a.style.getPropertyValue('height')).toBe('');
        expect(a.style.getPropertyValue('width')).toBe('');
    });

    it('ignores a property outside the allowlist', async () => {
        // On an element the module does apply (it carries an allowed property too).
        const dom = createDom(`<div id="a" ${S} data-dj-style-width="5px" data-dj-style-cursor="wait"></div>`);
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('width')).toBe('5px');
        expect(a.style.getPropertyValue('cursor')).toBe('');
        a.setAttribute('data-dj-style-cursor', 'pointer');
        a.setAttribute('data-dj-style-width', '6px');
        await flush();
        expect(a.style.getPropertyValue('width')).toBe('6px');
        expect(a.style.getPropertyValue('cursor')).toBe('');
    });

    it('cannot add a second declaration through a value', () => {
        const dom = createDom(`<div id="a" ${S} data-dj-style-width="1px;background-color:red"></div>`);
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('background-color')).toBe('');
        expect(a.style.getPropertyValue('width')).toBe('');
    });

    it('applies values in nodes a patch or a lazily mounted child inserts', async () => {
        const dom = createDom('<div id="host"></div>');
        const host = dom.window.document.getElementById('host');
        host.innerHTML = `<section><p id="c" ${S} data-dj-style-max-height="300px"></p></section>`;
        // A large swap (one scan of the parent) as well as a single insert.
        let many = '';
        for (let i = 0; i < 20; i++) many += `<i id="m${i}" ${S} data-dj-style-width="${i}px"></i>`;
        dom.window.document.body.insertAdjacentHTML('beforeend', `<div id="big">${many}</div>`);
        await flush(); // the swap is its own batch: one record that adds 20 nodes
        const big = dom.window.document.getElementById('big');
        big.innerHTML = many.replace(/px/g, '0px');
        await flush();
        expect(dom.window.document.getElementById('c').style.getPropertyValue('max-height')).toBe('300px');
        expect(dom.window.document.getElementById('m7').style.getPropertyValue('width')).toBe('70px');
    });

    it('keeps the values through a morph that drops the client-set style attribute', async () => {
        const dom = createDom(`<div id="a" ${S} data-dj-style-width="240px"></div>`);
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('width')).toBe('240px');
        // The server's HTML has no `style`: a full morph removes it and keeps
        // the (unchanged) data attribute.
        a.removeAttribute('style');
        await flush();
        expect(a.style.getPropertyValue('width')).toBe('240px');
    });

    it('follows a morph through the real patcher', async () => {
        const dom = createDom(`<div id="a" ${S} data-dj-style-width="100px"></div>`);
        const doc = dom.window.document;
        const desired = doc.createElement('div');
        desired.innerHTML = `<div id="a" ${S} data-dj-style-width="300px" data-dj-style-gap="4px"></div>`;
        dom.window.djust.morphChildren(doc.querySelector('[dj-root]'), desired);
        await flush();
        const a = doc.getElementById('a');
        expect(a.style.getPropertyValue('width')).toBe('300px');
        expect(a.style.getPropertyValue('gap')).toBe('4px');

        const next = doc.createElement('div');
        next.innerHTML = `<div id="a" ${S} data-dj-style-gap="4px"></div>`;
        dom.window.djust.morphChildren(doc.querySelector('[dj-root]'), next);
        await flush();
        expect(a.style.getPropertyValue('width')).toBe('');
        expect(a.style.getPropertyValue('gap')).toBe('4px');
    });

    it('adds the skeleton rules once, through the CSSOM, for data-dj-css', async () => {
        const dom = createDom(`<div class="djust-skeleton" ${S} data-dj-css="skeleton"></div>`);
        const doc = dom.window.document;
        expect(doc.adoptedStyleSheets.length).toBe(1);
        const sheet = doc.adoptedStyleSheets[0];
        const text = Array.from(sheet.cssRules).map((r) => r.cssText).join('\n');
        expect(text).toContain('djust-skeleton-shimmer');
        expect(text).toContain('.djust-skeleton-circle');
        const before = sheet.cssRules.length;
        doc.body.insertAdjacentHTML('beforeend', `<div ${S} data-dj-css="skeleton"></div><div ${S} data-dj-css="nope"></div>`);
        await flush();
        expect(sheet.cssRules.length).toBe(before);
        expect(doc.adoptedStyleSheets.length).toBe(1);
    });

    it('server and client allow exactly the same properties', () => {
        const dom = createDom('');
        const client = new Set(dom.window.djust.djStyle.props);
        const src = fs.readFileSync('./python/djust/components/utils.py', 'utf-8');
        const block = /STYLE_PROPERTY_NAMES = \(([\s\S]*?)\n\)/.exec(src)[1];
        const server = new Set(Array.from(block.matchAll(/"([^"]+)"/g), (m) => m[1]));
        expect(server.size).toBeGreaterThan(20);
        expect([...client].filter((p) => !server.has(p))).toEqual([]);
        expect([...server].filter((p) => !client.has(p))).toEqual([]);
        expect(client.has('inset')).toBe(false);
    });
});
