import { describe, it, expect } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const client = readFileSync('./python/djust/static/djust/client.js', 'utf8');
const form = `<form id="editor" dj-submit="save">
<input id="title" name="title" dj-input="validate_field" value="">
<textarea id="body" name="body" dj-input="validate_field"></textarea>
<select id="category" name="category" dj-change="validate_field"><option value="a" selected>A</option><option value="b">B</option></select>
<input id="published" name="published" type="checkbox" dj-change="validate_field">
<input id="radio-a" name="choice" type="radio" value="a" checked dj-change="validate_field">
<input id="radio-b" name="choice" type="radio" value="b" dj-change="validate_field">
<input id="ignored" name="ignored" dj-no-recover dj-input="validate_field" value="">
<input id="forced" name="forced" dj-force-value dj-input="validate_field" value="">
</form>`;

async function setup() {
    const dom = new JSDOM(`<div dj-root dj-view="test.Editor">${form}</div>`, {
        runScripts: 'dangerously', url: 'http://localhost/',
    });
    const w = dom.window;
    w.DJUST_USE_WEBSOCKET = false;
    const events = [];
    w.fetch = async (url, options) => {
        await Promise.resolve();
        events.push(JSON.parse(options.body));
        return { ok: true, json: async () => ({ patches: [], version: events.length }) };
    };
    w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
    w.eval(client);
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    await Promise.resolve();
    const socket = new w.djust.LiveViewWebSocket();
    socket.primaryViewPath = 'test.Editor';
    socket.skipMountHtml = true;
    return { dom, w, socket, events };
}

async function settle(w) {
    // Mount reinitialization and sequential recovery both cross async boundaries.
    for (let i = 0; i < 20; i++) await new Promise(resolve => w.setTimeout(resolve, 0));
}

for (const prerendered of [true, false]) {
    describe(`reconnect mount, prerendered=${prerendered}`, () => {
        it('preserves unfocused values and replays them after the real mount handler', async () => {
            const { dom, w, socket, events } = await setup();
            try {
                const d = w.document;
                d.querySelector('#title').value = 'Draft title';
                d.querySelector('#body').value = 'Unsaved body';
                d.querySelector('#category').value = 'b';
                d.querySelector('#published').checked = true;
                d.querySelector('#radio-b').checked = true;
                d.querySelector('#ignored').value = 'opted out';
                d.querySelector('#forced').value = 'server owned';
                d.querySelector('#title').focus();
                w.djust._isReconnect = true;
                socket.skipMountHtml = prerendered;
                await socket.handleMessage({ type: 'mount', view: 'test.Editor', html: form, has_ids: true });
                await settle(w);
                expect(d.querySelector('#body').value).toBe('Unsaved body');
                expect(d.querySelector('#category').value).toBe('b');
                expect(d.querySelector('#published').checked).toBe(true);
                expect(d.querySelector('#radio-b').checked).toBe(true);
                const params = events;
                expect(params).toEqual(expect.arrayContaining([
                    expect.objectContaining({ field: 'title', value: 'Draft title' }),
                    expect.objectContaining({ field: 'body', value: 'Unsaved body' }),
                    expect.objectContaining({ field: 'category', value: 'b' }),
                    expect.objectContaining({ field: 'published', value: true }),
                    expect.objectContaining({ field: 'choice', value: 'b' }),
                ]));
                expect(params.filter(p => p.field === 'choice')).toHaveLength(1);
                expect(params.some(p => ['ignored', 'forced'].includes(p.field))).toBe(false);
                expect(d.querySelector('#ignored').value).toBe('');
                expect(d.querySelector('#forced').value).toBe('');
                expect(w.djust._isReconnect).toBe(false);
                const eventCount = events.length;
                await socket.handleMessage({ type: 'html_update', html: `<div dj-root dj-view="test.Editor">${form}</div>`, version: events.length + 1 });
                await settle(w);
                expect(d.querySelector('#body').value).toBe('');
                expect(events).toHaveLength(eventCount);
            } finally { dom.window.close(); }
        });

        it('preserves custom recovery values and keeps repeated names scoped to forms', async () => {
            const { dom, w, socket, events } = await setup();
            try {
                const markup = `<form id="first"><input name="title" dj-input="validate_field" value=""></form>
                <form id="second"><input name="title" dj-input="validate_field" value=""></form>
                <div dj-auto-recover="custom_restore"><textarea name="notes"></textarea></div>`;
                const root = w.document.querySelector('[dj-root]');
                root.innerHTML = markup;
                root.querySelector('#first input').value = 'First draft';
                root.querySelector('#second input').value = 'Second draft';
                root.querySelector('textarea').value = 'Custom draft';
                w.djust._isReconnect = true;
                socket.skipMountHtml = prerendered;
                await socket.handleMessage({ type: 'mount', view: 'test.Editor', html: markup, has_ids: true });
                await settle(w);
                expect(root.querySelector('#first input').value).toBe('First draft');
                expect(root.querySelector('#second input').value).toBe('Second draft');
                expect(events.filter(event => event._form_values)).toEqual([
                    expect.objectContaining({ _form_values: { notes: 'Custom draft' } }),
                ]);
                expect(events.some(event => event.field === 'notes')).toBe(false);
            } finally { dom.window.close(); }
        });

        it('does not restore a removed field into a different field reusing its ID', async () => {
            const { dom, w, socket, events } = await setup();
            try {
                w.document.querySelector('#body').value = 'Private draft';
                w.djust._isReconnect = true;
                socket.skipMountHtml = prerendered;
                const renamed = form.replace('name="body"', 'name="other"');
                await socket.handleMessage({ type: 'mount', view: 'test.Editor', html: renamed, has_ids: true });
                await settle(w);
                expect(w.document.querySelector('#body').value).toBe('');
                expect(events).toHaveLength(0);
            } finally { dom.window.close(); }
        });

        it('does not resurrect a draft on an ordinary mount/navigation', async () => {
            const { dom, w, socket, events } = await setup();
            try {
                w.document.querySelector('#body').value = 'Discarded draft';
                socket.skipMountHtml = prerendered;
                await socket.handleMessage({ type: 'mount', view: 'test.Editor', html: form, has_ids: true });
                await settle(w);
                expect(w.document.querySelector('#body').value).toBe('');
                expect(events).toHaveLength(0);
            } finally { dom.window.close(); }
        });
    });
}
