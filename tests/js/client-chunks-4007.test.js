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
const CHUNKS = ['uploads', 'virtual-list', 'cursor-overlay', 'tutorial', 'service-worker',
    // #4320
    'component-behaviors', 'keyboard-nav', 'error-overlay', 'dj-style', 'flip', 'transitions',
    'infinite-scroll', 'prefetch', 'track-static', 'dialog'];
const NONCE = 'n0nce-4007';

// Serves python/djust/static/djust/** for /static/djust/** (and /hashed/**,
// standing in for ManifestStaticFilesStorage names); records every request.
// `hold` names chunks whose responses wait until `release(name)`; a held
// chunk never released stalls like a request that gets no answer.
function staticLoader(hold = []) {
    const requests = [];
    const gates = {};
    for (const name of hold) {
        let open;
        gates[name] = { promise: new Promise((r) => { open = r; }), open: () => open() };
    }
    const interceptor = requestInterceptor(async (request) => {
        const u = new URL(request.url);
        requests.push(u.pathname + u.search);
        const held = Object.keys(gates).find((n) => u.pathname.includes('/chunks/' + n + '.'));
        if (held) await gates[held].promise;
        const rel = u.pathname.replace(/^\/static\/djust\//, '').replace(/^\/hashed\//, '');
        const file = path.join(STATIC, rel.replace(/\.abc123\./, '.'));
        if (!file.startsWith(STATIC) || !fs.existsSync(file)) {
            return new Response('not found', { status: 404 });
        }
        return new Response(fs.readFileSync(file, 'utf-8'), {
            headers: { 'Content-Type': 'application/javascript' },
        });
    });
    return {
        requests,
        resources: { interceptors: [interceptor] },
        release: (name) => gates[name].open(),
    };
}

function page(body, { bundle = 'client-core.js', chunkMap = null, extraHead = '', swController = false, hold = [] } = {}) {
    const loader = staticLoader(hold);
    const mapAttr = chunkMap
        ? ` data-djust-chunks="${JSON.stringify(chunkMap).replace(/"/g, '&quot;')}"`
        : '';
    const dom = new JSDOM(`<!DOCTYPE html><html><head>
<script nonce="${NONCE}">window.DJUST_USE_WEBSOCKET = false; window.djust = { _chunkLoadTimeoutMs: 300 };</script>${extraHead}
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
        // #4320: the hover prefetch layer posts to the worker, so it comes too.
        expect(chunkRequests(loader).sort()).toEqual(['/static/djust/chunks/prefetch.js',
            '/static/djust/chunks/service-worker.js']);
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
            chunkMap: { 'virtual-list': '/nowhere/chunks/missing.js' },
        });
        const errors = [];
        window.console.error = (...a) => errors.push(a.join(' '));
        await until(() => window.djustInitialized, 'init');
        expect(errors.some((e) => e.includes('failed to load client chunk virtual-list'))).toBe(true);
    });
});

describe('core bundle: features a patch renders after init (#4007)', () => {
    // reinitAfterDOMUpdate() is what every patch, html_update and
    // live_redirect mount runs over the updated subtree.
    it('a dj-virtual list a patch renders loads its chunk and is virtualized', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div id="tab"></div></div>');
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual([]);
        const rows = Array.from({ length: 50 }, (_, i) => `<div data-key="${i}">row ${i}</div>`).join('');
        const tab = window.document.getElementById('tab');
        tab.innerHTML = `<div id="vl" dj-virtual="items" dj-virtual-item-height="20" style="height:100px;overflow:auto">${rows}</div>`;
        window.djust.reinitAfterDOMUpdate(tab);
        await until(() => window.document.querySelectorAll('#vl [data-key]').length < 50, 'virtualized list');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/virtual-list.js']);
    });

    it('a CursorOverlay hook a patch renders loads its chunk and is mounted', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div id="tab"></div></div>');
        await until(() => window.djustInitialized, 'init');
        const tab = window.document.getElementById('tab');
        tab.innerHTML = '<div id="co" dj-hook="CursorOverlay"><textarea></textarea></div>';
        window.djust.reinitAfterDOMUpdate(tab);
        // mounted() adds the overlay and the measuring mirror to the hook element.
        await until(() => window.document.getElementById('co').children.length === 3, 'mounted hook');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/cursor-overlay.js']);
    });

    it('a chunk URL from another origin is refused, not loaded', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div id="tab"></div></div>', {
            chunkMap: { 'virtual-list': 'https://elsewhere.example/chunks/virtual-list.js' },
        });
        const errors = [];
        window.console.error = (...a) => errors.push(a.join(' '));
        await until(() => window.djustInitialized, 'init');
        const tab = window.document.getElementById('tab');
        tab.innerHTML = '<div dj-virtual="x"></div>';
        window.djust.reinitAfterDOMUpdate(tab);
        await until(() => errors.length === 1, 'failure report');
        expect(chunkRequests(loader)).toEqual([]);
    });

    it('a chunk that failed is not requested again on every patch', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div id="tab"></div></div>', {
            chunkMap: { 'virtual-list': '/nowhere/chunks/missing.js' },
        });
        const errors = [];
        window.console.error = (...a) => errors.push(a.join(' '));
        await until(() => window.djustInitialized, 'init');
        const tab = window.document.getElementById('tab');
        tab.innerHTML = '<div dj-virtual="x"></div>';
        window.djust.reinitAfterDOMUpdate(tab);
        await until(() => errors.length === 1, 'failure report');
        window.djust.reinitAfterDOMUpdate(tab);
        window.djust.reinitAfterDOMUpdate(tab);
        await new Promise((r) => setTimeout(r, 50));
        expect(chunkRequests(loader)).toEqual(['/nowhere/chunks/missing.js']);
        expect(errors.length).toBe(1);
    });
});

describe('core bundle: stalled and slow chunks (#4007)', () => {
    it('a chunk request that never answers times out and init proceeds', async () => {
        const { window } = page('<div dj-view="app.V"><div dj-virtual="x"></div></div>', { hold: ['virtual-list'] });
        const errors = [];
        window.console.error = (...a) => errors.push(a.join(' '));
        await until(() => window.djustInitialized, 'init after timeout');
        expect(errors.some((e) => e.includes('did not load within 300 ms'))).toBe(true);
    });

    it('an upload push event that arrives while the uploads chunk loads reaches it', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>', { hold: ['uploads'] });
        await until(() => window.djustInitialized, 'init');
        const seen = [];
        window.addEventListener('djust:push_event', (e) => seen.push(e.detail.event));
        window.djust.uploads.setConfigs({ avatar: {} });
        await until(() => chunkRequests(loader).length === 1, 'uploads request');
        const ws = window.djust.liveViewInstance;
        await ws.handleMessage({ type: 'render', effects: [{ kind: 'push_event', event: 'djust:upload_progress', payload: { ref: 'r1' } }] });
        await ws.handleMessage({ type: 'render', effects: [{ kind: 'push_event', event: 'other', payload: {} }] });
        // Held, not lost: the unrelated event went through at once.
        expect(seen).toEqual(['other']);
        loader.release('uploads');
        await until(() => !window.djust.uploads._djustStub && seen.length === 2, 'uploads chunk');
        expect(seen).toEqual(['other', 'djust:upload_progress']);
    });
});

describe('core bundle: the slice-2 chunks (#4320)', () => {
    // Markup that needs each chunk, and what the loaded module provides.
    const CASES = [
        ['component-behaviors', '<div class="combobox"><input class="combobox-input"></div>',
            (w) => w.document._djustComponentBehaviors === true],
        ['keyboard-nav', '<div role="tablist"><button role="tab">a</button></div>',
            (w) => typeof w.djust.keyboardNav._handleKeydown === 'function'],
        ['flip', '<ul dj-flip><li>a</li></ul>', (w) => typeof w.djust.flip._install === 'function'],
        ['transitions', '<div dj-transition="a b c">x</div>',
            (w) => typeof w.djust.maybeDeferRemoval === 'function' && !!w.djust.djTransitionGroup],
        ['transitions', '<div dj-remove="fade">x</div>', (w) => typeof w.djust.maybeDeferRemoval === 'function'],
        ['infinite-scroll', '<ul dj-viewport-bottom="more"><li>a</li></ul>',
            (w) => w.djust._infiniteScrollTracked() === 1],
        ['prefetch', '<a href="/next/" dj-prefetch>next</a>', (w) => !!w.djust._intentPrefetch],
        ['dialog', '<dialog dj-dialog="open">hi</dialog>',
            (w) => w.document.querySelector('dialog').hasAttribute('open')],
    ];

    for (const [name, markup, ready] of CASES) {
        it(`${markup} loads the ${name} chunk before init`, async () => {
            const { window, loader } = page(`<div dj-view="app.V">${markup}</div>`);
            await until(() => window.djustInitialized, 'init');
            expect(chunkRequests(loader)).toEqual([`/static/djust/chunks/${name}.js`]);
            expect(window.document.querySelector(`script[data-djust-chunk="${name}"]`).getAttribute('nonce'))
                .toBe(NONCE);
            expect(ready(window)).toBe(true);
        });
    }

    it('dj-track-static in the head loads track-static, which snapshots the page-load assets', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>', {
            extraHead: '<link dj-track-static rel="stylesheet" href="/static/app.css">',
        });
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/track-static.js']);
        expect(window.djust.djTrackStatic._trackedUrls()).toEqual(['/static/app.css']);
    });

    it('a page that uses several chunks requests them all at once, before init', async () => {
        const { window, loader } = page(
            '<div dj-view="app.V"><dialog dj-dialog="close"></dialog><ul dj-flip></ul><div class="kanban"></div></div>',
            { hold: ['dialog', 'flip', 'component-behaviors'] });
        await until(() => chunkRequests(loader).length === 3, 'three requests');
        expect(window.djustInitialized).toBeFalsy();
        loader.release('dialog');
        loader.release('flip');
        loader.release('component-behaviors');
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader).sort()).toEqual(['/static/djust/chunks/component-behaviors.js',
            '/static/djust/chunks/dialog.js', '/static/djust/chunks/flip.js']);
    });

    it('DEBUG loads the error overlay; without DEBUG it is never requested', async () => {
        const dbg = page('<div dj-view="app.V"></div>', {
            extraHead: '<script nonce="' + NONCE + '">window.DEBUG_MODE = true;</script>',
        });
        await until(() => dbg.window.djustInitialized, 'init');
        expect(chunkRequests(dbg.loader)).toEqual(['/static/djust/chunks/error-overlay.js']);
        dbg.window.dispatchEvent(new dbg.window.CustomEvent('djust:error', { detail: { error: 'boom' } }));
        expect(dbg.window.document.getElementById('djust-error-overlay').textContent).toContain('boom');

        const prod = page('<div dj-view="app.V"></div>');
        await until(() => prod.window.djustInitialized, 'init');
        expect(chunkRequests(prod.loader)).toEqual([]);
        expect(prod.window.djustErrorOverlay).toBeUndefined();
    });

    it('the deferred-save status stays in core and works with no chunk', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>');
        await until(() => window.djustInitialized, 'init');
        window.dispatchEvent(new window.CustomEvent('djust:error', {
            detail: { error: 'still saving', code: 'state_error', transient: true, view: 'app.V' },
        }));
        expect(window.document.getElementById('djust-save-status').textContent).toBe('still saving');
        window.dispatchEvent(new window.CustomEvent('djust:rendered', { detail: { view: 'app.V' } }));
        expect(window.document.getElementById('djust-save-status')).toBeNull();
        expect(chunkRequests(loader)).toEqual([]);
    });

    it('the strict-styles meta loads dj-style, which applies the values before init', async () => {
        const { window, loader } = page(
            '<div dj-view="app.V"><div id="col" data-dj-style data-dj-style-width="120px"></div></div>',
            { extraHead: '<meta name="djust-styles" content="attrs">' });
        await until(() => window.djustInitialized, 'init');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/dj-style.js']);
        expect(window.document.getElementById('col').style.width).toBe('120px');
    });

    it('a dj-viewport list a patch renders loads infinite-scroll and is tracked', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div id="tab"></div></div>');
        await until(() => window.djustInitialized, 'init');
        // Stubs until the chunk runs: nothing is tracked, so nothing to do.
        expect(window.djust.initInfiniteScroll).toBeUndefined();
        expect(() => window.djust.resetViewport(window.document.body)).not.toThrow();
        const tab = window.document.getElementById('tab');
        tab.innerHTML = '<ul dj-viewport-bottom="more"><li>a</li><li>b</li></ul>';
        window.djust.reinitAfterDOMUpdate(tab);
        await until(() => typeof window.djust.initInfiniteScroll === 'function' &&
            window.djust._infiniteScrollTracked() === 1, 'tracked list');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/infinite-scroll.js']);
    });

    it('a dj-dialog a patch renders open loads the dialog chunk and opens', async () => {
        const { window, loader } = page('<div dj-view="app.V"><div id="tab"></div></div>');
        await until(() => window.djustInitialized, 'init');
        const tab = window.document.getElementById('tab');
        tab.innerHTML = '<dialog id="dlg" dj-dialog="open">hi</dialog>';
        window.djust.reinitAfterDOMUpdate(tab);
        await until(() => window.document.getElementById('dlg').hasAttribute('open'), 'opened dialog');
        expect(chunkRequests(loader)).toEqual(['/static/djust/chunks/dialog.js']);
    });

    it('registering a service worker brings the prefetch chunk once the worker controls the page', async () => {
        const { window, loader } = page('<div dj-view="app.V"></div>', { swController: true });
        await until(() => window.djustInitialized && !!window.djust._prefetch, 'prefetch chunk');
        expect(typeof window.djust._prefetch.clear).toBe('function');
        expect(chunkRequests(loader).filter((p) => p.includes('prefetch')).length).toBe(1);
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
        // The private bridge for chunks exists in the core build only.
        expect(window.djust._chunkScope).toBeUndefined();
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
    for (const k of ['indexedDB', 'IDBKeyRange', 'caches', 'DataTransfer', 'matchMedia']) browserGlobals[k] = 'readonly';

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
            'globalThis.djust._sw = {',
            // #4320
            'document._djustComponentBehaviors = true', 'globalThis.djust.keyboardNav = {',
            'window.djustErrorOverlay = {', 'globalThis.djust.djStyle = {', 'globalThis.djust.flip = {',
            'globalThis.djust.maybeDeferRemoval = maybeDeferRemoval', 'globalThis.djust.djTransitionGroup = {',
            'window.djust.initInfiniteScroll = initInfiniteScroll', 'window.djust._intentPrefetch = {',
            'globalThis.djust.djTrackStatic = {', 'globalThis.djust.djDialog = {']) {
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
