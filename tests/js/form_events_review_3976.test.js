/**
 * Review findings on PR #3976 (#3959, #3958, #3963), one suite per finding:
 *
 *  - reserved submitter names, on BOTH transports, on the exact bytes that left
 *    the client (the HTTP fallback reads a flat body containing `event` as its
 *    {event, params} envelope, so a button named `event` re-routed the call);
 *  - what a dj-change handler on a same-named checkbox group now receives (an
 *    owner decision: the list, so FormMixin.validate_field can validate a
 *    MultipleChoiceField; pinned so the change is visible, not silent);
 *  - the dj-input / dj-blur / dj-focus twins of the dj-change list;
 *  - dj-disable-with re-enabling a button that was disabled before the submit.
 */
import { describe, expect, it } from 'vitest';
import { legacy, param, setup, strict, submit, toggle } from './_form_events_harness.js';

const handlers = {
    open: strict(param('form', 'Any', 'var_keyword', false)),
    toggle: legacy(),
    toggle_bool: strict(param('value', 'bool'), param('field', 'str')),
    toggle_list: strict(param('value', 'list[str]'), param('field', 'str')),
    advance: legacy(),
};

describe.each(['http', 'ws'])('reserved submitter names (%s transport)', transport => {
    const html = name => `
        <form dj-submit="open"><input name="title" value="Hello">
            <button type="submit" name="${name}" value="INJ">Go</button></form>`;

    const press = async name => {
        const t = setup(html(name), handlers, undefined, { transport });
        await t.ready;
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        return t;
    };

    it.each([
        ['event', 'the HTTP envelope key'],
        ['params', 'the HTTP envelope key'],
        ['component_id', 'routing'],
        ['view_id', 'routing'],
        ['csrfmiddlewaretoken', 'the form\'s own token field'],
        ['constructor', 'a prototype key'],
        ['prototype', 'a prototype key'],
        ['__proto__', 'a prototype key'],
        ['_target', 'a framework-reserved name'],
    ])('a button named %s (%s) is not sent, and the event still goes through', async name => {
        const t = await press(name);
        expect(t.sent()).toHaveLength(1);
        expect(t.sent()[0].event).toBe('open');
        expect(Object.keys(t.sent()[0].params)).toEqual(['title']);
        expect(t.raw()[0]).not.toContain('INJ');
        expect(t.errors()).toEqual([]);
    });

    it('control: an ordinary name is sent', async () => {
        const t = await press('intent');
        expect(t.sent()[0].params.intent).toBe('INJ');
    });
});

describe('a button name no handler could declare is skipped, not a rejection', () => {
    it.each(['save-draft', '_x', 'café', '1st', 'a.b'])('%s', async name => {
        const html = `
            <form dj-submit="open"><input name="title" value="Hello">
                <button type="submit" name="${name}" value="1">Go</button></form>`;
        const t = setup(html, handlers);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(t.sent().at(-1).params).toEqual({ title: 'Hello' });
        expect(t.errors()).toEqual([]);
    });
});

describe('what a dj-change handler on a same-named checkbox group receives (owner decision)', () => {
    // A per-row toggle (`<input type="checkbox" name="done" dj-change="toggle_todo">`
    // on every row) used `value` as "is THIS box ticked". It is now the list of
    // checked values of the group: the form, or with no form the page.
    const rows = `
        <input type="checkbox" name="done" value="1" dj-change="toggle">
        <input type="checkbox" name="done" value="2" dj-change="toggle">`;

    it('untyped `value=False, **kw`: the list, truthy while any box is ticked', async () => {
        const t = setup(rows, handlers);
        const [a, b] = t.win.document.querySelectorAll('input');
        toggle(t.win, a, true);
        await t.settle();
        toggle(t.win, b, true);
        await t.settle();
        toggle(t.win, a, false);
        await t.settle();
        expect(t.sent().map(s => s.params.value)).toEqual([['1'], ['1', '2'], ['2']]);
        // Unticking `a` while `b` stays ticked is NOT "false" any more.
        expect(Boolean(t.sent().at(-1).params.value)).toBe(true);
    });

    it('strict `value: bool`: the client sends the list (the server binder rejects it)', async () => {
        const html = rows.replaceAll('"toggle"', '"toggle_bool"');
        const t = setup(html, handlers);
        toggle(t.win, t.win.document.querySelector('input'), true);
        await t.settle();
        expect(t.sent().at(-1).params.value).toEqual(['1']);
    });

    it('distinct names keep the per-box boolean', async () => {
        const html = `
            <input type="checkbox" name="done_1" dj-change="toggle">
            <input type="checkbox" name="done_2" dj-change="toggle">`;
        const t = setup(html, handlers);
        toggle(t.win, t.$('input'), true);
        await t.settle();
        expect(t.sent().at(-1).params.value).toBe(true);
    });
});

describe('dj-input / dj-blur / dj-focus on a multi-valued control send its list', () => {
    const select = attr => `
        <select name="colors" multiple ${attr}="toggle">
            <option value="r">R</option><option value="g">G</option><option value="b">B</option>
        </select>`;

    it.each([['dj-input', 'input'], ['dj-blur', 'focusout'], ['dj-focus', 'focusin']])(
        '%s on a <select multiple>: every selected option', async (attr, domEvent) => {
            const t = setup(select(attr), handlers);
            const el = t.$('select');
            [...el.options].forEach(o => { o.selected = o.value !== 'g'; });
            el.dispatchEvent(new t.win.Event(domEvent, { bubbles: true }));
            await t.settle(400);
            expect(t.sent().at(-1).params.value).toEqual(['r', 'b']);
        });

    it.each([['dj-input', 'input'], ['dj-blur', 'focusout'], ['dj-focus', 'focusin']])(
        'strict, declared list[str]: %s sends the list and is not rejected', async (attr, domEvent) => {
            const t = setup(select(attr).replace('"toggle"', '"toggle_list"'), handlers);
            const el = t.$('select');
            [...el.options].forEach(o => { o.selected = o.value !== 'g'; });
            el.dispatchEvent(new t.win.Event(domEvent, { bubbles: true }));
            await t.settle(400);
            expect(t.sent().at(-1).params.value).toEqual(['r', 'b']);
            expect(t.errors()).toEqual([]);
        });

    it('dj-input on one box of a checkbox group: the checked values', async () => {
        const html = `<form>${['x', 'y'].map(v =>
            `<input type="checkbox" name="tags" value="${v}" dj-input="toggle">`).join('')}</form>`;
        const t = setup(html, handlers);
        const [x, y] = t.win.document.querySelectorAll('input');
        x.checked = true;
        y.checked = true;
        x.dispatchEvent(new t.win.Event('input', { bubbles: true }));
        await t.settle(400);
        expect(t.sent().at(-1).params.value).toEqual(['x', 'y']);
    });

    it('unchanged: text keeps its string, a lone checkbox keeps its value attribute', async () => {
        const html = `
            <input name="q" value="hello" dj-input="toggle">
            <input type="checkbox" name="solo" value="yes" dj-input="toggle">`;
        const t = setup(html, handlers);
        const [text, box] = t.win.document.querySelectorAll('input');
        text.dispatchEvent(new t.win.Event('input', { bubbles: true }));
        await t.settle(400);
        box.checked = true;
        box.dispatchEvent(new t.win.Event('input', { bubbles: true }));
        await t.settle(400);
        expect(t.sent().map(s => s.params.value)).toEqual(['hello', 'yes']);
    });
});

describe('dj-disable-with restores only what the submit itself disabled', () => {
    const TWO = `
        <p>Step 1 of 2</p>
        <form dj-submit="advance">
            <button type="submit" name="next" dj-disable-with="Saving">Next</button>
            <button type="submit" name="other" dj-disable-with="Saving" disabled>Other</button>
        </form>
    `;

    it('a sibling submit button that was disabled BEFORE the submit stays disabled', async () => {
        const t = setup(TWO, handlers, () => ({ patches: [] }));
        const [next, other] = t.win.document.querySelectorAll('button');
        expect(other.disabled).toBe(true);
        submit(t.win, t.$('form'), next);
        expect([next.disabled, next.textContent]).toEqual([true, 'Saving']);
        await t.settle();
        expect([next.disabled, next.textContent]).toEqual([false, 'Next']);
        expect([other.disabled, other.textContent]).toEqual([true, 'Other']);
        expect(other.hasAttribute('data-djust-was-disabled')).toBe(false);
        expect(other.hasAttribute('data-djust-original-text')).toBe(false);
    });
});
