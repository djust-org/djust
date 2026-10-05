/**
 * #2985 — SortableList, SortableGrid, JsonViewer and LogViewer answer their
 * dj-hook: the interactions their docstrings describe, and an app's own hook
 * of the same name still wins wherever it is registered.
 *
 * The markup below is what the components render (see
 * python/djust/tests/test_component_interactions_2985.py, which pins it on
 * all three render paths).
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');

function createEnv(bodyHtml, { preRegister } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...args) => warnings.push(args.join(' ')),
    };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    if (preRegister) preRegister(window);
    return { window, warnings };
}

function boot(markup, scriptFile, opts) {
    const env = createEnv(markup, opts);
    env.window.eval(read(scriptFile));
    const sent = [];
    // Capture what the hook sends through the public client entry point.
    env.window.djust.handleEvent = (name, params) => { sent.push({ name, params }); };
    env.window.djust.mountHooks();
    env.sent = sent;
    env.$ = (sel) => env.window.document.querySelector(sel);
    env.$$ = (sel) => Array.from(env.window.document.querySelectorAll(sel));
    env.live = () => (env.window.document.getElementById('dj-component-live') || {}).textContent;
    return env;
}

const SORTABLE_LIST = (extra = '') =>
    '<ul class="dj-sortable-list" dj-hook="SortableList" data-move-event="reorder" role="list"' + extra + '>' +
    ['a:Alpha', 'b:Beta', 'c:Gamma'].map((s) => {
        const [id, label] = s.split(':');
        return `<li class="dj-sortable-list__item" data-id="${id}" data-key="${id}" draggable="true" role="listitem">` +
            `<span class="dj-sortable-list__handle" aria-hidden="true">&#x2630;</span> ` +
            `<span class="dj-sortable-list__label">${label}</span></li>`;
    }).join('') + '</ul>';

const SORTABLE_GRID = (extra = '') =>
    '<div class="dj-sortable-grid" dj-hook="SortableGrid" data-move-event="reorder" data-columns="2" role="grid"' + extra + '>' +
    ['a', 'b', 'c', 'd', 'e'].map((id) =>
        `<div class="dj-sortable-grid__item" data-id="${id}" data-key="${id}" draggable="true">` +
        `<span class="dj-sortable-grid__label">Item ${id}</span></div>`).join('') + '</div>';

const JSON_DATA = { name: 'djust "quoted"', nested: { deep: [1, 2] } };
const JSON_VIEWER = () => {
    const raw = JSON.stringify(JSON_DATA, null, 2).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    return '<div class="dj-json-viewer" dj-hook="JsonViewer" data-collapsed-depth="1">' +
        '<div class="dj-json-viewer__header"><span class="dj-json-viewer__label">root</span>' +
        '<button class="dj-json-viewer__copy" type="button" aria-label="Copy JSON">Copy</button></div>' +
        '<div class="dj-json-viewer__tree">' +
        '<div class="dj-json__node dj-json__node--object" id="n-root">' +
        '<span class="dj-json__toggle" role="button" tabindex="0" aria-expanded="true">&#9660;</span>' +
        '<span class="dj-json__bracket">{</span>' +
        '<div class="dj-json__children">' +
        '<div class="dj-json__pair"><span class="dj-json__key">"name"</span><span class="dj-json__colon">: </span>' +
        '<span class="dj-json__value dj-json__value--string">"x"</span></div>' +
        '<div class="dj-json__pair"><span class="dj-json__key">"nested"</span><span class="dj-json__colon">: </span>' +
        '<div class="dj-json__node dj-json__node--object dj-json__node--collapsed" id="n-nested">' +
        '<span class="dj-json__toggle" role="button" tabindex="0" aria-expanded="false">&#9654;</span>' +
        '<span class="dj-json__bracket">{</span> <span class="dj-json__count">(1 keys)</span>' +
        '<div class="dj-json__children"><div class="dj-json__pair"><span class="dj-json__key">"deep"</span></div></div>' +
        '<span class="dj-json__bracket">}</span></div></div>' +
        '</div><span class="dj-json__bracket">}</span></div></div>' +
        `<script type="application/json" class="dj-json-viewer__raw">${raw}</script></div>`;
};

const LOG_VIEWER = (attrs = ' data-stream-event="new_logs" data-auto-scroll="true" data-line-numbers="true" data-max-lines="4"') =>
    `<div class="dj-log-viewer" dj-hook="LogViewer"${attrs} role="log" aria-live="polite">` +
    '<div class="dj-log-viewer__body">' +
    '<div class="dj-log-viewer__line dj-log-viewer__line--info"><span class="dj-log-viewer__num">1</span><span class="dj-log-viewer__text">12:00 INFO up</span></div>' +
    '<div class="dj-log-viewer__line"><span class="dj-log-viewer__num">2</span><span class="dj-log-viewer__text">plain</span></div>' +
    '</div></div>';

// ---------------------------------------------------------------------------
// An app's own hook always wins (the owner's "preserve custom hooks").
// ---------------------------------------------------------------------------

const COMPONENTS = [
    { hook: 'SortableList', file: 'sortable-list.js', markup: SORTABLE_LIST(), probe: (el) => el.querySelector('li').hasAttribute('tabindex') },
    { hook: 'SortableGrid', file: 'sortable-grid.js', markup: SORTABLE_GRID(), probe: (el) => el.querySelector('div').hasAttribute('tabindex') },
    { hook: 'JsonViewer', file: 'json-viewer.js', markup: JSON_VIEWER(), probe: (el) => el.querySelector('.dj-json__toggle').hasAttribute('aria-label') },
    { hook: 'LogViewer', file: 'log-viewer.js', markup: LOG_VIEWER(), probe: (el) => el.querySelector('.dj-log-viewer__body').hasAttribute('tabindex') },
];

describe('the shipped hooks never replace an app hook (#2985)', () => {
    for (const c of COMPONENTS) {
        describe(c.hook, () => {
            it('registers a hook, so mounting logs no "No hook registered"', () => {
                const env = boot(c.markup, c.file);
                expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(true);
            });

            it('without the script the warning is logged (gate-off)', () => {
                const env = createEnv(c.markup);
                env.window.djust.mountHooks();
                expect(env.warnings.some((w) => w.includes(`No hook registered for "${c.hook}"`))).toBe(true);
            });

            it('an app hook in window.djust.hooks registered first is the one that runs', () => {
                const mounted = vi.fn();
                const env = boot(c.markup, c.file, {
                    preRegister: (w) => { w.djust.hooks = { [c.hook]: { mounted } }; },
                });
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook in window.DjustHooks registered first is the one that runs', () => {
                const mounted = vi.fn();
                const mine = { mounted };
                const env = boot(c.markup, c.file, {
                    preRegister: (w) => { w.DjustHooks = { [c.hook]: mine }; },
                });
                expect(env.window.DjustHooks[c.hook]).toBe(mine);
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook in window.djust.hooks registered AFTER the script still wins', () => {
                const mounted = vi.fn();
                const env = createEnv(c.markup);
                env.window.eval(read(c.file));
                env.window.djust.hooks = { [c.hook]: { mounted } };
                env.window.djust.mountHooks();
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.window.document.querySelector(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook assigned into window.DjustHooks AFTER the script still wins', () => {
                const mounted = vi.fn();
                const env = createEnv(c.markup);
                env.window.eval(read(c.file));
                env.window.DjustHooks[c.hook] = { mounted };
                env.window.djust.mountHooks();
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.window.document.querySelector(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it("an app hook's pushEvent/handleEvent API is untouched by the shipped script", () => {
                const seen = [];
                const env = boot(c.markup, c.file, {
                    preRegister: (w) => {
                        w.djust.hooks = {
                            [c.hook]: {
                                mounted() {
                                    seen.push(typeof this.pushEvent, typeof this.handleEvent, this.el.getAttribute('dj-hook'));
                                },
                            },
                        };
                    },
                });
                expect(seen).toEqual(['function', 'function', c.hook]);
                expect(env.$(`[dj-hook="${c.hook}"]`)).not.toBeNull();
            });
        });
    }
});

// ---------------------------------------------------------------------------
// SortableList
// ---------------------------------------------------------------------------

function dragEvent(window, type, { target, clientX = 0, clientY = 0, relatedTarget = null } = {}) {
    const e = new window.Event(type, { bubbles: true, cancelable: true });
    e.dataTransfer = { setData() {}, effectAllowed: '', dropEffect: '' };
    Object.assign(e, { clientX, clientY, relatedTarget });
    target.dispatchEvent(e);
    return e;
}

function key(window, el, k, extra = {}) {
    const e = new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra });
    el.dispatchEvent(e);
    return e;
}

function rect(el, r) {
    el.getBoundingClientRect = () => ({ top: 0, left: 0, width: 0, height: 0, ...r });
}

const ids = (env, sel) => env.$$(sel).map((el) => el.getAttribute('data-id'));

describe('SortableList', () => {
    it('gives the list one tab stop and a role description', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        expect(env.$$('li').map((li) => li.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
        expect(env.$('li').getAttribute('aria-roledescription')).toBe('sortable item');
    });

    it('dragging an item onto the lower half of another places it after, and sends the order', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const [a, , c] = env.$$('li');
        rect(c, { top: 100, height: 40 });
        dragEvent(env.window, 'dragstart', { target: a });
        expect(a.classList.contains('dj-sortable-list__item--dragging')).toBe(true);
        const over = dragEvent(env.window, 'dragover', { target: c.querySelector('.dj-sortable-list__label') });
        expect(over.defaultPrevented).toBe(true);
        expect(c.classList.contains('dj-sortable-list__item--over')).toBe(true);
        dragEvent(env.window, 'drop', { target: c, clientY: 130 });
        dragEvent(env.window, 'dragend', { target: a });
        expect(ids(env, 'li')).toEqual(['b', 'c', 'a']);
        expect(env.sent).toEqual([{ name: 'reorder', params: { order: ['b', 'c', 'a'] } }]);
        expect(a.classList.contains('dj-sortable-list__item--dragging')).toBe(false);
        expect(c.classList.contains('dj-sortable-list__item--over')).toBe(false);
    });

    it('the upper half places it before', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const [a, b, c] = env.$$('li');
        rect(a, { top: 0, height: 40 });
        dragEvent(env.window, 'dragstart', { target: c });
        dragEvent(env.window, 'drop', { target: a, clientY: 5 });
        expect(ids(env, 'li')).toEqual(['c', 'a', 'b']);
        expect(env.sent[0].params.order).toEqual(['c', 'a', 'b']);
        expect(b.parentNode).toBe(a.parentNode);
    });

    it('dropping where it started, or outside an item, sends nothing', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const [a, , c] = env.$$('li');
        dragEvent(env.window, 'dragstart', { target: a });
        dragEvent(env.window, 'drop', { target: a });
        dragEvent(env.window, 'dragend', { target: a });
        dragEvent(env.window, 'dragstart', { target: c });
        dragEvent(env.window, 'drop', { target: env.$('ul') });
        dragEvent(env.window, 'dragend', { target: c });
        expect(env.sent).toEqual([]);
        expect(ids(env, 'li')).toEqual(['a', 'b', 'c']);
    });

    it('a disabled list neither drags nor takes focus, and ignores the keyboard', () => {
        const env = boot(SORTABLE_LIST(' data-disabled="true"'), 'sortable-list.js');
        const [a, , c] = env.$$('li');
        expect(a.hasAttribute('tabindex')).toBe(false);
        const e = dragEvent(env.window, 'dragstart', { target: a });
        expect(a.classList.contains('dj-sortable-list__item--dragging')).toBe(false);
        expect(e.defaultPrevented).toBe(false);
        rect(c, { top: 0, height: 40 });
        dragEvent(env.window, 'drop', { target: c, clientY: 30 });
        key(env.window, a, ' ');
        expect(env.sent).toEqual([]);
        expect(ids(env, 'li')).toEqual(['a', 'b', 'c']);
        expect(env.live()).toBeUndefined();
    });

    it('sends nothing, but still reorders, when the list has no move event', () => {
        const env = boot(SORTABLE_LIST().replace(' data-move-event="reorder"', ''), 'sortable-list.js');
        const [a, , c] = env.$$('li');
        rect(c, { top: 0, height: 40 });
        dragEvent(env.window, 'dragstart', { target: a });
        dragEvent(env.window, 'drop', { target: c, clientY: 30 });
        expect(ids(env, 'li')).toEqual(['b', 'c', 'a']);
        expect(env.sent).toEqual([]);
    });

    it('announces a CustomEvent an app can observe', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const [a, , c] = env.$$('li');
        const seen = [];
        env.$('ul').addEventListener('dj-reorder', (e) => seen.push(e.detail));
        rect(c, { top: 0, height: 40 });
        dragEvent(env.window, 'dragstart', { target: a });
        dragEvent(env.window, 'drop', { target: c, clientY: 30 });
        expect(seen).toEqual([{ event: 'reorder', order: ['b', 'c', 'a'] }]);
    });

    describe('keyboard', () => {
        it('arrows move focus (roving tabindex), Home/End jump', () => {
            const env = boot(SORTABLE_LIST(), 'sortable-list.js');
            const [a, b, c] = env.$$('li');
            a.focus();
            key(env.window, a, 'ArrowDown');
            expect(env.window.document.activeElement).toBe(b);
            expect(env.$$('li').map((li) => li.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);
            key(env.window, b, 'End');
            expect(env.window.document.activeElement).toBe(c);
            key(env.window, c, 'Home');
            expect(env.window.document.activeElement).toBe(a);
            expect(env.sent).toEqual([]);
        });

        it('Space grabs, arrows move, Enter drops and sends the order', () => {
            const env = boot(SORTABLE_LIST(), 'sortable-list.js');
            const [a, , ] = env.$$('li');
            a.focus();
            key(env.window, a, ' ');
            expect(env.live()).toMatch(/^Alpha grabbed, position 1 of 3/);
            key(env.window, a, 'ArrowDown');
            expect(ids(env, 'li')).toEqual(['b', 'a', 'c']);
            expect(env.window.document.activeElement).toBe(a);
            expect(env.live()).toBe('Alpha moved to position 2 of 3');
            key(env.window, a, 'ArrowDown');
            key(env.window, a, 'ArrowDown'); // already last: stays
            expect(ids(env, 'li')).toEqual(['b', 'c', 'a']);
            expect(env.sent).toEqual([]); // nothing until it is dropped
            key(env.window, a, 'Enter');
            expect(env.sent).toEqual([{ name: 'reorder', params: { order: ['b', 'c', 'a'] } }]);
            expect(env.live()).toBe('Alpha dropped at position 3 of 3');
            expect(a.classList.contains('dj-sortable-list__item--dragging')).toBe(false);
        });

        it('survives the focusout Chrome fires when a focused node is moved (real-browser regression)', () => {
            const env = boot(SORTABLE_LIST(), 'sortable-list.js');
            const ul = env.$('ul');
            for (const method of ['insertBefore', 'appendChild']) {
                const real = ul[method].bind(ul);
                ul[method] = (node, ...rest) => {
                    if (node === env.window.document.activeElement) {
                        node.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
                    }
                    return real(node, ...rest);
                };
            }
            const [a] = env.$$('li');
            a.focus();
            key(env.window, a, ' ');
            key(env.window, a, 'ArrowDown');
            expect(ids(env, 'li')).toEqual(['b', 'a', 'c']);
            key(env.window, a, 'Enter');
            expect(env.sent).toEqual([{ name: 'reorder', params: { order: ['b', 'a', 'c'] } }]);
            key(env.window, a, ' ');
            key(env.window, a, 'End');
            key(env.window, a, 'Escape');
            expect(ids(env, 'li')).toEqual(['b', 'a', 'c']);
            expect(env.live()).toMatch(/^Reorder cancelled/);
        });

        it('Escape puts everything back and sends nothing', () => {
            const env = boot(SORTABLE_LIST(), 'sortable-list.js');
            const [a] = env.$$('li');
            a.focus();
            key(env.window, a, ' ');
            key(env.window, a, 'ArrowDown');
            key(env.window, a, 'End');
            key(env.window, a, 'Escape');
            expect(ids(env, 'li')).toEqual(['a', 'b', 'c']);
            expect(env.sent).toEqual([]);
            expect(env.live()).toMatch(/^Reorder cancelled/);
            expect(env.window.document.activeElement).toBe(a);
        });

        it('dropping a grab that did not move sends nothing', () => {
            const env = boot(SORTABLE_LIST(), 'sortable-list.js');
            const [a] = env.$$('li');
            a.focus();
            key(env.window, a, ' ');
            key(env.window, a, ' ');
            expect(env.sent).toEqual([]);
        });

        it('moving focus out of the list cancels a grab', () => {
            const env = boot(SORTABLE_LIST() + '<button id="out">x</button>', 'sortable-list.js');
            const [a] = env.$$('li');
            a.focus();
            key(env.window, a, ' ');
            key(env.window, a, 'ArrowDown');
            env.$('#out').focus();
            expect(ids(env, 'li')).toEqual(['a', 'b', 'c']);
            expect(env.sent).toEqual([]);
        });

        it('keys typed in a control inside an item are left alone', () => {
            const env = boot(
                SORTABLE_LIST().replace('Alpha', '<input id="in">'), 'sortable-list.js');
            const input = env.$('#in');
            const e = key(env.window, input, ' ');
            expect(e.defaultPrevented).toBe(false);
            expect(env.live()).toBeUndefined();
        });
    });

    it('updated() re-applies the tab stops a server patch dropped, and is idempotent', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        env.$$('li').forEach((li) => li.removeAttribute('tabindex'));
        env.window.djust.updateHooks();
        env.window.djust.updateHooks();
        expect(env.$$('li').map((li) => li.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
        expect(env.$$('li')[0].getAttribute('aria-roledescription')).toBe('sortable item');
    });

    it('gives focus back when a patch that moved the focused item blurred it', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const [a, b] = env.$$('li');
        b.focus();
        env.window.djust.beforeUpdateHooks();
        env.$('ul').insertBefore(b, a);
        b.blur(); // what Chrome does when the node is removed and re-inserted
        expect(env.window.document.activeElement).toBe(env.window.document.body);
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement).toBe(b);
    });

    it('does not steal focus the reader moved elsewhere', () => {
        const env = boot(SORTABLE_LIST() + '<button id="out">x</button>', 'sortable-list.js');
        const [, b] = env.$$('li');
        b.focus();
        env.window.djust.beforeUpdateHooks();
        env.$('#out').focus();
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement).toBe(env.$('#out'));
    });

    it('keeps its tab stop on the item the reader was on after the order changes', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const [a, b] = env.$$('li');
        b.focus();
        env.$('ul').insertBefore(b, a); // as a server patch would
        env.window.djust.updateHooks();
        expect(env.$$('li').map((li) => li.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
        expect(env.$$('li')[0]).toBe(b);
    });

    it('stops listening when the element goes away', () => {
        const env = boot(SORTABLE_LIST(), 'sortable-list.js');
        const ul = env.$('ul');
        const [a, , c] = env.$$('li');
        ul.remove();
        env.window.djust.updateHooks();
        env.window.document.querySelector('[dj-root]').appendChild(ul);
        rect(c, { top: 0, height: 40 });
        dragEvent(env.window, 'dragstart', { target: a });
        dragEvent(env.window, 'drop', { target: c, clientY: 30 });
        expect(env.sent).toEqual([]);
    });
});

// ---------------------------------------------------------------------------
// SortableGrid
// ---------------------------------------------------------------------------

describe('SortableGrid', () => {
    it('drops by the horizontal half of the tile under the pointer', () => {
        const env = boot(SORTABLE_GRID(), 'sortable-grid.js');
        const [a, b, c] = env.$$('.dj-sortable-grid__item');
        rect(c, { left: 100, width: 80 });
        dragEvent(env.window, 'dragstart', { target: a });
        dragEvent(env.window, 'drop', { target: c, clientX: 110 }); // left half: before c
        expect(ids(env, '.dj-sortable-grid__item')).toEqual(['b', 'a', 'c', 'd', 'e']);
        expect(env.sent).toEqual([{ name: 'reorder', params: { order: ['b', 'a', 'c', 'd', 'e'] } }]);
        dragEvent(env.window, 'dragstart', { target: b });
        rect(c, { left: 100, width: 80 });
        dragEvent(env.window, 'drop', { target: c, clientX: 170 }); // right half: after c
        expect(ids(env, '.dj-sortable-grid__item')).toEqual(['a', 'c', 'b', 'd', 'e']);
    });

    it('left/right move one tile, up/down move a row of data-columns', () => {
        const env = boot(SORTABLE_GRID(), 'sortable-grid.js');
        const tiles = () => env.$$('.dj-sortable-grid__item');
        const a = tiles()[0];
        a.focus();
        key(env.window, a, 'ArrowDown'); // 2 columns: a -> c
        expect(env.window.document.activeElement).toBe(tiles()[2]);
        key(env.window, tiles()[2], 'ArrowRight');
        expect(env.window.document.activeElement).toBe(tiles()[3]);
        key(env.window, tiles()[3], 'ArrowUp');
        expect(env.window.document.activeElement).toBe(tiles()[1]);
        key(env.window, tiles()[1], 'ArrowLeft');
        expect(env.window.document.activeElement).toBe(tiles()[0]);
    });

    it('a grabbed tile moves a row at a time and is sent on drop', () => {
        const env = boot(SORTABLE_GRID(), 'sortable-grid.js');
        const a = env.$$('.dj-sortable-grid__item')[0];
        a.focus();
        key(env.window, a, 'Enter');
        key(env.window, a, 'ArrowDown');
        expect(ids(env, '.dj-sortable-grid__item')).toEqual(['b', 'c', 'a', 'd', 'e']);
        key(env.window, a, 'ArrowDown');
        expect(ids(env, '.dj-sortable-grid__item')).toEqual(['b', 'c', 'd', 'e', 'a']);
        expect(env.live()).toBe('Item a moved to position 5 of 5');
        key(env.window, a, 'ArrowUp');
        key(env.window, a, 'Enter');
        expect(env.sent).toEqual([{ name: 'reorder', params: { order: ['b', 'c', 'a', 'd', 'e'] } }]);
    });

    it('survives the focusout Chrome fires when a focused tile is moved (real-browser regression)', () => {
        const env = boot(SORTABLE_GRID(), 'sortable-grid.js');
        const grid = env.$('.dj-sortable-grid');
        const real = grid.insertBefore.bind(grid);
        grid.insertBefore = (node, ...rest) => {
            if (node === env.window.document.activeElement) {
                node.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
            }
            return real(node, ...rest);
        };
        const a = env.$$('.dj-sortable-grid__item')[0];
        a.focus();
        key(env.window, a, 'Enter');
        key(env.window, a, 'ArrowDown');
        key(env.window, a, 'Enter');
        expect(env.sent).toEqual([{ name: 'reorder', params: { order: ['b', 'c', 'a', 'd', 'e'] } }]);
    });

    it('gives focus back to the tile a patch blurred', () => {
        const env = boot(SORTABLE_GRID(), 'sortable-grid.js');
        const [a, b] = env.$$('.dj-sortable-grid__item');
        b.focus();
        env.window.djust.beforeUpdateHooks();
        env.$('.dj-sortable-grid').insertBefore(b, a);
        b.blur();
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement).toBe(b);
    });

    it('Escape restores the original grid', () => {
        const env = boot(SORTABLE_GRID(), 'sortable-grid.js');
        const a = env.$$('.dj-sortable-grid__item')[0];
        a.focus();
        key(env.window, a, ' ');
        key(env.window, a, 'End');
        key(env.window, a, 'Escape');
        expect(ids(env, '.dj-sortable-grid__item')).toEqual(['a', 'b', 'c', 'd', 'e']);
        expect(env.sent).toEqual([]);
    });

    it('a disabled grid does nothing', () => {
        const env = boot(SORTABLE_GRID(' data-disabled="true"'), 'sortable-grid.js');
        const [a, , c] = env.$$('.dj-sortable-grid__item');
        rect(c, { left: 0, width: 80 });
        dragEvent(env.window, 'dragstart', { target: a });
        dragEvent(env.window, 'drop', { target: c, clientX: 70 });
        key(env.window, a, ' ');
        expect(env.sent).toEqual([]);
        expect(a.hasAttribute('tabindex')).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// JsonViewer
// ---------------------------------------------------------------------------

describe('JsonViewer', () => {
    it('names each toggle', () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        expect(env.$$('.dj-json__toggle').map((t) => t.getAttribute('aria-label'))).toEqual(['Toggle object', 'Toggle object']);
    });

    it('clicking a toggle expands a collapsed node and hides its count', () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        const node = env.$('#n-nested');
        const toggle = node.querySelector(':scope > .dj-json__toggle');
        toggle.click();
        expect(node.classList.contains('dj-json__node--collapsed')).toBe(false);
        expect(toggle.getAttribute('aria-expanded')).toBe('true');
        expect(toggle.textContent).toBe('▼');
        expect(node.querySelector(':scope > .dj-json__count').hidden).toBe(true);
        toggle.click();
        expect(node.classList.contains('dj-json__node--collapsed')).toBe(true);
        expect(toggle.getAttribute('aria-expanded')).toBe('false');
        expect(toggle.textContent).toBe('▶');
        expect(node.querySelector(':scope > .dj-json__count').hidden).toBe(false);
    });

    it('collapsing an expanded node works where the server rendered no count', () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        const root = env.$('#n-root');
        root.querySelector(':scope > .dj-json__toggle').click();
        expect(root.classList.contains('dj-json__node--collapsed')).toBe(true);
        // the nested node is hidden with it and keeps its own state
        expect(env.$('#n-nested').classList.contains('dj-json__node--collapsed')).toBe(true);
    });

    it('a toggle toggles only its own node', () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        env.$('#n-nested > .dj-json__toggle').click();
        expect(env.$('#n-root').classList.contains('dj-json__node--collapsed')).toBe(false);
    });

    it('Enter and Space toggle; ArrowRight expands, ArrowLeft collapses', () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        const node = env.$('#n-nested');
        const toggle = node.querySelector(':scope > .dj-json__toggle');
        const collapsed = () => node.classList.contains('dj-json__node--collapsed');
        expect(key(env.window, toggle, 'Enter').defaultPrevented).toBe(true);
        expect(collapsed()).toBe(false);
        key(env.window, toggle, ' ');
        expect(collapsed()).toBe(true);
        key(env.window, toggle, 'ArrowRight');
        expect(collapsed()).toBe(false);
        key(env.window, toggle, 'ArrowRight');
        expect(collapsed()).toBe(false);
        key(env.window, toggle, 'ArrowLeft');
        expect(collapsed()).toBe(true);
        key(env.window, toggle, 'a');
        expect(collapsed()).toBe(true);
    });

    it('copies the formatted JSON, decoded, and says so', async () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        const written = [];
        Object.defineProperty(env.window.navigator, 'clipboard', {
            value: { writeText: (t) => { written.push(t); return Promise.resolve(); } },
            configurable: true,
        });
        const button = env.$('.dj-json-viewer__copy');
        button.click();
        await Promise.resolve();
        await Promise.resolve();
        expect(written).toEqual([JSON.stringify(JSON_DATA, null, 2)]);
        expect(JSON.parse(written[0])).toEqual(JSON_DATA);
        expect(env.live()).toBe('JSON copied to the clipboard');
        expect(button.textContent).toBe('Copied');
    });

    it('restores the button label, even after a second click', async () => {
        vi.useFakeTimers();
        try {
            const env = boot(JSON_VIEWER(), 'json-viewer.js');
            Object.defineProperty(env.window.navigator, 'clipboard', {
                value: { writeText: () => Promise.resolve() }, configurable: true,
            });
            const button = env.$('.dj-json-viewer__copy');
            button.click();
            await Promise.resolve(); await Promise.resolve();
            button.click();
            await Promise.resolve(); await Promise.resolve();
            expect(button.textContent).toBe('Copied');
            vi.advanceTimersByTime(1600);
            expect(button.textContent).toBe('Copy');
        } finally {
            vi.useRealTimers();
        }
    });

    it('reports a copy that failed and leaves the label alone', async () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        Object.defineProperty(env.window.navigator, 'clipboard', {
            value: { writeText: () => Promise.reject(new Error('denied')) }, configurable: true,
        });
        const button = env.$('.dj-json-viewer__copy');
        button.click();
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        expect(env.live()).toBe('Could not copy the JSON');
        expect(button.textContent).toBe('Copy');
    });

    it('does not treat a click elsewhere as a toggle or a copy', () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        env.$('.dj-json__key').click();
        env.$('.dj-json-viewer__label').click();
        expect(env.$$('.dj-json__node--collapsed')).toHaveLength(1);
        expect(env.live()).toBeUndefined();
    });
});

// ---------------------------------------------------------------------------
// LogViewer
// ---------------------------------------------------------------------------

function scrollable(body, { scrollHeight = 1000, clientHeight = 100, scrollTop = 0 } = {}) {
    let top = scrollTop;
    Object.defineProperty(body, 'scrollHeight', { get: () => scrollHeight, configurable: true });
    Object.defineProperty(body, 'clientHeight', { get: () => clientHeight, configurable: true });
    Object.defineProperty(body, 'scrollTop', { get: () => top, set: (v) => { top = v; }, configurable: true });
}

describe('LogViewer', () => {
    it('makes the scrolling body focusable and follows the newest line on mount', () => {
        const env = createEnv(LOG_VIEWER());
        scrollable(env.window.document.querySelector('.dj-log-viewer__body'));
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        expect(body.getAttribute('tabindex')).toBe('0');
        expect(body.scrollTop).toBe(1000);
    });

    it('does not follow without data-auto-scroll', () => {
        const env = createEnv(LOG_VIEWER(' data-stream-event="new_logs"'));
        scrollable(env.window.document.querySelector('.dj-log-viewer__body'));
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        expect(env.window.document.querySelector('.dj-log-viewer__body').scrollTop).toBe(0);
    });

    it('appends streamed lines with level classes and continuing numbers', () => {
        const env = boot(LOG_VIEWER(' data-stream-event="new_logs" data-line-numbers="true"'), 'log-viewer.js');
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['12:01 ERROR down', 'warning: x', 'plain'] });
        const rows = env.$$('.dj-log-viewer__line');
        expect(rows.map((r) => r.querySelector('.dj-log-viewer__num').textContent)).toEqual(['1', '2', '3', '4', '5']);
        expect(rows[2].className).toBe('dj-log-viewer__line dj-log-viewer__line--error');
        expect(rows[3].className).toBe('dj-log-viewer__line dj-log-viewer__line--warn');
        expect(rows[3].querySelector('.dj-log-viewer__text').textContent).toBe('warning: x');
    });

    it('accepts a line, a list or a string, and ignores anything else', () => {
        const env = boot(LOG_VIEWER(' data-stream-event="new_logs" data-line-numbers="true"'), 'log-viewer.js');
        const n = () => env.$$('.dj-log-viewer__line').length;
        env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'one' });
        env.window.djust.dispatchPushEventToHooks('new_logs', ['two', 'three']);
        env.window.djust.dispatchPushEventToHooks('new_logs', 'four');
        expect(n()).toBe(6);
        env.window.djust.dispatchPushEventToHooks('new_logs', { nope: 1 });
        env.window.djust.dispatchPushEventToHooks('new_logs', null);
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: [] });
        expect(n()).toBe(6);
        env.window.djust.dispatchPushEventToHooks('other_event', { lines: ['x'] });
        expect(n()).toBe(6);
    });

    it('streamed text is text, never markup', () => {
        const env = boot(LOG_VIEWER(), 'log-viewer.js');
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['<img src=x onerror=alert(1)> INFO'] });
        expect(env.$('.dj-log-viewer__body img')).toBeNull();
        expect(env.$$('.dj-log-viewer__text').pop().textContent).toBe('<img src=x onerror=alert(1)> INFO');
    });

    it('drops the oldest lines past data-max-lines and keeps numbering absolute', () => {
        const env = boot(LOG_VIEWER(), 'log-viewer.js'); // max 4, 2 rendered
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO a', 'INFO b', 'INFO c', 'INFO d'] });
        const rows = env.$$('.dj-log-viewer__line');
        expect(rows.map((r) => r.querySelector('.dj-log-viewer__text').textContent)).toEqual(['INFO a', 'INFO b', 'INFO c', 'INFO d']);
        expect(rows[0].querySelector('.dj-log-viewer__num').textContent).toBe('3');
    });

    it('honours data-filter-level and data-line-numbers on streamed lines', () => {
        const env = boot(
            LOG_VIEWER(' data-stream-event="new_logs" data-filter-level="error"'), 'log-viewer.js');
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO a', 'ERROR b', 'ERROR c'] });
        const rows = env.$$('.dj-log-viewer__line');
        expect(rows).toHaveLength(4); // the two rendered, then the two errors
        expect(env.$$('.dj-log-viewer__num')).toHaveLength(0 + 2); // only the server-rendered ones carry numbers here
        expect(rows.slice(2).map((r) => r.querySelector('.dj-log-viewer__text').textContent)).toEqual(['ERROR b', 'ERROR c']);
    });

    // The reader's position is tracked from scroll events (never measured per
    // streamed line), and the follow is one scroll per animation frame.
    const frame = (env) => new Promise((resolve) => env.window.requestAnimationFrame(() => resolve()));
    const scrolled = (env, body, top) => {
        body.scrollTop = top;
        body.dispatchEvent(new env.window.Event('scroll'));
    };

    it('stays pinned to the bottom while the reader is at the bottom', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        scrollable(body, { scrollTop: 900 }); // 900 + 100 >= 1000: at the bottom
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 900);
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO more'] });
        await frame(env);
        expect(body.scrollTop).toBe(1000);
    });

    it('stops following once the reader scrolls up, and resumes at the bottom', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        scrollable(body);
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 200); // reading history
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO more'] });
        await frame(env);
        expect(body.scrollTop).toBe(200);
        scrolled(env, body, 900); // back at the bottom
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO more'] });
        await frame(env);
        expect(body.scrollTop).toBe(1000);
    });

    it('scrolls once per frame however many events arrived in it, and never measures per event', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        scrollable(body, { scrollTop: 900 });
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 900);
        let reads = 0;
        let writes = 0;
        let top = 900;
        Object.defineProperty(body, 'scrollHeight', { get: () => { reads += 1; return 1000; }, configurable: true });
        Object.defineProperty(body, 'scrollTop', { get: () => top, set: (v) => { writes += 1; top = v; }, configurable: true });
        for (let i = 0; i < 50; i++) {
            env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO ' + i });
        }
        expect(reads).toBe(0);
        expect(writes).toBe(0);
        await frame(env);
        expect(writes).toBe(1);
        expect(top).toBe(1000);
    });

    it('follows a server re-render the same way, unless the reader scrolled up', () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        scrollable(body);
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();

        scrolled(env, body, 900); // at the bottom
        env.window.djust.beforeUpdateHooks();
        env.window.djust.updateHooks();
        expect(body.scrollTop).toBe(1000);

        scrolled(env, body, 100); // read up
        env.window.djust.beforeUpdateHooks();
        env.window.djust.updateHooks();
        expect(body.scrollTop).toBe(100);
    });

    // The scroll event for the hook's own follow arrives a frame late, after
    // more rows were appended; it must not read as the reader leaving (review 2).
    it('keeps following a fast stream whose own scroll events arrive late', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        let height = 1000;
        let top = 900;
        Object.defineProperty(body, 'scrollHeight', { get: () => height, configurable: true });
        Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
        Object.defineProperty(body, 'scrollTop', { get: () => top, set: (v) => { top = v; }, configurable: true });
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 900);
        for (let round = 0; round < 20; round++) {
            env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO ' + round });
            await frame(env);
            expect(top).toBe(height); // followed
            height += 500; // more rows arrive before the scroll event of our own follow
            body.dispatchEvent(new env.window.Event('scroll')); // late: top < the new bottom
        }
        env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO last' });
        await frame(env);
        expect(top).toBe(height);
    });

    it('a reader who scrolled up stays up while a long stream arrives, and resumes at the bottom', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        let height = 1000;
        let top = 900;
        Object.defineProperty(body, 'scrollHeight', { get: () => height, configurable: true });
        Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
        Object.defineProperty(body, 'scrollTop', { get: () => top, set: (v) => { top = v; }, configurable: true });
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 900);
        scrolled(env, body, 300); // up
        for (let round = 0; round < 30; round++) {
            env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO ' + round });
            height += 500;
            await frame(env);
            expect(top).toBe(300);
        }
        scrolled(env, body, height - 100); // back to the bottom
        env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO more' });
        await frame(env);
        expect(top).toBe(height);
    });

    it('a reader scrolling up in the same frame as an append is not pulled down', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        scrollable(body, { scrollTop: 900 });
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 900);
        env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO a' }); // schedules a follow
        scrolled(env, body, 200); // the reader scrolls up before the frame
        await frame(env);
        expect(body.scrollTop).toBe(200);
    });

    it('a patch that clamps scrollTop after the log shrank does not unpin', async () => {
        const env = createEnv(LOG_VIEWER());
        const body = env.window.document.querySelector('.dj-log-viewer__body');
        let height = 1000;
        let top = 900;
        Object.defineProperty(body, 'scrollHeight', { get: () => height, configurable: true });
        Object.defineProperty(body, 'clientHeight', { get: () => 100, configurable: true });
        Object.defineProperty(body, 'scrollTop', { get: () => top, set: (v) => { top = v; }, configurable: true });
        env.window.eval(read('log-viewer.js'));
        env.window.djust.mountHooks();
        scrolled(env, body, 900);
        env.window.djust.beforeUpdateHooks();
        height = 600; top = 500; // shrunk and clamped: bottom is 600
        env.window.djust.updateHooks();
        expect(top).toBe(600);
        body.dispatchEvent(new env.window.Event('scroll')); // the clamp's scroll event
        env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO x' });
        height = 700;
        await frame(env);
        expect(top).toBe(700);
    });

    it('recounts from the re-rendered lines after a server update', () => {
        const env = boot(LOG_VIEWER(), 'log-viewer.js');
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO a'] }); // 3
        env.$('.dj-log-viewer__body').innerHTML =
            '<div class="dj-log-viewer__line"><span class="dj-log-viewer__num">10</span><span class="dj-log-viewer__text">x</span></div>';
        env.window.djust.updateHooks();
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO b'] });
        expect(env.$$('.dj-log-viewer__num').map((n) => n.textContent)).toEqual(['10', '11']);
    });

    it('without a stream event it registers no listener', () => {
        const env = boot(LOG_VIEWER(' data-auto-scroll="true"'), 'log-viewer.js');
        env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO a'] });
        expect(env.$$('.dj-log-viewer__line')).toHaveLength(2);
    });
});

// A server patch, as the client applies it: beforeUpdate hooks, DOM change, updated hooks.
function serverPatch(env, mutate) {
    env.window.djust.beforeUpdateHooks();
    mutate();
    env.window.djust.updateHooks();
}

const SHAPES = [
    { name: 'SortableList', file: 'sortable-list.js', markup: () => SORTABLE_LIST(), root: 'ul', item: 'li', ids: ['a', 'b', 'c'] },
    { name: 'SortableGrid', file: 'sortable-grid.js', markup: () => SORTABLE_GRID(), root: '.dj-sortable-grid', item: '.dj-sortable-grid__item', ids: ['a', 'b', 'c', 'd', 'e'] },
];

for (const shape of SHAPES) {
    describe(`${shape.name}: a server patch during a keyboard grab (review I1)`, () => {
        const grabFirstAndMove = (env) => {
            const first = env.$$(shape.item)[0];
            first.focus();
            key(env.window, first, ' ');
            key(env.window, first, 'ArrowRight');
            if (shape.name === 'SortableList') key(env.window, first, 'ArrowDown');
            return first;
        };
        const orderNow = (env) => ids(env, shape.item);

        it('Escape after the server removed an item does not bring it back', () => {
            const env = boot(shape.markup(), shape.file);
            const first = grabFirstAndMove(env);
            const victim = env.$$(shape.item).find((el) => el !== first);
            serverPatch(env, () => victim.remove());
            const expected = orderNow(env);
            expect(expected).not.toContain(victim.getAttribute('data-id'));
            key(env.window, first, 'Escape');
            expect(orderNow(env)).toEqual(expected);
            expect(env.sent).toEqual([]);
            expect(env.live()).toMatch(/^Reorder cancelled, the list was updated/);
        });

        it('Escape after the server reshuffled leaves the list as the server has it', () => {
            const env = boot(shape.markup(), shape.file);
            const first = grabFirstAndMove(env);
            serverPatch(env, () => {
                const root = env.$(shape.root);
                const nodes = env.$$(shape.item);
                nodes.reverse().forEach((n) => root.appendChild(n));
            });
            const reshuffled = orderNow(env);
            key(env.window, first, 'Escape');
            expect(orderNow(env)).toEqual(reshuffled);
            expect(env.sent).toEqual([]);
        });

        it('dropping without moving after a server reshuffle sends nothing', () => {
            const env = boot(shape.markup(), shape.file);
            const first = env.$$(shape.item)[0];
            first.focus();
            key(env.window, first, ' ');
            serverPatch(env, () => {
                const root = env.$(shape.root);
                env.$$(shape.item).reverse().forEach((n) => root.appendChild(n));
            });
            key(env.window, first, 'Enter');
            expect(env.sent).toEqual([]);
        });

        it('moving after the server added an item sends the order of the list as it now is', () => {
            const env = boot(shape.markup(), shape.file);
            const first = env.$$(shape.item)[0];
            first.focus();
            key(env.window, first, ' ');
            serverPatch(env, () => {
                const extra = first.parentNode.lastElementChild.cloneNode(true);
                extra.setAttribute('data-id', 'new');
                extra.setAttribute('data-key', 'new');
                first.parentNode.appendChild(extra);
            });
            key(env.window, first, 'End');
            key(env.window, first, 'Enter');
            expect(env.sent).toHaveLength(1);
            const order = env.sent[0].params.order;
            expect(order).toEqual(orderNow(env));
            expect(order).toContain('new');
            expect(order[order.length - 1]).toBe('a');
        });

        it('an unrelated patch (the list unchanged) keeps the grab restorable', () => {
            const env = boot(shape.markup(), shape.file);
            const first = grabFirstAndMove(env);
            serverPatch(env, () => {});
            key(env.window, first, 'Escape');
            expect(orderNow(env)).toEqual(shape.ids);
            expect(env.live()).toMatch(/is back at its original position/);
        });

        it('the held item being removed ends the grab and later keys do nothing', () => {
            const env = boot(shape.markup(), shape.file);
            const first = env.$$(shape.item)[0];
            first.focus();
            key(env.window, first, ' ');
            serverPatch(env, () => first.remove());
            expect(env.live()).toBe('The list changed, so the reorder ended');
            const next = env.$$(shape.item)[0];
            next.focus();
            key(env.window, next, 'End');
            expect(env.sent).toEqual([]);
            expect(orderNow(env)).toEqual(shape.ids.slice(1));
        });

        it('a drag whose item the server removed does not re-insert it on drop', () => {
            const env = boot(shape.markup(), shape.file);
            const [first, , third] = env.$$(shape.item);
            rect(third, { top: 0, height: 40, left: 0, width: 40 });
            dragEvent(env.window, 'dragstart', { target: first });
            serverPatch(env, () => first.remove());
            dragEvent(env.window, 'drop', { target: third, clientX: 30, clientY: 30 });
            expect(orderNow(env)).toEqual(shape.ids.slice(1));
            expect(env.sent).toEqual([]);
        });
    });
}

describe('a repeated announcement is still announced', () => {
    it('alternates a no-break space so the live region changes', async () => {
        const env = boot(JSON_VIEWER(), 'json-viewer.js');
        Object.defineProperty(env.window.navigator, 'clipboard', {
            value: { writeText: () => Promise.resolve() }, configurable: true,
        });
        const seen = [];
        for (let i = 0; i < 3; i++) {
            env.$('.dj-json-viewer__copy').click();
            await Promise.resolve(); await Promise.resolve();
            seen.push(env.live());
        }
        expect(seen.map((m) => m.trim())).toEqual(Array(3).fill('JSON copied to the clipboard'));
        expect(seen[1]).not.toBe(seen[0]);
        expect(seen[2]).not.toBe(seen[1]);
    });
});

describe('LogViewer: streaming does not rescan the rows it holds (review I2)', () => {
    it('1,000 single-line events cost no per-event row query', () => {
        const env = boot(LOG_VIEWER(' data-stream-event="new_logs" data-line-numbers="true"'), 'log-viewer.js');
        const body = env.$('.dj-log-viewer__body');
        let queries = 0;
        const real = body.querySelectorAll.bind(body);
        body.querySelectorAll = (...a) => { queries += 1; return real(...a); };
        for (let i = 0; i < 1000; i++) {
            env.window.djust.dispatchPushEventToHooks('new_logs', { line: 'INFO event ' + i });
        }
        expect(queries).toBe(0);
        expect(env.$$('.dj-log-viewer__line')).toHaveLength(1002);
        expect(env.$$('.dj-log-viewer__num').pop().textContent).toBe('1002');
    });

    it('max_lines trims from the front without querying, and the row count stays right', () => {
        const env = boot(LOG_VIEWER(' data-stream-event="new_logs" data-max-lines="50"'), 'log-viewer.js');
        const body = env.$('.dj-log-viewer__body');
        let queries = 0;
        const real = body.querySelectorAll.bind(body);
        body.querySelectorAll = (...a) => { queries += 1; return real(...a); };
        for (let i = 0; i < 400; i++) {
            env.window.djust.dispatchPushEventToHooks('new_logs', { lines: ['INFO a ' + i, 'INFO b ' + i] });
        }
        expect(queries).toBe(0);
        expect(env.$$('.dj-log-viewer__line')).toHaveLength(50);
        expect(env.$$('.dj-log-viewer__text').pop().textContent).toBe('INFO b 399');
    });
});

for (const shape of SHAPES) {
    describe(`${shape.name}: a list the server cannot key stays inert (review 2)`, () => {
        const unkeyed = () => shape.markup().replace(/ data-key="[^"]*"/, '');
        it('sets no tab stop, ignores keys, cancels drags and warns once', () => {
            const env = boot(unkeyed(), shape.file);
            const [first, , third] = env.$$(shape.item);
            expect(env.$$(shape.item).some((el) => el.hasAttribute('tabindex'))).toBe(false);
            first.focus();
            key(env.window, first, ' ');
            key(env.window, first, 'ArrowDown');
            key(env.window, first, 'Enter');
            expect(env.live()).toBeUndefined();
            rect(third, { top: 0, height: 40, left: 0, width: 40 });
            const start = dragEvent(env.window, 'dragstart', { target: first });
            expect(start.defaultPrevented).toBe(true);
            dragEvent(env.window, 'drop', { target: third, clientX: 30, clientY: 30 });
            expect(ids(env, shape.item)).toEqual(shape.ids);
            expect(env.sent).toEqual([]);
            env.window.djust.updateHooks();
            env.window.djust.updateHooks();
            expect(env.warnings.filter((w) => w.includes('reordering is off'))).toHaveLength(1);
        });

        it('is live when every item has a key', () => {
            const env = boot(shape.markup(), shape.file);
            expect(env.warnings.filter((w) => w.includes('reordering is off'))).toEqual([]);
            expect(env.$$(shape.item).some((el) => el.hasAttribute('tabindex'))).toBe(true);
        });
    });
}
