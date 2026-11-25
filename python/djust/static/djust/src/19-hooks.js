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
// var (not let): module 19 is concatenated after the bootstrap call;
// let would TDZ when _ensureHooksInit() is invoked from earlier modules.
// The lazy-init pattern below relies on hoisted-undefined semantics.
// eslint-disable-next-line no-var
var _activeHooks;
// eslint-disable-next-line no-var
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

    // pushEvent: send a custom event to the server.
    //
    // #3990: through the transport's own event path (`sendEvent`), not a frame
    // built here. The hand-built frame had no `ref`, so the server's reply
    // could not be told from the reply to another event: with one other event
    // pending it resolved THAT event's promise, and the hook itself got no
    // acknowledgment at all. Returns the promise `sendEvent` returns: it
    // resolves with the server's reply frame, or `null` when the event was
    // cancelled or there is no live connection to send it on.
    instance.pushEvent = function(event, payload = {}) {
        const transport = window.djust.liveViewInstance;
        // A hook inside a view mounted beside the page view talks to that
        // view, not the page view (#3252): its slot is named explicitly, and
        // the hook's element is not passed as the trigger, so it takes no
        // loading state from its own events.
        const sent = transport && typeof transport.sendEvent === 'function'
            ? transport.sendEvent(event, payload, null, slotIdFor(el))
            : false;
        if (!sent) {
            console.warn(`[dj-hook] Cannot pushEvent "${event}" — no live connection`);
            return Promise.resolve(null);
        }
        return sent;
    };

    // handleEvent: register a callback for server-pushed events
    instance._eventHandlers = {};
    instance.handleEvent = function(eventName, callback) {
        // eslint-disable-next-line security/detect-object-injection
        if (!instance._eventHandlers[eventName]) {
            // eslint-disable-next-line security/detect-object-injection
            instance._eventHandlers[eventName] = [];
        }
        // eslint-disable-next-line security/detect-object-injection
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

    // Typed values (ADR-025): dj-hook-value-<kebab> attributes exposed as
    // this.values.<camelCase>. JSON-first coercion with raw-string fallback;
    // a literal string that looks like JSON is written JSON-quoted
    // (dj-hook-value-code='"007"'). LIVE reads: each access re-reads the
    // element's CURRENT attribute, so after a server morph updates
    // attributes, updated() sees fresh values with no staleness machinery.
    // Read-only in v1 — write the attribute server-side instead.
    function _valueAttrName(prop) {
        return 'dj-hook-value-' + prop.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
    }
    instance.values = new Proxy({}, {
        get(_t, prop) {
            if (typeof prop !== 'string') return undefined;
            const raw = el.getAttribute(_valueAttrName(prop));
            if (raw === null) return undefined;
            try {
                return JSON.parse(raw);
            } catch (_err) {
                return raw;
            }
        },
        set(_t, prop) {
            throw new TypeError(
                `[dj-hook] this.values.${String(prop)} is read-only (ADR-025); ` +
                'update the dj-hook-value-* attribute server-side instead'
            );
        },
        has(_t, prop) {
            return typeof prop === 'string' && el.hasAttribute(_valueAttrName(prop));
        },
    });

    // Scoped targets (ADR-025): descendants marked dj-hook-target="name".
    // Live subtree queries; nested hooks are NOT excluded in v1 (documented).
    function _targetSelector(name) {
        const esc = (window.CSS && window.CSS.escape) ? window.CSS.escape(name) : name;
        return `[dj-hook-target="${esc}"]`;
    }
    instance.target = function(name) {
        return el.querySelector(_targetSelector(name));
    };
    instance.targets = function(name) {
        return Array.from(el.querySelectorAll(_targetSelector(name)));
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
    // Use %s placeholders for ``label`` so the format string is constant
    // and ``label`` (which derives from a user-controlled ``dj-hook``
    // attribute) is passed as a parameter — silences CodeQL's tainted-
    // format-string warning. console.error doesn't reach an exploitable
    // sink, but the parameterized form is the canonical safe pattern.
    try {
        const result = fn(...args);
        if (result && typeof result.then === 'function') {
            result.catch((e) =>
                console.error('[dj-hook] Error in %s:', label, e),
            );
        }
    } catch (e) {
        console.error('[dj-hook] Error in %s:', label, e);
    }
}


/**
 * Scan the DOM for elements with dj-hook and mount their hooks.
 * Called on init. For post-patch and post-navigation updates, use updateHooks().
 */
// #3992: the page's first sweep runs as soon as the client script has, and a
// script after it (an official adapter under ext/, or the app's own
// deferred script) registers its hook a moment later. That sweep used to warn
// "No hook registered" for it on every load and leave the element unmounted
// until some later patch swept again. The first sweep now waits instead: one
// retry when the document's deferred scripts have run, which mounts the hook or,
// if it is still missing, says so. `var`, read during init (#1370).
var _missingHookRetryArmed = false; // eslint-disable-line no-var

function _deferMissingHookSweep() {
    if (document.readyState === 'complete') return false;
    if (!_missingHookRetryArmed) {
        _missingHookRetryArmed = true;
        let done = false;
        const retry = () => {
            if (done) return;
            done = true;
            mountHooks(document);
        };
        // DOMContentLoaded follows the deferred scripts; `load` covers a client
        // script that was itself added after DOMContentLoaded.
        document.addEventListener('DOMContentLoaded', retry, { once: true });
        window.addEventListener('load', retry, { once: true });
    }
    return true;
}

// The lifecycle contract (#3656), documented in docs/website/guides/hooks.md
// ("The lifecycle contract"); tests/js/hook-lifecycle-contract-3656.test.js has
// one test per rule.
//
//   - An instance belongs to one element node for its whole life. A patch that
//     keeps the node (attributes, text, a keyed move) keeps the instance; a
//     node that is replaced, removed or loses/changes its dj-hook gets
//     destroyed(), and a replacement node a fresh instance with mounted().
//   - mounted() and destroyed() run exactly once per instance. A hook mounted
//     by an update gets no updated() for that update.
//   - After an update: first destroyed() for every hook that left, then
//     mounted() / updated() in document order (parents before children).
//   - disconnected() / reconnected() pair per instance: one disconnected() per
//     lost connection (not one per failed retry), and reconnected() only after
//     it. A hook mounted while offline gets disconnected() after mounted().
//   - Every callback is isolated: a throw or a rejected promise is logged and
//     the patch, the other hooks and the rest of the update go on.

// True between a lost connection and the next open one. `var` with no
// initializer, as _activeHooks above: an earlier module may run first.
var _hooksOffline; // eslint-disable-line no-var

function _callHook(entry, name) {
    // `name` is one of the fixed lifecycle names, never page input.
    // eslint-disable-next-line security/detect-object-injection
    const fn = entry.instance[name];
    if (typeof fn === 'function') {
        _safeCallHook(fn.bind(entry.instance), `${entry.hookName}.${name}()`);
    }
}

/** `root` itself (when it carries dj-hook) and its dj-hook descendants, in document order. */
function _hookElements(root) {
    const els = Array.from(root.querySelectorAll('[dj-hook]'));
    if (root.nodeType === 1 && root.hasAttribute('dj-hook')) els.unshift(root);
    return els;
}

function _mountHookEl(el, hooks, deferMissing) {
    const hookName = el.getAttribute('dj-hook');
    // eslint-disable-next-line security/detect-object-injection
    const hookDef = hooks[hookName];
    if (!hookDef || (typeof hookDef !== 'object' && typeof hookDef !== 'function')) {
        if (!hookDef && deferMissing && _deferMissingHookSweep()) return;
        console.warn(`[dj-hook] No hook registered for "${hookName}"`);
        return;
    }
    const entry = { hookName, instance: _createHookInstance(hookDef, el), el };
    _activeHooks.set(_getHookElId(el), entry);
    _callHook(entry, 'mounted');
    if (_hooksOffline) {
        entry.offline = true;
        _callHook(entry, 'disconnected');
    }
}

/** Visit the dj-hook elements under `root`; a throw for one does not stop the others. */
function _eachHookEl(root, visit) {
    _hookElements(root).forEach((el) => {
        try {
            visit(el);
        } catch (e) {
            console.error('[dj-hook] Error in %s:', el.getAttribute('dj-hook'), e);
        }
    });
}

function _destroyHookEntry(elId, entry) {
    // Deleted first: destroyed() runs once even if it re-enters updateHooks().
    _activeHooks.delete(elId);
    _callHook(entry, 'destroyed');
}

/**
 * destroyed() for every hook whose element left the document, and for every
 * hook under `root` whose element dropped or changed its dj-hook.
 */
function _destroyDetachedHooks(root) {
    _ensureHooksInit();
    root = root || document;
    for (const [elId, entry] of _activeHooks) {
        const el = entry.el;
        if (!el.isConnected || (root.contains(el) && el.getAttribute('dj-hook') !== entry.hookName)) {
            _destroyHookEntry(elId, entry);
        }
    }
}

/**
 * Mount the hooks under `root` that are not mounted yet. Called on init.
 * After a DOM update use updateHooks(), which also updates and destroys.
 */
function mountHooks(root, options) {
    _ensureHooksInit();
    const deferMissing = Boolean(options && options.deferMissing);
    const hooks = _getHookDefs();
    _eachHookEl(root || document, (el) => {
        if (!_activeHooks.has(_getHookElId(el))) _mountHookEl(el, hooks, deferMissing);
    });
}

/**
 * Notify active hooks that a DOM update is about to happen.
 * Called BEFORE the first DOM change of an update (12-vdom-patch.js), for the
 * hooks whose element is inside the updated root.
 */
function beforeUpdateHooks(root) {
    _ensureHooksInit();
    root = root || document;
    for (const [, entry] of _activeHooks) {
        if (root.contains(entry.el)) _callHook(entry, 'beforeUpdate');
    }
}

function _updateHookEntry(entry) {
    const instance = entry.instance;
    if (typeof instance.updated !== 'function') return;
    if (typeof instance.shouldUpdate === 'function') {
        try {
            // Only an explicit synchronous false suppresses the callback.
            // Exceptions and async predicates fail open so an update is
            // never silently lost; observe rejected promises as well.
            const decision = instance.shouldUpdate();
            if (decision === false) return;
            if (decision && typeof decision.then === 'function') {
                Promise.resolve(decision).catch((e) =>
                    console.error('[dj-hook] Error in %s:', `${entry.hookName}.shouldUpdate()`, e),
                );
            }
        } catch (e) {
            console.error('[dj-hook] Error in %s:', `${entry.hookName}.shouldUpdate()`, e);
        }
    }
    _callHook(entry, 'updated');
}

/**
 * Called after a DOM update: destroy the hooks that left, then update the
 * hooks that stayed and mount the new ones, in document order.
 */
function updateHooks(root) {
    _ensureHooksInit();
    root = root || document;
    const hooks = _getHookDefs();
    _destroyDetachedHooks(root);
    _eachHookEl(root, (el) => {
        const elId = _getHookElId(el);
        const entry = _activeHooks.get(elId);
        if (entry && entry.el === el) {
            _updateHookEntry(entry);
            return;
        }
        // Another node carrying this node's hook id (a copied expando): the
        // instance belongs to that node, so this one gets a fresh instance.
        if (entry) _destroyHookEntry(elId, entry);
        _mountHookEl(el, hooks, false);
    });
}

/**
 * Notify hooks that the live connection (WebSocket or SSE) dropped. Once per
 * lost connection: the failed retries that follow call this again and are
 * ignored until notifyHooksReconnected().
 */
function notifyHooksDisconnected() {
    _ensureHooksInit();
    _hooksOffline = true;
    for (const [, entry] of _activeHooks) {
        if (entry.offline) continue;
        entry.offline = true;
        _callHook(entry, 'disconnected');
    }
}

/** Notify the hooks that got disconnected() that a connection is open again. */
function notifyHooksReconnected() {
    _ensureHooksInit();
    _hooksOffline = false;
    for (const [, entry] of _activeHooks) {
        if (!entry.offline) continue;
        entry.offline = false;
        _callHook(entry, 'reconnected');
    }
}

/**
 * Dispatch a push_event to hooks that registered handleEvent listeners.
 */
function dispatchPushEventToHooks(eventName, payload) {
    _ensureHooksInit();
    for (const [, entry] of _activeHooks) {
        // eslint-disable-next-line security/detect-object-injection
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
    for (const [elId, entry] of _activeHooks) _destroyHookEntry(elId, entry);
}

/**
 * Public accessor (#3107): the hook instance mounted on `el`, or null.
 * `el` is an Element or a CSS selector (first match). Returns null for an
 * element with no mounted hook, including one whose hook was destroyed.
 * The instance is the same `this` the hook's callbacks receive.
 */
function getHook(el) {
    _ensureHooksInit();
    if (typeof el === 'string') el = document.querySelector(el);
    if (!el || !el._djustHookId) return null;
    const entry = _activeHooks.get(el._djustHookId);
    return entry && entry.el === el ? entry.instance : null;
}

// Export to namespace
window.djust.getHook = getHook;
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
