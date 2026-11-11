/**
 * #3958: under the strict event-parameter policy (ADR-036) the pressed submit
 * button was lost: `_target` (its only channel) is never generated under
 * strict (decision Q2), and the button's own name=value was never a field.
 *
 * Wire contract asserted here:
 *  - STRICT: the pressed button's name=value is a generated field, exactly as
 *    on a native post, and follows Q1 like every other generated field: sent
 *    only when the handler declares a parameter of that name or has a `**`
 *    catch-all. A closed handler that does not declare it never receives it
 *    (a page cannot inject a parameter by naming a button), and there is still
 *    no `_target`.
 *  - LEGACY: unchanged. `_target` stays the channel and no new field appears:
 *    a legacy handler with a closed signature rejects an unexpected parameter
 *    (validation.py: "received unexpected parameters"), so adding the button
 *    would break pages whose buttons carry a `name` today.
 *
 * Real DOM, a real `submit` event (requestSubmit / click) carrying the
 * `submitter`, through the bundled client over the HTTP fallback.
 */
import { describe, expect, it } from 'vitest';
import { legacy, param, setup, strict, submit } from './_form_events_harness.js';

const FORM = `
    <form id="f" dj-submit="HANDLER">
        <input name="title" value="Hello">
        <button type="submit" name="intent" value="draft">Save draft</button>
        <button type="submit" name="intent" value="submit">Submit</button>
        <button type="submit">Plain</button>
    </form>`;

const handlers = {
    open: strict(param('form', 'Any', 'var_keyword', false)),
    declared: strict(param('title', 'str'), param('intent', 'str', 'positional_or_keyword', false)),
    closed: strict(param('title', 'str')),
    old: legacy(),
};
const form = handler => FORM.replace('HANDLER', handler);
const buttons = t => t.win.document.querySelectorAll('button');
const last = t => t.sent().at(-1).params;

describe('dj-submit: the pressed button is a field under strict (#3958)', () => {
    it('a handler that declares `intent` receives the pressed button\'s value', async () => {
        const t = setup(form('declared'), handlers);
        submit(t.win, t.$('form'), buttons(t)[1]);
        await t.settle();
        expect(last(t).intent).toBe('submit');
        expect(last(t).title).toBe('Hello');
        expect(t.errors()).toEqual([]);
    });

    it('the OTHER button is distinguishable, and no `_target` is generated', async () => {
        const t = setup(form('declared'), handlers);
        submit(t.win, t.$('form'), buttons(t)[0]);
        await t.settle();
        expect(last(t).intent).toBe('draft');
        expect(Object.keys(last(t)).filter(k => k.startsWith('_'))).toEqual([]);
    });

    it('a **form_data handler receives it with the other fields', async () => {
        const t = setup(form('open'), handlers);
        submit(t.win, t.$('form'), buttons(t)[1]);
        await t.settle();
        expect(last(t).intent).toBe('submit');
        expect(last(t).title).toBe('Hello');
    });

    it('a real click on the button (jsdom activation behaviour) carries it too', async () => {
        const t = setup(form('declared'), handlers);
        buttons(t)[1].click();
        await t.settle();
        expect(last(t).intent).toBe('submit');
    });

    it('a closed handler that does not declare it never receives it, and is not rejected', async () => {
        const t = setup(form('closed'), handlers);
        submit(t.win, t.$('form'), buttons(t)[1]);
        await t.settle();
        expect(last(t)).toMatchObject({ title: 'Hello' });
        expect('intent' in last(t)).toBe(false);
        expect(t.errors()).toEqual([]);
    });

    it('an unnamed button, or a submit with no submitter, adds nothing', async () => {
        const t = setup(form('open'), handlers);
        submit(t.win, t.$('form'), buttons(t)[2]);
        t.$('form').dispatchEvent(new t.win.Event('submit', { bubbles: true, cancelable: true }));
        await t.settle();
        expect(t.sent().map(s => 'intent' in s.params)).toEqual([false, false]);
    });

    it('a button associated by form="id" from outside the form is the submitter too', async () => {
        const html = `
            <form id="f" dj-submit="declared"><input name="title" value="Hello"></form>
            <button type="submit" form="f" name="intent" value="outside">Go</button>`;
        const t = setup(html, handlers);
        t.$('button').click();
        await t.settle();
        expect(last(t).intent).toBe('outside');
    });

    it('a button name a handler could not declare is skipped, not a rejection', async () => {
        const html = `
            <form dj-submit="open"><input name="title" value="Hello">
                <button type="submit" name="save-draft" value="1">Go</button></form>`;
        const t = setup(html, handlers);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(last(t)).toEqual({ title: 'Hello' });
        expect(t.errors()).toEqual([]);
    });

    it('a button sharing a field\'s name joins it as a list, like native getlist', async () => {
        const html = `
            <form dj-submit="open"><input name="intent" value="typed">
                <button type="submit" name="intent" value="pressed">Go</button></form>`;
        const t = setup(html, handlers);
        submit(t.win, t.$('form'), t.$('button'));
        await t.settle();
        expect(last(t).intent).toEqual(['typed', 'pressed']);
    });
});

describe('dj-submit: legacy payload is unchanged (#3958)', () => {
    it('keeps `_target` and gains no field for the pressed button', async () => {
        const t = setup(form('old'), handlers);
        submit(t.win, t.$('form'), buttons(t)[1]);
        await t.settle();
        expect(last(t)._target).toBe('intent');
        expect('intent' in last(t)).toBe(false);
        expect(last(t).title).toBe('Hello');
    });
});
