/**
 * #2985 batch 4 — MentionsInput and DashboardGrid answer their dj-hook, and an app's own hook of the same name
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
    ];
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
                const env = boot(c.markup, c.file, { preRegister: (w) => { w.djust.hooks = { [c.hook]: { mounted } }; } });
                expect(env.window.DjustHooks?.[c.hook]).toBeUndefined();
                expect(mounted).toHaveBeenCalledTimes(1);
                expect(c.probe(env.$(`[dj-hook="${c.hook}"]`))).toBe(false);
            });

            it('an app hook in window.DjustHooks registered first is the one that runs', () => {
                const mounted = vi.fn();
                const mine = { mounted };
                const env = boot(c.markup, c.file, { preRegister: (w) => { w.DjustHooks = { [c.hook]: mine }; } });
                expect(env.window.DjustHooks[c.hook]).toBe(mine);
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
            input.dispatchEvent(new env.window.Event('beforeinput', { bubbles: true, cancelable: true }));
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
            env.key('Enter'); // "@Bob "
            env.type('@al@Bob ', 3); // a mention typed before it
            env.key('Enter'); // "@Alice Cooper @Bob "
            expect(env.input.value).toBe('@Alice Cooper @Bob ');
            env.type(env.input.value + '@bo');
            env.key('Enter'); // Bob again, at the end
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['1', '2']);
        });

        it('text that replaces everything is not a mention, even if it spells one', () => {
            const env = setup();
            env.type('@bo');
            env.key('Enter');
            env.sent.length = 0;
            env.type('@Alice Cooper first, then @Bob ');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]);
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

    describe('mentions are tracked by position', () => {
        const DUP = [{ id: '1', name: 'Alex' }, { id: '2', name: 'Alex' }, { id: '3', name: 'Bob' }];
        const choose = (env, query, ...downs) => {
            env.type(env.input.value + query);
            downs.forEach(() => env.key('ArrowDown'));
            env.key('Enter');
        };
        const submit = (env) => env.key('Enter');

        it('two people with one name are two mentions', () => {
            const env = setup(MENTIONS(DUP));
            choose(env, '@al'); // Alex #1
            choose(env, '@al', 1); // Alex #2
            expect(env.input.value).toBe('@Alex @Alex ');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['1', '2']);
        });

        it('delete the first Alex and choose the second: only the second is mentioned', () => {
            const env = setup(MENTIONS(DUP));
            choose(env, '@al'); // Alex #1
            env.type(''); // all of it deleted
            choose(env, '@al', 1); // Alex #2
            submit(env);
            expect(env.input.value).toBe('@Alex ');
            expect(env.sent[0][1].mentions).toEqual(['2']);
        });

        it('deleting the first of two identical tokens, caret where it was, drops that one and keeps the other', () => {
            const env = setup(MENTIONS(DUP));
            choose(env, '@al'); // Alex #1
            choose(env, '@al', 1); // Alex #2
            env.type('@Alex ', 0); // the first token (6 chars) deleted, caret at 0
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['2']);
        });

        it('typing before a token moves it, it is still a mention', () => {
            const env = setup(MENTIONS(DUP));
            choose(env, '@bo');
            env.type('hello there @Bob ', 12);
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['3']);
        });

        it('an edit inside a token, or right after it making it a longer word, drops it', () => {
            const env = setup(MENTIONS(DUP));
            choose(env, '@bo');
            env.type('@Bxb ');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]);
            env.sent.length = 0;
            choose(env, '@bo');
            env.type(env.input.value.replace('@Bob ', '@Bobby '));
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]);
        });

        it('a record whose text moved without an edit the hook saw is checked against the text at submit', () => {
            const env = setup(MENTIONS(DUP));
            choose(env, '@bo');
            env.input.value = 'Rob  rest'; // set by code (a re-render, an app), no input event
            submit(env);
            expect(env.sent[0][1].mentions).toEqual([]);
        });

        it('records of tokens that are gone are forgotten (no growth for the life of the input)', () => {
            const env = setup();
            for (let i = 0; i < 20; i++) {
                choose(env, '@bo');
                env.type('');
            }
            const entry = [...env.window.djust._activeHooks.values()].find((h) => h.instance && h.instance._mentions);
            expect(entry.instance._mentions).toEqual([]);
        });

        it('inserts with the browser\'s own edit (undo-friendly) when it has one, and falls back to setRangeText', () => {
            const env = setup();
            const calls = [];
            env.window.document.execCommand = (cmd, _ui, value) => {
                calls.push([cmd, value]);
                const i = env.input;
                i.setRangeText(value, i.selectionStart, i.selectionEnd, 'end');
                i.dispatchEvent(new env.window.Event('input', { bubbles: true }));
                return true;
            };
            env.type('hi @bo');
            env.key('Enter');
            expect(calls).toEqual([['insertText', '@Bob ']]);
            expect(env.input.value).toBe('hi @Bob ');
            env.window.document.execCommand = () => false; // refused: the fallback still inserts
            env.type(env.input.value + '@al');
            env.key('Enter');
            expect(env.input.value).toBe('hi @Bob @Alice Cooper ');
            submit(env);
            expect(env.sent[0][1].mentions).toEqual(['2', '1']);
        });
    });

    describe('input method composition', () => {
        it('Enter that commits a composition is not a submit: it does not reach the plain binding, and is not prevented', () => {
            const env = setup();
            const reached = [];
            env.$('[dj-root]').addEventListener('keydown', (e) => reached.push(e.key));
            env.type('にほん');
            const e = env.key('Enter', { isComposing: true });
            expect(reached).toEqual([]);
            expect(e.defaultPrevented).toBe(false);
            expect(env.sent).toEqual([]);
        });

        it('the same for the keyCode 229 Enter some browsers send after compositionend', () => {
            const env = setup();
            const reached = [];
            env.$('[dj-root]').addEventListener('keydown', (e) => reached.push(e.key));
            env.type('にほん');
            const ev = new env.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, keyCode: 229 });
            env.input.dispatchEvent(ev);
            expect(reached).toEqual([]);
            expect(ev.defaultPrevented).toBe(false);
            expect(env.sent).toEqual([]);
        });

        it('other keys during composition still reach the page (the hook only holds Enter)', () => {
            const env = setup();
            const reached = [];
            env.$('[dj-root]').addEventListener('keydown', (e) => reached.push(e.key));
            env.key('a', { isComposing: true });
            expect(reached).toEqual(['a']);
        });

        it('the Enter after the composition ended submits as usual', () => {
            const env = setup();
            env.type('日本語');
            env.key('Enter');
            expect(env.sent).toHaveLength(1);
            expect(env.sent[0][1].text).toBe('日本語');
        });
    });

    describe('routing: the event goes where a plain binding\'s would', () => {
        const wrapped = (attrs) => `<div ${attrs}>${MENTIONS()}</div>`;

        it('inside a LiveComponent it carries component_id', () => {
            const env = setup(wrapped('data-component-id="chat"'));
            env.type('hi');
            env.key('Enter');
            expect(env.sent[0][1].component_id).toBe('chat');
            expect(env.sent[0][1].view_id).toBeUndefined();
        });

        it('inside an embedded child view it carries view_id', () => {
            const env = setup(wrapped('data-djust-embedded="child1"'));
            env.type('hi');
            env.key('Enter');
            expect(env.sent[0][1].view_id).toBe('child1');
        });

        it('on the page it carries neither', () => {
            const env = setup();
            env.type('hi');
            env.key('Enter');
            expect(env.sent[0][1].component_id).toBeUndefined();
            expect(env.sent[0][1].view_id).toBeUndefined();
        });

        it('a strict handler contract gets the input as the routing context', () => {
            const env = setup(wrapped('data-component-id="chat"'));
            const calls = [];
            env.window.djust._strictBinding = (...a) => { calls.push(a); return { text: 'hi', component_id: 'chat' }; };
            env.type('hi');
            env.key('Enter');
            expect(calls[0][4]).toBe(env.input);
            expect(env.sent[0][1]).toEqual({ text: 'hi', component_id: 'chat' });
        });

        it('with no client API it falls back to pushEvent, with the ids', () => {
            const env = setup(wrapped('data-component-id="chat" data-djust-embedded="kid"'));
            env.window.djust.handleEvent = undefined;
            const pushed = [];
            const entry = [...env.window.djust._activeHooks.values()].find((h) => h.instance && h.instance._mentions);
            entry.instance.pushEvent = (n, p) => pushed.push([n, p]);
            env.type('hi');
            env.key('Enter');
            expect(pushed[0][1].component_id).toBe('chat');
            expect(pushed[0][1].view_id).toBe('kid');
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
