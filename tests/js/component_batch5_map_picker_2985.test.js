/* eslint-disable no-script-url -- the unsafe URLs are the hostile inputs under test */
/**
 * #2985 batch 5 — MapPicker answers its dj-hook on top of the vendored Leaflet
 * (ADR-040), and an app's own MapPicker hook still wins.
 *
 * The markup is what the component renders (python/djust/tests/
 * test_component_batch5_map_picker_2985.py pins it on every render path).
 * Leaflet is the real vendored bundle, run in jsdom (no layout: sizes are 0,
 * which is enough for projection maths, markers, layers and events; real
 * geometry, RTL and tile loading are checked in Chromium by
 * tests/playwright/test_component_batch5_map_picker_2985.py).
 */

import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'fs';

const clientCode = fs.readFileSync('./python/djust/static/djust/client.js', 'utf-8');
const DIR = './python/djust/components/static/djust_components/';
// eslint-disable-next-line security/detect-non-literal-fs-filename -- fixed names
const read = (f) => fs.readFileSync(DIR + f, 'utf-8');
const LEAFLET = read('vendor/leaflet/leaflet.js');
const SCRIPT = read('map-picker.js');

const CFG = {
    js: '/static/leaflet.js',
    jsIntegrity: 'sha384-JS',
    css: '/static/leaflet.css',
    cssIntegrity: 'sha384-CSS',
    crossOrigin: '',
    icon: '/static/marker-icon.abc.png',
    icon2x: '/static/marker-icon-2x.abc.png',
    shadow: '/static/marker-shadow.abc.png',
};

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

const MAP = ({
    lat = 51.5, lng = -0.12, zoom = 13, event = 'pick', tiles = 'https://tiles.example/{z}/{x}/{y}.png',
    attribution = '© Example contributors', attributionUrl = 'https://example.com/copyright',
    maxZoom = 19, label = 'Map picker', cfg = CFG, id = 'm1', extra = '',
} = {}) =>
    `<div id="${id}" class="dj-map-picker" dj-hook="MapPicker" data-lat="${lat}" data-lng="${lng}" data-zoom="${zoom}"` +
    (event === null ? '' : ` data-pick-event="${esc(event)}"`) +
    ` data-tile-url="${esc(tiles)}" data-attribution="${esc(attribution)}" data-attribution-url="${esc(attributionUrl)}"` +
    ` data-max-zoom="${maxZoom}"` + (cfg ? ` data-leaflet="${esc(JSON.stringify(cfg))}"` : '') +
    ` style="height:400px" role="application" aria-label="${esc(label)}"${extra}>` +
    '<div class="dj-map-picker__map"></div></div>';

function createEnv(bodyHtml, { preRegister, leaflet = true, matchMedia, resizeObserver } = {}) {
    const dom = new JSDOM(
        `<!DOCTYPE html><html><head></head><body><div dj-root>${bodyHtml}</div></body></html>`,
        { url: 'http://localhost:8000/test/', runScripts: 'dangerously', pretendToBeVisual: true },
    );
    const { window } = dom;
    const warnings = [];
    window.console = {
        log: () => {}, error: () => {}, debug: () => {}, info: () => {},
        warn: (...args) => warnings.push(args.join(' ')),
    };
    window.IntersectionObserver = class { observe() {} disconnect() {} };
    if (matchMedia) window.matchMedia = matchMedia;
    if (resizeObserver) window.ResizeObserver = resizeObserver;
    try {
        window.eval(clientCode);
    } catch (_e) {
        // client.js may throw on DOM APIs jsdom lacks; hooks still load.
    }
    if (leaflet) window.eval(LEAFLET);
    const sent = [];
    window.djust.handleEvent = (name, params) => { sent.push({ name, params }); };
    if (preRegister) preRegister(window);
    return { window, warnings, sent };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function boot(markup, opts) {
    const env = createEnv(markup, opts);
    env.window.eval(SCRIPT);
    env.window.djust.mountHooks();
    await tick();
    env.$ = (sel) => env.window.document.querySelector(sel);
    env.$$ = (sel) => Array.from(env.window.document.querySelectorAll(sel));
    env.hook = (sel = '[dj-hook="MapPicker"]') => env.window.djust.getHook(env.$(sel));
    env.mapEl = (sel = '[dj-hook="MapPicker"]') => env.$(sel).querySelector('.dj-map-picker__map');
    env.live = (sel) => (env.$(sel || '[dj-hook="MapPicker"]').querySelector('.dj-map-picker__sr[role=status]') || {}).textContent;
    env.click = (lat, lng, sel) => env.hook(sel)._map.fire('click', { latlng: { lat, lng } });
    env.key = (k, extra = {}, target) => {
        const e = new env.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra });
        (target || env.mapEl()).dispatchEvent(e);
        return e;
    };
    env.marker = (sel) => env.hook(sel)._marker.getLatLng();
    env.patch = (fn) => { fn(); env.window.djust.updateHooks(); };
    return env;
}

// ---------------------------------------------------------------------------
// An app's own hook always wins
// ---------------------------------------------------------------------------

describe('the shipped hook never replaces an app hook (#2985 batch 5)', () => {
    const probe = (env) => env.mapEl().hasAttribute('tabindex');

    it('registers a hook, so mounting logs no "No hook registered"', async () => {
        const env = await boot(MAP());
        expect(env.warnings.filter((w) => w.includes('No hook registered'))).toEqual([]);
        expect(probe(env)).toBe(true);
    });

    it('without the script the warning is logged (gate-off)', () => {
        const env = createEnv(MAP());
        env.window.djust.mountHooks();
        expect(env.warnings.some((w) => w.includes('No hook registered for "MapPicker"'))).toBe(true);
    });

    it('an app hook in window.djust.hooks registered first is the one that runs', async () => {
        const mounted = vi.fn();
        const env = await boot(MAP(), { preRegister: (w) => { w.djust.hooks = { MapPicker: { mounted } }; } });
        expect(mounted).toHaveBeenCalledTimes(1);
        expect(probe(env)).toBe(false);
    });

    it('an app hook in window.DjustHooks registered first is the one that runs', async () => {
        const mounted = vi.fn();
        const mine = { mounted };
        const env = await boot(MAP(), { preRegister: (w) => { w.DjustHooks = { MapPicker: mine }; } });
        expect(env.window.DjustHooks.MapPicker).toBe(mine);
        expect(mounted).toHaveBeenCalledTimes(1);
        expect(probe(env)).toBe(false);
    });

    it('an app hook registered AFTER the script still wins, in either registry', async () => {
        for (const registry of ['djust.hooks', 'DjustHooks']) {
            const mounted = vi.fn();
            const env = createEnv(MAP());
            env.window.eval(SCRIPT);
            if (registry === 'djust.hooks') env.window.djust.hooks = { MapPicker: { mounted } };
            else env.window.DjustHooks.MapPicker = { mounted };
            env.window.djust.mountHooks();
            await tick();
            expect(mounted).toHaveBeenCalledTimes(1);
            expect(env.window.document.querySelector('.dj-map-picker__map').hasAttribute('tabindex')).toBe(false);
        }
    });

    it("an app hook's pushEvent/handleEvent API is untouched", async () => {
        const seen = [];
        await boot(MAP(), {
            preRegister: (w) => {
                w.djust.hooks = { MapPicker: { mounted() { seen.push(typeof this.pushEvent, typeof this.handleEvent, this.el.getAttribute('dj-hook')); } } };
            },
        });
        expect(seen).toEqual(['function', 'function', 'MapPicker']);
    });
});

// ---------------------------------------------------------------------------
// Loading the vendored Leaflet
// ---------------------------------------------------------------------------

describe('Leaflet is loaded on demand, once per page, with its integrity values', () => {
    const injected = (env) => ({
        scripts: env.$$('head script[src]'),
        links: env.$$('head link[rel~="stylesheet"]'),
    });
    const finish = async (env, { script = true, css = true } = {}) => {
        const { scripts, links } = injected(env);
        if (script) {
            env.window.eval(LEAFLET);
            scripts.forEach((s) => s.dispatchEvent(new env.window.Event('load')));
        }
        if (css) links.forEach((l) => l.dispatchEvent(new env.window.Event('load')));
        await tick();
    };
    const bootCold = async (markup, opts) => {
        const env = createEnv(markup, { ...opts, leaflet: false });
        env.window.eval(SCRIPT);
        env.window.djust.mountHooks();
        await tick();
        env.$ = (s) => env.window.document.querySelector(s);
        env.$$ = (s) => Array.from(env.window.document.querySelectorAll(s));
        return env;
    };

    it('injects one stylesheet and one script, carrying the integrity values', async () => {
        const env = await bootCold(MAP({ id: 'a' }) + MAP({ id: 'b' }));
        const { scripts, links } = injected(env);
        expect(scripts).toHaveLength(1);
        expect(links).toHaveLength(1);
        expect(scripts[0].getAttribute('src')).toBe(CFG.js);
        expect(scripts[0].getAttribute('integrity')).toBe(CFG.jsIntegrity);
        expect(links[0].getAttribute('href')).toBe(CFG.css);
        expect(links[0].getAttribute('integrity')).toBe(CFG.cssIntegrity);
        expect(scripts[0].hasAttribute('crossorigin')).toBe(false);
        expect(env.$$('.leaflet-pane')).toHaveLength(0);
        await finish(env);
        expect(env.$$('.leaflet-map-pane')).toHaveLength(2);
        expect(injected(env).scripts).toHaveLength(1);
    });

    it('asks for anonymous CORS when the static files are on another origin', async () => {
        const env = await bootCold(MAP({ cfg: { ...CFG, crossOrigin: 'anonymous' } }));
        const { scripts, links } = injected(env);
        expect(scripts[0].getAttribute('crossorigin')).toBe('anonymous');
        expect(links[0].getAttribute('crossorigin')).toBe('anonymous');
    });

    it('does not load anything when the page already has Leaflet', async () => {
        const env = await boot(MAP());
        expect(injected(env).scripts).toHaveLength(0);
        expect(injected(env).links).toHaveLength(0);
        expect(env.$$('.leaflet-map-pane')).toHaveLength(1);
    });

    it('waits for a page tag for the same file instead of running Leaflet twice', async () => {
        const env = createEnv(MAP(), { leaflet: false });
        const tag = env.window.document.createElement('script');
        tag.setAttribute('src', CFG.js);
        env.window.document.head.appendChild(tag);
        env.window.eval(SCRIPT);
        env.window.djust.mountHooks();
        await tick();
        expect(env.window.document.querySelectorAll('script[src]')).toHaveLength(1);
        env.window.eval(LEAFLET);
        tag.dispatchEvent(new env.window.Event('load'));
        env.window.document.head.querySelectorAll('link').forEach((l) => l.dispatchEvent(new env.window.Event('load')));
        await tick();
        expect(env.window.document.querySelectorAll('.leaflet-map-pane')).toHaveLength(1);
    });

    it('says so, and warns once, when Leaflet fails to load (SRI, CSP, 404); a later mount tries again', async () => {
        const env = await bootCold(MAP({ id: 'a' }));
        const failed = injected(env).scripts[0];
        failed.dispatchEvent(new env.window.Event('error'));
        await tick();
        // The failed tags are taken back, or the next attempt would wait on them for ever.
        expect(failed.isConnected).toBe(false);
        expect(injected(env).links).toHaveLength(0);
        const note = env.$('#a .dj-map-picker__notice');
        expect(note.textContent).toBe('The map could not be loaded.');
        expect(env.$('#a .dj-map-picker__sr[role=status]').textContent).toBe('The map could not be loaded.');
        expect(env.warnings.filter((w) => w.includes('Leaflet did not load'))).toHaveLength(1);

        env.window.document.querySelector('[dj-root]').insertAdjacentHTML('beforeend', MAP({ id: 'b' }));
        env.window.djust.updateHooks();
        await tick();
        expect(injected(env).scripts).toHaveLength(1); // a fresh attempt
        expect(injected(env).scripts[0]).not.toBe(failed);
        expect(injected(env).links).toHaveLength(1);
        injected(env).scripts[0].dispatchEvent(new env.window.Event('error'));
        await tick();
        expect(env.warnings.filter((w) => w.includes('Leaflet did not load'))).toHaveLength(1);
        expect(env.$('#b .dj-map-picker__notice').textContent).toBe('The map could not be loaded.');
    });

    it('never fetches from an attribute that is not an http(s) or root-relative URL', async () => {
        for (const bad of ['javascript:alert(1)', 'data:text/javascript,1', '//evil.example/x.js', 'x y']) {
            const env = await bootCold(MAP({ cfg: { ...CFG, js: bad } }));
            expect(injected(env).scripts, bad).toHaveLength(0);
            expect(env.$('.dj-map-picker__notice').textContent).toBe('The map could not be loaded.');
        }
        const env = await bootCold(MAP({ cfg: null }));
        expect(injected(env).scripts).toHaveLength(0);
        const broken = await bootCold(MAP().replace('data-max-zoom', 'data-leaflet="{nope" data-max-zoom'));
        expect(injected(broken).scripts).toHaveLength(0);
    });

    it('a hook destroyed while Leaflet is loading builds nothing when it arrives', async () => {
        const env = await bootCold(MAP());
        const hook = env.window.djust.getHook(env.$('[dj-hook]'));
        hook.destroyed();
        await finish(env);
        expect(env.$$('.leaflet-map-pane')).toHaveLength(0);
        expect(hook._map).toBeNull();
    });
});

// ---------------------------------------------------------------------------
// Picking
// ---------------------------------------------------------------------------

describe('a click or tap picks a location', () => {
    it('sends only {lat, lng} to the configured event, rounded to six decimals', async () => {
        const env = await boot(MAP());
        env.click(48.858370123456, 2.294481987654);
        expect(env.sent).toEqual([{ name: 'pick', params: { lat: 48.85837, lng: 2.294482 } }]);
        expect(env.marker().lat).toBeCloseTo(48.85837, 6);
        expect(env.$('.dj-map-picker__coords').textContent).toBe('48.85837° N, 2.29448° E');
        expect(env.live()).toBe('Location chosen: 48.85837° N, 2.29448° E');
    });

    it('wraps the longitude and clamps the latitude of the world copies and poles', async () => {
        const env = await boot(MAP());
        env.click(95, 190);
        env.click(-120, -540.5);
        env.click(0, 180);
        expect(env.sent.map((s) => s.params)).toEqual([
            { lat: 85.051129, lng: -170 },
            { lat: -85.051129, lng: 179.5 },
            { lat: 0, lng: 180 },
        ]);
    });

    it('never sends a number that is not finite', async () => {
        const env = await boot(MAP());
        for (const [lat, lng] of [[NaN, 1], [1, NaN], [Infinity, 1], [1, -Infinity]]) env.click(lat, lng);
        expect(env.sent).toEqual([]);
    });

    it('moves the marker but sends nothing when the markup names no event', async () => {
        const env = await boot(MAP({ event: null }));
        env.click(10, 10);
        expect(env.sent).toEqual([]);
        expect(env.marker().lat).toBeCloseTo(10, 6);
    });

    it('preserves component and embedded-view notification addresses', async () => {
        const env = await boot('<section data-djust-embedded="child"><div data-component-id="map">' + MAP() + '</div></section>');
        delete env.window.djust._strictBinding;
        env.click(1, 2);
        expect(env.sent[0].params).toMatchObject({ component_id: 'map', view_id: 'child' });
    });

    it('passes the map root to strict binding', async () => {
        const env = await boot(MAP());
        env.window.djust._strictBinding = vi.fn((_el, _event, params, _required, context) => ({ ...params, view_id: context === env.$('#m1') ? 'child' : undefined }));
        env.click(1, 2);
        expect(env.sent[0].params.view_id).toBe('child');
        expect(env.window.djust._strictBinding.mock.calls[0][4]).toBe(env.$('#m1'));
    });

    it('goes through the strict-parameter gate like dj-click, and honours a veto', async () => {
        const env = await boot(MAP());
        env.window.djust._strictBinding = vi.fn(() => false);
        env.click(1, 1);
        expect(env.window.djust._strictBinding).toHaveBeenCalledTimes(1);
        expect(env.sent).toEqual([]);
    });

    it('falls back to the hook pushEvent when the client API is absent', async () => {
        const env = await boot(MAP());
        delete env.window.djust.handleEvent;
        const push = vi.fn();
        env.hook().pushEvent = push;
        env.click(1, 2);
        expect(push).toHaveBeenCalledWith('pick', { lat: 1, lng: 2 });
    });
});

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------

describe('the keyboard moves the marker; Enter chooses', () => {
    it('arrow keys move the marker one step on the right axis and send nothing', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        const start = env.marker();
        const e = env.key('ArrowRight');
        expect(e.defaultPrevented).toBe(true);
        expect(env.marker().lng).toBeGreaterThan(start.lng);
        expect(env.marker().lat).toBeCloseTo(start.lat, 6);
        const east = env.marker().lng;
        env.key('ArrowLeft');
        env.key('ArrowLeft');
        expect(env.marker().lng).toBeLessThan(start.lng);
        env.key('ArrowLeft', {}, env.mapEl());
        const up = env.marker().lat;
        env.key('ArrowUp');
        expect(env.marker().lat).toBeGreaterThan(up);
        env.key('ArrowDown');
        env.key('ArrowDown');
        expect(env.marker().lat).toBeLessThan(up);
        expect(east).toBeGreaterThan(0);
        expect(env.sent).toEqual([]);
    });

    it('Shift moves eight times as far', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        env.key('ArrowRight');
        const one = env.marker().lng;
        env.hook()._marker.setLatLng([0, 0]);
        env.key('ArrowRight', { shiftKey: true });
        expect(env.marker().lng / one).toBeCloseTo(8, 3);
    });

    it('a move is announced with the coordinates and what to do next, and shown as pending', async () => {
        const env = await boot(MAP({ lat: 10, lng: 20, zoom: 10 }));
        env.key('ArrowUp');
        expect(env.live()).toMatch(/^10\.\d{5}° N, 20\.00000° E\. Press Enter to choose this location\.$/);
        expect(env.$('.dj-map-picker__coords').className).toContain('dj-map-picker__coords--pending');
    });

    it('Enter (or Space) sends the marker position once; a held key does not repeat', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        env.key('ArrowRight', { shiftKey: true });
        const at = env.marker();
        env.key('Enter');
        env.key('Enter', { repeat: true });
        env.key(' ', { repeat: true });
        expect(env.sent).toHaveLength(1);
        expect(env.sent[0].params.lng).toBeCloseTo(at.lng, 6);
        expect(env.sent[0].params.lat).toBeCloseTo(at.lat, 6);
        expect(env.$('.dj-map-picker__coords').className).not.toContain('--pending');
        env.key(' ');
        expect(env.sent).toHaveLength(2);
    });

    it('Escape puts the marker back on the last chosen location; with nothing pending it is left alone', async () => {
        const env = await boot(MAP({ lat: 10, lng: 20, zoom: 10 }));
        const idle = env.key('Escape');
        expect(idle.defaultPrevented).toBe(false); // a surrounding dialog still sees it
        env.key('ArrowRight', { shiftKey: true });
        const e = env.key('Escape');
        expect(e.defaultPrevented).toBe(true);
        expect(env.marker().lat).toBeCloseTo(10, 6);
        expect(env.marker().lng).toBeCloseTo(20, 6);
        expect(env.live()).toBe('Back at the chosen location: 10.00000° N, 20.00000° E');
        expect(env.sent).toEqual([]);
    });

    it('+ and - zoom, announce the level, and stop at the limits', async () => {
        const env = await boot(MAP({ zoom: 18, maxZoom: 19 }));
        env.key('+');
        expect(env.hook()._map.getZoom()).toBe(19);
        expect(env.live()).toBe('Zoom level 19');
        env.key('=');
        expect(env.hook()._map.getZoom()).toBe(19);
        expect(env.live().trim()).toBe('Zoom level 19, the limit');
        env.key('-');
        expect(env.hook()._map.getZoom()).toBe(18);
    });

    it('ignores browser shortcuts and keys that belong to the zoom buttons', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        for (const mods of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
            expect(env.key('ArrowRight', mods).defaultPrevented).toBe(false);
        }
        expect(env.key('+', { ctrlKey: true }).defaultPrevented).toBe(false);
        const button = env.$('.leaflet-control-zoom-in');
        expect(env.key('Enter', {}, button).defaultPrevented).toBe(false);
        expect(env.key('ArrowRight', {}, button).defaultPrevented).toBe(false);
        expect(env.key('a').defaultPrevented).toBe(false);
        expect(env.marker().lng).toBe(0);
        expect(env.sent).toEqual([]);
    });

    it('the map is one focusable stop with a name, a role description and instructions', async () => {
        const env = await boot(MAP({ label: 'Pick a depot' }));
        const m = env.mapEl();
        expect(m.getAttribute('tabindex')).toBe('0');
        expect(m.getAttribute('aria-label')).toBe('Pick a depot');
        expect(m.getAttribute('aria-roledescription')).toBe('map');
        const help = env.window.document.getElementById(m.getAttribute('aria-describedby'));
        expect(help.textContent).toMatch(/arrow keys .* Enter .* Escape/);
        expect(env.$$('[tabindex="0"]').filter((n) => n.closest('.dj-map-picker')).map((n) => n.className.split(' ')[0])).toEqual(['dj-map-picker__map']);
    });

    it('two maps keep independent state and distinct help ids', async () => {
        const env = await boot(MAP({ id: 'a', lat: 0, lng: 0 }) + MAP({ id: 'b', lat: 5, lng: 5 }));
        env.key('ArrowRight', {}, env.mapEl('#a'));
        env.key('Enter', {}, env.mapEl('#a'));
        expect(env.sent).toHaveLength(1);
        expect(env.marker('#b').lat).toBeCloseTo(5, 6);
        expect(env.mapEl('#a').getAttribute('aria-describedby')).not.toBe(env.mapEl('#b').getAttribute('aria-describedby'));
    });
});

// ---------------------------------------------------------------------------
// The server stays in charge
// ---------------------------------------------------------------------------

describe('server patches', () => {
    it('a new data-lat / data-lng moves the marker, replacing a keyboard move not yet chosen', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        env.key('ArrowRight');
        env.patch(() => { env.$('#m1').setAttribute('data-lat', '12.5'); env.$('#m1').setAttribute('data-lng', '-7.25'); });
        expect(env.marker().lat).toBeCloseTo(12.5, 6);
        expect(env.marker().lng).toBeCloseTo(-7.25, 6);
        expect(env.live()).toBe('Location set to 12.50000° N, 7.25000° W');
        env.key('Escape'); // nothing pending any more
        expect(env.marker().lat).toBeCloseTo(12.5, 6);
    });

    it('a patch that changes neither position nor zoom leaves the reader alone', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        env.key('ArrowRight', { shiftKey: true });
        const moved = env.marker();
        env.hook()._map.setZoom(12, { animate: false });
        env.patch(() => { env.$('#m1').classList.add('other'); env.$('#m1').setAttribute('data-pick-event', 'pick'); });
        expect(env.marker().lng).toBe(moved.lng);
        expect(env.hook()._map.getZoom()).toBe(12);
        env.key('Enter');
        expect(env.sent).toHaveLength(1);
        expect(env.sent[0].params.lng).toBeCloseTo(moved.lng, 6);
    });

    it("the server's echo of a pick changes nothing, and a keyboard move made since stays", async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        env.click(30, 40);
        env.key('ArrowRight');
        const moved = env.marker();
        env.patch(() => { env.$('#m1').setAttribute('data-lat', '30'); env.$('#m1').setAttribute('data-lng', '40'); });
        expect(env.marker().lng).toBe(moved.lng);
        expect(env.live()).not.toMatch(/^Location set to/);
        env.key('Escape');
        expect(env.marker().lat).toBeCloseTo(30, 6);
        expect(env.marker().lng).toBeCloseTo(40, 6);
    });

    it('a pick the server did not echo is not undone by a later, unrelated patch', async () => {
        const env = await boot(MAP({ lat: 0, lng: 0, zoom: 10 }));
        env.click(30, 40); // the handler ignored it: data-lat / data-lng stay 0, 0
        env.patch(() => env.$('#m1').classList.add('other'));
        expect(env.marker().lat).toBeCloseTo(30, 6);
        expect(env.marker().lng).toBeCloseTo(40, 6);
        env.patch(() => env.$('#m1').setAttribute('data-lng', '1'));
        expect(env.marker().lng).toBeCloseTo(1, 6); // a real change from the server still wins
    });

    it('a new data-zoom zooms; an unchanged one does not undo the reader\'s own zoom', async () => {
        const env = await boot(MAP({ zoom: 10 }));
        env.patch(() => env.$('#m1').setAttribute('data-zoom', '7'));
        expect(env.hook()._map.getZoom()).toBe(7);
        env.key('+');
        env.patch(() => env.$('#m1').setAttribute('data-lat', '1'));
        expect(env.hook()._map.getZoom()).toBe(8);
    });

    it('invalid numbers from the server fall back instead of breaking the map', async () => {
        const env = await boot(MAP({ lat: 'nan', lng: 'inf', zoom: 'z' }));
        expect(env.marker().lat).toBe(0);
        expect(env.marker().lng).toBe(0);
        expect(env.hook()._map.getZoom()).toBe(13);
        env.patch(() => { env.$('#m1').setAttribute('data-lat', '999'); });
        expect(env.marker().lat).toBeCloseTo(85.0511287798, 6);
    });

    it('a full morph that strips the map is rebuilt, not left as a husk', async () => {
        const env = await boot(MAP());
        const before = env.hook()._map;
        env.patch(() => {
            const m = env.mapEl();
            m.innerHTML = '';
            for (const a of Array.from(m.attributes)) if (a.name !== 'class') m.removeAttribute(a.name);
            m.className = 'dj-map-picker__map';
        });
        await tick();
        const after = env.hook()._map;
        expect(after).not.toBe(before);
        expect(env.$$('.leaflet-map-pane')).toHaveLength(1);
        expect(env.$$('.leaflet-control-zoom')).toHaveLength(1);
        expect(env.mapEl().getAttribute('tabindex')).toBe('0');
        env.click(3, 4);
        expect(env.sent).toHaveLength(1);
    });

    it('marks the map element client-owned, before Leaflet has loaded and after it failed', async () => {
        const warm = await boot(MAP());
        expect(warm.mapEl().getAttribute('dj-update')).toBe('ignore');
        const cold = createEnv(MAP(), { leaflet: false });
        cold.window.eval(SCRIPT);
        cold.window.djust.mountHooks();
        expect(cold.window.document.querySelector('.dj-map-picker__map').getAttribute('dj-update')).toBe('ignore');
    });

    it('a morph that only drops the hook attributes gets them back', async () => {
        const env = await boot(MAP());
        env.patch(() => {
            const m = env.mapEl();
            ['tabindex', 'role', 'aria-label', 'aria-describedby', 'aria-roledescription'].forEach((a) => m.removeAttribute(a));
        });
        expect(env.mapEl().getAttribute('tabindex')).toBe('0');
        expect(env.mapEl().getAttribute('aria-label')).toBe('Map picker');
        expect(env.$$('.leaflet-map-pane')).toHaveLength(1);
    });
});

// ---------------------------------------------------------------------------
// Tiles, attribution, icon
// ---------------------------------------------------------------------------

describe('tiles and attribution', () => {
    it('adds a tile layer for a usable template and none for anything else', async () => {
        const env = await boot(MAP());
        expect(env.$$('img.leaflet-tile').length).toBeGreaterThan(0);
        expect(env.$('img.leaflet-tile').src).toMatch(/^https:\/\/tiles\.example\/13\/\d+\/\d+\.png$/);
        for (const bad of ['', 'javascript:alert(1)/{z}/{x}/{y}', 'https://x.example/tile.png', 'https://x.example/{z}/{x}/{y}/{k}', '//x.example/{z}/{x}/{y}']) {
            const none = await boot(MAP({ tiles: bad }));
            expect(none.$$('img.leaflet-tile'), bad).toHaveLength(0);
            expect(none.$$('.leaflet-map-pane')).toHaveLength(1); // the picker still works
            none.click(1, 1);
            expect(none.sent).toHaveLength(1);
        }
    });

    it('shows a notice, announced once, when no tile loads, and drops it when one does', async () => {
        const env = await boot(MAP());
        const tiles = env.$$('img.leaflet-tile');
        tiles.forEach((t) => t.dispatchEvent(new env.window.Event('error')));
        const note = env.$('.dj-map-picker__notice');
        expect(note.hidden).toBe(false);
        expect(note.textContent).toBe('Map tiles could not be loaded. You can still choose a location.');
        expect(env.live()).toBe('Map tiles could not be loaded. You can still choose a location.');
        env.click(1, 2);
        expect(env.sent).toHaveLength(1);
        // A later tile loads (after a zoom): the notice goes away.
        env.hook()._map.setZoom(12, { animate: false });
        env.$$('img.leaflet-tile').filter((t) => !tiles.includes(t)).forEach((t) => t.dispatchEvent(new env.window.Event('load')));
        expect(note.hidden).toBe(true);
    });

    it('does not raise the notice for a failed tile once some tile has loaded', async () => {
        const env = await boot(MAP());
        const map = env.hook()._map;
        const first = env.$$('img.leaflet-tile');
        map.setZoom(12, { animate: false });
        env.$$('img.leaflet-tile').filter((t) => !first.includes(t)).forEach((t) => t.dispatchEvent(new env.window.Event('load')));
        const seen = env.$$('img.leaflet-tile');
        map.setZoom(11, { animate: false });
        env.$$('img.leaflet-tile').filter((t) => !seen.includes(t) && !first.includes(t)).forEach((t) => t.dispatchEvent(new env.window.Event('error')));
        const note = env.$('.dj-map-picker__notice');
        expect(!note || note.hidden).toBe(true);
    });

    it('builds the attribution from text: no markup in any attribute is parsed', async () => {
        const hostile = '<img src=x onerror=window.__pwn=1><b>x</b>';
        const env = await boot(MAP({ attribution: hostile, attributionUrl: 'javascript:window.__pwn=1', label: hostile }));
        const box = env.$('.dj-map-picker__attribution');
        expect(box.textContent).toBe('Leaflet | ' + hostile);
        expect(box.querySelectorAll('img,b')).toHaveLength(0);
        expect(Array.from(box.querySelectorAll('a')).map((a) => a.getAttribute('href'))).toEqual(['https://leafletjs.com']);
        expect(env.window.__pwn).toBeUndefined();
        expect(env.$$('.dj-map-picker img[onerror], .dj-map-picker b')).toHaveLength(0);
    });

    it('links the attribution only for an http(s) URL, opening safely', async () => {
        const env = await boot(MAP());
        const links = Array.from(env.$$('.dj-map-picker__attribution a'));
        expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://leafletjs.com', 'https://example.com/copyright']);
        for (const a of links) {
            expect(a.getAttribute('rel')).toBe('noopener noreferrer');
            expect(a.getAttribute('target')).toBe('_blank');
        }
        const plain = await boot(MAP({ attributionUrl: '' }));
        expect(plain.$$('.dj-map-picker__attribution a')).toHaveLength(1);
        const none = await boot(MAP({ attribution: '' }));
        expect(none.$('.dj-map-picker__attribution').textContent).toBe('Leaflet');
    });

    it("uses the explicit icon URLs and never runs Leaflet's own path detection", async () => {
        const probe = vi.fn();
        const env = createEnv(MAP());
        const proto = env.window.L.Icon.Default.prototype;
        const original = proto._detectIconPath;
        proto._detectIconPath = function () { probe(); return original.call(this); };
        env.window.eval(SCRIPT);
        env.window.djust.mountHooks();
        await tick();
        const img = env.window.document.querySelector('img.leaflet-marker-icon');
        expect(img.getAttribute('src')).toBe(CFG.icon);
        expect(env.window.document.querySelector('img.leaflet-marker-shadow').getAttribute('src')).toBe(CFG.shadow);
        expect(img.getAttribute('alt')).toBe('');
        expect(probe).not.toHaveBeenCalled();
        expect(env.window.L.Icon.Default.imagePath).toBeUndefined();
    });

    it('draws a CSS pin when no icon URLs are available', async () => {
        const env = await boot(MAP({ cfg: { ...CFG, icon: '', shadow: '' } }));
        expect(env.$('.leaflet-marker-icon').className).toContain('dj-map-picker__pin');
        expect(env.$$('img.leaflet-marker-icon')).toHaveLength(0);
        const bad = await boot(MAP({ cfg: { ...CFG, icon: 'javascript:1', shadow: CFG.shadow } }));
        expect(bad.$('.leaflet-marker-icon').className).toContain('dj-map-picker__pin');
    });
});

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

describe('resize, motion, wheel and teardown', () => {
    class FakeRO {
        constructor(cb) { FakeRO.all.push(this); this.cb = cb; this.observed = []; this.disconnected = false; }
        observe(el) { this.observed.push(el); }
        disconnect() { this.disconnected = true; }
    }
    FakeRO.all = [];

    it('follows the container size, at most once per frame', async () => {
        FakeRO.all = [];
        const env = await boot(MAP(), { resizeObserver: FakeRO });
        const ro = FakeRO.all[0];
        expect(ro.observed).toEqual([env.mapEl()]);
        const spy = vi.spyOn(env.hook()._map, 'invalidateSize');
        for (let i = 0; i < 5; i++) ro.cb();
        expect(spy).not.toHaveBeenCalled();
        await new Promise((r) => env.window.requestAnimationFrame(() => r()));
        expect(spy).toHaveBeenCalledTimes(1);
        ro.cb();
        await new Promise((r) => env.window.requestAnimationFrame(() => r()));
        expect(spy).toHaveBeenCalledTimes(2);
    });

    it('honours prefers-reduced-motion', async () => {
        const calm = await boot(MAP(), { matchMedia: () => ({ matches: true }) });
        expect(calm.hook()._map.options.zoomAnimation).toBe(false);
        expect(calm.hook()._map.options.fadeAnimation).toBe(false);
        const normal = await boot(MAP(), { matchMedia: () => ({ matches: false }) });
        expect(normal.hook()._map.options.zoomAnimation).toBe(true);
    });

    it('zooms with the wheel only while the map has focus, so the page still scrolls', async () => {
        const env = await boot(MAP());
        const wheel = env.hook()._map.scrollWheelZoom;
        expect(wheel.enabled()).toBe(false);
        env.mapEl().dispatchEvent(new env.window.Event('focus'));
        expect(wheel.enabled()).toBe(true);
        env.mapEl().dispatchEvent(new env.window.Event('blur'));
        expect(wheel.enabled()).toBe(false);
    });

    it('destroyed() removes the map, its controls, its listeners and its observer', async () => {
        FakeRO.all = [];
        const env = await boot(MAP(), { resizeObserver: FakeRO });
        const hook = env.hook();
        const mapEl = env.mapEl();
        const map = hook._map;
        const remove = vi.spyOn(map, 'remove');
        const ours = [['keydown', hook._onKey], ['focus', hook._onFocus], ['blur', hook._onBlur]];
        const removed = [];
        const original = mapEl.removeEventListener.bind(mapEl);
        mapEl.removeEventListener = (type, fn, o) => { removed.push([type, fn]); return original(type, fn, o); };
        hook.destroyed();
        expect(remove).toHaveBeenCalledTimes(1);
        for (const [type, fn] of ours) expect(removed.some(([t, f]) => t === type && f === fn), type).toBe(true);
        expect(FakeRO.all[0].disconnected).toBe(true);
        expect(mapEl.querySelectorAll('.leaflet-pane, .leaflet-control-container, .dj-map-picker__sr, .dj-map-picker__notice')).toHaveLength(0);
        expect(mapEl._leaflet_id).toBeUndefined();
        expect(hook._map).toBeNull();
        // dead: keys and a late click do nothing
        env.key('ArrowRight');
        map.fire('click', { latlng: env.window.L.latLng(1, 1) });
        expect(env.sent).toEqual([]);
        hook.destroyed(); // twice is harmless
    });

    it('destroyed() cancels a pending resize frame', async () => {
        FakeRO.all = [];
        const env = await boot(MAP(), { resizeObserver: FakeRO });
        const hook = env.hook();
        const spy = vi.spyOn(hook._map, 'invalidateSize');
        const cancel = vi.spyOn(env.window, 'cancelAnimationFrame');
        FakeRO.all[0].cb();
        const frame = hook._raf;
        expect(frame).toBeTruthy();
        hook.destroyed();
        expect(cancel).toHaveBeenCalledWith(frame);
        await new Promise((r) => env.window.requestAnimationFrame(() => r()));
        expect(spy).not.toHaveBeenCalled();
    });

    it('removing the element through a patch destroys the hook; mounting the markup again binds exactly once', async () => {
        const env = await boot(MAP({ id: 'a' }));
        const root = env.$('[dj-root]');
        const old = env.hook('#a');
        const destroyed = vi.spyOn(old, 'destroyed');
        env.patch(() => env.$('#a').remove());
        expect(destroyed).toHaveBeenCalledTimes(1);
        root.insertAdjacentHTML('beforeend', MAP({ id: 'b', lat: 1, lng: 1 }));
        env.window.djust.updateHooks();
        await tick();
        env.click(2, 2, '#b');
        expect(env.sent).toHaveLength(1);
        env.key('ArrowRight', {}, env.mapEl('#b'));
        env.key('Enter', {}, env.mapEl('#b'));
        expect(env.sent).toHaveLength(2);
    });

    it('a remount over a container that still holds a Leaflet map does not throw or double-bind', async () => {
        const env = await boot(MAP());
        const first = env.hook();
        // Simulates a hook instance replaced without destroyed() running.
        const second = Object.create(Object.getPrototypeOf(first));
        second.el = env.$('#m1');
        second.pushEvent = first.pushEvent;
        second.mounted();
        await tick();
        expect(second._map).toBeTruthy();
        expect(second._map).not.toBe(first._map);
        expect(env.$$('.leaflet-map-pane').length).toBeGreaterThanOrEqual(1);
        second._map.fire('click', { latlng: env.window.L.latLng(4, 4) });
        expect(env.sent).toHaveLength(1);
        second.destroyed();
    });
});

describe('what the script may not do', () => {
    it('contains no markup parsing or dynamic code', () => {
        for (const banned of [/\binnerHTML\b/, /\bouterHTML\b/, /insertAdjacentHTML/, /\beval\s*\(/, /new Function/, /document\.write/]) {
            expect(SCRIPT).not.toMatch(banned);
        }
    });
});
