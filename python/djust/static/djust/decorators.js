/**
 * djust State Management Decorators (Testable Module)
 *
 * This file contains the decorator logic for @debounce, @throttle, @optimistic, and @cache.
 *
 * IMPORTANT: This module is used for testing and documentation.
 * The actual implementation runs as embedded JavaScript in live_view.py.
 * When making changes, update BOTH files to keep them in sync.
 *
 * Phase 2: @debounce, @throttle
 * Phase 3: @optimistic
 * Phase 5: @cache
 */

// ============================================================================
// State Management
// ============================================================================

export const debounceTimers = new Map(); // Map<handlerName, {timerId, firstCallTime}>
export const throttleState = new Map();  // Map<handlerName, {lastCall, timeoutId, pendingData}>
export const optimisticUpdates = new Map(); // Map<eventName, {element, originalState}>
export const pendingEvents = new Set(); // Set<eventName> (for loading indicators)
export const resultCache = new Map(); // Map<cacheKey, {result, expiresAt}>

/**
 * Clear all decorator state (useful for cleanup and testing)
 */
export function clearAllState() {
    debounceTimers.forEach(state => {
        if (state.timerId) clearTimeout(state.timerId);
    });
    debounceTimers.clear();

    throttleState.forEach(state => {
        if (state.timeoutId) clearTimeout(state.timeoutId);
    });
    throttleState.clear();

    optimisticUpdates.clear();
    pendingEvents.clear();
    resultCache.clear();
}

// ============================================================================
// Debounce Decorator (Phase 2)
// ============================================================================

/**
 * Debounce an event - delay until user stops triggering events
 *
 * @param {string} eventName - The event handler name
 * @param {object} eventData - Event parameters
 * @param {object} config - Debounce configuration
 * @param {number} config.wait - Wait time in seconds
 * @param {number} [config.max_wait] - Maximum wait time in seconds
 * @param {function} sendFn - Function to call when debounce fires
 */
export function debounceEvent(eventName, eventData, config, sendFn) {
    const { wait, max_wait } = config;
    const now = Date.now();

    // Get or create state
    let state = debounceTimers.get(eventName);
    if (!state) {
        state = { timerId: null, firstCallTime: now };
        debounceTimers.set(eventName, state);
    }

    // Clear existing timer
    if (state.timerId) {
        clearTimeout(state.timerId);
    }

    // Check if we've exceeded max_wait
    if (max_wait && (now - state.firstCallTime) >= (max_wait * 1000)) {
        // Force execution - max wait exceeded
        sendFn(eventName, eventData);
        debounceTimers.delete(eventName);
        if (globalThis.djustDebug) {
            console.log(`[LiveView:debounce] Force executing ${eventName} (max_wait exceeded)`);
        }
        return;
    }

    // Set new timer
    state.timerId = setTimeout(() => {
        sendFn(eventName, eventData);
        debounceTimers.delete(eventName);
        if (globalThis.djustDebug) {
            console.log(`[LiveView:debounce] Executing ${eventName} after ${wait}s wait`);
        }
    }, wait * 1000);

    if (globalThis.djustDebug) {
        console.log(`[LiveView:debounce] Debouncing ${eventName} (wait: ${wait}s, max_wait: ${max_wait || 'none'})`);
    }
}

// ============================================================================
// Throttle Decorator (Phase 2)
// ============================================================================

/**
 * Throttle an event - limit execution frequency
 *
 * @param {string} eventName - The event handler name
 * @param {object} eventData - Event parameters
 * @param {object} config - Throttle configuration
 * @param {number} config.interval - Minimum interval in seconds
 * @param {boolean} [config.leading=true] - Execute on leading edge
 * @param {boolean} [config.trailing=true] - Execute on trailing edge
 * @param {function} sendFn - Function to call when throttle fires
 */
export function throttleEvent(eventName, eventData, config, sendFn) {
    const { interval, leading, trailing } = config;
    const now = Date.now();

    if (!throttleState.has(eventName)) {
        // First call - execute immediately if leading=true
        if (leading) {
            sendFn(eventName, eventData);
            if (globalThis.djustDebug) {
                console.log(`[LiveView:throttle] Executing ${eventName} (leading edge)`);
            }
        }

        // Set up state
        const state = {
            lastCall: leading ? now : 0,
            timeoutId: null,
            pendingData: null
        };

        throttleState.set(eventName, state);

        // Schedule trailing call if needed
        if (trailing && !leading) {
            state.pendingData = eventData;
            state.timeoutId = setTimeout(() => {
                sendFn(eventName, state.pendingData);
                throttleState.delete(eventName);
                if (globalThis.djustDebug) {
                    console.log(`[LiveView:throttle] Executing ${eventName} (trailing edge - no leading)`);
                }
            }, interval * 1000);
        }

        return;
    }

    const state = throttleState.get(eventName);
    const elapsed = now - state.lastCall;

    if (elapsed >= (interval * 1000)) {
        // Enough time has passed - execute now
        sendFn(eventName, eventData);
        state.lastCall = now;
        state.pendingData = null;

        // Clear any pending trailing call
        if (state.timeoutId) {
            clearTimeout(state.timeoutId);
            state.timeoutId = null;
        }

        if (globalThis.djustDebug) {
            console.log(`[LiveView:throttle] Executing ${eventName} (interval elapsed: ${elapsed}ms)`);
        }
    } else if (trailing) {
        // Update pending data and reschedule trailing call
        state.pendingData = eventData;

        if (state.timeoutId) {
            clearTimeout(state.timeoutId);
        }

        const remaining = (interval * 1000) - elapsed;
        state.timeoutId = setTimeout(() => {
            if (state.pendingData) {
                sendFn(eventName, state.pendingData);
                if (globalThis.djustDebug) {
                    console.log(`[LiveView:throttle] Executing ${eventName} (trailing edge)`);
                }
            }
            throttleState.delete(eventName);
        }, remaining);

        if (globalThis.djustDebug) {
            console.log(`[LiveView:throttle] Throttled ${eventName} (${remaining}ms until trailing)`);
        }
    } else {
        if (globalThis.djustDebug) {
            console.log(`[LiveView:throttle] Dropped ${eventName} (within interval, no trailing)`);
        }
    }
}

// ============================================================================
// Optimistic Update Decorator (Phase 3)
// ============================================================================

/**
 * Apply optimistic DOM updates before server validation
 *
 * @param {string} eventName - The event handler name
 * @param {object} params - Event parameters
 * @param {HTMLElement} targetElement - The element to update
 */
export function applyOptimisticUpdate(eventName, params, targetElement) {
    if (!targetElement) {
        return;
    }

    // Save original state before update
    saveOptimisticState(eventName, targetElement);

    // Apply update based on element type
    if (targetElement.type === 'checkbox' || targetElement.type === 'radio') {
        optimisticToggle(targetElement, params);
    } else if (targetElement.tagName === 'INPUT' || targetElement.tagName === 'TEXTAREA') {
        optimisticInputUpdate(targetElement, params);
    } else if (targetElement.tagName === 'SELECT') {
        optimisticSelectUpdate(targetElement, params);
    } else if (targetElement.tagName === 'BUTTON') {
        optimisticButtonUpdate(targetElement, params);
    }

    // Add loading indicator
    targetElement.classList.add('optimistic-pending');
    pendingEvents.add(eventName);

    if (globalThis.djustDebug) {
        console.log('[LiveView:optimistic] Applied optimistic update:', eventName);
    }
}

/**
 * Save element's original state before optimistic update
 */
export function saveOptimisticState(eventName, element) {
    const originalState = {};

    if (element.type === 'checkbox' || element.type === 'radio') {
        originalState.checked = element.checked;
    }
    if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') {
        originalState.value = element.value;
    }
    if (element.tagName === 'BUTTON') {
        originalState.disabled = element.disabled;
        originalState.text = element.textContent;
    }

    optimisticUpdates.set(eventName, {
        element: element,
        originalState: originalState
    });
}

/**
 * Clear optimistic state and restore button state
 */
export function clearOptimisticState(eventName) {
    if (optimisticUpdates.has(eventName)) {
        const { element, originalState } = optimisticUpdates.get(eventName);

        // Remove loading indicator
        element.classList.remove('optimistic-pending');

        // Restore original button state (if button)
        if (element.tagName === 'BUTTON') {
            if (originalState.disabled !== undefined) {
                element.disabled = originalState.disabled;
            }
            if (originalState.text !== undefined) {
                element.textContent = originalState.text;
            }
        }

        optimisticUpdates.delete(eventName);
    }
    pendingEvents.delete(eventName);
}

/**
 * Revert optimistic update (on error)
 */
export function revertOptimisticUpdate(eventName) {
    if (!optimisticUpdates.has(eventName)) {
        return;
    }

    const { element, originalState } = optimisticUpdates.get(eventName);

    // Restore original state
    if (originalState.checked !== undefined) {
        element.checked = originalState.checked;
    }
    if (originalState.value !== undefined) {
        element.value = originalState.value;
    }
    if (originalState.disabled !== undefined) {
        element.disabled = originalState.disabled;
    }
    if (originalState.text !== undefined) {
        element.textContent = originalState.text;
    }

    // Add error indicator
    element.classList.remove('optimistic-pending');
    element.classList.add('optimistic-error');
    setTimeout(() => element.classList.remove('optimistic-error'), 2000);

    clearOptimisticState(eventName);

    if (globalThis.djustDebug) {
        console.log('[LiveView:optimistic] Reverted optimistic update:', eventName);
    }
}

/**
 * Optimistically toggle checkbox/radio
 */
export function optimisticToggle(element, params) {
    if (params.checked !== undefined) {
        element.checked = params.checked;
    } else {
        element.checked = !element.checked;
    }
}

/**
 * Optimistically update input value
 */
export function optimisticInputUpdate(element, params) {
    if (params.value !== undefined) {
        element.value = params.value;
    }
}

/**
 * Optimistically update select value
 */
export function optimisticSelectUpdate(element, params) {
    if (params.value !== undefined) {
        element.value = params.value;
    }
}

/**
 * Optimistically update button state (disable + loading text)
 */
export function optimisticButtonUpdate(element, params) {
    element.disabled = true;
    if (element.hasAttribute('data-loading-text')) {
        element.textContent = element.getAttribute('data-loading-text');
    }
}

// ============================================================================
// Cache Decorator (Phase 5)
// ============================================================================

/**
 * Cache event results with TTL-based expiration
 *
 * @param {string} eventName - The event handler name
 * @param {object} eventData - Event parameters
 * @param {object} config - Cache configuration
 * @param {number} [config.ttl=60] - Time-to-live in seconds (default: 60)
 * @param {string[]} [config.key_params=[]] - Parameters to include in cache key
 * @param {function} sendFn - Function to call on cache miss
 * @returns {Promise} Promise that resolves with cached or fresh result
 */
export function cacheEvent(eventName, eventData, config, sendFn) {
    const { ttl = 60, key_params = [] } = config;

    // Generate cache key from handler + params
    const cacheKey = generateCacheKey(eventName, eventData, key_params);

    // Check cache
    const cached = resultCache.get(cacheKey);
    if (cached && Date.now() < cached.expiresAt) {
        // Cache hit - no server call
        if (globalThis.djustDebug) {
            const remainingTtl = Math.round((cached.expiresAt - Date.now()) / 1000);
            console.log(`[LiveView:cache] Cache hit: ${cacheKey} (TTL: ${remainingTtl}s remaining)`);
        }
        return Promise.resolve(cached.result);
    }

    // Cache miss - call server
    if (globalThis.djustDebug) {
        console.log(`[LiveView:cache] Cache miss: ${cacheKey} (calling server)`);
    }

    return sendFn(eventName, eventData).then(result => {
        // Store in cache
        const expiresAt = Date.now() + (ttl * 1000);
        resultCache.set(cacheKey, {
            result,
            expiresAt
        });

        if (globalThis.djustDebug) {
            console.log(`[LiveView:cache] Cached result: ${cacheKey} (TTL: ${ttl}s)`);
        }

        return result;
    });
}

/**
 * Generate cache key from event name and parameters
 *
 * @param {string} eventName - The event handler name
 * @param {object} eventData - Event parameters
 * @param {string[]} keyParams - Parameters to include in cache key
 * @returns {string} Cache key
 */
export function generateCacheKey(eventName, eventData, keyParams) {
    if (keyParams.length === 0) {
        return eventName;
    }

    const paramValues = keyParams.map(param => {
        const value = eventData[param];
        if (value === undefined || value === null) {
            return '';
        }
        return String(value);
    }).join(':');

    return `${eventName}:${paramValues}`;
}

/**
 * Clear cache entries (useful for testing or invalidation)
 *
 * @param {string} [eventName] - If provided, clear only entries for this event
 */
export function clearCache(eventName) {
    if (!eventName) {
        // Clear all cache
        resultCache.clear();
        if (globalThis.djustDebug) {
            console.log('[LiveView:cache] Cleared all cache entries');
        }
        return;
    }

    // Clear specific event (all keys starting with eventName)
    let cleared = 0;
    for (const key of resultCache.keys()) {
        if (key === eventName || key.startsWith(eventName + ':')) {
            resultCache.delete(key);
            cleared++;
        }
    }

    if (globalThis.djustDebug) {
        console.log(`[LiveView:cache] Cleared ${cleared} cache entries for ${eventName}`);
    }
}

/**
 * Clean up expired cache entries
 */
export function cleanupExpiredCache() {
    const now = Date.now();
    let cleaned = 0;

    for (const [key, entry] of resultCache.entries()) {
        if (now >= entry.expiresAt) {
            resultCache.delete(key);
            cleaned++;
        }
    }

    if (globalThis.djustDebug && cleaned > 0) {
        console.log(`[LiveView:cache] Cleaned up ${cleaned} expired cache entries`);
    }

    return cleaned;
}
