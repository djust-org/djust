/**
 * #2985 batch 5 — ImageUploadPreview answers its dj-hook on top of djust's own
 * upload pipeline (dj-upload / dj-upload-drop, djust:upload:progress,
 * window.djust.uploads.cancelUpload), and an app's own hook of the same name
 * still wins.
 *
 * The markup is what the component renders (python/djust/tests/
 * test_component_batch5_image_upload_2985.py pins it on every render path).
 * jsdom has no DataTransfer, no object URLs and a FileList that cannot be
 * assigned, so those three are small fakes here; the real thing is checked in
 * Chromium by tests/playwright/test_component_batch5_image_upload_2985.py.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');
const SCRIPT = read('image-upload-preview.js');

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const ITEMS = '<ul class="dj-img-upload__items" aria-label="Selected images"></ul><div class="dj-img-upload__status" role="status" aria-live="polite"></div>';

const UP = ({ slot = 'photos', max = 3, maxSize = 0, event = 'photos_uploaded', accept = 'image/*', previews = [] } = {}) =>
    `<div class="dj-img-upload" dj-hook="ImageUploadPreview" data-event="${esc(event || 'upload')}" data-max="${max}"` +
    (event ? ' data-notify="true"' : '') + (slot ? ` data-upload="${esc(slot)}"` : '') + (maxSize ? ` data-max-size="${maxSize}"` : '') + '>' +
    `<label class="dj-img-upload__dropzone"${slot ? ` dj-upload-drop="${esc(slot)}"` : ''}><svg class="dj-img-upload__icon"></svg>` +
    '<span class="dj-img-upload__text">Drop images here or click to upload</span>' +
    `<input type="file" name="photos" accept="${esc(accept)}" multiple class="dj-img-upload__input" aria-label="Upload images"${slot ? ` dj-upload="${esc(slot)}"` : ''}></label>` +
    (previews.length ? `<div class="dj-img-upload__previews">${previews.map((u) => `<div class="dj-img-upload__thumb"><img src="${esc(u)}" alt="Preview" class="dj-img-upload__thumb-img"></div>`).join('')}</div>` : '') +
    ITEMS + '</div>';

function createEnv(bodyHtml, { preRegister, dataTransfer = true, setFiles = true, connected = true } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...args) => warnings.push(args.join(' ')),
    };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    const urls = { made: [], revoked: [] };
    let n = 0;
    window.URL.createObjectURL = (f) => { const u = `blob:mock/${++n}/${f.name}`; urls.made.push(u); return u; };
    window.URL.revokeObjectURL = (u) => { urls.revoked.push(u); };
    if (dataTransfer) {
        window.DataTransfer = class {
            constructor() { this._f = []; this.items = { add: (f) => { this._f.push(f); } }; }
            get files() { return this._f.slice(); }
        };
    }
    const sent = [];
    window.djust.handleEvent = (name, params) => { sent.push({ name, params }); };
    window.djust.uploads = window.djust.uploads || {};
    window.djust.uploads.activeUploads = new Map();
    window.djust.uploads.cancelUpload = vi.fn((ref) => { window.djust.uploads.activeUploads.delete(ref); });
    window.djust.liveViewInstance = connected ? { ws: { readyState: 1 } } : null;
    if (preRegister) preRegister(window);
    return { window, warnings, urls, sent, setFiles };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function file(window, name, { type = 'image/png', size = 10 } = {}) {
    const f = new window.File([new Uint8Array(Math.max(1, size))], name, { type, lastModified: 1000 });
    return f;
}

async function boot(markup, opts) {
    const env = createEnv(markup, opts);
    env.window.eval(SCRIPT);
    env.window.djust.mountHooks();
    await tick();
    const $ = (sel) => env.window.document.querySelector(sel);
    const $$ = (sel) => Array.from(env.window.document.querySelectorAll(sel));
    Object.assign(env, { $, $$ });
    env.hook = () => env.window.djust.getHook($('[dj-hook="ImageUploadPreview"]'));
    env.input = $('.dj-img-upload__input');
    // The input's FileList: jsdom cannot assign one, so the instance gets its own accessor.
    let current = [];
    const assigned = [];
    Object.defineProperty(env.input, 'files', {
        configurable: true,
        get: () => current,
        set: (v) => { if (!opts || opts.setFiles !== false) { current = Array.from(v); assigned.push(current); } else throw new TypeError('no'); },
    });
    env.assigned = assigned;
    env.choose = (files) => {
        current = files.slice();
        let chosen = 0;
        env.input.addEventListener('change', () => { chosen += 1; }, { once: true });
        env.input.dispatchEvent(new env.window.Event('change', { bubbles: true }));
        return chosen;
    };
    env.files = () => current.slice();
    env.items = () => $$('.dj-img-upload__item');
    env.names = () => env.items().map((li) => li.querySelector('.dj-img-upload__item-name').textContent);
    env.states = () => env.items().map((li) => li.getAttribute('data-state'));
    env.live = () => $('.dj-img-upload__status').textContent.trim();
    env.progress = (detail) => env.window.dispatchEvent(new env.window.CustomEvent('djust:upload:progress', { detail }));
    env.register = (ref, f) => env.window.djust.uploads.activeUploads.set(ref, { file: f, uploadName: 'photos' });
    env.press = (li, action) => li.querySelector(`button[data-action="${action}"]`).click();
    env.patch = (fn) => { fn(); env.window.djust.updateHooks(); };
    return env;
}

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

describe('the shipped hook never replaces an app hook (#2985 batch 5)', () => {
    const probe = (env) => env.$('.dj-img-upload__items').hasAttribute('dj-update');

    it('registers a hook, so mounting logs no "No hook registered"', async () => {
        const env = await boot(UP());
        expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
        expect(probe(env)).toBe(true);
    });

    it('without the script the warning is logged (gate-off)', () => {
        const env = createEnv(UP());
        env.window.djust.mountHooks();
        expect(env.warnings.some((w) => w.includes('No hook registered for "ImageUploadPreview"'))).toBe(true);
    });

    it('an app hook in either registry, registered first, is the one that runs', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = await boot(UP(), { preRegister: (w) => { if (where === 'DjustHooks') w.DjustHooks = { ImageUploadPreview: { mounted } }; else w.djust.hooks = { ImageUploadPreview: { mounted } }; } });
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(probe(env)).toBe(false);
        }
    });

    it('an app hook registered AFTER the script still wins, in either registry', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = createEnv(UP());
            env.window.eval(SCRIPT);
            if (where === 'djust.hooks') env.window.djust.hooks = { ImageUploadPreview: { mounted } };
            else env.window.DjustHooks.ImageUploadPreview = { mounted };
            env.window.djust.mountHooks();
            await tick();
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(env.window.document.querySelector('.dj-img-upload__items').hasAttribute('dj-update')).toBe(false);
        }
    });

    it("an app hook's pushEvent/handleEvent API is untouched", async () => {
        const seen = [];
        await boot(UP(), { preRegister: (w) => { w.djust.hooks = { ImageUploadPreview: { mounted() { seen.push(typeof this.pushEvent, typeof this.handleEvent); } } }; } });
        expect(seen).toEqual(['function', 'function']);
    });
});

// ---------------------------------------------------------------------------
// Choosing
// ---------------------------------------------------------------------------

describe('choosing images', () => {
    it('shows a thumbnail per image at once, from an object URL, with name and size', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'cat.png', { size: 2048 });
        const b = file(env.window, 'dog.jpg', { type: 'image/jpeg', size: 10 });
        env.choose([a, b]);
        expect(env.names()).toEqual(['cat.png', 'dog.jpg']);
        expect(env.states()).toEqual(['uploading', 'uploading']);
        expect(env.$$('.dj-img-upload__item-img').map((i) => i.getAttribute('src'))).toEqual(['blob:mock/1/cat.png', 'blob:mock/2/dog.jpg']);
        expect(env.$$('.dj-img-upload__item-meta').map((m) => m.textContent)).toEqual(['2.0 KB', '10 B']);
        expect(env.live()).toBe('2 images chosen');
        expect(env.assigned).toEqual([]); // nothing refused: the input is left exactly as chosen
    });

    it('announces the same thing twice in a row by changing the text (screen readers skip an identical one)', async () => {
        const env = await boot(UP({ max: 1 }));
        env.choose([file(env.window, 'a.png')]);
        const first = env.$('.dj-img-upload__status').textContent;
        env.choose([file(env.window, 'b.png')]);
        const second = env.$('.dj-img-upload__status').textContent;
        expect(first).toBe('1 image chosen');
        expect(second).not.toBe(first);
        expect(second.trim()).toBe('1 image chosen');
    });

    it('lists a file that fails a pre-check with the reason, never creates its URL, and holds it back from the input', async () => {
        const env = await boot(UP({ max: 5, maxSize: 100 }));
        const ok = file(env.window, 'ok.png');
        const doc = file(env.window, 'notes.pdf', { type: 'application/pdf' });
        const big = file(env.window, 'big.png', { size: 500 });
        env.choose([ok, doc, big]);
        expect(env.states()).toEqual(['uploading', 'refused', 'refused']);
        expect(env.$$('.dj-img-upload__item-note').map((n) => n.textContent)).toEqual([
            '0%'.replace('0%', 'Uploading'),
            'Not sent: not an accepted type (image/*)',
            'Not sent: larger than 100 B',
        ]);
        expect(env.urls.made).toEqual(['blob:mock/1/ok.png']);
        expect(env.assigned).toHaveLength(1);
        expect(env.files().map((f) => f.name)).toEqual(['ok.png']);
        expect(env.live()).toBe('1 image chosen. notes.pdf not an accepted type (image/*), not sent. big.png larger than 100 B, not sent');
    });

    it('checks the type by extension and by wildcard, and leaves an untyped file to the server', async () => {
        const env = await boot(UP({ accept: '.png,image/webp', max: 9 }));
        const png = file(env.window, 'a.PNG', { type: '' });
        const webp = file(env.window, 'b.bin', { type: 'image/webp' });
        const gif = file(env.window, 'c.gif', { type: 'image/gif' });
        const unknown = file(env.window, 'noext', { type: '' });
        env.choose([png, webp, gif, unknown]);
        expect(env.states()).toEqual(['uploading', 'uploading', 'refused', 'uploading']);
        expect(env.names()).toEqual(['a.PNG', 'b.bin', 'c.gif', 'noext']);
    });

    it('holds a selection to the count, the server previews included', async () => {
        const env = await boot(UP({ max: 3, previews: ['/a.png', '/b.png'] }));
        env.choose([file(env.window, '1.png'), file(env.window, '2.png')]);
        expect(env.states()).toEqual(['uploading', 'refused']);
        expect(env.$$('.dj-img-upload__item-note')[1].textContent).toBe('Not sent: more than the 3 allowed');
        expect(env.files().map((f) => f.name)).toEqual(['1.png']);
        env.choose([file(env.window, '3.png')]);
        expect(env.states()).toEqual(['uploading', 'refused', 'refused']);
    });

    it('with max 1 a new choice replaces the old image, cancelling it if it is still uploading', async () => {
        const env = await boot(UP({ max: 1 }));
        const first = file(env.window, 'first.png');
        env.choose([first]);
        env.register('ref-1', first);
        env.choose([file(env.window, 'second.png'), file(env.window, 'third.png')]);
        expect(env.names()).toEqual(['second.png']);
        expect(env.window.djust.uploads.cancelUpload).toHaveBeenCalledWith('ref-1');
        expect(env.urls.revoked).toEqual(['blob:mock/1/first.png']);
        expect(env.files().map((f) => f.name)).toEqual(['second.png', 'third.png']); // the input is only held back for a refusal
    });

    it('re-choosing the same file in single mode does not take it out of the input', async () => {
        const env = await boot(UP({ max: 1, slot: '' }));
        const f = file(env.window, 'same.png');
        env.choose([f]);
        env.choose([file(env.window, 'same.png')]);
        expect(env.names()).toEqual(['same.png']);
        expect(env.files().map((x) => x.name)).toEqual(['same.png']);
    });

    it('without a way to hold files back (no DataTransfer) shows them as chosen and lets the server decide', async () => {
        const env = await boot(UP({ max: 5 }), { dataTransfer: false, setFiles: false });
        env.choose([file(env.window, 'a.png'), file(env.window, 'doc.pdf', { type: 'application/pdf' })]);
        expect(env.states()).toEqual(['uploading', 'uploading']);
    });

    it('says so, and sends nothing, when there is no connection', async () => {
        const env = await boot(UP(), { connected: false });
        env.choose([file(env.window, 'a.png')]);
        expect(env.states()).toEqual(['error']);
        expect(env.$$('.dj-img-upload__item-note')[0].textContent).toBe('Not connected: nothing was sent');
        expect(env.live()).toBe('Not connected: nothing was sent');
    });

    it('a hostile file name stays text', async () => {
        const env = await boot(UP());
        const name = '<img src=x onerror=window.__pwn=1>.png';
        env.choose([file(env.window, name)]);
        expect(env.names()).toEqual([name]);
        expect(env.$$('.dj-img-upload__item img')).toHaveLength(1); // the thumbnail only
        expect(env.window.__pwn).toBeUndefined();
        expect(env.$$('.dj-img-upload__item-button')[0].getAttribute('aria-label')).toBe('Cancel ' + name);
    });
});

// ---------------------------------------------------------------------------
// Dropping
// ---------------------------------------------------------------------------

describe('dropping images', () => {
    const drop = (env, files, types = ['Files']) => {
        const e = new env.window.Event('drop', { bubbles: true, cancelable: true });
        e.dataTransfer = { files, types };
        const zone = env.$('.dj-img-upload__dropzone');
        let reached = false;
        zone.addEventListener('drop', () => { reached = true; });
        zone.querySelector('.dj-img-upload__text').dispatchEvent(e);
        return { e, reached };
    };

    it('becomes the same as choosing: the input gets the files and one change event follows; djust\'s own drop handler is not reached', async () => {
        const env = await boot(UP());
        const f = file(env.window, 'dropped.png');
        let changes = 0;
        env.input.addEventListener('change', () => { changes += 1; });
        const { e, reached } = drop(env, [f]);
        expect(e.defaultPrevented).toBe(true);
        expect(reached).toBe(false);
        expect(changes).toBe(1);
        expect(env.names()).toEqual(['dropped.png']);
        expect(env.files().map((x) => x.name)).toEqual(['dropped.png']);
    });

    it('ignores a drop that lands inside the component but outside the drop zone', async () => {
        const env = await boot(UP());
        const e = new env.window.Event('drop', { bubbles: true, cancelable: true });
        e.dataTransfer = { files: [file(env.window, 'x.png')], types: ['Files'] };
        env.$('.dj-img-upload__items').dispatchEvent(e);
        expect(e.defaultPrevented).toBe(false);
        expect(env.items()).toHaveLength(0);
        expect(env.assigned).toEqual([]);
    });

    it('applies the pre-checks to a drop too', async () => {
        const env = await boot(UP());
        drop(env, [file(env.window, 'a.png'), file(env.window, 'b.txt', { type: 'text/plain' })]);
        expect(env.states()).toEqual(['uploading', 'refused']);
        expect(env.files().map((x) => x.name)).toEqual(['a.png']);
    });

    it('leaves a drop that holds no files, and a drop outside the zone, alone', async () => {
        const env = await boot(UP());
        const { reached } = drop(env, [], ['text/plain']);
        expect(reached).toBe(true);
        const out = new env.window.Event('drop', { bubbles: true, cancelable: true });
        out.dataTransfer = { files: [file(env.window, 'x.png')], types: ['Files'] };
        env.window.document.body.dispatchEvent(out);
        expect(out.defaultPrevented).toBe(false);
        expect(env.items()).toHaveLength(0);
        expect(env.assigned).toEqual([]);
    });

    it('leaves the drop to djust when files cannot be handed over (no DataTransfer)', async () => {
        const env = await boot(UP(), { dataTransfer: false });
        const { reached } = drop(env, [file(env.window, 'a.png')]);
        expect(reached).toBe(true); // not stopped at the root: djust's own handler on the zone sees it
        expect(env.items()).toHaveLength(0);
        expect(env.assigned).toEqual([]);
    });

    it('marks the zone while files are dragged over it, and clears it only when the drag leaves', async () => {
        const env = await boot(UP({ slot: '' })); // with a slot djust\'s own handler also clears it on every dragleave
        const zone = env.$('.dj-img-upload__dropzone');
        const over = new env.window.Event('dragover', { bubbles: true, cancelable: true });
        over.dataTransfer = { types: ['Files'] };
        env.$('.dj-img-upload__text').dispatchEvent(over);
        expect(over.defaultPrevented).toBe(true);
        expect(zone.classList.contains('upload-dragover')).toBe(true);
        const inside = new env.window.Event('dragleave', { bubbles: true });
        Object.defineProperty(inside, 'relatedTarget', { value: env.$('.dj-img-upload__icon') });
        zone.dispatchEvent(inside);
        expect(zone.classList.contains('upload-dragover')).toBe(true);
        const outside = new env.window.Event('dragleave', { bubbles: true });
        Object.defineProperty(outside, 'relatedTarget', { value: env.window.document.body });
        zone.dispatchEvent(outside);
        expect(zone.classList.contains('upload-dragover')).toBe(false);
        const text = new env.window.Event('dragover', { bubbles: true, cancelable: true });
        text.dataTransfer = { types: ['text/plain'] };
        env.$('.dj-img-upload__text').dispatchEvent(text);
        expect(text.defaultPrevented).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// Progress, cancel, remove
// ---------------------------------------------------------------------------

describe('progress, cancel and remove', () => {
    it('feeds each thumbnail from djust:upload:progress, matching the upload to its file', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        const b = file(env.window, 'b.png', { size: 20 });
        env.choose([a, b]);
        env.register('ref-a', a);
        env.register('ref-b', b);
        env.progress({ ref: 'ref-b', progress: 40, status: 'uploading', uploadName: 'photos' });
        expect(env.$$('.dj-img-upload__item-bar').map((p) => p.value)).toEqual([0, 40]);
        expect(env.$$('.dj-img-upload__item-note').map((n) => n.textContent)).toEqual(['Uploading', '40%']);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        expect(env.states()).toEqual(['done', 'uploading']);
        expect(env.$$('.dj-img-upload__item-note')[0].textContent).toBe('Uploaded');
        expect(env.$$('.dj-img-upload__item-bar')).toHaveLength(1); // a finished one drops its bar
        expect(env.live()).toBe('a.png uploaded');
        expect(env.$$('.dj-img-upload__item-button').map((x) => x.getAttribute('data-action'))).toEqual(['remove', 'cancel']);
    });

    it('ignores progress for another slot, an unknown upload and a malformed event', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        env.choose([a]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 50, status: 'uploading', uploadName: 'avatar' });
        env.progress({ ref: 'nope', progress: 50, status: 'uploading', uploadName: 'photos' });
        env.progress({});
        env.window.dispatchEvent(new env.window.CustomEvent('djust:upload:progress'));
        expect(env.$$('.dj-img-upload__item-bar')[0].value).toBe(0);
    });

    it("marks a file the server refused or failed", async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        env.choose([a]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 10, status: 'error', uploadName: 'photos' });
        expect(env.states()).toEqual(['error']);
        expect(env.$$('.dj-img-upload__item-note')[0].textContent).toBe('Not accepted by the server');
        expect(env.live()).toBe('a.png was not accepted by the server');
    });

    it('Cancel calls djust\'s cancelUpload with the ref it minted, and marks the thumbnail', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        env.choose([a]);
        env.register('ref-a', a);
        env.press(env.items()[0], 'cancel');
        expect(env.window.djust.uploads.cancelUpload).toHaveBeenCalledWith('ref-a');
        expect(env.states()).toEqual(['cancelled']);
        expect(env.live()).toBe('Upload of a.png cancelled');
        expect(env.sent).toEqual([]); // no event for a selection nothing finished
    });

    it('a thumbnail the browser cannot decode becomes a placeholder, keeping the name and size', async () => {
        const env = await boot(UP());
        env.choose([file(env.window, 'odd.heic', { type: 'image/heic' })]);
        const img = env.$('.dj-img-upload__item-img');
        img.dispatchEvent(new env.window.Event('error'));
        expect(env.$$('.dj-img-upload__item-img')).toHaveLength(0);
        expect(env.$('.dj-img-upload__item-thumb').classList.contains('dj-img-upload__item-thumb--broken')).toBe(true);
        expect(env.names()).toEqual(['odd.heic']);
    });

    it('Cancel before djust has a ref for the file only marks it', async () => {
        const env = await boot(UP());
        env.choose([file(env.window, 'a.png')]);
        env.press(env.items()[0], 'cancel');
        expect(env.window.djust.uploads.cancelUpload).not.toHaveBeenCalled();
        expect(env.states()).toEqual(['cancelled']);
    });

    it('Remove drops the thumbnail and gives its object URL back', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        env.choose([a, file(env.window, 'b.png')]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        env.press(env.items()[0], 'remove');
        expect(env.names()).toEqual(['b.png']);
        expect(env.urls.revoked).toEqual(['blob:mock/1/a.png']);
        expect(env.live()).toBe('a.png removed');
    });

    it('Remove also takes the file out of a plain input, so a form post does not carry it', async () => {
        const env = await boot(UP({ slot: '', event: '' }));
        const a = file(env.window, 'a.png');
        const b = file(env.window, 'b.png');
        env.choose([a, b]);
        env.press(env.items()[0], 'remove');
        expect(env.files().map((f) => f.name)).toEqual(['b.png']);
    });

    it('a refused row has no object URL to give back and removes cleanly', async () => {
        const env = await boot(UP());
        env.choose([file(env.window, 'x.txt', { type: 'text/plain' })]);
        env.press(env.items()[0], 'remove');
        expect(env.items()).toHaveLength(0);
        expect(env.urls.revoked).toEqual([]);
    });

    it('djust\'s own size refusal (djust:upload:error) marks the file', async () => {
        const env = await boot(UP({ maxSize: 0 }));
        env.choose([file(env.window, 'huge.png')]);
        env.window.dispatchEvent(new env.window.CustomEvent('djust:upload:error', { detail: { file: 'huge.png', error: 'File too large' } }));
        expect(env.states()).toEqual(['error']);
        expect(env.$$('.dj-img-upload__item-note')[0].textContent).toBe('Not sent: File too large');
    });

    it('"Upload rejected" from the server marks files still waiting, not those that have started', async () => {
        const env = await boot(UP());
        const started = file(env.window, 'started.png');
        env.choose([started, file(env.window, 'waiting.png')]);
        env.register('ref-s', started);
        env.progress({ ref: 'ref-s', progress: 5, status: 'uploading', uploadName: 'photos' });
        env.window.dispatchEvent(new env.window.CustomEvent('djust:error', { detail: { error: 'Upload rejected (check file type, size, or max entries)' } }));
        expect(env.states()).toEqual(['uploading', 'error']);
        env.window.dispatchEvent(new env.window.CustomEvent('djust:error', { detail: { error: 'something else' } }));
        expect(env.states()).toEqual(['uploading', 'error']);
    });
});

// ---------------------------------------------------------------------------
// The event
// ---------------------------------------------------------------------------

describe('data-event', () => {
    it('is sent once, with the count, when the selection has finished uploading', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        const b = file(env.window, 'b.png', { size: 20 });
        env.choose([a, b]);
        env.register('ref-a', a);
        env.register('ref-b', b);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        expect(env.sent).toEqual([]);
        env.progress({ ref: 'ref-b', progress: 100, status: 'complete', uploadName: 'photos' });
        expect(env.sent).toEqual([{ name: 'photos_uploaded', params: { count: 2 } }]);
    });

    it('counts only the files that completed, and not at all when none did', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        const b = file(env.window, 'b.png', { size: 20 });
        env.choose([a, b]);
        env.register('ref-a', a);
        env.register('ref-b', b);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        env.press(env.items()[1], 'cancel');
        expect(env.sent).toEqual([{ name: 'photos_uploaded', params: { count: 1 } }]);
        const none = await boot(UP());
        const c = file(none.window, 'c.png');
        none.choose([c]);
        none.press(none.items()[0], 'cancel');
        expect(none.sent).toEqual([]);
    });

    it('is not sent when the app named no event (the default name has no handler)', async () => {
        const env = await boot(UP({ event: '' }));
        const a = file(env.window, 'a.png');
        env.choose([a]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        expect(env.sent).toEqual([]);
    });

    it('without an upload slot it is sent when files are chosen, with the number accepted', async () => {
        const env = await boot(UP({ slot: '' }));
        env.choose([file(env.window, 'a.png'), file(env.window, 'b.txt', { type: 'text/plain' })]);
        expect(env.sent).toEqual([{ name: 'photos_uploaded', params: { count: 1 } }]);
        env.choose([file(env.window, 'c.txt', { type: 'text/plain' })]);
        expect(env.sent).toHaveLength(1);
    });

    it('goes through the strict-parameter gate like dj-click, and honours a veto', async () => {
        const env = await boot(UP({ slot: '' }));
        env.window.djust._strictBinding = vi.fn(() => false);
        env.choose([file(env.window, 'a.png')]);
        expect(env.window.djust._strictBinding).toHaveBeenCalledTimes(1);
        expect(env.sent).toEqual([]);
    });
});

// ---------------------------------------------------------------------------
// The server's previews and morphs
// ---------------------------------------------------------------------------

describe('server re-renders', () => {
    it('finished local thumbnails give way when the server renders a different previews list; uploads in flight stay', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        const b = file(env.window, 'b.png');
        env.choose([a, b]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        env.patch(() => env.$('.dj-img-upload__items').insertAdjacentHTML('beforebegin', '<div class="dj-img-upload__previews"><div class="dj-img-upload__thumb"><img class="dj-img-upload__thumb-img" src="/saved/a.png"></div></div>'));
        expect(env.names()).toEqual(['b.png']);
        expect(env.urls.revoked).toEqual(['blob:mock/1/a.png']);
    });

    it('a finished local thumbnail stays through patches that leave the previews alone', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        env.choose([a]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        env.patch(() => env.$('.dj-img-upload').classList.add('other'));
        env.patch(() => env.$('.dj-img-upload').classList.add('another'));
        expect(env.names()).toEqual(['a.png']);
        expect(env.urls.revoked).toEqual([]);
    });

    it('an unrelated patch leaves everything alone', async () => {
        const env = await boot(UP());
        const a = file(env.window, 'a.png');
        env.choose([a]);
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 60, status: 'uploading', uploadName: 'photos' });
        env.patch(() => env.$('.dj-img-upload').classList.add('other'));
        expect(env.names()).toEqual(['a.png']);
        expect(env.$$('.dj-img-upload__item-bar')[0].value).toBe(60);
        expect(env.urls.revoked).toEqual([]);
    });

    it('marks the list and the status line client-owned, so a morph does not empty them', async () => {
        const env = await boot(UP());
        expect(env.$('.dj-img-upload__items').getAttribute('dj-update')).toBe('ignore');
        expect(env.$('.dj-img-upload__status').getAttribute('dj-update')).toBe('ignore');
    });

    it('moves the thumbnails into a list element a morph replaced', async () => {
        const env = await boot(UP());
        env.choose([file(env.window, 'a.png')]);
        env.patch(() => {
            const old = env.$('.dj-img-upload__items');
            const fresh = env.window.document.createElement('ul');
            fresh.className = 'dj-img-upload__items';
            old.replaceWith(fresh);
        });
        expect(env.names()).toEqual(['a.png']);
        expect(env.$('.dj-img-upload__items').getAttribute('dj-update')).toBe('ignore');
    });
});

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

describe('teardown', () => {
    it('destroyed() revokes every object URL and removes every listener', async () => {
        const env = await boot(UP());
        const win = vi.spyOn(env.window, 'removeEventListener');
        const rootEl = env.$('.dj-img-upload');
        const rootSpy = vi.spyOn(rootEl, 'removeEventListener');
        const a = file(env.window, 'a.png');
        env.choose([a, file(env.window, 'b.png')]);
        env.hook().destroyed();
        expect(env.urls.revoked.sort()).toEqual(['blob:mock/1/a.png', 'blob:mock/2/b.png']);
        expect(win.mock.calls.map((c) => c[0]).sort()).toEqual(['djust:error', 'djust:upload:error', 'djust:upload:progress']);
        expect(rootSpy.mock.calls.map((c) => c[0]).sort()).toEqual(['change', 'click', 'dragleave', 'dragover', 'drop']);
        // dead: later events do nothing
        env.register('ref-a', a);
        env.progress({ ref: 'ref-a', progress: 100, status: 'complete', uploadName: 'photos' });
        env.choose([file(env.window, 'c.png')]);
        expect(env.sent).toEqual([]);
        expect(env.urls.made).toHaveLength(2);
        env.hook(); // no throw
        env.window.djust.destroyAllHooks();
    });

    it('removing the element through a patch destroys the hook', async () => {
        const env = await boot(UP());
        env.choose([file(env.window, 'a.png')]);
        env.patch(() => env.$('.dj-img-upload').remove());
        expect(env.urls.revoked).toEqual(['blob:mock/1/a.png']);
    });

    it('binds once however often the page patches: one change handles one choice', async () => {
        const env = await boot(UP());
        for (let i = 0; i < 4; i++) env.patch(() => env.$('.dj-img-upload').classList.toggle('x'));
        env.choose([file(env.window, 'a.png')]);
        expect(env.items()).toHaveLength(1);
        expect(env.urls.made).toHaveLength(1);
        env.press(env.items()[0], 'cancel');
        env.press(env.items()[0], 'remove');
        expect(env.urls.revoked).toHaveLength(1);
    });

    it('two components keep their own thumbnails and slots', async () => {
        const env = await boot(UP({ slot: 'one' }) + UP({ slot: 'two' }).replace('class="dj-img-upload"', 'class="dj-img-upload" id="second"'));
        const inputs = env.$$('.dj-img-upload__input');
        const second = inputs[1];
        let current = [];
        Object.defineProperty(second, 'files', { configurable: true, get: () => current, set: (v) => { current = Array.from(v); } });
        current = [file(env.window, 'two.png')];
        second.dispatchEvent(new env.window.Event('change', { bubbles: true }));
        expect(env.$$('.dj-img-upload').map((r) => r.querySelectorAll('.dj-img-upload__item').length)).toEqual([0, 1]);
    });
});

describe('what the script may not do', () => {
    it('contains no markup parsing or dynamic code', () => {
        for (const banned of [/\binnerHTML\b/, /\bouterHTML\b/, /insertAdjacentHTML/, /\beval\s*\(/, /new Function/, /document\.write/, /FileReader/, /readAsDataURL/, /new WebSocket/, /XMLHttpRequest/, /\bfetch\s*\(/]) {
            expect(SCRIPT).not.toMatch(banned);
        }
    });
});
