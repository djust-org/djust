/**
 * `{% tabs %}` keyboard behaviour on the markup the server renders
 * (tests/js/fixtures/tabs_server_markup.html, pinned to the server's output by
 * python/djust/components/tests/test_tabs_keyboard_and_label.py).
 *
 * WAI-ARIA tabs pattern, manual activation:
 *  - one Tab stop, the selected tab (server-rendered roving tabindex);
 *  - Left/Right (and Up/Down) move between tabs, wrapping; Home/End jump;
 *  - when focus leaves the tab list, the Tab stop returns to the selected tab,
 *    so Tab back into the list lands on it again;
 *  - activation stays with the buttons (Enter/Space click a <button>).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const markup = fs.readFileSync('./tests/js/fixtures/tabs_server_markup.html', 'utf-8');

function createDom() {
    const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body>
  <div dj-view="test.views.TestView" dj-root>
    <button id="before">before</button>${markup}<button id="after">after</button>
  </div></body></html>`, { runScripts: 'dangerously', url: 'http://localhost/' });
    class MockWebSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor() { this.readyState = MockWebSocket.OPEN; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.DJUST_USE_WEBSOCKET = false;
    dom.window.eval(clientCode);
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function press(dom, el, key) {
    el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

function tabs(dom) {
    return ['mine', 'team', 'all'].map((id) => dom.window.document.getElementById(`who-tab-${id}`));
}

function stops(dom) {
    return tabs(dom).filter((t) => t.getAttribute('tabindex') === '0').map((t) => t.id);
}

describe('{% tabs %} keyboard on the server markup', () => {
    it('renders one Tab stop: the selected tab', () => {
        const dom = createDom();
        expect(stops(dom)).toEqual(['who-tab-team']);
        const list = dom.window.document.querySelector('[role="tablist"]');
        expect(list.getAttribute('aria-label')).toBe('Whose activity');
    });

    it('Right/Left move focus between tabs and wrap', () => {
        const dom = createDom();
        const [mine, team, all] = tabs(dom);
        team.focus();
        press(dom, team, 'ArrowRight');
        expect(dom.window.document.activeElement).toBe(all);
        press(dom, all, 'ArrowRight');
        expect(dom.window.document.activeElement).toBe(mine);
        press(dom, mine, 'ArrowLeft');
        expect(dom.window.document.activeElement).toBe(all);
        expect(stops(dom)).toEqual(['who-tab-all']);
    });

    it('Home and End jump to the first and last tab', () => {
        const dom = createDom();
        const [mine, team, all] = tabs(dom);
        team.focus();
        press(dom, team, 'End');
        expect(dom.window.document.activeElement).toBe(all);
        press(dom, all, 'Home');
        expect(dom.window.document.activeElement).toBe(mine);
    });

    it('arrow keys move focus only: the selected tab does not change (manual activation)', () => {
        const dom = createDom();
        const [, team, all] = tabs(dom);
        team.focus();
        press(dom, team, 'ArrowRight');
        expect(team.getAttribute('aria-selected')).toBe('true');
        expect(all.getAttribute('aria-selected')).toBe('false');
    });

    it('leaving the tab list puts the Tab stop back on the selected tab', () => {
        const dom = createDom();
        const [mine, team] = tabs(dom);
        team.focus();
        press(dom, team, 'ArrowLeft');
        expect(stops(dom)).toEqual(['who-tab-mine']);
        // Tab out of the list.
        dom.window.document.getElementById('after').focus();
        expect(stops(dom)).toEqual(['who-tab-team']);
        expect(mine.getAttribute('tabindex')).toBe('-1');
    });

    it('moving focus within the tab list keeps the roved stop', () => {
        const dom = createDom();
        const [mine, team] = tabs(dom);
        team.focus();
        press(dom, team, 'ArrowLeft');
        mine.focus();
        expect(stops(dom)).toEqual(['who-tab-mine']);
    });
});
