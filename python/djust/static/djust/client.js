// djust - WebSocket + HTTP Fallback Client

// ============================================================================
// Global Namespace
// ============================================================================

// Create djust namespace at the top to ensure it's available for all exports
window.djust = window.djust || {};

// ============================================================================
// Double-Load Guard
// ============================================================================
// Prevent double execution when client.js is included in both base template
// (for TurboNav compatibility) and injected by LiveView.
if (window._djustClientLoaded) {
    console.log('[LiveView] client.js already loaded, skipping duplicate initialization');
} else {
window._djustClientLoaded = true;

// ============================================================================
// Security Constants
// ============================================================================
// Dangerous keys that could cause prototype pollution attacks
const UNSAFE_KEYS = ['__proto__', 'constructor', 'prototype'];

// ============================================================================
// DOM Helper Functions
// ============================================================================

/**
 * Find the closest parent component ID by walking up the DOM tree.
 * Used by event handlers to determine which component an event originated from.
 * @param {HTMLElement} element - Starting element
 * @returns {string|null} - Component ID or null if not found
 */
function getComponentId(element) {
    let currentElement = element;
    while (currentElement && currentElement !== document.body) {
        if (currentElement.dataset.componentId) {
            return currentElement.dataset.componentId;
        }
        currentElement = currentElement.parentElement;
    }
    return null;
}

// ============================================================================
// TurboNav Integration - Early Registration
// ============================================================================
// Register turbo:load handler early to ensure it's ready before TurboNav navigation.
// This handler will be called when TurboNav swaps page content.

// Track if we've been initialized via DOMContentLoaded
// Exposed on window for external detection (e.g., Playwright E2E tests)
window.djustInitialized = false;

// Track pending turbo:load reinit
let pendingTurboReinit = false;

window.addEventListener('turbo:load', function(event) {
    console.log('[LiveView:TurboNav] turbo:load event received!');
    console.log('[LiveView:TurboNav] djustInitialized:', window.djustInitialized);

    if (!window.djustInitialized) {
        // client.js hasn't finished initializing yet, defer reinit
        console.log('[LiveView:TurboNav] Deferring reinit until DOMContentLoaded completes');
        pendingTurboReinit = true;
        return;
    }

    try {
        reinitLiveViewForTurboNav();
    } catch (error) {
        console.error('[LiveView:TurboNav] Error during reinit:', error);
    }
});

// Reinitialize LiveView after TurboNav navigation
function reinitLiveViewForTurboNav() {
    console.log('[LiveView:TurboNav] Reinitializing LiveView...');

    // Disconnect existing WebSocket
    if (liveViewWS) {
        console.log('[LiveView:TurboNav] Disconnecting existing WebSocket');
        liveViewWS.disconnect();
        liveViewWS = null;
    }

    // Reset client VDOM version
    clientVdomVersion = null;

    // Clear lazy hydration state
    lazyHydrationManager.init();

    // Find all LiveView containers in the new content
    const allContainers = document.querySelectorAll('[data-djust-view]');
    const lazyContainers = document.querySelectorAll('[data-djust-view][data-djust-lazy]');
    const eagerContainers = document.querySelectorAll('[data-djust-view]:not([data-djust-lazy])');

    console.log(`[LiveView:TurboNav] Found ${allContainers.length} containers (${lazyContainers.length} lazy, ${eagerContainers.length} eager)`);

    // Register lazy containers with the lazy hydration manager
    lazyContainers.forEach(container => {
        lazyHydrationManager.register(container);
    });

    // Only initialize WebSocket if there are eager containers
    if (eagerContainers.length > 0) {
        console.log('[LiveView:TurboNav] Initializing new WebSocket connection');
        // Initialize WebSocket
        liveViewWS = new LiveViewWebSocket();
        window.djust.liveViewInstance = liveViewWS;
        liveViewWS.connect();

        // Start heartbeat
        liveViewWS.startHeartbeat();
    } else if (lazyContainers.length > 0) {
        console.log('[LiveView:TurboNav] Deferring WebSocket connection until lazy elements are needed');
    } else {
        console.log('[LiveView:TurboNav] No LiveView containers found, skipping WebSocket');
    }

    // Re-bind events
    bindLiveViewEvents();

    // Re-scan dj-loading attributes
    globalLoadingManager.scanAndRegister();

    console.log('[LiveView:TurboNav] Reinitialization complete');
}

// ============================================================================
// Centralized Response Handler (WebSocket + HTTP)
// ============================================================================

/**
 * Centralized server response handler for both WebSocket and HTTP fallback.
 * Eliminates code duplication and ensures consistent behavior.
 *
 * @param {Object} data - Server response data
 * @param {string} eventName - Name of the event that triggered this response
 * @param {HTMLElement} triggerElement - Element that triggered the event
 * @returns {boolean} - True if handled successfully, false otherwise
 */
function handleServerResponse(data, eventName, triggerElement, context = {}) {
    try {
        // Handle cache storage (from @cache decorator)
        if (data.cache_request_id && pendingCacheRequests.has(data.cache_request_id)) {
            const { cacheKey, ttl, timeoutId } = pendingCacheRequests.get(data.cache_request_id);
            // Clear the cleanup timeout since we received a response
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            const expiresAt = Date.now() + (ttl * 1000);
            addToCache(cacheKey, {
                patches: data.patches,
                expiresAt
            });
            if (globalThis.djustDebug) {
                console.log(`[LiveView:cache] Cached patches: ${cacheKey} (TTL: ${ttl}s)`);
            }
            pendingCacheRequests.delete(data.cache_request_id);
        }

        // Handle version tracking and mismatch
        if (data.version !== undefined) {
            if (clientVdomVersion === null) {
                clientVdomVersion = data.version;
                console.log('[LiveView] Initialized VDOM version:', clientVdomVersion);
            } else if (clientVdomVersion !== data.version - 1 && !data.hotreload) {
                // Version mismatch - force full reload (skip check for hot reload)
                console.warn('[LiveView] VDOM version mismatch!');
                console.warn(`  Expected v${clientVdomVersion + 1}, got v${data.version}`);

                clearOptimisticState(eventName);

                if (data.html) {
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(data.html, 'text/html');
                    const liveviewRoot = getLiveViewRoot();
                    const newRoot = doc.querySelector('[data-djust-root]') || doc.body;
                    liveviewRoot.innerHTML = newRoot.innerHTML;
                    clientVdomVersion = data.version;
                    initReactCounters();
                    initTodoItems();
                    bindLiveViewEvents();
                }

                globalLoadingManager.stopLoading(eventName, triggerElement);
                return true;
            }
            clientVdomVersion = data.version;
        }

        // Handle optimistic updates
        const { optimisticUpdateId, targetSelector } = context;
        if (optimisticUpdateId && window.djust.optimistic) {
            if (data.error) {
                // Server returned an error, revert optimistic update
                window.djust.optimistic.revertOptimisticUpdate(optimisticUpdateId);
                showFlashMessage(data.error_message || 'An error occurred', 'error');
            } else {
                // Success, clear the optimistic update (server state is now applied)
                window.djust.optimistic.clearOptimisticUpdate(optimisticUpdateId);
            }
        }

        // Clear legacy optimistic state
        clearOptimisticState(eventName);

        // Global cleanup of lingering optimistic-pending classes
        document.querySelectorAll('.optimistic-pending').forEach(el => {
            el.classList.remove('optimistic-pending');
        });

        // Apply patches (efficient incremental updates)
        if (data.patches && Array.isArray(data.patches) && data.patches.length > 0) {
            console.log('[LiveView] Applying', data.patches.length, 'patches');

            // Store timing info globally for debug panel access
            window._lastPatchTiming = data.timing;
            // Store comprehensive performance data if available
            window._lastPerformanceData = data.performance;

            const success = applyPatches(data.patches, targetSelector);
            if (success === false) {
                console.error('[LiveView] Patches failed, reloading page...');
                globalLoadingManager.stopLoading(eventName, triggerElement);
                window.location.reload();
                return false;
            }

            console.log('[LiveView] Patches applied successfully');

            // Final cleanup
            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            initReactCounters();
            initTodoItems();
            bindLiveViewEvents();
        }
        // Apply full HTML update (fallback)
        else if (data.html) {
            console.log('[LiveView] Applying full HTML update');
            const parser = new DOMParser();
            const doc = parser.parseFromString(data.html, 'text/html');
            const liveviewRoot = getLiveViewRoot();
            const newRoot = doc.querySelector('[data-djust-root]') || doc.body;

            // Handle dj-update="append|prepend|ignore" for efficient list updates
            // This preserves existing DOM elements and only adds/updates new content
            applyDjUpdateElements(liveviewRoot, newRoot);

            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            initReactCounters();
            initTodoItems();
            bindLiveViewEvents();
        } else {
            console.warn('[LiveView] Response has neither patches nor html!', data);
        }

        // Handle form reset
        if (data.reset_form) {
            console.log('[LiveView] Resetting form');
            const form = document.querySelector('[data-djust-root] form');
            if (form) form.reset();
        }

        // Stop loading state
        globalLoadingManager.stopLoading(eventName, triggerElement);
        return true;

    } catch (error) {
        console.error('[LiveView] Error in handleServerResponse:', error);
        globalLoadingManager.stopLoading(eventName, triggerElement);
        return false;
    }
}

// ============================================================================
// WebSocket LiveView Client
// ============================================================================

class LiveViewWebSocket {
    constructor() {
        this.ws = null;
        this.sessionId = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.viewMounted = false;
        this.enabled = true;  // Can be disabled to use HTTP fallback
        this.lastEventName = null;  // Phase 5: Track last event for loading state
        this.lastTriggerElement = null;  // Phase 5: Track trigger element for scoped loading

        // WebSocket statistics tracking (Phase 2.1: WebSocket Inspector)
        this.stats = {
            sent: 0,           // Total messages sent
            received: 0,       // Total messages received
            sentBytes: 0,      // Total bytes sent
            receivedBytes: 0,  // Total bytes received
            reconnections: 0,  // Number of reconnections
            messages: [],      // Recent message history (last 50)
            connectedAt: null, // Timestamp of current connection
        };
    }

    /**
     * Cleanly disconnect the WebSocket for TurboNav navigation
     */
    disconnect() {
        console.log('[LiveView] Disconnecting for navigation...');

        // Stop heartbeat
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }

        // Clear reconnect attempts so we don't auto-reconnect
        this.reconnectAttempts = this.maxReconnectAttempts;

        // Close WebSocket if open
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.close();
        }

        this.ws = null;
        this.sessionId = null;
        this.viewMounted = false;
        this.vdomVersion = null;
    }

    connect(url = null) {
        if (!this.enabled) return;

        if (!url) {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;
            url = `${protocol}//${host}/ws/live/`;
        }

        console.log('[LiveView] Connecting to WebSocket:', url);
        this.ws = new WebSocket(url);

        this.ws.onopen = (_event) => {
            console.log('[LiveView] WebSocket connected');
            this.reconnectAttempts = 0;

            // Track reconnections (Phase 2.1: WebSocket Inspector)
            if (this.stats.connectedAt !== null) {
                this.stats.reconnections++;
            }
            this.stats.connectedAt = Date.now();
        };

        this.ws.onclose = (_event) => {
            console.log('[LiveView] WebSocket disconnected');
            this.viewMounted = false;

            // Clear all decorator state on disconnect
            // Phase 2: Debounce timers
            debounceTimers.forEach(state => {
                if (state.timerId) {
                    clearTimeout(state.timerId);
                }
            });
            debounceTimers.clear();

            // Phase 2: Throttle timers
            throttleState.forEach(state => {
                if (state.timeoutId) {
                    clearTimeout(state.timeoutId);
                }
            });
            throttleState.clear();

            // Phase 3: Optimistic updates
            optimisticUpdates.clear();
            pendingEvents.clear();

            // Remove loading indicators from DOM
            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                console.log(`[LiveView] Reconnecting in ${delay}ms...`);
                setTimeout(() => this.connect(url), delay);
            } else {
                console.warn('[LiveView] Max reconnection attempts reached. Falling back to HTTP mode.');
                this.enabled = false;
            }
        };

        this.ws.onerror = (error) => {
            console.error('[LiveView] WebSocket error:', error);
        };

        this.ws.onmessage = (event) => {
            try {
                // Track received message (Phase 2.1: WebSocket Inspector)
                const messageBytes = event.data.length;
                this.stats.received++;
                this.stats.receivedBytes += messageBytes;

                const data = JSON.parse(event.data);

                // Add to message history
                this.trackMessage({
                    direction: 'received',
                    type: data.type,
                    size: messageBytes,
                    timestamp: Date.now(),
                    data: data
                });

                this.handleMessage(data);
            } catch (error) {
                console.error('[LiveView] Failed to parse message:', error);
            }
        };
    }

    handleMessage(data) {
        console.log('[LiveView] Received:', data.type, data);

        switch (data.type) {
            case 'connect':
                this.sessionId = data.session_id;
                console.log('[LiveView] Session ID:', this.sessionId);
                this.autoMount();
                break;

            case 'mount':
                this.viewMounted = true;
                console.log('[LiveView] View mounted:', data.view);

                // Initialize VDOM version from mount response (critical for patch generation)
                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                    console.log('[LiveView] VDOM version initialized:', clientVdomVersion);
                }

                // Initialize cache configuration from mount response
                if (data.cache_config) {
                    setCacheConfig(data.cache_config);
                }

                // Initialize upload configurations from mount response
                if (data.upload_configs && window.djust.uploads) {
                    window.djust.uploads.setConfigs(data.upload_configs);
                }

                // OPTIMIZATION: Skip HTML replacement if content was pre-rendered via HTTP GET
                // Server sends has_ids flag to avoid client-side string search
                const hasDataDjAttrs = data.has_ids === true;
                if (this.skipMountHtml) {
                    // Content already rendered by HTTP GET - don't replace innerHTML
                    // If server HTML has data-dj-id attributes, stamp them onto existing DOM
                    // This preserves whitespace (e.g. in code blocks) that innerHTML would destroy
                    if (hasDataDjAttrs && data.html) {
                        console.log('[LiveView] Stamping data-dj-id attributes onto pre-rendered DOM');
                        _stampDjIds(data.html);
                    } else {
                        console.log('[LiveView] Skipping mount HTML - using pre-rendered content');
                    }
                    this.skipMountHtml = false;
                    bindLiveViewEvents();
                } else if (data.html) {
                    // No pre-rendered content - use server HTML directly
                    if (hasDataDjAttrs) {
                        console.log('[LiveView] Hydrating DOM with data-dj-id attributes for reliable patching');
                    }
                    let container = document.querySelector('[data-djust-view]');
                    if (!container) {
                        container = document.querySelector('[data-djust-root]');
                    }
                    if (container) {
                        container.innerHTML = data.html;
                        bindLiveViewEvents();
                    }
                    this.skipMountHtml = false;
                }
                break;

            case 'patch':
                // Use centralized response handler
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_update':
                // Use centralized response handler
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'error':
                console.error('[LiveView] Server error:', data.error);
                if (data.traceback) {
                    console.error('Traceback:', data.traceback);
                }
                // Dispatch event for dev tools (debug panel, toasts)
                window.dispatchEvent(new CustomEvent('djust:error', {
                    detail: {
                        error: data.error,
                        traceback: data.traceback || null,
                        event: data.event || this.lastEventName || null,
                        validation_details: data.validation_details || null
                    }
                }));

                // Phase 5: Stop loading state on error
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'pong':
                // Heartbeat response
                break;

            case 'upload_progress':
                // File upload progress update
                if (window.djust.uploads) {
                    window.djust.uploads.handleProgress(data);
                }
                break;

            case 'upload_registered':
                // Upload registration acknowledged
                console.log('[Upload] Registered:', data.ref, 'for', data.upload_name);
                break;

            case 'stream':
                // Streaming partial DOM updates (LLM chat, live feeds)
                if (window.djust.handleStreamMessage) {
                    window.djust.handleStreamMessage(data);
                }
                break;

            case 'push_event':
                // Server-pushed event for JS hooks
                window.dispatchEvent(new CustomEvent('djust:push_event', {
                    detail: { event: data.event, payload: data.payload }
                }));
                // Also dispatch to hooks that registered via handleEvent
                if (window.djust.hooks && window.djust.hooks._eventListeners) {
                    const listeners = window.djust.hooks._eventListeners[data.event];
                    if (listeners) {
                        listeners.forEach(cb => cb(data.payload));
                    }
                }
                break;

            case 'reload':
                // Hot reload: file changed, refresh the page
                window.location.reload();
                break;
        }
    }

    mount(viewPath, params = {}) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        console.log('[LiveView] Mounting view:', viewPath);
        this.sendMessage({
            type: 'mount',
            view: viewPath,
            params: params,
            url: window.location.pathname,
            has_prerendered: this.skipMountHtml || false  // Tell server we have pre-rendered content
        });
        return true;
    }

    /**
     * Track a WebSocket message in the history (Phase 2.1: WebSocket Inspector)
     * @param {Object} message - Message metadata
     */
    trackMessage(message) {
        this.stats.messages.unshift(message);
        // Keep only last 50 messages
        if (this.stats.messages.length > 50) {
            this.stats.messages = this.stats.messages.slice(0, 50);
        }
    }

    /**
     * Send a message via WebSocket with tracking (Phase 2.1: WebSocket Inspector)
     * @param {Object} data - Data to send (will be JSON stringified)
     */
    sendMessage(data) {
        const message = JSON.stringify(data);
        const messageBytes = message.length;

        // Track sent message
        this.stats.sent++;
        this.stats.sentBytes += messageBytes;

        // Add to message history
        this.trackMessage({
            direction: 'sent',
            type: data.type,
            size: messageBytes,
            timestamp: Date.now(),
            data: data
        });

        // Send the message
        this.ws.send(message);
    }

    autoMount() {
        // Look for container with view path
        let container = document.querySelector('[data-djust-view]');
        if (!container) {
            // Fallback: look for data-djust-root with data-djust-view attribute
            container = document.querySelector('[data-djust-root][data-djust-view]');
        }

        if (container) {
            const viewPath = container.dataset.djustView;
            if (viewPath) {
                // OPTIMIZATION: Check if content was already rendered by HTTP GET
                // We still send mount message (server needs to initialize session),
                // but we'll skip applying the HTML response
                const hasContent = container.innerHTML && container.innerHTML.trim().length > 0;

                if (hasContent) {
                    console.log('[LiveView] Content pre-rendered via HTTP - will skip HTML in mount response');
                    this.skipMountHtml = true;
                }

                // Always send mount message to initialize server-side session
                // Pass URL query params so server mount can read filters (e.g., ?sender=80)
                const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
                this.mount(viewPath, urlParams);
            } else {
                console.warn('[LiveView] Container found but no view path specified');
            }
        } else {
            console.warn('[LiveView] No LiveView container found for auto-mounting');
        }
    }

    sendEvent(eventName, params = {}, triggerElement = null) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        if (!this.viewMounted) {
            console.warn('[LiveView] View not mounted. Event ignored:', eventName);
            return false;
        }

        // Phase 5: Track event name and trigger element for loading state
        this.lastEventName = eventName;
        this.lastTriggerElement = triggerElement;

        this.sendMessage({
            type: 'event',
            event: eventName,
            params: params
        });
        return true;
    }

    // Removed duplicate applyPatches and patch helper methods
    // Now using centralized handleServerResponse() -> applyPatches()

    startHeartbeat(interval = 30000) {
        setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.sendMessage({ type: 'ping' });
            }
        }, interval);
    }
}

// Expose LiveViewWebSocket to window for client-dev.js to wrap
window.djust.LiveViewWebSocket = LiveViewWebSocket;
// Backward compatibility
window.LiveViewWebSocket = LiveViewWebSocket;

// Global WebSocket instance
let liveViewWS = null;

// === HTTP Fallback LiveView Client ===

// Track VDOM version for synchronization
let clientVdomVersion = null;

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
            console.log(`[LiveView:cache] Evicted (LRU): ${oldestKey}`);
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
            console.log(`[LiveView:cache] Configured cache for ${handlerName}:`, handlerConfig);
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
 * Note: Cache keys are global across all views. If two different views have handlers
 * with the same name and are called with the same params, they will share cache entries.
 * This is typically fine since event handler names are usually unique per view, but
 * use key_params in the @cache decorator to disambiguate if needed.
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
        console.log(`[LiveView:cache] Cleared all ${size} cached entries`);
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
        console.log(`[LiveView:cache] Invalidated ${count} entries matching: ${pattern}`);
    }

    return count;
}

// Expose cache invalidation API under djust namespace
window.djust.clearCache = clearCache;
window.djust.invalidateCache = invalidateCache;

// StateBus - Client-side State Coordination (Phase 5)
class StateBus {
    constructor() {
        this.state = new Map();
        this.subscribers = new Map();
    }

    set(key, value) {
        const oldValue = this.state.get(key);
        this.state.set(key, value);
        if (globalThis.djustDebug) {
            console.log(`[StateBus] Set: ${key} =`, value, `(was:`, oldValue, `)`);
        }
        this.notify(key, value, oldValue);
    }

    get(key) {
        return this.state.get(key);
    }

    subscribe(key, callback) {
        if (!this.subscribers.has(key)) {
            this.subscribers.set(key, new Set());
        }
        this.subscribers.get(key).add(callback);
        if (globalThis.djustDebug) {
            console.log(`[StateBus] Subscribed to: ${key} (${this.subscribers.get(key).size} subscribers)`);
        }
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

    clear() {
        this.state.clear();
        this.subscribers.clear();
        if (globalThis.djustDebug) {
            console.log('[StateBus] Cleared all state');
        }
    }

    getAll() {
        return Object.fromEntries(this.state.entries());
    }
}

const globalStateBus = new StateBus();

// DraftManager for localStorage-based draft saving
class DraftManager {
    constructor() {
        this.saveTimers = new Map();
        this.saveDelay = 500;
    }

    saveDraft(draftKey, data) {
        if (this.saveTimers.has(draftKey)) {
            clearTimeout(this.saveTimers.get(draftKey));
        }

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

    clearDraft(draftKey) {
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

    clearAllDrafts() {
        const keys = this.getAllDraftKeys();
        keys.forEach(key => this.clearDraft(key));

        if (globalThis.djustDebug) {
            console.log(`[DraftMode] Cleared all ${keys.length} drafts`);
        }
    }
}

const globalDraftManager = new DraftManager();

function initDraftMode() {
    // Check if draft mode is enabled on this page
    const draftRoot = document.querySelector('[data-draft-enabled]');
    if (!draftRoot) return;

    const draftKey = draftRoot.getAttribute('data-draft-key');
    if (!draftKey) {
        console.warn('[DraftMode] Draft enabled but no draft-key found');
        return;
    }

    console.log(`[DraftMode] Initializing draft mode with key: ${draftKey}`);

    // Load existing draft on page load
    const savedDraft = globalDraftManager.loadDraft(draftKey);
    if (savedDraft) {
        // Restore field values from draft
        Object.keys(savedDraft).forEach(fieldName => {
            const field = document.querySelector(`[name="${fieldName}"]`);
            if (field) {
                if (field.type === 'checkbox') {
                    field.checked = savedDraft[fieldName];
                } else {
                    field.value = savedDraft[fieldName];
                }
            }
        });
    }

    // Monitor all fields with data-draft="true" for changes
    const draftFields = document.querySelectorAll('[data-draft="true"]');
    draftFields.forEach(field => {
        const saveDraft = () => {
            // Collect all draft field values
            const draftData = {};
            draftFields.forEach(f => {
                // Prevent prototype pollution attacks
                if (f.name && !UNSAFE_KEYS.includes(f.name)) {
                    if (f.type === 'checkbox') {
                        draftData[f.name] = f.checked;
                    } else {
                        draftData[f.name] = f.value;
                    }
                }
            });
            globalDraftManager.saveDraft(draftKey, draftData);
        };

        // Attach input listeners with debouncing built into DraftManager
        field.addEventListener('input', saveDraft);
        field.addEventListener('change', saveDraft);
    });

    // Check for draft clear flag
    if (draftRoot.hasAttribute('data-draft-clear')) {
        console.log('[DraftMode] Draft clear flag detected, clearing draft...');
        globalDraftManager.clearDraft(draftKey);
        draftRoot.removeAttribute('data-draft-clear');
    }
}

function collectFormData(container) {
    const data = {};

    const fields = container.querySelectorAll('input, textarea, select');
    fields.forEach(field => {
        if (field.name && !UNSAFE_KEYS.includes(field.name)) {
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

    const editables = container.querySelectorAll('[contenteditable="true"]');
    editables.forEach(editable => {
        const name = editable.getAttribute('name') || editable.id;
        // Prevent prototype pollution attacks
        if (name && !UNSAFE_KEYS.includes(name)) {
            data[name] = editable.innerHTML;
        }
    });

    return data;
}

function restoreFormData(container, data) {
    if (!data) return;

    Object.entries(data).forEach(([name, value]) => {
        let field = container.querySelector(`[name="${name}"]`);

        if (!field) {
            field = container.querySelector(`#${name}`);
        }

        if (!field) return;

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

// Client-side React Counter component (vanilla JS implementation)
function initReactCounters() {
    document.querySelectorAll('[data-react-component="Counter"]').forEach(container => {
        const propsJson = container.dataset.reactProps;
        let props = {};
        try {
            props = JSON.parse(propsJson.replace(/&quot;/g, '"'));
        } catch { }

        let count = props.initialCount || 0;
        const display = container.querySelector('.counter-display');
        const minusBtn = container.querySelectorAll('.btn-sm')[0];
        const plusBtn = container.querySelectorAll('.btn-sm')[1];

        if (display && minusBtn && plusBtn) {
            minusBtn.addEventListener('click', () => {
                count--;
                display.textContent = count;
            });
            plusBtn.addEventListener('click', () => {
                count++;
                display.textContent = count;
            });
        }
    });
}

// Stub function for todo items initialization (reserved for future use)
function initTodoItems() {
    // Currently no-op - todo functionality handled via LiveView events
}

// Smart default rate limiting by input type
// Prevents VDOM version mismatches from high-frequency events
const DEFAULT_RATE_LIMITS = {
    'range': { type: 'throttle', ms: 150 },      // Sliders
    'number': { type: 'throttle', ms: 100 },     // Number spinners
    'color': { type: 'throttle', ms: 150 },      // Color pickers
    'text': { type: 'debounce', ms: 300 },       // Text inputs
    'search': { type: 'debounce', ms: 300 },     // Search boxes
    'email': { type: 'debounce', ms: 300 },      // Email inputs
    'url': { type: 'debounce', ms: 300 },        // URL inputs
    'tel': { type: 'debounce', ms: 300 },        // Phone inputs
    'password': { type: 'debounce', ms: 300 },   // Password inputs
    'textarea': { type: 'debounce', ms: 300 }    // Multi-line text
};

/**
 * Parse an event handler string to extract function name and arguments.
 *
 * Supports syntax like:
 *   "handler"              -> { name: "handler", args: [] }
 *   "handler()"            -> { name: "handler", args: [] }
 *   "handler('arg')"       -> { name: "handler", args: ["arg"] }
 *   "handler(123)"         -> { name: "handler", args: [123] }
 *   "handler(true)"        -> { name: "handler", args: [true] }
 *   "handler('a', 123)"    -> { name: "handler", args: ["a", 123] }
 *
 * @param {string} handlerString - The handler attribute value
 * @returns {Object} - { name: string, args: any[] }
 */
function parseEventHandler(handlerString) {
    const str = handlerString.trim();
    const parenIndex = str.indexOf('(');

    // No parentheses - simple handler name
    if (parenIndex === -1) {
        return { name: str, args: [] };
    }

    const name = str.slice(0, parenIndex).trim();

    // Validate handler name is a valid Python identifier
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name)) {
        console.warn(`[LiveView] Invalid handler name: "${name}", treating as literal`);
        return { name: str, args: [] };
    }

    const closeParen = str.lastIndexOf(')');

    // Invalid syntax - missing close paren, treat as simple name
    if (closeParen === -1 || closeParen < parenIndex) {
        return { name: str, args: [] };
    }

    const argsStr = str.slice(parenIndex + 1, closeParen).trim();

    // Empty parentheses
    if (!argsStr) {
        return { name, args: [] };
    }

    return { name, args: parseArguments(argsStr) };
}

/**
 * Parse comma-separated arguments into typed values.
 * Handles quoted strings, numbers, booleans, and null.
 *
 * @param {string} argsStr - Arguments string (e.g., "'hello', 123, true")
 * @returns {any[]} - Array of parsed argument values
 */
function parseArguments(argsStr) {
    const args = [];
    let current = '';
    let inString = false;
    let stringChar = null;
    let i = 0;

    while (i < argsStr.length) {
        const char = argsStr.charAt(i);

        if (inString) {
            if (char === '\\' && i + 1 < argsStr.length) {
                // Handle escaped characters
                current += char + argsStr[i + 1];
                i += 2;
                continue;
            }
            if (char === stringChar) {
                // End of string
                inString = false;
                current += char;
            } else {
                current += char;
            }
        } else {
            if (char === '"' || char === "'") {
                // Start of string
                inString = true;
                stringChar = char;
                current += char;
            } else if (char === ',') {
                // Argument separator
                const parsed = parseSingleArgument(current.trim());
                if (parsed !== undefined) {
                    args.push(parsed);
                }
                current = '';
            } else {
                current += char;
            }
        }
        i++;
    }

    // Handle the last argument
    if (current.trim()) {
        const parsed = parseSingleArgument(current.trim());
        if (parsed !== undefined) {
            args.push(parsed);
        }
    }

    return args;
}

/**
 * Parse a single argument value into its typed representation.
 *
 * @param {string} value - Single argument value string
 * @returns {any} - Parsed value (string, number, boolean, or null)
 */
function parseSingleArgument(value) {
    if (!value) return undefined;

    // Quoted string - remove quotes and handle escapes
    if ((value.startsWith("'") && value.endsWith("'")) ||
        (value.startsWith('"') && value.endsWith('"'))) {
        const inner = value.slice(1, -1);
        // Handle escape sequences in a single pass to avoid double-processing
        // e.g., \\t should become \t (backslash-t), not tab character
        return inner.replace(/\\(.)/g, (match, char) => {
            switch (char) {
                case 'n': return '\n';
                case 't': return '\t';
                case 'r': return '\r';
                case '\\': return '\\';
                case "'": return "'";
                case '"': return '"';
                default: return char; // Unknown escape, just return the char
            }
        });
    }

    // Boolean
    if (value === 'true') return true;
    if (value === 'false') return false;

    // Null
    if (value === 'null') return null;

    // Number (integer or float)
    if (/^-?\d+$/.test(value)) {
        return parseInt(value, 10);
    }
    if (/^-?\d*\.\d+$/.test(value) || /^-?\d+\.\d*$/.test(value)) {
        return parseFloat(value);
    }

    // Unknown - return as string (without quotes)
    return value;
}

// Export for global access and testing
window.djust = window.djust || {};
window.djust.parseEventHandler = parseEventHandler;

/**
 * Extract parameters from element data-* attributes with optional type coercion.
 *
 * Supports typed attributes via suffix notation:
 *   data-sender-id:int="42"     -> { sender_id: 42 }
 *   data-enabled:bool="true"    -> { enabled: true }
 *   data-price:float="19.99"    -> { price: 19.99 }
 *   data-tags:json='["a","b"]'  -> { tags: ["a", "b"] }
 *   data-items:list="a,b,c"     -> { items: ["a", "b", "c"] }
 *   data-name="John"            -> { name: "John" } (default: string)
 *
 * @param {HTMLElement} element - Element to extract params from
 * @returns {Object} - Parameters with coerced types
 */
function extractTypedParams(element) {
    const params = Object.create(null); // null prototype prevents prototype-pollution

    for (const attr of element.attributes) {
        if (!attr.name.startsWith('data-')) continue;

        // Skip djust internal attributes
        if (attr.name.startsWith('data-liveview') ||
            attr.name.startsWith('data-live-') ||
            attr.name.startsWith('data-djust') ||
            attr.name === 'data-dj-id' ||
            attr.name === 'data-loading' ||
            attr.name === 'data-component-id') {
            continue;
        }

        // Parse attribute name: data-sender-id:int -> key="sender_id", type="int"
        const nameWithoutPrefix = attr.name.slice(5); // Remove "data-"
        const colonIndex = nameWithoutPrefix.lastIndexOf(':');
        let rawKey, typeHint;

        if (colonIndex !== -1) {
            rawKey = nameWithoutPrefix.slice(0, colonIndex);
            typeHint = nameWithoutPrefix.slice(colonIndex + 1);
        } else {
            rawKey = nameWithoutPrefix;
            typeHint = null;
        }

        const key = rawKey.replace(/-/g, '_'); // Convert kebab-case to snake_case

        // Prevent prototype pollution attacks
        if (UNSAFE_KEYS.includes(key)) {
            continue;
        }
        let value = attr.value;

        // Apply type coercion based on suffix
        if (typeHint) {
            // Sanitize attribute name for logging (truncate, alphanumeric only)
            const safeAttrName = String(attr.name).slice(0, 50).replace(/[^a-z0-9-:]/gi, '_');

            switch (typeHint) {
                case 'int':
                case 'integer': {
                    if (value === '') {
                        value = 0;
                    } else {
                        const parsed = parseInt(value, 10);
                        if (isNaN(parsed)) {
                            console.warn(`[LiveView] Invalid int value for ${safeAttrName}: "${value}", using null`);
                            value = null;  // Let server-side validation handle invalid input
                        } else {
                            value = parsed;
                        }
                    }
                    break;
                }

                case 'float':
                case 'number': {
                    if (value === '') {
                        value = 0.0;
                    } else {
                        const parsed = parseFloat(value);
                        if (isNaN(parsed)) {
                            console.warn(`[LiveView] Invalid float value for ${safeAttrName}: "${value}", using null`);
                            value = null;  // Let server-side validation handle invalid input
                        } else {
                            value = parsed;
                        }
                    }
                    break;
                }

                case 'bool':
                case 'boolean':
                    value = ['true', '1', 'yes', 'on', 'checked'].includes(value.toLowerCase());
                    break;

                case 'json':
                case 'object':
                case 'array':
                    try {
                        value = JSON.parse(value);
                    } catch {
                        console.warn(`[LiveView] Failed to parse JSON for ${safeAttrName}: "${value}"`);
                        // Keep as string if JSON parse fails - server will validate
                    }
                    break;

                case 'list':
                    // Comma-separated list
                    value = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                    break;

                // Unknown type hint - keep as string
                default:
                    console.warn(`[LiveView] Unknown type hint "${typeHint}" for ${safeAttrName}, keeping as string`);
                    break;
            }
        }

        params[key] = value;
    }

    return params;
}

// Export for global access
window.djust = window.djust || {};
window.djust.extractTypedParams = extractTypedParams;

function bindLiveViewEvents() {
    // Bind upload handlers (dj-upload, dj-upload-drop, dj-upload-preview)
    if (window.djust.uploads) {
        window.djust.uploads.bindHandlers();
    }

    // Find all interactive elements
    const allElements = document.querySelectorAll('*');
    allElements.forEach(element => {
        // Handle dj-click events
        const clickHandler = element.getAttribute('dj-click');
        if (clickHandler && !element.dataset.liveviewClickBound) {
            element.dataset.liveviewClickBound = 'true';
            // Parse handler string to extract function name and arguments
            const parsed = parseEventHandler(clickHandler);
            
            const clickHandlerFn = async (e) => {
                e.preventDefault();

                // dj-confirm: show confirmation dialog before sending event
                const confirmMsg = element.getAttribute('dj-confirm');
                if (confirmMsg && !window.confirm(confirmMsg)) {
                    return; // User cancelled
                }

                // Apply optimistic update if specified
                let optimisticUpdateId = null;
                if (window.djust.optimistic) {
                    optimisticUpdateId = window.djust.optimistic.applyOptimisticUpdate(e.currentTarget, parsed.name);
                }

                // Extract all data-* attributes with type coercion support
                const params = extractTypedParams(element);

                // Add positional arguments from handler syntax if present
                // e.g., dj-click="set_period('month')" -> params._args = ['month']
                if (parsed.args.length > 0) {
                    params._args = parsed.args;
                }

                // Phase 4: Check if event is from a component
                const componentId = getComponentId(e.currentTarget);
                if (componentId) {
                    params.component_id = componentId;
                }

                // Pass target element and optimistic update ID
                params._targetElement = e.currentTarget;
                params._optimisticUpdateId = optimisticUpdateId;

                // Handle dj-target for scoped updates
                const targetSelector = element.getAttribute('dj-target');
                if (targetSelector) {
                    params._djTargetSelector = targetSelector;
                }

                await handleEvent(parsed.name, params);
            };

            // Apply rate limiting if specified
            const wrappedHandler = window.djust.rateLimit 
                ? window.djust.rateLimit.wrapWithRateLimit(element, 'click', clickHandlerFn)
                : clickHandlerFn;

            element.addEventListener('click', wrappedHandler);
        }

        // Handle dj-submit events on forms
        const submitHandler = element.getAttribute('dj-submit');
        if (submitHandler && !element.dataset.liveviewSubmitBound) {
            element.dataset.liveviewSubmitBound = 'true';
            element.addEventListener('submit', async (e) => {
                e.preventDefault();
                const formData = new FormData(e.target);
                const params = Object.fromEntries(formData.entries());

                // Phase 4: Check if event is from a component
                const componentId = getComponentId(e.target);
                if (componentId) {
                    params.component_id = componentId;
                }

                // Pass target element for optimistic updates (Phase 3)
                params._targetElement = e.target;

                await handleEvent(submitHandler, params);
                e.target.reset();
            });
        }

        // Helper: Extract field name from element attributes
        // Priority: data-field (explicit) > name (standard) > id (fallback)
        function getFieldName(element) {
            if (element.dataset.field) {
                return element.dataset.field;
            }
            if (element.name) {
                return element.name;
            }
            if (element.id) {
                // Strip common prefixes like 'id_' (Django convention)
                return element.id.replace(/^id_/, '');
            }
            return null;
        }

        /**
         * Build standard form event params with component context.
         * Used by change, input, blur, focus event handlers.
         * @param {HTMLElement} element - Form element that triggered the event
         * @param {any} value - Current value of the field
         * @returns {Object} - Params object with value, field, and optional component_id
         */
        function buildFormEventParams(element, value) {
            const fieldName = getFieldName(element);
            const params = { value, field: fieldName };
            const componentId = getComponentId(element);
            if (componentId) {
                params.component_id = componentId;
            }
            return params;
        }

        // Handle dj-change events
        const changeHandler = element.getAttribute('dj-change');
        if (changeHandler && !element.dataset.liveviewChangeBound) {
            element.dataset.liveviewChangeBound = 'true';
            
            const changeHandlerFn = async (e) => {
                const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
                const params = buildFormEventParams(e.target, value);
                
                // Add target element for loading state (consistent with other handlers)
                params._targetElement = e.target;

                // Handle dj-target for scoped updates
                const targetSelector = element.getAttribute('dj-target');
                if (targetSelector) {
                    params._djTargetSelector = targetSelector;
                }

                if (globalThis.djustDebug) {
                    console.log(`[LiveView] dj-change handler: value="${value}", params=`, params);
                }
                await handleEvent(changeHandler, params);
            };

            // Apply rate limiting if specified
            const wrappedHandler = window.djust.rateLimit 
                ? window.djust.rateLimit.wrapWithRateLimit(element, 'change', changeHandlerFn)
                : changeHandlerFn;

            element.addEventListener('change', wrappedHandler);
        }

        // Handle dj-input events (with smart debouncing/throttling)
        const inputHandler = element.getAttribute('dj-input');
        if (inputHandler && !element.dataset.liveviewInputBound) {
            element.dataset.liveviewInputBound = 'true';

            // Determine rate limit strategy
            const inputType = element.type || element.tagName.toLowerCase();
            const rateLimit = Object.prototype.hasOwnProperty.call(DEFAULT_RATE_LIMITS, inputType) ? DEFAULT_RATE_LIMITS[inputType] : { type: 'debounce', ms: 300 };

            // Check for explicit overrides
            if (element.hasAttribute('data-debounce')) {
                rateLimit.type = 'debounce';
                rateLimit.ms = parseInt(element.getAttribute('data-debounce'));
            } else if (element.hasAttribute('data-throttle')) {
                rateLimit.type = 'throttle';
                rateLimit.ms = parseInt(element.getAttribute('data-throttle'));
            }

            const handler = async (e) => {
                const params = buildFormEventParams(e.target, e.target.value);
                await handleEvent(inputHandler, params);
            };

            // Apply rate limiting wrapper
            let wrappedHandler;
            if (rateLimit.type === 'throttle') {
                wrappedHandler = throttle(handler, rateLimit.ms);
            } else {
                wrappedHandler = debounce(handler, rateLimit.ms);
            }

            element.addEventListener('input', wrappedHandler);
        }

        // Handle dj-blur events
        const blurHandler = element.getAttribute('dj-blur');
        if (blurHandler && !element.dataset.liveviewBlurBound) {
            element.dataset.liveviewBlurBound = 'true';
            element.addEventListener('blur', async (e) => {
                const params = buildFormEventParams(e.target, e.target.value);
                await handleEvent(blurHandler, params);
            });
        }

        // Handle dj-focus events
        const focusHandler = element.getAttribute('dj-focus');
        if (focusHandler && !element.dataset.liveviewFocusBound) {
            element.dataset.liveviewFocusBound = 'true';
            element.addEventListener('focus', async (e) => {
                const params = buildFormEventParams(e.target, e.target.value);
                await handleEvent(focusHandler, params);
            });
        }

        // Handle dj-keydown / dj-keyup events
        ['keydown', 'keyup'].forEach(eventType => {
            const keyHandler = element.getAttribute(`dj-${eventType}`);
            if (keyHandler && !element.dataset[`liveview${eventType}Bound`]) {
                element.dataset[`liveview${eventType}Bound`] = 'true';
                
                const keyHandlerFn = async (e) => {
                    // Check for key modifiers (e.g. dj-keydown.enter)
                    const modifiers = keyHandler.split('.');
                    const handlerName = modifiers[0];
                    const requiredKey = modifiers.length > 1 ? modifiers[1] : null;

                    if (requiredKey) {
                        if (requiredKey === 'enter' && e.key !== 'Enter') return;
                        if (requiredKey === 'escape' && e.key !== 'Escape') return;
                        if (requiredKey === 'space' && e.key !== ' ') return;
                        // Add more key mappings as needed
                    }

                    const fieldName = getFieldName(e.target);
                    const params = {
                        key: e.key,
                        code: e.code,
                        value: e.target.value,
                        field: fieldName
                    };

                    // Phase 4: Check if event is from a component
                    const componentId = getComponentId(e.target);
                    if (componentId) {
                        params.component_id = componentId;
                    }

                    // Add target element and handle dj-target
                    params._targetElement = e.target;
                    const targetSelector = element.getAttribute('dj-target');
                    if (targetSelector) {
                        params._djTargetSelector = targetSelector;
                    }

                    await handleEvent(handlerName, params);
                };

                // Apply rate limiting if specified
                const wrappedHandler = window.djust.rateLimit 
                    ? window.djust.rateLimit.wrapWithRateLimit(element, eventType, keyHandlerFn)
                    : keyHandlerFn;

                element.addEventListener(eventType, wrappedHandler);
            }
        });
    });
}

// Helper: Debounce function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Helper: Throttle function
function throttle(func, limit) {
    let inThrottle;
    return function (...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    }
}

// Helper: Get LiveView root element
function getLiveViewRoot() {
    return document.querySelector('[data-djust-root]') || document.body;
}

// Helper: Clear optimistic state
function clearOptimisticState(eventName) {
    if (eventName && optimisticUpdates.has(eventName)) {
        const { element: _element, originalState: _originalState } = optimisticUpdates.get(eventName);
        // TODO: Restore original state on error (e.g. _element, _originalState)
        optimisticUpdates.delete(eventName);
    }
}

// ============================================================================
// dj-transition — CSS Transition Support
// ============================================================================
// Applies enter/leave CSS transition classes when elements are added/removed
// from the DOM via VDOM patches. Inspired by Vue's <Transition>.
//
// Usage:
//   <div dj-transition="fade">...</div>
//   Applies: fade-enter-from → fade-enter-to (on mount)
//            fade-leave-from → fade-leave-to (on remove)
//
//   <div dj-transition-enter="opacity-0" dj-transition-enter-to="opacity-100"
//        dj-transition-leave="opacity-100" dj-transition-leave-to="opacity-0">
//   Explicit class-based transitions.

const djTransitions = {
    /**
     * Apply enter transition to a newly inserted element.
     * @param {HTMLElement} el - The element being inserted
     */
    applyEnter(el) {
        if (!el || el.nodeType !== Node.ELEMENT_NODE) return;

        const transitionName = el.getAttribute('dj-transition');
        const explicitEnter = el.getAttribute('dj-transition-enter');
        const explicitEnterTo = el.getAttribute('dj-transition-enter-to');

        if (!transitionName && !explicitEnter) return;

        // Determine classes
        const enterFromClasses = explicitEnter
            ? explicitEnter.split(/\s+/)
            : [`${transitionName}-enter-from`];
        const enterToClasses = explicitEnterTo
            ? explicitEnterTo.split(/\s+/)
            : [`${transitionName}-enter-to`];

        // Apply enter-from classes immediately
        enterFromClasses.forEach(cls => cls && el.classList.add(cls));

        // Force reflow to ensure the initial state is painted
        void el.offsetHeight;

        // Next frame: swap to enter-to classes
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                enterFromClasses.forEach(cls => cls && el.classList.remove(cls));
                enterToClasses.forEach(cls => cls && el.classList.add(cls));

                // Clean up enter-to classes after transition ends
                const onEnd = () => {
                    enterToClasses.forEach(cls => cls && el.classList.remove(cls));
                    el.removeEventListener('transitionend', onEnd);
                };
                el.addEventListener('transitionend', onEnd, { once: true });

                // Safety timeout: clean up if transitionend never fires (no CSS transition defined)
                setTimeout(() => {
                    enterToClasses.forEach(cls => cls && el.classList.remove(cls));
                }, 1000);
            });
        });
    },

    /**
     * Apply leave transition to an element being removed.
     * Returns a promise that resolves when the transition completes.
     * @param {HTMLElement} el - The element being removed
     * @returns {Promise<void>}
     */
    applyLeave(el) {
        if (!el || el.nodeType !== Node.ELEMENT_NODE) {
            return Promise.resolve();
        }

        const transitionName = el.getAttribute('dj-transition');
        const explicitLeave = el.getAttribute('dj-transition-leave');
        const explicitLeaveTo = el.getAttribute('dj-transition-leave-to');

        if (!transitionName && !explicitLeave) {
            return Promise.resolve();
        }

        const leaveFromClasses = explicitLeave
            ? explicitLeave.split(/\s+/)
            : [`${transitionName}-leave-from`];
        const leaveToClasses = explicitLeaveTo
            ? explicitLeaveTo.split(/\s+/)
            : [`${transitionName}-leave-to`];

        return new Promise(resolve => {
            // Apply leave-from classes
            leaveFromClasses.forEach(cls => cls && el.classList.add(cls));

            // Force reflow
            void el.offsetHeight;

            // Next frame: swap to leave-to
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    leaveFromClasses.forEach(cls => cls && el.classList.remove(cls));
                    leaveToClasses.forEach(cls => cls && el.classList.add(cls));

                    const onEnd = () => {
                        resolve();
                    };
                    el.addEventListener('transitionend', onEnd, { once: true });

                    // Safety timeout
                    setTimeout(resolve, 1000);
                });
            });
        });
    },

    /**
     * Check if an element has transition directives.
     * @param {HTMLElement} el
     * @returns {boolean}
     */
    hasTransition(el) {
        if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
        return el.hasAttribute('dj-transition') ||
               el.hasAttribute('dj-transition-enter') ||
               el.hasAttribute('dj-transition-leave');
    }
};

// Expose for use in VDOM patch application
window.djust = window.djust || {};
window.djust.transitions = djTransitions;

// Global Loading Manager (Phase 5)
// Handles dj-loading.disable, dj-loading.class, dj-loading.show, dj-loading.hide attributes
const globalLoadingManager = {
    // Map of element -> { originalState, modifiers }
    registeredElements: new Map(),
    pendingEvents: new Set(),

    // Register an element with dj-loading attributes
    register(element, eventName) {
        const modifiers = [];
        const originalState = {};

        // Parse dj-loading.* attributes
        Array.from(element.attributes).forEach(attr => {
            const match = attr.name.match(/^dj-loading\.(.+)$/);
            if (match) {
                const modifier = match[1];
                if (modifier === 'disable') {
                    modifiers.push({ type: 'disable' });
                    originalState.disabled = element.disabled;
                } else if (modifier === 'show') {
                    modifiers.push({ type: 'show' });
                    // Store original inline display to restore when loading stops
                    originalState.display = element.style.display;
                    // Determine display value to use when showing:
                    // 1. Use attribute value if specified (e.g., dj-loading.show="flex")
                    // 2. Otherwise default to 'block'
                    originalState.visibleDisplay = attr.value || 'block';
                } else if (modifier === 'hide' || modifier === 'remove') {
                    modifiers.push({ type: 'hide' });
                    // Store computed display to properly restore when loading stops
                    const computedDisplay = getComputedStyle(element).display;
                    originalState.display = computedDisplay !== 'none' ? computedDisplay : (element.style.display || 'block');
                } else if (modifier === 'class') {
                    const className = attr.value;
                    if (className) {
                        modifiers.push({ type: 'class', value: className });
                    }
                }
            }
        });

        if (modifiers.length > 0) {
            this.registeredElements.set(element, { eventName, modifiers, originalState });
            if (globalThis.djustDebug) {
                console.log(`[Loading] Registered element for "${eventName}":`, modifiers);
            }
        }
    },

    // Scan and register all elements with dj-loading attributes
    scanAndRegister() {
        // Use targeted attribute selectors for better performance on large pages
        const selectors = [
            '[dj-loading\\.disable]',
            '[dj-loading\\.show]',
            '[dj-loading\\.hide]',
            '[dj-loading\\.remove]',
            '[dj-loading\\.class]'
        ].join(',');

        const loadingElements = document.querySelectorAll(selectors);
        loadingElements.forEach(element => {
            // Find the associated event from dj-click, dj-submit, etc.
            const eventAttr = Array.from(element.attributes).find(
                attr => attr.name.startsWith('dj-') && !attr.name.startsWith('dj-loading')
            );
            const eventName = eventAttr ? eventAttr.value : null;
            if (eventName) {
                this.register(element, eventName);
            }
        });
        if (globalThis.djustDebug) {
            console.log(`[Loading] Scanned ${this.registeredElements.size} elements with dj-loading attributes`);
        }
    },

    startLoading(eventName, triggerElement) {
        this.pendingEvents.add(eventName);

        // Apply loading state to trigger element
        if (triggerElement) {
            triggerElement.classList.add('djust-loading');

            // Check if trigger element has dj-loading.disable
            const hasDisable = triggerElement.hasAttribute('dj-loading.disable');
            if (globalThis.djustDebug) {
                console.log(`[Loading] triggerElement:`, triggerElement);
                console.log(`[Loading] hasAttribute('dj-loading.disable'):`, hasDisable);
            }
            if (hasDisable) {
                triggerElement.disabled = true;
            }
        }

        // Apply loading state to all registered elements watching this event
        this.registeredElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                this.applyLoadingState(element, config);
            }
        });

        document.body.classList.add('djust-global-loading');

        if (globalThis.djustDebug) {
            console.log(`[Loading] Started: ${eventName}`);
        }
    },

    stopLoading(eventName, triggerElement) {
        this.pendingEvents.delete(eventName);

        // Remove loading state from trigger element
        if (triggerElement) {
            triggerElement.classList.remove('djust-loading');
            // Check if trigger element has dj-loading.disable
            const hasDisable = triggerElement.hasAttribute('dj-loading.disable');
            if (hasDisable) {
                triggerElement.disabled = false;
            }
        }

        // Remove loading state from all registered elements watching this event
        this.registeredElements.forEach((config, element) => {
            if (config.eventName === eventName) {
                this.removeLoadingState(element, config);
            }
        });

        document.body.classList.remove('djust-global-loading');

        if (globalThis.djustDebug) {
            console.log(`[Loading] Stopped: ${eventName}`);
        }
    },

    applyLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = true;
            } else if (modifier.type === 'show') {
                // Use stored visible display value (supports dj-loading.show="flex" etc.)
                element.style.display = config.originalState.visibleDisplay || 'block';
            } else if (modifier.type === 'hide') {
                element.style.display = 'none';
            } else if (modifier.type === 'class') {
                element.classList.add(modifier.value);
            }
        });
    },

    removeLoadingState(element, config) {
        config.modifiers.forEach(modifier => {
            if (modifier.type === 'disable') {
                element.disabled = config.originalState.disabled || false;
            } else if (modifier.type === 'show') {
                element.style.display = config.originalState.display || 'none';
            } else if (modifier.type === 'hide') {
                element.style.display = config.originalState.display || '';
            } else if (modifier.type === 'class') {
                element.classList.remove(modifier.value);
            }
        });
    }
};

// Expose globalLoadingManager under djust namespace
window.djust.globalLoadingManager = globalLoadingManager;
// Backward compatibility
window.globalLoadingManager = globalLoadingManager;

// Generate unique request ID for cache tracking
let cacheRequestCounter = 0;
function generateCacheRequestId() {
    return `cache_${Date.now()}_${++cacheRequestCounter}`;
}

// Main Event Handler
async function handleEvent(eventName, params = {}) {
    if (globalThis.djustDebug) {
        console.log(`[LiveView] Handling event: ${eventName}`, params);
    }

    // Extract optimistic update ID and target element
    const triggerElement = params._targetElement;
    const optimisticUpdateId = params._optimisticUpdateId;
    const targetSelector = params._djTargetSelector;

    // Clean up internal params before sending to server
    const serverParams = { ...params };
    delete serverParams._targetElement;
    delete serverParams._optimisticUpdateId;
    delete serverParams._djTargetSelector;

    // Check client-side cache first
    const config = cacheConfig.get(eventName);
    const keyParams = config?.key_params || null;
    const cacheKey = buildCacheKey(eventName, serverParams, keyParams);
    const cached = getCachedResult(cacheKey);

    if (cached) {
        // Cache hit! Apply cached patches without server round-trip
        if (globalThis.djustDebug) {
            console.log(`[LiveView:cache] Cache hit: ${cacheKey}`);
        }

        // Still show brief loading state for UX consistency
        globalLoadingManager.startLoading(eventName, triggerElement);

        // Clear optimistic update (server state is now applied)
        if (optimisticUpdateId && window.djust.optimistic) {
            window.djust.optimistic.clearOptimisticUpdate(optimisticUpdateId);
        }

        // Apply cached patches
        if (cached.patches && cached.patches.length > 0) {
            applyPatches(cached.patches, targetSelector);
            initReactCounters();
            initTodoItems();
            bindLiveViewEvents();
        }

        globalLoadingManager.stopLoading(eventName, triggerElement);
        return;
    }

    // Cache miss - need to fetch from server
    if (globalThis.djustDebug && cacheConfig.has(eventName)) {
        console.log(`[LiveView:cache] Cache miss: ${cacheKey}`);
    }

    globalLoadingManager.startLoading(eventName, triggerElement);

    // Prepare params for request
    let paramsWithCache = serverParams;

    // Only set up caching for events with @cache decorator
    if (config) {
        // Generate cache request ID for cacheable events
        const cacheRequestId = generateCacheRequestId();
        const ttl = config.ttl || 60;

        // Set up cleanup timeout to prevent memory leaks if request fails
        const timeoutId = setTimeout(() => {
            if (pendingCacheRequests.has(cacheRequestId)) {
                pendingCacheRequests.delete(cacheRequestId);
                if (globalThis.djustDebug) {
                    console.log(`[LiveView:cache] Cleaned up stale pending request: ${cacheRequestId}`);
                }
            }
        }, PENDING_CACHE_TIMEOUT);

        // Store pending cache request (will be fulfilled when response arrives)
        pendingCacheRequests.set(cacheRequestId, { cacheKey, ttl, timeoutId });

        // Add cache request ID to params
        paramsWithCache = { ...serverParams, _cacheRequestId: cacheRequestId };
    }

    // Store optimistic update context for later handling
    if (optimisticUpdateId) {
        paramsWithCache._optimistic_context = { updateId: optimisticUpdateId, targetSelector };
    }

    // Try WebSocket first
    if (liveViewWS && liveViewWS.sendEvent(eventName, paramsWithCache, triggerElement)) {
        return;
    }

    // Fallback to HTTP
    console.log('[LiveView] WebSocket unavailable, falling back to HTTP');

    try {
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1]
            || '';
        const response = await fetch(window.location.href, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Djust-Event': eventName,
                'X-Djust-Target': targetSelector || ''
            },
            body: JSON.stringify(paramsWithCache)
        });

        if (!response.ok) {
            // Revert optimistic update on HTTP error
            if (optimisticUpdateId && window.djust.optimistic) {
                window.djust.optimistic.revertOptimisticUpdate(optimisticUpdateId);
                showFlashMessage('An error occurred. Please try again.', 'error');
            }
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        handleServerResponse(data, eventName, triggerElement, { optimisticUpdateId, targetSelector });

    } catch (error) {
        console.error('[LiveView] HTTP fallback failed:', error);
        
        // Revert optimistic update on error
        if (optimisticUpdateId && window.djust.optimistic) {
            window.djust.optimistic.revertOptimisticUpdate(optimisticUpdateId);
            showFlashMessage('Connection error. Please try again.', 'error');
        }
        
        globalLoadingManager.stopLoading(eventName, triggerElement);
    }
}

/**
 * Show flash message to user
 * @param {string} message - Message to show
 * @param {string} type - Message type (error, success, info)
 */
function showFlashMessage(message, type = 'info') {
    // Try to find existing flash container
    let flashContainer = document.querySelector('.djust-flash-messages');
    
    if (!flashContainer) {
        // Create flash container if it doesn't exist
        flashContainer = document.createElement('div');
        flashContainer.className = 'djust-flash-messages';
        flashContainer.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
        `;
        document.body.appendChild(flashContainer);
    }

    // Create flash message element
    const flashElement = document.createElement('div');
    flashElement.className = `djust-flash djust-flash-${type}`;
    flashElement.style.cssText = `
        background: ${type === 'error' ? '#ef4444' : type === 'success' ? '#10b981' : '#3b82f6'};
        color: white;
        padding: 12px 16px;
        border-radius: 4px;
        margin-bottom: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        animation: djust-flash-slide-in 0.3s ease-out;
    `;
    flashElement.textContent = message;

    // Add CSS animation if not already added
    if (!document.querySelector('#djust-flash-styles')) {
        const style = document.createElement('style');
        style.id = 'djust-flash-styles';
        style.textContent = `
            @keyframes djust-flash-slide-in {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes djust-flash-slide-out {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
    }

    flashContainer.appendChild(flashElement);

    // Auto remove after 5 seconds
    setTimeout(() => {
        flashElement.style.animation = 'djust-flash-slide-out 0.3s ease-out';
        setTimeout(() => {
            if (flashElement.parentNode) {
                flashElement.parentNode.removeChild(flashElement);
            }
        }, 300);
    }, 5000);
}

// === VDOM Patch Application ===

/**
 * Sanitize a djust ID for safe logging (defense-in-depth).
 * @param {*} id - The ID to sanitize
 * @returns {string} - Sanitized ID safe for logging
 */
function sanitizeIdForLog(id) {
    if (!id) return 'none';
    return String(id).slice(0, 20).replace(/[^\w-]/g, '');
}

/**
 * Resolve a DOM node using ID-based lookup (primary) or path traversal (fallback).
 *
 * Resolution strategy:
 * 1. If djustId is provided, try querySelector('[data-dj-id="..."]') - O(1), reliable
 * 2. Fall back to index-based path traversal
 *
 * @param {Array<number>} path - Index-based path (fallback)
 * @param {string|null} djustId - Compact djust ID for direct lookup (e.g., "1a")
 * @returns {Node|null} - Found node or null
 */
function getNodeByPath(path, djustId = null, targetSelector = null) {
    // Strategy 1: ID-based resolution (fast, reliable)
    if (djustId) {
        const scope = targetSelector ? document.querySelector(targetSelector) : document;
        const byId = scope ? scope.querySelector(`[data-dj-id="${CSS.escape(djustId)}"]`) : null;
        if (byId) {
            return byId;
        }
        // ID not found - fall through to path-based
        if (globalThis.djustDebug) {
            // Log without user data to avoid log injection
            console.log('[LiveView] ID lookup failed, trying path fallback');
        }
    }

    // Strategy 2: Index-based path traversal (fallback)
    let node = targetSelector ? document.querySelector(targetSelector) : getLiveViewRoot();

    if (path.length === 0) {
        return node;
    }

    for (let i = 0; i < path.length; i++) {
        const index = path[i]; // eslint-disable-line security/detect-object-injection -- path is a server-provided integer array
        const children = Array.from(node.childNodes).filter(child => {
            if (child.nodeType === Node.ELEMENT_NODE) return true;
            if (child.nodeType === Node.TEXT_NODE) {
                return child.textContent.trim().length > 0;
            }
            return false;
        });

        if (index >= children.length) {
            if (globalThis.djustDebug) {
                // Explicit number coercion for safe logging
                const safeIndex = Number(index) || 0;
                const safeLen = Number(children.length) || 0;
                console.warn(`[LiveView] Path traversal failed at index ${safeIndex}, only ${safeLen} children`);
            }
            return null;
        }

        node = children[index];
    }

    return node;
}

// SVG namespace and tags for proper element creation
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set([
    'svg', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon',
    'ellipse', 'g', 'defs', 'use', 'text', 'tspan', 'textPath',
    'clipPath', 'mask', 'pattern', 'marker', 'symbol', 'linearGradient',
    'radialGradient', 'stop', 'image', 'foreignObject', 'switch',
    'desc', 'title', 'metadata'
]);

// Allowed HTML tags for VDOM element creation (security: prevents script injection)
// This whitelist covers standard HTML elements; extend as needed
const ALLOWED_HTML_TAGS = new Set([
    // Document structure
    'html', 'head', 'body', 'div', 'span', 'main', 'section', 'article',
    'aside', 'header', 'footer', 'nav', 'figure', 'figcaption',
    // Text content
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'pre', 'code', 'blockquote',
    'hr', 'br', 'wbr', 'address',
    // Inline text
    'a', 'abbr', 'b', 'bdi', 'bdo', 'cite', 'data', 'dfn', 'em', 'i',
    'kbd', 'mark', 'q', 's', 'samp', 'small', 'strong', 'sub', 'sup',
    'time', 'u', 'var', 'del', 'ins',
    // Lists
    'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'menu',
    // Tables
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'colgroup', 'col',
    // Forms
    'form', 'fieldset', 'legend', 'label', 'input', 'textarea', 'select',
    'option', 'optgroup', 'button', 'datalist', 'output', 'progress', 'meter',
    // Media
    'img', 'audio', 'video', 'source', 'track', 'picture', 'canvas',
    'iframe', 'embed', 'object', 'param', 'map', 'area',
    // Interactive
    'details', 'summary', 'dialog',
    // Other
    'template', 'slot', 'noscript'
]);

/**
 * Check if a DOM element is within an SVG context.
 * Used when creating new elements during patch application.
 */
function isInSvgContext(element) {
    if (!element) return false;
    // Check if element itself or any ancestor is an SVG element
    let current = element;
    while (current && current !== document.body) {
        if (current.namespaceURI === SVG_NAMESPACE) {
            return true;
        }
        current = current.parentElement;
    }
    return false;
}

/**
 * Create an SVG element by tag name (security: only creates whitelisted tags)
 * Uses a lookup object with factory functions to ensure only string literals
 * are passed to createElementNS.
 */
const SVG_ELEMENT_FACTORIES = {
    'svg': () => document.createElementNS(SVG_NAMESPACE, 'svg'),
    'path': () => document.createElementNS(SVG_NAMESPACE, 'path'),
    'circle': () => document.createElementNS(SVG_NAMESPACE, 'circle'),
    'rect': () => document.createElementNS(SVG_NAMESPACE, 'rect'),
    'line': () => document.createElementNS(SVG_NAMESPACE, 'line'),
    'polyline': () => document.createElementNS(SVG_NAMESPACE, 'polyline'),
    'polygon': () => document.createElementNS(SVG_NAMESPACE, 'polygon'),
    'ellipse': () => document.createElementNS(SVG_NAMESPACE, 'ellipse'),
    'g': () => document.createElementNS(SVG_NAMESPACE, 'g'),
    'defs': () => document.createElementNS(SVG_NAMESPACE, 'defs'),
    'use': () => document.createElementNS(SVG_NAMESPACE, 'use'),
    'text': () => document.createElementNS(SVG_NAMESPACE, 'text'),
    'tspan': () => document.createElementNS(SVG_NAMESPACE, 'tspan'),
    'textPath': () => document.createElementNS(SVG_NAMESPACE, 'textPath'),
    'clipPath': () => document.createElementNS(SVG_NAMESPACE, 'clipPath'),
    'mask': () => document.createElementNS(SVG_NAMESPACE, 'mask'),
    'pattern': () => document.createElementNS(SVG_NAMESPACE, 'pattern'),
    'marker': () => document.createElementNS(SVG_NAMESPACE, 'marker'),
    'symbol': () => document.createElementNS(SVG_NAMESPACE, 'symbol'),
    'linearGradient': () => document.createElementNS(SVG_NAMESPACE, 'linearGradient'),
    'radialGradient': () => document.createElementNS(SVG_NAMESPACE, 'radialGradient'),
    'stop': () => document.createElementNS(SVG_NAMESPACE, 'stop'),
    'image': () => document.createElementNS(SVG_NAMESPACE, 'image'),
    'foreignObject': () => document.createElementNS(SVG_NAMESPACE, 'foreignObject'),
    'switch': () => document.createElementNS(SVG_NAMESPACE, 'switch'),
    'desc': () => document.createElementNS(SVG_NAMESPACE, 'desc'),
    'title': () => document.createElementNS(SVG_NAMESPACE, 'title'),
    'metadata': () => document.createElementNS(SVG_NAMESPACE, 'metadata'),
};

function createSvgElement(tagLower) {
    const factory = SVG_ELEMENT_FACTORIES[tagLower];
    return factory ? factory() : document.createElement('span');
}

/**
 * Create an HTML element by tag name (security: only creates whitelisted tags)
 * Uses a lookup object with factory functions to ensure only string literals
 * are passed to createElement.
 */
const HTML_ELEMENT_FACTORIES = {
    // Document structure
    'html': () => document.createElement('html'),
    'head': () => document.createElement('head'),
    'body': () => document.createElement('body'),
    'div': () => document.createElement('div'),
    'span': () => document.createElement('span'),
    'main': () => document.createElement('main'),
    'section': () => document.createElement('section'),
    'article': () => document.createElement('article'),
    'aside': () => document.createElement('aside'),
    'header': () => document.createElement('header'),
    'footer': () => document.createElement('footer'),
    'nav': () => document.createElement('nav'),
    'figure': () => document.createElement('figure'),
    'figcaption': () => document.createElement('figcaption'),
    // Text content
    'h1': () => document.createElement('h1'),
    'h2': () => document.createElement('h2'),
    'h3': () => document.createElement('h3'),
    'h4': () => document.createElement('h4'),
    'h5': () => document.createElement('h5'),
    'h6': () => document.createElement('h6'),
    'p': () => document.createElement('p'),
    'pre': () => document.createElement('pre'),
    'code': () => document.createElement('code'),
    'blockquote': () => document.createElement('blockquote'),
    'hr': () => document.createElement('hr'),
    'br': () => document.createElement('br'),
    'wbr': () => document.createElement('wbr'),
    'address': () => document.createElement('address'),
    // Inline text
    'a': () => document.createElement('a'),
    'abbr': () => document.createElement('abbr'),
    'b': () => document.createElement('b'),
    'bdi': () => document.createElement('bdi'),
    'bdo': () => document.createElement('bdo'),
    'cite': () => document.createElement('cite'),
    'data': () => document.createElement('data'),
    'dfn': () => document.createElement('dfn'),
    'em': () => document.createElement('em'),
    'i': () => document.createElement('i'),
    'kbd': () => document.createElement('kbd'),
    'mark': () => document.createElement('mark'),
    'q': () => document.createElement('q'),
    's': () => document.createElement('s'),
    'samp': () => document.createElement('samp'),
    'small': () => document.createElement('small'),
    'strong': () => document.createElement('strong'),
    'sub': () => document.createElement('sub'),
    'sup': () => document.createElement('sup'),
    'time': () => document.createElement('time'),
    'u': () => document.createElement('u'),
    'var': () => document.createElement('var'),
    'del': () => document.createElement('del'),
    'ins': () => document.createElement('ins'),
    // Lists
    'ul': () => document.createElement('ul'),
    'ol': () => document.createElement('ol'),
    'li': () => document.createElement('li'),
    'dl': () => document.createElement('dl'),
    'dt': () => document.createElement('dt'),
    'dd': () => document.createElement('dd'),
    'menu': () => document.createElement('menu'),
    // Tables
    'table': () => document.createElement('table'),
    'thead': () => document.createElement('thead'),
    'tbody': () => document.createElement('tbody'),
    'tfoot': () => document.createElement('tfoot'),
    'tr': () => document.createElement('tr'),
    'th': () => document.createElement('th'),
    'td': () => document.createElement('td'),
    'caption': () => document.createElement('caption'),
    'colgroup': () => document.createElement('colgroup'),
    'col': () => document.createElement('col'),
    // Forms
    'form': () => document.createElement('form'),
    'fieldset': () => document.createElement('fieldset'),
    'legend': () => document.createElement('legend'),
    'label': () => document.createElement('label'),
    'input': () => document.createElement('input'),
    'textarea': () => document.createElement('textarea'),
    'select': () => document.createElement('select'),
    'option': () => document.createElement('option'),
    'optgroup': () => document.createElement('optgroup'),
    'button': () => document.createElement('button'),
    'datalist': () => document.createElement('datalist'),
    'output': () => document.createElement('output'),
    'progress': () => document.createElement('progress'),
    'meter': () => document.createElement('meter'),
    // Media
    'img': () => document.createElement('img'),
    'audio': () => document.createElement('audio'),
    'video': () => document.createElement('video'),
    'source': () => document.createElement('source'),
    'track': () => document.createElement('track'),
    'picture': () => document.createElement('picture'),
    'canvas': () => document.createElement('canvas'),
    'iframe': () => document.createElement('iframe'),
    'embed': () => document.createElement('embed'),
    'object': () => document.createElement('object'),
    'param': () => document.createElement('param'),
    'map': () => document.createElement('map'),
    'area': () => document.createElement('area'),
    // Interactive
    'details': () => document.createElement('details'),
    'summary': () => document.createElement('summary'),
    'dialog': () => document.createElement('dialog'),
    // Other
    'template': () => document.createElement('template'),
    'slot': () => document.createElement('slot'),
    'noscript': () => document.createElement('noscript'),
};

function createHtmlElement(tagLower) {
    const factory = HTML_ELEMENT_FACTORIES[tagLower];
    return factory ? factory() : document.createElement('span');
}

/**
 * Create a DOM node from a virtual node (VDOM).
 * SECURITY NOTE: vnode data comes from the trusted server (Django templates
 * rendered server-side). This is the standard LiveView pattern where the
 * server controls all HTML structure via VDOM patches.
 */
function createNodeFromVNode(vnode, inSvgContext = false) {
    if (vnode.tag === '#text') {
        return document.createTextNode(vnode.text || '');
    }

    // Validate tag name against whitelist (security: prevents script injection)
    // Convert to lowercase for consistent matching
    const tagLower = String(vnode.tag || '').toLowerCase();

    // Check if tag is in our whitelists
    const isSvgTag = SVG_TAGS.has(tagLower);
    const isAllowedHtml = ALLOWED_HTML_TAGS.has(tagLower);

    // Determine SVG context for child element creation
    const useSvgNamespace = isSvgTag || inSvgContext;

    // Security: Only pass whitelisted string literals to createElement
    // If not in whitelist, use 'span' as a safe fallback
    let elem;
    if (isSvgTag) {
        // SVG tag: use switch for known values only
        elem = createSvgElement(tagLower);
    } else if (isAllowedHtml) {
        // HTML tag: use switch for known values only
        elem = createHtmlElement(tagLower);
    } else {
        // Unknown tag - use safe span placeholder
        if (globalThis.djustDebug) {
            console.warn('[LiveView] Blocked unknown tag, using span placeholder');
        }
        elem = document.createElement('span');
    }

    if (vnode.attrs) {
        for (const [key, value] of Object.entries(vnode.attrs)) {
            if (key.startsWith('dj-')) {
                // Parse attribute name to extract event type and modifiers
                // e.g., "dj-keydown.enter" -> eventType: "keydown", modifiers: ["enter"]
                const attrParts = key.substring(3).split('.');
                const eventType = attrParts[0];
                const modifiers = attrParts.slice(1);

                // Store the dj-* attribute key so the listener can re-read it
                // at event time. This avoids stale closure args when SetAttribute
                // patches update the attribute value after initial binding.
                const djAttrKey = key;
                elem.addEventListener(eventType, (e) => {
                    // dj-confirm: show confirmation dialog before sending click events
                    if (eventType === 'click') {
                        const confirmMsg = elem.getAttribute('dj-confirm');
                        if (confirmMsg && !window.confirm(confirmMsg)) {
                            e.preventDefault();
                            return;
                        }
                    }

                    // Handle key modifiers for keydown/keyup events
                    if ((eventType === 'keydown' || eventType === 'keyup') && modifiers.length > 0) {
                        const requiredKey = modifiers[0];
                        if (requiredKey === 'enter' && e.key !== 'Enter') return;
                        if (requiredKey === 'escape' && e.key !== 'Escape') return;
                        if (requiredKey === 'space' && e.key !== ' ') return;
                        if (requiredKey === 'tab' && e.key !== 'Tab') return;
                    }

                    // Re-parse handler from DOM attribute at event time to pick up
                    // any changes made by SetAttribute patches since binding.
                    const parsed = parseEventHandler(elem.getAttribute(djAttrKey));

                    e.preventDefault();
                    const params = {};

                    // For form element events (change, input, blur, focus), extract value
                    if (['change', 'input', 'blur', 'focus'].includes(eventType)) {
                        const target = e.target;
                        params.value = target.type === 'checkbox' ? target.checked : target.value;
                        params.field = target.name || target.id || null;
                    } else {
                        // For other events, extract data-* attributes (but skip internal ones)
                        Array.from(elem.attributes).forEach(attr => {
                            if (attr.name.startsWith('data-') &&
                                !attr.name.startsWith('data-liveview') &&
                                !attr.name.startsWith('data-djust') &&
                                attr.name !== 'data-dj-id') {
                                const paramKey = attr.name.substring(5).replace(/-/g, '_');
                                // Prevent prototype pollution attacks
                                if (!UNSAFE_KEYS.includes(paramKey)) {
                                    params[paramKey] = attr.value;
                                }
                            }
                        });
                    }

                    // Add positional arguments from handler syntax if present
                    if (parsed.args.length > 0) {
                        params._args = parsed.args;
                    }

                    // Pass target element for optimistic updates (Phase 3)
                    params._targetElement = e.currentTarget;

                    // Check if event is from a component
                    const componentId = getComponentId(elem);
                    if (componentId) {
                        params.component_id = componentId;
                    }

                    handleEvent(parsed.name, params);
                });
                // Set the dj-* attribute on the DOM so SetAttribute patches
                // can update it and bindLiveViewEvents can re-read it.
                elem.setAttribute(key, value);
                // Mark as bound so bindLiveViewEvents() won't add a duplicate listener
                const boundKey = `liveview${eventType.charAt(0).toUpperCase() + eventType.slice(1)}Bound`;
                elem.dataset[boundKey] = 'true';
            } else {
                if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                    elem.value = value;
                }
                elem.setAttribute(key, value);
            }
        }
    }

    if (vnode.children) {
        // Pass SVG context to children so nested SVG elements are created correctly
        for (const child of vnode.children) {
            elem.appendChild(createNodeFromVNode(child, useSvgNamespace));
        }
    }

    // For textareas, set .value from text content (textContent alone doesn't set displayed value)
    if (elem.tagName === 'TEXTAREA') {
        elem.value = elem.textContent || '';
    }

    return elem;
}

/**
 * Handle dj-update attribute for efficient list updates with temporary_assigns.
 *
 * When using temporary_assigns in djust LiveViews, the server clears large collections
 * from memory after each render. This function ensures the client preserves existing
 * DOM elements and only adds new content.
 *
 * Supported dj-update values:
 *   - "append": Add new children to the end (e.g., chat messages, feed items)
 *   - "prepend": Add new children to the beginning (e.g., notifications)
 *   - "replace": Replace all content (default behavior)
 *   - "ignore": Don't update this element at all (for user-edited content)
 *
 * Example template usage:
 *   <ul dj-update="append" id="messages">
 *     {% for msg in messages %}
 *       <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
 *     {% endfor %}
 *   </ul>
 *
 * @param {HTMLElement} existingRoot - The current DOM root
 * @param {HTMLElement} newRoot - The new content from server
 */
function applyDjUpdateElements(existingRoot, newRoot) {
    // Find all elements with dj-update attribute in the new content
    const djUpdateElements = newRoot.querySelectorAll('[dj-update]');

    if (djUpdateElements.length === 0) {
        // No dj-update elements, do a full replacement
        existingRoot.innerHTML = newRoot.innerHTML;
        return;
    }

    // Track which elements we've handled specially
    const handledIds = new Set();

    // Process each dj-update element
    for (const newElement of djUpdateElements) {
        const updateMode = newElement.getAttribute('dj-update');
        const elementId = newElement.id;

        if (!elementId) {
            console.warn('[LiveView:dj-update] Element with dj-update must have an id:', newElement);
            continue;
        }

        const existingElement = existingRoot.querySelector(`#${CSS.escape(elementId)}`);
        if (!existingElement) {
            // Element doesn't exist yet, will be created by full update
            continue;
        }

        handledIds.add(elementId);

        switch (updateMode) {
            case 'append': {
                // Get new children that don't already exist
                const existingChildIds = new Set(
                    Array.from(existingElement.children)
                        .map(child => child.id)
                        .filter(id => id)
                );

                for (const newChild of Array.from(newElement.children)) {
                    if (newChild.id && !existingChildIds.has(newChild.id)) {
                        // Clone and append new child
                        existingElement.appendChild(newChild.cloneNode(true));
                        console.log(`[LiveView:dj-update] Appended #${newChild.id} to #${elementId}`);
                    }
                }
                break;
            }

            case 'prepend': {
                // Get new children that don't already exist
                const existingChildIds = new Set(
                    Array.from(existingElement.children)
                        .map(child => child.id)
                        .filter(id => id)
                );

                const firstExisting = existingElement.firstChild;
                for (const newChild of Array.from(newElement.children).reverse()) {
                    if (newChild.id && !existingChildIds.has(newChild.id)) {
                        // Clone and prepend new child
                        existingElement.insertBefore(newChild.cloneNode(true), firstExisting);
                        console.log(`[LiveView:dj-update] Prepended #${newChild.id} to #${elementId}`);
                    }
                }
                break;
            }

            case 'ignore':
                // Don't update this element at all
                console.log(`[LiveView:dj-update] Ignoring #${elementId}`);
                break;

            case 'replace':
            default:
                // Standard replacement
                existingElement.innerHTML = newElement.innerHTML;
                // Copy attributes except dj-update
                for (const attr of newElement.attributes) {
                    if (attr.name !== 'dj-update') {
                        existingElement.setAttribute(attr.name, attr.value);
                    }
                }
                break;
        }
    }

    // For elements NOT handled by dj-update, do standard updates
    // This ensures non-dj-update parts of the page still get updated

    // Get all top-level elements in both roots
    const existingChildren = Array.from(existingRoot.children);
    const newChildren = Array.from(newRoot.children);

    // Create a map of new children by id for quick lookup
    const newChildMap = new Map();
    for (const child of newChildren) {
        if (child.id) {
            newChildMap.set(child.id, child);
        }
    }

    // Update or add elements
    for (const newChild of newChildren) {
        if (newChild.id && handledIds.has(newChild.id)) {
            // Already handled by dj-update, skip
            continue;
        }

        if (newChild.id) {
            const existing = existingRoot.querySelector(`#${CSS.escape(newChild.id)}`);
            if (existing) {
                // Check if this element contains dj-update children
                if (newChild.querySelector('[dj-update]')) {
                    // Recursively process
                    applyDjUpdateElements(existing, newChild);
                } else {
                    // Replace content
                    existing.innerHTML = newChild.innerHTML;
                    for (const attr of newChild.attributes) {
                        existing.setAttribute(attr.name, attr.value);
                    }
                }
            } else {
                // New element, append it
                existingRoot.appendChild(newChild.cloneNode(true));
            }
        }
    }

    // Handle elements that exist in old but not in new (remove them)
    // But preserve dj-update elements since their children are managed differently
    for (const existing of existingChildren) {
        if (existing.id && !handledIds.has(existing.id) && !newChildMap.has(existing.id)) {
            // Check if it's a dj-update element
            if (!existing.hasAttribute('dj-update')) {
                existing.remove();
            }
        }
    }
}

/**
 * Stamp data-dj-id attributes from server HTML onto existing pre-rendered DOM.
 * This avoids replacing innerHTML (which destroys whitespace in code blocks).
 * Walks both trees in parallel and copies data-dj-id from server elements to DOM elements.
 * Note: serverHtml is trusted (comes from our own WebSocket mount response).
 */
function _stampDjIds(serverHtml, container) {
    if (!container) {
        container = document.querySelector('[data-djust-view]') ||
                    document.querySelector('[data-djust-root]');
    }
    if (!container) return;

    const parser = new DOMParser();
    const doc = parser.parseFromString('<div>' + serverHtml + '</div>', 'text/html');
    const serverRoot = doc.body.firstChild;

    function stampRecursive(domNode, serverNode) {
        if (!domNode || !serverNode) return;
        if (serverNode.nodeType !== Node.ELEMENT_NODE || domNode.nodeType !== Node.ELEMENT_NODE) return;

        // Bail out if structure diverges (e.g. browser extension injected elements)
        if (domNode.tagName !== serverNode.tagName) return;

        const djId = serverNode.getAttribute('data-dj-id');
        if (djId) {
            domNode.setAttribute('data-dj-id', djId);
        }

        // Walk children in parallel (element nodes only)
        const domChildren = Array.from(domNode.children);
        const serverChildren = Array.from(serverNode.children);
        const len = Math.min(domChildren.length, serverChildren.length);
        for (let i = 0; i < len; i++) {
            stampRecursive(domChildren[i], serverChildren[i]);
        }
    }

    // Walk container children vs server root children
    const domChildren = Array.from(container.children);
    const serverChildren = Array.from(serverRoot.children);
    const len = Math.min(domChildren.length, serverChildren.length);
    for (let i = 0; i < len; i++) {
        stampRecursive(domChildren[i], serverChildren[i]);
    }
}

/**
 * Get significant children (elements and non-whitespace text nodes).
 * Preserves all whitespace inside <pre>, <code>, and <textarea> elements.
 */
function getSignificantChildren(node) {
    // Check if we're inside a whitespace-preserving element
    const preserveWhitespace = isWhitespacePreserving(node);

    return Array.from(node.childNodes).filter(child => {
        if (child.nodeType === Node.ELEMENT_NODE) return true;
        if (child.nodeType === Node.TEXT_NODE) {
            // Preserve all text nodes inside pre/code/textarea
            if (preserveWhitespace) return true;
            return child.textContent.trim().length > 0;
        }
        return false;
    });
}

/**
 * Check if a node is a whitespace-preserving element or inside one.
 */
function isWhitespacePreserving(node) {
    const WHITESPACE_PRESERVING_TAGS = ['PRE', 'CODE', 'TEXTAREA', 'SCRIPT', 'STYLE'];
    let current = node;
    while (current) {
        if (current.nodeType === Node.ELEMENT_NODE &&
            WHITESPACE_PRESERVING_TAGS.includes(current.tagName)) {
            return true;
        }
        current = current.parentNode;
    }
    return false;
}

// Export for testing
window.djust.getSignificantChildren = getSignificantChildren;
window.djust._applySinglePatch = applySinglePatch;
window.djust._stampDjIds = _stampDjIds;

/**
 * Group patches by their parent path for batching.
 */
function groupPatchesByParent(patches) {
    const groups = new Map(); // Use Map to avoid prototype pollution
    for (const patch of patches) {
        const parentPath = patch.path.slice(0, -1).join('/');
        if (!groups.has(parentPath)) {
            groups.set(parentPath, []);
        }
        groups.get(parentPath).push(patch);
    }
    return groups;
}

/**
 * Group InsertChild patches with consecutive indices.
 * Only consecutive inserts can be batched with DocumentFragment.
 *
 * Example: [2, 3, 4, 7, 8] -> [[2,3,4], [7,8]]
 *
 * @param {Array} inserts - Array of InsertChild patches
 * @returns {Array<Array>} - Groups of consecutive inserts
 */
function groupConsecutiveInserts(inserts) {
    if (inserts.length === 0) return [];

    // Sort by index first
    inserts.sort((a, b) => a.index - b.index);

    const groups = [];
    let currentGroup = [inserts[0]];

    for (let i = 1; i < inserts.length; i++) {
        // Check if this insert is consecutive with the previous one
        if (inserts[i].index === inserts[i - 1].index + 1) {
            currentGroup.push(inserts[i]);
        } else {
            // Start a new group
            groups.push(currentGroup);
            currentGroup = [inserts[i]];
        }
    }

    // Don't forget the last group
    groups.push(currentGroup);

    return groups;
}

/**
 * Apply a single patch operation.
 *
 * Patches include:
 * - `path`: Index-based path (fallback)
 * - `d`: Compact djust ID for O(1) querySelector lookup
 */
function applySinglePatch(patch, targetSelector = null) {
    // Use ID-based resolution (d field) with path as fallback
    const node = getNodeByPath(patch.path, patch.d, targetSelector);
    if (!node) {
        // Sanitize for logging (patches come from trusted server, but log defensively)
        const safePath = Array.isArray(patch.path) ? patch.path.map(Number).join('/') : 'invalid';
        console.warn(`[LiveView] Failed to find node: path=${safePath}, id=${sanitizeIdForLog(patch.d)}`);
        return false;
    }

    try {
        switch (patch.type) {
            case 'Replace':
                const newNode = createNodeFromVNode(patch.node, isInSvgContext(node.parentNode));
                node.parentNode.replaceChild(newNode, node);
                // Apply enter transition to replacement node
                if (window.djust.transitions) {
                    window.djust.transitions.applyEnter(newNode);
                }
                break;

            case 'SetText':
                node.textContent = patch.text;
                // If this is a text node inside a textarea, also update the textarea's .value
                // (textContent alone doesn't update what's displayed in the textarea)
                if (node.parentNode && node.parentNode.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node.parentNode) {
                        node.parentNode.value = patch.text;
                    }
                }
                break;

            case 'SetAttr':
                if (patch.key === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                    if (document.activeElement !== node) {
                        node.value = patch.value;
                    }
                    node.setAttribute(patch.key, patch.value);
                } else {
                    node.setAttribute(patch.key, patch.value);
                }
                break;

            case 'RemoveAttr':
                // Never remove dj-* event handler attributes — defense in depth
                // against VDOM path mismatches from conditional rendering
                if (patch.key && patch.key.startsWith('dj-')) {
                    break;
                }
                node.removeAttribute(patch.key);
                break;

            case 'InsertChild': {
                const newChild = createNodeFromVNode(patch.node, isInSvgContext(node));
                const children = getSignificantChildren(node);
                const refChild = children[patch.index];
                if (refChild) {
                    node.insertBefore(newChild, refChild);
                } else {
                    node.appendChild(newChild);
                }
                // Apply enter transition to newly inserted element
                if (window.djust.transitions) {
                    window.djust.transitions.applyEnter(newChild);
                }
                // If inserting a text node into a textarea, also update its .value
                if (newChild.nodeType === Node.TEXT_NODE && node.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node) {
                        node.value = newChild.textContent || '';
                    }
                }
                break;
            }

            case 'RemoveChild': {
                const children = getSignificantChildren(node);
                const child = children[patch.index];
                if (child) {
                    const wasTextNode = child.nodeType === Node.TEXT_NODE;
                    const parentTag = node.tagName;
                    // Check for leave transition before removing
                    if (window.djust.transitions && window.djust.transitions.hasTransition(child)) {
                        window.djust.transitions.applyLeave(child).then(() => {
                            if (child.parentNode) {
                                child.parentNode.removeChild(child);
                            }
                        });
                    } else {
                        node.removeChild(child);
                    }
                    // If removing a text node from a textarea, also clear its .value
                    // (removing textContent alone doesn't update what's displayed)
                    if (wasTextNode && parentTag === 'TEXTAREA' && document.activeElement !== node) {
                        node.value = '';
                    }
                }
                break;
            }

            case 'MoveChild': {
                let child;
                if (patch.child_d) {
                    // ID-based resolution: find direct child by data-dj-id (resilient to index shifts)
                    const escaped = CSS.escape(patch.child_d);
                    child = node.querySelector(`:scope > [data-dj-id="${escaped}"]`);
                }
                if (!child) {
                    // Fallback: index-based
                    const fallbackChildren = getSignificantChildren(node);
                    child = fallbackChildren[patch.from];
                }
                if (child) {
                    const children = getSignificantChildren(node);
                    const refChild = children[patch.to];
                    if (refChild) {
                        node.insertBefore(child, refChild);
                    } else {
                        node.appendChild(child);
                    }
                }
                break;
            }

            default:
                // Sanitize type for logging
                const safeType = String(patch.type || 'undefined').slice(0, 50);
                console.warn('[LiveView] Unknown patch type:', safeType);
                return false;
        }

        return true;
    } catch (error) {
        // Log error without potentially sensitive patch data
        console.error('[LiveView] Error applying patch:', error.message || error);
        return false;
    }
}

/**
 * Apply VDOM patches with optimized batching.
 *
 * Improvements over sequential application:
 * - Groups patches by parent path for batch operations
 * - Uses DocumentFragment for consecutive InsertChild patches on same parent
 * - Skips batching overhead for small patch sets (<=10 patches)
 */
function applyPatches(patches, targetSelector = null) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Sort patches: RemoveChild in descending order to preserve indices
    patches.sort((a, b) => {
        if (a.type === 'RemoveChild' && b.type === 'RemoveChild') {
            const pathA = JSON.stringify(a.path);
            const pathB = JSON.stringify(b.path);
            if (pathA === pathB) {
                return b.index - a.index;
            }
        }
        return 0;
    });

    // For small patch sets, apply directly without batching overhead
    if (patches.length <= 10) {
        let failedCount = 0;
        for (const patch of patches) {
            if (!applySinglePatch(patch, targetSelector)) {
                failedCount++;
            }
        }
        if (failedCount > 0) {
            console.error(`[LiveView] ${failedCount}/${patches.length} patches failed`);
            return false;
        }
        return true;
    }

    // For larger patch sets, use batching
    let failedCount = 0;
    let successCount = 0;

    // Group patches by parent for potential batching
    const patchGroups = groupPatchesByParent(patches);

    for (const [parentPath, group] of patchGroups) {
        // Optimization: Use DocumentFragment for consecutive InsertChild on same parent
        const insertPatches = group.filter(p => p.type === 'InsertChild');

        if (insertPatches.length >= 3) {
            // Group only consecutive inserts (can't batch non-consecutive indices)
            const consecutiveGroups = groupConsecutiveInserts(insertPatches);

            for (const consecutiveGroup of consecutiveGroups) {
                // Only batch if we have 3+ consecutive inserts
                if (consecutiveGroup.length < 3) continue;

                const firstPatch = consecutiveGroup[0];
                // Use ID-based resolution for parent node
                const parentNode = getNodeByPath(firstPatch.path, firstPatch.d);

                if (parentNode) {
                    try {
                        const fragment = document.createDocumentFragment();
                        const svgContext = isInSvgContext(parentNode);
                        for (const patch of consecutiveGroup) {
                            const newChild = createNodeFromVNode(patch.node, svgContext);
                            fragment.appendChild(newChild);
                            successCount++;
                        }

                        // Insert fragment at the first index position
                        const children = getSignificantChildren(parentNode);
                        const firstIndex = consecutiveGroup[0].index;
                        const refChild = children[firstIndex];

                        if (refChild) {
                            parentNode.insertBefore(fragment, refChild);
                        } else {
                            parentNode.appendChild(fragment);
                        }

                        // Mark these patches as processed
                        const processedSet = new Set(consecutiveGroup);
                        for (let i = group.length - 1; i >= 0; i--) {
                            if (processedSet.has(group[i])) {
                                group.splice(i, 1);
                            }
                        }
                    } catch (error) {
                        console.error('[LiveView] Batch insert failed, falling back to individual patches:', error.message);
                        // On failure, patches remain in group for individual processing
                        successCount -= consecutiveGroup.length;  // Undo count
                    }
                }
            }
        }

        // Apply remaining patches individually
        for (const patch of group) {
            if (applySinglePatch(patch, targetSelector)) {
                successCount++;
            } else {
                failedCount++;
            }
        }
    }

    if (failedCount > 0) {
        console.error(`[LiveView] ${failedCount}/${patches.length} patches failed`);
        return false;
    }

    return true;
}

// ============================================================================
// Lazy Hydration Support (Performance Optimization)
// ============================================================================

/**
 * Lazy LiveView Hydration Manager
 *
 * Defers WebSocket connection and LiveView mounting until elements enter the
 * viewport. This significantly reduces memory usage and WebSocket connections
 * for pages with below-fold LiveView components.
 *
 * Usage:
 *   <div data-djust-view="my_view" data-djust-lazy>
 *     <!-- Content loads when element scrolls into view -->
 *   </div>
 *
 *   <div data-djust-view="my_view" data-djust-lazy="click">
 *     <!-- Content loads on first user interaction -->
 *   </div>
 *
 * Supported lazy modes:
 *   - "viewport" (default): Mount when element enters viewport
 *   - "click": Mount on first click within the element
 *   - "hover": Mount on first mouse hover
 *   - "idle": Mount when browser is idle (requestIdleCallback)
 */
const lazyHydrationManager = {
    // Set of element IDs that have been hydrated
    hydratedElements: new Set(),

    // IntersectionObserver instance for viewport-based hydration
    viewportObserver: null,

    // Queue of elements waiting for WebSocket connection
    pendingMounts: [],

    // Initialize lazy hydration
    init() {
        // Clear pending mounts on reinit (e.g., TurboNav navigation)
        this.pendingMounts = [];
        this.hydratedElements.clear();

        // Inject CSS for lazy click elements (only once)
        if (!document.getElementById('djust-lazy-styles')) {
            const style = document.createElement('style');
            style.id = 'djust-lazy-styles';
            style.textContent = '.djust-lazy-click { cursor: pointer; }';
            document.head.appendChild(style);
        }

        // Create viewport observer if supported
        if ('IntersectionObserver' in window) {
            this.viewportObserver = new IntersectionObserver(
                (entries) => this.handleIntersection(entries),
                {
                    // Start loading slightly before element is visible
                    rootMargin: '50px',
                    threshold: 0
                }
            );
        }
    },

    // Handle viewport intersection
    handleIntersection(entries) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const element = entry.target;
                this.hydrateElement(element);
                this.viewportObserver.unobserve(element);
            }
        });
    },

    // Register an element for lazy hydration
    register(element) {
        const lazyMode = element.getAttribute('data-djust-lazy') || 'viewport';

        switch (lazyMode) {
            case 'click':
                element.addEventListener('click', () => this.hydrateElement(element), { once: true });
                // Add CSS class for styling (avoids overriding inline styles)
                element.classList.add('djust-lazy-click');
                break;

            case 'hover':
                element.addEventListener('mouseenter', () => this.hydrateElement(element), { once: true });
                break;

            case 'idle':
                if ('requestIdleCallback' in window) {
                    requestIdleCallback(() => this.hydrateElement(element), { timeout: 5000 });
                } else {
                    // Fallback: use setTimeout
                    setTimeout(() => this.hydrateElement(element), 2000);
                }
                break;

            case 'viewport':
            case '':
            default:
                if (this.viewportObserver) {
                    this.viewportObserver.observe(element);
                } else {
                    // Fallback for browsers without IntersectionObserver
                    this.hydrateElement(element);
                }
                break;
        }

        if (globalThis.djustDebug) {
            console.log(`[LiveView:lazy] Registered element for lazy hydration (mode: ${lazyMode})`, element);
        }
    },

    // Hydrate a single element
    hydrateElement(element) {
        const elementId = element.id || element.getAttribute('data-djust-view');

        // Prevent double hydration
        if (this.hydratedElements.has(elementId)) {
            return;
        }
        this.hydratedElements.add(elementId);

        const viewPath = element.getAttribute('data-djust-view');
        if (!viewPath) {
            console.warn('[LiveView:lazy] Element missing data-djust-view attribute', element);
            return;
        }

        console.log(`[LiveView:lazy] Hydrating: ${viewPath}`);

        // Ensure WebSocket is connected
        if (!liveViewWS || !liveViewWS.enabled) {
            liveViewWS = new LiveViewWebSocket();
            window.djust.liveViewInstance = liveViewWS;
            liveViewWS.connect();
        }

        // Wait for WebSocket connection then mount
        if (liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN) {
            this.mountElement(element, viewPath);
        } else {
            // Queue mount for when WebSocket connects (handles multiple lazy elements)
            this.pendingMounts.push({ element, viewPath });

            // Set up connection callback if not already done
            if (this.pendingMounts.length === 1 && liveViewWS.ws) {
                const originalOnOpen = liveViewWS.ws.onopen;
                liveViewWS.ws.onopen = (event) => {
                    if (originalOnOpen) originalOnOpen.call(liveViewWS.ws, event);
                    this.processPendingMounts();
                };
            }
        }
    },

    // Process all queued mounts when WebSocket connects
    processPendingMounts() {
        console.log(`[LiveView:lazy] Processing ${this.pendingMounts.length} pending mounts`);
        const mounts = this.pendingMounts.slice();
        this.pendingMounts = [];
        mounts.forEach(({ element, viewPath }) => {
            this.mountElement(element, viewPath);
        });
    },

    // Mount a specific element
    mountElement(element, viewPath) {
        // Check if content was already pre-rendered
        const hasContent = element.innerHTML && element.innerHTML.trim().length > 0;

        if (hasContent) {
            console.log('[LiveView:lazy] Using pre-rendered content');
            liveViewWS.skipMountHtml = true;
        }

        // Pass URL query params
        const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
        liveViewWS.mount(viewPath, urlParams);

        // Remove lazy attribute to indicate hydration complete
        element.removeAttribute('data-djust-lazy');
        element.setAttribute('data-live-hydrated', 'true');

        // Bind events to the newly hydrated content
        bindLiveViewEvents();
    },

    // Check if an element is lazily loaded
    isLazy(element) {
        return element.hasAttribute('data-djust-lazy');
    },

    // Force hydrate all lazy elements (useful for testing or SPA navigation)
    hydrateAll() {
        document.querySelectorAll('[data-djust-lazy]').forEach(el => {
            this.hydrateElement(el);
        });
    }
};

// Expose lazy hydration API
window.djust.lazyHydration = lazyHydrationManager;

// Initialize on load (support both normal page load and dynamic script injection via TurboNav)
function djustInit() {
    console.log('[LiveView] Initializing...');

    // Initialize lazy hydration manager
    lazyHydrationManager.init();

    // Find all LiveView containers
    const allContainers = document.querySelectorAll('[data-djust-view]');
    const lazyContainers = document.querySelectorAll('[data-djust-view][data-djust-lazy]');
    const eagerContainers = document.querySelectorAll('[data-djust-view]:not([data-djust-lazy])');

    console.log(`[LiveView] Found ${allContainers.length} containers (${lazyContainers.length} lazy, ${eagerContainers.length} eager)`);

    // Register lazy containers with the lazy hydration manager
    lazyContainers.forEach(container => {
        lazyHydrationManager.register(container);
    });

    // Only initialize WebSocket if there are eager containers
    if (eagerContainers.length > 0) {
        // Initialize WebSocket
        liveViewWS = new LiveViewWebSocket();
        window.djust.liveViewInstance = liveViewWS;
        liveViewWS.connect();

        // Start heartbeat
        liveViewWS.startHeartbeat();
    } else if (lazyContainers.length > 0) {
        console.log('[LiveView] Deferring WebSocket connection until lazy elements are needed');
    }

    // Initialize React counters (if any)
    initReactCounters();

    // Bind initial events
    bindLiveViewEvents();

    // Initialize Draft Mode
    initDraftMode();

    // Scan and register dj-loading attributes
    globalLoadingManager.scanAndRegister();

    // Mark as initialized so turbo:load handler knows we're ready
    window.djustInitialized = true;
    console.log('[LiveView] Initialization complete, window.djustInitialized = true');

    // Check if we have a pending turbo reinit (turbo:load fired before we finished init)
    if (pendingTurboReinit) {
        console.log('[LiveView] Processing pending turbo:load reinit');
        pendingTurboReinit = false;
        reinitLiveViewForTurboNav();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', djustInit);
} else {
    djustInit();
}

} // End of double-load guard
// ============================================================================
// File Upload Support
// ============================================================================
// Handles: file selection, chunked binary WebSocket upload, progress tracking,
// image previews, and drag-and-drop zones.
//
// Template directives:
//   dj-upload="name"         — file input bound to upload slot
//   dj-upload-drop="name"    — drop zone for drag-and-drop
//   dj-upload-preview="name" — container for image previews
//   dj-upload-progress="name"— container for progress bars

(function() {
    'use strict';

    // Frame types matching server protocol
    const FRAME_CHUNK    = 0x01;
    const FRAME_COMPLETE = 0x02;
    const FRAME_CANCEL   = 0x03;

    const DEFAULT_CHUNK_SIZE = 64 * 1024; // 64KB

    // Active uploads: ref -> { file, config, chunkIndex, resolve, reject }
    const activeUploads = new Map();

    // Upload configs from server mount response
    let uploadConfigs = {};

    /**
     * Initialize upload configs from server mount data.
     * Called when mount response includes upload configuration.
     */
    function setUploadConfigs(configs) {
        uploadConfigs = configs || {};
        console.log('[Upload] Configs loaded:', Object.keys(uploadConfigs));
    }

    /**
     * Generate a UUID v4 as bytes (Uint8Array of 16 bytes).
     */
    function uuidBytes() {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
        bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1
        return bytes;
    }

    /**
     * Convert UUID bytes to string format.
     */
    function uuidToString(bytes) {
        const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
        return [
            hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
            hex.slice(16, 20), hex.slice(20)
        ].join('-');
    }

    /**
     * Build a binary upload frame.
     * @param {number} frameType - FRAME_CHUNK, FRAME_COMPLETE, or FRAME_CANCEL
     * @param {Uint8Array} refBytes - 16-byte UUID
     * @param {ArrayBuffer|null} payload - Chunk data (for FRAME_CHUNK)
     * @param {number} chunkIndex - Chunk index (for FRAME_CHUNK)
     * @returns {ArrayBuffer}
     */
    function buildFrame(frameType, refBytes, payload, chunkIndex) {
        if (frameType === FRAME_CHUNK && payload) {
            const header = new Uint8Array(21); // 1 + 16 + 4
            header[0] = frameType;
            header.set(refBytes, 1);
            const view = new DataView(header.buffer);
            view.setUint32(17, chunkIndex, false); // big-endian
            const frame = new Uint8Array(21 + payload.byteLength);
            frame.set(header);
            frame.set(new Uint8Array(payload), 21);
            return frame.buffer;
        } else {
            // COMPLETE or CANCEL: just type + ref
            const frame = new Uint8Array(17);
            frame[0] = frameType;
            frame.set(refBytes, 1);
            return frame.buffer;
        }
    }

    /**
     * Upload a single file via chunked binary WebSocket frames.
     */
    async function uploadFile(ws, uploadName, file, config) {
        const refBytes = uuidBytes();
        const ref = uuidToString(refBytes);
        const chunkSize = (config && config.chunk_size) || DEFAULT_CHUNK_SIZE;

        // Announce the upload to the server via JSON
        ws.sendMessage({
            type: 'upload_register',
            upload_name: uploadName,
            ref: ref,
            client_name: file.name,
            client_type: file.type,
            client_size: file.size,
        });

        return new Promise((resolve, reject) => {
            activeUploads.set(ref, {
                file, config, uploadName, refBytes, resolve, reject,
                chunkIndex: 0, sent: 0,
            });

            // Read file and send chunks
            const reader = new FileReader();
            reader.onload = function() {
                const buffer = reader.result;
                let offset = 0;
                let chunkIndex = 0;

                function sendNextChunk() {
                    if (offset >= buffer.byteLength) {
                        // All chunks sent — send complete frame
                        const completeFrame = buildFrame(FRAME_COMPLETE, refBytes);
                        ws.ws.send(completeFrame);
                        return;
                    }

                    const end = Math.min(offset + chunkSize, buffer.byteLength);
                    const chunk = buffer.slice(offset, end);
                    const frame = buildFrame(FRAME_CHUNK, refBytes, chunk, chunkIndex);
                    ws.ws.send(frame);

                    const upload = activeUploads.get(ref);
                    if (upload) {
                        upload.sent = end;
                        upload.chunkIndex = chunkIndex + 1;
                    }

                    offset = end;
                    chunkIndex++;

                    // Small delay to avoid overwhelming the WebSocket
                    if (ws.ws.bufferedAmount > chunkSize * 4) {
                        setTimeout(sendNextChunk, 10);
                    } else {
                        sendNextChunk();
                    }
                }

                sendNextChunk();
            };

            reader.onerror = function() {
                reject(new Error('Failed to read file: ' + file.name));
                activeUploads.delete(ref);
            };

            reader.readAsArrayBuffer(file);
        });
    }

    /**
     * Cancel an active upload.
     */
    function cancelUpload(ws, ref) {
        const upload = activeUploads.get(ref);
        if (upload) {
            const cancelFrame = buildFrame(FRAME_CANCEL, upload.refBytes);
            ws.ws.send(cancelFrame);
            activeUploads.delete(ref);
            if (upload.reject) {
                upload.reject(new Error('Upload cancelled'));
            }
        }
    }

    /**
     * Handle upload progress message from server.
     */
    function handleUploadProgress(data) {
        const { ref, progress, status } = data;
        const upload = activeUploads.get(ref);

        // Update progress bars in DOM
        document.querySelectorAll(`[data-upload-ref="${ref}"] .upload-progress-bar`).forEach(bar => {
            bar.style.width = progress + '%';
            bar.setAttribute('aria-valuenow', progress);
        });

        // Update progress text
        document.querySelectorAll(`[data-upload-ref="${ref}"] .upload-progress-text`).forEach(el => {
            el.textContent = progress + '%';
        });

        // Dispatch custom event for app-level handling
        window.dispatchEvent(new CustomEvent('djust:upload:progress', {
            detail: { ref, progress, status, uploadName: upload ? upload.uploadName : null }
        }));

        if (status === 'complete') {
            if (upload && upload.resolve) {
                upload.resolve({ ref, status: 'complete' });
            }
            activeUploads.delete(ref);
        } else if (status === 'error') {
            if (upload && upload.reject) {
                upload.reject(new Error('Upload failed on server'));
            }
            activeUploads.delete(ref);
        }
    }

    /**
     * Generate image preview for a file.
     * @returns {Promise<string>} Data URL
     */
    function generatePreview(file) {
        return new Promise((resolve, reject) => {
            if (!file.type.startsWith('image/')) {
                reject(new Error('Not an image'));
                return;
            }
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    /**
     * Show previews in a dj-upload-preview container.
     */
    async function showPreviews(uploadName, files) {
        const containers = document.querySelectorAll(`[dj-upload-preview="${uploadName}"]`);
        if (containers.length === 0) return;

        for (const container of containers) {
            container.innerHTML = '';

            for (const file of files) {
                const wrapper = document.createElement('div');
                wrapper.className = 'upload-preview-item';

                if (file.type.startsWith('image/')) {
                    try {
                        const dataUrl = await generatePreview(file);
                        const img = document.createElement('img');
                        img.src = dataUrl;
                        img.alt = file.name;
                        img.className = 'upload-preview-image';
                        wrapper.appendChild(img);
                    } catch (e) {
                        // Fall through to filename display
                    }
                }

                const info = document.createElement('span');
                info.className = 'upload-preview-name';
                info.textContent = file.name;
                wrapper.appendChild(info);

                const size = document.createElement('span');
                size.className = 'upload-preview-size';
                size.textContent = formatSize(file.size);
                wrapper.appendChild(size);

                container.appendChild(wrapper);
            }
        }
    }

    /**
     * Create progress bar HTML for an upload.
     */
    function createProgressBar(ref, fileName) {
        const wrapper = document.createElement('div');
        wrapper.className = 'upload-progress-item';
        wrapper.setAttribute('data-upload-ref', ref);

        wrapper.innerHTML = `
            <div class="upload-progress-name">${escapeHtml(fileName)}</div>
            <div class="upload-progress-track">
                <div class="upload-progress-bar" role="progressbar"
                     aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"
                     style="width: 0%"></div>
            </div>
            <span class="upload-progress-text">0%</span>
        `;

        return wrapper;
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    /**
     * Handle file selection from dj-upload input.
     */
    async function handleFileSelect(input, uploadName) {
        const files = Array.from(input.files);
        if (files.length === 0) return;

        const config = uploadConfigs[uploadName];

        // Show previews
        await showPreviews(uploadName, files);

        // Create progress bars
        const progressContainers = document.querySelectorAll(`[dj-upload-progress="${uploadName}"]`);

        // Auto-upload if configured (default)
        if (!config || config.auto_upload !== false) {
            if (!liveViewWS || !liveViewWS.ws || liveViewWS.ws.readyState !== WebSocket.OPEN) {
                console.error('[Upload] WebSocket not connected');
                return;
            }

            for (const file of files) {
                // Validate client-side
                if (config) {
                    if (file.size > config.max_file_size) {
                        console.warn(`[Upload] File too large: ${file.name} (${file.size} > ${config.max_file_size})`);
                        window.dispatchEvent(new CustomEvent('djust:upload:error', {
                            detail: { file: file.name, error: 'File too large' }
                        }));
                        continue;
                    }
                }

                try {
                    const result = await uploadFile(liveViewWS, uploadName, file, config);
                    console.log(`[Upload] Complete: ${file.name}`, result);
                } catch (err) {
                    console.error(`[Upload] Failed: ${file.name}`, err);
                }
            }
        }
    }

    /**
     * Bind upload-related event handlers.
     * Called after DOM updates (mount, patch, etc.)
     */
    function bindUploadHandlers() {
        // File inputs with dj-upload
        document.querySelectorAll('[dj-upload]').forEach(input => {
            if (input._djUploadBound) return;
            input._djUploadBound = true;

            const uploadName = input.getAttribute('dj-upload');

            // Set accept attribute from config
            const config = uploadConfigs[uploadName];
            if (config && config.accept && !input.getAttribute('accept')) {
                input.setAttribute('accept', config.accept);
            }
            if (config && config.max_entries > 1 && !input.hasAttribute('multiple')) {
                input.setAttribute('multiple', '');
            }

            input.addEventListener('change', () => handleFileSelect(input, uploadName));
        });

        // Drop zones with dj-upload-drop
        document.querySelectorAll('[dj-upload-drop]').forEach(zone => {
            if (zone._djDropBound) return;
            zone._djDropBound = true;

            const uploadName = zone.getAttribute('dj-upload-drop');

            zone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.add('upload-dragover');
            });

            zone.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.remove('upload-dragover');
            });

            zone.addEventListener('drop', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                zone.classList.remove('upload-dragover');

                const files = Array.from(e.dataTransfer.files);
                if (files.length === 0) return;

                const config = uploadConfigs[uploadName];
                await showPreviews(uploadName, files);

                if (!liveViewWS || !liveViewWS.ws || liveViewWS.ws.readyState !== WebSocket.OPEN) {
                    console.error('[Upload] WebSocket not connected');
                    return;
                }

                for (const file of files) {
                    if (config && file.size > config.max_file_size) {
                        window.dispatchEvent(new CustomEvent('djust:upload:error', {
                            detail: { file: file.name, error: 'File too large' }
                        }));
                        continue;
                    }
                    try {
                        await uploadFile(liveViewWS, uploadName, file, config);
                    } catch (err) {
                        console.error(`[Upload] Drop upload failed: ${file.name}`, err);
                    }
                }
            });
        });
    }

    // ========================================================================
    // Exports
    // ========================================================================

    window.djust.uploads = {
        setConfigs: setUploadConfigs,
        handleProgress: handleUploadProgress,
        bindHandlers: bindUploadHandlers,
        cancelUpload: (ref) => cancelUpload(liveViewWS, ref),
        activeUploads: activeUploads,
    };

})();
// ============================================================================
// Optimistic UI and Enhanced Debounce
// ============================================================================

// Global storage for optimistic updates
const optimisticUpdates = new Map();
const optimisticCounters = new Map();

/**
 * Apply optimistic update to DOM element based on dj-optimistic attribute
 * @param {HTMLElement} element - Target element
 * @param {string} eventName - Name of the event being triggered
 */
function applyOptimisticUpdate(element, eventName) {
    const optimisticAttr = element.getAttribute('dj-optimistic');
    if (!optimisticAttr) {
        return null;
    }

    // Parse optimistic attribute: "field:value" or "field:!field"
    const [fieldPath, valueExpr] = optimisticAttr.split(':', 2);
    if (!fieldPath || valueExpr === undefined) {
        console.warn('[djust:optimistic] Invalid dj-optimistic format, expected "field:value"');
        return null;
    }

    // Find target element for the update (could be the element itself or a data-target)
    const targetSelector = element.getAttribute('dj-target');
    const targetElement = targetSelector 
        ? document.querySelector(targetSelector)
        : element;

    if (!targetElement) {
        console.warn('[djust:optimistic] Target element not found for optimistic update');
        return null;
    }

    // Store original state before applying optimistic update
    const updateId = `${eventName}-${Date.now()}-${Math.random()}`;
    const originalState = {
        attributes: new Map(),
        textContent: targetElement.textContent,
        innerHTML: targetElement.innerHTML,
        dataset: { ...targetElement.dataset }
    };

    // Store all current attributes
    for (let attr of targetElement.attributes) {
        originalState.attributes.set(attr.name, attr.value);
    }

    optimisticUpdates.set(updateId, {
        element: targetElement,
        originalState,
        fieldPath,
        valueExpr
    });

    // Apply the optimistic update
    applyOptimisticValue(targetElement, fieldPath, valueExpr, originalState);

    return updateId;
}

/**
 * Apply optimistic value to the target element
 * @param {HTMLElement} targetElement - Element to update
 * @param {string} fieldPath - Field path (supports dot notation)
 * @param {string} valueExpr - Value expression (literal value or !field for toggle)
 * @param {Object} originalState - Original state for reference
 */
function applyOptimisticValue(targetElement, fieldPath, valueExpr, originalState) {
    // Handle toggle expressions like "!liked"
    if (valueExpr.startsWith('!')) {
        const toggleField = valueExpr.slice(1);
        const currentValue = getFieldValue(targetElement, toggleField, originalState);
        const newValue = !coerceToBoolean(currentValue);
        setFieldValue(targetElement, fieldPath, newValue);
        return;
    }

    // Handle literal values
    const newValue = parseOptimisticValue(valueExpr);
    setFieldValue(targetElement, fieldPath, newValue);
}

/**
 * Get current field value from element
 * @param {HTMLElement} element - Target element
 * @param {string} fieldPath - Field path
 * @param {Object} originalState - Original state for fallback
 * @returns {any} Current field value
 */
function getFieldValue(element, fieldPath, originalState) {
    // Handle data attributes
    if (fieldPath.startsWith('data-')) {
        const dataKey = fieldPath.slice(5).replace(/-/g, '');
        return element.dataset[dataKey] || originalState.dataset[dataKey];
    }

    // Handle class-related fields
    if (fieldPath === 'class') {
        return element.className;
    }

    // Handle text content
    if (fieldPath === 'textContent' || fieldPath === 'text') {
        return element.textContent;
    }

    // Handle innerHTML
    if (fieldPath === 'innerHTML' || fieldPath === 'html') {
        return element.innerHTML;
    }

    // Handle other attributes
    return element.getAttribute(fieldPath) || originalState.attributes.get(fieldPath);
}

/**
 * Set field value on element
 * @param {HTMLElement} element - Target element
 * @param {string} fieldPath - Field path
 * @param {any} value - New value
 */
function setFieldValue(element, fieldPath, value) {
    // Handle data attributes
    if (fieldPath.startsWith('data-')) {
        const dataKey = fieldPath.slice(5).replace(/-/g, '');
        element.dataset[dataKey] = String(value);
        return;
    }

    // Handle class-related fields
    if (fieldPath === 'class') {
        element.className = String(value);
        return;
    }

    // Handle text content
    if (fieldPath === 'textContent' || fieldPath === 'text') {
        element.textContent = String(value);
        return;
    }

    // Handle innerHTML
    if (fieldPath === 'innerHTML' || fieldPath === 'html') {
        element.innerHTML = String(value);
        return;
    }

    // Handle boolean attributes
    if (typeof value === 'boolean') {
        if (value) {
            element.setAttribute(fieldPath, '');
        } else {
            element.removeAttribute(fieldPath);
        }
        return;
    }

    // Handle other attributes
    element.setAttribute(fieldPath, String(value));
}

/**
 * Parse optimistic value from string
 * @param {string} valueExpr - Value expression
 * @returns {any} Parsed value
 */
function parseOptimisticValue(valueExpr) {
    // Handle quoted strings
    if ((valueExpr.startsWith('"') && valueExpr.endsWith('"')) ||
        (valueExpr.startsWith("'") && valueExpr.endsWith("'"))) {
        return valueExpr.slice(1, -1);
    }

    // Handle booleans
    if (valueExpr === 'true') return true;
    if (valueExpr === 'false') return false;

    // Handle null
    if (valueExpr === 'null') return null;

    // Handle numbers
    if (/^-?\d+$/.test(valueExpr)) {
        return parseInt(valueExpr, 10);
    }
    if (/^-?\d*\.\d+$/.test(valueExpr)) {
        return parseFloat(valueExpr);
    }

    // Return as string
    return valueExpr;
}

/**
 * Coerce value to boolean
 * @param {any} value - Value to coerce
 * @returns {boolean} Boolean value
 */
function coerceToBoolean(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
        return ['true', '1', 'yes', 'on', 'checked'].includes(value.toLowerCase());
    }
    return Boolean(value);
}

/**
 * Revert optimistic update
 * @param {string} updateId - Update ID to revert
 */
function revertOptimisticUpdate(updateId) {
    const update = optimisticUpdates.get(updateId);
    if (!update) {
        return;
    }

    const { element, originalState } = update;

    // Restore original attributes
    // First, remove all current attributes
    const currentAttrs = [...element.attributes];
    currentAttrs.forEach(attr => {
        if (!originalState.attributes.has(attr.name)) {
            element.removeAttribute(attr.name);
        }
    });

    // Then restore original attributes
    for (let [name, value] of originalState.attributes) {
        element.setAttribute(name, value);
    }

    // Restore dataset
    Object.keys(element.dataset).forEach(key => {
        delete element.dataset[key];
    });
    Object.assign(element.dataset, originalState.dataset);

    // Restore content
    element.innerHTML = originalState.innerHTML;

    optimisticUpdates.delete(updateId);
}

/**
 * Clear optimistic update without reverting (called on successful server response)
 * @param {string} updateId - Update ID to clear
 */
function clearOptimisticUpdate(updateId) {
    optimisticUpdates.delete(updateId);
}

/**
 * Enhanced debounce function that works with any event type
 * @param {HTMLElement} element - Target element
 * @param {string} eventType - Event type (click, input, change, etc.)
 * @param {Function} handler - Event handler function
 * @param {number} delay - Delay in milliseconds
 * @returns {Function} Debounced event handler
 */
function createDebouncedHandler(element, eventType, handler, delay) {
    const debounceKey = `${eventType}-${element.id || Math.random()}`;
    
    return function debouncedHandler(...args) {
        // Clear existing timeout for this element/event combination
        if (window.djustDebounceTimeouts) {
            clearTimeout(window.djustDebounceTimeouts[debounceKey]);
        } else {
            window.djustDebounceTimeouts = {};
        }

        // Set new timeout
        window.djustDebounceTimeouts[debounceKey] = setTimeout(() => {
            handler.apply(this, args);
            delete window.djustDebounceTimeouts[debounceKey];
        }, delay);
    };
}

/**
 * Enhanced throttle function that works with any event type
 * @param {HTMLElement} element - Target element
 * @param {string} eventType - Event type (click, input, change, etc.)
 * @param {Function} handler - Event handler function
 * @param {number} delay - Delay in milliseconds
 * @returns {Function} Throttled event handler
 */
function createThrottledHandler(element, eventType, handler, delay) {
    const throttleKey = `${eventType}-${element.id || Math.random()}`;
    
    return function throttledHandler(...args) {
        if (!window.djustThrottleTimestamps) {
            window.djustThrottleTimestamps = {};
        }

        const now = Date.now();
        const lastCall = window.djustThrottleTimestamps[throttleKey] || 0;

        if (now - lastCall >= delay) {
            window.djustThrottleTimestamps[throttleKey] = now;
            handler.apply(this, args);
        }
    };
}

/**
 * Get debounce/throttle configuration for an element
 * @param {HTMLElement} element - Target element
 * @returns {Object|null} Configuration object with type and delay, or null
 */
function getRateLimitConfig(element) {
    // Check for explicit dj-debounce
    if (element.hasAttribute('dj-debounce')) {
        const delay = parseInt(element.getAttribute('dj-debounce'), 10);
        return delay > 0 ? { type: 'debounce', delay } : null;
    }

    // Check for explicit dj-throttle
    if (element.hasAttribute('dj-throttle')) {
        const delay = parseInt(element.getAttribute('dj-throttle'), 10);
        return delay > 0 ? { type: 'throttle', delay } : null;
    }

    return null;
}

/**
 * Wrap event handler with rate limiting if configured
 * @param {HTMLElement} element - Target element
 * @param {string} eventType - Event type
 * @param {Function} handler - Original handler
 * @returns {Function} Wrapped handler (or original if no rate limiting)
 */
function wrapWithRateLimit(element, eventType, handler) {
    const config = getRateLimitConfig(element);
    if (!config) {
        return handler;
    }

    if (config.type === 'debounce') {
        return createDebouncedHandler(element, eventType, handler, config.delay);
    } else if (config.type === 'throttle') {
        return createThrottledHandler(element, eventType, handler, config.delay);
    }

    return handler;
}

// Export to global namespace
window.djust = window.djust || {};
window.djust.optimistic = {
    applyOptimisticUpdate,
    revertOptimisticUpdate,
    clearOptimisticUpdate,
    optimisticUpdates
};

window.djust.rateLimit = {
    createDebouncedHandler,
    createThrottledHandler,
    getRateLimitConfig,
    wrapWithRateLimit
};
// ============================================================================
// Streaming — Real-time partial DOM updates (LLM chat, live feeds, etc.)
// ============================================================================

/**
 * Handle a "stream" WebSocket message by applying DOM operations directly.
 *
 * Message format:
 *   { type: "stream", stream: "messages", ops: [
 *       { op: "replace", target: "#message-list", html: "..." },
 *       { op: "append",  target: "#message-list", html: "<div>...</div>" },
 *       { op: "prepend", target: "#message-list", html: "<div>...</div>" },
 *       { op: "delete",  target: "#msg-42" },
 *   ]}
 */
function handleStreamMessage(data) {
    const ops = data.ops;
    if (!ops || !Array.isArray(ops)) return;

    for (const op of ops) {
        const target = op.target;
        if (!target) continue;

        const el = document.querySelector(target);
        if (!el) {
            if (globalThis.djustDebug) {
                console.warn('[LiveView:stream] Target not found:', target);
            }
            continue;
        }

        switch (op.op) {
            case 'replace':
                el.innerHTML = op.html || '';
                break;

            case 'append': {
                const frag = _htmlToFragment(op.html);
                el.appendChild(frag);
                // Auto-scroll for chat-like containers
                _autoScroll(el);
                break;
            }

            case 'prepend': {
                const frag = _htmlToFragment(op.html);
                el.insertBefore(frag, el.firstChild);
                break;
            }

            case 'delete':
                el.remove();
                break;

            default:
                console.warn('[LiveView:stream] Unknown op:', op.op);
        }
    }

    // Re-bind events on new DOM content
    if (typeof bindLiveViewEvents === 'function') {
        bindLiveViewEvents();
    }
}

/**
 * Parse an HTML string into a DocumentFragment.
 */
function _htmlToFragment(html) {
    const template = document.createElement('template');
    template.innerHTML = html || '';
    return template.content;
}

/**
 * Auto-scroll a container to the bottom if the user is near the bottom.
 * "Near" = within 100px of the bottom before the update.
 */
function _autoScroll(el) {
    const threshold = 100;
    const isNearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < threshold;
    if (isNearBottom) {
        requestAnimationFrame(() => {
            el.scrollTop = el.scrollHeight;
        });
    }
}

// Expose for WebSocket handler
window.djust = window.djust || {};
window.djust.handleStreamMessage = handleStreamMessage;
