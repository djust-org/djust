
// ============================================================================
// Views mounted beside the page view (#3252)
// ============================================================================
//
// A page can hold an eager LiveView, `dj-lazy` views that hydrate later, and
// the views of a `mount_batch`. They share one socket. The page view keeps the
// socket's own state: `clientVdomVersion`, `getLiveViewRoot()`, and every frame
// that names no view. Every other view is a SLOT: the server addresses its
// frames with the `target_id` of its container
// (`[dj-view][data-djust-target="<id>"]`), the client addresses its events and
// recovery requests the same way, and each slot keeps its own VDOM version.
//
// A frame for a slot is applied by the code that applies the page view's
// frames, run in the slot's CONTEXT: `withSlot` points `getLiveViewRoot()` and
// `applyPatches()` at the slot's container and swaps `clientVdomVersion` for
// the slot's. Frames are handled one at a time (`handleMessage` serializes
// them, and a buffered server push is replayed under the context of its own
// frame), so no other frame observes the swap.

// The state lives in 04-cache.js, beside `clientVdomVersion`, so every module
// that reads it (getLiveViewRoot, applyPatches) is initialized after it:
// `_activeSlot`, `_slotVersions`, `_mountedSlots`.

// The frames the server addresses to a slot with ``target_id``. The others
// (errors, upload progress, flash, ...) do not depend on a view's container.
const _SLOT_FRAME_TYPES = new Set([
    'mount', 'patch', 'html_update', 'html_recovery', 'embedded_update', 'child_update', 'sticky_update',
]);

function _slotSelector(targetId) {
    const escaped = (typeof CSS !== 'undefined' && typeof CSS.escape === 'function')
        ? CSS.escape(targetId)
        : String(targetId).replace(/([^A-Za-z0-9_-])/g, '\\$1');
    return '[dj-view][data-djust-target="' + escaped + '"]';
}

/** The container of the slot `targetId`, or null. */
function slotContainer(targetId) {
    if (typeof targetId !== 'string' || !targetId) return null;
    return document.querySelector(_slotSelector(targetId));
}

/**
 * The slot an element belongs to: its nearest `[dj-view][data-djust-target]`
 * ancestor, when the server has mounted a view there. Null for the page view.
 * An event from inside a slot is addressed to it with this.
 */
function slotIdFor(element) {
    if (!element || typeof element.closest !== 'function' || _mountedSlots.size === 0) return null;
    const container = element.closest('[dj-view][data-djust-target]');
    const id = container && container.getAttribute('data-djust-target');
    return id && _mountedSlots.has(id) ? id : null;
}

/**
 * Record on event `params` the slot `element` lives in, so `handleEvent` sends
 * the event to that view (`_slotId`, stripped before it goes on the wire). The
 * bindings that pass no `_targetElement` (input, blur, poll, model, ...) use
 * this: without it the event would run on the page view. No-op for an element
 * of the page view. Returns `params`.
 */
function markSlotOf(params, element) {
    const id = slotIdFor(element);
    if (id) params._slotId = id;
    return params;
}

/** `{target_id}` while addressing a slot, else `{}`: spread into outbound frames. */
function slotFrameFields(targetId) {
    return targetId ? { target_id: targetId } : {};
}

function registerSlot(targetId, viewPath, version) {
    _mountedSlots.set(targetId, { viewPath: viewPath });
    _slotVersions.set(targetId, typeof version === 'number' ? version : null);
    watchSlotContainers();
}

// A container that leaves the document takes its view with it: the server is
// told, so the view's groups, presence, tick and uploads go (and its place in
// the connection's cap is freed). Checked a moment after the DOM settles, so a
// morph that moves a container is not taken for its removal.
let _slotWatcher = null;
let _slotSweepTimer = null;
const SLOT_SWEEP_DELAY_MS = 100;

function _sweepSlotContainers() {
    _slotSweepTimer = null;
    const instance = window.djust && window.djust.liveViewInstance;
    for (const id of Array.from(_mountedSlots.keys())) {
        if (slotContainer(id)) continue;
        if (instance && typeof instance.unmountView === 'function') {
            instance.unmountView(id);
        } else {
            forgetSlot(id);
        }
    }
    if (_mountedSlots.size === 0 && _slotWatcher) {
        _slotWatcher.disconnect();
        _slotWatcher = null;
    }
}

function watchSlotContainers() {
    if (_slotWatcher || typeof MutationObserver !== 'function' || !document.documentElement) return;
    _slotWatcher = new MutationObserver(() => {
        if (_slotSweepTimer === null) _slotSweepTimer = setTimeout(_sweepSlotContainers, SLOT_SWEEP_DELAY_MS);
    });
    _slotWatcher.observe(document.documentElement, { childList: true, subtree: true });
}

function forgetSlot(targetId) {
    _mountedSlots.delete(targetId);
    _slotVersions.delete(targetId);
}

/** Forget every slot (the page is replaced, or the socket is gone). */
function clearSlots() {
    _mountedSlots.clear();
    _slotVersions.clear();
    _activeSlot = null;
}

/**
 * Run `fn(root)` in the context of the slot `targetId`: `getLiveViewRoot()` and
 * `applyPatches()` use its container and `clientVdomVersion` is the slot's.
 * Resolves to `undefined` without running `fn` when the slot has no container
 * (the page replaced it, or it was never hydrated).
 */
async function withSlot(targetId, fn) {
    const root = slotContainer(targetId);
    if (!root) return undefined;
    const outerSlot = _activeSlot;
    const outerVersion = clientVdomVersion;
    _activeSlot = { id: targetId, root: root, viewPath: root.getAttribute('dj-view') };
    clientVdomVersion = _slotVersions.has(targetId) ? _slotVersions.get(targetId) : null;
    try {
        return await fn(root);
    } finally {
        _slotVersions.set(targetId, clientVdomVersion);
        _activeSlot = outerSlot;
        clientVdomVersion = outerVersion;
    }
}

/**
 * Install the delegated event listeners on a slot's container, unless an
 * enclosing view's already cover it (installing here too would send every
 * event twice).
 */
function installSlotListeners(container) {
    if (!container || typeof installDelegatedListeners !== 'function') return;
    const outer = container.parentElement &&
        container.parentElement.closest('[dj-view]:not([dj-sticky-root]):not([data-djust-embedded])');
    if (outer) return;
    installDelegatedListeners(container);
}

/**
 * Apply a slot's `mount` reply, or one entry of a `mount_batch` reply.
 *
 * The slot's container takes the server's HTML (morphed against the
 * pre-rendered content when it had some, so per-connection state reaches the
 * page), the slot starts its own VDOM cursor at the mount's version, and its
 * handler / cache / parameter configuration is added beside the page view's
 * without resetting it.
 *
 * @param {Object} transport  The LiveViewWebSocket the frame arrived on.
 * @param {Object} data       The mount frame, or a `mount_batch` entry.
 * @param {Object} [options]  `{html}`: `'replace'` (default) sets the
 *                            container's content, `'morph'` reconciles its
 *                            existing content, `'none'` leaves it (the caller
 *                            already applied it).
 * @returns {boolean} whether the slot's container was found.
 */
function applySlotMount(transport, data, options = {}) {
    const container = slotContainer(data.target_id);
    if (!container) {
        if (globalThis.djustDebug) {
            console.warn('[LiveView] mount: target not found: %s', String(data.target_id));
        }
        return false;
    }
    registerSlot(data.target_id, data.view || container.getAttribute('dj-view'), data.version);
    installAdditionalMountEventConfig(data);
    if (typeof data.view === 'string' && data.view) {
        // The page view keeps its own contracts: a slot adds its view's
        // beside them (a reset here would drop the page view's).
        _installParameterContracts(transport, data.parameter_contracts, data.view, false);
        const order = transport._parameterContractFrames && transport._parameterContractFrames.get(data);
        if (order !== undefined && transport._parameterContractApplied) {
            transport._parameterContractApplied.set(data.view, order);
        }
    }
    if (data.upload_configs && window.djust.uploads) {
        window.djust.uploads.setConfigs(data.upload_configs);
    }
    const htmlMode = options.html || 'replace';
    if (typeof data.html === 'string' && htmlMode !== 'none') {
        if (htmlMode === 'morph' && data.has_ids === true) {
            _morphPrerenderedMount(container, data.html);
        } else {
            // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
            container.innerHTML = data.html;
            _runInsertedScripts(container);
            _warnDeadScripts(container);
        }
    }
    installSlotListeners(container);
    reinitAfterDOMUpdate(container);
    window.djust._mountReady = true;
    return true;
}

window.djust._markSlotOf = markSlotOf;
window.djust.viewSlots = {
    contextOf: () => _activeSlot,
    container: slotContainer,
    idFor: slotIdFor,
    mounted: () => Array.from(_mountedSlots.keys()),
    version: (targetId) => (_slotVersions.has(targetId) ? _slotVersions.get(targetId) : null),
    withSlot: withSlot,
    clear: clearSlots,
};
