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
 * Users set this via: window.djust.hooks = { HookName: { mounted(){}, ... } }
 */

/**
 * Map of active hook instances keyed by element id.
 * Each entry: { hookName, instance, el }
 */
const _activeHooks = new Map();

/**
 * Counter for generating unique IDs for hooked elements.
 */
let _hookIdCounter = 0;

/**
 * Get a stable ID for an element, creating one if needed.
 */
function _getHookElId(el) {
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
    instance.viewName = el.closest('[data-djust-view]')?.getAttribute('data-djust-view') || '';

    // pushEvent: send a custom event to the server
    instance.pushEvent = function(event, payload = {}) {
        if (window.djust.liveViewInstance && window.djust.liveViewInstance.ws) {
            window.djust.liveViewInstance.ws.send(JSON.stringify({
                type: 'event',
                event: event,
                data: payload,
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

    return instance;
}

/**
 * Scan the DOM for elements with dj-hook and mount their hooks.
 * Called on init and after DOM patches.
 */
function mountHooks(root) {
    root = root || document;
    const hooks = window.djust.hooks || {};
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

        // Call mounted()
        if (typeof instance.mounted === 'function') {
            try {
                instance.mounted();
            } catch (e) {
                console.error(`[dj-hook] Error in ${hookName}.mounted():`, e);
            }
        }
    });
}

/**
 * Called after a DOM patch to update/mount/destroy hooks as needed.
 */
function updateHooks(root) {
    root = root || document;
    const hooks = window.djust.hooks || {};

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
                try {
                    existing.instance.updated();
                } catch (e) {
                    console.error(`[dj-hook] Error in ${hookName}.updated():`, e);
                }
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
                try {
                    instance.mounted();
                } catch (e) {
                    console.error(`[dj-hook] Error in ${hookName}.mounted():`, e);
                }
            }
        }
    });

    // 2. Destroy hooks whose elements were removed
    for (const [elId, entry] of _activeHooks) {
        if (!currentElements.has(elId)) {
            if (typeof entry.instance.destroyed === 'function') {
                try {
                    entry.instance.destroyed();
                } catch (e) {
                    console.error(`[dj-hook] Error in ${entry.hookName}.destroyed():`, e);
                }
            }
            _activeHooks.delete(elId);
        }
    }
}

/**
 * Notify all hooks of WebSocket disconnect.
 */
function notifyHooksDisconnected() {
    for (const [, entry] of _activeHooks) {
        if (typeof entry.instance.disconnected === 'function') {
            try {
                entry.instance.disconnected();
            } catch (e) {
                console.error(`[dj-hook] Error in ${entry.hookName}.disconnected():`, e);
            }
        }
    }
}

/**
 * Notify all hooks of WebSocket reconnect.
 */
function notifyHooksReconnected() {
    for (const [, entry] of _activeHooks) {
        if (typeof entry.instance.reconnected === 'function') {
            try {
                entry.instance.reconnected();
            } catch (e) {
                console.error(`[dj-hook] Error in ${entry.hookName}.reconnected():`, e);
            }
        }
    }
}

/**
 * Dispatch a push_event to hooks that registered handleEvent listeners.
 */
function dispatchPushEventToHooks(eventName, payload) {
    for (const [, entry] of _activeHooks) {
        const handlers = entry.instance._eventHandlers[eventName];
        if (handlers) {
            handlers.forEach(cb => {
                try {
                    cb(payload);
                } catch (e) {
                    console.error(`[dj-hook] Error in ${entry.hookName}.handleEvent("${eventName}"):`, e);
                }
            });
        }
    }
}

/**
 * Destroy all hooks (cleanup).
 */
function destroyAllHooks() {
    for (const [elId, entry] of _activeHooks) {
        if (typeof entry.instance.destroyed === 'function') {
            try {
                entry.instance.destroyed();
            } catch (e) {
                console.error(`[dj-hook] Error in ${entry.hookName}.destroyed():`, e);
            }
        }
    }
    _activeHooks.clear();
}

// Export to namespace
window.djust.mountHooks = mountHooks;
window.djust.updateHooks = updateHooks;
window.djust.notifyHooksDisconnected = notifyHooksDisconnected;
window.djust.notifyHooksReconnected = notifyHooksReconnected;
window.djust.dispatchPushEventToHooks = dispatchPushEventToHooks;
window.djust.destroyAllHooks = destroyAllHooks;
window.djust._activeHooks = _activeHooks;
