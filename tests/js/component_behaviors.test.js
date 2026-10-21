/**
 * Component behaviours that replaced inline event handlers
 * (src/53-component-behaviors.js).
 *
 * rich_select, combobox and theme_toggle used to open, close, filter or switch
 * the colour mode through onclick=/onkeydown=/oninput= attributes, which a
 * Content-Security-Policy without 'unsafe-inline' refuses. The markup now
 * carries none; the client bundle's delegated listeners on `document` do the
 * work. The markup below is what the component tags render.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const themeJs = fs.readFileSync('./python/djust/theming/static/djust_theming/js/theme.js', 'utf-8');

const RICH_SELECT = `
<div class="rich-select" id="rs-pick">
  <input type="hidden" name="pick" value="">
  <div class="rich-select-trigger" tabindex="0" role="combobox" aria-controls="rs-pick-listbox"
       aria-expanded="false" aria-haspopup="listbox"><span class="rich-select-placeholder">Select...</span></div>
  <div class="rich-select-dropdown" role="listbox" id="rs-pick-listbox">
    <div class="rich-select-search"><input type="text" class="rich-select-search-input" aria-label="Search options"></div>
    <div class="rich-select-option" data-value="a" role="option" aria-selected="false">Alpha</div>
    <div class="rich-select-option" data-value="b" role="option" aria-selected="false">Beta</div>
  </div>
</div>
<div class="rich-select rich-select--disabled" id="rs-off">
  <div class="rich-select-trigger" tabindex="-1" role="combobox" aria-expanded="false" aria-disabled="true">x</div>
</div>
<p id="elsewhere">elsewhere</p>`;

const COMBOBOX = `
<div class="combobox" id="city-combobox">
  <input class="combobox-input form-input" type="text" id="city-input" name="city" role="combobox"
         aria-autocomplete="list" aria-expanded="false" aria-controls="city-listbox" autocomplete="off">
  <div class="combobox-dropdown" id="city-listbox" role="listbox">
    <div class="combobox-option" role="option" aria-selected="false" data-value="a">Alpha</div>
  </div>
</div>`;

const THEME_TOGGLE = `
<div class="dj-theme-toggle" data-current="system" role="radiogroup" aria-label="Color theme">
  <button type="button" class="dj-theme-toggle__btn" role="radio" data-theme-mode="light" aria-checked="false">L</button>
  <button type="button" class="dj-theme-toggle__btn" role="radio" data-theme-mode="dark" aria-checked="false">D</button>
  <button type="button" class="dj-theme-toggle__btn dj-theme-toggle__btn--active" role="radio" data-theme-mode="system" aria-checked="true">S</button>
</div>`;

function createDom(bodyHtml, { withThemeJs = false, storedMode = null } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body>
            <div dj-root dj-view="app.View">${bodyHtml}</div>
        </body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true }
    );
    if (storedMode) dom.window.localStorage.setItem('djust-theme-mode', storedMode);
    dom.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
    class MockWebSocket {
        constructor() { this.readyState = 1; }
        send() {}
        close() {}
    }
    dom.window.WebSocket = MockWebSocket;
    dom.window.console = { log() {}, error() {}, warn() {}, debug() {}, info() {} };
    if (withThemeJs) dom.window.eval(themeJs);
    try { dom.window.eval(clientCode); } catch (_) { /* ignore missing DOM APIs */ }
    // jsdom stays at readyState 'loading' after an eval; a browser fires this.
    dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    return dom;
}

function click(dom, el) {
    el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

function key(dom, el, k) {
    const ev = new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev;
}

describe('rich_select', () => {
    it('opens and closes on a trigger click, mirroring aria-expanded', () => {
        const dom = createDom(RICH_SELECT);
        const doc = dom.window.document;
        const root = doc.getElementById('rs-pick');
        const trigger = root.querySelector('.rich-select-trigger');
        click(dom, trigger);
        expect(root.classList.contains('rich-select--open')).toBe(true);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        click(dom, trigger);
        expect(root.classList.contains('rich-select--open')).toBe(false);
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
    });

    it('toggles on Enter and Space, closes on Escape', () => {
        const dom = createDom(RICH_SELECT);
        const root = dom.window.document.getElementById('rs-pick');
        const trigger = root.querySelector('.rich-select-trigger');
        const enter = key(dom, trigger, 'Enter');
        expect(enter.defaultPrevented).toBe(true);
        expect(root.classList.contains('rich-select--open')).toBe(true);
        key(dom, trigger, 'Escape');
        expect(root.classList.contains('rich-select--open')).toBe(false);
        key(dom, trigger, ' ');
        expect(root.classList.contains('rich-select--open')).toBe(true);
    });

    it('closes on an option pick and on a click elsewhere', () => {
        const dom = createDom(RICH_SELECT);
        const doc = dom.window.document;
        const root = doc.getElementById('rs-pick');
        const trigger = root.querySelector('.rich-select-trigger');
        click(dom, trigger);
        click(dom, root.querySelector('[data-value="b"]'));
        expect(root.classList.contains('rich-select--open')).toBe(false);
        click(dom, trigger);
        click(dom, doc.getElementById('elsewhere'));
        expect(root.classList.contains('rich-select--open')).toBe(false);
    });

    it('stays open while typing in its search box, which filters options', () => {
        const dom = createDom(RICH_SELECT);
        const root = dom.window.document.getElementById('rs-pick');
        click(dom, root.querySelector('.rich-select-trigger'));
        const search = root.querySelector('.rich-select-search-input');
        click(dom, search);
        expect(root.classList.contains('rich-select--open')).toBe(true);
        search.value = 'bet';
        search.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        expect(root.querySelector('[data-value="a"]').style.display).toBe('none');
        expect(root.querySelector('[data-value="b"]').style.display).toBe('');
    });

    it('a disabled picker does not open', () => {
        const dom = createDom(RICH_SELECT);
        const off = dom.window.document.getElementById('rs-off');
        click(dom, off.querySelector('.rich-select-trigger'));
        key(dom, off.querySelector('.rich-select-trigger'), 'Enter');
        expect(off.classList.contains('rich-select--open')).toBe(false);
    });
});

describe('combobox', () => {
    it('keeps focus in the input while an option is pressed', () => {
        const dom = createDom(COMBOBOX);
        const option = dom.window.document.querySelector('.combobox-option');
        const down = new dom.window.MouseEvent('mousedown', { bubbles: true, cancelable: true });
        option.dispatchEvent(down);
        expect(down.defaultPrevented).toBe(true);
    });

    it('reflects the open list in aria-expanded and closes after the pick', () => {
        const dom = createDom(COMBOBOX);
        const doc = dom.window.document;
        const input = doc.getElementById('city-input');
        input.focus();
        expect(doc.activeElement).toBe(input);
        expect(input.getAttribute('aria-expanded')).toBe('true');
        click(dom, doc.querySelector('.combobox-option'));
        expect(doc.activeElement).not.toBe(input);
        expect(input.getAttribute('aria-expanded')).toBe('false');
    });
});

describe('theme_toggle', () => {
    it('switches the mode without theme.js and stores it under djust.theming\'s key', () => {
        const dom = createDom(THEME_TOGGLE);
        const doc = dom.window.document;
        click(dom, doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]'));
        expect(doc.documentElement.getAttribute('data-theme')).toBe('dark');
        expect(doc.documentElement.getAttribute('data-theme-mode')).toBe('dark');
        expect(dom.window.localStorage.getItem('djust-theme-mode')).toBe('dark');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]').getAttribute('aria-checked')).toBe('true');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="system"]').getAttribute('aria-checked')).toBe('false');
        expect(doc.querySelector('.dj-theme-toggle').getAttribute('data-current')).toBe('dark');
    });

    it('shows the stored mode at load', () => {
        const dom = createDom(THEME_TOGGLE, { storedMode: 'light' });
        const doc = dom.window.document;
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="light"]').getAttribute('aria-checked')).toBe('true');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="system"]').getAttribute('aria-checked')).toBe('false');
    });

    it('with theme.js on the page (theme_head), theme.js owns the switch', async () => {
        const dom = createDom(THEME_TOGGLE, { withThemeJs: true });
        const win = dom.window;
        const doc = win.document;
        expect(win.djustTheme).toBeTruthy();
        let setModeCalls = 0;
        const realSetMode = win.djustTheme.setMode.bind(win.djustTheme);
        win.djustTheme.setMode = (mode) => { setModeCalls += 1; realSetMode(mode); };
        click(dom, doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]'));
        expect(setModeCalls).toBe(1);
        expect(win.localStorage.getItem('djust-theme-mode')).toBe('dark');
        expect(doc.querySelector('.dj-theme-toggle [data-theme-mode="dark"]').getAttribute('aria-checked')).toBe('true');
    });

    it('theme.js shows a mode set elsewhere (server push) on the toggle', async () => {
        const dom = createDom(THEME_TOGGLE, { withThemeJs: true });
        const win = dom.window;
        // Let the page finish loading first: jsdom fires its own
        // DOMContentLoaded asynchronously, and the load-time sync of the
        // stored mode must not be what this test observes.
        if (win.document.readyState !== 'complete') {
            await new Promise((r) => win.addEventListener('load', r));
        }
        await new Promise((r) => setTimeout(r, 30));
        const light = win.document.querySelector('.dj-theme-toggle [data-theme-mode="light"]');
        expect(light.getAttribute('aria-checked')).toBe('false');
        win.djustTheme.setMode('light');
        // theme.js updates its UI after a 16 ms debounce and a frame; wait
        // for the state rather than a fixed delay, which a loaded machine
        // can outrun.
        for (let i = 0; i < 100 && light.getAttribute('aria-checked') !== 'true'; i++) {
            await new Promise((r) => setTimeout(r, 20));
        }
        expect(light.getAttribute('aria-checked')).toBe('true');
        expect(win.document.querySelector('.dj-theme-toggle [data-theme-mode="system"]').getAttribute('aria-checked')).toBe('false');
    });
});

// ---------------------------------------------------------------------------
// The remaining components that used inline handlers or inline scripts.
// ---------------------------------------------------------------------------

function fire(dom, el, type, props = {}) {
    const ev = new dom.window.MouseEvent(type, { bubbles: true, cancelable: true, ...props });
    for (const [k, v] of Object.entries(props.extra || {})) {
        Object.defineProperty(ev, k, { value: v });
    }
    el.dispatchEvent(ev);
    return ev;
}

function fakeTransfer(files = []) {
    const store = {};
    return {
        files,
        setData(k, v) { store[k] = v; },
        getData(k) { return store[k] || ''; },
    };
}

const POPUPS = `
<div class="popover-wrapper" id="p1">
  <button type="button" class="popover-trigger btn" aria-expanded="false" aria-haspopup="dialog">More</button>
  <div class="popover popover-bottom" role="dialog"><div class="popover-content"><a id="inside">in</a></div></div>
</div>
<div class="dj-popconfirm-wrapper" id="pc">
  <div class="dj-popconfirm-trigger" aria-expanded="false"><button id="del">Delete</button></div>
  <div class="dj-popconfirm" role="alertdialog"><div class="dj-popconfirm__actions">
    <button type="button" class="dj-popconfirm__btn dj-popconfirm__btn--cancel" id="no">No</button>
  </div></div>
</div>
<div class="collapsible" id="coll"><button type="button" class="collapsible-trigger" aria-expanded="false">D</button>
  <div class="collapsible-content">x</div></div>
<div class="ctx-wrapper" id="ctx"><div class="ctx-trigger">Right-click</div><div class="ctx-menu" role="menu">m</div></div>
<div class="code-block"><div class="code-block-header"><button type="button" class="code-block-copy">Copy</button></div>
  <pre class="code-block-pre"><code class="language-python">x = 1</code></pre></div>
<p id="outside">outside</p>`;

describe('popover and popconfirm', () => {
    it('the popover trigger toggles data-open and aria-expanded; a click outside closes', () => {
        const dom = createDom(POPUPS);
        const doc = dom.window.document;
        const wrapper = doc.getElementById('p1');
        const trigger = wrapper.querySelector('.popover-trigger');
        click(dom, trigger);
        expect(wrapper.hasAttribute('data-open')).toBe(true);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        click(dom, doc.getElementById('inside'));
        expect(wrapper.hasAttribute('data-open')).toBe(true);
        click(dom, doc.getElementById('outside'));
        expect(wrapper.hasAttribute('data-open')).toBe(false);
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
    });

    it('Escape closes an open popover', () => {
        const dom = createDom(POPUPS);
        const wrapper = dom.window.document.getElementById('p1');
        click(dom, wrapper.querySelector('.popover-trigger'));
        key(dom, wrapper, 'Escape');
        expect(wrapper.hasAttribute('data-open')).toBe(false);
    });

    it('the popconfirm opens from its trigger content and closes on a button', () => {
        const dom = createDom(POPUPS);
        const doc = dom.window.document;
        const wrapper = doc.getElementById('pc');
        click(dom, doc.getElementById('del'));
        expect(wrapper.hasAttribute('data-open')).toBe(true);
        click(dom, doc.getElementById('no'));
        expect(wrapper.hasAttribute('data-open')).toBe(false);
    });
});

describe('collapsible, context menu, code block copy', () => {
    it('the collapsible trigger toggles the open class and aria-expanded', () => {
        const dom = createDom(POPUPS);
        const box = dom.window.document.getElementById('coll');
        const trigger = box.querySelector('.collapsible-trigger');
        click(dom, trigger);
        expect(box.classList.contains('collapsible-open')).toBe(true);
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        click(dom, trigger);
        expect(box.classList.contains('collapsible-open')).toBe(false);
    });

    it('right-click opens the context menu at the pointer; the next click closes it', () => {
        const dom = createDom(POPUPS);
        const doc = dom.window.document;
        const wrapper = doc.getElementById('ctx');
        const ev = fire(dom, wrapper.querySelector('.ctx-trigger'), 'contextmenu', { clientX: 40, clientY: 12 });
        const menu = wrapper.querySelector('.ctx-menu');
        expect(ev.defaultPrevented).toBe(true);
        expect(menu.getAttribute('data-open')).toBe('1');
        expect(menu.style.left).toBe('40px');
        expect(menu.style.top).toBe('12px');
        click(dom, doc.getElementById('outside'));
        expect(menu.hasAttribute('data-open')).toBe(false);
    });

    it('the copy button copies its block\'s code', async () => {
        const dom = createDom(POPUPS);
        const copied = [];
        Object.defineProperty(dom.window.navigator, 'clipboard', {
            configurable: true,
            value: { writeText: (t) => { copied.push(t); return Promise.resolve(); } },
        });
        const btn = dom.window.document.querySelector('.code-block-copy');
        click(dom, btn);
        await Promise.resolve();
        expect(copied).toEqual(['x = 1']);
        expect(btn.textContent).toBe('Copied!');
    });
});

const DRAG = `
<div class="dropzone" id="dz"><input type="file" name="f" class="dropzone-input" multiple>
  <div class="dz-text">Drag</div><div class="dz-file-count" aria-live="polite"></div></div>
<div class="kanban" data-move-event="moved">
  <div class="kanban-col" data-col-id="todo"><div class="kanban-cards">
    <div class="kanban-card" draggable="true" data-card-id="c1" data-col-id="todo"><div class="kanban-card-title">One</div></div>
  </div></div>
  <div class="kanban-col" data-col-id="done"><div class="kanban-cards"></div></div>
</div>`;

describe('dropzone and kanban', () => {
    it('a file drop shows the count and the drag-over class comes and goes', () => {
        const dom = createDom(DRAG);
        const zone = dom.window.document.getElementById('dz');
        const over = fire(dom, zone, 'dragover');
        expect(over.defaultPrevented).toBe(true);
        expect(zone.classList.contains('dropzone-over')).toBe(true);
        const drop = fire(dom, zone.querySelector('.dz-text'), 'drop',
            { extra: { dataTransfer: fakeTransfer([{ name: 'a' }, { name: 'b' }]) } });
        expect(drop.defaultPrevented).toBe(true);
        expect(zone.classList.contains('dropzone-over')).toBe(false);
        expect(zone.querySelector('.dz-file-count').textContent).toBe('2 files selected');
        expect(zone.classList.contains('dropzone-has-file')).toBe(true);
    });

    it('choosing files with the input shows the count', () => {
        const dom = createDom(DRAG);
        const zone = dom.window.document.getElementById('dz');
        const input = zone.querySelector('input');
        Object.defineProperty(input, 'files', { configurable: true, value: [{ name: 'a' }] });
        input.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
        expect(zone.querySelector('.dz-file-count').textContent).toBe('1 file selected');
    });

    it('dropping a card on another column sends the move event', () => {
        const dom = createDom(DRAG);
        const doc = dom.window.document;
        const sent = [];
        dom.window.djust.handleEvent = (name, params) => { sent.push([name, params]); };
        const transfer = fakeTransfer();
        const card = doc.querySelector('.kanban-card');
        fire(dom, card, 'dragstart', { extra: { dataTransfer: transfer } });
        expect(card.classList.contains('dragging')).toBe(true);
        const done = doc.querySelector('[data-col-id="done"].kanban-col');
        const over = fire(dom, done, 'dragover');
        expect(over.defaultPrevented).toBe(true);
        fire(dom, done, 'drop', { extra: { dataTransfer: transfer } });
        fire(dom, card, 'dragend');
        expect(sent).toEqual([['moved', { card_id: 'c1', from_col: 'todo', to_col: 'done' }]]);
        expect(card.classList.contains('dragging')).toBe(false);
        expect(done.classList.contains('kanban-col-over')).toBe(false);
    });

    it('dropping a card on its own column sends nothing', () => {
        const dom = createDom(DRAG);
        const doc = dom.window.document;
        const sent = [];
        dom.window.djust.handleEvent = (name, params) => { sent.push([name, params]); };
        const transfer = fakeTransfer();
        fire(dom, doc.querySelector('.kanban-card'), 'dragstart', { extra: { dataTransfer: transfer } });
        fire(dom, doc.querySelector('[data-col-id="todo"].kanban-col'), 'drop', { extra: { dataTransfer: transfer } });
        expect(sent).toEqual([]);
    });
});

const EDITORS = `
<div class="rte" id="rte-body"><div class="rte-toolbar">
  <button class="rte-btn" type="button" data-command="formatBlock" data-command-arg="h2">H2</button></div>
  <div class="rte-editor" contenteditable="true" role="textbox"></div>
  <input type="hidden" name="body" value=""></div>
<div class="multi-select" id="ms-tags"><input type="text" class="multi-select-search">
  <div class="multi-select-options">
    <label class="multi-select-option"><input type="checkbox" value="a"> Alpha</label>
    <label class="multi-select-option"><input type="checkbox" value="b"> Beta</label>
  </div></div>
<div class="split-pane split-pane-horizontal"><div class="sp-pane sp-pane-1" style="width:50%">a</div>
  <div class="sp-handle sp-handle-horizontal" role="separator"></div><div class="sp-pane sp-pane-2">b</div></div>`;

describe('rich text editor, multi-select, split pane', () => {
    it('a toolbar button runs its command on mousedown and keeps the selection', () => {
        const dom = createDom(EDITORS);
        const doc = dom.window.document;
        const calls = [];
        doc.execCommand = (...args) => { calls.push(args); return true; };
        const down = fire(dom, doc.querySelector('.rte-btn'), 'mousedown');
        expect(down.defaultPrevented).toBe(true);
        expect(calls).toEqual([['formatBlock', false, 'h2']]);
    });

    it('the editor\'s HTML is mirrored into the hidden input', () => {
        const dom = createDom(EDITORS);
        const doc = dom.window.document;
        const editor = doc.querySelector('.rte-editor');
        editor.innerHTML = '<b>hi</b>';
        editor.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        expect(doc.querySelector('.rte input[type=hidden]').value).toBe('<b>hi</b>');
    });

    it('the multi-select search filters options', () => {
        const dom = createDom(EDITORS);
        const doc = dom.window.document;
        const search = doc.querySelector('.multi-select-search');
        search.value = 'bet';
        search.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
        const [alpha, beta] = doc.querySelectorAll('.multi-select-option');
        expect(alpha.style.display).toBe('none');
        expect(beta.style.display).toBe('');
    });

    it('dragging the split-pane handle resizes the first pane within 10%-90%', () => {
        const dom = createDom(EDITORS);
        const doc = dom.window.document;
        const pane = doc.querySelector('.split-pane');
        pane.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100 });
        fire(dom, doc.querySelector('.sp-handle'), 'mousedown');
        fire(dom, doc, 'mousemove', { clientX: 60 });
        expect(doc.querySelector('.sp-pane-1').style.width).toBe('30%');
        fire(dom, doc, 'mousemove', { clientX: 199 });
        expect(doc.querySelector('.sp-pane-1').style.width).toBe('90%');
        fire(dom, doc, 'mouseup');
        fire(dom, doc, 'mousemove', { clientX: 100 });
        expect(doc.querySelector('.sp-pane-1').style.width).toBe('90%');
    });
});

describe('tutorial bubble', () => {
    it('Skip and Close dispatch tour:hide without an inline handler', () => {
        const dom = createDom(`<div id="dj-tutorial-bubble" class="dj-tutorial-bubble" data-visible="true">
            <button type="button" class="dj-tutorial-bubble__skip">Skip</button>
            <button type="button" class="dj-tutorial-bubble__cancel">Close</button></div>`);
        const doc = dom.window.document;
        let hides = 0;
        doc.addEventListener('tour:hide', () => { hides += 1; });
        click(dom, doc.querySelector('.dj-tutorial-bubble__skip'));
        click(dom, doc.querySelector('.dj-tutorial-bubble__cancel'));
        expect(hides).toBe(2);
    });
});
