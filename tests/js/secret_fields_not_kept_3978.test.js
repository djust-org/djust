/**
 * A password field is never written to browser storage by the draft manager,
 * never sent in the `dj-auto-recover` envelope, and never collected by
 * `_collectFormData`, even when the page marks it `data-draft="true"`.
 * A field marked `autocomplete="new-password"` / `"current-password"` counts
 * as a password whatever its type.
 */
import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { legacy, setup } from './_form_events_harness.js';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

const FIELDS = `
    <input type="text" name="user" value="ann" data-draft="true">
    <input type="password" name="pw" value="hunter2" data-draft="true">
    <input type="text" name="pin" value="1234" autocomplete="current-password" data-draft="true">
    <input type="text" name="newpw" value="s3cret" autocomplete="new-password" data-draft="true">
    <input type="hidden" name="token" value="hid" data-draft="true">
    <input type="text" name="locked" value="dis" disabled data-draft="true">
`;
const SECRETS = ['hunter2', '1234', 's3cret'];

function draftPage(saved) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-view="app.Page">
            <form data-draft-enabled data-draft-key="k">${FIELDS}</form>
        </div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' });
    const win = dom.window;
    if (saved) win.localStorage.setItem('djust_draft_k', JSON.stringify(saved));
    win.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.fetch = async () => ({ ok: true, json: async () => ({ patches: [], version: 1 }) });
    `);
    win.console.error = () => {};
    win.eval(source);
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    return win;
}

describe('secret fields stay out of drafts, auto-recover and _collectFormData', () => {
    it('the draft manager never stores a password value', async () => {
        const win = draftPage();
        const user = win.document.querySelector('input[name="user"]');
        user.value = 'bob';
        user.dispatchEvent(new win.Event('input', { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 650));
        const raw = win.localStorage.getItem('djust_draft_k');
        expect(raw).toContain('bob'); // the save did happen
        for (const secret of SECRETS) expect(raw).not.toContain(secret);
        const data = JSON.parse(raw).data;
        expect(Object.keys(data).sort()).toEqual(['locked', 'token', 'user']);
    });

    it('a stored draft is never written into a password field', () => {
        const win = draftPage({ data: { pw: 'from-storage', user: 'zed' }, timestamp: Date.now(), format: 2 });
        expect(win.document.querySelector('input[name="user"]').value).toBe('zed');
        expect(win.document.querySelector('input[name="pw"]').value).toBe('hunter2');
    });

    it('the dj-auto-recover envelope carries no password value', async () => {
        const t = setup(`<div dj-auto-recover="restore_state">${FIELDS}</div>`, { restore_state: legacy() });
        t.win.djust._isReconnect = true;
        t.win.djust._processAutoRecover();
        await t.settle();
        const sent = t.sent().find(s => s.event === 'restore_state');
        const raw = JSON.stringify(sent.params);
        for (const secret of SECRETS) expect(raw).not.toContain(secret);
        expect(sent.params._form_values.user).toBe('ann');
    });

    it('_collectFormData skips a password field', () => {
        const FORM_SRC = readFileSync('./python/djust/static/djust/src/07-form-data.js', 'utf8');
        // eslint-disable-next-line no-new-func -- evaluates the real bundle module (test-only)
        const collect = new Function('djLog', 'UNSAFE_KEYS', FORM_SRC + '\nreturn _collectFormData;')(
            () => {}, ['__proto__', 'constructor', 'prototype']);
        const dom = new JSDOM(`<form>${FIELDS}</form>`);
        const raw = JSON.stringify(collect(dom.window.document.querySelector('form')));
        for (const secret of SECRETS) expect(raw).not.toContain(secret);
    });
});
