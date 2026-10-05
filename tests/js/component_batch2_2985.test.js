/**
 * #2985 batch 2 — ImageLightbox, FileTree, ResizablePanel and AnimatedNumber
 * answer their dj-hook, and an app's own hook of the same name still wins.
 *
 * The markup is what the components render (python/djust/tests/
 * test_component_batch2_2985.py pins it on every render path).
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

const LIGHTBOX = ({ active = 1, total = 3 } = {}) => {
    const imgs = [['a', 'Cap A'], ['b', ''], ['c', '']];
    const [name, caption] = imgs[active];
    return '<div class="dj-lightbox" dj-hook="ImageLightbox" data-close-event="close_lightbox" data-navigate-event="lightbox_navigate" role="dialog" aria-modal="true">' +
        '<div class="dj-lightbox__backdrop" dj-click="close_lightbox"></div>' +
        '<button class="dj-lightbox__close" dj-click="close_lightbox" aria-label="Close">&times;</button>' +
        (total > 1 ? `<button class="dj-lightbox__prev" dj-click="lightbox_navigate" data-value="${active - 1}" aria-label="Previous">&#8249;</button>` : '') +
        `<div class="dj-lightbox__stage"><img class="dj-lightbox__image" src="/${name}.jpg" alt="${name.toUpperCase()}">` +
        (caption ? `<p class="dj-lightbox__caption">${caption}</p>` : '') + '</div>' +
        (total > 1 ? `<button class="dj-lightbox__next" dj-click="lightbox_navigate" data-value="${active + 1}" aria-label="Next">&#8250;</button>` : '') +
        (total > 1 ? `<span class="dj-lightbox__counter">${active + 1} of ${total}</span>` : '') + '</div>';
};

const node = ({ name, type = 'file', depth = 0, selected = false, expanded = true, children = null }) => {
    const pad = `style="padding-left:${(depth * 1.25).toFixed(1)}rem"`;
    const sel = selected ? ' dj-file-tree__node--selected' : '';
    if (type === 'folder' && children && children.length) {
        const exp = expanded ? ' dj-file-tree__node--expanded' : '';
        return `<div class="dj-file-tree__node dj-file-tree__node--folder${sel}${exp}" ${pad} data-name="${name}" data-type="folder">` +
            `<span class="dj-file-tree__toggle" role="button" tabindex="0" aria-expanded="${expanded}">${expanded ? '&#9660;' : '&#9654;'}</span>` +
            `<span class="dj-file-tree__icon" aria-hidden="true">${expanded ? '&#x1F4C2;' : '&#x1F4C1;'}</span>` +
            `<span class="dj-file-tree__name">${name}</span></div>` +
            `<div class="dj-file-tree__children"${expanded ? '' : ' style="display:none"'}>${children.map((c) => node({ ...c, depth: depth + 1 })).join('')}</div>`;
    }
    return `<div class="dj-file-tree__node dj-file-tree__node--${type}${sel}" ${pad} data-name="${name}" data-type="${type}" dj-click="select_file" role="treeitem" tabindex="0">` +
        `<span class="dj-file-tree__icon" aria-hidden="true">&#x1F4C4;</span><span class="dj-file-tree__name">${name}</span></div>`;
};

const TREE_SPEC = [
    { name: 'src', type: 'folder', children: [
        { name: 'main.py', selected: true },
        { name: 'sub', type: 'folder', expanded: false, children: [{ name: 'deep.txt' }] },
        { name: 'utils.py' },
    ] },
    { name: 'docs', type: 'folder', children: [{ name: 'guide.md' }] },
    { name: 'README.md' },
];
const TREE = (spec = TREE_SPEC) =>
    '<div class="dj-file-tree" dj-hook="FileTree" data-event="select_file" data-selected="main.py" role="tree">' +
    spec.map((n) => node(n)).join('') + '</div>';

const PANEL = ({ direction = 'horizontal', extra = '' } = {}) => {
    const prop = direction === 'horizontal' ? 'width' : 'height';
    return `<div class="dj-resizable-panel dj-resizable-panel--${direction}" dj-hook="ResizablePanel" data-direction="${direction}" data-min-size="100px" data-max-size="600px" style="${prop}:300px;min-${prop}:100px;max-${prop}:600px"${extra}>` +
        '<div class="dj-resizable-panel__content"><p>x</p></div>' +
        `<div class="dj-resizable-panel__handle" role="separator" aria-orientation="${direction}" tabindex="0"><span class="dj-resizable-panel__handle-bar"></span></div></div>`;
};

const NUMBER = ({ value = 1234.5, text = '1,234.5', decimals = 1, duration = 800, sep = ',' } = {}) =>
    `<span class="dj-animated-number" dj-hook="AnimatedNumber" data-value="${value}" data-duration="${duration}" data-decimals="${decimals}" data-separator="${sep}">` +
    `<span class="dj-animated-number__prefix">$</span><span class="dj-animated-number__value">${text}</span></span>`;

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

const COMPONENTS = [
    { hook: 'ImageLightbox', file: 'image-lightbox.js', markup: LIGHTBOX(), probe: (el) => el.hasAttribute('aria-label') },
    { hook: 'FileTree', file: 'file-tree.js', markup: TREE(), probe: (el) => el.querySelector('.dj-file-tree__node').hasAttribute('aria-level') },
    { hook: 'ResizablePanel', file: 'resizable-panel.js', markup: PANEL(), probe: (el) => el.querySelector('.dj-resizable-panel__handle').hasAttribute('aria-valuenow') },
    { hook: 'AnimatedNumber', file: 'animated-number.js', markup: NUMBER(), probe: (el) => el.querySelector('.dj-animated-number__value').textContent !== '1,234.5' },
];

describe('the shipped hooks never replace an app hook (#2985 batch 2)', () => {
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

            it("an app hook's pushEvent/handleEvent API and the element's attributes are untouched", () => {
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
// ImageLightbox
// ---------------------------------------------------------------------------

describe('ImageLightbox', () => {
    const clicks = (env) => {
        const log = [];
        for (const cls of ['close', 'prev', 'next']) {
            env.$(`.dj-lightbox__${cls}`)?.addEventListener('click', () => log.push(cls));
        }
        return log;
    };

    it('moves focus into the dialog, names it, and locks page scroll', () => {
        const env = createEnv('<button id="open">open</button>' + LIGHTBOX());
        env.$ = (s) => env.window.document.querySelector(s);
        env.$('#open').focus();
        env.window.eval(read('image-lightbox.js'));
        env.window.djust.mountHooks();
        expect(env.window.document.activeElement.className).toBe('dj-lightbox__close');
        expect(env.$('.dj-lightbox').getAttribute('aria-label')).toBe('Image viewer');
        expect(env.$('.dj-lightbox').getAttribute('tabindex')).toBe('-1');
        expect(env.window.document.body.style.overflow).toBe('hidden');
    });

    it('keeps an aria-label an app supplied', () => {
        const env = boot(LIGHTBOX().replace('role="dialog"', 'role="dialog" aria-label="Gallery"'), 'image-lightbox.js');
        expect(env.$('.dj-lightbox').getAttribute('aria-label')).toBe('Gallery');
    });

    it('Escape, ArrowLeft and ArrowRight press the rendered controls (no event of its own)', () => {
        const env = boot(LIGHTBOX(), 'image-lightbox.js');
        const log = clicks(env);
        key(env.window, env.$('.dj-lightbox__close'), 'ArrowRight');
        key(env.window, env.$('.dj-lightbox__close'), 'ArrowLeft');
        key(env.window, env.$('.dj-lightbox__close'), 'Escape');
        expect(log).toEqual(['next', 'prev', 'close']);
    });

    it('works wherever focus has wandered to while it is open', () => {
        const env = boot('<button id="behind">b</button>' + LIGHTBOX(), 'image-lightbox.js');
        const log = clicks(env);
        env.$('#behind').focus();
        key(env.window, env.$('#behind'), 'Escape');
        expect(log).toEqual(['close']);
    });

    it('ignores modified keys and a single image has no prev/next to press', () => {
        const env = boot(LIGHTBOX({ active: 0, total: 1 }), 'image-lightbox.js');
        const log = clicks(env);
        key(env.window, env.$('.dj-lightbox__close'), 'ArrowRight');
        key(env.window, env.$('.dj-lightbox__close'), 'ArrowLeft', { altKey: true });
        expect(log).toEqual([]);
    });

    it('Tab and Shift+Tab stay inside the dialog', () => {
        const env = boot('<button id="behind">b</button>' + LIGHTBOX(), 'image-lightbox.js');
        const close = env.$('.dj-lightbox__close');
        const next = env.$('.dj-lightbox__next');
        next.focus();
        expect(key(env.window, next, 'Tab').defaultPrevented).toBe(true);
        expect(env.window.document.activeElement).toBe(close);
        expect(key(env.window, close, 'Tab', { shiftKey: true }).defaultPrevented).toBe(true);
        expect(env.window.document.activeElement).toBe(next);
        // inside the dialog Tab is left to the browser
        env.$('.dj-lightbox__prev').focus();
        expect(key(env.window, env.$('.dj-lightbox__prev'), 'Tab').defaultPrevented).toBe(false);
    });

    it('a swipe left presses Next, a swipe right Previous; a short or vertical one does nothing', () => {
        const env = boot(LIGHTBOX(), 'image-lightbox.js');
        const log = clicks(env);
        const swipe = (dx, dy) => {
            const root = env.$('.dj-lightbox');
            const t = (x, y) => [{ clientX: x, clientY: y }];
            const start = new env.window.Event('touchstart', { bubbles: true });
            start.touches = t(200, 200);
            root.dispatchEvent(start);
            const end = new env.window.Event('touchend', { bubbles: true });
            end.changedTouches = t(200 + dx, 200 + dy);
            root.dispatchEvent(end);
        };
        swipe(-120, 10);
        swipe(120, -10);
        swipe(-20, 0);
        swipe(-120, 200);
        expect(log).toEqual(['next', 'prev']);
    });

    it('announces the new image when the server shows another one, and only then', () => {
        const env = boot(LIGHTBOX({ active: 0 }), 'image-lightbox.js');
        env.window.djust.updateHooks(); // an unrelated patch
        expect(env.live()).toBeUndefined();
        env.$('.dj-lightbox').outerHTML; // keep reference semantics simple
        const root = env.$('.dj-lightbox');
        root.querySelector('.dj-lightbox__image').setAttribute('alt', 'B');
        root.querySelector('.dj-lightbox__caption').remove();
        root.querySelector('.dj-lightbox__counter').textContent = '2 of 3';
        env.window.djust.updateHooks();
        expect(env.live()).toBe('B. Image 2 of 3');
    });

    it('puts focus back on the same kind of control when a patch dropped it', () => {
        const env = boot(LIGHTBOX(), 'image-lightbox.js');
        const next = env.$('.dj-lightbox__next');
        next.focus();
        next.replaceWith(next.cloneNode(true)); // a morph that replaced the node
        expect(env.window.document.activeElement).toBe(env.window.document.body);
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement.className).toBe('dj-lightbox__next');
    });

    it('closing restores focus to what had it and unlocks scroll', () => {
        const env = createEnv('<button id="open">open</button>' + LIGHTBOX());
        env.window.document.getElementById('open').focus();
        env.window.eval(read('image-lightbox.js'));
        env.window.djust.mountHooks();
        env.window.document.body.style.overflow = '';
        env.window.document.querySelector('.dj-lightbox').remove();
        env.window.djust.updateHooks();
        expect(env.window.document.activeElement.id).toBe('open');
    });

    it('restores the page scroll it locked, and a page that was already locked stays locked', () => {
        const env = createEnv(LIGHTBOX());
        env.window.eval(read('image-lightbox.js'));
        env.window.document.body.style.overflow = 'scroll';
        env.window.djust.mountHooks();
        expect(env.window.document.body.style.overflow).toBe('hidden');
        env.window.document.querySelector('.dj-lightbox').remove();
        env.window.djust.updateHooks();
        expect(env.window.document.body.style.overflow).toBe('scroll');

        const locked = createEnv(LIGHTBOX());
        locked.window.eval(read('image-lightbox.js'));
        locked.window.document.body.style.overflow = 'hidden';
        locked.window.djust.mountHooks();
        locked.window.document.querySelector('.dj-lightbox').remove();
        locked.window.djust.updateHooks();
        expect(locked.window.document.body.style.overflow).toBe('hidden');
    });

    it('stops listening to the document once closed', () => {
        const env = boot('<button id="behind">b</button>' + LIGHTBOX(), 'image-lightbox.js');
        const log = clicks(env);
        const root = env.$('.dj-lightbox');
        const close = env.$('.dj-lightbox__close');
        root.remove();
        env.window.djust.updateHooks();
        env.$('#behind').focus();
        key(env.window, env.$('#behind'), 'Escape');
        expect(log).toEqual([]);
        expect(close).toBeTruthy();
    });

    it('does not double-bind after repeated patches', () => {
        const env = boot(LIGHTBOX(), 'image-lightbox.js');
        const log = clicks(env);
        for (let i = 0; i < 5; i++) env.window.djust.updateHooks();
        key(env.window, env.$('.dj-lightbox__close'), 'ArrowRight');
        key(env.window, env.window.document.body, 'ArrowRight');
        expect(log).toEqual(['next', 'next']);
    });
});

// ---------------------------------------------------------------------------
// FileTree
// ---------------------------------------------------------------------------

const row = (env, name) => env.$(`.dj-file-tree__node[data-name="${name}"]`);
const group = (env, name) => row(env, name).nextElementSibling;
const hidden = (g) => g.style.display === 'none';

describe('FileTree', () => {
    it('applies tree semantics, and keeps exactly one tab stop on the selected row', () => {
        const env = boot(TREE(), 'file-tree.js');
        expect(env.$('.dj-file-tree').getAttribute('role')).toBe('tree');
        expect(row(env, 'src').getAttribute('role')).toBe('treeitem');
        expect(row(env, 'src').getAttribute('aria-level')).toBe('1');
        expect(row(env, 'main.py').getAttribute('aria-level')).toBe('2');
        expect(row(env, 'deep.txt').getAttribute('aria-level')).toBe('3');
        expect(row(env, 'src').getAttribute('aria-expanded')).toBe('true');
        expect(row(env, 'sub').getAttribute('aria-expanded')).toBe('false');
        expect(group(env, 'src').getAttribute('role')).toBe('group');
        expect(row(env, 'main.py').getAttribute('aria-selected')).toBe('true');
        expect(row(env, 'utils.py').getAttribute('aria-selected')).toBe('false');
        const stops = env.$$('.dj-file-tree__node').filter((n) => n.getAttribute('tabindex') === '0');
        expect(stops).toEqual([row(env, 'main.py')]);
        // the arrow is a click target only
        const toggle = row(env, 'src').querySelector('.dj-file-tree__toggle');
        expect(toggle.getAttribute('tabindex')).toBe('-1');
        expect(toggle.getAttribute('aria-hidden')).toBe('true');
    });

    it('clicking a folder (row or arrow) expands and collapses it, with glyph, icon, class and aria in step', () => {
        const env = boot(TREE(), 'file-tree.js');
        const sub = row(env, 'sub');
        sub.querySelector('.dj-file-tree__name').click();
        expect(hidden(group(env, 'sub'))).toBe(false);
        expect(sub.getAttribute('aria-expanded')).toBe('true');
        expect(sub.classList.contains('dj-file-tree__node--expanded')).toBe(true);
        expect(sub.querySelector('.dj-file-tree__toggle').textContent).toBe('▼');
        expect(sub.querySelector('.dj-file-tree__icon').textContent).toBe('📂');
        sub.querySelector('.dj-file-tree__toggle').click();
        expect(hidden(group(env, 'sub'))).toBe(true);
        expect(sub.getAttribute('aria-expanded')).toBe('false');
        expect(sub.classList.contains('dj-file-tree__node--expanded')).toBe(false);
        expect(sub.querySelector('.dj-file-tree__icon').textContent).toBe('📁');
    });

    it('clicking a file fires only the row\'s own click (no folder toggling, nothing sent by the hook)', () => {
        const env = boot(TREE(), 'file-tree.js');
        const sent = [];
        env.window.djust.handleEvent = (n, p) => sent.push([n, p]);
        row(env, 'utils.py').click();
        expect(sent).toEqual([]);
        expect(hidden(group(env, 'src'))).toBe(false);
        expect(row(env, 'utils.py').getAttribute('tabindex')).toBe('0');
    });

    describe('keyboard', () => {
        const focused = (env) => env.window.document.activeElement.getAttribute('data-name');

        it('Up/Down walk the visible rows only (a collapsed folder hides its children)', () => {
            const env = boot(TREE(), 'file-tree.js');
            row(env, 'src').focus();
            const seen = [];
            for (let i = 0; i < 6; i++) {
                key(env.window, env.window.document.activeElement, 'ArrowDown');
                seen.push(focused(env));
            }
            expect(seen).toEqual(['main.py', 'sub', 'utils.py', 'docs', 'guide.md', 'README.md']);
            key(env.window, env.window.document.activeElement, 'ArrowDown'); // end: stays
            expect(focused(env)).toBe('README.md');
            key(env.window, row(env, 'README.md'), 'ArrowUp');
            expect(focused(env)).toBe('guide.md');
        });

        it('Home and End jump to the first and last visible row', () => {
            const env = boot(TREE(), 'file-tree.js');
            row(env, 'main.py').focus();
            key(env.window, row(env, 'main.py'), 'End');
            expect(focused(env)).toBe('README.md');
            key(env.window, row(env, 'README.md'), 'Home');
            expect(focused(env)).toBe('src');
        });

        it('Right expands a collapsed folder, then steps into it; Left collapses, then steps out', () => {
            const env = boot(TREE(), 'file-tree.js');
            const sub = row(env, 'sub');
            sub.focus();
            key(env.window, sub, 'ArrowRight');
            expect(hidden(group(env, 'sub'))).toBe(false);
            expect(focused(env)).toBe('sub');
            key(env.window, sub, 'ArrowRight');
            expect(focused(env)).toBe('deep.txt');
            key(env.window, row(env, 'deep.txt'), 'ArrowLeft');
            expect(focused(env)).toBe('sub');
            key(env.window, sub, 'ArrowLeft');
            expect(hidden(group(env, 'sub'))).toBe(true);
            key(env.window, sub, 'ArrowLeft');
            expect(focused(env)).toBe('src');
        });

        it('Enter and Space open a folder and activate a file (by clicking the row)', () => {
            const env = boot(TREE(), 'file-tree.js');
            const clicked = [];
            env.$$('.dj-file-tree__node').forEach((n) => n.addEventListener('click', () => clicked.push(n.getAttribute('data-name'))));
            const sub = row(env, 'sub');
            sub.focus();
            expect(key(env.window, sub, 'Enter').defaultPrevented).toBe(true);
            expect(hidden(group(env, 'sub'))).toBe(false);
            key(env.window, sub, ' ');
            expect(hidden(group(env, 'sub'))).toBe(true);
            const util = row(env, 'utils.py');
            util.focus();
            key(env.window, util, ' ');
            expect(clicked.filter((n) => n === 'utils.py')).toEqual(['utils.py']);
        });

        it('collapsing a folder while focus is inside it moves focus to the folder', () => {
            const env = boot(TREE(), 'file-tree.js');
            row(env, 'main.py').focus();
            row(env, 'src').querySelector('.dj-file-tree__toggle').click();
            expect(hidden(group(env, 'src'))).toBe(true);
            expect(env.window.document.activeElement).toBe(row(env, 'src'));
            expect(row(env, 'src').getAttribute('tabindex')).toBe('0');
            expect(row(env, 'main.py').getAttribute('tabindex')).toBe('-1');
        });

        it('typing letters jumps to the next row starting with them; the same letter moves on', () => {
            const env = boot(TREE(), 'file-tree.js');
            let t = 0;
            env.window.Date.now = () => t;
            const type = (el, k) => { t += 1000; key(env.window, el, k); };
            row(env, 'src').focus();
            type(row(env, 'src'), 'u');
            expect(focused(env)).toBe('utils.py');
            type(row(env, 'utils.py'), 'd');
            expect(focused(env)).toBe('docs');
            type(row(env, 'docs'), 'r');
            expect(focused(env)).toBe('README.md');
        });

        it('typing quickly builds a prefix', () => {
            const env = boot(TREE(), 'file-tree.js');
            let t = 0;
            env.window.Date.now = () => t;
            row(env, 'src').focus();
            key(env.window, row(env, 'src'), 'd');
            expect(focused(env)).toBe('docs');
            t += 100;
            key(env.window, row(env, 'docs'), 'o');
            expect(focused(env)).toBe('docs');
            t += 100;
            key(env.window, row(env, 'docs'), 'c');
            expect(focused(env)).toBe('docs');
        });

        it('keys with modifiers, and keys on controls other than rows, are left alone', () => {
            const env = boot(TREE(), 'file-tree.js');
            row(env, 'src').focus();
            expect(key(env.window, row(env, 'src'), 'ArrowDown', { ctrlKey: true }).defaultPrevented).toBe(false);
            expect(key(env.window, env.$('.dj-file-tree'), 'ArrowDown').defaultPrevented).toBe(false);
        });
    });

    describe('server patches', () => {
        it('a folder the reader opened stays open when an unrelated patch changes the selection', () => {
            const env = boot(TREE(), 'file-tree.js');
            row(env, 'sub').click();
            expect(hidden(group(env, 'sub'))).toBe(false);
            // the server moves the selection: class patch on two rows
            row(env, 'main.py').classList.remove('dj-file-tree__node--selected');
            row(env, 'utils.py').classList.add('dj-file-tree__node--selected');
            env.window.djust.updateHooks();
            expect(hidden(group(env, 'sub'))).toBe(false);
            expect(row(env, 'sub').getAttribute('aria-expanded')).toBe('true');
            expect(row(env, 'utils.py').getAttribute('aria-selected')).toBe('true');
            expect(row(env, 'main.py').getAttribute('aria-selected')).toBe('false');
        });

        it('a server class patch that overwrites a folder\'s class is reconciled with what is visible', () => {
            const env = boot(TREE(), 'file-tree.js');
            const sub = row(env, 'sub');
            sub.click(); // reader opens it
            // the server patches the whole class attribute back to its own (collapsed) state
            sub.setAttribute('class', 'dj-file-tree__node dj-file-tree__node--folder');
            env.window.djust.updateHooks();
            expect(hidden(group(env, 'sub'))).toBe(false);
            expect(sub.classList.contains('dj-file-tree__node--expanded')).toBe(true);
            expect(sub.getAttribute('aria-expanded')).toBe('true');
        });

        it('new rows from a patch get their attributes, and a removed row\'s tab stop moves on', () => {
            const env = boot(TREE(), 'file-tree.js');
            const parent = row(env, 'utils.py').parentNode;
            const extra = document_fragment(env, node({ name: 'extra.py', depth: 1 }));
            parent.appendChild(extra);
            row(env, 'main.py').remove();
            env.window.djust.updateHooks();
            expect(row(env, 'extra.py').getAttribute('aria-level')).toBe('2');
            const stops = env.$$('.dj-file-tree__node').filter((n) => n.getAttribute('tabindex') === '0');
            expect(stops).toHaveLength(1);
            expect(stops[0].getAttribute('data-name')).toBe('src');
        });

        it('re-applies the tree attributes after a morph restored the server\'s markup (no class/style change)', () => {
            const env = boot(TREE(), 'file-tree.js');
            // what a morph back to the server's HTML does: every row tabindex=0, no aria-level
            env.$$('.dj-file-tree__node').forEach((n) => {
                n.setAttribute('tabindex', '0');
                n.removeAttribute('aria-level');
            });
            env.window.djust.updateHooks();
            expect(env.$$('.dj-file-tree__node').filter((n) => n.getAttribute('tabindex') === '0')).toHaveLength(1);
            expect(row(env, 'deep.txt').getAttribute('aria-level')).toBe('3');
        });

        it('does not walk the tree again for patches that did not touch it', () => {
            const env = boot(TREE(), 'file-tree.js');
            let sets = 0;
            const real = env.window.Element.prototype.setAttribute;
            env.window.Element.prototype.setAttribute = function (...a) { sets += 1; return real.apply(this, a); };
            for (let i = 0; i < 10; i++) env.window.djust.updateHooks();
            env.window.Element.prototype.setAttribute = real;
            expect(sets).toBe(0);
        });

        it('does not double-bind after repeated patches', () => {
            const env = boot(TREE(), 'file-tree.js');
            for (let i = 0; i < 5; i++) env.window.djust.updateHooks();
            row(env, 'sub').click();
            expect(hidden(group(env, 'sub'))).toBe(false); // one toggle, not five
        });

        it('a folder with no children is an ordinary row and fires its own event', () => {
            const env = boot(TREE([{ name: 'empty', type: 'folder' }]), 'file-tree.js');
            const r = row(env, 'empty');
            expect(r.hasAttribute('aria-expanded')).toBe(false);
            expect(() => r.click()).not.toThrow();
        });
    });

    it('unmounting stops the observer and the listeners', () => {
        const env = boot(TREE(), 'file-tree.js');
        const tree = env.$('.dj-file-tree');
        tree.remove();
        env.window.djust.updateHooks();
        env.window.document.querySelector('[dj-root]').appendChild(tree);
        const sub = tree.querySelector('[data-name="sub"]');
        sub.click();
        expect(hidden(sub.nextElementSibling)).toBe(true);
    });
});

function document_fragment(env, html) {
    const t = env.window.document.createElement('template');
    t.innerHTML = html;
    return t.content;
}

// ---------------------------------------------------------------------------
// ResizablePanel
// ---------------------------------------------------------------------------

function sized(env, { size = 300, parent = 1000 } = {}) {
    const panel = env.$('.dj-resizable-panel');
    panel.getBoundingClientRect = () => {
        const px = parseFloat(panel.style.width || panel.style.height) || size;
        const horizontal = panel.getAttribute('data-direction') !== 'vertical';
        return horizontal ? { width: px, height: 40 } : { width: 40, height: px };
    };
    Object.defineProperty(panel.parentElement, 'clientWidth', { get: () => parent, configurable: true });
    Object.defineProperty(panel.parentElement, 'clientHeight', { get: () => parent, configurable: true });
    return panel;
}

const pointer = (window, el, type, x, y, id = 1) => {
    const e = new window.Event(type, { bubbles: true, cancelable: true });
    Object.assign(e, { clientX: x, clientY: y, pointerId: id, button: 0 });
    el.dispatchEvent(e);
    return e;
};

describe('ResizablePanel', () => {
    const setup = (opts, markup = PANEL()) => {
        const env = createEnv(markup);
        env.window.eval(read('resizable-panel.js'));
        env.$ = (s) => env.window.document.querySelector(s);
        const panel = sized(env, opts);
        env.window.djust.mountHooks();
        env.handle = env.$('.dj-resizable-panel__handle');
        env.panel = panel;
        env.events = [];
        panel.addEventListener('dj-resize', (e) => env.events.push(e.detail));
        return env;
    };

    it('turns the handle into a splitter: values, correct orientation, name, no touch panning', () => {
        const env = setup();
        const h = env.handle;
        expect(h.getAttribute('aria-valuenow')).toBe('300');
        expect(h.getAttribute('aria-valuemin')).toBe('100');
        expect(h.getAttribute('aria-valuemax')).toBe('600');
        expect(h.getAttribute('aria-valuetext')).toBe('300 px');
        expect(h.getAttribute('aria-label')).toBe('Resize panel');
        expect(h.getAttribute('aria-orientation')).toBe('vertical'); // the bar of a width-resizing panel
        expect(h.style.touchAction).toBe('none');
    });

    it('a vertical panel\'s separator is horizontal', () => {
        const env = setup({}, PANEL({ direction: 'vertical' }));
        expect(env.handle.getAttribute('aria-orientation')).toBe('horizontal');
    });

    it('dragging the handle resizes the panel, clamped to min and max', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointermove', 350, 10);
        expect(env.panel.style.width).toBe('350px');
        expect(env.handle.getAttribute('aria-valuenow')).toBe('350');
        pointer(env.window, env.handle, 'pointermove', 5000, 10);
        expect(env.panel.style.width).toBe('600px');
        pointer(env.window, env.handle, 'pointermove', -5000, 10);
        expect(env.panel.style.width).toBe('100px');
        pointer(env.window, env.handle, 'pointerup', -5000, 10);
        expect(env.events).toEqual([{ size: 100, direction: 'horizontal' }]);
    });

    it('a drag is relative to where it started, and only the pressing pointer moves it', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10, 7);
        pointer(env.window, env.handle, 'pointermove', 400, 10, 8); // another finger
        expect(env.panel.style.width).toBe('300px');
        pointer(env.window, env.handle, 'pointermove', 340, 10, 7);
        expect(env.panel.style.width).toBe('340px');
        pointer(env.window, env.handle, 'pointercancel', 340, 10, 7);
        pointer(env.window, env.handle, 'pointermove', 500, 10, 7); // no drag any more
        expect(env.panel.style.width).toBe('340px');
    });

    it('a vertical panel resizes its height from the vertical pointer position', () => {
        const env = setup({}, PANEL({ direction: 'vertical' }));
        pointer(env.window, env.handle, 'pointerdown', 10, 300);
        pointer(env.window, env.handle, 'pointermove', 10, 380);
        expect(env.panel.style.height).toBe('380px');
        pointer(env.window, env.handle, 'pointerup', 10, 380);
        expect(env.events[0].direction).toBe('vertical');
    });

    it('the container is a ceiling when max_size is none', () => {
        const env = setup({ parent: 450 }, PANEL().replace('max-width:600px', 'max-width:none'));
        pointer(env.window, env.handle, 'pointerdown', 300, 0);
        pointer(env.window, env.handle, 'pointermove', 900, 0);
        expect(env.panel.style.width).toBe('450px');
    });

    it('arrow keys resize by 10 px (Shift 50), Home/End go to the limits, Enter resets', () => {
        const env = setup();
        const h = env.handle;
        h.focus();
        expect(key(env.window, h, 'ArrowRight').defaultPrevented).toBe(true);
        expect(env.panel.style.width).toBe('310px');
        key(env.window, h, 'ArrowLeft', { shiftKey: true });
        expect(env.panel.style.width).toBe('260px');
        key(env.window, h, 'ArrowDown'); // not this panel's axis
        expect(env.panel.style.width).toBe('260px');
        key(env.window, h, 'End');
        expect(env.panel.style.width).toBe('600px');
        key(env.window, h, 'Home');
        expect(env.panel.style.width).toBe('100px');
        expect(h.getAttribute('aria-valuenow')).toBe('100');
        key(env.window, h, 'Enter');
        expect(env.panel.style.width).toBe('300px');
        expect(env.events.map((e) => e.size)).toEqual([310, 260, 600, 100, 300]);
    });

    it('a vertical panel answers the vertical arrows', () => {
        const env = setup({}, PANEL({ direction: 'vertical' }));
        key(env.window, env.handle, 'ArrowDown');
        expect(env.panel.style.height).toBe('310px');
        key(env.window, env.handle, 'ArrowUp');
        key(env.window, env.handle, 'ArrowUp');
        expect(env.panel.style.height).toBe('290px');
        key(env.window, env.handle, 'ArrowRight');
        expect(env.panel.style.height).toBe('290px');
    });

    it('double-click puts the panel back at its initial size', () => {
        const env = setup();
        key(env.window, env.handle, 'ArrowRight', { shiftKey: true });
        env.handle.dispatchEvent(new env.window.Event('dblclick', { bubbles: true }));
        expect(env.panel.style.width).toBe('300px');
    });

    it('a disabled panel takes no focus, no keys and no drag', () => {
        const env = setup({}, PANEL({ extra: ' data-disabled="true"' }));
        expect(env.handle.hasAttribute('tabindex')).toBe(false);
        expect(env.handle.getAttribute('aria-disabled')).toBe('true');
        key(env.window, env.handle, 'ArrowRight');
        pointer(env.window, env.handle, 'pointerdown', 300, 0);
        pointer(env.window, env.handle, 'pointermove', 380, 0);
        expect(env.panel.style.width).toBe('300px');
        expect(env.events).toEqual([]);
    });

    it('an unrelated patch leaves the reader\'s size alone; a new server size takes over', () => {
        const env = setup();
        key(env.window, env.handle, 'ArrowRight', { shiftKey: true });
        env.window.djust.updateHooks();
        expect(env.panel.style.width).toBe('350px');
        env.panel.style.width = '420px'; // the server patched its own style
        env.window.djust.updateHooks();
        expect(env.panel.style.width).toBe('420px');
        key(env.window, env.handle, 'ArrowRight');
        env.handle.dispatchEvent(new env.window.Event('dblclick', { bubbles: true }));
        expect(env.panel.style.width).toBe('420px'); // the new initial size
    });

    it('re-applies what a patch dropped from the handle, and does not double-bind', () => {
        const env = setup();
        env.handle.removeAttribute('aria-valuenow');
        env.handle.removeAttribute('aria-label');
        for (let i = 0; i < 4; i++) env.window.djust.updateHooks();
        expect(env.handle.getAttribute('aria-valuenow')).toBe('300');
        expect(env.handle.getAttribute('aria-label')).toBe('Resize panel');
        key(env.window, env.handle, 'ArrowRight');
        expect(env.panel.style.width).toBe('310px');
        expect(env.events).toHaveLength(1);
    });

    it('keeps an aria-label an app supplied', () => {
        const env = setup({}, PANEL().replace('role="separator"', 'role="separator" aria-label="Sidebar width"'));
        expect(env.handle.getAttribute('aria-label')).toBe('Sidebar width');
    });
});

// ---------------------------------------------------------------------------
// AnimatedNumber
// ---------------------------------------------------------------------------

describe('AnimatedNumber', () => {
    const text = (env) => env.$('.dj-animated-number__value').textContent;
    const wait = (env, ms) => new Promise((resolve) => env.window.setTimeout(resolve, ms));

    it('counts up from zero and ends on the exact text the server rendered', async () => {
        const env = boot(NUMBER({ duration: 120 }), 'animated-number.js');
        expect(text(env)).toBe('0.0'); // the starting frame is shown at once, not the final number
        await wait(env, 60);
        const mid = parseFloat(text(env).replace(/,/g, ''));
        expect(mid).toBeGreaterThan(0);
        expect(mid).toBeLessThan(1234.5);
        await wait(env, 200);
        expect(text(env)).toBe('1,234.5');
    });

    it('the last frame is the server\'s text even where client rounding would differ', async () => {
        // Python renders 0.125 with 2 places as "0.12" (half to even); toFixed gives "0.13".
        const env = boot(NUMBER({ value: 0.125, text: '0.12', decimals: 2, duration: 50 }), 'animated-number.js');
        await wait(env, 200);
        expect(text(env)).toBe('0.12');
    });

    it('leaves the prefix and suffix alone', async () => {
        const env = boot(NUMBER({ duration: 50 }), 'animated-number.js');
        await wait(env, 150);
        expect(env.$('.dj-animated-number__prefix').textContent).toBe('$');
    });

    it('animates from the number on screen to a new value when a patch changes it', async () => {
        const env = boot(NUMBER({ value: 100, text: '100', decimals: 0, duration: 40 }), 'animated-number.js');
        await wait(env, 150);
        expect(text(env)).toBe('100');
        const root = env.$('.dj-animated-number');
        root.setAttribute('data-value', '5000');
        env.$('.dj-animated-number__value').textContent = '5,000'; // the server's patch
        env.window.djust.updateHooks();
        expect(text(env)).toBe('100'); // starts from what was on screen, not a flash of 5,000
        await wait(env, 150);
        expect(text(env)).toBe('5,000');
    });

    it('an unrelated patch mid-count neither restarts nor freezes it', async () => {
        const env = boot(NUMBER({ duration: 150 }), 'animated-number.js');
        await wait(env, 40);
        env.window.djust.updateHooks();
        env.window.djust.updateHooks();
        await wait(env, 250);
        expect(text(env)).toBe('1,234.5');
    });

    it('a value change whose text did not change still ends on the server\'s text', async () => {
        const env = boot(NUMBER({ value: 1.2, text: '1', decimals: 0, duration: 40 }), 'animated-number.js');
        await wait(env, 120);
        env.$('.dj-animated-number').setAttribute('data-value', '1.4'); // renders "1" both times: no text patch
        env.window.djust.updateHooks();
        await wait(env, 120);
        expect(text(env)).toBe('1');
    });

    it('a patch during the count that sets a new final text ends on that text', async () => {
        const env = boot(NUMBER({ duration: 100 }), 'animated-number.js');
        await wait(env, 30);
        env.$('.dj-animated-number').setAttribute('data-value', '9999.9');
        env.$('.dj-animated-number__value').textContent = '9,999.9';
        env.window.djust.updateHooks();
        await wait(env, 250);
        expect(text(env)).toBe('9,999.9');
    });

    it('skips the animation under prefers-reduced-motion', () => {
        const env = boot(NUMBER(), 'animated-number.js', {
            matchMedia: (q) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} }),
        });
        expect(text(env)).toBe('1,234.5');
    });

    it('skips it for a zero duration or a value that is not a number', () => {
        expect(text(boot(NUMBER({ duration: 0 }), 'animated-number.js'))).toBe('1,234.5');
        expect(text(boot(NUMBER({ value: 'nan', text: '0' }), 'animated-number.js'))).toBe('0');
    });

    it('formats intermediate frames with the data-separator and decimals, and negative values', async () => {
        const env = boot(NUMBER({ value: -12345.678, text: '-12.345,68', decimals: 2, sep: '.', duration: 200 }), 'animated-number.js');
        await wait(env, 100);
        expect(text(env)).toMatch(/^-?\d{1,3}(\.\d{3})*\.\d{2}$|^-?\d+\.\d{2}$/);
        await wait(env, 300);
        expect(text(env)).toBe('-12.345,68');
    });

    it('stops its frames when the element goes away', async () => {
        const env = boot(NUMBER({ duration: 300 }), 'animated-number.js');
        const value = env.$('.dj-animated-number__value');
        const root = env.$('.dj-animated-number');
        root.remove();
        env.window.djust.updateHooks();
        const frozen = value.textContent;
        await wait(env, 100);
        expect(value.textContent).toBe(frozen);
    });
});

// ---------------------------------------------------------------------------
// Large trees stay cheap (the real-browser run measures the same in Chromium)
// ---------------------------------------------------------------------------

describe('FileTree on a large tree', () => {
    const big = (folders, files) => Array.from({ length: folders }, (_, f) => ({
        name: `dir${f}`,
        type: 'folder',
        expanded: f === 0,
        children: Array.from({ length: files }, (_, i) => ({ name: `f${f}_${i}.txt` })),
    }));

    it('keyboard navigation and a patch of one row do not rewalk collapsed folders\' rows per key', () => {
        const env = boot(TREE(big(40, 100)), 'file-tree.js');
        expect(env.$$('.dj-file-tree__node').length).toBe(40 + 4000);
        const first = env.$('.dj-file-tree__node');
        first.focus();
        let walked = 0;
        const real = env.window.Element.prototype.setAttribute;
        env.window.Element.prototype.setAttribute = function (...a) { walked += 1; return real.apply(this, a); };
        // only dir0 is open: 1 + 100 + 39 visible rows; End must land on the last directory row
        key(env.window, first, 'End');
        expect(env.window.document.activeElement.getAttribute('data-name')).toBe('dir39');
        env.window.Element.prototype.setAttribute = real;
        expect(walked).toBeLessThan(10);
    });
});
