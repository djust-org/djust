/**
 * djust State Management Decorators (Testable Module)
 *
 * This file contains the decorator logic for @debounce, @throttle, @optimistic, @cache, @client_state, and DraftMode.
 *
 * IMPORTANT: This module is used for testing and documentation.
 * The actual implementation runs as embedded JavaScript in live_view.py.
 * When making changes, update BOTH files to keep them in sync.
 *
 * Phase 2: @debounce, @throttle
 * Phase 3: @optimistic
 * Phase 5: @cache, @client_state, DraftMode
 */

// ============================================================================
// State Management
// ============================================================================

export const debounceTimers = new Map(); // Map<handlerName, {timerId, firstCallTime}>
export const throttleState = new Map();  // Map<handlerName, {lastCall, timeoutId, pendingData}>
export const optimisticUpdates = new Map(); // Map<eventName, {element, originalState}>
export const pendingEvents = new Set(); // Set<eventName> (for loading indicators)
export const resultCache = new Map(); // Map<cacheKey, {result, expiresAt}>

// ============================================================================
// StateBus - Client-side State Coordination (Phase 5)
// ============================================================================

/**
 * StateBus provides client-side state coordination across multiple components.
 *
 * Use cases:
 * - Multi-component dashboards where components react to shared state
 * - Search filters that affect multiple result lists
 * - Shopping cart count displayed in header and sidebar
 * - User preferences synced across UI elements
 */
export class StateBus {
    constructor() {
        this.state = new Map(); // Map<key, value>
        this.subscribers = new Map(); // Map<key, Set<callback>>
    }

    /**
     * Set state value and notify all subscribers
     * @param {string} key - State key
     * @param {any} value - State value
     */
    set(key, value) {
        const oldValue = this.state.get(key);
        this.state.set(key, value);

        if (globalThis.djustDebug) {
            console.log(`[StateBus] Set: ${key} =`, value, `(was:`, oldValue, `)`);
        }

        this.notify(key, value, oldValue);
    }

    /**
     * Get current state value
     * @param {string} key - State key
     * @returns {any} Current value or undefined
     */
    get(key) {
        return this.state.get(key);
    }

    /**
     * Subscribe to state changes
     * @param {string} key - State key to watch
     * @param {function} callback - Callback(newValue, oldValue)
     * @returns {function} Unsubscribe function
     */
    subscribe(key, callback) {
        if (!this.subscribers.has(key)) {
            this.subscribers.set(key, new Set());
        }
        this.subscribers.get(key).add(callback);

        if (globalThis.djustDebug) {
            console.log(`[StateBus] Subscribed to: ${key} (${this.subscribers.get(key).size} subscribers)`);
        }

        // Return unsubscribe function
        return () => {
            const subs = this.subscribers.get(key);
            if (subs) {
                subs.delete(callback);
                if (globalThis.djustDebug) {
                    console.log(`[StateBus] Unsubscribed from: ${key} (${subs.size} remaining)`);
                }
            }
        };
    }

    /**
     * Notify all subscribers of a state change
     * @param {string} key - State key
     * @param {any} newValue - New value
     * @param {any} oldValue - Old value
     */
    notify(key, newValue, oldValue) {
        const callbacks = this.subscribers.get(key) || new Set();

        if (callbacks.size > 0 && globalThis.djustDebug) {
            console.log(`[StateBus] Notifying ${callbacks.size} subscribers of: ${key}`);
        }

        callbacks.forEach(callback => {
            try {
                callback(newValue, oldValue);
            } catch (error) {
                console.error(`[StateBus] Subscriber error for ${key}:`, error);
            }
        });
    }

    /**
     * Clear all state and subscribers
     */
    clear() {
        this.state.clear();
        this.subscribers.clear();

        if (globalThis.djustDebug) {
            console.log('[StateBus] Cleared all state');
        }
    }

    /**
     * Get all current state (for debugging)
     * @returns {Object} Current state as plain object
     */
    getAll() {
        return Object.fromEntries(this.state.entries());
    }
}

// Global StateBus instance
export const globalStateBus = new StateBus();

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
    globalStateBus.clear();
    globalLoadingManager.clear();
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

// ============================================================================
// Client State Decorator (Phase 5)
// ============================================================================

/**
 * Coordinate state across multiple components via StateBus
 *
 * @param {string} eventName - The event handler name
 * @param {object} eventData - Event parameters
 * @param {object} config - Client state configuration
 * @param {string} config.state_key - State key to coordinate (required)
 * @param {function} sendFn - Function to call after updating state
 * @returns {Promise} Promise that resolves when server responds
 */
export function clientStateEvent(eventName, eventData, config, sendFn) {
    const { state_key } = config;

    if (!state_key) {
        console.error('[LiveView:client_state] Missing state_key in config');
        return sendFn(eventName, eventData);
    }

    // Extract state value from event data
    // Common patterns: {value: ...}, {checked: ...}, or direct value
    const stateValue = eventData.value !== undefined ? eventData.value :
                       eventData.checked !== undefined ? eventData.checked :
                       eventData;

    if (globalThis.djustDebug) {
        console.log(`[LiveView:client_state] Setting ${state_key} =`, stateValue);
    }

    // Update StateBus (this will notify all subscribers)
    globalStateBus.set(state_key, stateValue);

    // Call server to persist state
    return sendFn(eventName, eventData);
}

// ============================================================================
// DraftMode - localStorage Auto-save (Phase 5)
// ============================================================================

/**
 * DraftManager provides automatic draft saving to localStorage.
 *
 * Features:
 * - Auto-save every 500ms (debounced)
 * - Auto-restore on page load
 * - Clear draft on successful submit
 * - Works with input/textarea/contenteditable elements
 */
export class DraftManager {
    constructor() {
        this.saveTimers = new Map(); // Map<draftKey, timerId>
        this.saveDelay = 500; // 500ms debounce
    }

    /**
     * Save draft data to localStorage (debounced)
     * @param {string} draftKey - Unique key for this draft
     * @param {object} data - Form data to save
     */
    saveDraft(draftKey, data) {
        // Clear existing timer
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
        }

        // Set new debounced timer
        const timerId = setTimeout(() => {
            try {
                const draftData = {
                    data,
                    timestamp: Date.now()
                };
                localStorage.setItem(`djust_draft_${draftKey}`, JSON.stringify(draftData));

                if (globalThis.djustDebug) {
                    console.log(`[DraftMode] Saved draft: ${draftKey}`, data);
                }
            } catch (error) {
                console.error(`[DraftMode] Failed to save draft ${draftKey}:`, error);
            }
            this.saveTimers.delete(draftKey);
        }, this.saveDelay);

        this.saveTimers.set(draftKey, timerId);
    }

    /**
     * Load draft data from localStorage
     * @param {string} draftKey - Unique key for this draft
     * @returns {object|null} Draft data or null if not found
     */
    loadDraft(draftKey) {
        try {
            const stored = localStorage.getItem(`djust_draft_${draftKey}`);
            if (!stored) {
                return null;
            }

            const draftData = JSON.parse(stored);

            if (globalThis.djustDebug) {
                const age = Math.round((Date.now() - draftData.timestamp) / 1000);
                console.log(`[DraftMode] Loaded draft: ${draftKey} (${age}s old)`, draftData.data);
            }

            return draftData.data;
        } catch (error) {
            console.error(`[DraftMode] Failed to load draft ${draftKey}:`, error);
            return null;
        }
    }

    /**
     * Clear draft from localStorage
     * @param {string} draftKey - Unique key for this draft
     */
    clearDraft(draftKey) {
        // Clear any pending save timer
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
            this.saveTimers.delete(draftKey);
        }

        try {
            localStorage.removeItem(`djust_draft_${draftKey}`);

            if (globalThis.djustDebug) {
                console.log(`[DraftMode] Cleared draft: ${draftKey}`);
            }
        } catch (error) {
            console.error(`[DraftMode] Failed to clear draft ${draftKey}:`, error);
        }
    }

    /**
     * Get all draft keys from localStorage
     * @returns {string[]} Array of draft keys
     */
    getAllDraftKeys() {
        const keys = [];
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith('djust_draft_')) {
                    keys.push(key.replace('djust_draft_', ''));
                }
            }
        } catch (error) {
            console.error('[DraftMode] Failed to get draft keys:', error);
        }
        return keys;
    }

    /**
     * Clear all drafts from localStorage
     */
    clearAllDrafts() {
        const keys = this.getAllDraftKeys();
        keys.forEach(key => this.clearDraft(key));

        if (globalThis.djustDebug) {
            console.log(`[DraftMode] Cleared all ${keys.length} drafts`);
        }
    }
}

// Global DraftManager instance
export const globalDraftManager = new DraftManager();

// ============================================================================
// Loading Attribute Support (Phase 5)
// ============================================================================

/**
 * LoadingManager handles dj-loading HTML attributes for showing/hiding elements
 * and adding/removing classes during async operations.
 *
 * Supported modifiers:
 * - dj-loading.disable: Disable element during loading
 * - dj-loading.class="class-name": Add class during loading
 * - dj-loading.show: Show element during loading (display: block/inline)
 * - dj-loading.hide: Hide element during loading (display: none)
 *
 * Example:
 *   <button dj-click="save" dj-loading.disable>Save</button>
 *   <button dj-click="save" dj-loading.class="opacity-50">Save</button>
 *   <div dj-loading.show>Saving...</div>
 *   <div dj-loading.hide>Form content</div>
 */
export class LoadingManager {
    constructor() {
        this.loadingElements = new Map(); // Map<element, LoadingState>
        this.pendingEvents = new Set();   // Set<eventName>
    }

    /**
     * Register an element with dj-loading attributes
     * @param {HTMLElement} element - Element with dj-loading attribute
     * @param {string} eventName - Event name that triggers loading
     */
    register(element, eventName) {
        const attributes = element.attributes;
        const loadingConfig = {
            eventName,
            modifiers: [],
            originalState: {}
        };

        // Parse dj-loading.* attributes
        for (let i = 0; i < attributes.length; i++) {
            const attr = attributes[i];
            const match = attr.name.match(/^dj-loading\.(.+)$/);
            if (match) {
                const modifier = match[1];

                if (modifier === 'disable') {
                    loadingConfig.modifiers.push({ type: 'disable' });
                    loadingConfig.originalState.disabled = element.disabled;
                } else if (modifier === 'show') {
                    loadingConfig.modifiers.push({ type: 'show' });
                    loadingConfig.originalState.display = element.style.display;
                } else if (modifier === 'hide') {
                    loadingConfig.modifiers.push({ type: 'hide' });
                    loadingConfig.originalState.display = element.style.display;
                } else if (modifier === 'class') {
                    // For dj-loading.class="className", value is in attr.value
                    const className = attr.value;
                    if (className) {
                        loadingConfig.modifiers.push({ type: 'class', value: className });
                    }
                }
            }
        }

        if (loadingConfig.modifiers.length > 0) {
            this.loadingElements.set(element, loadingConfig);

            if (globalThis.djustDebug) {
                console.log(`[Loading] Registered element for "${eventName}":`, loadingConfig);
            }
        }
    }

    /**
     * Mark an event as pending (start loading)
     * @param {string} eventName - Event name
     */
    startLoading(eventName) {
        this.pendingEvents.add(eventName);

        if (globalThis.djustDebug) {
            console.log(`[Loading] Started: ${eventName}`);
        }

        // Apply loading state to all elements watching this event
        this.loadingElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                this.applyLoadingState(element, config);
            }
        });
    }

    /**
     * Mark an event as complete (stop loading)
     * @param {string} eventName - Event name
     */
    stopLoading(eventName) {
        this.pendingEvents.delete(eventName);

        if (globalThis.djustDebug) {
            console.log(`[Loading] Stopped: ${eventName}`);
        }

        // Remove loading state from all elements watching this event
        this.loadingElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                this.removeLoadingState(element, config);
            }
        });
    }

    /**
     * Apply loading state to an element
     * @param {HTMLElement} element - Target element
     * @param {Object} config - Loading configuration
     */
    applyLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = true;
            } else if (modifier.type === 'show') {
                element.style.display = element.getAttribute('data-loading-display') || 'block';
            } else if (modifier.type === 'hide') {
                element.style.display = 'none';
            } else if (modifier.type === 'class') {
                element.classList.add(modifier.value);
            }
        });

        if (globalThis.djustDebug) {
            console.log(`[Loading] Applied to element:`, element, config);
        }
    }

    /**
     * Remove loading state from an element
     * @param {HTMLElement} element - Target element
     * @param {Object} config - Loading configuration
     */
    removeLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = config.originalState.disabled || false;
            } else if (modifier.type === 'show' || modifier.type === 'hide') {
                element.style.display = config.originalState.display || '';
            } else if (modifier.type === 'class') {
                element.classList.remove(modifier.value);
            }
        });

        if (globalThis.djustDebug) {
            console.log(`[Loading] Removed from element:`, element);
        }
    }

    /**
     * Check if an event is currently loading
     * @param {string} eventName - Event name
     * @returns {boolean}
     */
    isLoading(eventName) {
        return this.pendingEvents.has(eventName);
    }

    /**
     * Clear all loading elements and state
     */
    clear() {
        this.loadingElements.clear();
        this.pendingEvents.clear();

        if (globalThis.djustDebug) {
            console.log('[Loading] Cleared all state');
        }
    }
}

// Global LoadingManager instance
export const globalLoadingManager = new LoadingManager();

/**
 * Collect form data from a container element
 * @param {HTMLElement} container - Container with form fields
 * @returns {object} Form data as key-value pairs
 */
export function collectFormData(container) {
    const data = {};

    // Collect from input/textarea elements
    const fields = container.querySelectorAll('input, textarea, select');
    fields.forEach(field => {
        if (field.name) {
            if (field.type === 'checkbox') {
                data[field.name] = field.checked;
            } else if (field.type === 'radio') {
                if (field.checked) {
                    data[field.name] = field.value;
                }
            } else {
                data[field.name] = field.value;
            }
        }
    });

    // Collect from contenteditable elements
    const editables = container.querySelectorAll('[contenteditable="true"]');
    editables.forEach(editable => {
        const name = editable.getAttribute('name') || editable.id;
        if (name) {
            data[name] = editable.innerHTML;
        }
    });

    return data;
}

/**
 * Restore form data to a container element
 * @param {HTMLElement} container - Container with form fields
 * @param {object} data - Form data to restore
 */
export function restoreFormData(container, data) {
    if (!data) return;

    Object.entries(data).forEach(([name, value]) => {
        // Try to find field by name
        let field = container.querySelector(`[name="${name}"]`);

        // Try to find by ID if name didn't work
        if (!field) {
            field = container.querySelector(`#${name}`);
        }

        if (!field) return;

        // Restore based on field type
        if (field.tagName === 'INPUT') {
            if (field.type === 'checkbox') {
                field.checked = value;
            } else if (field.type === 'radio') {
                if (field.value === value) {
                    field.checked = true;
                }
            } else {
                field.value = value;
            }
        } else if (field.tagName === 'TEXTAREA') {
            field.value = value;
        } else if (field.tagName === 'SELECT') {
            field.value = value;
        } else if (field.getAttribute('contenteditable') === 'true') {
            field.innerHTML = value;
        }
    });

    if (globalThis.djustDebug) {
        console.log('[DraftMode] Restored form data:', data);
    }
}
