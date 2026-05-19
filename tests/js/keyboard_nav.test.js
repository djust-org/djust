/**
 * Tests for keyboard-nav — #1522 keyboard interaction module
 * (51-keyboard-nav.js).
 *
 * Mirrors tests/js/dj_dialog.test.js: vitest + jsdom, evals client.js,
 * dispatches DOMContentLoaded. Covers the four interactive djust-native
 * components (modal, tablist, accordion, dropdown menu):
 *
 *  - focus trap for modal/dialog (Tab / Shift+Tab wrap)
 *  - Esc-to-close for modal + dropdown
 *  - arrow-key roving for tablist / accordion / dropdown menu
 *
 * jsdom note: jsdom's layout engine is a no-op, so `offsetParent`-based
 * visibility filtering is unreliable. The module therefore filters on
 * DOM order without layout-dependent visibility checks; the tests assert
 * against DOM order accordingly.
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

function waitForMutation(dom) {
    // MutationObserver fires asynchronously on the microtask queue. Flush
    // with a setTimeout(0) so the observer callback runs.
    return new Promise((resolve) => dom.window.setTimeout(resolve, 0));
}

function press(dom, el, key, opts = {}) {
    const ev = new dom.window.KeyboardEvent('keydown', {
        key,
        bubbles: true,
        cancelable: true,
        shiftKey: !!opts.shiftKey,
    });
    el.dispatchEvent(ev);
    return ev;
}

function installHandleEventSpy(dom) {
    dom.window.eval(`
        window._handleEventCalls = [];
        window.handleEvent = function(name, params) {
            window._handleEventCalls.push({ name: name, params: params });
            return Promise.resolve();
        };
    `);
}

// ---------------------------------------------------------------------------
// Module surface
// ---------------------------------------------------------------------------

describe('keyboard-nav — module surface', () => {
    it('exposes window.djust.keyboardNav', () => {
        const dom = createDom('');
        expect(typeof dom.window.djust.keyboardNav).toBe('object');
        expect(typeof dom.window.djust.keyboardNav._handleKeydown).toBe('function');
    });
});

// ---------------------------------------------------------------------------
// Focus trap — modal / role="dialog"
// ---------------------------------------------------------------------------

describe('keyboard-nav — focus trap', () => {
    const dialogHtml = `
      <div class="dj-modal" role="dialog" aria-modal="true">
        <button id="first">First</button>
        <input id="mid" type="text">
        <button id="last">Last</button>
      </div>`;

    it('forward Tab on the last focusable element wraps to the first', () => {
        const dom = createDom(dialogHtml);
        const last = dom.window.document.getElementById('last');
        const first = dom.window.document.getElementById('first');
        last.focus();
        const ev = press(dom, last, 'Tab');
        expect(ev.defaultPrevented).toBe(true);
        expect(dom.window.document.activeElement).toBe(first);
    });

    it('Shift+Tab on the first focusable element wraps to the last', () => {
        const dom = createDom(dialogHtml);
        const first = dom.window.document.getElementById('first');
        const last = dom.window.document.getElementById('last');
        first.focus();
        const ev = press(dom, first, 'Tab', { shiftKey: true });
        expect(ev.defaultPrevented).toBe(true);
        expect(dom.window.document.activeElement).toBe(last);
    });

    it('Tab in the middle does not wrap (lets the browser advance)', () => {
        const dom = createDom(dialogHtml);
        const mid = dom.window.document.getElementById('mid');
        mid.focus();
        const ev = press(dom, mid, 'Tab');
        // Not at an edge — the trap should not intervene.
        expect(ev.defaultPrevented).toBe(false);
    });

    it('no-focusable-children dialog traps focus on the container (Tab is a no-op)', () => {
        const dom = createDom(
            '<div class="dj-modal" role="dialog" id="empty">just text</div>'
        );
        const empty = dom.window.document.getElementById('empty');
        // Container should be made programmatically focusable.
        expect(empty.getAttribute('tabindex')).toBe('-1');
        empty.focus();
        const ev = press(dom, empty, 'Tab');
        expect(ev.defaultPrevented).toBe(true);
        expect(dom.window.document.activeElement).toBe(empty);
    });

    it('moves focus into a dialog when it is added to the DOM (morph)', async () => {
        const dom = createDom('<div id="host"><button id="outside">Outside</button></div>');
        const outside = dom.window.document.getElementById('outside');
        outside.focus();
        const host = dom.window.document.getElementById('host');
        host.insertAdjacentHTML(
            'beforeend',
            '<div class="dj-modal" role="dialog"><button id="inner">Inner</button></div>'
        );
        await waitForMutation(dom);
        expect(dom.window.document.activeElement)
            .toBe(dom.window.document.getElementById('inner'));
    });

    it('restores focus to the pre-open element when the dialog is removed', async () => {
        const dom = createDom('<div id="host"><button id="opener">Opener</button></div>');
        const opener = dom.window.document.getElementById('opener');
        opener.focus();
        const host = dom.window.document.getElementById('host');
        host.insertAdjacentHTML(
            'beforeend',
            '<div class="dj-modal" role="dialog" id="dlg"><button id="inner">Inner</button></div>'
        );
        await waitForMutation(dom);
        expect(dom.window.document.activeElement)
            .toBe(dom.window.document.getElementById('inner'));
        // Close the dialog by removing it.
        dom.window.document.getElementById('dlg').remove();
        await waitForMutation(dom);
        expect(dom.window.document.activeElement).toBe(opener);
    });

    it('falls back to document.body when the return target was removed while open (#1532)', async () => {
        const dom = createDom('<div id="host"><button id="opener">Opener</button></div>');
        const opener = dom.window.document.getElementById('opener');
        opener.focus();
        const host = dom.window.document.getElementById('host');
        host.insertAdjacentHTML(
            'beforeend',
            '<div class="dj-modal" role="dialog" id="dlg"><button id="inner">Inner</button></div>'
        );
        await waitForMutation(dom);
        // The opener's region is replaced (e.g. a morphdom patch) while the
        // dialog is open — the recorded return target is now detached. The
        // close handler must not call .focus() on the detached node and
        // must leave keyboard focus somewhere reachable.
        opener.remove();
        dom.window.document.getElementById('dlg').remove();
        await waitForMutation(dom);
        const active = dom.window.document.activeElement;
        expect(active === dom.window.document.body || active === null).toBe(true);
        expect(active).not.toBe(opener);
    });

    it('nested dialogs — Esc pops only the top dialog; trap acts on the top', async () => {
        const dom = createDom('<div id="host"></div>');
        installHandleEventSpy(dom);
        const host = dom.window.document.getElementById('host');
        host.insertAdjacentHTML(
            'beforeend',
            '<div class="dj-modal" role="dialog" id="d1">' +
            '<button class="dj-modal__close" dj-click="close_one">x</button></div>'
        );
        await waitForMutation(dom);
        host.insertAdjacentHTML(
            'beforeend',
            '<div class="dj-modal" role="dialog" id="d2">' +
            '<button class="dj-modal__close" dj-click="close_two">x</button>' +
            '<button id="d2btn">d2</button></div>'
        );
        await waitForMutation(dom);
        // Esc should close the TOP dialog (d2) only.
        const d2btn = dom.window.document.getElementById('d2btn');
        d2btn.focus();
        press(dom, d2btn, 'Escape');
        const calls = dom.window._handleEventCalls;
        expect(calls.length).toBe(1);
        expect(calls[0].name).toBe('close_two');
    });
});

// ---------------------------------------------------------------------------
// Esc-to-close
// ---------------------------------------------------------------------------

describe('keyboard-nav — Esc-to-close', () => {
    it('Esc inside a modal dispatches the close event from dj-click', () => {
        const dom = createDom(`
          <div class="dj-modal" role="dialog">
            <button class="dj-modal__close" dj-click="close_modal">x</button>
            <button id="body">body</button>
          </div>`);
        installHandleEventSpy(dom);
        const body = dom.window.document.getElementById('body');
        body.focus();
        const ev = press(dom, body, 'Escape');
        expect(ev.defaultPrevented).toBe(true);
        const calls = dom.window._handleEventCalls;
        expect(calls.length).toBe(1);
        expect(calls[0].name).toBe('close_modal');
    });

    it('Esc inside a dropdown menu dispatches the toggle event and refocuses the trigger', () => {
        const dom = createDom(`
          <div class="dj-dropdown" data-open="true">
            <button class="dj-dropdown__trigger" id="trig"
                    aria-haspopup="menu" aria-controls="m-menu"
                    dj-click="toggle_dropdown">Menu</button>
            <div role="menu" id="m-menu">
              <button role="menuitem" id="i1">One</button>
              <button role="menuitem" id="i2">Two</button>
            </div>
          </div>`);
        installHandleEventSpy(dom);
        const i1 = dom.window.document.getElementById('i1');
        i1.focus();
        const ev = press(dom, i1, 'Escape');
        expect(ev.defaultPrevented).toBe(true);
        const calls = dom.window._handleEventCalls;
        expect(calls.length).toBe(1);
        expect(calls[0].name).toBe('toggle_dropdown');
        expect(dom.window.document.activeElement)
            .toBe(dom.window.document.getElementById('trig'));
    });
});

// ---------------------------------------------------------------------------
// Roving tabindex — tablist
// ---------------------------------------------------------------------------

describe('keyboard-nav — tablist roving', () => {
    const tablistHtml = `
      <nav class="dj-tabs__nav" role="tablist">
        <button class="dj-tab" role="tab" id="t0" aria-selected="true" tabindex="0">Tab 0</button>
        <button class="dj-tab" role="tab" id="t1" aria-selected="false" tabindex="-1">Tab 1</button>
        <button class="dj-tab" role="tab" id="t2" aria-selected="false" tabindex="-1">Tab 2</button>
      </nav>`;

    function tabs(dom) {
        return ['t0', 't1', 't2'].map((id) => dom.window.document.getElementById(id));
    }

    it('ArrowRight moves focus and tabindex="0" to the next tab', () => {
        const dom = createDom(tablistHtml);
        const [t0, t1] = tabs(dom);
        t0.focus();
        press(dom, t0, 'ArrowRight');
        expect(dom.window.document.activeElement).toBe(t1);
        expect(t1.getAttribute('tabindex')).toBe('0');
        expect(t0.getAttribute('tabindex')).toBe('-1');
    });

    it('ArrowLeft moves focus to the previous tab', () => {
        const dom = createDom(tablistHtml);
        const [t0, t1, t2] = tabs(dom);
        t2.focus();
        // sync DOM tabindex state to t2 first
        press(dom, t2, 'ArrowLeft');
        expect(dom.window.document.activeElement).toBe(t1);
        press(dom, t1, 'ArrowLeft');
        expect(dom.window.document.activeElement).toBe(t0);
    });

    it('ArrowRight on the last tab wraps to the first (index len-1 -> 0)', () => {
        const dom = createDom(tablistHtml);
        const [t0, , t2] = tabs(dom);
        t2.focus();
        press(dom, t2, 'ArrowRight');
        expect(dom.window.document.activeElement).toBe(t0);
    });

    it('ArrowLeft on the first tab wraps to the last (index 0 -> len-1)', () => {
        const dom = createDom(tablistHtml);
        const [t0, , t2] = tabs(dom);
        t0.focus();
        press(dom, t0, 'ArrowLeft');
        expect(dom.window.document.activeElement).toBe(t2);
    });

    it('Home jumps to the first tab, End to the last', () => {
        const dom = createDom(tablistHtml);
        const [t0, t1, t2] = tabs(dom);
        t1.focus();
        press(dom, t1, 'End');
        expect(dom.window.document.activeElement).toBe(t2);
        press(dom, t2, 'Home');
        expect(dom.window.document.activeElement).toBe(t0);
    });

    it('after navigation exactly one tab has tabindex="0"', () => {
        const dom = createDom(tablistHtml);
        const [t0, t1, t2] = tabs(dom);
        t0.focus();
        press(dom, t0, 'ArrowRight');
        press(dom, t1, 'ArrowRight');
        const tabbable = [t0, t1, t2].filter((t) => t.getAttribute('tabindex') === '0');
        expect(tabbable.length).toBe(1);
        expect(tabbable[0]).toBe(t2);
    });
});

// ---------------------------------------------------------------------------
// Roving — accordion (focus movement only, no tabindex juggling)
// ---------------------------------------------------------------------------

describe('keyboard-nav — accordion roving', () => {
    const accordionHtml = `
      <div class="dj-accordion">
        <button class="dj-accordion__trigger" id="a0" aria-expanded="false">Section 0</button>
        <button class="dj-accordion__trigger" id="a1" aria-expanded="false">Section 1</button>
        <button class="dj-accordion__trigger" id="a2" aria-expanded="false">Section 2</button>
      </div>`;

    function triggers(dom) {
        return ['a0', 'a1', 'a2'].map((id) => dom.window.document.getElementById(id));
    }

    it('ArrowDown moves focus to the next trigger', () => {
        const dom = createDom(accordionHtml);
        const [a0, a1] = triggers(dom);
        a0.focus();
        press(dom, a0, 'ArrowDown');
        expect(dom.window.document.activeElement).toBe(a1);
    });

    it('ArrowUp moves focus to the previous trigger', () => {
        const dom = createDom(accordionHtml);
        const [a0, a1] = triggers(dom);
        a1.focus();
        press(dom, a1, 'ArrowUp');
        expect(dom.window.document.activeElement).toBe(a0);
    });

    it('ArrowDown on the last trigger wraps to the first', () => {
        const dom = createDom(accordionHtml);
        const [a0, , a2] = triggers(dom);
        a2.focus();
        press(dom, a2, 'ArrowDown');
        expect(dom.window.document.activeElement).toBe(a0);
    });

    it('ArrowUp on the first trigger wraps to the last', () => {
        const dom = createDom(accordionHtml);
        const [a0, , a2] = triggers(dom);
        a0.focus();
        press(dom, a0, 'ArrowUp');
        expect(dom.window.document.activeElement).toBe(a2);
    });

    it('accordion triggers keep their native tab order (no tabindex="-1" added)', () => {
        const dom = createDom(accordionHtml);
        const [a0, a1, a2] = triggers(dom);
        a0.focus();
        press(dom, a0, 'ArrowDown');
        press(dom, a1, 'ArrowDown');
        // None of the triggers should have been pushed out of the tab order.
        for (const t of [a0, a1, a2]) {
            expect(t.getAttribute('tabindex')).toBe(null);
        }
    });
});

// ---------------------------------------------------------------------------
// Roving — dropdown menu
// ---------------------------------------------------------------------------

describe('keyboard-nav — dropdown menu roving', () => {
    const dropdownHtml = `
      <div class="dj-dropdown" data-open="true">
        <button class="dj-dropdown__trigger" id="dt" aria-haspopup="menu"
                aria-controls="dd-menu" dj-click="toggle_dropdown">Menu</button>
        <div role="menu" id="dd-menu">
          <button role="menuitem" id="m0">Item 0</button>
          <button role="menuitem" id="m1">Item 1</button>
          <button role="menuitem" id="m2">Item 2</button>
        </div>
      </div>`;

    function items(dom) {
        return ['m0', 'm1', 'm2'].map((id) => dom.window.document.getElementById(id));
    }

    it('first ArrowDown after open focuses the first menu item', () => {
        const dom = createDom(dropdownHtml);
        const trig = dom.window.document.getElementById('dt');
        const [m0] = items(dom);
        trig.focus();
        press(dom, trig, 'ArrowDown');
        expect(dom.window.document.activeElement).toBe(m0);
    });

    it('ArrowDown moves focus to the next item', () => {
        const dom = createDom(dropdownHtml);
        const [m0, m1] = items(dom);
        m0.focus();
        press(dom, m0, 'ArrowDown');
        expect(dom.window.document.activeElement).toBe(m1);
    });

    it('ArrowUp moves focus to the previous item', () => {
        const dom = createDom(dropdownHtml);
        const [m0, m1] = items(dom);
        m1.focus();
        press(dom, m1, 'ArrowUp');
        expect(dom.window.document.activeElement).toBe(m0);
    });

    it('ArrowDown on the last item wraps to the first', () => {
        const dom = createDom(dropdownHtml);
        const [m0, , m2] = items(dom);
        m2.focus();
        press(dom, m2, 'ArrowDown');
        expect(dom.window.document.activeElement).toBe(m0);
    });

    it('ArrowUp on the first item wraps to the last', () => {
        const dom = createDom(dropdownHtml);
        const [m0, , m2] = items(dom);
        m0.focus();
        press(dom, m0, 'ArrowUp');
        expect(dom.window.document.activeElement).toBe(m2);
    });

    it('Home jumps to the first item, End to the last', () => {
        const dom = createDom(dropdownHtml);
        const [m0, m1, m2] = items(dom);
        m1.focus();
        press(dom, m1, 'End');
        expect(dom.window.document.activeElement).toBe(m2);
        press(dom, m2, 'Home');
        expect(dom.window.document.activeElement).toBe(m0);
    });
});
