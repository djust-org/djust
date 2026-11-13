/**
 * #2985 batch 2 — ImageLightbox and ResizablePanel
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
    env.initialHtml = markup;
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

const PANEL = ({ direction = 'horizontal', extra = '' } = {}) => {
    const prop = direction === 'horizontal' ? 'width' : 'height';
    return `<div class="dj-resizable-panel dj-resizable-panel--${direction}" dj-hook="ResizablePanel" data-direction="${direction}" data-min-size="100px" data-max-size="600px" style="${prop}:300px;min-${prop}:100px;max-${prop}:600px"${extra}>` +
        '<div class="dj-resizable-panel__content"><p>x</p></div>' +
        `<div class="dj-resizable-panel__handle" role="separator" aria-orientation="${direction}" tabindex="0"><span class="dj-resizable-panel__handle-bar"></span></div></div>`;
};

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

const COMPONENTS = [
    { hook: 'ImageLightbox', file: 'image-lightbox.js', markup: LIGHTBOX(), probe: (el) => el.hasAttribute('aria-label') },
    { hook: 'ResizablePanel', file: 'resizable-panel.js', markup: PANEL(), probe: (el) => el.querySelector('.dj-resizable-panel__handle').hasAttribute('aria-valuenow') },
];

describe('the shipped hooks never replace an app hook (#2985 batch 2)', () => {
    for (const c of COMPONENTS) {
        describe(c.hook, () => {
            it('registers a hook, so mounting logs no "No hook registered"', () => {
                const env = boot(c.markup, c.file);
                expect(typeof env.window.DjustHooks?.[c.hook]?.mounted).toBe('function');
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
                expect(env.window.DjustHooks?.[c.hook]).toBeUndefined();
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

    it('two lightboxes share one scroll lock: it is given back only when the last closes', () => {
        const env = createEnv(LIGHTBOX() + LIGHTBOX({ active: 0 }));
        env.window.eval(read('image-lightbox.js'));
        env.window.document.body.style.overflow = 'auto';
        env.window.djust.mountHooks();
        const [first, second] = Array.from(env.window.document.querySelectorAll('.dj-lightbox'));
        expect(env.window.document.body.style.overflow).toBe('hidden');
        first.remove();
        env.window.djust.updateHooks();
        expect(env.window.document.body.style.overflow).toBe('hidden'); // the second is still open
        second.remove();
        env.window.djust.updateHooks();
        expect(env.window.document.body.style.overflow).toBe('auto');
    });

    it('Escape belongs to the top dialog while a wrapping Tab may propagate', () => {
        const env = boot('<button id="behind">b</button>' + LIGHTBOX(), 'image-lightbox.js');
        const reached = [];
        env.window.document.addEventListener('keydown', (e) => reached.push([e.key, e.defaultPrevented]));
        key(env.window, env.$('.dj-lightbox__close'), 'Escape');
        const next = env.$('.dj-lightbox__next');
        next.focus();
        key(env.window, next, 'Tab'); // wraps
        env.$('.dj-lightbox__prev').focus();
        key(env.window, env.$('.dj-lightbox__prev'), 'Tab'); // inside: left to the browser (and core)
        expect(reached).toEqual([['Tab', true], ['Tab', false]]);
    });

    it('does not deliver consumed Escape to ancestor or window shortcut listeners', () => {
        const env = boot('<section id="outer">' + LIGHTBOX() + '</section>', 'image-lightbox.js');
        const close = clicks(env);
        const outer = vi.fn();
        const shortcut = vi.fn();
        env.$('#outer').addEventListener('keydown', outer);
        env.window.addEventListener('keydown', shortcut);
        key(env.window, env.$('.dj-lightbox__close'), 'Escape');
        expect(close).toEqual(['close']);
        expect(outer).not.toHaveBeenCalled();
        expect(shortcut).not.toHaveBeenCalled();
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

    describe('right to left (the handle is on the panel\'s left edge)', () => {
        const rtl = () => setup({}, PANEL().replace('style="width:300px', 'style="direction:rtl;width:300px'));

        it('dragging outward (to the left) grows the panel, dragging inward shrinks it', () => {
            const env = rtl();
            pointer(env.window, env.handle, 'pointerdown', 300, 10);
            pointer(env.window, env.handle, 'pointermove', 240, 10);
            expect(env.panel.style.width).toBe('360px');
            pointer(env.window, env.handle, 'pointermove', 340, 10);
            expect(env.panel.style.width).toBe('260px');
            pointer(env.window, env.handle, 'pointerup', 340, 10);
        });

        it('ArrowLeft grows and ArrowRight shrinks', () => {
            const env = rtl();
            key(env.window, env.handle, 'ArrowLeft');
            expect(env.panel.style.width).toBe('310px');
            key(env.window, env.handle, 'ArrowRight', { shiftKey: true });
            expect(env.panel.style.width).toBe('260px');
        });

        it('Home and End are still smallest and largest', () => {
            const env = rtl();
            key(env.window, env.handle, 'End');
            expect(env.panel.style.width).toBe('600px');
            key(env.window, env.handle, 'Home');
            expect(env.panel.style.width).toBe('100px');
        });

        it('a vertical panel is not mirrored', () => {
            const env = setup({}, PANEL({ direction: 'vertical' }).replace('style="height:300px', 'style="direction:rtl;height:300px'));
            key(env.window, env.handle, 'ArrowDown');
            expect(env.panel.style.height).toBe('310px');
            pointer(env.window, env.handle, 'pointerdown', 10, 300);
            pointer(env.window, env.handle, 'pointermove', 10, 340);
            expect(env.panel.style.height).toBe('350px'); // 310 after the key, +40
        });
    });

    it('a move reporting no buttons pressed ends the drag (the release happened where nothing heard it)', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointermove', 350, 10);
        expect(env.panel.style.width).toBe('350px');
        const e = new env.window.Event('pointermove', { bubbles: true });
        Object.assign(e, { clientX: 450, clientY: 10, pointerId: 1, buttons: 0 });
        env.handle.dispatchEvent(e);
        expect(env.panel.style.width).toBe('350px');
        expect(env.events).toEqual([{ size: 350, direction: 'horizontal' }]);
        pointer(env.window, env.handle, 'pointermove', 500, 10); // and the drag is over
        expect(env.panel.style.width).toBe('350px');
    });

    it('keeps an aria-label an app supplied', () => {
        const env = setup({}, PANEL().replace('role="separator"', 'role="separator" aria-label="Sidebar width"'));
        expect(env.handle.getAttribute('aria-label')).toBe('Sidebar width');
    });
});

// ---------------------------------------------------------------------------
// Optional server event: ResizablePanel resize_event
// ---------------------------------------------------------------------------

describe('ResizablePanel resize_event', () => {
    const setup = (extra = ' data-resize-event="panel_resized"', markup = null) => {
        const env = createEnv(markup || PANEL({ extra }));
        env.window.eval(read('resizable-panel.js'));
        env.$ = (s) => env.window.document.querySelector(s);
        const panel = sized(env);
        env.window.djust.mountHooks();
        env.handle = env.$('.dj-resizable-panel__handle');
        env.panel = panel;
        env.sent = [];
        env.window.djust.handleEvent = (n, p) => env.sent.push([n, p]);
        env.events = [];
        panel.addEventListener('dj-resize', (e) => env.events.push(e.detail));
        const timers = new Map();
        let nextTimer = 0;
        env.window.setTimeout = (callback) => { timers.set(++nextTimer, callback); return nextTimer; };
        env.window.clearTimeout = (id) => timers.delete(id);
        env.flush = () => { const pending = [...timers.values()]; timers.clear(); for (const callback of pending) callback(); };
        return env;
    };

    it('stale resize echoes do not shrink a newer gesture or replace the reset size', () => {
        const env = setup();
        for (const x of [350, 400]) {
            pointer(env.window, env.handle, 'pointerdown', parseFloat(env.panel.style.width), 10);
            pointer(env.window, env.handle, 'pointermove', x, 10);
            pointer(env.window, env.handle, 'pointerup', x, 10);
        }
        env.panel.style.width = '350px';
        env.window.djust.updateHooks();
        expect(env.panel.style.width).toBe('400px');
        env.panel.style.width = '400px';
        env.window.djust.updateHooks();
        key(env.window, env.handle, 'Enter');
        env.flush();
        expect(env.panel.style.width).toBe('300px');
    });

    it('held resize keys send one trailing notification while local events stay immediate', () => {
        const env = setup();
        for (let i = 0; i < 20; i++) key(env.window, env.handle, 'ArrowRight');
        expect(env.panel.style.width).toBe('500px');
        expect(env.events).toHaveLength(20);
        expect(env.sent).toEqual([]);
        env.flush();
        expect(env.sent).toEqual([['panel_resized', { size: 500 }]]);
    });

    it('teardown cancels a pending keyboard notification', () => {
        const env = setup();
        key(env.window, env.handle, 'ArrowRight');
        env.window.djust.getHook(env.panel).destroyed();
        env.flush();
        expect(env.sent).toEqual([]);
    });

    it('resize notifications retain component and child-view routing', () => {
        const env = setup('', '<section data-djust-embedded="child"><div data-component-id="panel">' + PANEL({ extra: ' data-resize-event="panel_resized"' }) + '</div></section>');
        delete env.window.djust._strictBinding;
        key(env.window, env.handle, 'ArrowRight');
        env.flush();
        expect(env.sent[0][1]).toMatchObject({ component_id: 'panel', view_id: 'child' });
    });

    it('a no-op drag does not send a resize', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointerup', 300, 10);
        expect(env.sent).toEqual([]);
    });

    it('without the attribute nothing goes to the server (the CustomEvent still does)', () => {
        const env = setup('');
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointermove', 350, 10);
        pointer(env.window, env.handle, 'pointerup', 350, 10);
        expect(env.sent).toEqual([]);
        expect(env.events).toEqual([{ size: 350, direction: 'horizontal' }]);
    });

    it('a drag sends {size} once, when it ends, in whole pixels', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        for (const x of [310, 330, 351.6]) pointer(env.window, env.handle, 'pointermove', x, 10);
        expect(env.sent).toEqual([]);
        pointer(env.window, env.handle, 'pointerup', 351.6, 10);
        expect(env.sent).toEqual([['panel_resized', { size: 352 }]]);
        expect(env.events).toEqual([{ size: 352, direction: 'horizontal' }]);
    });

    it('is the clamped size that is sent', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointermove', 9000, 10);
        pointer(env.window, env.handle, 'pointerup', 9000, 10);
        expect(env.sent).toEqual([['panel_resized', { size: 600 }]]);
    });

    it('settled key presses notify; Home and End send the limits; a reset sends the initial size', () => {
        const env = setup();
        key(env.window, env.handle, 'ArrowRight');
        env.flush();
        key(env.window, env.handle, 'ArrowRight', { shiftKey: true });
        env.flush();
        key(env.window, env.handle, 'End');
        env.flush();
        key(env.window, env.handle, 'Home');
        env.flush();
        expect(env.sent.map((s) => s[1].size)).toEqual([310, 360, 600, 100]);
        env.handle.dispatchEvent(new env.window.MouseEvent('dblclick', { bubbles: true }));
        expect(env.sent.at(-1)).toEqual(['panel_resized', { size: 300 }]);
    });

    it('a key that changes nothing (not an arrow) sends nothing', () => {
        const env = setup();
        key(env.window, env.handle, 'a');
        env.flush();
        expect(env.sent).toEqual([]);
    });

    it('a cancelled drag still ends the gesture: it sends the size it is at', () => {
        const env = setup();
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointermove', 340, 10);
        pointer(env.window, env.handle, 'pointercancel', 340, 10);
        expect(env.sent).toEqual([['panel_resized', { size: 340 }]]);
    });

    it('a vertical panel sends the same {size} (the direction is the component\'s own)', () => {
        const env = setup(' data-resize-event="panel_resized"', PANEL({ direction: 'vertical', extra: ' data-resize-event="panel_resized"' }));
        pointer(env.window, env.handle, 'pointerdown', 10, 300);
        pointer(env.window, env.handle, 'pointermove', 10, 380);
        pointer(env.window, env.handle, 'pointerup', 10, 380);
        expect(env.sent).toEqual([['panel_resized', { size: 380 }]]);
    });

    it('a disabled panel sends nothing', () => {
        const env = setup(' data-resize-event="panel_resized" data-disabled="true"');
        pointer(env.window, env.handle, 'pointerdown', 300, 10);
        pointer(env.window, env.handle, 'pointermove', 340, 10);
        pointer(env.window, env.handle, 'pointerup', 340, 10);
        key(env.window, env.handle, 'ArrowRight');
        env.flush();
        expect(env.sent).toEqual([]);
    });

    it('a server re-render, with or without a new size, sends nothing', () => {
        const env = setup();
        env.panel.style.width = '420px';
        env.window.djust.updateHooks();
        expect(env.sent).toEqual([]);
    });

    it('goes through the client\'s strict-parameter check, and falls back to the hook\'s pushEvent', () => {
        const env = setup();
        env.window.djust._strictBinding = () => false;
        key(env.window, env.handle, 'ArrowRight');
        env.flush();
        expect(env.sent).toEqual([]);
        env.window.djust._strictBinding = () => ({ size: 1 });
        key(env.window, env.handle, 'ArrowRight');
        env.flush();
        expect(env.sent).toEqual([['panel_resized', { size: 1 }]]);

        const env2 = setup();
        env2.window.djust.handleEvent = undefined;
        const pushed = [];
        const entry = [...env2.window.djust._activeHooks.values()].find((h) => h.instance && h.instance._emit);
        entry.instance.pushEvent = (n, p) => pushed.push([n, p]);
        key(env2.window, env2.handle, 'ArrowRight');
        env2.flush();
        expect(pushed).toEqual([['panel_resized', { size: 310 }]]);
    });

    it('does not double-send after repeated patches', () => {
        const env = setup();
        for (let i = 0; i < 4; i++) env.window.djust.updateHooks();
        key(env.window, env.handle, 'ArrowRight');
        env.flush();
        expect(env.sent).toHaveLength(1);
    });
});
