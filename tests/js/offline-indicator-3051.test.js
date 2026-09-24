/**
 * #3051: {% djust_offline_indicator %} and djust/pwa/offline_banner.html.
 *
 * The show_when="offline" indicator and the banner carried an inline
 * `display: none` nothing removed, and no client code read the indicator's
 * data-online-text / data-offline-text / data-online-class /
 * data-offline-class. Now visibility is the dj-offline-show CSS on the body
 * class 52-offline-state.js sets (#3041), and the same module swaps the
 * indicator's text and status class.
 *
 * The markup and CSS below mirror what the tags render
 * (templatetags/djust_pwa.py; pinned by
 * tests/unit/test_pwa_offline_indicator_3051.py).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');

// {% djust_offline_styles %} directive rules, plus the indicator tag's own
// offline-only rule.
const CSS = `
body.djust-offline [dj-offline-hide],
body:not(.djust-online) [dj-offline-hide] { display: none !important; }
body.djust-online [dj-offline-show],
body:not(.djust-offline) [dj-offline-show] { display: none !important; }
.djust-offline-indicator { display: inline-flex; }
body:not(.djust-offline) .djust-offline-indicator[dj-offline-show] { display: none !important; }
`;

function indicator(id, mode, initial) {
    const vis = mode === 'offline' ? ' dj-offline-show' : mode === 'online' ? ' dj-offline-hide' : '';
    return `<div id="${id}" class="djust-offline-indicator ${initial.cls}"${vis}
     data-online-text="Up" data-offline-text="Down"
     data-online-class="is-up" data-offline-class="is-down extra">
    <span class="djust-indicator-dot"></span>
    <span class="djust-indicator-text">${initial.text}</span>
</div>`;
}

const UP = { text: 'Up', cls: 'is-up' };
const DOWN = { text: 'Down', cls: 'is-down extra' };

function createDom({ online = true, withClient = true } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head><style>${CSS}</style></head><body>
            <div dj-root dj-view="app.View">
                ${indicator('ind-offline', 'offline', DOWN)}
                ${indicator('ind-online', 'online', UP)}
                ${indicator('ind-always', 'always', UP)}
                <div id="banner" class="djust-offline-banner" dj-offline-show dj-offline="show">
                    <p>No network</p>
                </div>
            </div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    Object.defineProperty(dom.window.navigator, 'onLine', { configurable: true, get: () => online });
    class MockWebSocket {
        constructor() { this.readyState = 1; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.console = { log: () => {}, error: () => {}, warn: () => {}, debug: () => {}, info: () => {} };
    if (withClient) {
        try { dom.window.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    }
    return dom;
}

const el = (dom, id) => dom.window.document.getElementById(id);
const shown = (dom, id) => dom.window.getComputedStyle(el(dom, id)).display !== 'none';
const text = (dom, id) => el(dom, id).querySelector('.djust-indicator-text').textContent;
const has = (dom, id, cls) => el(dom, id).classList.contains(cls);

function expectOnline(dom) {
    expect(shown(dom, 'ind-offline')).toBe(false);
    expect(shown(dom, 'banner')).toBe(false);
    expect(shown(dom, 'ind-online')).toBe(true);
    expect(shown(dom, 'ind-always')).toBe(true);
    for (const id of ['ind-offline', 'ind-online', 'ind-always']) {
        expect(text(dom, id)).toBe('Up');
        expect(has(dom, id, 'is-up')).toBe(true);
        expect(has(dom, id, 'is-down')).toBe(false);
        expect(has(dom, id, 'extra')).toBe(false);
        expect(has(dom, id, 'djust-offline-indicator')).toBe(true);
    }
}

function expectOffline(dom) {
    expect(shown(dom, 'ind-offline')).toBe(true);
    expect(shown(dom, 'banner')).toBe(true);
    expect(shown(dom, 'ind-online')).toBe(false);
    expect(shown(dom, 'ind-always')).toBe(true);
    for (const id of ['ind-offline', 'ind-online', 'ind-always']) {
        expect(text(dom, id)).toBe('Down');
        expect(has(dom, id, 'is-down')).toBe(true);
        expect(has(dom, id, 'extra')).toBe(true);
        expect(has(dom, id, 'is-up')).toBe(false);
        expect(has(dom, id, 'djust-offline-indicator')).toBe(true);
    }
}

describe('offline indicator and banner (#3051)', () => {
    it('keeps the offline-only indicator and banner hidden before the client runs', () => {
        const dom = createDom({ withClient: false });
        expect(shown(dom, 'ind-offline')).toBe(false);
        expect(shown(dom, 'banner')).toBe(false);
    });

    it('online at startup: online text and class, offline-only pieces hidden', () => {
        expectOnline(createDom({ online: true }));
    });

    it('offline at startup: offline text and class, indicator and banner shown', () => {
        expectOffline(createDom({ online: false }));
    });

    it('follows the offline and online events', () => {
        const dom = createDom({ online: true });
        const { window } = dom;
        Object.defineProperty(window.navigator, 'onLine', { configurable: true, get: () => false });
        window.dispatchEvent(new window.Event('offline'));
        expectOffline(dom);
        Object.defineProperty(window.navigator, 'onLine', { configurable: true, get: () => true });
        window.dispatchEvent(new window.Event('online'));
        expectOnline(dom);
        window.dispatchEvent(new window.Event('offline'));
        expectOffline(dom);
    });

    it('keeps a class named in both lists', () => {
        const dom = createDom({ online: true });
        const { window } = dom;
        const ind = el(dom, 'ind-always');
        ind.setAttribute('data-online-class', 'shared up');
        ind.setAttribute('data-offline-class', 'shared down');
        window.dispatchEvent(new window.Event('offline'));
        expect(ind.classList.contains('shared')).toBe(true);
        expect(ind.classList.contains('down')).toBe(true);
        window.dispatchEvent(new window.Event('online'));
        expect(ind.classList.contains('shared')).toBe(true);
        expect(ind.classList.contains('up')).toBe(true);
        expect(ind.classList.contains('down')).toBe(false);
    });

    it('syncs an indicator a DOM update inserts', () => {
        const dom = createDom({ online: true });
        const { window } = dom;
        window.dispatchEvent(new window.Event('offline'));
        const wrap = window.document.createElement('div');
        wrap.innerHTML = indicator('ind-new', 'always', UP);
        window.document.querySelector('[dj-root]').appendChild(wrap);
        window.djust.reinitAfterDOMUpdate(wrap);
        expect(text(dom, 'ind-new')).toBe('Down');
        expect(has(dom, 'ind-new', 'is-down')).toBe(true);
        expect(has(dom, 'ind-new', 'is-up')).toBe(false);
    });
});
