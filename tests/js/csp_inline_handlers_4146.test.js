/**
 * #4146 -- the inline event handlers djust used to write, as delegated listeners.
 *
 * A Content-Security-Policy whose script-src lacks 'unsafe-inline' refuses every
 * `on*=` attribute. By default NavBar's menu toggle (Tailwind and plain) and the
 * theme alert's and toast's close button stay inline handlers; under
 * LIVEVIEW_CONFIG["csp_strict"] they are markers handled from a listener on
 * `document`:
 *
 *   navbar-toggle.js   [data-navbar-toggle] -> toggle data-navbar-toggle-class
 *                      on the element with that id (was classList.toggle).
 *   components.js      [data-theme-dismiss] -> remove the button's parent
 *                      (was this.parentElement.remove()).
 *
 * Each test loads the REAL script into a page holding the markup the server
 * renders in strict mode (python/djust/tests/test_csp_inline_handlers_4146.py
 * pins it), for a button present at load, one inserted afterwards, and one
 * inserted by the client's real patch path (applyPatches / InsertChild). A
 * <script> a patch inserts never runs, so a strict LiveView page loads
 * navbar-toggle.js with the client; the patch tests load it that way and carry
 * the navbar's own (inert) <script> in the patch, as the server renders it.
 */

import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const NAVBAR_JS = './python/djust/components/static/djust_components/navbar-toggle.js';
const THEMING_JS = './python/djust/theming/static/djust_theming/js/components.js';
const CLIENT_JS = './python/djust/static/djust/client.js';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(f, 'utf-8');

function page(body, scripts) {
    const dom = new JSDOM(`<!DOCTYPE html><html><body>${body}</body></html>`, {
        url: 'http://localhost/',
        runScripts: 'outside-only',
    });
    for (const s of scripts) dom.window.eval(read(s));
    return dom.window;
}

function click(window, el) {
    el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

const TAILWIND = `
<nav class="bg-white" data-djust-navbar><div class="md:hidden">
  <button type="button" class="p-2" data-navbar-toggle="nav1-mobile" data-navbar-toggle-class="hidden">
    <svg><path d="M4 6h16"></path></svg>
  </button></div>
  <div class="hidden md:hidden" id="nav1-mobile">menu</div></nav>`;

const PLAIN = `
<nav class="navbar navbar-light" data-djust-navbar>
  <button class="navbar-toggler" data-navbar-toggle="nav2-menu" data-navbar-toggle-class="show">&#9776;</button>
  <div class="navbar-menu" id="nav2-menu">menu</div></nav>`;

describe('navbar-toggle.js', () => {
    it('toggles `hidden` on the Tailwind mobile menu, both ways, from a click on the icon', () => {
        const window = page(TAILWIND, [NAVBAR_JS]);
        const menu = window.document.getElementById('nav1-mobile');
        const path = window.document.querySelector('path');
        click(window, path);
        expect(menu.classList.contains('hidden')).toBe(false);
        expect(menu.classList.contains('md:hidden')).toBe(true);
        click(window, path);
        expect(menu.classList.contains('hidden')).toBe(true);
    });

    it('toggles `show` on the plain menu', () => {
        const window = page(PLAIN, [NAVBAR_JS]);
        const menu = window.document.getElementById('nav2-menu');
        click(window, window.document.querySelector('.navbar-toggler'));
        expect(menu.classList.contains('show')).toBe(true);
        click(window, window.document.querySelector('.navbar-toggler'));
        expect(menu.classList.contains('show')).toBe(false);
    });

    it('works for a navbar inserted after load', () => {
        const window = page('', [NAVBAR_JS]);
        window.document.body.insertAdjacentHTML('beforeend', PLAIN);
        click(window, window.document.querySelector('.navbar-toggler'));
        expect(window.document.getElementById('nav2-menu').classList.contains('show')).toBe(true);
    });

    it('loaded twice (two navbars each render the tag), still toggles once per click', () => {
        const window = page(PLAIN, [NAVBAR_JS, NAVBAR_JS]);
        click(window, window.document.querySelector('.navbar-toggler'));
        expect(window.document.getElementById('nav2-menu').classList.contains('show')).toBe(true);
    });

    it('does nothing for a missing menu or an unmarked button', () => {
        const window = page(
            `<nav data-djust-navbar>
               <button id="a" data-navbar-toggle="nope" data-navbar-toggle-class="show">a</button>
               <button id="b">b</button>
               <div id="m" class="x"></div></nav>`,
            [NAVBAR_JS],
        );
        for (const id of ['a', 'b']) click(window, window.document.getElementById(id));
        expect(window.document.getElementById('m').className).toBe('x');
    });

    it('refuses a target outside the button\'s own navbar', () => {
        const window = page(
            `<nav data-djust-navbar><button id="t" data-navbar-toggle="victim" data-navbar-toggle-class="hidden">t</button></nav>
             <div id="victim" class="v"></div>`,
            [NAVBAR_JS],
        );
        click(window, window.document.getElementById('t'));
        expect(window.document.getElementById('victim').className).toBe('v');
    });

    it('refuses a target in another navbar', () => {
        const window = page(
            `<nav data-djust-navbar><button id="t" data-navbar-toggle="other-menu" data-navbar-toggle-class="show">t</button></nav>
             <nav data-djust-navbar><div id="other-menu" class="navbar-menu"></div></nav>`,
            [NAVBAR_JS],
        );
        click(window, window.document.getElementById('t'));
        expect(window.document.getElementById('other-menu').classList.contains('show')).toBe(false);
    });

    it.each(['pwned', 'hidden show', 'Hidden', '', 'constructor'])(
        'refuses the class token %j',
        (token) => {
            const window = page(
                `<nav data-djust-navbar><button id="t" data-navbar-toggle="m" data-navbar-toggle-class="${token}">t</button>
                 <div id="m" class="x"></div></nav>`,
                [NAVBAR_JS],
            );
            click(window, window.document.getElementById('t'));
            expect(window.document.getElementById('m').className).toBe('x');
        },
    );

    it('refuses a button whose nav is not a djust navbar root', () => {
        const window = page(
            `<nav><button id="t" data-navbar-toggle="m" data-navbar-toggle-class="show">t</button>
             <div id="m" class="x"></div></nav>`,
            [NAVBAR_JS],
        );
        click(window, window.document.getElementById('t'));
        expect(window.document.getElementById('m').className).toBe('x');
    });

    it('refuses toggling the navbar root itself', () => {
        const window = page(
            `<nav id="n" class="x" data-djust-navbar><button id="t" data-navbar-toggle="n" data-navbar-toggle-class="show">t</button></nav>`,
            [NAVBAR_JS],
        );
        click(window, window.document.getElementById('t'));
        expect(window.document.getElementById('n').className).toBe('x');
    });
});

const ALERT = `
<div class="alert alert-info" data-theme-dismiss-root role="alert" id="al">
  <div class="alert-content"><div class="alert-message">Hi</div></div>
  <button class="alert-dismiss" data-theme-dismiss aria-label="Dismiss">
    <svg width="16" height="16"><line x1="18" y1="6" x2="6" y2="18"></line></svg>
  </button>
</div>`;

const TOAST = `
<div class="toast toast-info toast-top-right" data-theme-dismiss-root role="status" aria-live="polite" data-theme-toast data-duration="5000" id="to">
  <div class="toast-content"><div class="toast-message">Saved</div></div>
  <button class="toast-close" data-theme-dismiss aria-label="Dismiss"><svg><line></line></svg></button>
</div>`;

describe('theming components.js: data-theme-dismiss', () => {
    it.each([
        ['alert', ALERT, 'al'],
        ['toast', TOAST, 'to'],
    ])('removes the %s when its close button (or the icon inside it) is clicked', (_n, html, id) => {
        const window = page(`<main>${html}<p id="keep">x</p></main>`, [THEMING_JS]);
        click(window, window.document.querySelector(`#${id} line`));
        expect(window.document.getElementById(id)).toBe(null);
        expect(window.document.getElementById('keep')).not.toBe(null);
    });

    it('removes one inserted after load', () => {
        const window = page('<main></main>', [THEMING_JS]);
        window.document.querySelector('main').insertAdjacentHTML('beforeend', ALERT);
        click(window, window.document.querySelector('[data-theme-dismiss]'));
        expect(window.document.getElementById('al')).toBe(null);
    });

    it('works on a LiveView page, where the modal and dropdown bindings stand down', () => {
        const window = page(`<div dj-view="app.V" dj-root>${ALERT}</div>`, [THEMING_JS]);
        click(window, window.document.querySelector('[data-theme-dismiss]'));
        expect(window.document.getElementById('al')).toBe(null);
    });

    it('a marked button without a marked root removes nothing', () => {
        const window = page(
            '<main id="c"><section id="box"><button id="b" data-theme-dismiss>x</button></section></main>',
            [THEMING_JS],
        );
        click(window, window.document.getElementById('b'));
        expect(window.document.getElementById('box')).not.toBe(null);
        expect(window.document.getElementById('c')).not.toBe(null);
    });

    it('a button nested deeper than the root removes nothing', () => {
        const window = page(
            '<div id="r" data-theme-dismiss-root><span><button id="b" data-theme-dismiss>x</button></span></div>',
            [THEMING_JS],
        );
        click(window, window.document.getElementById('b'));
        expect(window.document.getElementById('r')).not.toBe(null);
    });

    it('is inert for default-mode markup (no markers)', () => {
        const window = page(
            '<div class="alert" id="al" role="alert"><button class="alert-dismiss" aria-label="Dismiss">x</button></div>',
            [THEMING_JS],
        );
        click(window, window.document.querySelector('.alert-dismiss'));
        expect(window.document.getElementById('al')).not.toBe(null);
    });

    it('a click elsewhere removes nothing', () => {
        const window = page(ALERT, [THEMING_JS]);
        click(window, window.document.querySelector('.alert-message'));
        expect(window.document.getElementById('al')).not.toBe(null);
    });
});

// The client's own patch path: a VDOM InsertChild builds the element from a vnode,
// not from HTML, so this is what a LiveView re-render that adds the button does.
function vnode(tag, djId, attrs = {}, children = []) {
    return { tag, djust_id: djId, attrs: { 'dj-id': djId, ...attrs }, children };
}
function text(t) {
    return { tag: '#text', djust_id: null, attrs: {}, children: [], text: t };
}

function livePage(scripts) {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body><div dj-view="app.V" dj-root><div dj-id="P"></div></div></body></html>',
        { url: 'http://localhost/', runScripts: 'dangerously' },
    );
    const { window } = dom;
    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) window.CSS.escape = (s) => String(s).replace(/([^\w-])/g, '\\$1');
    window.eval(read(CLIENT_JS));
    for (const s of scripts) window.eval(read(s));
    return window;
}

const NAVBAR_PATCH = [
    {
        type: 'InsertChild', path: [0], d: 'P', index: 0, ref_d: null,
        node: vnode('nav', 'n0', { class: 'navbar navbar-light', 'data-djust-navbar': '' }, [
            vnode('button', 'b1', {
                class: 'navbar-toggler',
                'data-navbar-toggle': 'm1',
                'data-navbar-toggle-class': 'show',
            }, [text('menu')]),
            vnode('div', 'm1', { id: 'm1', class: 'navbar-menu' }),
            vnode('script', 's1', {
                src: '/static/djust_components/navbar-toggle.js', defer: '',
            }),
        ]),
    },
];

describe('a button the client inserts with applyPatches', () => {
    it('NavBar toggle: a navbar that first arrives in a patch, on a strict page', async () => {
        // The strict page loaded navbar-toggle.js with the client, before any navbar.
        const window = livePage([NAVBAR_JS]);
        expect(window.document.querySelector('nav')).toBe(null);
        const ok = await window.djust.applyPatches(NAVBAR_PATCH);
        expect(ok).toBe(true);
        click(window, window.document.querySelector('[data-navbar-toggle]'));
        expect(window.document.getElementById('m1').classList.contains('show')).toBe(true);
        click(window, window.document.querySelector('[data-navbar-toggle]'));
        expect(window.document.getElementById('m1').classList.contains('show')).toBe(false);
    });

    it('why the page loads it: the navbar\'s own <script> in a patch does not run', async () => {
        const window = livePage([]);
        const ok = await window.djust.applyPatches(NAVBAR_PATCH);
        expect(ok).toBe(true);
        click(window, window.document.querySelector('[data-navbar-toggle]'));
        expect(window.document.getElementById('m1').classList.contains('show')).toBe(false);
    });

    it('theme alert close button', async () => {
        const window = livePage([THEMING_JS]);
        const ok = await window.djust.applyPatches([
            {
                type: 'InsertChild', path: [0], d: 'P', index: 0, ref_d: null,
                node: vnode('div', 'a1', { id: 'al', class: 'alert', role: 'alert', 'data-theme-dismiss-root': '' }, [
                    vnode('button', 'a2', { class: 'alert-dismiss', 'data-theme-dismiss': '' }, [text('x')]),
                ]),
            },
        ]);
        expect(ok).toBe(true);
        click(window, window.document.querySelector('[data-theme-dismiss]'));
        expect(window.document.getElementById('al')).toBe(null);
    });
});
