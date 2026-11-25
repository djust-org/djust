/**
 * #4205: strict is the only parameter path. Every binder sends exactly what the
 * handler's contract declares, plus the element's dj-value-* arguments:
 *
 *  - an undeclared generated value (value, field, key, ...) never reaches the
 *    handler;
 *  - a data-* attribute and the 1.x `_target` key are never sent;
 *  - a handler the mount does not list declares nothing, so only its
 *    dj-value-* arguments are sent;
 *  - an invalid contract scope sends nothing and reports one djust:error.
 *
 * Each element carries `data-leak="x"` and `dj-value-tag="t"`; the handler is
 * `go(self, tag)`, so `{tag: 't'}` is the whole payload.
 */
import { describe, expect, it } from 'vitest';
import { anyArgs, param, setup, strict } from './_form_events_harness.js';

const ATTRS = 'data-leak="x" dj-value-tag="t"';
const KEY = (win, init) => new win.KeyboardEvent('keydown', {bubbles: true, cancelable: true, ...init});

// name -> [markup, fire(win, $)]
const BINDERS = {
    'dj-click': [`<button id="t" dj-click="go" ${ATTRS}>x</button>`, (win, $) => $('#t').click()],
    'dj-submit': [`<form id="t" dj-submit="go" ${ATTRS}><input name="a" value="1"></form>`,
        (win, $) => $('#t').requestSubmit()],
    'dj-change': [`<input id="t" name="a" dj-change="go" ${ATTRS}>`,
        (win, $) => { $('#t').value = 'v'; $('#t').dispatchEvent(new win.Event('change', {bubbles: true})); }],
    'dj-input': [`<input id="t" name="a" dj-input="go" dj-debounce="0" ${ATTRS}>`,
        (win, $) => { $('#t').value = 'v'; $('#t').dispatchEvent(new win.Event('input', {bubbles: true})); }],
    'dj-blur': [`<input id="t" name="a" dj-blur="go" ${ATTRS}>`, (win, $) => $('#t').dispatchEvent(new win.FocusEvent('focusout', {bubbles: true}))],
    'dj-focus': [`<input id="t" name="a" dj-focus="go" ${ATTRS}>`, (win, $) => $('#t').dispatchEvent(new win.FocusEvent('focusin', {bubbles: true}))],
    'dj-keydown': [`<input id="t" dj-keydown="go" ${ATTRS}>`, (win, $) => $('#t').dispatchEvent(KEY(win, {key: 'a'}))],
    'dj-window-keydown': [`<div id="t" dj-window-keydown="go" ${ATTRS}></div>`,
        (win) => win.dispatchEvent(KEY(win, {key: 'a'}))],
    'dj-mouseenter': [`<div id="t" dj-mouseenter="go" ${ATTRS}>x</div>`,
        (win, $) => $('#t').dispatchEvent(new win.MouseEvent('mouseenter'))],
};

async function run(name, handlers, transport) {
    const [markup, fire] = BINDERS[name];
    const env = setup(markup, handlers, undefined, {transport});
    await env.ready;
    fire(env.win, env.$);
    await env.settle(450);
    return env;
}

for (const transport of ['http', 'ws']) {
    describe(`strict is the only parameter path, ${transport}`, () => {
        for (const name of Object.keys(BINDERS)) {
            it(`${name}: sends the declared argument and the dj-value-*, nothing else`, async () => {
                const env = await run(name, {go: strict(param('tag', 'str'))}, transport);
                expect(env.sent().map(s => s.event)).toEqual(['go']);
                const params = env.sent()[0].params;
                expect(params).toEqual({tag: 't'});
                expect(env.raw().join('')).not.toContain('leak');
                expect(env.raw().join('')).not.toContain('_target');
            });

            it(`${name}: an unlisted handler declares nothing, so only dj-value-* is sent`, async () => {
                const env = await run(name, {}, transport);
                expect(env.sent().map(s => s.event)).toEqual(['go']);
                expect(env.sent()[0].params).toEqual({tag: 't'});
            });
        }

        it('dj-input with ** declares everything generated, still never data-*', async () => {
            const env = await run('dj-input', {go: anyArgs()}, transport);
            const params = env.sent()[0].params;
            expect(params.value).toBe('v');
            expect(params.field).toBe('a');
            expect(params.tag).toBe('t');
            expect(params).not.toHaveProperty('leak');
            expect(params).not.toHaveProperty('_target');
        });
    });
}

describe('an invalid contract scope fails closed (#4205)', () => {
    it('sends nothing and reports one djust:error', async () => {
        const env = setup(BINDERS['dj-click'][0], {go: {policy: 'legacy'}});
        await env.ready;
        BINDERS['dj-click'][1](env.win, env.$);
        await env.settle();
        expect(env.sent()).toEqual([]);
        expect(env.errors()).toHaveLength(1);
        expect(env.errors()[0].event).toBe('go');
    });
});

// An unlisted handler declares nothing: the scoped and passive binders send only the
// element's dj-value-* arguments, never data-*, `_target` or a generated value.
describe('an unlisted handler sends only dj-value-* from the scoped binders (#4205)', () => {
    const ONLY = {tag: 't'};
    const SCOPED = {
        'dj-poll': [`<div id="t" dj-poll="go" dj-poll-interval="30" ${ATTRS}></div>`, () => {}],
        'dj-paste': [`<textarea id="t" dj-paste="go" ${ATTRS}></textarea>`, (win, $) => {
            const e = new win.Event('paste', {bubbles: true, cancelable: true});
            e.clipboardData = {getData: () => 'pasted', files: [], items: [], types: ['text/plain']};
            $('#t').dispatchEvent(e);
        }],
        'dj-shortcut': [`<div id="t" dj-shortcut="a:go" ${ATTRS}></div>`,
            (win) => win.document.dispatchEvent(KEY(win, {key: 'a'}))],
        'dj-click-away': [`<div id="t" dj-click-away="go" ${ATTRS}>in</div><p id="out">out</p>`,
            (win, $) => $('#out').dispatchEvent(new win.MouseEvent('click', {bubbles: true}))],
        // Fires for an element that arrives after the initial mount.
        'dj-mounted': ['<p>host</p>', (win) => {
            win.document.querySelector('[dj-view]').insertAdjacentHTML('beforeend',
                `<div id="t" dj-mounted="go" ${ATTRS}>x</div>`);
            win.djust._mountReady = true;
            win.djust.reinitAfterDOMUpdate();
        }],
        'dj-copy-event': [`<button id="t" dj-copy="text" dj-copy-event="go" ${ATTRS}>c</button>`, (win, $) => {
            Object.defineProperty(win.navigator, 'clipboard', {value: {writeText: async () => {}}, configurable: true});
            $('#t').click();
        }],
    };
    for (const [name, [markup, fire]] of Object.entries(SCOPED)) {
        it(`${name}`, async () => {
            const env = setup(markup, {});
            await env.ready;
            fire(env.win, env.$);
            await env.settle(450);
            const sent = env.sent().filter(s => s.event === 'go');
            expect(sent.length).toBeGreaterThan(0);
            expect(sent[0].params).toEqual(ONLY);
            expect(env.raw().join('')).not.toContain('leak');
            expect(env.raw().join('')).not.toContain('_target');
        });
    }
});
