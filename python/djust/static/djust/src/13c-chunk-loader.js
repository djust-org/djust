// ============================================================================
// Client chunks (#4007): load optional feature modules only when used
// ============================================================================
//
// The auto-injected client is the CORE bundle (client-core.min.js). The
// heaviest optional modules are built as separate chunk files under
// static/djust/chunks/ by scripts/build-client.sh, and this module loads one
// only when the page needs it:
//
//   uploads         [dj-upload], [dj-upload-drop], or a mount that declares
//                   uploads (upload_configs)
//   virtual-list    [dj-virtual]
//   cursor-overlay  [dj-hook="CursorOverlay"]
//   tutorial        #dj-tutorial-bubble ({% tutorial_bubble %})
//   service-worker  a service worker controls the page, or the app calls
//                   djust.registerServiceWorker()
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
};

// Captured while this bundle executes; null when it was evaluated, not loaded.
const _djustClientScript = document.currentScript || null;
const _djustChunkLoaded = {};
const _djustChunkPromises = {};

function _djustClientNonce() {
    const own = _djustClientScript;
    // Browsers hide the nonce attribute after load: read the property first.
    if (own && (own.nonce || own.getAttribute('nonce'))) {
        return own.nonce || own.getAttribute('nonce');
    }
    const any = document.querySelector('script[nonce]');
    return any ? (any.nonce || any.getAttribute('nonce') || '') : '';
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
                return url;
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
    if (!Object.prototype.hasOwnProperty.call(_DJUST_CHUNK_SELECTORS, name) && name !== 'service-worker') {
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
        const script = document.createElement('script');
        script.src = url;
        const nonce = _djustClientNonce();
        if (nonce) script.setAttribute('nonce', nonce);
        script.setAttribute('data-djust-chunk', name);
        script.onload = function () { resolve(); };
        script.onerror = function () {
            // eslint-disable-next-line security/detect-object-injection
            delete _djustChunkPromises[name];
            reject(new Error('[djust] failed to load client chunk ' + name + ' from ' + url));
        };
        document.head.appendChild(script);
    });
    // eslint-disable-next-line security/detect-object-injection
    _djustChunkPromises[name] = promise;
    return promise;
}

/** Names of the chunks `root` needs that are not loaded yet (core bundle only). */
function _djustNeededChunks(root) {
    if (!_djustIsCoreBundle()) return [];
    const scope = root || document;
    const needed = [];
    for (const name of Object.keys(_DJUST_CHUNK_SELECTORS)) {
        // eslint-disable-next-line security/detect-object-injection
        if (_djustChunkLoaded[name]) continue;
        // eslint-disable-next-line security/detect-object-injection
        if (scope.querySelector && scope.querySelector(_DJUST_CHUNK_SELECTORS[name])) needed.push(name);
    }
    if (!_djustChunkLoaded['service-worker'] &&
        typeof navigator !== 'undefined' && navigator.serviceWorker && navigator.serviceWorker.controller) {
        needed.push('service-worker');
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
            console.error(err && err.message ? err.message : err);
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
        console.error(err && err.message ? err.message : err);
    });
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

// Called by each chunk after its code has run (see build-client.sh).
const _DJUST_CHUNK_READY = {
    'uploads': function () {
        const uploads = window.djust.uploads;
        if (_djustUploadConfigs !== undefined) uploads.setConfigs(_djustUploadConfigs);
        if (_djustUploadsBindWanted || window.djustInitialized) uploads.bindHandlers(document);
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

// The core-scope bindings a chunk reads. build-client.sh rewrites each name a
// chunk uses (CHUNK_SPECS) to a read through this object, so a reassigned
// binding (liveViewWS) is read live.
window.djust._chunkScope = Object.freeze({
    get liveViewWS() { return liveViewWS; },
    isWSConnected: isWSConnected,
    slotIdFor: slotIdFor,
    slotFrameFields: slotFrameFields,
});

window.djust.loadClientChunk = loadClientChunk;
window.djust._neededClientChunks = _djustNeededChunks;
