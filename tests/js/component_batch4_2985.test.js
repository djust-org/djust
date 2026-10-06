/**
 * #2985 batch 4 — CursorsOverlay, CollabSelection, MentionsInput and
 * DashboardGrid answer their dj-hook, and an app's own hook of the same name
 * still wins.
 *
 * The markup is what the components render (python/djust/tests/
 * test_component_batch4_2985.py pins it on every render path).
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');

function createEnv(bodyHtml, { preRegister, matchMedia } = {}) {
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
    if (matchMedia) window.matchMedia = matchMedia;
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
    env.window.djust.mountHooks();
    env.$ = (sel) => env.window.document.querySelector(sel);
    env.$$ = (sel) => Array.from(env.window.document.querySelectorAll(sel));
    env.live = () => (env.window.document.getElementById('dj-component-live') || {}).textContent;
    return env;
}

const key = (window, el, k, extra = {}) => {
    const e = new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra });
    el.dispatchEvent(e);
    return e;
};
const frame = (env) => new Promise((resolve) => env.window.requestAnimationFrame(() => resolve()));

// ---------------------------------------------------------------------------
// Fixtures (as rendered)
// ---------------------------------------------------------------------------

const CURSOR = (name, x, y, color = '#3b82f6') =>
    `<div class="dj-cursors__cursor" style="left:${x}px;top:${y}px" data-user="${name}">` +
    `<svg class="dj-cursors__arrow" width="16" height="20" viewBox="0 0 16 20" fill="${color}"><path d="M0 0L16 12L8 12L12 20L8 18L4 12L0 16Z"/></svg>` +
    `<span class="dj-cursors__label" style="background:${color}">${name}</span></div>`;
const CURSORS = (users = [['Alice', 120, 340], ['Bob', 450, 200]]) =>
    `<div class="dj-cursors" role="group" aria-label="${users.length} cursor${users.length === 1 ? '' : 's'}" dj-hook="CursorsOverlay">` +
    users.map((u) => CURSOR(...u)).join('') + '</div>';


const COLLAB_RANGE = (name, start, end, text, color = '#3b82f6') =>
    `<span class="dj-collab-sel__range" style="--dj-collab-sel-color:${color}" data-user="${name}" data-start="${start}" data-end="${end}">` +
    `<span class="dj-collab-sel__highlight">${text}</span><span class="dj-collab-sel__label" style="background:${color}">${name}</span></span>`;
const COLLAB = (users = [['Alice', 0, 5, 'Hello'], ['Bob', 6, 11, 'brave']], target = '') =>
    `<div class="dj-collab-sel" role="group" aria-label="${users.length} selections" dj-hook="CollabSelection"${target ? ` data-target="${target}"` : ''}>` +
    users.map((u) => COLLAB_RANGE(...u)).join('') + '</div>';

const USERS = [
    { id: '1', name: 'Alice Cooper' }, { id: '2', name: 'Bob' }, { id: '3', name: 'Cora Lee' },
    { id: '4', name: 'Albert' }, { id: '5', name: 'Dan' },
];
const MENTIONS = (users = USERS, { disabled = false, attrs = 'dj-keydown.enter="send_message"' } = {}) =>
    `<div class="dj-mentions${disabled ? ' dj-mentions--disabled' : ''}" dj-hook="MentionsInput" data-users="[]">` +
    `<input type="text" class="dj-mentions__input" name="message" placeholder="Type @" autocomplete="off"${disabled ? ' disabled' : ''} ${attrs}>` +
    '<ul class="dj-mentions__dropdown" role="listbox">' +
    users.map((u) => `<li class="dj-mentions__item" data-user-id="${u.id}" data-user-name="${u.name}" role="option"><span class="dj-mentions__avatar"><span class="dj-mentions__avatar-initials">${u.name[0]}</span></span><span class="dj-mentions__name">${u.name}</span></li>`).join('') +
    '</ul></div>';

const PANEL = (id, title, col, row, w, h, body = '<p>content</p>') =>
    `<div class="dj-dashboard-grid__panel" data-panel-id="${id}" style="grid-column:${col}/span ${w};grid-row:${row}/span ${h}" draggable="true">` +
    `<div class="dj-dashboard-grid__panel-header"><span class="dj-dashboard-grid__panel-title">${title}</span><span class="dj-dashboard-grid__panel-drag" aria-hidden="true">&#x2630;</span></div>` +
    `<div class="dj-dashboard-grid__panel-body">${body}</div><div class="dj-dashboard-grid__panel-resize" role="separator"></div></div>`;
const PANELS = [['a', 'Revenue', 1, 1, 2, 1], ['b', 'Users', 3, 1, 1, 1], ['c', 'Errors', 1, 2, 1, 1]];
const DASH = (panels = PANELS, columns = 4) =>
    `<div class="dj-dashboard-grid" dj-hook="DashboardGrid" data-move-event="dashboard_move" data-resize-event="dashboard_resize" data-columns="${columns}" ` +
    `style="display:grid;grid-template-columns:repeat(${columns},1fr);grid-auto-rows:minmax(200px,auto);gap:1rem">` +
    panels.map((p) => PANEL(...p)).join('') + '</div>';

describe('the shipped hooks never replace an app hook (#2985 batch 4)', () => {
    const COMPONENTS = [
        { hook: 'DashboardGrid', file: 'dashboard-grid.js', markup: DASH(), probe: (el) => el.querySelector('.dj-dashboard-grid__panel').getAttribute('draggable') === 'false' },
        { hook: 'MentionsInput', file: 'mentions-input.js', markup: MENTIONS(), probe: (el) => el.querySelector('input').getAttribute('role') === 'combobox' },
        { hook: 'CollabSelection', file: 'collab-selection.js', markup: COLLAB(), probe: (el) => el.querySelector('.dj-collab-sel__range').getAttribute('role') === 'group' },
        { hook: 'CursorsOverlay', file: 'cursors-overlay.js', markup: CURSORS(), probe: (el) => el.querySelector('.dj-cursors__cursor').getAttribute('aria-hidden') === 'true' },
    ];
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
                const env = boot(c.markup, c.file, { preRegister: (w) => { w.djust.hooks = { [c.hook]: { mounted } }; } });
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook in window.DjustHooks registered first is the one that runs', () => {
                const mounted = vi.fn();
                const env = boot(c.markup, c.file, { preRegister: (w) => { w.DjustHooks = { [c.hook]: { mounted } }; } });
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook registered after the script still wins (window.djust.hooks overrides)', () => {
                const mounted = vi.fn();
                const env = createEnv(c.markup);
                env.window.eval(read(c.file));
                env.window.djust.hooks = { [c.hook]: { mounted } };
                env.window.djust.mountHooks();
                expect(mounted).toHaveBeenCalledTimes(1);
            });
        });
    }
});

// ---------------------------------------------------------------------------
// CursorsOverlay
// ---------------------------------------------------------------------------

describe('CursorsOverlay', () => {
    const patch = (env, users) => {
        const root = env.$('.dj-cursors');
        root.innerHTML = users.map((u) => CURSOR(...u)).join('');
        root.setAttribute('aria-label', `${users.length} cursors`);
        env.window.djust.updateHooks();
    };

    it('marks the cursors decoration (aria-hidden) and keeps the group label', () => {
        const env = boot(CURSORS(), 'cursors-overlay.js');
        expect(env.$$('.dj-cursors__cursor').every((c) => c.getAttribute('aria-hidden') === 'true')).toBe(true);
        expect(env.$('.dj-cursors').getAttribute('aria-label')).toBe('2 cursors');
        expect(env.$('.dj-cursors').getAttribute('role')).toBe('group');
    });

    it('announces nobody on the first render', () => {
        const env = boot(CURSORS(), 'cursors-overlay.js');
        expect(env.live()).toBeUndefined();
    });

    it('announces who joined and who left, never a movement', () => {
        const env = boot(CURSORS(), 'cursors-overlay.js');
        patch(env, [['Alice', 130, 350], ['Bob', 460, 210]]); // both moved
        expect(env.live()).toBeUndefined();
        patch(env, [['Alice', 130, 350], ['Bob', 460, 210], ['Carol', 5, 5]]);
        expect(env.live()).toBe('Carol joined');
        patch(env, [['Alice', 130, 350], ['Carol', 5, 5]]);
        expect(env.live()).toBe('Bob left');
        patch(env, [['Dave', 1, 1], ['Carol', 5, 5]]);
        expect(env.live()).toBe('Dave joined. Alice left');
    });

    it('summarises a crowd as a count', () => {
        const env = boot(CURSORS(), 'cursors-overlay.js');
        patch(env, [['Alice', 1, 1], ['Bob', 2, 2], ['A', 3, 3], ['B', 4, 4], ['C', 5, 5], ['D', 6, 6]]);
        expect(env.live()).toBe('4 people joined');
        patch(env, []);
        expect(env.live()).toBe('6 people left');
    });

    it('two people with one name: the second joining is announced', () => {
        const env = boot(CURSORS([['Sam', 1, 1]]), 'cursors-overlay.js');
        patch(env, [['Sam', 1, 1], ['Sam', 9, 9]]);
        expect(env.live()).toBe('Sam joined');
    });

    it('a name that is markup is announced as text, never parsed', () => {
        const env = boot(CURSORS(), 'cursors-overlay.js');
        const evil = '<img src=x onerror=window.__pwn=1>';
        const root = env.$('.dj-cursors');
        const c = env.window.document.createElement('div');
        c.className = 'dj-cursors__cursor';
        c.setAttribute('data-user', evil);
        c.setAttribute('style', 'left:1px;top:1px');
        root.appendChild(c);
        env.window.djust.updateHooks();
        expect(env.live()).toBe(`${evil} joined`);
        expect(env.window.document.querySelector('#dj-component-live img')).toBeNull();
        expect(env.window.__pwn).toBeUndefined();
    });

    it('re-applies aria-hidden to a cursor a patch added', () => {
        const env = boot(CURSORS(), 'cursors-overlay.js');
        patch(env, [['Alice', 1, 1], ['Bob', 2, 2], ['Carol', 3, 3]]);
        expect(env.$$('.dj-cursors__cursor').every((c) => c.getAttribute('aria-hidden') === 'true')).toBe(true);
    });

    describe('label placement', () => {
        // a 400 x 300 overlay, labels 60 x 18
        const laid = (users, { width = 400, height = 300, label = [60, 18] } = {}) => {
            const env = createEnv(CURSORS(users));
            const root = env.window.document.querySelector('.dj-cursors');
            Object.defineProperty(root, 'clientWidth', { get: () => width, configurable: true });
            Object.defineProperty(root, 'clientHeight', { get: () => height, configurable: true });
            const size = (l) => {
                Object.defineProperty(l, 'offsetWidth', { get: () => label[0], configurable: true });
                Object.defineProperty(l, 'offsetHeight', { get: () => label[1], configurable: true });
            };
            root.querySelectorAll('.dj-cursors__label').forEach(size);
            env.window.eval(read('cursors-overlay.js'));
            env.window.djust.mountHooks();
            env.size = size;
            env.$$ = (s) => Array.from(env.window.document.querySelectorAll(s));
            return env;
        };
        const classes = (env) => env.$$('.dj-cursors__label').map((l) => [...l.classList].filter((c) => c !== 'dj-cursors__label').join(' '));

        it('a cursor with room keeps the default placement', async () => {
            const env = laid([['Alice', 50, 50]]);
            await frame(env);
            expect(classes(env)).toEqual(['']);
        });

        it('a cursor near the right edge puts its label on the left', async () => {
            const env = laid([['Alice', 380, 50]]);
            await frame(env);
            expect(classes(env)).toEqual(['dj-cursors__label--left']);
        });

        it('a cursor near the bottom puts its label above', async () => {
            const env = laid([['Alice', 50, 290]]);
            await frame(env);
            expect(classes(env)).toEqual(['dj-cursors__label--up']);
        });

        it('a cursor in the bottom-right corner goes left and up', async () => {
            const env = laid([['Alice', 370, 295]]);
            await frame(env);
            expect(classes(env)).toEqual(['dj-cursors__label--left dj-cursors__label--up']);
        });

        it('two cursors on one spot do not stack their labels on top of each other', async () => {
            const env = laid([['Alice', 50, 50], ['Bob', 50, 50]]);
            await frame(env);
            const [a, b] = classes(env);
            expect(a).toBe('');
            expect(b).not.toBe('');
        });

        it('a pile of cursors on one spot ends with a distinct placement for each as far as the room goes', async () => {
            const env = laid([['A', 50, 50], ['B', 50, 50], ['C', 50, 50], ['D', 50, 50]]);
            await frame(env);
            expect(new Set(classes(env)).size).toBe(4);
        });

        it('is recomputed from scratch after a patch (positional patches hand classes to the wrong cursor)', async () => {
            const env = laid([['Alice', 380, 50], ['Bob', 50, 50]]);
            await frame(env);
            expect(classes(env)).toEqual(['dj-cursors__label--left', '']);
            // Alice leaves: Bob's data moves into the first node, which still carries Alice's class
            const root = env.$$('.dj-cursors')[0];
            const first = root.children[0];
            first.setAttribute('data-user', 'Bob');
            first.setAttribute('style', 'left:50px;top:50px');
            root.removeChild(root.children[1]);
            env.window.djust.updateHooks();
            await frame(env);
            expect(classes(env)).toEqual(['']);
        });

        it('does nothing where there is no layout (hidden, or jsdom)', async () => {
            const env = createEnv(CURSORS([['Alice', 380, 50]]));
            env.window.eval(read('cursors-overlay.js'));
            env.window.djust.mountHooks();
            await frame(env);
            expect([...env.window.document.querySelector('.dj-cursors__label').classList]).toEqual(['dj-cursors__label']);
        });

        it('lays out once per frame however many patches arrived in it', async () => {
            const env = laid([['Alice', 50, 50]]);
            await frame(env);
            let reads = 0;
            const label = env.window.document.querySelector('.dj-cursors__label');
            Object.defineProperty(label, 'offsetWidth', { get: () => { reads += 1; return 60; }, configurable: true });
            for (let i = 0; i < 20; i++) env.window.djust.updateHooks();
            expect(reads).toBe(0);
            await frame(env);
            expect(reads).toBe(1);
        });

        it('lays out again when the overlay is resized', async () => {
            let notify;
            const env = createEnv(CURSORS([['Alice', 380, 50]]));
            env.window.ResizeObserver = class { constructor(cb) { notify = cb; } observe() {} disconnect() { this.gone = true; } };
            const root = env.window.document.querySelector('.dj-cursors');
            let width = 600;
            Object.defineProperty(root, 'clientWidth', { get: () => width, configurable: true });
            Object.defineProperty(root, 'clientHeight', { get: () => 300, configurable: true });
            const label = root.querySelector('.dj-cursors__label');
            Object.defineProperty(label, 'offsetWidth', { get: () => 60, configurable: true });
            Object.defineProperty(label, 'offsetHeight', { get: () => 18, configurable: true });
            env.window.eval(read('cursors-overlay.js'));
            env.window.djust.mountHooks();
            await frame(env);
            expect(label.classList.contains('dj-cursors__label--left')).toBe(false);
            width = 400;
            notify();
            await frame(env);
            expect(label.classList.contains('dj-cursors__label--left')).toBe(true);
        });

        it('destroying the hook disconnects the observer and cancels a pending layout', async () => {
            let observer;
            const env = createEnv(CURSORS([['Alice', 380, 50]]));
            env.window.ResizeObserver = class { constructor() { observer = this; } observe() {} disconnect() { this.gone = true; } };
            env.window.eval(read('cursors-overlay.js'));
            env.window.djust.mountHooks();
            env.window.djust.destroyAllHooks();
            expect(observer.gone).toBe(true);
        });
    });
});

// ---------------------------------------------------------------------------
// CollabSelection
// ---------------------------------------------------------------------------

describe('CollabSelection', () => {
    const DOC = '<p id="doc">Hello brave <b>new</b> world</p>';
    // The CSS Custom Highlight API and layout, which jsdom has neither of.
    const anchored = (users, { target = '#doc', rect = { left: 100, top: 50, width: 40, height: 12 }, targetRect = { left: 0, top: 0, right: 600, bottom: 400 }, highlights = true, doc = DOC } = {}) => {
        const env = createEnv(doc + COLLAB(users, target));
        const w = env.window;
        const registry = new Map();
        if (highlights) {
            w.CSS = { highlights: registry };
            w.Highlight = class { constructor(...ranges) { this.ranges = ranges; } };
        }
        w.Range.prototype.getClientRects = function () { return [rect]; };
        w.Range.prototype.getBoundingClientRect = function () { return rect; };
        let targetEl = null;
        try { targetEl = w.document.querySelector(target); } catch (_e) { /* an invalid selector, on purpose */ }
        if (targetEl) targetEl.getBoundingClientRect = () => ({ ...targetRect, width: targetRect.right - targetRect.left, height: targetRect.bottom - targetRect.top });
        w.eval(read('collab-selection.js'));
        w.djust.mountHooks();
        env.registry = registry;
        env.$ = (sel) => w.document.querySelector(sel);
        env.$$ = (sel) => Array.from(w.document.querySelectorAll(sel));
        env.live = () => (w.document.getElementById('dj-component-live') || {}).textContent;
        env.texts = () => [...registry.entries()].sort().map(([k, h]) => h.ranges[0].toString());
        return env;
    };
    const patch = (env, users, target = '#doc') => {
        const root = env.$('.dj-collab-sel');
        root.innerHTML = users.map((u) => COLLAB_RANGE(...u)).join('');
        root.setAttribute('data-target', target);
        env.window.djust.updateHooks();
    };

    describe('without a target', () => {
        it('names each selection (a group) and hides the visual label and highlight from assistive tech', () => {
            const env = boot(COLLAB(), 'collab-selection.js');
            const r = env.$$('.dj-collab-sel__range');
            expect(r.map((x) => x.getAttribute('role'))).toEqual(['group', 'group']);
            expect(r.map((x) => x.getAttribute('aria-label'))).toEqual(['Alice selected: Hello', 'Bob selected: brave']);
            expect(env.$$('.dj-collab-sel__highlight, .dj-collab-sel__label').every((e) => e.getAttribute('aria-hidden') === 'true')).toBe(true);
        });

        it('a selection with no text is named after the person, a long one is cut', () => {
            const env = boot(COLLAB([['Alice', 3, 3, ''], ['Bob', 0, 500, 'x'.repeat(200)]]), 'collab-selection.js');
            const [a, b] = env.$$('.dj-collab-sel__range');
            expect(a.getAttribute('aria-label')).toBe("Alice's selection");
            expect(b.getAttribute('aria-label')).toBe('Bob selected: ' + 'x'.repeat(80) + '\u2026');
        });

        it('draws nothing of its own: no highlight, no anchored class, the inline text stays', () => {
            const env = anchored(undefined, { target: '' });
            expect(env.registry.size).toBe(0);
            expect(env.$('.dj-collab-sel').classList.contains('dj-collab-sel--anchored')).toBe(false);
        });

        it('announces who selected and who cleared, never a change of range', () => {
            const env = boot(COLLAB(), 'collab-selection.js');
            expect(env.live()).toBeUndefined();
            const root = env.$('.dj-collab-sel');
            const set = (users) => { root.innerHTML = users.map((u) => COLLAB_RANGE(...u)).join(''); env.window.djust.updateHooks(); };
            set([['Alice', 1, 6, 'ello '], ['Bob', 6, 11, 'brave']]);
            expect(env.live()).toBeUndefined();
            set([['Alice', 1, 6, 'ello '], ['Bob', 6, 11, 'brave'], ['Carol', 0, 1, 'H']]);
            expect(env.live()).toBe('Carol selected text');
            set([['Carol', 0, 1, 'H']]);
            expect(env.live()).toBe('Alice, Bob cleared their selection');
            set([['A', 0, 1, 'a'], ['B', 0, 1, 'a'], ['C', 0, 1, 'a'], ['D', 0, 1, 'a']]);
            expect(env.live()).toBe('4 people selected text. Carol cleared their selection');
        });

        it('a name that is markup is announced and named as text', () => {
            const env = boot(COLLAB(), 'collab-selection.js');
            const evil = '<img src=x onerror=window.__pwn=1>';
            const root = env.$('.dj-collab-sel');
            const r = env.window.document.createElement('span');
            r.className = 'dj-collab-sel__range';
            r.setAttribute('data-user', evil);
            r.setAttribute('data-start', '0');
            r.setAttribute('data-end', '1');
            root.appendChild(r);
            env.window.djust.updateHooks();
            expect(env.live()).toBe(`${evil} selected text`);
            expect(r.getAttribute('aria-label')).toBe(`${evil}'s selection`);
            expect(env.$$('img')).toHaveLength(0);
            expect(env.window.__pwn).toBeUndefined();
        });
    });

    describe('with a target', () => {
        it('highlights each range of the target text with the CSS Custom Highlight API, the page nodes untouched', async () => {
            const env = anchored();
            await frame(env);
            expect(env.texts()).toEqual(['Hello', 'brave']);
            expect(env.$('#doc').innerHTML).toBe('Hello brave <b>new</b> world'); // no marks, no wrappers
            expect(env.$('.dj-collab-sel').classList.contains('dj-collab-sel--anchored')).toBe(true);
        });

        it('counts offsets over the textContent, across elements', async () => {
            const env = anchored([['Alice', 6, 15, 'brave new']]);
            await frame(env);
            expect(env.texts()).toEqual(['brave new']);
        });

        it('counts UTF-16 code units (an emoji is two)', async () => {
            const env = anchored([['Alice', 0, 3, 'x']], { doc: '<p id="doc">\u{1F600}b</p>' });
            await frame(env);
            expect(env.texts()).toEqual(['\u{1F600}b']);
        });

        it('does not count the text of the component itself when it sits inside the target', async () => {
            // the component comes first, so its own text would shift every offset after it
            const env = createEnv('<div id="doc">' + COLLAB([['Alice', 6, 11, 'world']], '#doc') + 'Hello world</div>');
            const w = env.window;
            w.CSS = { highlights: new Map() };
            w.Highlight = class { constructor(...r) { this.ranges = r; } };
            w.Range.prototype.getClientRects = () => [{ left: 10, top: 10, width: 5, height: 5 }];
            w.document.querySelector('#doc').getBoundingClientRect = () => ({ left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500 });
            w.eval(read('collab-selection.js'));
            w.djust.mountHooks();
            await frame(env);
            expect([...w.CSS.highlights.values()][0].ranges[0].toString()).toBe('world');
        });

        it('hides a name whose text is inside the target but scrolled out of a scroll container around it', async () => {
            const env = createEnv('<div id="scroller"><p id="doc">Hello brave new world</p></div>' + COLLAB([['Alice', 0, 5, 'Hello']], '#doc'));
            const w = env.window;
            w.CSS = { highlights: new Map() };
            w.Highlight = class { constructor(...r) { this.ranges = r; } };
            // the text is at y = 20 (inside #doc, which has scrolled up with its content), the container shows y 100..200
            w.Range.prototype.getClientRects = () => [{ left: 10, top: 20, width: 40, height: 12 }];
            w.document.querySelector('#doc').getBoundingClientRect = () => ({ left: 0, top: -300, right: 500, bottom: 500, width: 500, height: 800 });
            const scroller = w.document.querySelector('#scroller');
            scroller.getBoundingClientRect = () => ({ left: 0, top: 100, right: 500, bottom: 200, width: 500, height: 100 });
            const real = w.getComputedStyle.bind(w);
            w.getComputedStyle = (el, ...rest) => (el === scroller ? { overflowX: 'visible', overflowY: 'auto' } : real(el, ...rest));
            w.eval(read('collab-selection.js'));
            w.djust.mountHooks();
            await frame(env);
            const label = w.document.querySelector('.dj-collab-sel__label');
            expect(label.style.visibility).toBe('hidden');
            // scrolled back so the text is in view: shown
            w.Range.prototype.getClientRects = () => [{ left: 10, top: 120, width: 40, height: 12 }];
            w.document.querySelector('#doc').getBoundingClientRect = () => ({ left: 0, top: 100, right: 500, bottom: 900, width: 500, height: 800 });
            w.dispatchEvent(new w.Event('scroll'));
            await frame(env);
            expect(label.style.visibility).toBe('');
        });

        it('clamps untrusted offsets to the text, and draws nothing for an empty or reversed range', async () => {
            const env = anchored([['A', -50, 5, 'a'], ['B', 16, 9999, 'b'], ['C', 6, 6, 'c'], ['D', 10, 2, 'd'], ['E', 'x', 'y', 'e']]);
            await frame(env);
            expect(env.texts()).toEqual(['Hello', 'world']);
            const labels = env.$$('.dj-collab-sel__label');
            expect(labels.map((l) => l.style.visibility)).toEqual(['', '', 'hidden', 'hidden', 'hidden']);
        });

        it('puts each name above the start of its range, at the viewport position of that text', async () => {
            const env = anchored([['Alice', 0, 5, 'Hello']], { rect: { left: 100, top: 50, width: 40, height: 12 } });
            const label = env.$('.dj-collab-sel__label');
            Object.defineProperty(label, 'offsetHeight', { get: () => 16, configurable: true });
            await frame(env);
            expect(label.style.position).toBe('fixed');
            expect(label.style.left).toBe('100px');
            expect(label.style.top).toBe('32px');
            expect(label.style.visibility).toBe('');
        });

        it('corrects for an ancestor with a transform, which makes position: fixed relative to itself', async () => {
            const env = anchored([['Alice', 0, 5, 'Hello'], ['Bob', 6, 11, 'brave']], { rect: { left: 100, top: 50, width: 40, height: 12 } });
            const labels = env.$$('.dj-collab-sel__label');
            labels.forEach((l) => {
                Object.defineProperty(l, 'offsetHeight', { get: () => 16, configurable: true });
                // an ancestor translated by (40, 25): the label is drawn that far from where left/top say
                l.getBoundingClientRect = () => ({ left: parseFloat(l.style.left) + 40, top: parseFloat(l.style.top) + 25, width: 30, height: 16 });
            });
            env.window.dispatchEvent(new env.window.Event('resize'));
            await frame(env);
            expect(labels.map((l) => [l.style.left, l.style.top])).toEqual([['60px', '7px'], ['60px', '7px']]);
        });

        it('hides a name whose text is scrolled out of the target', async () => {
            const env = anchored([['Alice', 0, 5, 'Hello']], { rect: { left: 100, top: 900, width: 40, height: 12 } });
            await frame(env);
            expect(env.$('.dj-collab-sel__label').style.visibility).toBe('hidden');
        });

        it('writes a colour into the highlight rule only when it is plainly a colour', async () => {
            const env = anchored([
                ['A', 0, 1, 'a', '#ef4444'], ['B', 1, 2, 'b', 'rgb(1, 2, 3)'], ['C', 2, 3, 'c', 'red; } body { display:none } .x {'],
                ['D', 3, 4, 'd', 'url(https://evil.example/x)'],
            ]);
            await frame(env);
            const css = env.$('style[data-dj-collab-sel]').textContent;
            expect(css).toContain('#ef4444');
            expect(css).toContain('rgb(1, 2, 3)');
            expect(css).not.toContain('body');
            expect(css).not.toContain('evil.example');
            expect(css).not.toContain('url(');
            expect(css.match(/::highlight\(/g)).toHaveLength(4);
        });

        it('falls back to the inline text where the browser has no CSS.highlights', async () => {
            const env = anchored(undefined, { highlights: false });
            await frame(env);
            expect(env.registry.size).toBe(0);
            expect(env.$('.dj-collab-sel').classList.contains('dj-collab-sel--anchored')).toBe(false);
            expect(env.$('style[data-dj-collab-sel]')).toBeNull();
        });

        it('a selector that matches nothing, or is not a selector, is the same as no target (and does not throw)', async () => {
            for (const target of ['#nope', '###', '[']) {
                const env = anchored(undefined, { target });
                await frame(env);
                expect(env.registry.size).toBe(0);
                expect(env.$('.dj-collab-sel').classList.contains('dj-collab-sel--anchored')).toBe(false);
            }
        });

        it('re-anchors after a patch: a user leaving drops their highlight, new offsets are followed', async () => {
            const env = anchored();
            await frame(env);
            patch(env, [['Bob', 12, 15, 'new']]);
            await frame(env);
            expect(env.texts()).toEqual(['new']);
            expect(env.registry.size).toBe(1);
        });

        it('re-anchors when the target text changes', async () => {
            let notify;
            const env = createEnv(DOC + COLLAB([['Alice', 0, 5, 'Hello']], '#doc'));
            const w = env.window;
            w.CSS = { highlights: new Map() };
            w.Highlight = class { constructor(...r) { this.ranges = r; } };
            const RealMO = w.MutationObserver;
            w.MutationObserver = class extends RealMO {
                constructor(cb) { super(cb); this.cb = cb; }
                observe(node, opts) { if (node && node.id === 'doc') notify = this.cb; return super.observe(node, opts); }
            };
            w.Range.prototype.getClientRects = () => [{ left: 10, top: 10, width: 5, height: 5 }];
            w.document.querySelector('#doc').getBoundingClientRect = () => ({ left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500 });
            w.eval(read('collab-selection.js'));
            w.djust.mountHooks();
            await frame(env);
            expect([...w.CSS.highlights.values()][0].ranges[0].toString()).toBe('Hello');
            w.document.querySelector('#doc').firstChild.nodeValue = 'Howdy brave ';
            notify();
            await frame(env);
            expect([...w.CSS.highlights.values()][0].ranges[0].toString()).toBe('Howdy');
        });

        it('re-anchors on scroll (any scroller) and on resize, once per frame', async () => {
            const env = anchored();
            await frame(env);
            let calls = 0;
            const original = env.window.Range.prototype.getClientRects;
            env.window.Range.prototype.getClientRects = function () { calls += 1; return original.call(this); };
            const inner = env.window.document.createElement('div');
            env.window.document.body.appendChild(inner);
            for (let i = 0; i < 10; i++) inner.dispatchEvent(new env.window.Event('scroll'));
            env.window.dispatchEvent(new env.window.Event('resize'));
            await frame(env);
            expect(calls).toBe(2); // one per selection, once
        });

        it('scrolling and resizing only move the names: the highlights and the rule are not rebuilt', async () => {
            const env = anchored();
            await frame(env);
            const before = [...env.registry.values()];
            const style = env.$('style[data-dj-collab-sel]');
            const walker = [];
            const real = env.window.document.createTreeWalker.bind(env.window.document);
            env.window.document.createTreeWalker = (root, ...a) => { if (root.id === 'doc') walker.push(1); return real(root, ...a); };
            env.window.dispatchEvent(new env.window.Event('resize'));
            env.window.document.body.dispatchEvent(new env.window.Event('scroll'));
            await frame(env);
            expect([...env.registry.values()]).toEqual(before);
            expect(env.$('style[data-dj-collab-sel]')).toBe(style);
            expect(walker).toHaveLength(0); // the target's text was not walked again
        });

        it('anchors when a patch gives the component a target it did not have', async () => {
            const env = anchored(undefined, { target: '' });
            await frame(env);
            expect(env.registry.size).toBe(0);
            env.$('.dj-collab-sel').setAttribute('data-target', '#doc');
            env.window.djust.updateHooks();
            await frame(env);
            expect(env.texts()).toEqual(['Hello', 'brave']);
            env.$('.dj-collab-sel').removeAttribute('data-target');
            env.window.djust.updateHooks();
            await frame(env);
            expect(env.registry.size).toBe(0);
            expect(env.$('.dj-collab-sel').classList.contains('dj-collab-sel--anchored')).toBe(false);
        });

        it('follows a patch that changes the target selector', async () => {
            const env = anchored(undefined, { doc: DOC + '<p id="other">Other words here</p>' });
            await frame(env);
            env.$('.dj-collab-sel').setAttribute('data-target', '#other');
            env.window.djust.updateHooks();
            await frame(env);
            expect(env.texts()).toEqual(['Other', 'words']);
        });

        it('destroying the hook removes its highlights and rule and stops following the page', async () => {
            const env = anchored();
            await frame(env);
            expect(env.registry.size).toBe(2);
            env.window.djust.destroyAllHooks();
            expect(env.registry.size).toBe(0);
            expect(env.$('style[data-dj-collab-sel]')).toBeNull();
            let calls = 0;
            env.window.Range.prototype.getClientRects = () => { calls += 1; return []; };
            env.window.dispatchEvent(new env.window.Event('resize'));
            await frame(env);
            expect(calls).toBe(0);
        });

        it('two instances do not share highlight names', async () => {
            const env = createEnv(DOC + COLLAB([['Alice', 0, 5, 'Hello']], '#doc') + COLLAB([['Bob', 6, 11, 'brave']], '#doc'));
            const w = env.window;
            const registry = new Map();
            w.CSS = { highlights: registry };
            w.Highlight = class { constructor(...r) { this.ranges = r; } };
            w.Range.prototype.getClientRects = () => [{ left: 10, top: 10, width: 5, height: 5 }];
            w.document.querySelector('#doc').getBoundingClientRect = () => ({ left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500 });
            w.eval(read('collab-selection.js'));
            w.djust.mountHooks();
            await frame(env);
            expect(registry.size).toBe(2);
            w.djust.destroyAllHooks();
            expect(registry.size).toBe(0);
        });
    });
});

// ---------------------------------------------------------------------------
// MentionsInput
// ---------------------------------------------------------------------------

describe('MentionsInput', () => {
    const setup = (markup = MENTIONS()) => {
        const env = boot(markup, 'mentions-input.js');
        const input = env.$('.dj-mentions__input');
        const list = env.$('.dj-mentions__dropdown');
        const sent = [];
        env.window.djust.handleEvent = (name, params) => sent.push([name, params]);
        env.sent = sent;
        env.input = input;
        env.list = list;
        env.rows = () => env.$$('.dj-mentions__item');
        env.shown = () => env.rows().filter((r) => r.style.display !== 'none').map((r) => r.getAttribute('data-user-name'));
        env.type = (text, caret = text.length) => {
            input.focus();
            input.value = text;
            input.setSelectionRange(caret, caret);
            input.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        };
        env.key = (k, extra = {}) => key(env.window, input, k, extra);
        env.open = () => list.style.display === 'block';
        env.active = () => env.rows().filter((r) => r.getAttribute('aria-selected') === 'true').map((r) => r.getAttribute('data-user-name'));
        return env;
    };

    describe('combobox wiring', () => {
        it('names the input a combobox over the listbox, collapsed until it opens', () => {
            const env = setup();
            expect(env.input.getAttribute('role')).toBe('combobox');
            expect(env.input.getAttribute('aria-autocomplete')).toBe('list');
            expect(env.input.getAttribute('aria-haspopup')).toBe('listbox');
            expect(env.input.getAttribute('aria-expanded')).toBe('false');
            expect(env.input.getAttribute('aria-controls')).toBe(env.list.id);
            expect(env.list.getAttribute('aria-label')).toBeTruthy();
            expect(env.rows().map((r) => r.id)).toHaveLength(5);
            expect(new Set(env.rows().map((r) => r.id)).size).toBe(5);
            expect(env.open()).toBe(false);
        });

        it('two components get different ids', () => {
            const env = boot(MENTIONS() + MENTIONS(), 'mentions-input.js');
            const ids = env.$$('.dj-mentions__dropdown').map((l) => l.id);
            expect(new Set(ids).size).toBe(2);
        });

        it('keeps an id an app gave the list and a label it set', () => {
            const html = MENTIONS().replace('role="listbox"', 'role="listbox" id="mine" aria-label="People"');
            const env = setup(html);
            expect(env.list.id).toBe('mine');
            expect(env.list.getAttribute('aria-label')).toBe('People');
            expect(env.input.getAttribute('aria-controls')).toBe('mine');
        });
    });

    describe('opening and filtering', () => {
        it('typing @ opens the list with the first suggestion active', () => {
            const env = setup();
            env.type('@');
            expect(env.open()).toBe(true);
            expect(env.input.getAttribute('aria-expanded')).toBe('true');
            expect(env.shown()).toEqual(['Alice Cooper', 'Bob', 'Cora Lee', 'Albert', 'Dan']);
            expect(env.active()).toEqual(['Alice Cooper']);
            expect(env.input.getAttribute('aria-activedescendant')).toBe(env.rows()[0].id);
        });

        it('filters by the start of the name or of any word in it', () => {
            const env = setup();
            env.type('@co');
            expect(env.shown()).toEqual(['Alice Cooper', 'Cora Lee']);
            env.type('@al');
            expect(env.shown()).toEqual(['Alice Cooper', 'Albert']);
            env.type('@LEE');
            expect(env.shown()).toEqual(['Cora Lee']);
        });

        it('opens after a space but not inside a word (an email address is not a mention)', () => {
            const env = setup();
            env.type('hi @al');
            expect(env.open()).toBe(true);
            env.type('mail me at a@al');
            expect(env.open()).toBe(false);
        });

        it('closes when the caret leaves the mention (a space ends it)', () => {
            const env = setup();
            env.type('@al');
            expect(env.open()).toBe(true);
            env.type('@al ');
            expect(env.open()).toBe(false);
            expect(env.rows().every((r) => r.style.display === '')).toBe(true);
        });

        it('follows the caret, not the end of the text', () => {
            const env = setup();
            env.type('@bo and more', 3);
            expect(env.shown()).toEqual(['Bob']);
        });

        it('a selection (not a caret) opens nothing', () => {
            const env = setup();
            env.input.value = '@al';
            env.input.setSelectionRange(0, 3);
            env.input.dispatchEvent(new env.window.Event('input', { bubbles: true }));
            expect(env.open()).toBe(false);
        });

        it('shows at most 8 suggestions, in the order given', () => {
            const many = Array.from({ length: 20 }, (_, i) => ({ id: String(i), name: 'User ' + i }));
            const env = setup(MENTIONS(many));
            env.type('@');
            expect(env.shown()).toHaveLength(8);
            expect(env.shown()[0]).toBe('User 0');
        });

        it('stays closed with no matches (and says so), and with no users at all', () => {
            const env = setup();
            env.type('@zz');
            expect(env.open()).toBe(false);
            expect(env.live()).toBe('No matching people');
            const none = setup(MENTIONS([]));
            none.type('@');
            expect(none.open()).toBe(false);
        });

        it('a query longer than 30 characters is not a mention', () => {
            const env = setup();
            env.type('@' + 'a'.repeat(31));
            expect(env.open()).toBe(false);
        });

        it('announces how many people match', () => {
            const env = setup();
            env.type('@');
            expect(env.live()).toBe('5 people to mention');
            env.type('@bo');
            expect(env.live()).toBe('1 person to mention');
        });

        it('never reorders, adds or removes the rows the server rendered', () => {
            const env = setup();
            const before = env.rows();
            env.type('@co');
            env.type('@');
            env.type('hi');
            expect(env.rows()).toEqual(before);
            expect(env.list.children).toHaveLength(5);
        });

        it('closing leaves no inline style behind but an empty one the server never set', () => {
            const env = setup();
            env.type('@co');
            env.type('');
            expect(env.list.style.display).toBe('');
            expect(env.rows().every((r) => r.style.display === '' && !r.hasAttribute('aria-selected'))).toBe(true);
            expect(env.input.hasAttribute('aria-activedescendant')).toBe(false);
            expect(env.input.getAttribute('aria-expanded')).toBe('false');
        });
    });

    describe('keyboard', () => {
        it('ArrowDown / ArrowUp move through the suggestions and wrap', () => {
            const env = setup();
            env.type('@');
            expect(env.key('ArrowDown').defaultPrevented).toBe(true);
            expect(env.active()).toEqual(['Bob']);
            env.key('ArrowUp');
            env.key('ArrowUp');
            expect(env.active()).toEqual(['Dan']);
            env.key('ArrowDown');
            expect(env.active()).toEqual(['Alice Cooper']);
            expect(env.input.getAttribute('aria-activedescendant')).toBe(env.rows()[0].id);
        });

        it('Enter inserts the active suggestion as @Name and a space, replacing what was typed', () => {
            const env = setup();
            env.type('hi @co');
            env.key('ArrowDown');
            const inputs = [];
            env.input.addEventListener('input', () => inputs.push(env.input.value));
            const e = env.key('Enter');
            expect(e.defaultPrevented).toBe(true);
            expect(env.input.value).toBe('hi @Cora Lee ');
            expect(env.input.selectionStart).toBe(13);
            expect(env.open()).toBe(false);
            expect(inputs).toEqual(['hi @Cora Lee ']); // an input event, for anything bound to it
            expect(env.live()).toBe('Cora Lee mentioned');
            expect(env.sent).toEqual([]); // choosing is not submitting
        });

        it('Enter that chooses does not reach the plain dj-keydown.enter binding (djust root)', () => {
            const env = setup();
            const reached = [];
            env.$('[dj-root]').addEventListener('keydown', (e) => reached.push(e.key));
            env.type('@bo');
            env.key('Enter');
            expect(reached).toEqual([]);
        });

        it('Tab chooses too', () => {
            const env = setup();
            env.type('@bo');
            expect(env.key('Tab').defaultPrevented).toBe(true);
            expect(env.input.value).toBe('@Bob ');
        });

        it('Tab with the list closed is left alone', () => {
            const env = setup();
            env.type('hello');
            expect(env.key('Tab').defaultPrevented).toBe(false);
        });

        it('Escape closes the list, marked handled, and a second Escape is not', () => {
            const env = setup();
            env.type('@');
            const reached = [];
            env.window.document.addEventListener('keydown', (e) => reached.push([e.key, e.defaultPrevented]));
            expect(env.key('Escape').defaultPrevented).toBe(true);
            expect(env.open()).toBe(false);
            env.key('Escape');
            expect(reached).toEqual([['Escape', true], ['Escape', false]]);
        });

        it('ArrowDown reopens the list while the caret is in a mention', () => {
            const env = setup();
            env.type('@co');
            env.key('Escape');
            expect(env.open()).toBe(false);
            env.key('ArrowDown');
            expect(env.open()).toBe(true);
        });

        it('keys during IME composition are not ours', () => {
            const env = setup();
            env.type('@bo');
            const e = env.key('Enter', { isComposing: true });
            expect(e.defaultPrevented).toBe(false);
            expect(env.input.value).toBe('@bo');
        });

        it('a disabled component is inert', () => {
            const env = setup(MENTIONS(USERS, { disabled: true }));
            env.input.value = '@';
            env.input.setSelectionRange(1, 1);
            env.input.dispatchEvent(new env.window.Event('input', { bubbles: true }));
            expect(env.open()).toBe(false);
            expect(env.key('Enter').defaultPrevented).toBe(false);
            expect(env.sent).toEqual([]);
        });
    });

    describe('mouse', () => {
        it('pressing a suggestion keeps focus in the input; clicking it inserts', () => {
            const env = setup();
            env.type('@al');
            const row = env.rows()[3]; // Albert
            const down = new env.window.MouseEvent('mousedown', { bubbles: true, cancelable: true });
            row.dispatchEvent(down);
            expect(down.defaultPrevented).toBe(true);
            row.querySelector('.dj-mentions__name').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
            expect(env.input.value).toBe('@Albert ');
            expect(env.open()).toBe(false);
        });

        it('leaving the component closes the list, moving focus within it does not', () => {
            const env = setup();
            env.type('@');
            env.input.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: env.rows()[0] }));
            expect(env.open()).toBe(true);
            env.input.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
            expect(env.open()).toBe(false);
        });

        it('a click elsewhere in the page does nothing', () => {
            const env = setup();
            env.window.document.body.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
            expect(env.input.value).toBe('');
        });
    });

    describe('submitting', () => {
        const submit = (env) => env.key('Enter');

        it('Enter sends text and the ids of the people mentioned, with what the plain binding sends', () => {
            const env = setup();
            env.type('Thanks ');
            env.type('Thanks @al');
            env.key('Enter'); // chooses Alice Cooper
            env.type(env.input.value + 'and @bo');
            env.key('Enter'); // chooses Bob
            const e = submit(env);
            expect(e.defaultPrevented).toBe(true);
            expect(env.sent).toEqual([[
                'send_message',
                { text: 'Thanks @Alice Cooper and @Bob ', mentions: ['1', '2'], value: 'Thanks @Alice Cooper and @Bob ', field: 'message', key: 'Enter', code: '' },
            ]]);
        });

        it('lists the ids in the order they appear in the text, each once', () => {
            const env = setup();
            env.type('@bo');
            env.key('Enter');
            env.type(env.input.value + '@al');
            env.key('Enter');
            env.type(env.input.value + '@bo');
            env.key('Enter'); // Bob again
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['2', '1']);
            env.sent.length = 0;
            env.type('@Alice Cooper first, then @Bob ');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['1', '2']);
        });

        it('drops a mention whose text was edited away, and one that is only a prefix of a longer word', () => {
            const env = setup();
            env.type('@bo');
            env.key('Enter');
            env.type(env.input.value + '@al');
            env.key('Enter');
            env.type('@Bobby and @Alice. ');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]);
        });

        it('only people chosen from the list are mentions: typing a name by hand is not', () => {
            const env = setup();
            env.type('@Bob is typed by hand');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]);
            expect(env.sent[0][1].text).toBe('@Bob is typed by hand');
        });

        it('a name with regex characters is matched literally', () => {
            const users = [{ id: '9', name: 'A.B (dev)' }, { id: '8', name: 'AxB (dev)' }];
            const env = setup(MENTIONS(users));
            env.type('@a.b');
            env.key('Enter');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['9']);
            env.sent.length = 0;
            env.type('@AxB (dev) only');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]); // 8 was never chosen
        });

        it('an empty text is still sent (the app decides)', () => {
            const env = setup();
            submit(env);
            expect(env.sent).toEqual([['send_message', { text: '', mentions: [], value: '', field: 'message', key: 'Enter', code: '' }]]);
        });

        it('Enter does not reach the plain binding as well (one event, not two)', () => {
            const env = setup();
            const reached = [];
            env.$('[dj-root]').addEventListener('keydown', (e) => reached.push(e.key));
            env.type('hello');
            submit(env);
            expect(env.sent).toHaveLength(1);
            expect(reached).toEqual([]);
        });

        it('without a dj-keydown.enter the hook does not take Enter', () => {
            const env = setup(MENTIONS(USERS, { attrs: '' }));
            env.type('hello');
            expect(submit(env).defaultPrevented).toBe(false);
            expect(env.sent).toEqual([]);
        });

        it('goes through the client\'s strict-parameter check: a refusal sends nothing, a narrowed set is what is sent', () => {
            const env = setup();
            env.window.djust._strictBinding = () => false;
            env.type('hello');
            submit(env);
            expect(env.sent).toEqual([]);
            env.window.djust._strictBinding = () => ({ text: 'hello', mentions: [] });
            submit(env);
            expect(env.sent).toEqual([['send_message', { text: 'hello', mentions: [] }]]);
        });

        it('carries the input\'s dj-value-* attributes, which the plain binding sends, without overriding the payload', () => {
            const env = setup(MENTIONS(USERS, { attrs: 'dj-keydown.enter="send_message" dj-value-room="lobby" dj-value-text="nope"' }));
            env.type('hello');
            submit(env);
            expect(env.sent[0][1].room).toBe('lobby');
            expect(env.sent[0][1].text).toBe('hello');
        });

        it('with no client API it falls back to the hook\'s own pushEvent', () => {
            const env = setup();
            const pushed = [];
            env.window.djust.handleEvent = undefined;
            const instance = [...env.window.djust._activeHooks.values()].find((h) => h.instance && h.instance._enhance);
            instance.instance.pushEvent = (name, params) => pushed.push([name, params]);
            env.type('hello');
            submit(env);
            expect(pushed[0][0]).toBe('send_message');
            expect(pushed[0][1].text).toBe('hello');
        });
    });

    describe('after the server re-renders', () => {
        it('re-derives what is shown from the rows: a changed users list is followed, the active person kept', () => {
            const env = setup();
            env.type('@');
            env.key('ArrowDown'); // Bob
            // the server drops Alice: positional patches shift the rows up
            const rows = env.rows();
            const names = ['Bob', 'Cora Lee', 'Albert', 'Dan'];
            const ids = ['2', '3', '4', '5'];
            names.forEach((n, i) => { rows[i].setAttribute('data-user-name', n); rows[i].setAttribute('data-user-id', ids[i]); });
            env.list.removeChild(rows[4]);
            env.window.djust.updateHooks();
            expect(env.shown()).toEqual(names);
            expect(env.active()).toEqual(['Bob']);
        });

        it('re-applies the wiring to rows the patch added', () => {
            const env = setup();
            const li = env.rows()[0].cloneNode(true);
            li.removeAttribute('id');
            li.setAttribute('data-user-id', '77');
            li.setAttribute('data-user-name', 'New Person');
            env.list.appendChild(li);
            env.window.djust.updateHooks();
            expect(new Set(env.rows().map((r) => r.id)).size).toBe(6);
            expect(env.rows().every((r) => r.id)).toBe(true);
        });

        it('keeps the mentions chosen so far across a re-render', () => {
            const env = setup();
            env.type('@bo');
            env.key('Enter');
            env.window.djust.updateHooks();
            env.key('Enter');
            expect(env.sent[0][1].mentions).toEqual(['2']);
        });

        it('does not double-bind after repeated patches', () => {
            const env = setup();
            for (let i = 0; i < 5; i++) env.window.djust.updateHooks();
            env.type('hi');
            env.key('Enter');
            expect(env.sent).toHaveLength(1);
        });

        it('destroying the hook removes its listeners', () => {
            const env = setup();
            env.window.djust.destroyAllHooks();
            env.type('@');
            expect(env.open()).toBe(false);
            expect(env.key('Enter').defaultPrevented).toBe(false);
        });
    });

    describe('untrusted values', () => {
        it('a name that is markup is inserted as text', () => {
            const evil = '<img src=x onerror=window.__pwn=1>';
            const html = MENTIONS([{ id: '1', name: 'X' }]).replace('data-user-name="X"', `data-user-name="${evil.replace(/"/g, '&quot;').replace(/</g, '&lt;')}"`);
            const env = setup(html);
            env.type('@');
            env.key('Enter');
            expect(env.input.value).toBe(`@${evil} `);
            expect(env.$$('img')).toHaveLength(0);
            expect(env.window.__pwn).toBeUndefined();
        });

        it('ids are sent as the strings the server rendered, not interpreted', () => {
            const env = setup(MENTIONS([{ id: '1; DROP TABLE', name: 'Eve' }]));
            env.type('@e');
            env.key('Enter');
            env.key('Enter');
            expect(env.sent[0][1].mentions).toEqual(['1; DROP TABLE']);
        });
    });
});

// ---------------------------------------------------------------------------
// DashboardGrid
// ---------------------------------------------------------------------------

describe('DashboardGrid', () => {
    // 4 columns of 100px, rows of 100px, a 10px gap: cell (c, r) starts at ((c-1) * 110, (r-1) * 110).
    const CELL = 110;
    const cellsOf = (el) => {
        const st = el.getAttribute('style') || '';
        const c = /grid-column:(\d+)\/span (\d+)/.exec(st);
        const r = /grid-row:(\d+)\/span (\d+)/.exec(st);
        return { col: +c[1], w: +c[2], row: +r[1], h: +r[2] };
    };
    const setup = (panels = PANELS, columns = 4, { rtl = false, scale = 1, left = 0 } = {}) => {
        const env = createEnv(DASH(panels, columns));
        const w = env.window;
        const root = w.document.querySelector('.dj-dashboard-grid');
        const real = w.getComputedStyle.bind(w);
        w.getComputedStyle = (el, ...rest) => (el === root
            ? { direction: rtl ? 'rtl' : 'ltr', gridTemplateColumns: Array(columns).fill('100px').join(' '), gridTemplateRows: '100px 100px', columnGap: '10px', rowGap: '10px', borderLeftWidth: '0px', borderRightWidth: '0px', borderTopWidth: '0px', paddingLeft: '0px', paddingRight: '0px', paddingTop: '0px' }
            : real(el, ...rest));
        const width = (columns * CELL - 10) * scale;
        // drawn at `scale`, laid out at 1; in a right-to-left grid column 1 is the rightmost
        root.getBoundingClientRect = () => ({ left, top: 0, width, height: 210 * scale, right: left + width, bottom: 210 * scale });
        Object.defineProperty(root, 'offsetWidth', { get: () => columns * CELL - 10, configurable: true });
        Object.defineProperty(root, 'offsetHeight', { get: () => 210, configurable: true });
        const patchRects = () => root.querySelectorAll('.dj-dashboard-grid__panel').forEach((p) => {
            p.getBoundingClientRect = () => {
                const c = cellsOf(p);
                const x0 = (c.col - 1) * CELL;
                const top = (c.row - 1) * CELL * scale;
                const wpx = (c.w * CELL - 10) * scale;
                const left_ = rtl ? left + width - x0 * scale - wpx : left + x0 * scale;
                return { left: left_, top, right: left_ + wpx, bottom: top + (c.h * CELL - 10) * scale, width: wpx, height: (c.h * CELL - 10) * scale };
            };
        });
        patchRects();
        w.eval(read('dashboard-grid.js'));
        w.djust.mountHooks();
        const sent = [];
        w.djust.handleEvent = (name, params) => sent.push([name, params]);
        env.sent = sent;
        env.root = root;
        env.patchRects = patchRects;
        env.$ = (sel) => w.document.querySelector(sel);
        env.$$ = (sel) => Array.from(w.document.querySelectorAll(sel));
        env.live = () => (w.document.getElementById('dj-component-live') || {}).textContent;
        env.panel = (id) => root.querySelector(`[data-panel-id="${id}"]`);
        env.header = (id) => env.panel(id).querySelector('.dj-dashboard-grid__panel-header');
        env.bar = (id) => env.panel(id).querySelector('.dj-dashboard-grid__panel-resize');
        env.ptr = (type, target, x, y, extra = {}) => {
            const e = new w.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, ...extra });
            Object.defineProperty(e, 'pointerId', { value: extra.pointerId === undefined ? 1 : extra.pointerId });
            target.dispatchEvent(e);
            return e;
        };
        env.drag = (id, x0, y0, x1, y1, up = true) => {
            env.ptr('pointerdown', env.header(id), x0, y0);
            env.ptr('pointermove', env.root, x1, y1);
            if (up) env.ptr('pointerup', env.root, x1, y1);
        };
        env.dragEdge = (id, x0, y0, x1, y1, up = true) => {
            env.ptr('pointerdown', env.bar(id), x0, y0);
            env.ptr('pointermove', env.root, x1, y1);
            if (up) env.ptr('pointerup', env.root, x1, y1);
        };
        env.key = (el, k, extra = {}) => key(w, el, k, extra);
        return env;
    };

    describe('panels', () => {
        it('turns native drag off, names each panel (a group), and gives the grid one tab stop', () => {
            const env = setup();
            const panels = env.$$('.dj-dashboard-grid__panel');
            expect(panels.every((p) => p.getAttribute('draggable') === 'false')).toBe(true);
            expect(panels.map((p) => p.getAttribute('role'))).toEqual(['group', 'group', 'group']);
            expect(panels.map((p) => p.getAttribute('aria-label'))).toEqual(['Revenue', 'Users', 'Errors']);
            expect(panels.map((p) => p.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
            expect(env.$$('.dj-dashboard-grid__panel-resize').every((b) => b.getAttribute('aria-hidden') === 'true')).toBe(true);
        });

        it('the tab stop follows focus', () => {
            const env = setup();
            env.panel('b').focus();
            expect(env.$$('.dj-dashboard-grid__panel').map((p) => p.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);
        });

        it('re-applies all of it to a panel a patch added, and moves the tab stop off a removed one', () => {
            const env = setup();
            env.root.insertAdjacentHTML('beforeend', PANEL('d', 'New', 2, 2, 1, 1));
            env.window.djust.updateHooks();
            expect(env.panel('d').getAttribute('draggable')).toBe('false');
            expect(env.panel('d').getAttribute('aria-label')).toBe('New');
            env.root.removeChild(env.panel('a'));
            env.window.djust.updateHooks();
            expect(env.$$('.dj-dashboard-grid__panel').map((p) => p.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
        });

        it('a title that is markup is a name, never parsed', () => {
            const evil = '<img src=x onerror=window.__pwn=1>';
            const env = setup([['a', evil.replace(/</g, '&lt;'), 1, 1, 1, 1]]);
            expect(env.panel('a').getAttribute('aria-label')).toBe(evil);
            expect(env.$$('img')).toHaveLength(0);
            expect(env.window.__pwn).toBeUndefined();
        });
    });

    describe('moving with the pointer', () => {
        it('dragging a header onto another cell sends {id, col, row} in grid units', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120); // Users, from column 3 / row 1 to column 2 / row 2
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 2 }]]);
        });

        it('the panel itself is not moved: the server decides', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120);
            expect(env.panel('b').getAttribute('style')).toBe('grid-column:3/span 1;grid-row:1/span 1');
        });

        it('keeps a wide panel inside the columns, and puts nothing above or left of the grid', () => {
            const env = setup();
            env.drag('a', 10, 10, 900, 10); // 2 wide: the last column it can start in is 3
            expect(env.sent).toEqual([['dashboard_move', { id: 'a', col: 3, row: 1 }]]);
            env.sent.length = 0;
            env.drag('b', 340, 10, -500, -500);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 1, row: 1 }]]);
        });

        it('allows one row below the last, no more', () => {
            const env = setup();
            env.drag('b', 340, 10, 340, 900);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 3, row: 3 }]]);
        });

        it('a drop where it started sends nothing', () => {
            const env = setup();
            env.drag('b', 340, 10, 345, 15);
            expect(env.sent).toEqual([]);
        });

        it('a press without movement, or under 4px, does nothing', () => {
            const env = setup();
            env.ptr('pointerdown', env.header('b'), 340, 10);
            env.ptr('pointerup', env.root, 340, 10);
            env.drag('b', 340, 10, 342, 12);
            expect(env.sent).toEqual([]);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            expect(env.live()).toBeUndefined(); // and says nothing
        });

        it('shows where it will land with a dashed cell (attribute and cell numbers on the grid), and removes it after', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false);
            expect(env.root.getAttribute('data-dj-drop')).toBe('move');
            const st = env.root.style;
            expect([st.getPropertyValue('--dj-drop-col'), st.getPropertyValue('--dj-drop-row'), st.getPropertyValue('--dj-drop-w'), st.getPropertyValue('--dj-drop-h')]).toEqual(['2', '2', '1', '1']);
            expect(env.panel('b').classList.contains('dj-dashboard-grid__panel--dragging')).toBe(true);
            env.ptr('pointerup', env.root, 230, 120);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            expect(env.root.style.getPropertyValue('--dj-drop-col')).toBe('');
            expect(env.root.style.display).toBe('grid'); // the server's own inline style is untouched
            expect(env.$$('.dj-dashboard-grid__panel--dragging')).toHaveLength(0);
            expect(env.root.children).toHaveLength(3); // nothing was added to the DOM
        });

        it('the browser\'s own drag does not start (the press is not a default action)', () => {
            const env = setup();
            const e = env.ptr('pointerdown', env.header('b'), 340, 10);
            expect(e.defaultPrevented).toBe(true);
        });

        it('buttons and fields in a header work as usual and do not start a drag', () => {
            const env = setup();
            env.header('b').insertAdjacentHTML('beforeend', '<button class="h-btn">x</button>');
            const btn = env.header('b').querySelector('.h-btn');
            const e = env.ptr('pointerdown', btn, 340, 10);
            expect(e.defaultPrevented).toBe(false);
            env.ptr('pointermove', env.root, 100, 100);
            env.ptr('pointerup', env.root, 100, 100);
            expect(env.sent).toEqual([]);
        });

        it('the body of a panel is not a handle (text can be selected there)', () => {
            const env = setup();
            const body = env.panel('b').querySelector('.dj-dashboard-grid__panel-body');
            const e = env.ptr('pointerdown', body, 340, 60);
            expect(e.defaultPrevented).toBe(false);
            env.ptr('pointermove', env.root, 100, 100);
            env.ptr('pointerup', env.root, 100, 100);
            expect(env.sent).toEqual([]);
        });

        it('only the primary button drags', () => {
            const env = setup();
            env.ptr('pointerdown', env.header('b'), 340, 10, { button: 2 });
            env.ptr('pointermove', env.root, 100, 100);
            env.ptr('pointerup', env.root, 100, 100);
            expect(env.sent).toEqual([]);
        });

        it('a second pointer, or a second press, does not disturb a gesture in progress', () => {
            const env = setup();
            env.ptr('pointerdown', env.header('b'), 340, 10, { pointerId: 1 });
            env.ptr('pointerdown', env.header('a'), 10, 10, { pointerId: 2 });
            env.ptr('pointermove', env.root, 230, 120, { pointerId: 2 });
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false); // not the gesture's pointer
            env.ptr('pointermove', env.root, 230, 120, { pointerId: 1 });
            env.ptr('pointerup', env.root, 230, 120, { pointerId: 2 });
            expect(env.sent).toEqual([]); // pointer 2 ending is not the gesture ending
            env.ptr('pointerup', env.root, 230, 120, { pointerId: 1 });
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 2 }]]);
        });

        it('Escape puts everything back and sends nothing, handled so djust leaves it alone', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false);
            const e = key(env.window, env.window.document.body, 'Escape');
            expect(e.defaultPrevented).toBe(true);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            env.ptr('pointerup', env.root, 230, 120);
            expect(env.sent).toEqual([]);
            expect(env.live()).toBe('Cancelled, cancelled');
        });

        it('Escape with no gesture is not ours', () => {
            const env = setup();
            expect(key(env.window, env.window.document.body, 'Escape').defaultPrevented).toBe(false);
        });

        it('a cancelled pointer (the browser took over) sends nothing', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false);
            env.ptr('pointercancel', env.root, 230, 120);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            env.ptr('pointerup', env.root, 230, 120);
            expect(env.sent).toEqual([]);
        });

        it('captures the pointer on the grid so a drag out of it is still followed', () => {
            const env = setup();
            const captured = [];
            env.root.setPointerCapture = (id) => captured.push(['set', id]);
            env.root.releasePointerCapture = (id) => captured.push(['release', id]);
            env.drag('b', 340, 10, 230, 120);
            expect(captured).toEqual([['set', 1], ['release', 1]]);
        });

        it('goes through the client\'s strict-parameter check', () => {
            const env = setup();
            env.window.djust._strictBinding = () => false;
            env.drag('b', 340, 10, 230, 120);
            expect(env.sent).toEqual([]);
            env.window.djust._strictBinding = () => ({ id: 'b', col: 2, row: 2 });
            env.drag('b', 340, 10, 230, 120);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 2 }]]);
        });

        it('no event name, no event', () => {
            const env = setup();
            env.root.removeAttribute('data-move-event');
            env.drag('b', 340, 10, 230, 120);
            expect(env.sent).toEqual([]);
        });

        it('uses the hook\'s own pushEvent when the client API is absent', () => {
            const env = setup();
            env.window.djust.handleEvent = undefined;
            const pushed = [];
            const entry = [...env.window.djust._activeHooks.values()].find((h) => h.instance && h.instance._cellsOf === undefined && h.instance._enhance);
            entry.instance.pushEvent = (name, params) => pushed.push([name, params]);
            env.drag('b', 340, 10, 230, 120);
            expect(pushed).toEqual([['dashboard_move', { id: 'b', col: 2, row: 2 }]]);
        });
    });

    describe('right-to-left and scaled grids', () => {
        it('a right-to-left grid is mirrored: dragging left of column 1 goes toward the last column', () => {
            const env = setup(PANELS, 4, { rtl: true });
            // Users is column 3 (visually second from the left): its inline-start edge is its right edge, x = 440 - 220 = 220... drag it to the visual far right = column 1
            const r = env.panel('b').getBoundingClientRect();
            env.ptr('pointerdown', env.header('b'), r.left + 10, 10);
            env.ptr('pointermove', env.root, r.left + 10 + 300, 10);
            env.ptr('pointerup', env.root, r.left + 10 + 300, 10);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 1, row: 1 }]]);
        });

        it('the same panel dragged to the visual left goes to the last column it fits', () => {
            const env = setup(PANELS, 4, { rtl: true });
            const r = env.panel('b').getBoundingClientRect();
            env.ptr('pointerdown', env.header('b'), r.left + 10, 10);
            env.ptr('pointermove', env.root, -500, 10);
            env.ptr('pointerup', env.root, -500, 10);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 4, row: 1 }]]);
        });

        it('resizing by the inline-end edge (the left one) widens toward the left', () => {
            const env = setup(PANELS, 4, { rtl: true });
            // Revenue: columns 1-2, i.e. visually the two rightmost; widen by dragging its left edge 110px further left
            const r = env.panel('a').getBoundingClientRect();
            env.ptr('pointerdown', env.bar('a'), r.left + 5, 100);
            env.ptr('pointermove', env.root, r.left + 5 - 110, 100);
            env.ptr('pointerup', env.root, r.left + 5 - 110, 100);
            expect(env.sent).toEqual([['dashboard_resize', { id: 'a', width: 3, height: 1 }]]);
        });

        it('the arrow keys point where they point: in a right-to-left grid Left moves toward the last column', () => {
            const env = setup(PANELS, 4, { rtl: true });
            const b = env.panel('b');
            b.focus();
            env.key(b, 'Enter');
            env.key(b, 'ArrowLeft');
            expect(env.live()).toBe('Users: column 4, row 1, 1 wide, 1 high');
            env.key(b, 'ArrowRight');
            env.key(b, 'ArrowRight');
            expect(env.live()).toBe('Users: column 2, row 1, 1 wide, 1 high');
            env.key(b, 'ArrowLeft', { shiftKey: true }); // Left grows (the end edge is on the left)
            expect(env.live()).toBe('Users: column 2, row 1, 2 wide, 1 high');
        });

        it('a grid drawn at half size is measured as drawn (the computed track sizes are layout pixels)', () => {
            const env = setup(PANELS, 4, { scale: 0.5, left: 100 });
            const r = env.panel('b').getBoundingClientRect();
            // Users is column 3: drag it one column left in the drawn grid (55 viewport px = 110 layout px)
            env.ptr('pointerdown', env.header('b'), r.left + 5, 10);
            env.ptr('pointermove', env.root, r.left + 5 - 55, 10);
            env.ptr('pointerup', env.root, r.left + 5 - 55, 10);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 1 }]]);
        });

        it('resizing in a scaled grid uses drawn pixels too', () => {
            const env = setup(PANELS, 4, { scale: 0.5, left: 100 });
            const r = env.panel('a').getBoundingClientRect(); // 2 wide
            env.ptr('pointerdown', env.bar('a'), r.right - 3, 40);
            env.ptr('pointermove', env.root, r.right - 3 + 55, 40);
            env.ptr('pointerup', env.root, r.right - 3 + 55, 40);
            expect(env.sent).toEqual([['dashboard_resize', { id: 'a', width: 3, height: 1 }]]);
        });
    });

    describe('resizing with the pointer', () => {
        it('dragging the bottom handle sends {id, width, height} in cells', () => {
            const env = setup();
            // Users: column 3, right edge at 320, bottom at 100; the edge goes to the end of column 4 and the end of row 2
            env.dragEdge('b', 320, 100, 430, 210);
            expect(env.sent).toEqual([['dashboard_resize', { id: 'b', width: 2, height: 2 }]]);
        });

        it('widens to the nearest column edge and clamps to the columns', () => {
            const env = setup();
            // panel a: cols 1-2, right edge at 210; drag its edge by +100 -> 310 -> end of column 3 -> 3 wide
            env.dragEdge('a', 200, 100, 300, 100);
            expect(env.sent).toEqual([['dashboard_resize', { id: 'a', width: 3, height: 1 }]]);
            env.sent.length = 0;
            env.dragEdge('a', 200, 100, 2000, 100);
            expect(env.sent).toEqual([['dashboard_resize', { id: 'a', width: 4, height: 1 }]]);
        });

        it('never narrower than one cell, never shorter than one row', () => {
            const env = setup();
            env.dragEdge('a', 200, 100, -2000, -2000);
            expect(env.sent).toEqual([['dashboard_resize', { id: 'a', width: 1, height: 1 }]]);
        });

        it('may grow one row below the last', () => {
            const env = setup();
            env.dragEdge('c', 100, 210, 100, 5000); // Errors: row 2, the last row
            expect(env.sent).toEqual([['dashboard_resize', { id: 'c', width: 1, height: 2 }]]);
        });

        it('a resize that changes nothing sends nothing; a move of the handle only sends the size', () => {
            const env = setup();
            env.dragEdge('a', 200, 100, 205, 105);
            expect(env.sent).toEqual([]);
        });

        it('shows the new extent with the dashed cell', () => {
            const env = setup();
            env.dragEdge('a', 200, 100, 300, 100, false);
            expect(env.root.getAttribute('data-dj-drop')).toBe('resize');
            const st = env.root.style;
            expect([st.getPropertyValue('--dj-drop-col'), st.getPropertyValue('--dj-drop-row'), st.getPropertyValue('--dj-drop-w'), st.getPropertyValue('--dj-drop-h')]).toEqual(['1', '1', '3', '1']);
        });

        it('the handle is not a move handle, and a header is not a resize handle', () => {
            const env = setup();
            env.dragEdge('b', 320, 100, 100, 130);
            expect(env.sent.every(([name]) => name === 'dashboard_resize')).toBe(true);
        });
    });

    describe('keyboard', () => {
        it('arrow keys move focus between panels by where they are', () => {
            const env = setup();
            env.panel('a').focus();
            env.key(env.panel('a'), 'ArrowRight');
            expect(env.window.document.activeElement).toBe(env.panel('b'));
            env.key(env.panel('b'), 'ArrowLeft');
            expect(env.window.document.activeElement).toBe(env.panel('a'));
            env.key(env.panel('a'), 'ArrowDown');
            expect(env.window.document.activeElement).toBe(env.panel('c'));
            env.key(env.panel('c'), 'ArrowUp');
            expect(env.window.document.activeElement).toBe(env.panel('a'));
            expect(env.key(env.panel('a'), 'ArrowUp').defaultPrevented).toBe(true); // nothing above: stays
            expect(env.window.document.activeElement).toBe(env.panel('a'));
        });

        it('Space grabs, arrow keys move, Enter drops and sends the move', () => {
            const env = setup();
            const b = env.panel('b');
            b.focus();
            expect(env.key(b, ' ').defaultPrevented).toBe(true);
            expect(env.live()).toContain('Users grabbed, column 3, row 1');
            expect(env.root.getAttribute('data-dj-drop')).toBe('move');
            env.key(b, 'ArrowDown');
            env.key(b, 'ArrowLeft');
            expect(env.live()).toBe('Users: column 2, row 2, 1 wide, 1 high');
            expect([env.root.style.getPropertyValue('--dj-drop-col'), env.root.style.getPropertyValue('--dj-drop-row')]).toEqual(['2', '2']);
            env.key(b, 'Enter');
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 2 }]]);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            expect(env.window.document.activeElement).toBe(b);
        });

        it('stays inside the grid: no column past the last, no row above the first, one row below the last', () => {
            const env = setup();
            const a = env.panel('a'); // 2 wide
            a.focus();
            env.key(a, 'Enter');
            for (let i = 0; i < 10; i++) env.key(a, 'ArrowRight');
            for (let i = 0; i < 10; i++) env.key(a, 'ArrowUp');
            for (let i = 0; i < 10; i++) env.key(a, 'ArrowDown');
            expect(env.live()).toBe('Revenue: column 3, row 3, 2 wide, 1 high');
            env.key(a, 'Enter');
            expect(env.sent).toEqual([['dashboard_move', { id: 'a', col: 3, row: 3 }]]);
        });

        it('Shift+arrows resize (Right/Down grow, Left/Up shrink) and drop sends the resize', () => {
            const env = setup();
            const b = env.panel('b');
            b.focus();
            env.key(b, ' ');
            env.key(b, 'ArrowDown', { shiftKey: true });
            env.key(b, 'ArrowRight', { shiftKey: true }); // already in the last column: stays 1 wide
            env.key(b, 'ArrowLeft', { shiftKey: true }); // not below 1
            expect(env.live()).toBe('Users: column 3, row 1, 1 wide, 2 high');
            env.key(b, ' ');
            expect(env.sent).toEqual([['dashboard_resize', { id: 'b', width: 1, height: 2 }]]);
        });

        it('a move and a resize in one grab send both events', () => {
            const env = setup();
            const c = env.panel('c');
            c.focus();
            env.key(c, 'Enter');
            env.key(c, 'ArrowRight');
            env.key(c, 'ArrowRight', { shiftKey: true });
            env.key(c, 'Enter');
            expect(env.sent).toEqual([
                ['dashboard_move', { id: 'c', col: 2, row: 2 }],
                ['dashboard_resize', { id: 'c', width: 2, height: 1 }],
            ]);
        });

        it('a drop that changed nothing sends nothing (and says so)', () => {
            const env = setup();
            const b = env.panel('b');
            b.focus();
            env.key(b, 'Enter');
            env.key(b, 'ArrowUp');
            env.key(b, 'Enter');
            expect(env.sent).toEqual([]);
            expect(env.live()).toBe('Users dropped where it was');
        });

        it('Escape cancels, handled, and sends nothing', () => {
            const env = setup();
            const b = env.panel('b');
            b.focus();
            env.key(b, 'Enter');
            env.key(b, 'ArrowDown');
            expect(env.key(b, 'Escape').defaultPrevented).toBe(true);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            env.key(b, 'Enter');
            env.key(b, 'Enter');
            expect(env.sent).toEqual([]);
        });

        it('Escape on a panel nobody grabbed is left alone', () => {
            const env = setup();
            env.panel('b').focus();
            expect(env.key(env.panel('b'), 'Escape').defaultPrevented).toBe(false);
        });

        it('leaving the grid cancels a grab', () => {
            const env = setup();
            const b = env.panel('b');
            b.focus();
            env.key(b, 'Enter');
            b.dispatchEvent(new env.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            env.key(b, 'Enter');
            expect(env.root.getAttribute('data-dj-drop')).toBe('move'); // a fresh grab
        });

        it('keys typed in a field inside a panel are not ours', () => {
            const env = setup([['a', 'Form', 1, 1, 1, 1, '<input class="f">']]);
            const input = env.$('.f');
            input.focus();
            expect(env.key(input, ' ').defaultPrevented).toBe(false);
            expect(env.key(input, 'ArrowRight').defaultPrevented).toBe(false);
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
        });

        it('modified keys are not ours', () => {
            const env = setup();
            env.panel('b').focus();
            expect(env.key(env.panel('b'), 'ArrowLeft', { ctrlKey: true }).defaultPrevented).toBe(false);
            expect(env.key(env.panel('b'), ' ', { metaKey: true }).defaultPrevented).toBe(false);
        });

        it('a pointer gesture in progress is not interrupted by a grab', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false);
            env.panel('a').focus();
            env.key(env.panel('a'), 'Enter');
            expect(env.root.getAttribute('data-dj-drop')).toBe('move');
            expect(env.root.style.getPropertyValue('--dj-drop-col')).toBe('2'); // still b's gesture
        });
    });

    describe('after the server re-renders', () => {
        it('follows a gesture by panel id when the patch shifts the nodes under it', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false); // dragging Users
            // the server drops Revenue: the nodes shift up, so node 0 now is Users
            const [n0, n1, n2] = env.$$('.dj-dashboard-grid__panel');
            n0.setAttribute('data-panel-id', 'b');
            n0.setAttribute('style', 'grid-column:3/span 1;grid-row:1/span 1');
            n1.setAttribute('data-panel-id', 'c');
            n1.setAttribute('style', 'grid-column:1/span 1;grid-row:2/span 1');
            env.root.removeChild(n2);
            env.window.djust.updateHooks();
            expect(env.$$('.dj-dashboard-grid__panel--dragging').map((p) => p.getAttribute('data-panel-id'))).toEqual(['b']);
            env.patchRects();
            env.ptr('pointerup', env.root, 230, 120);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 2 }]]);
        });

        it('cancels a gesture whose panel is gone', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false);
            env.root.removeChild(env.panel('b'));
            env.window.djust.updateHooks();
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            env.ptr('pointerup', env.root, 230, 120);
            expect(env.sent).toEqual([]);
        });

        it('cancels a keyboard grab whose panel is gone', () => {
            const env = setup();
            const b = env.panel('b');
            b.focus();
            env.key(b, 'Enter');
            env.root.removeChild(b);
            env.window.djust.updateHooks();
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
        });

        it('gives focus back to the panel that had it, by id', () => {
            const env = setup();
            env.panel('b').focus();
            env.window.djust.beforeUpdateHooks && env.window.djust.beforeUpdateHooks();
            const entry = [...env.window.djust._activeHooks.values()].find((h) => h.instance && h.instance._enhance);
            entry.instance.beforeUpdate();
            env.panel('b').blur();
            env.window.djust.updateHooks();
            expect(env.window.document.activeElement).toBe(env.panel('b'));
        });

        it('does not double-bind after repeated patches', () => {
            const env = setup();
            for (let i = 0; i < 5; i++) env.window.djust.updateHooks();
            env.drag('b', 340, 10, 230, 120);
            expect(env.sent).toHaveLength(1);
        });

        it('destroying the hook removes its listeners and any preview', () => {
            const env = setup();
            env.drag('b', 340, 10, 230, 120, false);
            env.window.djust.destroyAllHooks();
            expect(env.root.hasAttribute('data-dj-drop')).toBe(false);
            env.drag('b', 340, 10, 230, 120);
            expect(env.sent).toEqual([]);
            expect(key(env.window, env.window.document.body, 'Escape').defaultPrevented).toBe(false);
        });

        it('a panel with no readable position is treated as column 1, row 1, one cell (nothing throws)', () => {
            const env = setup([['a', 'A', 1, 1, 1, 1]]);
            env.panel('a').setAttribute('style', 'color:red');
            env.panel('a').getBoundingClientRect = () => ({ left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100 });
            env.drag('a', 10, 10, 340, 10);
            expect(env.sent).toEqual([['dashboard_move', { id: 'a', col: 4, row: 1 }]]);
        });

        it('with computed tracks that do not match the columns it falls back to equal columns', () => {
            const env = setup();
            const real = env.window.getComputedStyle;
            env.window.getComputedStyle = (el, ...rest) => (el === env.root ? { ...real(el, ...rest), gridTemplateColumns: 'none', gridTemplateRows: 'none', columnGap: 'normal', rowGap: 'normal', paddingLeft: '0px', paddingRight: '0px' } : real(el, ...rest));
            env.root.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200 });
            env.drag('b', 340, 10, 220, 10);
            expect(env.sent).toEqual([['dashboard_move', { id: 'b', col: 2, row: 1 }]]);
        });
    });
});
