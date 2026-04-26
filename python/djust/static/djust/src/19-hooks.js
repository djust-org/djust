// ============================================================================
// dj-hook — Client-Side JavaScript Hooks
// ============================================================================
//
// Allows custom JavaScript to run when elements with dj-hook="HookName"
// are mounted, updated, or destroyed in the DOM.
//
// Usage:
//   1. Register hooks:
//      window.djust.hooks = {
//        MyChart: {
//          mounted() { /* this.el is the DOM element */ },
//          updated() { /* called after server re-render */ },
//          destroyed() { /* cleanup */ },
//          disconnected() { /* WebSocket lost */ },
//          reconnected() { /* WebSocket restored */ },
//        }
//      };
//
//   2. In template:
//      <canvas dj-hook="MyChart" data-values="1,2,3"></canvas>
//
//   The hook instance has access to:
//     this.el       — the DOM element
//     this.viewName — the LiveView name
//     this.pushEvent(event, payload) — send event to server
//     this.handleEvent(event, callback) — listen for server push_events
//
// ============================================================================

/**
 * Registry of hook definitions provided by the user.
 * Users set this via:
 *   window.djust.hooks = { HookName: { mounted(){}, ... } }
 * or (Phoenix LiveView-compatible):
 *   window.DjustHooks = { HookName: { mounted(){}, ... } }
 */

/**
 * Get the merged hook registry (window.djust.hooks + window.DjustHooks).
 */
function _getHookDefs() {
    return Object.assign({}, window.DjustHooks || {}, window.djust.hooks || {});
}

/**
 * Map of active hook instances keyed by element id.
 * Each entry: { hookName, instance, el }
 */
// Use var so declarations hoist above the IIFE guard that calls
// djustInit() → mountHooks() before this file executes.
// Initializations are deferred to _ensureHooksInit() since var hoists
// the name but not the `= new Map()` assignment.
var _activeHooks;
var _hookIdCounter;

/**
 * Lazy-initialize hook state.  Called at the top of every public function
 * so the Map/counter exist regardless of source-file concatenation order.
 */
function _ensureHooksInit() {
    if (!_activeHooks) {
        _activeHooks = new Map();
        _hookIdCounter = 0;
    }
}

/**
 * Get a stable ID for an element, creating one if needed.
 */
function _getHookElId(el) {
    _ensureHooksInit();
    if (!el._djustHookId) {
        el._djustHookId = `djust-hook-${++_hookIdCounter}`;
    }
    return el._djustHookId;
}

/**
 * Create a hook instance with the standard API.
 */
function _createHookInstance(hookDef, el) {
    const instance = Object.create(hookDef);

    instance.el = el;
    instance.viewName = el.closest('[dj-view]')?.getAttribute('dj-view') || '';

    // pushEvent: send a custom event to the server
    instance.pushEvent = function(event, payload = {}) {
        if (window.djust.liveViewInstance && window.djust.liveViewInstance.ws) {
            window.djust.liveViewInstance.ws.send(JSON.stringify({
                type: 'event',
                event: event,
                params: payload,
            }));
        } else {
            console.warn(`[dj-hook] Cannot pushEvent "${event}" — no WebSocket connection`);
        }
    };

    // handleEvent: register a callback for server-pushed events
    instance._eventHandlers = {};
    instance.handleEvent = function(eventName, callback) {
        if (!instance._eventHandlers[eventName]) {
            instance._eventHandlers[eventName] = [];
        }
        instance._eventHandlers[eventName].push(callback);
    };

    // js(): programmable JS Commands from hook callbacks (Phoenix 1.0 parity).
    // Returns a fresh JSChain bound (via exec()) to this hook's element.
    // Usage inside a hook method:
    //   this.js().show('#modal').addClass('active', {to: '#overlay'}).exec();
    instance.js = function() {
        return (window.djust.js && window.djust.js.chain)
            ? window.djust.js.chain()
            : null;
    };

    return instance;
}

/**
 * Safely invoke a hook lifecycle method. Tolerates both sync and async
 * implementations (v0.8.6 async-hooks enhancement, leveraging PR-A's
 * async patch path + #1098's queued handleMessage):
 *
 * - Sync return: errors caught and logged immediately.
 * - Async return (Promise): errors caught via ``.catch`` and logged when
 *   the Promise rejects. The dispatcher does NOT await — hooks fire-and-
 *   forget on the lifecycle path so user I/O in a hook can't block the
 *   render loop. User code that needs sequencing across hook completion
 *   should coordinate that explicitly.
 *
 * No API signature change for hook authors — sync hooks behave exactly
 * as before. Async hooks now have safe error reporting instead of
 * Unhandled Promise Rejection logs.
 */
function _safeCallHook(fn, label, ...args) {
    try {
        const result = fn(...args);
        if (result && typeof result.then === 'function') {
            result.catch((e) =>
                console.error(`[dj-hook] Error in ${label}:`, e),
            );
        }
    } catch (e) {
        console.error(`[dj-hook] Error in ${label}:`, e);
    }
}


/**
 * Scan the DOM for elements with dj-hook and mount their hooks.
 * Called on init. For post-patch and post-navigation updates, use updateHooks().
 */
function mountHooks(root) {
    _ensureHooksInit();
    root = root || document;
    const hooks = _getHookDefs();
    const elements = root.querySelectorAll('[dj-hook]');

    elements.forEach(el => {
        const hookName = el.getAttribute('dj-hook');
        const elId = _getHookElId(el);

        // Skip if already mounted
        if (_activeHooks.has(elId)) {
            return;
        }

        const hookDef = hooks[hookName];
        if (!hookDef) {
            console.warn(`[dj-hook] No hook registered for "${hookName}"`);
            return;
        }

        const instance = _createHookInstance(hookDef, el);
        _activeHooks.set(elId, { hookName, instance, el });

        // Call mounted() (async-tolerant — see _safeCallHook)
        if (typeof instance.mounted === 'function') {
            _safeCallHook(instance.mounted.bind(instance), `${hookName}.mounted()`);
        }
    });
}

/**
 * Notify active hooks that a DOM update is about to happen.
 * Call this BEFORE applying patches.
 */
function beforeUpdateHooks(root) {
    _ensureHooksInit();
    root = root || document;
    for (const [, entry] of _activeHooks) {
        // Only call if element is still in the DOM
        if (root.contains(entry.el) && typeof entry.instance.beforeUpdate === 'function') {
            _safeCallHook(
                entry.instance.beforeUpdate.bind(entry.instance),
                `${entry.hookName}.beforeUpdate()`,
            );
        }
    }
}

/**
 * Called after a DOM patch to update/mount/destroy hooks as needed.
 */
function updateHooks(root) {
    _ensureHooksInit();
    root = root || document;
    const hooks = _getHookDefs();

    // 1. Find all currently hooked elements in the DOM
    const currentElements = new Set();
    root.querySelectorAll('[dj-hook]').forEach(el => {
        const elId = _getHookElId(el);
        currentElements.add(elId);

        const hookName = el.getAttribute('dj-hook');
        const existing = _activeHooks.get(elId);

        if (existing) {
            // Element still exists — call updated()
            existing.el = el; // Update reference in case DOM was replaced
            existing.instance.el = el;
            if (typeof existing.instance.updated === 'function') {
                _safeCallHook(
                    existing.instance.updated.bind(existing.instance),
                    `${hookName}.updated()`,
                );
            }
        } else {
            // New element — mount it
            const hookDef = hooks[hookName];
            if (!hookDef) {
                console.warn(`[dj-hook] No hook registered for "${hookName}"`);
                return;
            }
            const instance = _createHookInstance(hookDef, el);
            _activeHooks.set(elId, { hookName, instance, el });
            if (typeof instance.mounted === 'function') {
                _safeCallHook(instance.mounted.bind(instance), `${hookName}.mounted()`);
            }
        }
    });

    // 2. Destroy hooks whose elements were removed
    for (const [elId, entry] of _activeHooks) {
        if (!currentElements.has(elId)) {
            if (typeof entry.instance.destroyed === 'function') {
                _safeCallHook(
                    entry.instance.destroyed.bind(entry.instance),
                    `${entry.hookName}.destroyed()`,
                );
            }
            _activeHooks.delete(elId);
        }
    }
}

/**
 * Notify all hooks of WebSocket disconnect.
 */
function notifyHooksDisconnected() {
    _ensureHooksInit();
    for (const [, entry] of _activeHooks) {
        if (typeof entry.instance.disconnected === 'function') {
            _safeCallHook(
                entry.instance.disconnected.bind(entry.instance),
                `${entry.hookName}.disconnected()`,
            );
        }
    }
}

/**
 * Notify all hooks of WebSocket reconnect.
 */
function notifyHooksReconnected() {
    _ensureHooksInit();
    for (const [, entry] of _activeHooks) {
        if (typeof entry.instance.reconnected === 'function') {
            _safeCallHook(
                entry.instance.reconnected.bind(entry.instance),
                `${entry.hookName}.reconnected()`,
            );
        }
    }
}

/**
 * Dispatch a push_event to hooks that registered handleEvent listeners.
 */
function dispatchPushEventToHooks(eventName, payload) {
    _ensureHooksInit();
    for (const [, entry] of _activeHooks) {
        const handlers = entry.instance._eventHandlers[eventName];
        if (handlers) {
            handlers.forEach((cb) => {
                _safeCallHook(
                    () => cb(payload),
                    `${entry.hookName}.handleEvent("${eventName}")`,
                );
            });
        }
    }
}

/**
 * Destroy all hooks (cleanup).
 */
function destroyAllHooks() {
    _ensureHooksInit();
    for (const [, entry] of _activeHooks) {
        if (typeof entry.instance.destroyed === 'function') {
            _safeCallHook(
                entry.instance.destroyed.bind(entry.instance),
                `${entry.hookName}.destroyed()`,
            );
        }
    }
    _activeHooks.clear();
}

// Export to namespace
window.djust.mountHooks = mountHooks;
window.djust.beforeUpdateHooks = beforeUpdateHooks;
window.djust.updateHooks = updateHooks;
window.djust.notifyHooksDisconnected = notifyHooksDisconnected;
window.djust.notifyHooksReconnected = notifyHooksReconnected;
window.djust.dispatchPushEventToHooks = dispatchPushEventToHooks;
window.djust.destroyAllHooks = destroyAllHooks;
// Use a getter so callers always see the live Map (initialized lazily)
Object.defineProperty(window.djust, '_activeHooks', {
    get() { _ensureHooksInit(); return _activeHooks; },
    configurable: true,
});
