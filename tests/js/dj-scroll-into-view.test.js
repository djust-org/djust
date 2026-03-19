/**
 * Tests for dj-scroll-into-view — auto-scroll on render
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

function createDom(bodyHtml = '<span>content</span>') {
    const dom = new JSDOM(`<!DOCTYPE html>
<html>
<body>
  <div dj-view="test.views.TestView" dj-root>
    ${bodyHtml}
  </div>
</body>
</html>`, { runScripts: 'dangerously' });

    class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        constructor() {
            this.readyState = MockWebSocket.OPEN;
            this.onopen = null;
            this.onclose = null;
            this.onmessage = null;
            this.onerror = null;
        }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;

    dom.window.eval(clientCode);

    return { dom };
}

describe('dj-scroll-into-view', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('calls scrollIntoView on elements with dj-scroll-into-view after DOM update', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view>scroll me</div>'
        );
        const doc = dom.window.document;

        const el = doc.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();

        expect(el.scrollIntoView).toHaveBeenCalled();
    });

    it('uses smooth behavior by default', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view>scroll me</div>'
        );
        const doc = dom.window.document;

        const el = doc.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();

        expect(el.scrollIntoView).toHaveBeenCalledWith(
            expect.objectContaining({ behavior: 'smooth', block: 'nearest' })
        );
    });

    it('supports "instant" value', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view="instant">scroll me</div>'
        );
        const el = dom.window.document.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();

        expect(el.scrollIntoView).toHaveBeenCalledWith(
            expect.objectContaining({ behavior: 'instant', block: 'nearest' })
        );
    });

    it('supports "center" value', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view="center">scroll me</div>'
        );
        const el = dom.window.document.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();

        expect(el.scrollIntoView).toHaveBeenCalledWith(
            expect.objectContaining({ behavior: 'smooth', block: 'center' })
        );
    });

    it('supports "start" value', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view="start">scroll me</div>'
        );
        const el = dom.window.document.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();

        expect(el.scrollIntoView).toHaveBeenCalledWith(
            expect.objectContaining({ behavior: 'smooth', block: 'start' })
        );
    });

    it('supports "end" value', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view="end">scroll me</div>'
        );
        const el = dom.window.document.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();

        expect(el.scrollIntoView).toHaveBeenCalledWith(
            expect.objectContaining({ behavior: 'smooth', block: 'end' })
        );
    });

    it('one-shot: does not scroll same element twice on subsequent updates', () => {
        const { dom } = createDom(
            '<div id="scroll-target" dj-scroll-into-view>scroll me</div>'
        );
        const el = dom.window.document.getElementById('scroll-target');
        el.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();
        expect(el.scrollIntoView).toHaveBeenCalledTimes(1);

        dom.window.djust.reinitAfterDOMUpdate();
        expect(el.scrollIntoView).toHaveBeenCalledTimes(1);
    });

    it('scrolls new elements that replace VDOM nodes (fresh node)', () => {
        const { dom } = createDom(
            '<div id="container"><div id="scroll-target" dj-scroll-into-view>scroll me</div></div>'
        );
        const doc = dom.window.document;

        const el1 = doc.getElementById('scroll-target');
        el1.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();
        expect(el1.scrollIntoView).toHaveBeenCalledTimes(1);

        // Simulate VDOM replacement
        const container = doc.getElementById('container');
        container.innerHTML = '<div id="scroll-target-new" dj-scroll-into-view>new content</div>';
        const el2 = doc.getElementById('scroll-target-new');
        el2.scrollIntoView = vi.fn();

        dom.window.djust.reinitAfterDOMUpdate();
        expect(el2.scrollIntoView).toHaveBeenCalledTimes(1);
    });

    it('does not throw when no dj-scroll-into-view elements exist', () => {
        const { dom } = createDom('<div>no scroll targets</div>');

        expect(() => {
            dom.window.djust.reinitAfterDOMUpdate();
        }).not.toThrow();
    });
});
