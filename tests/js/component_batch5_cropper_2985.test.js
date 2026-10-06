/**
 * #2985 batch 5 — ImageCropper answers its dj-hook, sends only a box in natural
 * pixels, and an app's own hook of the same name still wins.
 *
 * The markup is what the component renders (python/djust/tests/
 * test_component_batch5_cropper_2985.py pins it on every render path). jsdom has
 * no layout or image decoding, so natural and displayed sizes are set on the
 * element; real pointers, real layout, touch and the server crop are checked in
 * Chromium by tests/playwright/test_component_batch5_cropper_2985.py.
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');
const SCRIPT = read('image-cropper.js');

const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
const CROP = ({ ratio = '', minW = 50, minH = 50, event = 'save_crop', disabled = false, src = '/p.jpg' } = {}) =>
    `<div class="dj-image-cropper${disabled ? ' dj-image-cropper--disabled' : ''}" dj-hook="ImageCropper" data-crop-event="${event}" data-min-width="${minW}" data-min-height="${minH}"${ratio ? ` data-aspect-ratio="${ratio}"` : ''}>` +
    `<div class="dj-image-cropper__canvas"><img class="dj-image-cropper__image" src="${src}" alt="Image to crop" draggable="false">` +
    '<div class="dj-image-cropper__overlay"></div>' +
    '<div class="dj-image-cropper__selection" tabindex="0" role="group" aria-label="Crop area">' +
    HANDLES.map((h) => `<span class="dj-image-cropper__handle dj-image-cropper__handle--${h}" data-handle="${h}" aria-hidden="true"></span>`).join('') +
    '</div></div><div class="dj-image-cropper__actions"><button class="dj-image-cropper__crop-btn" type="button">Crop</button>' +
    '<button class="dj-image-cropper__reset-btn" type="button">Reset</button></div>' +
    '<div class="dj-image-cropper__status" role="status" aria-live="polite"></div></div>';

function createEnv(bodyHtml, { resizeObserver } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = { log: () => {}, error: () => {}, debug: () => {}, info: () => {}, warn: (...a) => warnings.push(a.join(' ')) };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    if (resizeObserver) window.ResizeObserver = resizeObserver;
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    const env = { window, warnings, sent: [] };
    window.djust.handleEvent = (name, params) => { env.sent.push({ name, params }); };
    return env;
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// The image: natural size, displayed width, and whether it has loaded.
function setImage(img, { nw = 1600, nh = 900, shown = 400, complete = true } = {}) {
    Object.defineProperty(img, 'naturalWidth', { value: complete ? nw : 0, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: complete ? nh : 0, configurable: true });
    Object.defineProperty(img, 'clientWidth', { value: shown, configurable: true });
    Object.defineProperty(img, 'complete', { value: complete, configurable: true });
    Object.defineProperty(img, 'getBoundingClientRect', { value: () => ({ left: 10, top: 20, width: shown, height: (shown * nh) / nw }), configurable: true });
}

async function boot(markup, { image = {}, preRegister, resizeObserver, load = true } = {}) {
    const env = createEnv(markup, { resizeObserver });
    if (preRegister) preRegister(env.window);
    const $ = (s) => env.window.document.querySelector(s);
    const img = $('.dj-image-cropper__image');
    if (img) setImage(img, image);
    env.window.eval(SCRIPT);
    env.window.djust.mountHooks();
    await tick();
    Object.assign(env, { $, $$: (s) => Array.from(env.window.document.querySelectorAll(s)) });
    env.img = img;
    env.root = $('.dj-image-cropper');
    env.sel = $('.dj-image-cropper__selection');
    env.wrap = $('.dj-image-cropper__canvas');
    env.hook = () => env.window.djust.getHook(env.root);
    env.status = () => $('.dj-image-cropper__status').textContent.trim();
    env.btn = (n) => $(`.dj-image-cropper__${n}-btn`);
    env.box = () => { const b = env.hook()._box; return b && { x: b.x, y: b.y, w: b.w, h: b.h }; };
    env.captured = [];
    env.released = [];
    env.wrap.setPointerCapture = (id) => env.captured.push(id);
    env.wrap.releasePointerCapture = (id) => env.released.push(id);
    // client coordinates of a natural point: the image is at (10, 20), displayed at 400 wide for 1600
    env.client = (nx, ny, shown = parseFloat(env.img.clientWidth)) => [10 + (nx * shown) / env.img.naturalWidth, 20 + (ny * shown) / env.img.naturalWidth];
    env.ptr = (type, target, cx, cy, extra = {}) => {
        const e = new env.window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: cx, clientY: cy, button: extra.button || 0 });
        for (const [k, v] of Object.entries({ pointerId: 1, isPrimary: true, pointerType: 'mouse', ...extra })) Object.defineProperty(e, k, { value: v });
        target.dispatchEvent(e);
        return e;
    };
    env.drag = (target, from, to, extra = {}) => {
        const [fx, fy] = env.client(...from);
        const [tx, ty] = env.client(...to);
        env.ptr('pointerdown', target, fx, fy, extra);
        env.ptr('pointermove', env.wrap, (fx + tx) / 2, (fy + ty) / 2, extra);
        env.ptr('pointermove', env.wrap, tx, ty, extra);
        env.ptr('pointerup', env.wrap, tx, ty, extra);
    };
    env.key = (k, extra = {}) => {
        const e = new env.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra });
        env.sel.dispatchEvent(e);
        return e;
    };
    env.handle = (h) => $(`[data-handle="${h}"]`);
    env.load = () => env.img.dispatchEvent(new env.window.Event('load'));
    void load;
    return env;
}

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

describe('the shipped hook never replaces an app hook (#2985 batch 5)', () => {
    const probe = (env) => env.sel.getAttribute('dj-update') === 'ignore';

    it('registers a hook, so mounting logs no "No hook registered"', async () => {
        const env = await boot(CROP());
        expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
        expect(probe(env)).toBe(true);
    });

    it('without the script the warning is logged (gate-off)', () => {
        const env = createEnv(CROP());
        env.window.djust.mountHooks();
        expect(env.warnings.some((w) => w.includes('No hook registered for "ImageCropper"'))).toBe(true);
    });

    it('an app hook in either registry, registered first, is the one that runs', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = await boot(CROP(), { preRegister: (w) => { if (where === 'DjustHooks') w.DjustHooks = { ImageCropper: { mounted } }; else w.djust.hooks = { ImageCropper: { mounted } }; } });
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(probe(env)).toBe(false);
        }
    });

    it('an app hook registered AFTER the script still wins, in either registry', async () => {
        for (const where of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = createEnv(CROP());
            env.window.eval(SCRIPT);
            if (where === 'djust.hooks') env.window.djust.hooks = { ImageCropper: { mounted } };
            else env.window.DjustHooks.ImageCropper = { mounted };
            env.window.djust.mountHooks();
            await tick();
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(env.window.document.querySelector('.dj-image-cropper__selection').hasAttribute('dj-update')).toBe(false);
        }
    });

    it("an app hook's pushEvent/handleEvent API is untouched", async () => {
        const seen = [];
        await boot(CROP(), { preRegister: (w) => { w.djust.hooks = { ImageCropper: { mounted() { seen.push(typeof this.pushEvent, typeof this.handleEvent); } } }; } });
        expect(seen).toEqual(['function', 'function']);
    });
});

// ---------------------------------------------------------------------------
// Geometry: pure functions on boxes in natural pixels
// ---------------------------------------------------------------------------

function rng(seed) {
    let s = seed;
    return () => { s = (s * 1664525 + 1013904223) % 4294967296; return s / 4294967296; };
}

describe('geometry', () => {
    const geo = async () => (await boot(CROP())).hook().geometry;

    it('parses a ratio from "16/9", "4:3" and "1.5"; anything else is free (0)', async () => {
        const g = await geo();
        expect(g.parseRatio('16/9')).toBeCloseTo(16 / 9, 9);
        expect(g.parseRatio('4:3')).toBeCloseTo(4 / 3, 9);
        expect(g.parseRatio(' 1.5 ')).toBe(1.5);
        for (const bad of ['', null, undefined, 'x', '0', '1/0', '0/1', '-1', '100', '0.001', 'nan']) expect(g.parseRatio(bad), String(bad)).toBe(0);
    });

    it('the smallest box: the minimums, but never more than the image, and in the ratio', async () => {
        const g = await geo();
        expect(g.limits(1000, 500, 50, 40, 0)).toEqual({ minW: 50, minH: 40 });
        expect(g.limits(30, 20, 50, 50, 0)).toEqual({ minW: 30, minH: 20 });
        const r = g.limits(1000, 500, 50, 40, 2);
        expect(r.minW).toBe(80);
        expect(r.minH).toBe(40);
        const tight = g.limits(100, 30, 50, 50, 2);
        expect(tight.minH).toBeLessThanOrEqual(30);
        expect(tight.minW / tight.minH).toBeCloseTo(2, 9);
        expect(g.limits(10, 10, 5, 5, 0)).toEqual({ minW: 5, minH: 5 });
    });

    it('starts at 80% of the image, centred, in the ratio when one is set', async () => {
        const g = await geo();
        expect(g.initialBox(1000, 500, 0, 50, 50)).toEqual({ x: 100, y: 50, w: 800, h: 400 });
        const wide = g.initialBox(1600, 900, 1, 50, 50);
        expect(wide.w).toBeCloseTo(wide.h, 9);
        expect(wide.h).toBeCloseTo(720, 9);
        expect(wide.x + wide.w / 2).toBeCloseTo(800, 9);
        const tall = g.initialBox(900, 1600, 16 / 9, 50, 50);
        expect(tall.w / tall.h).toBeCloseTo(16 / 9, 9);
        expect(tall.w).toBeCloseTo(720, 9);
    });

    it('grows to the minimum, and an image smaller than the minimum is selected whole', async () => {
        const g = await geo();
        const small = g.initialBox(100, 60, 0, 90, 55);
        expect(small.w).toBeGreaterThanOrEqual(90);
        expect(small.h).toBeGreaterThanOrEqual(55);
        expect(g.initialBox(30, 20, 0, 50, 50)).toEqual({ x: 0, y: 0, w: 30, h: 20 });
        const locked = g.initialBox(30, 20, 16 / 9, 50, 50);
        expect(locked.w).toBeLessThanOrEqual(30);
        expect(locked.h).toBeLessThanOrEqual(20);
        expect(locked.w / locked.h).toBeCloseTo(16 / 9, 9);
    });

    it('moves inside the image and never leaves it', async () => {
        const g = await geo();
        const box = { x: 100, y: 100, w: 200, h: 100 };
        expect(g.moveBox(box, 30, -20, 1000, 500)).toEqual({ x: 130, y: 80, w: 200, h: 100 });
        expect(g.moveBox(box, -1000, -1000, 1000, 500)).toEqual({ x: 0, y: 0, w: 200, h: 100 });
        expect(g.moveBox(box, 5000, 5000, 1000, 500)).toEqual({ x: 800, y: 400, w: 200, h: 100 });
    });

    describe('free resize', () => {
        const box = { x: 100, y: 100, w: 200, h: 100 };
        const free = (h, dx, dy) => geo().then((g) => g.resizeBox(box, h, dx, dy, 0, 1000, 500, 50, 40));

        it('each handle moves its own side and keeps the opposite one', async () => {
            expect(await free('e', 30, 99)).toEqual({ x: 100, y: 100, w: 230, h: 100 });
            expect(await free('w', 30, 99)).toEqual({ x: 130, y: 100, w: 170, h: 100 });
            expect(await free('s', 99, 30)).toEqual({ x: 100, y: 100, w: 200, h: 130 });
            expect(await free('n', 99, 30)).toEqual({ x: 100, y: 130, w: 200, h: 70 });
            expect(await free('se', 30, 20)).toEqual({ x: 100, y: 100, w: 230, h: 120 });
            expect(await free('nw', 30, 20)).toEqual({ x: 130, y: 120, w: 170, h: 80 });
            expect(await free('ne', 30, 20)).toEqual({ x: 100, y: 120, w: 230, h: 80 });
            expect(await free('sw', 30, 20)).toEqual({ x: 130, y: 100, w: 170, h: 120 });
        });

        it('stops at the minimum instead of flipping over', async () => {
            expect((await free('e', -1000, 0)).w).toBe(50);
            expect((await free('w', 1000, 0))).toEqual({ x: 250, y: 100, w: 50, h: 100 });
            expect((await free('s', 0, -1000)).h).toBe(40);
            expect((await free('nw', 1000, 1000))).toEqual({ x: 250, y: 160, w: 50, h: 40 });
        });

        it('stops at the image edge', async () => {
            expect((await free('e', 5000, 0))).toEqual({ x: 100, y: 100, w: 900, h: 100 });
            expect((await free('w', -5000, 0))).toEqual({ x: 0, y: 100, w: 300, h: 100 });
            expect((await free('s', 0, 5000))).toEqual({ x: 100, y: 100, w: 200, h: 400 });
            expect((await free('n', 0, -5000))).toEqual({ x: 100, y: 0, w: 200, h: 200 });
        });
    });

    describe('locked ratio', () => {
        const ratio = 2;
        const box = { x: 200, y: 100, w: 200, h: 100 };
        const locked = (h, dx, dy, b = box) => geo().then((g) => g.resizeBox(b, h, dx, dy, ratio, 1000, 500, 50, 40));

        it('a corner keeps the opposite corner and the ratio', async () => {
            const se = await locked('se', 60, 30);
            expect(se.x).toBe(200);
            expect(se.y).toBe(100);
            expect(se.w / se.h).toBeCloseTo(ratio, 9);
            expect(se.w).toBeGreaterThan(200);
            const nw = await locked('nw', -60, -30);
            expect(nw.x + nw.w).toBeCloseTo(400, 9);
            expect(nw.y + nw.h).toBeCloseTo(200, 9);
            expect(nw.w / nw.h).toBeCloseTo(ratio, 9);
            const ne = await locked('ne', 40, -20);
            expect(ne.x).toBe(200);
            expect(ne.y + ne.h).toBeCloseTo(200, 9);
            const sw = await locked('sw', -40, 20);
            expect(sw.x + sw.w).toBeCloseTo(400, 9);
            expect(sw.y).toBe(100);
        });

        it('a side changes that dimension and the other follows, centred', async () => {
            const e = await locked('e', 40, 99);
            expect(e.w).toBe(240);
            expect(e.h).toBeCloseTo(120, 9);
            expect(e.x).toBe(200);
            expect(e.y + e.h / 2).toBeCloseTo(150, 9);
            const w = await locked('w', -40, 0);
            expect(w.x + w.w).toBe(400);
            expect(w.w).toBe(240);
            const s = await locked('s', 99, 30);
            expect(s.h).toBe(130);
            expect(s.w).toBeCloseTo(260, 9);
            expect(s.x + s.w / 2).toBeCloseTo(300, 9);
            const n = await locked('n', 0, -30);
            expect(n.y + n.h).toBe(200);
            expect(n.w / n.h).toBeCloseTo(ratio, 9);
        });

        it('a corner drag sets the size from both axes (their average, in the ratio)', async () => {
            const se = await locked('se', 60, 0);
            expect(se.w).toBeCloseTo(230, 9); // (260 + 100 x 2) / 2
            expect(se.h).toBeCloseTo(115, 9);
            const tall = await locked('se', 0, 60);
            expect(tall.w).toBeCloseTo(260, 9); // (200 + 160 x 2) / 2
        });

        it('stays inside the image, above the minimum, and exactly in ratio, for any drag (random)', async () => {
            const g = await geo();
            const rand = rng(7);
            for (let i = 0; i < 4000; i++) {
                const nw = 100 + Math.floor(rand() * 2000);
                const nh = 100 + Math.floor(rand() * 2000);
                const r = [0.5, 1, 4 / 3, 16 / 9, 2.5][Math.floor(rand() * 5)];
                let b = g.initialBox(nw, nh, r, 50, 50);
                for (let step = 0; step < 6; step++) {
                    const h = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'][Math.floor(rand() * 8)];
                    b = g.resizeBox(b, h, (rand() - 0.5) * nw, (rand() - 0.5) * nh, r, nw, nh, 50, 50);
                    expect(b.x).toBeGreaterThanOrEqual(-1e-6);
                    expect(b.y).toBeGreaterThanOrEqual(-1e-6);
                    expect(b.x + b.w).toBeLessThanOrEqual(nw + 1e-6);
                    expect(b.y + b.h).toBeLessThanOrEqual(nh + 1e-6);
                    expect(Math.abs(b.w / b.h - r)).toBeLessThan(1e-6);
                    const lim = g.limits(nw, nh, 50, 50, r);
                    expect(b.w).toBeGreaterThanOrEqual(lim.minW - 1e-6);
                    expect(b.h).toBeGreaterThanOrEqual(lim.minH - 1e-6);
                }
            }
        });
    });

    it('free resizing also stays inside the image and above the minimum (random)', async () => {
        const g = await geo();
        const rand = rng(11);
        for (let i = 0; i < 4000; i++) {
            const nw = 60 + Math.floor(rand() * 2000);
            const nh = 60 + Math.floor(rand() * 2000);
            let b = g.initialBox(nw, nh, 0, 50, 40);
            for (let step = 0; step < 6; step++) {
                const h = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'][Math.floor(rand() * 8)];
                b = g.resizeBox(b, h, (rand() - 0.5) * 3 * nw, (rand() - 0.5) * 3 * nh, 0, nw, nh, 50, 40);
                const lim = g.limits(nw, nh, 50, 40, 0);
                expect(b.x).toBeGreaterThanOrEqual(0);
                expect(b.y).toBeGreaterThanOrEqual(0);
                expect(b.x + b.w).toBeLessThanOrEqual(nw + 1e-9);
                expect(b.y + b.h).toBeLessThanOrEqual(nh + 1e-9);
                expect(b.w).toBeGreaterThanOrEqual(lim.minW - 1e-9);
                expect(b.h).toBeGreaterThanOrEqual(lim.minH - 1e-9);
            }
        }
    });

    describe('a new box dragged from a point', () => {
        it('free: the four directions, clamped to the image', async () => {
            const g = await geo();
            expect(g.newBox(500, 250, 700, 350, 0, 1000, 500, 50, 40)).toEqual({ x: 500, y: 250, w: 200, h: 100 });
            expect(g.newBox(500, 250, 300, 150, 0, 1000, 500, 50, 40)).toEqual({ x: 300, y: 150, w: 200, h: 100 });
            expect(g.newBox(500, 250, 700, 150, 0, 1000, 500, 50, 40)).toEqual({ x: 500, y: 150, w: 200, h: 100 });
            expect(g.newBox(500, 250, 300, 350, 0, 1000, 500, 50, 40)).toEqual({ x: 300, y: 250, w: 200, h: 100 });
            expect(g.newBox(900, 400, 5000, 5000, 0, 1000, 500, 50, 40)).toEqual({ x: 900, y: 400, w: 100, h: 100 });
            expect(g.newBox(100, 100, -5000, -5000, 0, 1000, 500, 50, 40)).toEqual({ x: 0, y: 0, w: 100, h: 100 });
        });

        it('near an edge, where the minimum does not fit on the dragged side, the box is shifted back inside', async () => {
            const g = await geo();
            const b = g.newBox(5, 5, 0, 0, 0, 1000, 500, 50, 40);
            expect(b).toEqual({ x: 0, y: 0, w: 50, h: 40 });
            const l = g.newBox(5, 5, 0, 0, 2, 1000, 500, 50, 40);
            expect(l.x).toBe(0);
            expect(l.y).toBeGreaterThanOrEqual(0);
            expect(l.w / l.h).toBeCloseTo(2, 9);
        });

        it('locked: both axes of the drag set the size', async () => {
            const g = await geo();
            const b = g.newBox(500, 250, 600, 450, 2, 1000, 500, 50, 40);
            expect(b.w).toBeCloseTo(250, 9); // (100 + 200 x 2) / 2
            expect(b.h).toBeCloseTo(125, 9);
        });

        it('free: never smaller than the minimum', async () => {
            const g = await geo();
            const b = g.newBox(500, 250, 502, 251, 0, 1000, 500, 50, 40);
            expect(b.w).toBe(50);
            expect(b.h).toBe(40);
        });

        it('locked: always in the ratio, inside the image (random)', async () => {
            const g = await geo();
            const rand = rng(5);
            for (let i = 0; i < 4000; i++) {
                const nw = 100 + Math.floor(rand() * 2000);
                const nh = 100 + Math.floor(rand() * 2000);
                const r = [0.5, 1, 16 / 9][Math.floor(rand() * 3)];
                const b = g.newBox(rand() * nw, rand() * nh, rand() * nw * 1.2 - nw * 0.1, rand() * nh * 1.2 - nh * 0.1, r, nw, nh, 50, 50);
                expect(Math.abs(b.w / b.h - r)).toBeLessThan(1e-6);
                expect(b.x).toBeGreaterThanOrEqual(-1e-6);
                expect(b.y).toBeGreaterThanOrEqual(-1e-6);
                expect(b.x + b.w).toBeLessThanOrEqual(nw + 1e-6);
                expect(b.y + b.h).toBeLessThanOrEqual(nh + 1e-6);
            }
        });
    });

    it('sends whole numbers inside the image, at least one pixel', async () => {
        const g = await geo();
        expect(g.toPixels({ x: 10.4, y: 20.6, w: 100.5, h: 50.4 }, 1000, 500)).toEqual({ x: 10, y: 21, width: 101, height: 50 });
        expect(g.toPixels({ x: -5, y: -5, w: 0.2, h: 0.2 }, 100, 100)).toEqual({ x: 0, y: 0, width: 1, height: 1 });
        expect(g.toPixels({ x: 95, y: 95, w: 50, h: 50 }, 100, 100)).toEqual({ x: 95, y: 95, width: 5, height: 5 });
        expect(g.toPixels({ x: 1e9, y: 1e9, w: 1e9, h: 1e9 }, 100, 80)).toEqual({ x: 99, y: 79, width: 1, height: 1 });
        const p = g.toPixels({ x: 0, y: 0, w: 100, h: 80 }, 100, 80);
        expect(Object.keys(p).sort()).toEqual(['height', 'width', 'x', 'y']);
    });
});

// ---------------------------------------------------------------------------
// The image and the box on screen
// ---------------------------------------------------------------------------

describe('the image and its box', () => {
    it('shows the box over the image at the displayed scale (natural pixels x scale)', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        // initial: 80% centred = x 160, y 90, w 1280, h 720 natural; scale 0.25
        expect(env.box()).toEqual({ x: 160, y: 90, w: 1280, h: 720 });
        expect(env.sel.style.left).toBe('40px');
        expect(env.sel.style.top).toBe('22.5px');
        expect(env.sel.style.width).toBe('320px');
        expect(env.sel.style.height).toBe('180px');
        expect(env.sel.hidden).toBe(false);
        expect(env.status()).toBe('Crop area 160, 90, 1280 by 720 pixels of 1600 by 900');
    });

    it('is hidden, with Crop and Reset disabled, until the image has loaded', async () => {
        const env = await boot(CROP(), { image: { complete: false } });
        expect(env.sel.hidden).toBe(true);
        expect(env.btn('crop').disabled).toBe(true);
        expect(env.btn('reset').disabled).toBe(true);
        setImage(env.img, { nw: 800, nh: 600, shown: 400 });
        env.load();
        expect(env.sel.hidden).toBe(false);
        expect(env.btn('crop').disabled).toBe(false);
        expect(env.box().w).toBe(640);
    });

    it('a broken image disables Crop and says so', async () => {
        const env = await boot(CROP(), { image: { complete: false } });
        setImage(env.img, { nw: 0, nh: 0, shown: 0, complete: true });
        env.img.dispatchEvent(new env.window.Event('error'));
        expect(env.status()).toBe('The image could not be loaded');
        expect(env.btn('crop').disabled).toBe(true);
        expect(env.sel.hidden).toBe(true);
        env.btn('crop').click();
        expect(env.sent).toEqual([]);
    });

    it('a load event for an image with no size is a failure too', async () => {
        const env = await boot(CROP(), { image: { complete: false } });
        setImage(env.img, { nw: 0, nh: 0, shown: 0, complete: true });
        env.load();
        expect(env.status()).toBe('The image could not be loaded');
        expect(env.btn('crop').disabled).toBe(true);
    });

    it('announces the same thing twice in a row by changing the text', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        env.btn('reset').click();
        const first = env.$('.dj-image-cropper__status').textContent;
        env.btn('reset').click();
        expect(env.$('.dj-image-cropper__status').textContent).not.toBe(first);
        expect(env.$('.dj-image-cropper__status').textContent.trim()).toBe(first.trim());
    });

    it('an image that is already broken when the hook mounts is reported at once', async () => {
        const env = await boot(CROP(), { image: { nw: 0, nh: 0, shown: 0, complete: true } });
        expect(env.status()).toBe('The image could not be loaded');
    });

    it('another image from the server resets the box to the new image', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        env.drag(env.sel, [800, 450], [900, 500]);
        setImage(env.img, { nw: 400, nh: 400, shown: 400 });
        env.img.setAttribute('src', '/other.jpg');
        env.load();
        expect(env.box()).toEqual({ x: 40, y: 40, w: 320, h: 320 });
        expect(env.status()).toBe('Crop area 40, 40, 320 by 320 pixels of 400 by 400');
    });

    it('follows a change of the displayed size: the same box in natural pixels, a new place on screen', async () => {
        class FakeRO { constructor(cb) { this.cb = cb; FakeRO.last = this; } observe(n) { this.node = n; } disconnect() { this.gone = true; } }
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 }, resizeObserver: FakeRO });
        expect(FakeRO.last.node).toBe(env.img);
        const before = env.box();
        setImage(env.img, { nw: 1600, nh: 900, shown: 200 });
        FakeRO.last.cb();
        expect(env.box()).toEqual(before);
        expect(env.sel.style.width).toBe('160px');
        expect(env.sel.style.left).toBe('20px');
    });

    it('Reset puts the box back and announces it', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        const start = env.box();
        env.drag(env.sel, [800, 450], [900, 500]);
        expect(env.box()).not.toEqual(start);
        env.btn('reset').click();
        expect(env.box()).toEqual(start);
        expect(env.status()).toMatch(/^Reset\. Crop area 160, 90, 1280 by 720/);
    });

    it('a disabled cropper does nothing', async () => {
        const env = await boot(CROP({ disabled: true }), { image: { nw: 1600, nh: 900, shown: 400 } });
        expect(env.btn('crop').disabled).toBe(true);
        const start = env.box();
        env.drag(env.sel, [800, 450], [900, 500]);
        env.key('ArrowRight');
        expect(env.box()).toEqual(start);
        env.hook()._crop();
        expect(env.sent).toEqual([]);
    });
});

// ---------------------------------------------------------------------------
// Crop: what is sent
// ---------------------------------------------------------------------------

describe('Crop', () => {
    it('sends only {x, y, width, height}, whole numbers in natural pixels, to the configured event', async () => {
        const env = await boot(CROP({ event: 'do_crop' }), { image: { nw: 1600, nh: 900, shown: 400 } });
        env.btn('crop').click();
        expect(env.sent).toEqual([{ name: 'do_crop', params: { x: 160, y: 90, width: 1280, height: 720 } }]);
        for (const v of Object.values(env.sent[0].params)) expect(Number.isInteger(v)).toBe(true);
        expect(env.status()).toBe('Crop sent: 160, 90, 1280 by 720 pixels');
    });

    it('sends the box after it was moved and resized, rounded', async () => {
        const env = await boot(CROP(), { image: { nw: 1000, nh: 500, shown: 500 } });
        env.drag(env.sel, [500, 250], [533.3, 270.4]);
        env.btn('crop').click();
        const p = env.sent[0].params;
        expect(p.x).toBe(Math.round(env.box().x));
        expect(p.width).toBe(Math.round(env.box().w));
        expect(p.x + p.width).toBeLessThanOrEqual(1000);
        expect(p.y + p.height).toBeLessThanOrEqual(500);
    });

    it('goes through the strict-parameter gate like dj-click, and honours a veto', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        env.window.djust._strictBinding = vi.fn(() => false);
        env.btn('crop').click();
        expect(env.window.djust._strictBinding).toHaveBeenCalledTimes(1);
        expect(env.sent).toEqual([]);
    });

    it('falls back to the hook pushEvent when the client API is absent', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        delete env.window.djust.handleEvent;
        const push = vi.fn();
        env.hook().pushEvent = push;
        env.btn('crop').click();
        expect(push).toHaveBeenCalledWith('save_crop', { x: 160, y: 90, width: 1280, height: 720 });
    });
});

// ---------------------------------------------------------------------------
// Pointer
// ---------------------------------------------------------------------------

describe('pointer', () => {
    const at = { image: { nw: 1600, nh: 900, shown: 400 } };

    it('dragging the box moves it by the pointer\'s distance in natural pixels', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        env.drag(env.sel, [800, 450], [900, 500]);
        expect(env.box().x).toBeCloseTo(start.x + 100, 6);
        expect(env.box().y).toBeCloseTo(start.y + 50, 6);
        expect(env.box().w).toBe(start.w);
        expect(env.captured).toEqual([1]);
        expect(env.released).toEqual([1]);
        expect(env.status()).toMatch(/^Crop area 260, 140, 1280 by 720/);
    });

    it('a drag is clamped to the image', async () => {
        const env = await boot(CROP(), at);
        env.drag(env.sel, [800, 450], [5000, 5000]);
        expect(env.box()).toEqual({ x: 320, y: 180, w: 1280, h: 720 });
    });

    it('a click, or a jitter under a few pixels, changes nothing', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        const [x, y] = env.client(800, 450);
        env.ptr('pointerdown', env.sel, x, y);
        env.ptr('pointermove', env.wrap, x + 1, y + 1);
        env.ptr('pointerup', env.wrap, x + 1, y + 1);
        expect(env.box()).toEqual(start);
        env.drag(env.img, [10, 10], [11, 11]);
        expect(env.box()).toEqual(start);
    });

    it('dragging a handle resizes from the opposite side', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        env.drag(env.handle('se'), [1440, 810], [1500, 850]);
        expect(env.box().x).toBe(start.x);
        expect(env.box().y).toBe(start.y);
        expect(env.box().w).toBeCloseTo(start.w + 60, 6);
        expect(env.box().h).toBeCloseTo(start.h + 40, 6);
        env.drag(env.handle('w'), [160, 450], [260, 450]);
        expect(env.box().x).toBeCloseTo(start.x + 100, 6);
        expect(env.box().x + env.box().w).toBeCloseTo(start.x + start.w + 60, 6);
    });

    it('a locked ratio holds through handle drags', async () => {
        const env = await boot(CROP({ ratio: '16/9' }), at);
        env.drag(env.handle('ne'), [1440, 90], [1500, 50]);
        expect(env.box().w / env.box().h).toBeCloseTo(16 / 9, 6);
        env.drag(env.handle('s'), [800, 810], [800, 700]);
        expect(env.box().w / env.box().h).toBeCloseTo(16 / 9, 6);
    });

    it('dragging on the image outside the box draws a new one', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 400 } });
        env.drag(env.img, [20, 20], [100, 60]);
        // 80 x 40 natural is below the minimum (50 is the minimum, 80 x 40 -> h raised to 50)
        expect(env.box().x).toBeCloseTo(20, 6);
        expect(env.box().y).toBeCloseTo(20, 6);
        expect(env.box().w).toBeCloseTo(80, 6);
        expect(env.box().h).toBe(50);
        env.drag(env.img, [1500, 800], [1200, 500]);
        expect(env.box()).toEqual({ x: 1200, y: 500, w: 300, h: 300 });
    });

    it('ignores a second finger, the right button and a drag started while another is in progress', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        const [x, y] = env.client(800, 450);
        env.ptr('pointerdown', env.sel, x, y, { isPrimary: false });
        env.ptr('pointerdown', env.sel, x, y, { button: 2 });
        env.ptr('pointermove', env.wrap, x + 40, y + 40);
        expect(env.box()).toEqual(start);
        env.ptr('pointerdown', env.sel, x, y);
        env.ptr('pointerdown', env.sel, x + 5, y + 5, { pointerId: 2 });
        env.ptr('pointermove', env.wrap, x + 40, y + 40, { pointerId: 2 });
        env.ptr('pointerup', env.wrap, x + 40, y + 40, { pointerId: 2 });
        expect(env.box()).toEqual(start);
        env.ptr('pointermove', env.wrap, x + 40, y + 40);
        expect(env.box().x).not.toBe(start.x);
    });

    it('prevents the default of the pointer-down (no text selection, no image drag)', async () => {
        const env = await boot(CROP(), at);
        const [x, y] = env.client(800, 450);
        expect(env.ptr('pointerdown', env.sel, x, y).defaultPrevented).toBe(true);
        env.ptr('pointerup', env.wrap, x, y);
    });

    it('a lost capture ends the drag', async () => {
        const env = await boot(CROP(), at);
        const [x, y] = env.client(800, 450);
        env.ptr('pointerdown', env.sel, x, y);
        env.ptr('pointermove', env.wrap, x + 40, y);
        env.ptr('lostpointercapture', env.wrap, x + 40, y);
        const at1 = env.box();
        env.ptr('pointermove', env.wrap, x + 200, y);
        expect(env.box()).toEqual(at1);
    });

    it('measures in natural pixels whatever the displayed size', async () => {
        const env = await boot(CROP(), { image: { nw: 1600, nh: 900, shown: 200 } });
        const start = env.box();
        env.drag(env.sel, [800, 450], [900, 500]);
        expect(env.box().x).toBeCloseTo(start.x + 100, 6);
    });
});

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------

describe('keyboard', () => {
    const at = { image: { nw: 1600, nh: 900, shown: 400 } };

    it('arrows move the box one screen pixel (4 natural at this scale), Shift ten times that', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        expect(env.key('ArrowRight').defaultPrevented).toBe(true);
        expect(env.box().x).toBe(start.x + 4);
        env.key('ArrowDown');
        expect(env.box().y).toBe(start.y + 4);
        env.key('ArrowLeft', { shiftKey: true });
        expect(env.box().x).toBe(start.x + 4 - 40);
        env.key('ArrowUp', { shiftKey: true });
        expect(env.box().y).toBe(start.y + 4 - 40);
        expect(env.status()).toMatch(/^Crop area 124, 54, 1280 by 720 pixels of 1600 by 900/);
    });

    it('moves by at least one natural pixel when the image is shown larger than it is', async () => {
        const env = await boot(CROP(), { image: { nw: 100, nh: 100, shown: 400 } });
        const start = env.box();
        env.key('ArrowRight');
        expect(env.box().x).toBe(start.x + 1);
    });

    it('Alt with the arrows resizes: right and down grow, left and up shrink', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        env.key('ArrowRight', { altKey: true });
        expect(env.box().w).toBe(start.w + 4);
        expect(env.box().x).toBe(start.x);
        env.key('ArrowLeft', { altKey: true, shiftKey: true });
        expect(env.box().w).toBe(start.w + 4 - 40);
        env.key('ArrowDown', { altKey: true });
        expect(env.box().h).toBe(start.h + 4);
        env.key('ArrowUp', { altKey: true });
        expect(env.box().h).toBe(start.h);
    });

    it('with a locked ratio, Alt with the arrows keeps it', async () => {
        const env = await boot(CROP({ ratio: '16/9' }), at);
        env.key('ArrowRight', { altKey: true, shiftKey: true });
        expect(env.box().w / env.box().h).toBeCloseTo(16 / 9, 6);
        env.key('ArrowUp', { altKey: true });
        expect(env.box().w / env.box().h).toBeCloseTo(16 / 9, 6);
    });

    it('never leaves the image or goes below the minimum', async () => {
        const env = await boot(CROP(), at);
        for (let i = 0; i < 400; i++) env.key('ArrowRight', { shiftKey: true });
        expect(env.box().x + env.box().w).toBe(1600);
        for (let i = 0; i < 400; i++) env.key('ArrowLeft', { altKey: true, shiftKey: true });
        expect(env.box().w).toBe(50);
    });

    it('Enter crops and Escape resets', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        env.key('ArrowRight', { shiftKey: true });
        expect(env.key('Enter').defaultPrevented).toBe(true);
        expect(env.sent).toHaveLength(1);
        expect(env.sent[0].params.x).toBe(start.x + 40);
        expect(env.key('Escape').defaultPrevented).toBe(true);
        expect(env.box()).toEqual(start);
    });

    it('leaves other keys and browser shortcuts alone', async () => {
        const env = await boot(CROP(), at);
        const start = env.box();
        expect(env.key('a').defaultPrevented).toBe(false);
        expect(env.key('Tab').defaultPrevented).toBe(false);
        expect(env.key('ArrowRight', { ctrlKey: true }).defaultPrevented).toBe(false);
        expect(env.key('ArrowRight', { metaKey: true }).defaultPrevented).toBe(false);
        expect(env.box()).toEqual(start);
    });
});

// ---------------------------------------------------------------------------
// Server patches
// ---------------------------------------------------------------------------

describe('server re-renders', () => {
    const at = { image: { nw: 1600, nh: 900, shown: 400 } };

    it('re-derives what a patch reset: the selection\'s style and visibility, and the buttons', async () => {
        const env = await boot(CROP(), at);
        env.drag(env.sel, [800, 450], [900, 500]);
        const moved = env.box();
        env.sel.removeAttribute('style');
        env.sel.hidden = true;
        env.btn('crop').disabled = true;
        env.sel.removeAttribute('dj-update');
        env.window.djust.updateHooks();
        expect(env.sel.hidden).toBe(false);
        expect(env.btn('crop').disabled).toBe(false);
        expect(env.sel.style.width).toBe(moved.w * 0.25 + 'px');
        expect(env.sel.getAttribute('dj-update')).toBe('ignore');
        expect(env.box()).toEqual(moved);
    });

    it('a patch that changes nothing relevant keeps the box (and the box in a drag)', async () => {
        const env = await boot(CROP(), at);
        env.drag(env.sel, [800, 450], [900, 500]);
        const moved = env.box();
        for (let i = 0; i < 3; i++) env.window.djust.updateHooks();
        expect(env.box()).toEqual(moved);
        const [x, y] = env.client(800, 450);
        env.ptr('pointerdown', env.sel, x, y);
        env.ptr('pointermove', env.wrap, x + 4, y);
        env.window.djust.updateHooks();
        env.ptr('pointermove', env.wrap, x + 8, y);
        env.ptr('pointerup', env.wrap, x + 8, y);
        expect(env.box().x).toBeCloseTo(moved.x + 32, 6); // 8 screen px x 4
    });

    it('a new aspect ratio or minimum from the server refits the box', async () => {
        const env = await boot(CROP(), at);
        env.root.setAttribute('data-aspect-ratio', '1/1');
        env.window.djust.updateHooks();
        expect(env.box().w / env.box().h).toBeCloseTo(1, 9);
        env.root.setAttribute('data-aspect-ratio', '');
        env.window.djust.updateHooks();
        expect(env.box().w).toBe(1280);
        env.root.setAttribute('data-min-width', '1500');
        env.window.djust.updateHooks();
        expect(env.box().w).toBeGreaterThanOrEqual(1500);
    });

    it('follows the server disabling it', async () => {
        const env = await boot(CROP(), at);
        env.root.classList.add('dj-image-cropper--disabled');
        env.window.djust.updateHooks();
        expect(env.btn('crop').disabled).toBe(true);
        env.root.classList.remove('dj-image-cropper--disabled');
        env.window.djust.updateHooks();
        expect(env.btn('crop').disabled).toBe(false);
    });

    it('rebinds when a patch replaces the image element', async () => {
        const env = await boot(CROP(), at);
        const fresh = env.window.document.createElement('img');
        fresh.className = 'dj-image-cropper__image';
        fresh.setAttribute('src', '/replaced.jpg');
        setImage(fresh, { nw: 800, nh: 800, shown: 400 });
        env.img.replaceWith(fresh);
        env.window.djust.updateHooks();
        expect(env.box()).toEqual({ x: 80, y: 80, w: 640, h: 640 });
        // the old element's load event no longer reaches the hook
        const before = env.box();
        env.img.dispatchEvent(new env.window.Event('load'));
        expect(env.box()).toEqual(before);
    });
});

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

describe('teardown', () => {
    const at = { image: { nw: 1600, nh: 900, shown: 400 } };

    it('destroyed() removes every listener, disconnects the observer, releases a drag and ignores later input', async () => {
        class FakeRO { constructor(cb) { this.cb = cb; FakeRO.last = this; } observe() {} disconnect() { this.gone = true; } }
        const env = await boot(CROP(), { ...at, resizeObserver: FakeRO });
        const hook = env.hook();
        const winRemoved = [];
        const orig = env.window.removeEventListener.bind(env.window);
        env.window.removeEventListener = (t, f, o) => { winRemoved.push(t); return orig(t, f, o); };
        const wrapRemoved = [];
        const origWrap = env.wrap.removeEventListener.bind(env.wrap);
        env.wrap.removeEventListener = (t, f, o) => { wrapRemoved.push(t); return origWrap(t, f, o); };
        const imgRemoved = [];
        const origImg = env.img.removeEventListener.bind(env.img);
        env.img.removeEventListener = (t, f, o) => { imgRemoved.push(t); return origImg(t, f, o); };
        const [x, y] = env.client(800, 450);
        env.ptr('pointerdown', env.sel, x, y);
        hook.destroyed();
        expect(FakeRO.last.gone).toBe(true);
        expect(winRemoved).toEqual(['resize']);
        expect(wrapRemoved.sort()).toEqual(['lostpointercapture', 'pointercancel', 'pointerdown', 'pointermove', 'pointerup']);
        expect(imgRemoved.sort()).toEqual(['error', 'load']);
        expect(env.released).toEqual([1]);
        env.key('ArrowRight');
        env.btn('crop').click();
        env.load();
        expect(env.sent).toEqual([]);
        hook.destroyed();
    });

    it('binds once however often the page patches', async () => {
        const env = await boot(CROP(), at);
        for (let i = 0; i < 4; i++) env.window.djust.updateHooks();
        const start = env.box();
        env.key('ArrowRight');
        expect(env.box().x).toBe(start.x + 4);
        env.btn('crop').click();
        expect(env.sent).toHaveLength(1);
    });

    it('removing the element through a patch destroys the hook', async () => {
        const env = await boot(CROP(), at);
        const destroyed = vi.spyOn(env.hook(), 'destroyed');
        env.root.remove();
        env.window.djust.updateHooks();
        expect(destroyed).toHaveBeenCalledTimes(1);
    });

    it('two croppers keep their own boxes', async () => {
        const env = await boot(CROP() + CROP().replace('class="dj-image-cropper"', 'class="dj-image-cropper" id="two"'), at);
        const second = env.window.document.querySelectorAll('.dj-image-cropper')[1];
        setImage(second.querySelector('img'), { nw: 800, nh: 800, shown: 400 });
        second.querySelector('img').dispatchEvent(new env.window.Event('load'));
        expect(env.window.djust.getHook(second).geometry).toBeTruthy();
        expect(env.window.djust.getHook(second)._box.w).toBe(640);
        expect(env.box().w).toBe(1280);
    });
});

describe('what the script may not do', () => {
    it('touches no pixels, creates no elements and parses no markup', () => {
        for (const banned of [/\binnerHTML\b/, /\bouterHTML\b/, /insertAdjacentHTML/, /\beval\s*\(/, /new Function/, /document\.write/, /createElement/, /getContext/, /toDataURL|toBlob/, /new Image/, /\bfetch\s*\(/, /XMLHttpRequest/, /FileReader/]) {
            expect(SCRIPT).not.toMatch(banned);
        }
    });
});
