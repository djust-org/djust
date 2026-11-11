/**
 * #3959, parallel path (#1646): reconnect form recovery replays a field's DOM
 * value as a synthetic dj-change. For a checkbox group / <select multiple> it
 * must replay the same LIST a live dj-change sends, once per group, not one
 * `checked` boolean per box.
 */
import { describe, expect, it } from 'vitest';
import { legacy, param, setup, strict } from './_form_events_harness.js';

const handlers = { pick: legacy(), pick_list: strict(param('value', 'list[str]'), param('field', 'str')) };

const group = (handler, checked = []) => `
    <form>
        ${['x', 'y', 'z'].map(v =>
            `<input type="checkbox" name="tags" value="${v}" dj-change="${handler}"${checked.includes(v) ? ' checked' : ''}>`
        ).join('\n        ')}
    </form>`;

async function recover(t) {
    t.win.djust._isReconnect = true;
    t.win.djust._processFormRecovery();
    await t.settle(80);
    return t.sent();
}
const tick = (t, ...values) => t.win.document.querySelectorAll('input').forEach(b => { b.checked = values.includes(b.value); });

describe('form recovery replays a multi-valued field as one list (#3959)', () => {
    it('a checkbox group is ONE event carrying every checked value', async () => {
        const t = setup(group('pick'), handlers);
        tick(t, 'x', 'y');
        const sent = await recover(t);
        expect(sent.map(s => [s.event, s.params.field, s.params.value])).toEqual([['pick', 'tags', ['x', 'y']]]);
    });

    it('strict, declared list[str]: accepted', async () => {
        const t = setup(group('pick_list'), handlers);
        tick(t, 'z');
        const sent = await recover(t);
        expect(sent.map(s => s.params.value)).toEqual([['z']]);
        expect(t.errors()).toEqual([]);
    });

    it('an unticked-since-render box is recovered: server defaults x,y; user left only x', async () => {
        const t = setup(group('pick', ['x', 'y']), handlers);
        tick(t, 'x');
        const sent = await recover(t);
        expect(sent.map(s => s.params.value)).toEqual([['x']]);
    });

    it('a group still equal to its server default sends nothing', async () => {
        const t = setup(group('pick', ['x', 'y']), handlers);
        expect(await recover(t)).toEqual([]);
    });

    it('<select multiple>: the selected options, and nothing when they match the default', async () => {
        const html = `
            <select name="colors" multiple dj-change="pick">
                <option value="r" selected>R</option><option value="g">G</option><option value="b">B</option>
            </select>`;
        const t = setup(html, handlers);
        expect(await recover(t)).toEqual([]);
        t.win.document.querySelectorAll('option').forEach(o => { o.selected = o.value !== 'g'; });
        const sent = await recover(t);
        expect(sent.map(s => s.params.value)).toEqual([['r', 'b']]);
    });
});
