/**
 * #4007 — the core bundle loads optional client chunks only when the page
 * uses them (src/13c-chunk-loader.js, scripts/build-client.sh CHUNK_SPECS).
 *
 * These tests load the real built files through a JSDOM resource loader, so
 * the client script has a src, a nonce and (optionally) data-djust-chunks,
 * exactly as the LiveView injects it, and every chunk the core asks for is a
 * real network request the test can see.
 *
 * The behaviour of each chunked module is covered by its own tests, which
 * also run against core + chunks in the `split-build` vitest project
 * (tests/js/_split_build_setup.js).
 */

import { describe, it, expect } from 'vitest';
import { JSDOM, requestInterceptor } from 'jsdom';
import { Linter } from 'eslint';
import fs from 'fs';
import path from 'path';

const STATIC = path.resolve('python/djust/static/djust');
const CHUNKS = ['uploads', 'virtual-list', 'cursor-overlay', 'tutorial', 'service-worker'];
const NONCE = 'n0nce-4007';

// Serves python/djust/static/djust/** for /static/djust/** (and /hashed/**,
// standing in for ManifestStaticFilesStorage names); records every request.
function staticLoader() {
    const requests = [];
    const interceptor = requestInterceptor((request) => {
        const u = new URL(request.url);
        requests.push(u.pathname + u.search);
        const rel = u.pathname.replace(/^\/static\/djust\//, '').replace(/^\/hashed\//, '');
        const file = path.join(STATIC, rel.replace(/\.abc123\./, '.'));
        if (!file.startsWith(STATIC) || !fs.existsSync(file)) {
            return new Response('not found', { status: 404 });
        }
        return new Response(fs.readFileSync(file, 'utf-8'), {
            headers: { 'Content-Type': 'application/javascript' },
        });
    });
    return { requests, resources: { interceptors: [interceptor] } };
}

function page(body, { bundle = 'client-core.js', chunkMap = null, extraHead = '', swController = false } = {}) {
    const loader = staticLoader();
    const mapAttr = chunkMap
        ? ` data-djust-chunks="${JSON.stringify(chunkMap).replace(/"/g, '&quot;')}"`
        : '';
    const dom = new JSDOM(`<!DOCTYPE html><html><head>
<script nonce="${NONCE}">window.DJUST_USE_WEBSOCKET = false;</script>${extraHead}
</head><body>${body}
<script src="/static/djust/${bundle}" nonce="${NONCE}"${mapAttr}></script>
</body></html>`, {
        url: 'http://localhost/page/',
        runScripts: 'dangerously',
        resources: loader.resources,
        pretendToBeVisual: true,
        beforeParse(window) {
            window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
            if (swController) {
                Object.defineProperty(window.navigator, 'serviceWorker', {
                    value: { controller: { postMessage() {} }, addEventListener() {} },
                });
            }
        },
    });
    return { dom, window: dom.window, loader };
}

async function until(predicate, what) {
    for (let i = 0; i < 200; i++) {
        if (predicate()) return;
        await new Promise((r) => setTimeout(r, 10));
    }
    throw new Error('timed out waiting for ' + what);
}

const chunkRequests = (loader) => loader.requests.filter((p) => p.includes('/chunks/'));

describe('core bundle: chunks load only when used (#4007)', () => {
    it('a page using no chunked feature loads no chunk', async () => {
        const { window, loader } = page('<div dj-view="app.V"><button dj-click="go">x</button></div>');
        await until(() => window.djustInitialized, 'init');
        expect(window.djust._clientBundle).toBe('core');
        expect(chunkRequests(loader)).toEqual([]);
        expect(window.document.querySelectorAll('script[data-djust-chunk]').length).toBe(0);
        // The lazy stubs stand in for the absent modules.
        expect(window.djust.uploads._djustStub).toBe(true);
        expect(typeof window.djust.initVirtualLists).toBe('undefined');
    });

    it('dj-virtual loads the virtual-list chunk, with the nonce, before init', async () => {
        const items = Array.from({ length: 50 }, (_, i) => `<div data-key="${i}">row ${i}</div>`).join('');
        const { window, loader } = page(
            `<div dj-view="app.V"><div id="vl" dj-virtual="items" dj-virtual-item-height="20" style="height:100px;overflow:auto">${items}</div></div>`
        );
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/virtual-list.js']);
        const tag = window.document.querySelector('script[data-djust-chunk="virtual-list"]');
        expect(tag.getAttribute('nonce')).toBe(NONCE);
        // Init ran after the chunk: the list was virtualized by djustInit.
        expect(typeof window.djust.initVirtualLists).toBe('function');
        expect(window.document.querySelectorAll('#vl [data-key]').length).toBeLessThan(50);
    });

    it('the minified core asks for minified chunks', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div dj-hook="CursorOverlay"><textarea></textarea></div></div>', {
            bundle: 'client-core.min.js',
        });
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/cursor-overlay.min.js']);
        expect(typeof window.djust.hooks.CursorOverlay.mounted).toBe('function');
    });

    it('data-djust-chunks URLs (static(), hashed) win over the relative path', async () => {
        const { window, loader } = page('<div id="dj-tutorial-bubble"></div><div dj-view="app.V"></div>', {
            chunkMap: { tutorial: '/hashed/chunks/tutorial.abc123.js' },
        });
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/hashed/chunks/tutorial.abc123.js']);
        expect(typeof window.djust._tutorialBubble.handleNarrate).toBe('function');
    });

    it('a mount declaring uploads loads the chunk and replays the configs', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>');
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual([]);
        window.djust.uploads.setConfigs({ avatar: { max_file_size: 10 } });
        await until(() => !window.djust.uploads._djustStub, 'uploads chunk');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/uploads.js']);
        expect(typeof window.djust.uploads.handleProgress).toBe('function');
        expect(typeof window.djust.uploads.activeUploads).toBe('object');
    });

    it('dj-upload loads the uploads chunk and binds the input at init', async () => {
        const { window, loader } = page('<div dj-view="app.V"><input type="file" dj-upload="avatar"></div>');
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/uploads.js']);
        expect(window.djust.uploads._djustStub).toBeUndefined();
    });

    it('djust.registerServiceWorker() loads the service-worker chunk then forwards', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>');
        await until(() => window.djustInitialized, 'init');
        const stub = window.djust.registerServiceWorker;
        const result = await stub({ instantShell: true });
        // JSDOM has no navigator.serviceWorker: the real function resolves null.
        expect(result).toBe(null);
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/service-worker.js']);
        expect(window.djust.registerServiceWorker).not.toBe(stub);
        expect(typeof window.djust._sw.lookupVdom).toBe('function');
    });

    it('a page a service worker controls loads the service-worker chunk at init', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>', { swController: true });
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/service-worker.js']);
        expect(window.djust._sw._djustStub).toBeUndefined();
        expect(typeof window.djust._sw.syncIdentity).toBe('function');
    });

    it('a TurboNav page swap loads the new page\'s chunks before reinit', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>');
        await until(() => window.djustInitialized, 'init');
        window.document.body.innerHTML =
            '<div dj-view="app.W"><div dj-virtual="x" dj-virtual-item-height="20"><div data-key="1">a</div></div></div>';
        window.dispatchEvent(new window.Event('turbo:load'));
        await until(() => chunkRequests(loader).length === 1 && typeof window.djust.initVirtualLists === 'function',
            'virtual-list chunk');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/virtual-list.js']);
    });

    it('a chunk loads once however many callers ask', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>');
        await until(() => window.djustInitialized, 'init');
        await Promise.all([
            window.djust.loadClientChunk('tutorial'),
            window.djust.loadClientChunk('tutorial'),
        ]);
        await window.djust.loadClientChunk('tutorial');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/tutorial.js']);
        await expect(window.djust.loadClientChunk('nope')).rejects.toThrow(/unknown client chunk/);
    });

    it('a chunk that fails to load is reported and init still runs', async () => {
        const { window } = page('<div dj-view="app.V"><div dj-virtual="x"></div></div>', {
            chunkMap: { 'virtual-list': '/static/djust/chunks/missing.js' },
        });
        const errors = [];
        window.console.error = (...a) => errors.push(a.join(' '));
        await until(() => window.djustInitialized, 'init');
        expect(errors.some((e) => e.includes('failed to load client chunk virtual-list'))).toBe(true);
    });
});

describe('full bundle (#4007)', () => {
    it('client.js holds every module and loads no chunk', async () => {
        const items = '<div data-key="1">a</div>';
        const { window, loader } = page(
            `<div dj-view="app.V"><div dj-virtual="x" dj-virtual-item-height="20">${items}</div><input type="file" dj-upload="a"></div>`,
            { bundle: 'client.js' }
        );
        await until(() => window.djustInitialized, 'init');
        expect(window.djust._clientBundle).toBe('full');
        expect(chunkRequests(loader)).toEqual([]);
        expect(window.djust.uploads._djustStub).toBeUndefined();
        expect(typeof window.djust.initVirtualLists).toBe('function');
    });

    it('a chunk evaluated beside the full bundle does nothing', async () => {
        const { window } = page('<div dj-view="app.V"></div>', { bundle: 'client.js' });
        await until(() => window.djustInitialized, 'init');
        const before = window.djust._tutorialBubble;
        window.eval(fs.readFileSync(path.join(STATIC, 'chunks/tutorial.js'), 'utf-8'));
        expect(window.djust._tutorialBubble).toBe(before);
    });
});

describe('built chunks (#4007)', () => {
    // Every name a chunk reads must be a browser global or come through
    // window.djust._chunkScope: a chunk runs outside the core IIFE, so a core
    // binding it names directly would be a ReferenceError at run time. This
    // is what catches a module edit that starts using a new core helper
    // without adding it to CHUNK_SPECS in build-client.sh.
    const { window } = new JSDOM('', { url: 'http://localhost/' });
    const browserGlobals = {};
    for (let o = window; o; o = Object.getPrototypeOf(o)) {
        for (const k of Object.getOwnPropertyNames(o)) browserGlobals[k] = 'readonly';
    }
    for (const k of Object.getOwnPropertyNames(globalThis)) browserGlobals[k] = 'readonly';
    // Browser APIs JSDOM does not implement.
    for (const k of ['indexedDB', 'IDBKeyRange', 'caches']) browserGlobals[k] = 'readonly';

    const core = fs.readFileSync(path.join(STATIC, 'client-core.js'), 'utf-8');
    const scopeBlock = core.slice(core.indexOf('window.djust._chunkScope = Object.freeze({'));
    const scopeNames = new Set(
        [...scopeBlock.slice(0, scopeBlock.indexOf('});')).matchAll(/^\s+(?:get\s+)?(\w+)\s*[:(]/gm)].map((m) => m[1])
    );

    for (const name of CHUNKS) {
        for (const variant of [`${name}.js`, `${name}.min.js`]) {
            it(`chunks/${variant} has no free core identifiers`, () => {
                const code = fs.readFileSync(path.join(STATIC, 'chunks', variant), 'utf-8');
                const messages = new Linter().verify(code, {
                    languageOptions: { ecmaVersion: 2022, sourceType: 'script', globals: browserGlobals },
                    rules: { 'no-undef': 'error' },
                });
                expect(messages.filter((m) => m.ruleId === 'no-undef' || m.fatal)
                    .map((m) => m.line + ': ' + m.message)).toEqual([]);
                for (const m of code.matchAll(/__djScope\.(\w+)/g)) {
                    expect(scopeNames.has(m[1]), `${m[1]} missing from _chunkScope`).toBe(true);
                }
            });
        }
    }

    it('the core bundle omits the chunked modules and the full bundle keeps them', () => {
        const full = fs.readFileSync(path.join(STATIC, 'client.js'), 'utf-8');
        for (const marker of ['setConfigs: setUploadConfigs', 'window.djust.initVirtualLists = initVirtualLists',
            'window.djust.hooks.CursorOverlay =', 'window.djust._tutorialBubble =',
            'globalThis.djust._sw = {']) {
            expect(full.includes(marker), marker).toBe(true);
            expect(core.includes(marker), marker).toBe(false);
        }
    });

    it('client-sizes.json reports core, full and every chunk', () => {
        const m = JSON.parse(fs.readFileSync(path.join(STATIC, 'client-sizes.json'), 'utf-8'));
        expect(m.core.artifact).toBe('client-core.min.js.gz');
        expect(m.shipped.artifact).toBe('client.min.js.gz');
        expect(Object.keys(m.chunks).sort()).toEqual([...CHUNKS].sort());
        expect(m.core.kb).toBeLessThan(m.shipped.kb);
    });
});
