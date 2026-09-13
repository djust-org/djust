import { afterEach, describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'node:fs';

const code = fs.readFileSync('./python/djust/static/djust/client.js', 'utf8');
let dom;
afterEach(() => dom?.window.close());
function setup() {
    dom = new JSDOM('<div dj-root><section id="child"><p id="probe" dj-id="probe" dj-hook="Probe">before</p></section><p id="sibling" dj-hook="Probe">outside</p></div>', {
        url: 'http://localhost/', runScripts: 'dangerously', pretendToBeVisual: true,
    });
    dom.window.eval(code);
    const calls = [];
    dom.window.djust.hooks.Probe = {
        beforeUpdate() { calls.push([this.el.id, this.el.textContent, this.el.getAttribute('title')]); },
    };
    dom.window.djust.mountHooks(dom.window.document);
    return { api: dom.window.djust, doc: dom.window.document, calls };
}
describe('beforeUpdate through DOM mutation entry points (#2816)', () => {
    it.each([1, 11])('notifies once before a batch of %i patches', async count => {
        const { api, doc, calls } = setup();
        const patches = Array.from({ length: count }, (_, i) => ({ type: 'SetAttr', path: [0, 0], d: 'probe', key: 'title', value: String(i) }));
        expect(await api.applyPatches(patches, doc.querySelector('#child'))).toBe(true);
        expect(calls).toEqual([['probe', 'before', null]]);
        expect(doc.querySelector('#probe').title).toBe(String(count - 1));
    });
    it('scopes the real child update dispatcher to its child', async () => {
        const { api, doc, calls } = setup();
        const child = doc.querySelector('#child');
        child.setAttribute('dj-view', 'test.Child');
        child.setAttribute('data-djust-embedded', 'child');
        await api.childView.handleChildUpdate({ view_id: 'child', version: 1,
            patches: [{ type: 'SetAttr', path: [0], d: 'probe', key: 'title', value: 'changed' }] });
        expect(calls).toEqual([['probe', 'before', null]]);
        expect(doc.querySelector('#probe').title).toBe('changed');
    });
    it.each([false, true])('notifies once through full HTML dispatch (dj-update=%s)', async special => {
        const { api, doc, calls } = setup();
        const ws = new api.LiveViewWebSocket();
        ws.viewMounted = true;
        const html = `<section id="child"><p id="probe" dj-id="probe" dj-hook="Probe" title="changed">after</p></section><p id="sibling" dj-hook="Probe" ${special ? 'dj-update="ignore"' : ''}>outside</p>`;
        await ws.handleMessage({ type: 'html_update', html });
        expect(calls).toEqual([['probe', 'before', null], ['sibling', 'outside', null]]);
        expect(doc.querySelector('#probe').textContent).toBe('after');
    });
    it('lets a hook remove browser-owned children before morph snapshots', () => {
        const { api, doc } = setup();
        const probe = doc.querySelector('#probe');
        probe.innerHTML = '<b>toolbar</b><span>before</span>';
        api.destroyAllHooks();
        api.hooks.Probe = { beforeUpdate() { this.el.querySelector('b')?.remove(); } };
        api.mountHooks(doc);
        const desired = probe.cloneNode(false);
        desired.innerHTML = '<span>after</span>';
        api.morphElement(probe, desired);
        expect(probe.innerHTML).toBe('<span>after</span>');
    });
    it('does not notify for empty patches', async () => {
        const { api, calls } = setup();
        await api.applyPatches([]);
        expect(calls).toEqual([]);
    });
    it.each(['morphChildren', 'morphElement'])('notifies once before recursive %s', method => {
        const { api, doc, calls } = setup();
        const desired = doc.createElement('section');
        desired.id = 'child';
        desired.innerHTML = '<p id="probe" dj-id="probe" dj-hook="Probe" title="changed">after</p>';
        api[method](doc.querySelector('#child'), desired);
        expect(calls).toEqual([['probe', 'before', null]]);
        expect(doc.querySelector('#probe').textContent).toBe('after');
    });
});
