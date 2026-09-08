
// === Handler-level rate limiting: the client half of @debounce / @throttle ===
//
// #2656. Until this module existed, `@debounce` and `@throttle` stamped
// metadata that nothing read: `debounceTimers` / `throttleState` were
// declared in 04-cache.js and only ever CLEARED on disconnect
// (03-websocket.js), and `window.handlerMetadata` — written by
// `mixins/template.py` — had zero readers. A decorated handler fired on
// every event exactly as an undecorated one did.
//
// Transport. The config arrives on the MOUNT FRAME as `handler_config`,
// the same route `@cache` already uses for `cache_config`
// (runtime.py `_extract_cache_config` -> 03-websocket.js `setCacheConfig`).
// That route is CSP-strict by construction (#1175/#2183): no inline
// <script>, so nothing depends on a script tag surviving the #1610 mount
// morph, and it works identically over WebSocket and SSE.
// `window.handlerMetadata` is kept as the FALLBACK source for the HTTP
// path, where no mount frame is ever received — see `_configFor()`. It is
// therefore a real reader, not a second unread emission.
//
// Placement. This module gates DISPATCH (`handleEvent` in
// 11-event-handler.js), which is a different layer from the existing
// `dj-debounce` / `dj-throttle` HTML attributes handled by
// `_applyRateLimitAttrs` in 09-event-binding.js — those wrap the DOM
// LISTENER on one element. Both may apply to the same interaction and
// compose: the element-level wrapper decides when a handler call happens,
// this one decides when that call reaches the server. The names `debounce`
// and `throttle` are already taken at bundle scope by 09-event-binding.js,
// hence the `_handler*Gate` names here.

// event name -> {debounce: {wait, max_wait}, throttle: {interval, leading, trailing}}
const handlerRateConfig = new Map();
let handlerMountConfigured = false;
// Active element wrappers only; settled/cancelled timers release DOM references.
const pendingElementRateLimits = new Set();
let teardownEventTransport = null;

function cancelPendingRateLimits() {
    pendingElementRateLimits.forEach(wrapper => wrapper.cancel());
    debounceTimers.forEach(state => clearTimeout(state.timerId));
    throttleState.forEach(state => clearTimeout(state.timeoutId));
    debounceTimers.clear();
    throttleState.clear();
}

function flushPendingRateLimits() {
    const previous = teardownEventTransport;
    teardownEventTransport = { url: window.location.href, collecting: true, pending: new Map() };
    try {
        // The handler gate may hold an older edit while its element wrapper
        // holds the newest one. Collect the older layer first, then replace
        // it by event name instead of issuing racing HTTP requests for both.
        flushHandlerRateLimit();
        Array.from(pendingElementRateLimits).forEach(wrapper => wrapper.flush());
        teardownEventTransport.collecting = false;
        teardownEventTransport.pending.forEach((params, eventName) => {
            window.djust.handleEvent(eventName, params, true);
        });
    } finally {
        teardownEventTransport = previous;
    }
}
window.addEventListener('pagehide', flushPendingRateLimits);

// A mount replaces the complete configuration, including omitted/empty maps.
// Share this between WS and SSE to avoid cross-view rules and cached patches.
function installMountEventConfig(data) {
    cancelPendingRateLimits();
    handlerRateConfig.clear();
    handlerMountConfigured = true;
    setHandlerConfig(data.handler_config);
    cacheConfig.clear();
    resultCache.clear();
    pendingCacheRequests.forEach(state => clearTimeout(state.timeoutId));
    pendingCacheRequests.clear();
    setCacheConfig(data.cache_config);
    optimisticUpdates.clear();
    window.djust._optimisticRules = data.optimistic_rules || {};
}


/**
 * Install handler rate-limit configuration (called on mount, WS and SSE).
 *
 * @param {Object} config - Map of handler name -> `_djust_decorators` dict.
 */
function setHandlerConfig(config) {
    if (!config) return;
    Object.entries(config).forEach(([handlerName, handlerCfg]) => {
        handlerRateConfig.set(handlerName, handlerCfg || {});
        if (globalThis.djustDebug) {
            djLog(`[LiveView:rate] Configured rate limit for ${handlerName}:`, handlerCfg);
        }
    });
}

window.djust.setHandlerConfig = setHandlerConfig;

/**
 * Resolve the decorator config for an event.
 *
 * Mount-frame config wins. `window.handlerMetadata` is consulted only when
 * the mount frame carried nothing for this handler — i.e. the HTTP-only
 * path, where `handleEvent` falls through to `fetch()` and no mount frame
 * is ever delivered.
 *
 * @param {string} eventName
 * @returns {Object|null}
 */
function _configFor(eventName) {
    if (handlerRateConfig.has(eventName)) {
        return handlerRateConfig.get(eventName);
    }
    if (handlerMountConfigured) return null;
    const meta = window.handlerMetadata;
    if (meta && Object.prototype.hasOwnProperty.call(meta, eventName)) {
        // eslint-disable-next-line security/detect-object-injection
        return meta[eventName];
    }
    return null;
}

/**
 * Debounce gate — collapse a burst of events into one send.
 *
 * Django/LiveView semantics: the LAST event's payload wins. Every call
 * cancels the pending timer and installs a new one closing over its own
 * params, so whichever call arrives last is the one that reaches the
 * server.
 *
 * `max_wait` bounds the total delay measured from the FIRST call of the
 * burst, so a user typing continuously still gets a send.
 *
 * @returns {boolean} true when the event was deferred (caller must stop).
 */
function _handlerDebounceGate(eventName, params, dispatch, cfg) {
    const waitMs = Math.max(0, (typeof cfg.wait === 'number' ? cfg.wait : 0.3) * 1000);
    const maxWaitMs =
        typeof cfg.max_wait === 'number' ? Math.max(0, cfg.max_wait * 1000) : null;

    const prev = debounceTimers.get(eventName);
    const firstCallTime = prev ? prev.firstCallTime : Date.now();
    if (prev && prev.timerId) {
        clearTimeout(prev.timerId);
    }

    let delay = waitMs;
    if (maxWaitMs !== null) {
        // Never let the burst push the send past max_wait from its start.
        const remaining = maxWaitMs - (Date.now() - firstCallTime);
        delay = Math.min(waitMs, Math.max(0, remaining));
    }

    const timerId = setTimeout(() => {
        debounceTimers.delete(eventName);
        // Third arg bypasses this gate so the deferred send is not re-deferred.
        dispatch(eventName, params, true);
    }, delay);

    debounceTimers.set(eventName, { timerId, firstCallTime, pendingData: params });
    if (globalThis.djustDebug) {
        djLog(`[LiveView:rate] Debounced ${eventName} for ${delay}ms`);
    }
    return true;
}

/**
 * Schedule the trailing-edge send for a throttled handler.
 *
 * @param {string} eventName
 * @param {Function} dispatch
 * @param {number} intervalMs
 * @param {number} delay - ms until the current window closes
 */
function _scheduleThrottleTrailing(eventName, dispatch, intervalMs, delay) {
    const state = throttleState.get(eventName);
    if (!state) return;
    state.timeoutId = setTimeout(() => {
        const current = throttleState.get(eventName);
        if (!current) return;
        current.timeoutId = null;
        const pending = current.pendingData;
        current.pendingData = null;
        if (pending) {
            // Opening a fresh window from the moment we actually send keeps
            // the guarantee "at most one send per interval" across the
            // leading/trailing boundary.
            current.lastCall = Date.now();
            dispatch(eventName, pending, true);
        } else {
            throttleState.delete(eventName);
        }
    }, delay);
}

/**
 * Throttle gate — cap a handler at one send per `interval`.
 *
 * Both edges are honoured, matching the shipped `@throttle` docstring
 * (`leading=True, trailing=True`): the first event of a window sends
 * immediately, further events inside the window are collapsed into a single
 * trailing send carrying the LAST payload. `leading=False` suppresses the
 * immediate send; `trailing=False` drops everything after it.
 *
 * @returns {boolean} true when the event was deferred or dropped.
 */
function _handlerThrottleGate(eventName, params, dispatch, cfg) {
    const intervalMs = Math.max(
        0,
        (typeof cfg.interval === 'number' ? cfg.interval : 0.1) * 1000
    );
    const leading = cfg.leading !== false;
    const trailing = cfg.trailing !== false;
    const now = Date.now();
    const state = throttleState.get(eventName);

    // No open window: this is a leading edge.
    if (!state || now - state.lastCall >= intervalMs) {
        if (leading) {
            throttleState.set(eventName, {
                lastCall: now,
                timeoutId: null,
                pendingData: null,
            });
            return false; // send now
        }
        if (!trailing) return true; // leading=False + trailing=False drops everything
        throttleState.set(eventName, {
            lastCall: now,
            timeoutId: null,
            pendingData: params,
        });
        _scheduleThrottleTrailing(eventName, dispatch, intervalMs, intervalMs);
        return true;
    }

    // Inside an open window.
    if (!trailing) return true; // drop
    state.pendingData = params; // last one wins
    if (state.timeoutId === null || state.timeoutId === undefined) {
        _scheduleThrottleTrailing(
            eventName,
            dispatch,
            intervalMs,
            Math.max(0, intervalMs - (now - state.lastCall))
        );
    }
    return true;
}

/**
 * Apply @debounce / @throttle for an outbound event.
 *
 * `@debounce` wins when a handler carries both — debouncing already bounds
 * the send rate, and running them in series would delay the trailing send
 * twice.
 *
 * @param {string} eventName
 * @param {Object} params - the event payload (passed through untouched)
 * @param {Function} dispatch - re-entry point, called as (name, params, true)
 * @returns {boolean} true when the caller must STOP (deferred or dropped).
 */
function applyHandlerRateLimit(eventName, params, dispatch) {
    const cfg = _configFor(eventName);
    if (!cfg) return false;
    if (cfg.debounce) {
        return _handlerDebounceGate(eventName, params, dispatch, cfg.debounce);
    }
    if (cfg.throttle) {
        return _handlerThrottleGate(eventName, params, dispatch, cfg.throttle);
    }
    return false;
}

window.djust.applyHandlerRateLimit = applyHandlerRateLimit;

/**
 * Immediately send every pending debounced / throttled event.
 *
 * Called from the `dj-submit` path in 09-event-binding.js for the same
 * reason `_flushPendingDebouncesInForm` is (#1278): a user who edits a
 * debounced field and immediately submits must not lose the last change.
 * `_flushPendingDebouncesInForm` flushes the ELEMENT-level `dj-debounce`
 * wrappers; this flushes the HANDLER-level `@debounce` / `@throttle` gate,
 * which is a separate timer set and would otherwise still be pending.
 */
function flushHandlerRateLimit() {
    // Validate BEFORE destroying anything (#2700 review). The clears below are
    // irreversible: bailing out after them would drop every pending event
    // instead of flushing it. Unreachable in the shipped bundle —
    // 11-event-handler.js assigns `handleEvent` at load — so this is shape,
    // not a live bug.
    if (!window.djust.handleEvent) return;

    // Snapshot first: dispatching re-enters handleEvent, which may write to
    // these maps (a throttled handler opens a fresh window).
    const debouncePending = [];
    debounceTimers.forEach((state, eventName) => {
        if (state.timerId) clearTimeout(state.timerId);
        if (state.pendingData) debouncePending.push([eventName, state.pendingData]);
    });
    debounceTimers.clear();

    const throttlePending = [];
    throttleState.forEach((state, eventName) => {
        if (state.timeoutId) clearTimeout(state.timeoutId);
        if (state.pendingData) throttlePending.push([eventName, state.pendingData]);
    });
    throttleState.clear();

    debouncePending.concat(throttlePending).forEach(([eventName, pending]) => {
        window.djust.handleEvent(eventName, pending, true);
    });
}

window.djust.flushHandlerRateLimit = flushHandlerRateLimit;
