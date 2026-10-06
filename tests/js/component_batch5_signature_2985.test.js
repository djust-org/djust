/**
 * #2985 batch 5 — SignaturePad answers its dj-hook, and an app's own hook of the
 * same name still wins.
 *
 * The markup is what the component renders (python/djust/tests/
 * test_component_batch5_signature_2985.py pins it on every render path). jsdom
 * has no 2D context, so a recording fake stands in: what is checked here is
 * WHAT is drawn, sized, sent and refused; real pixels, real pointer input and
 * the PNG a browser encodes are checked in Chromium by
 * tests/playwright/test_component_batch5_signature_2985.py.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');
const SCRIPT = read('signature-pad.js');

const PAD = ({ w = 400, h = 200, event = 'save_signature', maxBytes = 204800, disabled = false, color = '#112233', pen = 4, typed = true } = {}) =>
    `<div class="dj-signature-pad${disabled ? ' dj-signature-pad--disabled' : ''}" dj-hook="SignaturePad" data-save-event="${event}" data-pen-color="${color}" data-pen-width="${pen}" data-max-bytes="${maxBytes}">` +
    `<canvas class="dj-signature-pad__canvas" width="${w}" height="${h}"${disabled ? ' disabled' : ''} role="img" aria-label="Signature drawing area"></canvas>` +
    '<input type="hidden" name="signature" class="dj-signature-pad__value">' +
    (typed ? '<div class="dj-signature-pad__typed" hidden><label class="dj-signature-pad__typed-label">Type your name<input type="text" class="dj-signature-pad__typed-input" maxlength="80"></label></div>' : '') +
    '<div class="dj-signature-pad__actions"><button class="dj-signature-pad__clear-btn" type="button">Clear</button>' +
    '<button class="dj-signature-pad__undo-btn" type="button">Undo</button>' +
    (typed ? '<button class="dj-signature-pad__mode-btn" type="button" aria-pressed="false">Type instead</button>' : '') +
    `<button class="dj-signature-pad__save-btn" type="button"${disabled ? ' disabled' : ''}>Save</button></div>` +
    '<div class="dj-signature-pad__status" role="status" aria-live="polite"></div></div>';

function makeCtx(canvas) {
    const ctx = {
        canvas, ops: [], lineWidth: 1, strokeStyle: '', fillStyle: '', lineCap: '', lineJoin: '', font: '', textAlign: '', textBaseline: '',
    };
    for (const name of ['setTransform', 'clearRect', 'beginPath', 'moveTo', 'lineTo', 'quadraticCurveTo', 'stroke', 'fill', 'arc', 'fillText']) {
        ctx[name] = (...args) => { ctx.ops.push({ op: name, args, lineWidth: ctx.lineWidth, fillStyle: ctx.fillStyle, strokeStyle: ctx.strokeStyle, font: ctx.font }); };
    }
    ctx.measureText = (t) => ({ width: String(t).length * (parseInt(/(\d+)px/.exec(ctx.font || '10px')[1], 10) / 2) });
    return ctx;
}

function createEnv(bodyHtml, { dpr = 2, resizeObserver, requestAnimationFrame = true } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = { log: () => {}, error: () => {}, debug: () => {}, info: () => {}, warn: (...a) => warnings.push(a.join(' ')) };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    Object.defineProperty(window, 'devicePixelRatio', { value: dpr, configurable: true, writable: true });
    const env = { window, warnings, sent: [], ctxs: [], sizer: () => 100 };
    window.HTMLCanvasElement.prototype.getContext = function () {
        if (!this._ctx) { this._ctx = makeCtx(this); env.ctxs.push(this._ctx); }
        return this._ctx;
    };
    window.HTMLCanvasElement.prototype.toDataURL = function () {
        return 'data:image/png;base64,' + 'A'.repeat(env.sizer(this.width, this.height, this._ctx ? this._ctx.ops.length : 0));
    };
    if (resizeObserver) window.ResizeObserver = resizeObserver;
    void requestAnimationFrame;
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    window.djust.handleEvent = (name, params) => { env.sent.push({ name, params }); };
    return env;
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const frame = (env) => new Promise((resolve) => env.window.requestAnimationFrame(() => resolve()));

async function boot(markup, opts = {}) {
    const env = createEnv(markup, opts);
    if (opts.preRegister) opts.preRegister(env.window);
    env.window.eval(SCRIPT);
    if (opts.width !== undefined) {
        Object.defineProperty(env.window.document.querySelector('.dj-signature-pad'), 'clientWidth', { value: opts.width, configurable: true });
    }
    env.window.djust.mountHooks();
    await tick();
    const $ = (s) => env.window.document.querySelector(s);
    Object.assign(env, { $, $$: (s) => Array.from(env.window.document.querySelectorAll(s)) });
    env.root = $('.dj-signature-pad');
    env.canvas = $('.dj-signature-pad__canvas');
    env.hook = () => env.window.djust.getHook(env.root);
    env.display = () => env.canvas._ctx;
    env.status = () => $('.dj-signature-pad__status').textContent.trim();
    env.btn = (name) => $(`.dj-signature-pad__${name}-btn`);
    env.hidden = () => $('.dj-signature-pad__value').value;
    env.click = (name) => env.btn(name).click();
    Object.defineProperty(env.canvas, 'getBoundingClientRect', { value: () => ({ left: 10, top: 20, width: parseFloat(env.canvas.style.width) || 400, height: parseFloat(env.canvas.style.height) || 200 }), configurable: true });
    env.captured = [];
    env.released = [];
    env.canvas.setPointerCapture = (id) => env.captured.push(id);
    env.canvas.releasePointerCapture = (id) => env.released.push(id);
    env.ptr = (type, x, y, extra = {}) => {
        const e = new env.window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: extra.button || 0 });
        for (const [k, v] of Object.entries({ pointerId: 1, isPrimary: true, pointerType: 'mouse', pressure: 0.5, ...extra })) Object.defineProperty(e, k, { value: v });
        env.canvas.dispatchEvent(e);
        return e;
    };
    // a stroke: client coordinates are canvas coordinates plus the canvas' (10, 20) offset
    env.draw = (points, extra = {}) => {
        env.ptr('pointerdown', 10 + points[0][0], 20 + points[0][1], extra);
        for (const [x, y] of points.slice(1)) env.ptr('pointermove', 10 + x, 20 + y, extra);
        const last = points[points.length - 1];
        env.ptr('pointerup', 10 + last[0], 20 + last[1], extra);
    };
    return env;
}

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

describe('the shipped hook never replaces an app hook (#2985 batch 5)', () => {
    const probe = (env) => env.canvas.getAttribute('dj-update') === 'ignore';

    it('registers a hook, so mounting logs no "No hook registered"', async () => {
        const env = await boot(PAD());
        expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
        expect(probe(env)).toBe(true);
    });

    it('without the script the warning is logged (gate-off)', () => {
        const env = createEnv(PAD());
        env.window.djust.mountHooks();
        expect(env.warnings.some((w) => w.includes('No hook registered for "SignaturePad"'))).toBe(true);
    });

    it('an app hook in either registry, registered first, is the one that runs', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = await boot(PAD(), { preRegister: (w) => { if (where === 'DjustHooks') w.DjustHooks = { SignaturePad: { mounted } }; else w.djust.hooks = { SignaturePad: { mounted } }; } });
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(probe(env)).toBe(false);
        }
    });

    it('an app hook registered AFTER the script still wins, in either registry', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = createEnv(PAD());
            env.window.eval(SCRIPT);
            if (where === 'djust.hooks') env.window.djust.hooks = { SignaturePad: { mounted } };
            else env.window.DjustHooks.SignaturePad = { mounted };
            env.window.djust.mountHooks();
            await tick();
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(env.window.document.querySelector('.dj-signature-pad__canvas').hasAttribute('dj-update')).toBe(false);
        }
    });

    it("an app hook's pushEvent/handleEvent API is untouched", async () => {
        const seen = [];
        await boot(PAD(), { preRegister: (w) => { w.djust.hooks = { SignaturePad: { mounted() { seen.push(typeof this.pushEvent, typeof this.handleEvent); } } }; } });
        expect(seen).toEqual(['function', 'function']);
    });
});

// ---------------------------------------------------------------------------
// Size, pixel ratio, resize
// ---------------------------------------------------------------------------

describe('sharp, responsive, resize-proof', () => {
    it('backs the canvas with the device pixel ratio and draws in the component\'s own units', async () => {
        const env = await boot(PAD({ w: 400, h: 200 }), { dpr: 2, width: 400 });
        expect(env.canvas.width).toBe(800);
        expect(env.canvas.height).toBe(400);
        expect(env.canvas.style.width).toBe('400px');
        expect(env.canvas.style.height).toBe('200px');
        expect(env.display().ops.find((o) => o.op === 'setTransform').args).toEqual([2, 0, 0, 2, 0, 0]);
    });

    it('caps the pixel ratio at 3', async () => {
        const env = await boot(PAD(), { dpr: 5, width: 400 });
        expect(env.canvas.width).toBe(1200);
    });

    it('follows a narrower container, keeping the aspect ratio', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 300 });
        expect(env.canvas.style.width).toBe('300px');
        expect(env.canvas.style.height).toBe('150px');
        expect(env.canvas.width).toBe(300);
    });

    it('never grows past the size it was given', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 900 });
        expect(env.canvas.style.width).toBe('400px');
    });

    it('keeps the drawing when the container resizes: it is redrawn from the strokes at the new size', async () => {
        class FakeRO { constructor(cb) { this.cb = cb; FakeRO.last = this; } observe(node) { this.node = node; } disconnect() { this.gone = true; } }
        const env = await boot(PAD(), { dpr: 1, width: 400, resizeObserver: FakeRO });
        expect(FakeRO.last.node).toBe(env.root);
        env.draw([[10, 10], [60, 60], [120, 40]]);
        const before = env.hook()._strokes.length;
        Object.defineProperty(env.root, 'clientWidth', { value: 200, configurable: true });
        env.display().ops.length = 0;
        FakeRO.last.cb();
        FakeRO.last.cb();
        await frame(env);
        expect(env.canvas.style.width).toBe('200px');
        expect(env.canvas.width).toBe(200);
        expect(env.hook()._strokes).toHaveLength(before);
        const redraw = env.display().ops;
        expect(redraw.filter((o) => o.op === 'setTransform')).toHaveLength(1); // coalesced to one frame
        expect(redraw.find((o) => o.op === 'setTransform').args[0]).toBe(0.5);
        expect(redraw.some((o) => o.op === 'stroke')).toBe(true);
    });

    it('redraws after a change of pixel ratio (window resize)', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [80, 80]]);
        env.window.devicePixelRatio = 2;
        env.window.dispatchEvent(new env.window.Event('resize'));
        await frame(env);
        expect(env.canvas.width).toBe(800);
        expect(env.hook()._strokes).toHaveLength(1);
    });
});

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

describe('drawing with pointer events', () => {
    it('a stroke records its points in the component\'s units and is drawn as it goes', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 40], [90, 20]]);
        const strokes = env.hook()._strokes;
        expect(strokes).toHaveLength(1);
        expect(strokes[0].pts.map((p) => [p[0], p[1]])).toEqual([[10, 10], [50, 40], [90, 20]]);
        const ops = env.display().ops.map((o) => o.op);
        expect(ops).toContain('quadraticCurveTo');
        expect(env.display().ops.find((o) => o.op === 'stroke').strokeStyle).toBe('#112233');
        expect(env.display().ops.find((o) => o.op === 'stroke').lineWidth).toBe(4);
    });

    it('measures in the component\'s units when the canvas is shown smaller', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 200 });
        env.draw([[0, 0], [100, 50]]); // 200 px wide on screen for a 400-unit pad
        expect(env.hook()._strokes[0].pts.map((p) => [p[0], p[1]])).toEqual([[0, 0], [200, 100]]);
    });

    it('a stroke reaches its last point (the live drawing stops at the last midpoint)', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [60, 10], [160, 10]]);
        const lines = env.display().ops.filter((o) => o.op === 'lineTo');
        expect(lines[lines.length - 1].args).toEqual([160, 10]);
        const lastMove = env.display().ops.filter((o) => o.op === 'moveTo').pop();
        expect(lastMove.args).toEqual([110, 10]); // from the midpoint of the last segment
    });

    it('a tap leaves a dot', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[30, 30]]);
        expect(env.hook()._strokes[0].pts).toHaveLength(1);
        expect(env.display().ops.some((o) => o.op === 'arc')).toBe(true);
        expect(env.display().ops.some((o) => o.op === 'fill')).toBe(true);
    });

    it('captures the pointer and releases it, so a stroke that leaves the canvas still ends', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [20, 20]]);
        expect(env.captured).toEqual([1]);
        expect(env.released).toEqual([1]);
        env.ptr('pointerdown', 40, 40);
        env.ptr('lostpointercapture', 40, 40);
        env.ptr('pointermove', 90, 90); // no longer drawing
        expect(env.hook()._strokes[1].pts).toHaveLength(1);
    });

    it('clamps points that fall outside the canvas', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[-50, -50], [900, 900]]);
        expect(env.hook()._strokes[0].pts.map((p) => [p[0], p[1]])).toEqual([[0, 0], [400, 200]]);
    });

    it('drops points that barely moved', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [10.2, 10.2], [10.3, 10.1], [30, 30]]);
        expect(env.hook()._strokes[0].pts).toHaveLength(2);
    });

    it('uses the coalesced events of a fast move', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.ptr('pointerdown', 10, 20);
        const e = new env.window.MouseEvent('pointermove', { bubbles: true, clientX: 60, clientY: 40 });
        const mk = (x, y) => { const c = new env.window.MouseEvent('pointermove', { clientX: x, clientY: y }); Object.defineProperty(c, 'pointerId', { value: 1 }); Object.defineProperty(c, 'pointerType', { value: 'mouse' }); return c; };
        Object.defineProperty(e, 'pointerId', { value: 1 });
        Object.defineProperty(e, 'getCoalescedEvents', { value: () => [mk(30, 30), mk(45, 38), mk(60, 40)] });
        env.canvas.dispatchEvent(e);
        expect(env.hook()._strokes[0].pts).toHaveLength(4);
    });

    it('ignores a second finger, the right button, a disabled pad and typed mode', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.ptr('pointerdown', 20, 30, { isPrimary: false });
        env.ptr('pointerdown', 20, 30, { button: 2 });
        expect(env.hook()._strokes).toHaveLength(0);
        env.ptr('pointerdown', 20, 30);
        env.ptr('pointerdown', 50, 60, { pointerId: 2 }); // a second pointer mid-stroke
        env.ptr('pointermove', 60, 60, { pointerId: 2 });
        env.ptr('pointerup', 60, 60);
        expect(env.hook()._strokes).toHaveLength(1);
        expect(env.hook()._strokes[0].pts).toHaveLength(1);
        const off = await boot(PAD({ disabled: true }), { dpr: 1, width: 400 });
        off.draw([[10, 10], [30, 30]]);
        expect(off.hook()._strokes).toHaveLength(0);
        env.click('mode');
        env.draw([[10, 10], [30, 30]]);
        expect(env.hook()._strokes).toHaveLength(1);
    });

    it('prevents the default of the pointer-down (no text selection or scroll) and of the context menu', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        expect(env.ptr('pointerdown', 20, 30).defaultPrevented).toBe(true);
        env.ptr('pointerup', 20, 30);
        const menu = new env.window.Event('contextmenu', { bubbles: true, cancelable: true });
        env.canvas.dispatchEvent(menu);
        expect(menu.defaultPrevented).toBe(true);
    });

    it('a pen varies the line width with pressure; a mouse does not', async () => {
        const env = await boot(PAD({ pen: 4 }), { dpr: 1, width: 400 });
        env.draw([[10, 10], [40, 40], [80, 40]], { pointerType: 'pen', pressure: 1 });
        const widths = env.display().ops.filter((o) => o.op === 'stroke').map((o) => o.lineWidth);
        expect(Math.max(...widths)).toBeCloseTo(6, 5); // 4 * (0.5 + 1)
        const soft = await boot(PAD({ pen: 4 }), { dpr: 1, width: 400 });
        soft.draw([[10, 10], [40, 40], [80, 40]], { pointerType: 'pen', pressure: 0.1 });
        expect(Math.max(...soft.display().ops.filter((o) => o.op === 'stroke').map((o) => o.lineWidth))).toBeLessThan(4);
    });

    it('stops adding points to a stroke at the hard limit', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.ptr('pointerdown', 20, 30);
        env.hook()._points = 19999;
        env.ptr('pointermove', 40, 50);
        env.ptr('pointermove', 60, 70);
        env.ptr('pointermove', 80, 90);
        env.ptr('pointerup', 80, 90);
        expect(env.hook()._strokes[0].pts).toHaveLength(2);
    });

    it('another pointer lifting does not end the stroke in progress', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.ptr('pointerdown', 20, 30);
        env.ptr('pointerup', 20, 30, { pointerId: 2 });
        env.ptr('pointermove', 60, 70);
        expect(env.hook()._strokes[0].pts).toHaveLength(2);
        expect(env.released).toEqual([]);
    });

    it('stops accepting points at a hard limit, and says so', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.hook()._points = 20000;
        env.ptr('pointerdown', 20, 30);
        expect(env.hook()._strokes).toHaveLength(0);
        expect(env.status()).toBe('This signature has reached its limit; use Undo or Clear');
    });
});

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

describe('Clear, Undo and the empty state', () => {
    it('starts empty: nothing can be saved, cleared or undone', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        expect(['save', 'clear', 'undo'].map((b) => env.btn(b).disabled)).toEqual([true, true, true]);
        expect(env.btn('mode').disabled).toBe(false);
    });

    it('a stroke enables them', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        expect(['save', 'clear', 'undo'].map((b) => env.btn(b).disabled)).toEqual([false, false, false]);
    });

    it('Undo removes the last stroke and redraws the rest', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.draw([[60, 60], [90, 90]]);
        env.display().ops.length = 0;
        env.click('undo');
        expect(env.hook()._strokes).toHaveLength(1);
        expect(env.display().ops.filter((o) => o.op === 'clearRect')).toHaveLength(1);
        expect(env.display().ops.some((o) => o.op === 'stroke')).toBe(true);
        expect(env.status()).toBe('Last stroke undone');
        env.click('undo');
        expect(env.btn('undo').disabled).toBe(true);
        expect(env.btn('save').disabled).toBe(true);
    });

    it('Clear empties the drawing and the hidden value, and announces it', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        expect(env.hidden()).not.toBe('');
        env.click('clear');
        expect(env.hook()._strokes).toHaveLength(0);
        expect(env.hidden()).toBe('');
        expect(env.status()).toBe('Signature cleared');
        expect(env.btn('save').disabled).toBe(true);
    });

    it('drawing again after a save clears the stale form value', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        env.draw([[60, 60], [70, 70]]);
        expect(env.hidden()).toBe('');
    });

    it('announces the same message twice in a row by changing the text', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10]]);
        env.click('clear');
        const first = env.$('.dj-signature-pad__status').textContent;
        env.draw([[10, 10]]);
        env.click('clear');
        expect(env.$('.dj-signature-pad__status').textContent).not.toBe(first);
    });

    it('a pad the server disabled after drawing does not save', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.root.classList.add('dj-signature-pad--disabled');
        env.hook()._save();
        expect(env.sent).toEqual([]);
    });

    it('a disabled pad can do nothing, and the buttons say so', async () => {
        const env = await boot(PAD({ disabled: true }), { dpr: 1, width: 400 });
        expect(['save', 'clear', 'undo', 'mode'].map((b) => env.btn(b).disabled)).toEqual([true, true, true, true]);
        expect(env.canvas.getAttribute('aria-disabled')).toBe('true');
        env.hook()._save();
        expect(env.sent).toEqual([]);
    });
});

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

describe('Save', () => {
    it('with nothing drawn sends nothing and says so', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.hook()._save();
        expect(env.sent).toEqual([]);
        expect(env.status()).toBe('Nothing to save: draw or type your signature first');
    });

    it('a tap counts as ink', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[30, 30]]);
        env.click('save');
        expect(env.sent).toHaveLength(1);
    });

    it('sends only {signature} to the configured event and fills the hidden field with the same value', async () => {
        const env = await boot(PAD({ event: 'sign_here' }), { dpr: 1, width: 400 });
        env.sizer = () => 200;
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        expect(env.sent).toHaveLength(1);
        expect(env.sent[0].name).toBe('sign_here');
        expect(Object.keys(env.sent[0].params)).toEqual(['signature']);
        expect(env.sent[0].params.signature).toMatch(/^data:image\/png;base64,A{200}$/);
        expect(env.hidden()).toBe(env.sent[0].params.signature);
        expect(env.status()).toBe('Signature saved');
    });

    it('goes through the strict-parameter gate like dj-click, and honours a veto', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.window.djust._strictBinding = vi.fn(() => false);
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        expect(env.window.djust._strictBinding).toHaveBeenCalledTimes(1);
        expect(env.sent).toEqual([]);
    });

    it('falls back to the hook pushEvent when the client API is absent', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        delete env.window.djust.handleEvent;
        const push = vi.fn();
        env.hook().pushEvent = push;
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        expect(push).toHaveBeenCalledWith('save_signature', { signature: expect.stringMatching(/^data:image\/png;base64,/) });
    });

    it('exports at the pad\'s own size, whatever the screen shows', async () => {
        const env = await boot(PAD({ w: 400, h: 200 }), { dpr: 1, width: 150 });
        env.draw([[10, 10], [50, 50]]);
        const sizes = [];
        env.sizer = (w, h) => { sizes.push([w, h]); return 100; };
        env.click('save');
        expect(sizes).toEqual([[400, 200]]);
    });

    it('exports at up to twice the pad size on a sharp screen, never more', async () => {
        const env = await boot(PAD(), { dpr: 3, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        const sizes = [];
        env.sizer = (w, h) => { sizes.push([w, h]); return 100; };
        env.click('save');
        expect(sizes).toEqual([[800, 400]]);
    });

    it('re-renders at a lower resolution until the data URL fits, and never sends one over the cap', async () => {
        const env = await boot(PAD({ maxBytes: 5000 }), { dpr: 2, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        const tried = [];
        env.sizer = (w) => { tried.push(w); return w * 8; }; // 800 -> 6400, 600 -> 4800
        env.click('save');
        expect(tried).toEqual([800, 600]);
        expect(env.sent).toHaveLength(1);
        expect(env.sent[0].params.signature.length).toBeLessThanOrEqual(5000);
    });

    it('refuses a signature that cannot be made small enough, and sends nothing', async () => {
        const env = await boot(PAD({ maxBytes: 2048 }), { dpr: 2, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.sizer = () => 1_000_000;
        env.click('save');
        expect(env.sent).toEqual([]);
        expect(env.hidden()).toBe('');
        expect(env.status()).toMatch(/^This signature is too detailed to send \(the limit is 2 KB\)/);
    });

    it('the cap is data-max-bytes, 200 KB by default and at least 1 KB', async () => {
        const dflt = await boot(PAD({ maxBytes: 'nope' }), { dpr: 1, width: 400 });
        dflt.draw([[10, 10], [50, 50]]);
        dflt.sizer = () => 204800 - 22;
        dflt.click('save');
        expect(dflt.sent).toHaveLength(1);
        const tiny = await boot(PAD({ maxBytes: 5 }), { dpr: 1, width: 400 });
        tiny.draw([[10, 10], [50, 50]]);
        tiny.sizer = () => 800;
        tiny.click('save');
        expect(tiny.sent).toHaveLength(1); // a cap below 1 KB is 1 KB
        tiny.sizer = () => 2000;
        tiny.click('save');
        expect(tiny.sent).toHaveLength(1);
    });
});

// ---------------------------------------------------------------------------
// The server's frame limit
// ---------------------------------------------------------------------------

describe('"Message too large"', () => {
    const tooLarge = (env, bytes) => env.window.dispatchEvent(new env.window.CustomEvent('djust:error', { detail: { error: `Message too large (${bytes} bytes)` } }));

    it('sends the signature once more, smaller, when the server refused this one', async () => {
        const env = await boot(PAD(), { dpr: 2, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.sizer = (w) => (w >= 800 ? 40000 : w >= 600 ? 30000 : 20000);
        env.click('save');
        const first = env.sent[0].params.signature.length;
        expect(first).toBeLessThan(48000); // it fitted the cap; the server's frame limit said otherwise
        tooLarge(env, first + 80);
        expect(env.sent).toHaveLength(2);
        const second = env.sent[1].params.signature.length;
        expect(second).toBeLessThan(first); // a SMALLER one, not the same again
        expect(env.hidden()).toBe(env.sent[1].params.signature);
        expect(env.status()).toBe('Signature sent at a smaller size');
        tooLarge(env, first + 80);
        expect(env.sent).toHaveLength(2); // once
    });

    it('says what is wrong when it cannot be made to fit', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.sizer = () => 70000;
        env.click('save');
        tooLarge(env, 70100);
        expect(env.sent).toHaveLength(1);
        expect(env.status()).toMatch(/message limit .* smaller than this signature/);
    });

    it('ignores a refusal that is not about this signature', async () => {
        const env = await boot(PAD(), { dpr: 2, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.sizer = () => 100;
        env.click('save');
        tooLarge(env, 90000);
        tooLarge(env, 10);
        env.window.dispatchEvent(new env.window.CustomEvent('djust:error', { detail: { error: 'something else' } }));
        env.window.dispatchEvent(new env.window.CustomEvent('djust:error'));
        expect(env.sent).toHaveLength(1);
    });

    it('does nothing when nothing was saved', async () => {
        const env = await boot(PAD(), { dpr: 2, width: 400 });
        tooLarge(env, 70000);
        expect(env.sent).toEqual([]);
    });
});

// ---------------------------------------------------------------------------
// Typed alternative
// ---------------------------------------------------------------------------

describe('typing a name instead', () => {
    it('shows the field, announces it, and moves focus into it', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        expect(env.$('.dj-signature-pad__typed').hidden).toBe(false);
        expect(env.btn('mode').textContent).toBe('Draw instead');
        expect(env.btn('mode').getAttribute('aria-pressed')).toBe('true');
        expect(env.status()).toBe('Type your name; it becomes your signature');
        expect(env.window.document.activeElement).toBe(env.$('.dj-signature-pad__typed-input'));
        expect(env.btn('undo').disabled).toBe(true);
    });

    it('Undo is for drawn strokes only: it is off in typed mode even when strokes exist', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        expect(env.btn('undo').disabled).toBe(false);
        env.click('mode');
        expect(env.btn('undo').disabled).toBe(true);
    });

    it('typing draws the name on the canvas in a script font and enables Save', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        expect(env.btn('save').disabled).toBe(true);
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = 'Ada Lovelace';
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        expect(env.btn('save').disabled).toBe(false);
        const text = env.display().ops.filter((o) => o.op === 'fillText').pop();
        expect(text.args[0]).toBe('Ada Lovelace');
        expect(text.fillStyle).toBe('#112233');
        expect(text.font).toMatch(/italic \d+px .*cursive/);
    });

    it('shrinks the font until the name fits the pad', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = 'x'.repeat(20); // half a font size per character in the fake: 1000 wide at the first size, 100
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        const sizes = env.display().ops.filter((o) => o.op === 'fillText').map((o) => parseInt(/(\d+)px/.exec(o.font)[1], 10));
        const size = sizes.pop();
        expect(size).toBeLessThan(100);
        expect(size * 0.5 * 20).toBeLessThanOrEqual(360);
    });

    it('Save sends the typed signature; Enter in the field saves too', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = '  Ada  ';
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        const enter = new env.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
        field.dispatchEvent(enter);
        expect(enter.defaultPrevented).toBe(true);
        expect(env.sent).toHaveLength(1);
        const exported = env.ctxs[env.ctxs.length - 1].ops.filter((o) => o.op === 'fillText');
        expect(exported[exported.length - 1].args[0]).toBe('Ada');
        env.window.document.querySelector('.dj-signature-pad__save-btn').click();
        expect(env.sent).toHaveLength(2);
    });

    it('ignores input events from fields that are not the typed one', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        const hidden = env.$('.dj-signature-pad__value');
        hidden.value = 'not a name';
        hidden.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        expect(env.btn('save').disabled).toBe(true);
    });

    it('a blank name is empty: nothing to save', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = '   ';
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        expect(env.btn('save').disabled).toBe(true);
    });

    it('a hostile name is drawn as text and creates no element', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        const hostile = '<img src=x onerror=window.__pwn=1>';
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = hostile;
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        env.click('save');
        expect(env.window.__pwn).toBeUndefined();
        expect(env.$$('.dj-signature-pad img')).toHaveLength(0);
        expect(env.display().ops.filter((o) => o.op === 'fillText').pop().args[0]).toBe(hostile);
    });

    it('going back to drawing keeps the strokes; each mode keeps its own content', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.click('mode');
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = 'Ada';
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        env.click('mode');
        expect(env.$('.dj-signature-pad__typed').hidden).toBe(true);
        expect(env.hook()._strokes).toHaveLength(1);
        expect(env.btn('save').disabled).toBe(false);
        expect(env.btn('mode').textContent).toBe('Type instead');
    });

    it('Clear in typed mode clears the name, not the drawing', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.click('mode');
        const field = env.$('.dj-signature-pad__typed-input');
        field.value = 'Ada';
        field.dispatchEvent(new env.window.Event('input', { bubbles: true }));
        env.click('clear');
        expect(field.value).toBe('');
        expect(env.hook()._strokes).toHaveLength(1);
    });

    it('works with typed turned off (no field, no button)', async () => {
        const env = await boot(PAD({ typed: false }), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        expect(env.sent).toHaveLength(1);
    });
});

// ---------------------------------------------------------------------------
// Server patches
// ---------------------------------------------------------------------------

describe('server re-renders', () => {
    it('derives every control again after a patch reset them', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.click('save');
        const saved = env.hidden();
        env.window.djust.updateHooks; // keep the reference used below
        env.btn('save').disabled = false;
        env.btn('clear').disabled = true;
        env.btn('undo').disabled = true;
        env.btn('mode').textContent = 'Type instead';
        env.$('.dj-signature-pad__value').value = '';
        env.canvas.removeAttribute('dj-update');
        env.window.djust.updateHooks();
        expect(env.btn('clear').disabled).toBe(false);
        expect(env.btn('undo').disabled).toBe(false);
        expect(env.hidden()).toBe(saved);
        expect(env.canvas.getAttribute('dj-update')).toBe('ignore');
    });

    it('keeps typed mode through a patch that resets the field\'s visibility', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.click('mode');
        env.$('.dj-signature-pad__typed').hidden = true;
        env.btn('mode').textContent = 'Type instead';
        env.window.djust.updateHooks();
        expect(env.$('.dj-signature-pad__typed').hidden).toBe(false);
        expect(env.btn('mode').textContent).toBe('Draw instead');
    });

    it('follows the server disabling the pad', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.root.classList.add('dj-signature-pad--disabled');
        env.window.djust.updateHooks();
        expect(env.btn('save').disabled).toBe(true);
        expect(env.canvas.getAttribute('aria-disabled')).toBe('true');
        env.draw([[60, 60], [70, 70]]);
        expect(env.hook()._strokes).toHaveLength(1);
        env.root.classList.remove('dj-signature-pad--disabled');
        env.window.djust.updateHooks();
        expect(env.btn('save').disabled).toBe(false);
        expect(env.canvas.hasAttribute('aria-disabled')).toBe(false);
    });

    it('does not clear the drawing on a patch', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        env.draw([[10, 10], [50, 50]]);
        env.display().ops.length = 0;
        for (let i = 0; i < 3; i++) env.window.djust.updateHooks();
        expect(env.hook()._strokes).toHaveLength(1);
        expect(env.display().ops.filter((o) => o.op === 'clearRect')).toHaveLength(0);
        expect(env.canvas.width).toBe(400);
    });
});

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

describe('teardown', () => {
    it('destroyed() removes every listener, disconnects the observer and ignores later input', async () => {
        class FakeRO { constructor(cb) { this.cb = cb; FakeRO.last = this; } observe() {} disconnect() { this.gone = true; } }
        const env = await boot(PAD(), { dpr: 1, width: 400, resizeObserver: FakeRO });
        const hook = env.hook();
        const winRemoved = [];
        const orig = env.window.removeEventListener.bind(env.window);
        env.window.removeEventListener = (t, f, o) => { winRemoved.push(t); return orig(t, f, o); };
        const canvasRemoved = [];
        const origCanvas = env.canvas.removeEventListener.bind(env.canvas);
        env.canvas.removeEventListener = (t, f, o) => { canvasRemoved.push(t); return origCanvas(t, f, o); };
        env.ptr('pointerdown', 20, 30); // a stroke in progress
        hook.destroyed();
        expect(FakeRO.last.gone).toBe(true);
        expect(winRemoved.sort()).toEqual(['djust:error', 'resize']);
        expect(canvasRemoved.sort()).toEqual(['contextmenu', 'lostpointercapture', 'pointercancel', 'pointerdown', 'pointermove', 'pointerup']);
        expect(env.released).toEqual([1]);
        env.draw([[10, 10], [50, 50]]);
        env.click('clear');
        env.window.dispatchEvent(new env.window.CustomEvent('djust:error', { detail: { error: 'Message too large (70000 bytes)' } }));
        expect(env.sent).toEqual([]);
        hook.destroyed(); // twice is harmless
    });

    it('cancels a pending resize frame', async () => {
        class FakeRO { constructor(cb) { this.cb = cb; FakeRO.last = this; } observe() {} disconnect() {} }
        const env = await boot(PAD(), { dpr: 1, width: 400, resizeObserver: FakeRO });
        const cancel = vi.spyOn(env.window, 'cancelAnimationFrame');
        FakeRO.last.cb();
        const id = env.hook()._raf;
        expect(id).toBeTruthy();
        env.hook().destroyed();
        expect(cancel).toHaveBeenCalledWith(id);
    });

    it('binds once however often the page patches: one gesture, one stroke', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        for (let i = 0; i < 4; i++) env.window.djust.updateHooks();
        env.draw([[10, 10], [50, 50]]);
        env.click('clear');
        env.draw([[10, 10], [50, 50]]);
        expect(env.hook()._strokes).toHaveLength(1);
        env.click('save');
        expect(env.sent).toHaveLength(1);
    });

    it('removing the element through a patch destroys the hook', async () => {
        const env = await boot(PAD(), { dpr: 1, width: 400 });
        const destroyed = vi.spyOn(env.hook(), 'destroyed');
        env.root.remove();
        env.window.djust.updateHooks();
        expect(destroyed).toHaveBeenCalledTimes(1);
    });

    it('two pads keep their own strokes', async () => {
        const env = await boot(PAD() + PAD().replace('class="dj-signature-pad"', 'class="dj-signature-pad" id="two"'), { dpr: 1, width: 400 });
        const pads = env.$$('.dj-signature-pad');
        const second = env.window.djust.getHook(pads[1]);
        expect(second).not.toBe(env.hook());
        env.draw([[10, 10], [50, 50]]);
        expect(second._strokes).toHaveLength(0);
    });
});

describe('what the script may not do', () => {
    it('contains no markup parsing, no loading and no storage', () => {
        for (const banned of [/\binnerHTML\b/, /\bouterHTML\b/, /insertAdjacentHTML/, /\beval\s*\(/, /new Function/, /document\.write/, /new Image/, /\bfetch\s*\(/, /XMLHttpRequest/, /localStorage|sessionStorage|indexedDB/]) {
            expect(SCRIPT).not.toMatch(banned);
        }
    });
});
