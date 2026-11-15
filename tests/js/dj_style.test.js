/**
 * Tests for dj-style (54-dj-style.js): free-form style values applied
 * through the CSSOM, for a strict CSP (#3979).
 *
 * The server writes `data-dj-style-<property>="<value>"` instead of a
 * `style=` attribute; the module sets each one with
 * `el.style.setProperty`, keeps it in step with morphs and patches, and
 * removes it when the attribute goes away.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html><head></head>
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
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

// MutationObserver callbacks are microtasks.
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('dj-style', () => {
    it('applies the values on first paint', () => {
        const dom = createDom(
            '<div id="a" data-dj-style-width="240px" data-dj-style-min-width="240px"></div>' +
            '<span id="b" data-dj-style-background-color="#ff0000"></span>'
        );
        const a = dom.window.document.getElementById('a');
        const b = dom.window.document.getElementById('b');
        expect(a.style.getPropertyValue('width')).toBe('240px');
        expect(a.style.getPropertyValue('min-width')).toBe('240px');
        expect(b.style.getPropertyValue('background-color')).toBe('rgb(255, 0, 0)');
    });

    it('applies a custom property and a vendor property', () => {
        const dom = createDom(
            '<div id="a" data-dj-style---dj-calendar-event-color="#123456" ' +
            'data-dj-style--webkit-line-clamp="3"></div>'
        );
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('--dj-calendar-event-color')).toBe('#123456');
    });

    it('updates a property when the attribute changes', async () => {
        const dom = createDom('<div id="a" data-dj-style-height="10px"></div>');
        const a = dom.window.document.getElementById('a');
        a.setAttribute('data-dj-style-height', '20px');
        await flush();
        expect(a.style.getPropertyValue('height')).toBe('20px');
    });

    it('removes a property when the attribute goes away', async () => {
        const dom = createDom('<div id="a" data-dj-style-height="10px" data-dj-style-width="5px"></div>');
        const a = dom.window.document.getElementById('a');
        a.removeAttribute('data-dj-style-height');
        await flush();
        expect(a.style.getPropertyValue('height')).toBe('');
        expect(a.style.getPropertyValue('width')).toBe('5px');
    });

    it('ignores a property outside the allowlist', async () => {
        // On an element the module does apply (it carries an allowed property too).
        const dom = createDom('<div id="a" data-dj-style-width="5px" data-dj-style-cursor="wait"></div>');
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
        const dom = createDom('<div id="a" data-dj-style-width="1px;background-color:red"></div>');
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('background-color')).toBe('');
        expect(a.style.getPropertyValue('width')).toBe('');
    });

    it('applies values in nodes a patch or a lazily mounted child inserts', async () => {
        const dom = createDom('<div id="host"></div>');
        const host = dom.window.document.getElementById('host');
        host.innerHTML = '<section><p id="c" data-dj-style-max-height="300px"></p></section>';
        await flush();
        expect(dom.window.document.getElementById('c').style.getPropertyValue('max-height')).toBe('300px');
    });

    it('keeps the values through a morph that drops the client-set style attribute', async () => {
        const dom = createDom('<div id="a" data-dj-style-width="240px"></div>');
        const a = dom.window.document.getElementById('a');
        expect(a.style.getPropertyValue('width')).toBe('240px');
        // The server's HTML has no `style`: a full morph removes it and keeps
        // the (unchanged) data attribute.
        a.removeAttribute('style');
        await flush();
        expect(a.style.getPropertyValue('width')).toBe('240px');
    });

    it('follows a morph through the real patcher', async () => {
        const dom = createDom('<div id="a" data-dj-style-width="100px"></div>');
        const doc = dom.window.document;
        const desired = doc.createElement('div');
        desired.innerHTML = '<div id="a" data-dj-style-width="300px" data-dj-style-gap="4px"></div>';
        dom.window.djust.morphChildren(doc.querySelector('[dj-root]'), desired);
        await flush();
        const a = doc.getElementById('a');
        expect(a.style.getPropertyValue('width')).toBe('300px');
        expect(a.style.getPropertyValue('gap')).toBe('4px');

        const next = doc.createElement('div');
        next.innerHTML = '<div id="a" data-dj-style-gap="4px"></div>';
        dom.window.djust.morphChildren(doc.querySelector('[dj-root]'), next);
        await flush();
        expect(a.style.getPropertyValue('width')).toBe('');
        expect(a.style.getPropertyValue('gap')).toBe('4px');
    });

    it('adds the skeleton rules once, through the CSSOM, for data-dj-css', async () => {
        const dom = createDom('<div class="djust-skeleton" data-dj-css="skeleton"></div>');
        const doc = dom.window.document;
        // A constructed stylesheet where the browser has them; jsdom has not, so
        // the fallback <style> element (an empty one, filled through the CSSOM).
        const adopted = doc.adoptedStyleSheets || [];
        expect(doc.querySelectorAll('#djust-css').length + adopted.length).toBe(1);
        const sheet = adopted[0] || doc.getElementById('djust-css').sheet;
        if (!adopted.length) expect(doc.getElementById('djust-css').textContent).toBe('');
        const text = Array.from(sheet.cssRules).map((r) => r.cssText).join('\n');
        expect(text).toContain('djust-skeleton-shimmer');
        expect(text).toContain('.djust-skeleton-circle');
        const before = sheet.cssRules.length;
        doc.body.insertAdjacentHTML('beforeend', '<div data-dj-css="skeleton"></div><div data-dj-css="nope"></div>');
        await flush();
        expect(sheet.cssRules.length).toBe(before);
    });

    it('server and client allow the same properties', () => {
        const dom = createDom('');
        const props = dom.window.djust.djStyle.props;
        const src = fs.readFileSync('./python/djust/components/utils.py', 'utf-8');
        for (const p of props) expect(src).toContain(`"${p}"`);
        expect(props).toContain('width');
        expect(props).not.toContain('cursor');
    });
});
