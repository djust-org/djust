/**
 * #3959: a field with several values (a group of same-named checkboxes, a
 * `<select multiple>`, any repeated name) must reach the handler as a LIST, the
 * way Django's `QueryDict.getlist` sees it on a native POST:
 *
 *  - `dj-submit` used to keep only the LAST FormData entry for a repeated name
 *    (`Object.fromEntries(new FormData(form).entries())`);
 *  - `dj-change` on one box of a group sent that box's `checked` boolean, and on
 *    a `<select multiple>` only the first selected option.
 *
 * Real DOM (innerHTML, whitespace), real `submit` (requestSubmit) and `change`
 * events through the bundled client, over the HTTP fallback, under both the
 * legacy and the strict (ADR-036) policy.
 */
import { describe, expect, it } from 'vitest';
import { legacy, param, setup, strict, submit, toggle } from './_form_events_harness.js';

const TAGS = `
    <form dj-submit="save_open">
        <input type="checkbox" name="tags" value="x">
        <input type="checkbox" name="tags" value="y">
        <input type="checkbox" name="tags" value="z">
        <button type="submit">Save</button>
    </form>`;

const handlers = {
    save_open: strict(param('form', 'Any', 'var_keyword', false)),
    save_list: strict(param('tags', 'list[str]')),
    save_str: strict(param('tags', 'str')),
    save_legacy: legacy(),
    change_open: strict(param('value', 'Any'), param('field', 'str')),
    change_list: strict(param('value', 'list[str]'), param('field', 'str')),
    change_legacy: legacy(),
};

const sentOnly = t => t.sent()[0].params;
const check = (t, ...values) => {
    for (const box of t.win.document.querySelectorAll('input[name="tags"]')) {
        box.checked = values.includes(box.value);
    }
};

describe('dj-submit: a repeated name is a list (#3959)', () => {
    it('legacy: two ticked boxes arrive as both values, not the last', async () => {
        const t = setup(TAGS.replace('save_open', 'save_legacy'), handlers);
        check(t, 'x', 'y');
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).tags).toEqual(['x', 'y']);
    });

    it('strict, **form_data: the same list', async () => {
        const t = setup(TAGS, handlers);
        check(t, 'x', 'y');
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).tags).toEqual(['x', 'y']);
        expect(t.errors()).toEqual([]);
    });

    it('strict, declared list[str]: accepted, not rejected', async () => {
        const t = setup(TAGS.replace('save_open', 'save_list'), handlers);
        check(t, 'y', 'z');
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).tags).toEqual(['y', 'z']);
        expect(t.errors()).toEqual([]);
    });

    it('one ticked box of a group is still a one-element list', async () => {
        const t = setup(TAGS, handlers);
        check(t, 'z');
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).tags).toEqual(['z']);
    });

    it('no ticked box of a group is an empty list, so a cleared group is not stale server-side', async () => {
        const t = setup(TAGS, handlers);
        check(t);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).tags).toEqual([]);
    });

    it('a declared scalar `tags: str` rejects the list rather than guessing which value', async () => {
        const t = setup(TAGS.replace('save_open', 'save_str'), handlers);
        check(t, 'x', 'y');
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        // The client sends the honest list; the SERVER's strict binder owns the
        // type verdict (see python/djust/tests/test_form_multi_value_3959.py).
        expect(sentOnly(t).tags).toEqual(['x', 'y']);
    });

    it.each([
        ['every selected option', ['r', 'b'], ['r', 'b']],
        ['a single selected option', ['g'], ['g']],
        ['no selected option', [], []],
    ])('<select multiple>: %s', async (_label, chosen, expected) => {
        const html = `
            <form dj-submit="save_open">
                <select name="colors" multiple>
                    <option value="r">R</option><option value="g">G</option><option value="b">B</option>
                </select>
                <button type="submit">Save</button>
            </form>`;
        const t = setup(html, handlers);
        t.win.document.querySelectorAll('option').forEach(o => { o.selected = chosen.includes(o.value); });
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).colors).toEqual(expected);
    });

    it('any repeated name is a list, in document order (getlist)', async () => {
        const html = `
            <form dj-submit="save_open">
                <input name="phone" value="111"><input name="phone" value="222">
                <input name="title" value="solo">
                <button type="submit">Save</button>
            </form>`;
        const t = setup(html, handlers);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(sentOnly(t).phone).toEqual(['111', '222']);
        expect(sentOnly(t).title).toBe('solo');
    });

    it('unchanged shapes: a lone checkbox, radios, text and a single select stay scalar', async () => {
        const html = `
            <form dj-submit="save_open">
                <input type="checkbox" name="agree" value="yes" checked>
                <input type="radio" name="size" value="s"><input type="radio" name="size" value="m" checked>
                <input name="title" value="hello">
                <select name="one"><option value="a">A</option><option value="b" selected>B</option></select>
                <button type="submit">Save</button>
            </form>`;
        const t = setup(html, handlers);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        const p = sentOnly(t);
        expect([p.agree, p.size, p.title, p.one]).toEqual(['yes', 'm', 'hello', 'b']);
        expect(p.tags).toBeUndefined();
    });

    it('an unticked lone checkbox is absent, as on a native post (not turned into a list)', async () => {
        const html = `
            <form dj-submit="save_open">
                <input type="checkbox" name="agree" value="yes">
                <button type="submit">Save</button>
            </form>`;
        const t = setup(html, handlers);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect('agree' in sentOnly(t)).toBe(false);
    });
});

describe('dj-change: a multi-valued control sends its list (#3959)', () => {
    const groupHtml = handler => `
        <form>
            <input type="checkbox" name="tags" value="x" dj-change="${handler}">
            <input type="checkbox" name="tags" value="y" dj-change="${handler}">
            <input type="checkbox" name="tags" value="z" dj-change="${handler}">
        </form>`;

    it('legacy: a change on one box of a group sends every checked value', async () => {
        const t = setup(groupHtml('change_legacy'), handlers);
        const [x, y] = t.win.document.querySelectorAll('input');
        toggle(t.win, x, true);
        toggle(t.win, y, true);
        await t.settle();
        const last = t.sent().at(-1).params;
        expect(last.value).toEqual(['x', 'y']);
        expect(last.field).toBe('tags');
    });

    it('un-ticking the last box sends an empty list', async () => {
        const t = setup(groupHtml('change_legacy'), handlers);
        const [x] = t.win.document.querySelectorAll('input');
        toggle(t.win, x, true);
        toggle(t.win, x, false);
        await t.settle();
        expect(t.sent().at(-1).params.value).toEqual([]);
    });

    it('strict, declared value: list[str]', async () => {
        const t = setup(groupHtml('change_list'), handlers);
        const [, y, z] = t.win.document.querySelectorAll('input');
        toggle(t.win, y, true);
        toggle(t.win, z, true);
        await t.settle();
        expect(t.sent().at(-1).params.value).toEqual(['y', 'z']);
        expect(t.errors()).toEqual([]);
    });

    it('the group is the checkboxes of the SAME form with that name, not the page', async () => {
        const html = `
            <form id="a"><input type="checkbox" name="tags" value="x" dj-change="change_legacy">
                         <input type="checkbox" name="tags" value="y" dj-change="change_legacy"></form>
            <form id="b"><input type="checkbox" name="tags" value="q" checked></form>`;
        const t = setup(html, handlers);
        toggle(t.win, t.$('#a input'), true);
        await t.settle();
        expect(t.sent().at(-1).params.value).toEqual(['x']);
    });

    it('<select multiple>: every selected option, not the first', async () => {
        const html = `
            <select name="colors" multiple dj-change="change_legacy">
                <option value="r">R</option><option value="g">G</option><option value="b">B</option>
            </select>`;
        const t = setup(html, handlers);
        const select = t.$('select');
        [...select.options].forEach(o => { o.selected = o.value !== 'g'; });
        select.dispatchEvent(new t.win.Event('change', {bubbles: true}));
        await t.settle();
        expect(t.sent().at(-1).params.value).toEqual(['r', 'b']);
    });

    it('unchanged shapes: a lone checkbox stays a boolean, a plain select a string', async () => {
        const html = `
            <input type="checkbox" name="agree" dj-change="change_legacy">
            <select name="one" dj-change="change_legacy"><option value="a">A</option><option value="b">B</option></select>`;
        const t = setup(html, handlers);
        toggle(t.win, t.$('input'), true);
        const select = t.$('select');
        select.value = 'b';
        select.dispatchEvent(new t.win.Event('change', {bubbles: true}));
        await t.settle();
        expect(t.sent().map(s => s.params.value)).toEqual([true, 'b']);
    });
});
