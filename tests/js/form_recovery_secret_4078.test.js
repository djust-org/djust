/**
 * #4078: the reconnect form-recovery path applies the same password rule as
 * drafts, `dj-auto-recover` and `_collectFormData` (#4067). A password field
 * (`type="password"`, or `autocomplete="new-password"` / `"current-password"`)
 * is not captured before the reconnect mount, not written back into the new
 * markup, and not replayed to the server. Hidden and disabled controls keep
 * their existing treatment (owner decision on #4078).
 *
 * The last block pins the caller set of the shared predicate, so a new path
 * that keeps or re-sends form values has to decide it explicitly.
 */
import { describe, expect, it } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync, readdirSync } from 'node:fs';

const source = readFileSync('./python/djust/static/djust/client.js', 'utf8');

const FIELDS = `
    <input type="text" name="user" value="" dj-input="set_user">
    <input type="password" name="pw" value="" dj-input="set_pw">
    <input type="text" name="pin" value="" autocomplete="current-password" dj-input="set_pin">
    <input type="text" name="newpw" value="" autocomplete="New-Password" dj-change="set_newpw">
`;
const SECRETS = ['hunter2', '1234', 's3cret'];

function page() {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root dj-view="app.Page"><form id="f">${FIELDS}</form></div></body></html>`,
        { runScripts: 'dangerously', url: 'http://localhost/' });
    const win = dom.window;
    win.eval(`
        window.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
        window.DJUST_USE_WEBSOCKET = false;
        window.location.reload = function() {};
        window._sent = [];
        window.fetch = async function(url, opts) {
            var body = {};
            try { body = JSON.parse((opts && opts.body) || "{}"); } catch (e) {}
            window._sent.push({ event: (opts && opts.headers && opts.headers["X-Djust-Event"]) || "", body: body });
            return { ok: true, json: async function() { return { patches: [], version: 1 }; } };
        };
    `);
    win.console.error = () => {};
    win.eval(source);
    win.document.dispatchEvent(new win.Event('DOMContentLoaded'));
    return win;
}

function type(win) {
    const q = name => win.document.querySelector(`[name="${name}"]`);
    q('user').value = 'ann';
    q('pw').value = 'hunter2';
    q('pin').value = '1234';
    q('newpw').value = 's3cret';
}

function snapshotText(snapshot) {
    return JSON.stringify(Array.from(snapshot.values.entries()));
}

describe('reconnect form recovery skips password fields (#4078)', () => {
    it('_captureFormRecovery holds no password value', () => {
        const win = page();
        type(win);
        const snapshot = win.djust._captureFormRecovery();
        const raw = snapshotText(snapshot);
        expect(raw).toContain('ann'); // the capture did happen
        for (const secret of SECRETS) expect(raw).not.toContain(secret);
    });

    it('_restoreFormRecovery writes nothing into a password field', () => {
        const win = page();
        // A snapshot taken by an older client (or forged) that still carries
        // a password value: the restore must not write it into the new markup.
        type(win);
        const fields = Array.from(win.document.querySelectorAll('input'));
        const values = new Map();
        const form = win.document.getElementById('f');
        for (const field of fields) {
            const key = JSON.stringify([
                form.getAttribute('id'), field.id, field.name, field.tagName, field.type, null,
                field.getAttribute('dj-change'), field.getAttribute('dj-input'),
            ]);
            values.set(key, [{ value: 'restored-' + field.name, checked: false, selected: null }]);
            field.value = '';
        }
        win.djust._restoreFormRecovery({ view: 'app.Page', values });
        const q = name => win.document.querySelector(`[name="${name}"]`).value;
        expect(q('user')).toBe('restored-user');
        expect(q('pw')).toBe('');
        expect(q('pin')).toBe('');
        expect(q('newpw')).toBe('');
    });

    it('_processFormRecovery replays no password value to the server', async () => {
        const win = page();
        type(win);
        win.djust._isReconnect = true;
        win.djust._processFormRecovery();
        await new Promise(resolve => setTimeout(resolve, 50));
        const raw = JSON.stringify(win._sent);
        expect(raw).toContain('ann'); // the replay did happen
        for (const secret of SECRETS) expect(raw).not.toContain(secret);
    });

    it('a disabled field stays out of reconnect recovery, as before (owner decision)', () => {
        const win = page();
        type(win);
        win.document.querySelector('[name="user"]').disabled = true;
        const raw = snapshotText(win.djust._captureFormRecovery());
        expect(raw).not.toContain('ann');
    });
});

describe('one password predicate for every keeping path (#4078)', () => {
    const SRC_DIR = './python/djust/static/djust/src';
    const callers = new Map();
    for (const file of readdirSync(SRC_DIR).filter(f => f.endsWith('.js'))) {
        const text = readFileSync(`${SRC_DIR}/${file}`, 'utf8');
        // Name of the enclosing top-level function for each call site.
        const lines = text.split('\n');
        let current = null;
        lines.forEach(line => {
            const fn = /^(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(/.exec(line);
            if (fn) current = fn[1];
            if (/_isSecretControl\(/.test(line) && !/^function\s+_isSecretControl/.test(line)) {
                callers.set(`${file}:${current}`, true);
            }
        });
    }

    it('_isSecretControl is called by exactly the value-keeping readers and writers', () => {
        expect(Array.from(callers.keys()).sort()).toEqual([
            '07-form-data.js:_readControls',
            '07-form-data.js:_writeControl',
            '09-event-binding.js:_formRecoveryFields',
        ]);
    });

    it('drafts, dj-auto-recover and _collectFormData read controls through _readControls', () => {
        const read = name => readFileSync(`${SRC_DIR}/${name}`, 'utf8');
        expect(read('06-draft-manager.js')).toMatch(/_readControls\(/);
        expect(read('07-form-data.js')).toMatch(/function _collectFormData[\s\S]*?_readControls\(/);
        expect(read('09-event-binding.js')).toMatch(/function _processAutoRecover[\s\S]*?_readControls\(/);
        // The reconnect capture, restore and replay all enumerate through
        // _formRecoveryFields.
        const binding = read('09-event-binding.js');
        for (const fn of ['_captureFormRecovery', '_restoreFormRecovery', '_processFormRecovery']) {
            const body = new RegExp(`function ${fn}\\([\\s\\S]*?\\n}`).exec(binding)[0];
            expect(body, fn).toMatch(/_formRecoveryFields\(/);
        }
    });
});
