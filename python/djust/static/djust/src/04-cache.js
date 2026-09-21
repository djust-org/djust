
// === HTTP Fallback LiveView Client ===

// Track VDOM version for synchronization.
// `let` (NOT const) — reassigned across multiple src/ modules (websocket
// handlers, response handler). ESLint's per-file scope misses cross-file
// mutation; auto-fix would break runtime (#1351).
// eslint-disable-next-line prefer-const
let clientVdomVersion = null;

// Event sequencing (#560): monotonic ref counter for matching event
// responses to requests, and buffering server-initiated pushes during
// pending events. Uses a Set to track multiple concurrent pending refs.
// Both transports allocate from this single monotonic sequence.
let _eventRefCounter = 0;
const _pendingEventRefs = new Set();     // refs of events awaiting server response
const _pendingEventNames = new Map();    // ref -> event name for pending events
const _pendingTriggerEls = new Map();    // ref -> trigger element for loading state
const _pendingEventResolvers = new Map(); // ref -> resolve() for Promise-based sendEvent (#1315)
const _pendingEventOwners = new Map();   // ref -> transport instance
const _pendingAsyncBatches = new Map();  // opaque server batch -> originating control
const _tickBuffer = [];                  // buffered server-initiated patches during pending events
const _tickBufferOwners = new WeakMap();

function hasPendingEventRequests(transport) {
    return [..._pendingEventOwners.values()].some(owner => owner === transport);
}

function bufferServerUpdate(transport, data) {
    _tickBufferOwners.set(data, transport);
    _tickBuffer.push(data);
}

function takeServerUpdates(transport, limit = Infinity) {
    const owned = [];
    for (let index = 0; index < _tickBuffer.length && owned.length < limit;) {
        // index is a bounded local array cursor, never a wire-provided key.
        // eslint-disable-next-line security/detect-object-injection
        const frame = _tickBuffer[index];
        if (_tickBufferOwners.get(frame) === transport) {
            owned.push(frame);
            _tickBuffer.splice(index, 1);
            _tickBufferOwners.delete(frame);
        } else index += 1;
    }
    return owned;
}

async function flushServerUpdates(transport) {
    // Leave unprocessed frames owned by the queue across application awaits.
    // Disconnect can discard them, and a newly started event can defer them.
    while (!hasPendingEventRequests(transport)) {
        const [frame] = takeServerUpdates(transport, 1);
        if (!frame) return;
        await handleServerResponse(frame, null, null);
        completeLegacyAsyncBatches(transport, frame);
    }
}

/** Register before sending: even an immediate reply must find its request. */
function registerEventRequest(transport, eventName, triggerElement) {
    const ref = ++_eventRefCounter;
    _pendingEventRefs.add(ref);
    _pendingEventNames.set(ref, eventName);
    _pendingTriggerEls.set(ref, triggerElement);
    _pendingEventOwners.set(ref, transport);
    transport.lastEventName = eventName;
    transport.lastTriggerElement = triggerElement;
    const promise = new Promise(resolve => _pendingEventResolvers.set(ref, resolve));
    return { ref, promise };
}

function rememberAsyncBatch(transport, data, eventName, trigger) {
    if (data.async_pending && data.async_batch == null && eventName) {
        _pendingAsyncBatches.set(Symbol('legacy'), {transport, eventName, trigger, legacy: true});
        return;
    }
    if (data.async_pending && typeof data.async_batch === 'string' &&
        data.async_batch.length > 0 && data.async_batch.length <= 128 &&
        !_pendingAsyncBatches.has(data.async_batch)) {
        _pendingAsyncBatches.set(data.async_batch, { transport, eventName, trigger });
    }
}

function completeLegacyAsyncBatches(transport, data) {
    if (data.source !== 'async' || data.async_pending || !data.event_name) return;
    for (const [token, batch] of _pendingAsyncBatches) {
        if (batch.legacy && batch.transport === transport && batch.eventName === data.event_name) {
            completeAsyncBatch(transport, token);
        }
    }
}

/** Completion is a separate control message, not a foreground acknowledgement. */
function completeAsyncBatch(transport, token) {
    const batch = _pendingAsyncBatches.get(token);
    if (!batch || batch.transport !== transport) return;
    _pendingAsyncBatches.delete(token);
    if (batch.eventName) globalLoadingManager.stopLoading(batch.eventName, batch.trigger);
}

/** Consume only an owned acknowledgement; unknown refs never use last-event state. */
function acknowledgeEventRequest(transport, data) {
    if (['async', 'tick', 'broadcast'].includes(data.source)) return null;
    let ref = data.ref;
    if (ref == null) {
        // Compatibility with old no-ref servers is unambiguous only with one
        // outstanding request. Never guess between overlapping requests.
        const owned = [..._pendingEventOwners].filter(([, owner]) => owner === transport);
        if (owned.length > 1) return null;
        if (owned.length === 1) ref = owned[0][0];
        else {
            const legacy = { eventName: transport.lastEventName, trigger: transport.lastTriggerElement };
            rememberAsyncBatch(transport, data, legacy.eventName, legacy.trigger);
            transport.lastEventName = null;
            transport.lastTriggerElement = null;
            return legacy;
        }
    }
    if (!_pendingEventRefs.has(ref) || _pendingEventOwners.get(ref) !== transport) return null;
    const eventName = _pendingEventNames.get(ref);
    const trigger = _pendingTriggerEls.get(ref);
    rememberAsyncBatch(transport, data, eventName, trigger);
    const resolve = _pendingEventResolvers.get(ref);
    _pendingEventRefs.delete(ref);
    _pendingEventNames.delete(ref);
    _pendingTriggerEls.delete(ref);
    _pendingEventResolvers.delete(ref);
    _pendingEventOwners.delete(ref);
    const remaining = [..._pendingEventOwners].filter(([, owner]) => owner === transport);
    const latest = remaining.length ? remaining[remaining.length - 1][0] : null;
    transport.lastEventName = latest == null ? null : _pendingEventNames.get(latest);
    transport.lastTriggerElement = latest == null ? null : _pendingTriggerEls.get(latest);
    if (resolve) resolve(data.cancelled ? null : data);
    return { eventName, trigger };
}

/** Cancel this transport's requests, never those of a replacement connection. */
function cancelEventRequests(transport, ref = null) {
    const refs = ref == null
        ? [..._pendingEventOwners].filter(([, owner]) => owner === transport).map(([key]) => key)
        : [ref];
    for (const key of refs) {
        const event = acknowledgeEventRequest(transport, { ref: key, cancelled: true });
        if (event?.eventName) globalLoadingManager.stopLoading(event.eventName, event.trigger);
    }
    if (ref == null) {
        for (const [token, batch] of _pendingAsyncBatches) {
            if (batch.transport === transport) completeAsyncBatch(transport, token);
        }
    }
}

// State management for decorators
const debounceTimers = new Map(); // Map<handlerName, {timerId, firstCallTime}>
const throttleState = new Map();  // Map<handlerName, {lastCall, timeoutId, pendingData}>
const optimisticUpdates = new Map(); // Map<eventName, {element, originalState}>
const pendingEvents = new Set(); // Set<eventName> (for loading indicators)
const resultCache = new Map(); // Map<cacheKey, {patches, expiresAt}>
const pendingCacheRequests = new Map(); // Map<requestId, {cacheKey, ttl, timeoutId}>
const CACHE_MAX_SIZE = 100; // Maximum number of cached entries (LRU eviction)
const PENDING_CACHE_TIMEOUT = 30000; // Cleanup pending cache requests after 30 seconds

// Cache configuration from server (event_name -> {ttl, key_params})
const cacheConfig = new Map();

/**
 * Add entry to cache with LRU eviction
 * @param {string} cacheKey - Cache key
 * @param {Object} value - Value to cache {patches, expiresAt}
 */
function addToCache(cacheKey, value) {
    // If key exists, delete it first to update insertion order (for LRU)
    if (resultCache.has(cacheKey)) {
        resultCache.delete(cacheKey);
    }

    // Evict oldest entries if cache is full
    while (resultCache.size >= CACHE_MAX_SIZE) {
        const oldestKey = resultCache.keys().next().value;
        resultCache.delete(oldestKey);
        if (globalThis.djustDebug) {
            djLog(`[LiveView:cache] Evicted (LRU): ${oldestKey}`);
        }
    }

    resultCache.set(cacheKey, value);
}

/**
 * Set cache configuration for handlers (called during mount)
 * @param {Object} config - Map of handler names to cache config {ttl, key_params}
 */
function setCacheConfig(config) {
    if (!config) return;

    Object.entries(config).forEach(([handlerName, handlerConfig]) => {
        cacheConfig.set(handlerName, handlerConfig);
        if (globalThis.djustDebug) {
            djLog(`[LiveView:cache] Configured cache for ${handlerName}:`, handlerConfig);
        }
    });
}

// Expose setCacheConfig under djust namespace
window.djust.setCacheConfig = setCacheConfig;
// Backward compatibility
window.setCacheConfig = setCacheConfig;

/**
 * Build a cache key from event name and parameters.
 *
 * Cache keys are deterministic: the same event name + params will always produce
 * the same key. This is intentional - it allows caching across repeated requests.
 *
 * Cache keys are NOT namespaced per view. The page view's own mount clears the
 * cache (`installMountEventConfig`, 05-handler-rate-limit.js), so entries do not
 * survive a navigation — but within one page they are shared: sticky children,
 * components, and lazily-hydrated sibling views all mount onto the same socket
 * and keep their entries (#2721). Two views on one page whose handlers share a
 * name and are called with the same params therefore share a cache entry. Use
 * `key_params` in the `@cache` decorator to disambiguate if needed.
 *
 * @param {string} eventName - The event handler name
 * @param {Object} params - Event parameters
 * @param {Array<string>} keyParams - Which params to include in key (if specified)
 * @returns {string} Cache key in format "eventName:param1=value1:param2=value2"
 */
function buildCacheKey(eventName, params, keyParams = null) {
    // Filter out internal params (starting with _)
    const cacheParams = {};
    let usedKeyParams = false;

    if (keyParams && keyParams.length > 0) {
        // Try to use specified key params
        keyParams.forEach(key => {
            if (Object.prototype.hasOwnProperty.call(params, key)) {
                // eslint-disable-next-line security/detect-object-injection
                cacheParams[key] = params[key];
                usedKeyParams = true;
            }
        });
    }

    // If no keyParams specified OR none of the keyParams were found in params,
    // fall back to using all non-internal params for the cache key
    if (!usedKeyParams) {
        Object.keys(params).forEach(key => {
            if (!key.startsWith('_')) {
                // eslint-disable-next-line security/detect-object-injection
                cacheParams[key] = params[key];
            }
        });
    }

    // Build key: eventName:param1=value1:param2=value2
    const paramParts = Object.keys(cacheParams)
        .sort()
        // eslint-disable-next-line security/detect-object-injection
        .map(k => `${k}=${JSON.stringify(cacheParams[k])}`)
        .join(':');

    return paramParts ? `${eventName}:${paramParts}` : eventName;
}

/**
 * Check if there's a valid cached result for this request
 * @param {string} cacheKey - The cache key to check
 * @returns {Object|null} Cached data if valid, null otherwise
 */
function getCachedResult(cacheKey) {
    const cached = resultCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) {
        return cached;
    }
    // Clean up expired entry
    if (cached) {
        resultCache.delete(cacheKey);
    }
    return null;
}

/**
 * Clear all cached results.
 * Useful when data has changed and cached responses are stale.
 */
function clearCache() {
    const size = resultCache.size;
    resultCache.clear();
    if (globalThis.djustDebug) {
        djLog(`[LiveView:cache] Cleared all ${size} cached entries`);
    }
}

/**
 * Invalidate cache entries matching a pattern.
 * @param {string|RegExp} pattern - Event name prefix or regex to match against cache keys
 * @returns {number} Number of entries invalidated
 *
 * @example
 * // Invalidate all cache entries for "search" handler
 * window.djust.invalidateCache('search');
 *
 * @example
 * // Invalidate using regex pattern
 * window.djust.invalidateCache(/^user_/);
 */
function invalidateCache(pattern) {
    let count = 0;
    const isRegex = pattern instanceof RegExp;

    for (const key of resultCache.keys()) {
        const matches = isRegex ? pattern.test(key) : key.startsWith(pattern);
        if (matches) {
            resultCache.delete(key);
            count++;
        }
    }

    if (globalThis.djustDebug) {
        djLog(`[LiveView:cache] Invalidated ${count} entries matching: ${pattern}`);
    }

    return count;
}

// Expose cache invalidation API under djust namespace
window.djust.clearCache = clearCache;
window.djust.invalidateCache = invalidateCache;

// Event sequencing (#560): expose accessors for testing
window.djust._getEventSeqState = function() {
    return {
        // Legacy scalar accessors (null when empty, first ref when non-empty)
        pendingEventRef: _pendingEventRefs.size > 0 ? Array.from(_pendingEventRefs)[0] : null,
        pendingEventName: _pendingEventRefs.size > 0
            ? _pendingEventNames.get(Array.from(_pendingEventRefs)[0]) || null
            : null,
        // Set-based accessors
        pendingEventRefs: Array.from(_pendingEventRefs),
        tickBufferLength: _tickBuffer.length,
        eventRefCounter: _eventRefCounter,
    };
};
window.djust._pushTickBuffer = function(data, transport) {
    bufferServerUpdate(transport || _pendingEventOwners.values().next().value || liveViewWS, data);
};
