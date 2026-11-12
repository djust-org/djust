/**
 * #3978: multi-valued controls (a group of same-named checkboxes, a
 * `<select multiple>`, a radio group) were flattened to one scalar by the
 * draft manager, `_collectFormData` / `_restoreFormData`, the `dj-auto-recover`
 * envelope and `dj-model` on a checkbox group: last box wins, and a restore wrote
 * that one boolean to every box of the group.
 *
 * Runs against the bundled client in JSDOM; the DOM is built with innerHTML
 * (real inter-element whitespace); events are real DOM events.
 */
import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { legacy, setup, toggle } from './_form_events_harness.js';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

const GROUP = `
    <input type="checkbox" name="tags" value="a" data-draft="true">
    <input type="checkbox" name="tags" value="b" data-draft="true">
    <input type="checkbox" name="tags" value="c" data-draft="true">
    <input type="checkbox" name="agree" data-draft="true">
    <select name="colors" multiple data-draft="true">
        <option value="red">Red</option><option value="green">Green</option><option value="blue">Blue</option>
    </select>
    <input type="radio" name="size" value="s" data-draft="true">
    <input type="radio" name="size" value="m" data-draft="true">
    <input type="text" name="title" data-draft="true">
`;

function draftPage(saved) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-view="app.Page">
            <form data-draft-enabled data-draft-key="k">${GROUP}</form>
        </div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' });
    const win = dom.window;
    if (saved !== undefined) {
        win.localStorage.setItem('djust_draft_k', JSON.stringify({ data: saved, timestamp: Date.now() }));
    }
    win.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.fetch = async () => ({ ok: true, json: async () => ({ patches: [], version: 1 }) });
    `);
    win.console.error = () => {};
    win.eval(source);
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    const $$ = selector => Array.from(win.document.querySelectorAll(selector));
    return { win, $: s => win.document.querySelector(s), $$ };
}

const checked = boxes => boxes.filter(box => box.checked).map(box => box.value);
const stored = win => {
    const raw = JSON.parse(win.localStorage.getItem('djust_draft_k'));
    return raw && (raw.data || raw);
};
const flush = () => new Promise(resolve => setTimeout(resolve, 650));

describe('draft manager keeps a group as a list (#3978)', () => {
    it('saves a checkbox group, a <select multiple> and a radio group as one value each', async () => {
        const { win, $, $$ } = draftPage();
        const [a, , c] = $$('input[name="tags"]');
        a.checked = true;
        c.checked = true;
        $('select').options[0].selected = true;
        $('select').options[2].selected = true;
        $$('input[name="size"]')[1].checked = true;
        $('input[name="agree"]').checked = true;
        toggle(win, a, true); // the change that triggers the save
        await flush();
        const data = stored(win);
        expect(data.tags).toEqual(['a', 'c']);
        expect(data.colors).toEqual(['red', 'blue']);
        expect(data.size).toBe('m');
        expect(data.agree).toBe(true);
    });

    it('restores each box of a group from the list, not one boolean onto every box', () => {
        const { $, $$ } = draftPage({ tags: ['b'], colors: ['green', 'blue'], size: 's', agree: true, title: 'T' });
        expect(checked($$('input[name="tags"]'))).toEqual(['b']);
        expect(Array.from($('select').selectedOptions, o => o.value)).toEqual(['green', 'blue']);
        expect(checked($$('input[name="size"]'))).toEqual(['s']);
        // A radio's own value is never overwritten by the saved one.
        expect($$('input[name="size"]').map(r => r.value)).toEqual(['s', 'm']);
        expect($('input[name="agree"]').checked).toBe(true);
        expect($('input[name="title"]').value).toBe('T');
    });

    it('a group saved as a boolean before #3978 still restores', () => {
        const { $$ } = draftPage({ tags: true });
        expect(checked($$('input[name="tags"]'))).toEqual(['a', 'b', 'c']);
    });

    it('an empty group is saved as an empty list', async () => {
        const { win, $$ } = draftPage();
        toggle(win, $$('input[name="tags"]')[0], false);
        await flush();
        expect(stored(win).tags).toEqual([]);
    });
});

describe('_collectFormData / _restoreFormData (#3978)', () => {
    const FORM_SRC = readFileSync('./python/djust/static/djust/src/07-form-data.js', 'utf8');
    // eslint-disable-next-line no-new-func -- evaluates the real bundle module (test-only)
    const { collect, restore } = new Function('djLog', 'UNSAFE_KEYS',
        FORM_SRC + '\nreturn { collect: _collectFormData, restore: _restoreFormData };'
    )(() => {}, ['__proto__', 'constructor', 'prototype']);

    it('round-trips a group', () => {
        const dom = new JSDOM(`<form>${GROUP}</form>`);
        const form = dom.window.document.querySelector('form');
        const boxes = Array.from(form.querySelectorAll('input[name="tags"]'));
        boxes[0].checked = true;
        boxes[2].checked = true;
        const data = collect(form);
        expect(data.tags).toEqual(['a', 'c']);
        boxes.forEach(box => { box.checked = false; });
        restore(form, data);
        expect(checked(boxes)).toEqual(['a', 'c']);
    });

    it('refuses a prototype-polluting name', () => {
        const dom = new JSDOM('<form><input name="__proto__" value="x"></form>');
        const data = collect(dom.window.document.querySelector('form'));
        expect(Object.prototype.hasOwnProperty.call(data, '__proto__')).toBe(false);
    });
});

describe('dj-auto-recover sends a group as a list (#3978)', () => {
    it('serializes a checkbox group and a <select multiple> as lists', async () => {
        const t = setup(`
            <div dj-auto-recover="restore_state">
                <input type="checkbox" name="tags" value="a" checked>
                <input type="checkbox" name="tags" value="b">
                <input type="checkbox" name="tags" value="c" checked>
                <input type="checkbox" name="agree" checked>
                <select name="colors" multiple><option value="r" selected>R</option><option value="g" selected>G</option></select>
            </div>`, { restore_state: legacy() });
        t.win.djust._isReconnect = true;
        t.win.djust._processAutoRecover();
        await t.settle();
        const sent = t.sent().find(s => s.event === 'restore_state');
        expect(sent.params._form_values).toEqual({ tags: ['a', 'c'], agree: true, colors: ['r', 'g'] });
    });
});

describe('dj-model on a checkbox group binds a list (#3978)', () => {
    it('sends the checked values of the group, not the last box clicked', async () => {
        const t = setup(`
            <form>
                <input type="checkbox" name="tags" value="a" dj-model="tags">
                <input type="checkbox" name="tags" value="b" dj-model="tags">
                <input type="checkbox" name="tags" value="c" dj-model="tags">
            </form>`, { update_model: legacy() });
        const boxes = Array.from(t.win.document.querySelectorAll('input'));
        toggle(t.win, boxes[0], true);
        await t.settle();
        toggle(t.win, boxes[2], true);
        await t.settle();
        toggle(t.win, boxes[0], false);
        await t.settle();
        const values = t.sent().filter(s => s.event === 'update_model').map(s => s.params.value);
        expect(values.at(-1)).toEqual(['c']);
        expect(values).toContainEqual(['a', 'c']);
    });

    it('a lone checkbox still binds a boolean', async () => {
        const t = setup('<input type="checkbox" name="agree" dj-model="agree">', { update_model: legacy() });
        toggle(t.win, t.$('input[name="agree"]'), true);
        await t.settle();
        expect(t.sent().filter(s => s.event === 'update_model').at(-1).params).toMatchObject({ field: 'agree', value: true });
    });
});
