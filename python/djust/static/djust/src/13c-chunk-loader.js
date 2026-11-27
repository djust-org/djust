// ============================================================================
// Client chunks (#4007): load optional feature modules only when used
// ============================================================================
//
// The auto-injected client is the CORE bundle (client-core.min.js). The
// optional modules are built as separate chunk files under
// static/djust/chunks/ by scripts/build-client.sh, and this module loads one
// only when the page needs it:
//
//   uploads              [dj-upload], [dj-upload-drop], or a mount that
//                        declares uploads (upload_configs)
//   virtual-list         [dj-virtual]
//   cursor-overlay       [dj-hook="CursorOverlay"]
//   tutorial             #dj-tutorial-bubble ({% tutorial_bubble %})
//   service-worker       a service worker controls the page, or the app calls
//                        djust.registerServiceWorker()
//   component-behaviors  a djust.components widget (.rich-select, .combobox,
//                        .kanban, ...: see 53-component-behaviors.js)
//   keyboard-nav         [role="dialog"], [role="tablist"], .dj-modal,
//                        .dj-accordion, .dj-dropdown, .dj-dropdown-menu
//   error-overlay        window.DEBUG_MODE (Django DEBUG); never in production
//   dj-style             <meta name="djust-styles" content="attrs"> in the
//                        head (LIVEVIEW_CONFIG csp_strict_styles)
//   flip                 [dj-flip]
//   transitions          [dj-transition], [dj-remove], [dj-transition-group]
//   infinite-scroll      [dj-viewport-top], [dj-viewport-bottom]
//   prefetch             a[dj-prefetch], or a service worker controls the page
//   track-static         [dj-track-static]
//   dialog               dialog[dj-dialog]
//
// The SSE transport (03b-sse.js) stays in core: see
// docs/website/guides/client-bundle.md, "What stays in core".
//
// djustInit() (14-init.js) scans the document, loads the chunks it needs and
// only then binds and connects, so a page that uses a chunk behaves exactly as
// it did with the single bundle. A page that uses none loads none.
//
// Each chunk is a <script> carrying the CSP nonce of the client's own script
// tag. Its URL comes from the tag's data-djust-chunks map (static() URLs,
// hashed under ManifestStaticFilesStorage) or, without one, sits next to the
// client script under chunks/.
//
// The full bundle (client.js / client.min.js, or LIVEVIEW_CONFIG
// ['client_bundle'] = 'full') contains every module and loads nothing.
// window.djust._clientBundle says which one is running; it is set by the build
// at the end of the bundle, so it is undefined while modules evaluate and
// 'core' / 'full' by the time djustInit() runs. Undefined (src modules
// concatenated by hand, as some tests do) behaves as the full bundle.

const _DJUST_CHUNK_SELECTORS = {
    'uploads': '[dj-upload],[dj-upload-drop]',
    'virtual-list': '[dj-virtual]',
    'cursor-overlay': '[dj-hook="CursorOverlay"]',
    'tutorial': '#dj-tutorial-bubble',
    // The widgets 53-component-behaviors.js serves (its header lists them).
    'component-behaviors': '.rich-select,.combobox,.dj-theme-toggle,.popover-wrapper,' +
        '.dj-popconfirm-wrapper,.collapsible,.ctx-wrapper,.code-block-copy,.dropzone,' +
        '.kanban,.rte,.multi-select,.split-pane',
    'keyboard-nav': '[role="dialog"],.dj-modal,[role="tablist"],.dj-accordion,' +
        '.dj-dropdown,.dj-dropdown-menu',
    'flip': '[dj-flip]',
    'transitions': '[dj-transition],[dj-remove],[dj-transition-group]',
    'infinite-scroll': '[dj-viewport-top],[dj-viewport-bottom]',
    'prefetch': 'a[dj-prefetch]',
    'track-static': '[dj-track-static]',
    'dialog': 'dialog[dj-dialog]',
};

function _djustSwControlled() {
    return typeof navigator !== 'undefined' && !!navigator.serviceWorker &&
        !!navigator.serviceWorker.controller;
}

// Chunks a page needs for a reason other than an element: checked at every
// scan (init, TurboNav swap, patch), whatever the scanned subtree.
const _DJUST_CHUNK_PAGE_TRIGGERS = {
    'service-worker': _djustSwControlled,
    // The hover layer of 22-prefetch.js posts to the controlling worker.
    'prefetch': _djustSwControlled,
    // The persistent overlay only renders under DEBUG (set by the server).
    'error-overlay': function () { return window.DEBUG_MODE === true; },
    // 54-dj-style.js reads the server-rendered head once.
    'dj-style': function () {
        return !!(document.head &&
            document.head.querySelector('meta[name="djust-styles"][content="attrs"]'));
    },
};

function _djustIsChunkName(name) {
    return Object.prototype.hasOwnProperty.call(_DJUST_CHUNK_SELECTORS, name) ||
        Object.prototype.hasOwnProperty.call(_DJUST_CHUNK_PAGE_TRIGGERS, name);
}

// Captured while this bundle executes; null when it was evaluated, not loaded.
const _djustClientScript = document.currentScript || null;
const _djustChunkLoaded = {};
const _djustChunkPromises = {};
// A chunk that failed (error or timeout) is not requested again by the
// page scans, and its failure is reported once.
const _djustChunkFailed = {};
// A request that neither loads nor errors must not hold back init for good.
const _DJUST_CHUNK_TIMEOUT_MS = 10000;

function _djustReportChunkFailure(name, err) {
    // eslint-disable-next-line security/detect-object-injection
    if (_djustChunkFailed[name]) return;
    // eslint-disable-next-line security/detect-object-injection
    _djustChunkFailed[name] = true;
    console.error(err && err.message ? err.message : err);
}

function _djustClientNonce() {
    const own = _djustClientScript;
    // Browsers hide the nonce attribute after load: read the property first.
    if (own && (own.nonce || own.getAttribute('nonce'))) {
        return own.nonce || own.getAttribute('nonce');
    }
    const any = document.querySelector('script[nonce]');
    return any ? (any.nonce || any.getAttribute('nonce') || '') : '';
}

// A chunk URL from data-djust-chunks loads only from the origin that served
// the client script itself (static() may point at a CDN), over http(s).
// Anything else resolves to null and the chunk is reported as unloadable.
function _djustSameOriginScriptUrl(url, own) {
    try {
        const base = own.src || window.location.href;
        const resolved = new URL(url, base);
        const expected = new URL(base).origin;
        if ((resolved.protocol === 'https:' || resolved.protocol === 'http:') &&
            resolved.origin === expected) {
            return resolved.href;
        }
    } catch (_e) {
        if (globalThis.djustDebug) console.log('[djust] bad chunk URL', _e);
    }
    return null;
}

function _djustChunkUrl(name) {
    const own = _djustClientScript ||
        document.querySelector('script[src*="djust/client-core"]');
    if (!own) return null;
    const mapAttr = own.getAttribute('data-djust-chunks');
    if (mapAttr) {
        try {
            const map = JSON.parse(mapAttr);
            // `name` is one of the fixed chunk names, never page input.
            // eslint-disable-next-line security/detect-object-injection
            const url = map && Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null;
            if (typeof url === 'string') {
                return _djustSameOriginScriptUrl(url, own);
            }
        } catch (_e) {
            if (globalThis.djustDebug) console.log('[djust] unreadable data-djust-chunks', _e);
        }
    }
    if (!own.src) return null;
    const minified = /\.min\.[^/]*$/.test(new URL(own.src).pathname);
    return new URL('chunks/' + name + (minified ? '.min.js' : '.js'), own.src).href;
}

function _djustIsCoreBundle() {
    return window.djust._clientBundle === 'core';
}

/**
 * Load one client chunk. Resolves once its code has run; resolves at once
 * when it is already loaded or when the full bundle is running.
 * Public as `djust.loadClientChunk(name)`.
 */
function loadClientChunk(name) {
    if (!_djustIsChunkName(name)) {
        return Promise.reject(new Error('[djust] unknown client chunk: ' + name));
    }
    // eslint-disable-next-line security/detect-object-injection
    if (!_djustIsCoreBundle() || _djustChunkLoaded[name]) return Promise.resolve();
    // eslint-disable-next-line security/detect-object-injection
    if (_djustChunkPromises[name]) return _djustChunkPromises[name];
    const url = _djustChunkUrl(name);
    if (!url) {
        return Promise.reject(new Error('[djust] cannot locate client chunk ' + name +
            ': the client script has no src (use the full bundle, client.min.js)'));
    }
    const promise = new Promise(function (resolve, reject) {
        // window.djust._chunkLoadTimeoutMs: private override, for tests.
        const timeoutMs = window.djust._chunkLoadTimeoutMs || _DJUST_CHUNK_TIMEOUT_MS;
        const timer = setTimeout(function () {
            reject(new Error('[djust] client chunk ' + name + ' did not load within ' +
                timeoutMs + ' ms (' + url + ')'));
        }, timeoutMs);
        const script = document.createElement('script');
        script.src = url;
        const nonce = _djustClientNonce();
        if (nonce) script.setAttribute('nonce', nonce);
        script.setAttribute('data-djust-chunk', name);
        script.onload = function () { clearTimeout(timer); resolve(); };
        script.onerror = function () {
            clearTimeout(timer);
            reject(new Error('[djust] failed to load client chunk ' + name + ' from ' + url));
        };
        document.head.appendChild(script);
    });
    // eslint-disable-next-line security/detect-object-injection
    _djustChunkPromises[name] = promise;
    promise.catch(function () {
        // An explicit loadClientChunk() later may try again; page scans do not.
        // eslint-disable-next-line security/detect-object-injection
        if (_djustChunkPromises[name] === promise) delete _djustChunkPromises[name];
    });
    return promise;
}

/** Names of the chunks `root` needs that are not loaded yet (core bundle only). */
function _djustNeededChunks(root, changes) {
    if (!_djustIsCoreBundle()) return [];
    const scope = root || document;
    const needed = [];
    const pending = Object.keys(_DJUST_CHUNK_SELECTORS).filter(function (name) {
        // eslint-disable-next-line security/detect-object-injection
        return !_djustChunkLoaded[name] && !_djustChunkFailed[name];
    });
    // #4001: after a patch, only what it inserted or re-attributed.
    const matches = function (selector) {
        return changes ? _changedMatches(changes, selector).length > 0
            : !!((scope.matches && scope.matches(selector)) ||
                (scope.querySelector && scope.querySelector(selector)));
    };
    // One pass over the scanned elements for every pending selector; only a
    // hit is narrowed down to the chunks it names.
    if (pending.length && matches(pending.map(function (name) {
        // eslint-disable-next-line security/detect-object-injection
        return _DJUST_CHUNK_SELECTORS[name];
    }).join(','))) {
        for (const name of pending) {
            // eslint-disable-next-line security/detect-object-injection
            if (matches(_DJUST_CHUNK_SELECTORS[name])) needed.push(name);
        }
    }
    for (const name of Object.keys(_DJUST_CHUNK_PAGE_TRIGGERS)) {
        // eslint-disable-next-line security/detect-object-injection
        if (_djustChunkLoaded[name] || _djustChunkFailed[name] || needed.indexOf(name) !== -1) continue;
        // eslint-disable-next-line security/detect-object-injection
        if (_DJUST_CHUNK_PAGE_TRIGGERS[name]()) needed.push(name);
    }
    return needed;
}

/**
 * Run `fn` once the chunks `root` needs have loaded: synchronously when none
 * are needed (every page without them, and the full bundle), otherwise after
 * they load. A chunk that fails to load is reported and `fn` still runs, so
 * the rest of the page works.
 */
function _djustWithChunks(root, fn) {
    const needed = _djustNeededChunks(root);
    if (needed.length === 0) {
        fn();
        return;
    }
    if (globalThis.djustDebug) console.log('[djust] loading client chunks:', needed.join(', '));
    Promise.all(needed.map(function (name) {
        return loadClientChunk(name).catch(function (err) {
            _djustReportChunkFailure(name, err);
        });
    })).then(function () {
        // queueMicrotask, not inside .then: an exception from init surfaces
        // as an uncaught error rather than an unhandled rejection (#2185).
        queueMicrotask(fn);
    });
}

// ---------------------------------------------------------------------------
// Lazy stubs for the APIs core and applications call on chunked modules.
// The chunk replaces each stub with the real object when it runs; then
// _chunkReady() replays what the stub recorded.
// ---------------------------------------------------------------------------

let _djustUploadConfigs;
let _djustUploadsBindWanted = false;
let _djustSwMountData = null;

function _djustForwardAfterLoad(chunk, getTarget, method) {
    return function () {
        const args = arguments;
        return loadClientChunk(chunk).then(function () {
            const target = getTarget();
            // `method` is a fixed API name from this module, never page input.
            // eslint-disable-next-line security/detect-object-injection
            const fn = target && !target._djustStub ? target[method] : null;
            if (typeof fn !== 'function') {
                throw new Error('[djust] client chunk ' + chunk + ' did not provide ' + method);
            }
            return fn.apply(target, args);
        });
    };
}

function _djustBackgroundLoad(chunk) {
    loadClientChunk(chunk).catch(function (err) {
        _djustReportChunkFailure(chunk, err);
        if (chunk === 'uploads') _djustFlushUploadPushEvents();
    });
}

/**
 * After a patch: load, in the background, the chunks the patched subtree
 * now needs (a first dj-virtual list, CursorOverlay hook or tutorial bubble
 * rendered by a patch or live_redirect). Each chunk's ready handler then runs
 * its init over the document. Called from reinitAfterDOMUpdate().
 */
function _djustLoadChunksFor(scope, changes) {
    for (const name of _djustNeededChunks(scope, changes)) _djustBackgroundLoad(name);
}

// Upload push events (e.g. ``djust:upload_progress`` from the view's
// cancel_upload()) that arrive while the uploads chunk is loading are held,
// listeners and hooks alike, and dispatched once it has run, so the chunk's
// own listener sees them. Any push event whose name mentions "upload" is
// held, so a listener the module adds later is covered without a change here.
const _djustHeldUploadPushEvents = [];

function _djustDispatchPushEvent(event, payload) {
    if (typeof event === 'string' && event.indexOf('upload') !== -1 &&
        window.djust.uploads && window.djust.uploads._djustStub &&
        _djustChunkPromises['uploads']) {
        _djustHeldUploadPushEvents.push([event, payload]);
        return;
    }
    window.dispatchEvent(new CustomEvent('djust:push_event', {
        detail: { event: event, payload: payload }
    }));
    dispatchPushEventToHooks(event, payload);
}

function _djustFlushUploadPushEvents() {
    const held = _djustHeldUploadPushEvents.splice(0);
    for (const entry of held) {
        window.dispatchEvent(new CustomEvent('djust:push_event', {
            detail: { event: entry[0], payload: entry[1] }
        }));
        dispatchPushEventToHooks(entry[0], entry[1]);
    }
}

if (!window.djust.uploads) {
    const getUploads = function () { return window.djust.uploads; };
    window.djust.uploads = {
        _djustStub: true,
        // A mount that declares uploads needs the chunk even when its inputs
        // arrive later; the configs are replayed when it runs.
        setConfigs: function (configs) {
            _djustUploadConfigs = configs;
            if (configs && Object.keys(configs).length > 0) _djustBackgroundLoad('uploads');
        },
        bindHandlers: function () {
            _djustUploadsBindWanted = true;
            if (document.querySelector(_DJUST_CHUNK_SELECTORS.uploads)) _djustBackgroundLoad('uploads');
        },
        handleProgress: _djustForwardAfterLoad('uploads', getUploads, 'handleProgress'),
        handleResumed: _djustForwardAfterLoad('uploads', getUploads, 'handleResumed'),
        queueClipboardFiles: _djustForwardAfterLoad('uploads', getUploads, 'queueClipboardFiles'),
        cancelUpload: _djustForwardAfterLoad('uploads', getUploads, 'cancelUpload'),
        uploadFile: _djustForwardAfterLoad('uploads', getUploads, 'uploadFile'),
        uploadPresigned: _djustForwardAfterLoad('uploads', getUploads, 'uploadPresigned'),
    };
}

if (!window.djust.registerServiceWorker) {
    const getDjust = function () { return window.djust; };
    window.djust.registerServiceWorker = _djustForwardAfterLoad('service-worker', getDjust, 'registerServiceWorker');
}

if (!window.djust._sw) {
    // Core calls only applyMountMetadata unguarded; the rest it type-checks.
    window.djust._sw = {
        _djustStub: true,
        applyMountMetadata: function (data) {
            if (data && data.type === 'mount') _djustSwMountData = data;
        },
    };
}

// djust.resetViewport / teardownInfiniteScroll act on the containers the
// infinite-scroll chunk tracks; before it has loaded there are none, so they
// do nothing. djust.initInfiniteScroll stays undefined until then: core skips
// the dj-viewport-* pass without it.
if (!window.djust.resetViewport) window.djust.resetViewport = function () {};
if (!window.djust.teardownInfiniteScroll) window.djust.teardownInfiniteScroll = function () {};

// Called by each chunk after its code has run (see build-client.sh).
const _DJUST_CHUNK_READY = {
    'uploads': function () {
        const uploads = window.djust.uploads;
        if (_djustUploadConfigs !== undefined) uploads.setConfigs(_djustUploadConfigs);
        if (_djustUploadsBindWanted || window.djustInitialized) uploads.bindHandlers(document);
        _djustFlushUploadPushEvents();
    },
    'virtual-list': function () {
        if (window.djustInitialized) window.djust.initVirtualLists(document);
    },
    'cursor-overlay': function () {
        if (window.djustInitialized) mountHooks(document);
    },
    'tutorial': function () {},
    'service-worker': function () {
        if (_djustSwMountData) window.djust._sw.applyMountMetadata(_djustSwMountData);
        _djustSwMountData = null;
        // The hover prefetch layer posts to the worker this page now uses.
        if (_djustSwControlled()) _djustBackgroundLoad('prefetch');
    },
    // These modules set themselves up when they run (each installs its
    // document listeners or observer and scans the page at once when the
    // document is already parsed), so their handlers have nothing to do.
    'component-behaviors': function () {},
    'keyboard-nav': function () {},
    'error-overlay': function () {},
    'dj-style': function () {},
    'flip': function () {},
    'transitions': function () {},
    'prefetch': function () {},
    'track-static': function () {},
    'dialog': function () {},
    'infinite-scroll': function () {
        if (window.djustInitialized) window.djust.initInfiniteScroll(document);
    },
};

window.djust._chunkShouldRun = function (name) {
    // eslint-disable-next-line security/detect-object-injection
    return _djustIsCoreBundle() && !_djustChunkLoaded[name];
};

window.djust._chunkReady = function (name) {
    if (!Object.prototype.hasOwnProperty.call(_DJUST_CHUNK_READY, name)) return;
    // eslint-disable-next-line security/detect-object-injection
    _djustChunkLoaded[name] = true;
    try {
        // eslint-disable-next-line security/detect-object-injection
        _DJUST_CHUNK_READY[name]();
    } catch (err) {
        console.error('[djust] client chunk ' + name + ' failed to start:', err);
    }
};

// PRIVATE: window.djust._chunkScope is not an API. It holds the core-scope
// bindings a chunk reads; build-client.sh rewrites each name a chunk uses
// (CHUNK_SPECS) to a read through this object, so a reassigned binding
// (liveViewWS) is read live. Only the core bundle exposes it: the build calls
// this from the core bundle's last line.
function _djustExposeChunkScope() {
    window.djust._chunkScope = Object.freeze({
        get liveViewWS() { return liveViewWS; },
        isWSConnected: isWSConnected,
        slotIdFor: slotIdFor,
        slotFrameFields: slotFrameFields,
        _destroyDetachedHooks: _destroyDetachedHooks,
    });
}

window.djust.loadClientChunk = loadClientChunk;
window.djust._neededClientChunks = _djustNeededChunks;
