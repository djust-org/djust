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
    if (globalThis.djustDebug) console.log('[LiveView] client.js already loaded, skipping duplicate initialization');
} else {
window._djustClientLoaded = true;

// ============================================================================
// Security Constants
// ============================================================================
// Dangerous keys that could cause prototype pollution attacks
const UNSAFE_KEYS = ['__proto__', 'constructor', 'prototype'];

// ============================================================================
// dj-cloak CSS injection — hide [dj-cloak] elements until mount completes
// ============================================================================
(function() {
    const style = document.createElement('style');
    style.textContent = '[dj-cloak] { display: none !important; }';
    document.head.appendChild(style);
})();

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

/**
 * Find the closest parent embedded view ID by walking up the DOM tree.
 * Used by event handlers to route events to embedded child views.
 * @param {HTMLElement} element - Starting element
 * @returns {string|null} - Embedded view ID or null if not found
 */
function getEmbeddedViewId(element) {
    let currentElement = element;
    while (currentElement && currentElement !== document.body) {
        if (currentElement.dataset.djustEmbedded) {
            return currentElement.dataset.djustEmbedded;
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

window.addEventListener('turbo:load', function(_event) {
    if (globalThis.djustDebug) console.log('[LiveView:TurboNav] turbo:load event received!');
    if (globalThis.djustDebug) console.log('[LiveView:TurboNav] djustInitialized:', window.djustInitialized);

    if (!window.djustInitialized) {
        // client.js hasn't finished initializing yet, defer reinit
        if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Deferring reinit until DOMContentLoaded completes');
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
    if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Reinitializing LiveView...');

    // Disconnect existing WebSocket
    if (liveViewWS) {
        if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Disconnecting existing WebSocket');
        liveViewWS.disconnect();
        liveViewWS = null;
    }

    // Reset client VDOM version
    clientVdomVersion = null;

    // Clear lazy hydration state
    lazyHydrationManager.init();

    // Auto-stamp root attributes on new content before querying containers
    autoStampRootAttributes();

    // Find all LiveView containers in the new content
    const allContainers = document.querySelectorAll('[dj-view]');
    const lazyContainers = document.querySelectorAll('[dj-view][dj-lazy]');
    const eagerContainers = document.querySelectorAll('[dj-view]:not([dj-lazy])');

    if (globalThis.djustDebug) console.log(`[LiveView:TurboNav] Found ${allContainers.length} containers (${lazyContainers.length} lazy, ${eagerContainers.length} eager)`);

    // Register lazy containers with the lazy hydration manager
    lazyContainers.forEach(container => {
        lazyHydrationManager.register(container);
    });

    // Only initialize WebSocket if there are eager containers
    if (eagerContainers.length > 0) {
        if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Initializing new WebSocket connection');
        // Initialize WebSocket
        liveViewWS = new LiveViewWebSocket();
        window.djust.liveViewInstance = liveViewWS;
        liveViewWS.connect();

        // Start heartbeat
        liveViewWS.startHeartbeat();
    } else if (lazyContainers.length > 0) {
        if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Deferring WebSocket connection until lazy elements are needed');
    } else {
        if (globalThis.djustDebug) console.log('[LiveView:TurboNav] No LiveView containers found, skipping WebSocket');
    }

    // Clean up existing poll intervals before re-binding
    document.querySelectorAll('[dj-poll]').forEach(el => {
        if (el._djustPollIntervalId) clearInterval(el._djustPollIntervalId);
        if (el._djustPollVisibilityHandler) document.removeEventListener('visibilitychange', el._djustPollVisibilityHandler);
    });

    // Clean up scoped (window/document) listeners before re-binding
    for (const el of _scopedListenerElements) {
        _cleanupScopedListeners(el);
    }

    // Re-bind events and hooks
    reinitAfterDOMUpdate();

    // Re-scan dj-loading attributes
    globalLoadingManager.scanAndRegister();

    if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Reinitialization complete');
}

// ============================================================================
// Centralized Response Handler (WebSocket + HTTP)
// ============================================================================

/**
 * Check whether the global WebSocket connection is open and ready.
 * @returns {boolean}
 */
function isWSConnected() {
    return liveViewWS && liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN;
}

/**
 * Remove the 'optimistic-pending' CSS class from all elements.
 * Called after server confirms state to clear optimistic UI indicators.
 */
function clearOptimisticPending() {
    document.querySelectorAll('.optimistic-pending').forEach(el => {
        el.classList.remove('optimistic-pending');
    });
}

/**
 * Centralized server response handler for both WebSocket and HTTP fallback.
 * Eliminates code duplication and ensures consistent behavior.
 *
 * @param {Object} data - Server response data
 * @param {string} eventName - Name of the event that triggered this response
 * @param {HTMLElement} triggerElement - Element that triggered the event
 * @returns {boolean} - True if handled successfully, false otherwise
 */
function handleServerResponse(data, eventName, triggerElement) {
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
                if (globalThis.djustDebug) console.log('[LiveView] Initialized VDOM version:', clientVdomVersion);
            } else if (clientVdomVersion !== data.version - 1 && !data.hotreload) {
                // Version mismatch - force full reload (skip check for hot reload)
                if (globalThis.djustDebug) {
                    console.warn('[LiveView] VDOM version mismatch!');
                    console.warn(`  Expected v${clientVdomVersion + 1}, got v${data.version}`);
                }

                clearOptimisticState(eventName);

                // Request full HTML for recovery morph
                if (isWSConnected()) {
                    liveViewWS.sendMessage({ type: 'request_html' });
                } else {
                    window.location.reload();
                }

                globalLoadingManager.stopLoading(eventName, triggerElement);
                return true;
            }
            clientVdomVersion = data.version;
        }

        // Clear optimistic state BEFORE applying changes
        clearOptimisticState(eventName);

        // Global cleanup of lingering optimistic-pending classes
        clearOptimisticPending();

        // Apply patches (efficient incremental updates)
        // Empty patches array = server confirmed no DOM changes needed (no-op success)
        if (data.patches && Array.isArray(data.patches) && data.patches.length === 0) {
            if (globalThis.djustDebug) console.log('[LiveView] No DOM changes needed (0 patches)');
        }
        else if (data.patches && Array.isArray(data.patches) && data.patches.length > 0) {
            if (globalThis.djustDebug) console.log('[LiveView] Applying', data.patches.length, 'patches');

            // Store timing info globally for debug panel access
            window._lastPatchTiming = data.timing;
            // Store comprehensive performance data if available
            window._lastPerformanceData = data.performance;

            // For broadcast patches (from other users via push_to_view),
            // tell preserveFormValues to accept remote content instead of
            // restoring the focused element's stale local value.
            _isBroadcastUpdate = !!data.broadcast;
            const success = applyPatches(data.patches);
            _isBroadcastUpdate = false;

            // For broadcast patches, sync textarea .value from .textContent.
            // VDOM patches update textContent directly (not via innerHTML),
            // so preserveFormValues never runs. Textarea .value is separate
            // from .textContent after initial render — must sync explicitly.
            if (data.broadcast) {
                const root = getLiveViewRoot();
                if (root) {
                    root.querySelectorAll('textarea').forEach(el => {
                        el.value = el.textContent || '';
                    });
                }
            }

            if (success === false) {
                // Patches failed — likely due to {% if %} blocks shifting DOM structure.
                // Request full HTML from server for DOM morphing (on-demand, not sent
                // with every response to avoid bandwidth regression).
                console.warn(
                    '[LiveView] VDOM patches failed. This usually happens when {% if %} blocks ' +
                    'add/remove DOM elements, shifting sibling positions.\n' +
                    'Fix: Use style="display:none" toggling instead of {% if %} for elements:\n' +
                    '  <div style="{% if not show %}display:none{% endif %}">...</div>\n' +
                    'Requesting recovery HTML from server.'
                );

                // Revert VDOM version — recovery response will set the correct version
                clientVdomVersion = data.version - 1;

                if (isWSConnected()) {
                    liveViewWS.sendMessage({ type: 'request_html' });
                } else {
                    // No WebSocket available — last resort page reload
                    window.location.reload();
                }

                globalLoadingManager.stopLoading(eventName, triggerElement);
                return true;
            }

            if (globalThis.djustDebug) console.log('[LiveView] Patches applied successfully');

            // Final cleanup
            clearOptimisticPending();

            // Ensure dj-mounted is active for elements added by VDOM patches
            if (!window.djust._mountReady) window.djust._mountReady = true;

            reinitAfterDOMUpdate();
        }
        // Apply full HTML update (fallback)
        else if (data.html) {
            if (globalThis.djustDebug) console.log('[LiveView] Applying full HTML update');
            _isBroadcastUpdate = !!data.broadcast;
            const parser = new DOMParser();
            // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine; DOMParser creates an inert document
            const doc = parser.parseFromString(data.html, 'text/html');
            const liveviewRoot = getLiveViewRoot();
            if (!liveviewRoot) {
                globalLoadingManager.stopLoading(eventName, triggerElement);
                window.location.reload();
                return false;
            }
            const newRoot = doc.querySelector('[dj-root]') || doc.body;

            // Handle dj-update="append|prepend|ignore" for efficient list updates.
            // When no dj-update elements exist, applyDjUpdateElements falls back
            // to morphChildren internally, which preserves JS state (canvas contexts,
            // event listeners, hook properties) better than innerHTML replacement.
            applyDjUpdateElements(liveviewRoot, newRoot);

            clearOptimisticPending();

            _isBroadcastUpdate = false;
            // Ensure dj-mounted is active for elements added by HTML update
            if (!window.djust._mountReady) window.djust._mountReady = true;
            reinitAfterDOMUpdate();
        } else {
            if (globalThis.djustDebug) console.warn('[LiveView] Response has neither patches nor html!', data);
        }

        // Handle form reset
        if (data.reset_form) {
            if (globalThis.djustDebug) console.log('[LiveView] Resetting form');
            const form = document.querySelector('[dj-root] form');
            if (form) form.reset();
        }

        // Process side-channel commands from HTTP response (flash, page metadata)
        if (data._flash && window.djust.flash) {
            data._flash.forEach(function(cmd) {
                window.djust.flash.handleFlash(cmd);
            });
        }
        if (data._page_metadata && window.djust.pageMetadata) {
            data._page_metadata.forEach(function(cmd) {
                window.djust.pageMetadata.handlePageMetadata(cmd);
            });
        }

        // Forward debug info to debug panel (HTTP-only mode)
        if (data._debug && window.djustDebugPanel && typeof window.djustDebugPanel.processDebugInfo === 'function') {
            window.djustDebugPanel.processDebugInfo(data._debug);
        }

        // Stop loading state (unless server has async work pending)
        // For async completion responses, data.event_name identifies which
        // loading state to clear (since lastEventName was already consumed).
        const loadingEventName = eventName || data.event_name;
        if (!data.async_pending) {
            if (loadingEventName) {
                globalLoadingManager.stopLoading(loadingEventName, triggerElement);
            }

            // dj-lock: unlock all locked elements after server response
            document.querySelectorAll('[data-djust-locked]').forEach(el => {
                el.removeAttribute('data-djust-locked');
                if (el.tagName === 'BUTTON' || el.tagName === 'INPUT' ||
                    el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
                    el.disabled = false;
                } else {
                    el.classList.remove('djust-locked');
                }
            });

            // dj-disable-with: restore original text on all disabled-with elements
            document.querySelectorAll('[data-djust-original-text]').forEach(el => {
                el.textContent = el.getAttribute('data-djust-original-text');
                el.removeAttribute('data-djust-original-text');
                el.disabled = false;
            });
        } else if (globalThis.djustDebug) {
            console.log('[LiveView] Keeping loading state — async work pending');
        }
        return true;

    } catch (error) {
        if (globalThis.djustDebug) console.error('[LiveView] Error in handleServerResponse:', error);
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
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;
        this.minReconnectDelay = 500;
        this.maxReconnectDelayMs = 30000;
        this.viewMounted = false;
        this.enabled = true;  // Can be disabled to use HTTP fallback
        this._intentionalDisconnect = false;  // Set by disconnect() to suppress error overlay
        this.lastEventName = null;  // Phase 5: Track last event for loading state
        this.lastTriggerElement = null;  // Phase 5: Track trigger element for scoped loading
        // Optional callback invoked when all reconnect attempts are exhausted.
        // If set, it is called instead of _showConnectionErrorOverlay(), allowing
        // 14-init.js to switch to the SSE fallback transport.
        this.onTransportFailed = null;

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
        if (globalThis.djustDebug) console.log('[LiveView] Disconnecting for navigation...');

        // Stop heartbeat
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
            if (globalThis.djustDebug) console.log('[LiveView] Heartbeat stopped');
        }

        // Mark as intentional so onclose doesn't show error overlay
        this._intentionalDisconnect = true;
        // Clear reconnect attempts so we don't auto-reconnect
        this.reconnectAttempts = this.maxReconnectAttempts;

        // Close WebSocket if open or connecting
        if (this.ws) {
            if (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN) {
                this.ws.close();
                if (globalThis.djustDebug) console.log('[LiveView] WebSocket closed');
            }
        }

        this.ws = null;
        this.sessionId = null;
        this.viewMounted = false;
        this.vdomVersion = null;
        this.stats.connectedAt = null; // Reset connection timestamp

        // Remove connection state CSS classes on intentional disconnect
        document.body.classList.remove('dj-connected');
        document.body.classList.remove('dj-disconnected');

        // Clear reconnection UI state
        document.body.removeAttribute('data-dj-reconnect-attempt');
        document.body.style.removeProperty('--dj-reconnect-attempt');
        this._removeReconnectBanner();

        // Event sequencing (#560): clear pending event state
        _pendingEventRefs.clear();
        _pendingEventNames.clear();
        _pendingTriggerEls.clear();
        _tickBuffer.length = 0;
    }

    connect(url = null) {
        if (!this.enabled) return;

        // Guard: prevent duplicate connections
        if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
            if (globalThis.djustDebug) console.log('[LiveView] WebSocket already connected or connecting, skipping');
            return;
        }

        if (!url) {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;
            url = `${protocol}//${host}/ws/live/`;
        }

        if (globalThis.djustDebug) console.log('[LiveView] Connecting to WebSocket:', url);
        this.ws = new WebSocket(url);

        this.ws.onopen = (_event) => {
            if (globalThis.djustDebug) console.log('[LiveView] WebSocket connected');
            this.reconnectAttempts = 0;
            this._intentionalDisconnect = false;

            // Connection state CSS classes
            document.body.classList.add('dj-connected');
            document.body.classList.remove('dj-disconnected');

            // Clear reconnection UI state
            document.body.removeAttribute('data-dj-reconnect-attempt');
            document.body.style.removeProperty('--dj-reconnect-attempt');
            this._removeReconnectBanner();

            // Track reconnections (Phase 2.1: WebSocket Inspector)
            if (this.stats.connectedAt !== null) {
                this.stats.reconnections++;
                // Set reconnect flag for dj-auto-recover
                if (window.djust) window.djust._isReconnect = true;
                // Notify hooks of reconnection
                notifyHooksReconnected();
            }
            this.stats.connectedAt = Date.now();
        };

        this.ws.onclose = (_event) => {
            if (globalThis.djustDebug) console.log('[LiveView] WebSocket disconnected');
            this.viewMounted = false;

            // Connection state CSS classes
            document.body.classList.add('dj-disconnected');
            document.body.classList.remove('dj-connected');

            // Notify hooks of disconnection
            notifyHooksDisconnected();

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

            // Event sequencing (#560): clear pending event state
            _pendingEventRefs.clear();
            _pendingEventNames.clear();
            _pendingTriggerEls.clear();
            _tickBuffer.length = 0;

            // Remove loading indicators from DOM
            clearOptimisticPending();

            // Skip reconnection logic if this was an intentional disconnect (TurboNav)
            if (this._intentionalDisconnect) {
                this._intentionalDisconnect = false;
                return;
            }

            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const baseDelay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                const cappedBase = Math.min(baseDelay, this.maxReconnectDelayMs);
                const jitteredDelay = Math.max(this.minReconnectDelay, Math.random() * cappedBase);
                if (globalThis.djustDebug) console.log('[LiveView] Reconnecting in ' + Math.round(jitteredDelay) + 'ms (attempt ' + this.reconnectAttempts + '/' + this.maxReconnectAttempts + ')...');

                // Update reconnection UI state
                document.body.setAttribute('data-dj-reconnect-attempt', String(this.reconnectAttempts));
                document.body.style.setProperty('--dj-reconnect-attempt', String(this.reconnectAttempts));
                this._showReconnectBanner(this.reconnectAttempts, this.maxReconnectAttempts);

                setTimeout(() => this.connect(url), jitteredDelay);
            } else {
                console.warn('[LiveView] Max reconnection attempts reached.');
                this.enabled = false;
                // If an SSE fallback is configured, hand off to it instead of
                // showing the connection error overlay.
                if (typeof this.onTransportFailed === 'function') {
                    if (globalThis.djustDebug) console.log('[LiveView] Invoking onTransportFailed for SSE fallback');
                    this.onTransportFailed();
                } else {
                    this._showConnectionErrorOverlay();
                }
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

                // Latency simulation on receive (DEBUG_MODE only)
                const simLatency = window.DEBUG_MODE && window.djust && window.djust._simulatedLatency;
                if (simLatency > 0) {
                    const jitter = (window.djust._simulatedJitter || 0);
                    const actual = Math.max(0, simLatency + (Math.random() * 2 - 1) * simLatency * jitter);
                    setTimeout(() => this.handleMessage(data), actual);
                } else {
                    this.handleMessage(data);
                }
            } catch (error) {
                console.error('[LiveView] Failed to parse message:', error);
            }
        };
    }

    handleMessage(data) {
        if (globalThis.djustDebug) console.log('[LiveView] Received: %s %o', String(data.type), data);

        switch (data.type) {
            case 'connect':
                this.sessionId = data.session_id;
                if (globalThis.djustDebug) console.log('[LiveView] Session ID:', this.sessionId);
                this.autoMount();
                break;

            case 'mount':
                this.viewMounted = true;
                if (globalThis.djustDebug) console.log('[LiveView] View mounted: %s', String(data.view));

                // Remove dj-cloak from all elements (FOUC prevention)
                document.querySelectorAll('[dj-cloak]').forEach(el => el.removeAttribute('dj-cloak'));

                // Finish page loading bar on mount
                if (window.djust.pageLoading) window.djust.pageLoading.finish();

                // Initialize VDOM version from mount response (critical for patch generation)
                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                    if (globalThis.djustDebug) console.log('[LiveView] VDOM version initialized:', clientVdomVersion);
                }

                // Initialize cache configuration from mount response
                if (data.cache_config) {
                    setCacheConfig(data.cache_config);
                }

                // Initialize optimistic UI rules from descriptor components (DEP-002)
                if (data.optimistic_rules) {
                    window.djust._optimisticRules = data.optimistic_rules;
                    if (globalThis.djustDebug) console.log('[LiveView] Optimistic rules loaded:', Object.keys(data.optimistic_rules));
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
                    // If server HTML has dj-id attributes, stamp them onto existing DOM
                    // This preserves whitespace (e.g. in code blocks) that innerHTML would destroy
                    if (hasDataDjAttrs && data.html) {
                        if (globalThis.djustDebug) console.log('[LiveView] Stamping dj-id attributes onto pre-rendered DOM');
                        _stampDjIds(data.html); // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                    } else {
                        if (globalThis.djustDebug) console.log('[LiveView] Skipping mount HTML - using pre-rendered content');
                    }
                    this.skipMountHtml = false;
                    // FIX #619: Defer reinitAfterDOMUpdate() to the next animation
                    // frame on pre-rendered mounts. Calling it synchronously here
                    // forces the browser to recalc layout mid-paint, which causes
                    // a visible flash on pages where the pre-rendered content has
                    // large elements (e.g. big dashboard stats) — the elements
                    // briefly render at the wrong size before settling. Deferring
                    // to the next frame lets the browser finish its current paint
                    // first. Form recovery + _mountReady must run AFTER reinit so
                    // handlers are bound when recovery fires, so the whole block
                    // moves into the rAF callback. Falls back to a synchronous call
                    // when requestAnimationFrame isn't available (JSDOM tests).
                    const runPostMount = () => {
                        reinitAfterDOMUpdate();
                        // Set mount ready flag so dj-mounted handlers only fire
                        // for elements added by subsequent VDOM patches, not on initial load
                        window.djust._mountReady = true;
                        // Trigger form recovery and dj-auto-recover after reconnect mount
                        if (window.djust._isReconnect) {
                            if (typeof window.djust._processFormRecovery === 'function') {
                                window.djust._processFormRecovery();
                            }
                            if (typeof window.djust._processAutoRecover === 'function') {
                                window.djust._processAutoRecover();
                            }
                        }
                    };
                    if (typeof requestAnimationFrame === 'function') {
                        requestAnimationFrame(runPostMount);
                    } else {
                        runPostMount();
                    }
                } else if (data.html) {
                    // No pre-rendered content - use server HTML directly
                    if (hasDataDjAttrs) {
                        if (globalThis.djustDebug) console.log('[LiveView] Hydrating DOM with dj-id attributes for reliable patching');
                    }
                    let container = document.querySelector('[dj-view]');
                    if (!container) {
                        container = document.querySelector('[dj-root]');
                    }
                    if (container) {
                        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                        container.innerHTML = data.html;
                        reinitAfterDOMUpdate();
                    }
                    this.skipMountHtml = false;
                    // Set mount ready flag so dj-mounted handlers only fire
                    // for elements added by subsequent VDOM patches, not on initial load
                    window.djust._mountReady = true;
                    // Trigger form recovery and dj-auto-recover after reconnect mount
                    if (window.djust._isReconnect) {
                        if (typeof window.djust._processFormRecovery === 'function') {
                            window.djust._processFormRecovery();
                        }
                        if (typeof window.djust._processAutoRecover === 'function') {
                            window.djust._processAutoRecover();
                        }
                    }
                }
                break;

            case 'patch':
            case 'html_update': {
                // Event sequencing (#560): if this is a server-initiated update
                // (tick, broadcast, async) and we have pending user events,
                // buffer it and apply after the event responses arrive. This
                // prevents server pushes from consuming event loading state
                // and ensures version numbers stay sequential.
                const isServerInitiated = (
                    data.source === 'tick' ||
                    data.source === 'broadcast' ||
                    data.source === 'async'
                );
                const isEventResponse = (
                    data.ref != null && _pendingEventRefs.has(data.ref)
                );

                if (!isEventResponse && isServerInitiated && _pendingEventRefs.size > 0) {
                    // Buffer server-initiated patch — will be applied after
                    // all pending event responses arrive.
                    _tickBuffer.push(data);
                    if (globalThis.djustDebug) {
                        console.log('[LiveView] Buffered %s patch (v%s) — waiting for %d pending event(s)', String(data.source), String(data.version), _pendingEventRefs.size);
                    }
                    break;
                }

                // Determine event name and trigger for loading state
                let evName = this.lastEventName;
                let evTrigger = this.lastTriggerElement;

                if (isEventResponse) {
                    // This response matches a pending event — use tracked
                    // event name/trigger and remove from pending set.
                    evName = _pendingEventNames.get(data.ref) || this.lastEventName;
                    evTrigger = _pendingTriggerEls.get(data.ref) || this.lastTriggerElement;
                    _pendingEventRefs.delete(data.ref);
                    _pendingEventNames.delete(data.ref);
                    _pendingTriggerEls.delete(data.ref);
                } else if (isServerInitiated) {
                    // Server-initiated patch with no pending events — apply
                    // without consuming event loading state.
                    evName = null;
                    evTrigger = null;
                }

                handleServerResponse(data, evName, evTrigger);

                if (!isServerInitiated) {
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }

                // After processing the event response, flush buffered
                // patches only when ALL pending events have resolved.
                if (isEventResponse && _pendingEventRefs.size === 0 && _tickBuffer.length > 0) {
                    if (globalThis.djustDebug) {
                        console.log('[LiveView] Flushing ' + _tickBuffer.length + ' buffered patches');
                    }
                    const buffered = _tickBuffer.splice(0);
                    for (const tickData of buffered) {
                        handleServerResponse(tickData, null, null);
                    }
                }
                break;
            }

            case 'html_recovery': {
                // Server response to request_html — morph DOM with recovered HTML.
                // Bypasses normal version tracking to avoid mismatch loops.
                const parser = new DOMParser();
                // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                const doc = parser.parseFromString(data.html, 'text/html');
                const liveviewRoot = getLiveViewRoot();
                if (!liveviewRoot) {
                    window.location.reload();
                    break;
                }
                const newRoot = doc.querySelector('[dj-root]') || doc.body;
                morphChildren(liveviewRoot, newRoot);
                clientVdomVersion = data.version;
                reinitAfterDOMUpdate();
                if (globalThis.djustDebug) {
                    // codeql[js/log-injection] -- data.version is a server-controlled integer
                    console.log('[LiveView] DOM recovered via morph, version: %s', String(data.version));
                }
                break;
            }

            case 'error':
                // codeql[js/log-injection] -- data.error and data.traceback are server-provided error messages, not user input
                console.error('[LiveView] Server error:', data.error);
                if (data.traceback) {
                    // codeql[js/log-injection] -- data.traceback is a server-provided stack trace, not user input
                    console.error('Traceback:', data.traceback);
                }

                // Non-recoverable errors (e.g. server restart lost state) — auto-reload
                if (data.recoverable === false) {
                    console.warn('[LiveView] Non-recoverable error, reloading page...');
                    window.location.reload();
                    break;
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

                // Clear pending event refs (#560)
                _pendingEventRefs.clear();
                _pendingEventNames.clear();
                _pendingTriggerEls.clear();
                _tickBuffer.length = 0;

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
                // codeql[js/log-injection] -- data.ref and data.upload_name are server-assigned upload identifiers
                if (globalThis.djustDebug) console.log('[Upload] Registered: %s for %s', String(data.ref), String(data.upload_name));
                break;

            case 'stream':
                // Streaming partial DOM updates (LLM chat, live feeds)
                if (window.djust.handleStreamMessage) {
                    window.djust.handleStreamMessage(data);
                }
                break;

            case 'noop': {
                // Server acknowledged event but no DOM changes needed (auto-detected
                // or explicit _skip_render). Clear loading state unless async pending.
                const noopEvName = (data.ref != null ? _pendingEventNames.get(data.ref) : null)
                    || this.lastEventName;
                const noopTrigger = (data.ref != null ? _pendingTriggerEls.get(data.ref) : null)
                    || this.lastTriggerElement;

                // Clear pending event ref (#560)
                if (data.ref != null && _pendingEventRefs.has(data.ref)) {
                    _pendingEventRefs.delete(data.ref);
                    _pendingEventNames.delete(data.ref);
                    _pendingTriggerEls.delete(data.ref);
                }

                if (noopEvName) {
                    if (!data.async_pending) {
                        globalLoadingManager.stopLoading(noopEvName, noopTrigger);
                    } else {
                        if (globalThis.djustDebug) console.log('[LiveView] Keeping loading state — async work pending');
                    }
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }

                // Flush buffered patches only when all pending events resolved
                if (_pendingEventRefs.size === 0 && _tickBuffer.length > 0) {
                    const buffered = _tickBuffer.splice(0);
                    for (const tickData of buffered) {
                        handleServerResponse(tickData, null, null);
                    }
                }
                break;
            }

            case 'push_event':
                // Server-pushed event for JS hooks
                window.dispatchEvent(new CustomEvent('djust:push_event', {
                    detail: { event: data.event, payload: data.payload }
                }));
                // Dispatch to dj-hook instances that registered via handleEvent
                dispatchPushEventToHooks(data.event, data.payload);
                // Clear loading state — when _skip_render is used, this is the
                // only response the client gets (no patch/html_update follows).
                globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                break;

            case 'embedded_update':
                // Scoped HTML update for an embedded child LiveView
                this.handleEmbeddedUpdate(data);
                // Stop loading state
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'rate_limit_exceeded':
                // Server is dropping events due to rate limiting — show brief warning, do NOT retry
                // codeql[js/log-injection] -- data.message is a server-controlled rate limit message
                console.warn('[LiveView] Rate limited:', data.message || 'Too many events');
                this._showRateLimitWarning();
                // Stop loading state if applicable
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'navigation':
                // Server-side live_patch or live_redirect
                if (window.djust.navigation) {
                    window.djust.navigation.handleNavigation(data);
                }
                break;

            case 'accessibility':
                // Screen reader announcements from server
                if (window.djust.accessibility) {
                    window.djust.accessibility.processAnnouncements(data.announcements);
                }
                break;

            case 'focus':
                // Focus command from server
                if (window.djust.accessibility) {
                    window.djust.accessibility.processFocus([data.selector, data.options]);
                }
                break;

            case 'flash':
                // Flash message from server (put_flash / clear_flash)
                if (window.djust.flash) {
                    window.djust.flash.handleFlash(data);
                }
                break;

            case 'page_metadata':
                // Page metadata from server (page_title / page_meta)
                if (window.djust.pageMetadata) {
                    window.djust.pageMetadata.handlePageMetadata(data);
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

        if (globalThis.djustDebug) console.log('[LiveView] Mounting view:', viewPath);
        // Detect browser timezone for server-side local time rendering
        let clientTimezone = null;
        try {
            clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (e) {
            console.warn('[LiveView] Could not detect browser timezone:', e);
        }

        this.sendMessage({
            type: 'mount',
            view: viewPath,
            params: params,
            url: window.location.pathname,
            has_prerendered: this.skipMountHtml || false,  // Tell server we have pre-rendered content
            client_timezone: clientTimezone  // IANA timezone string (e.g. "America/New_York")
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

        // Latency simulation (DEBUG_MODE only)
        const simLatency = window.DEBUG_MODE && window.djust && window.djust._simulatedLatency;
        if (simLatency > 0) {
            const jitter = (window.djust._simulatedJitter || 0);
            const actual = Math.max(0, simLatency + (Math.random() * 2 - 1) * simLatency * jitter);
            setTimeout(() => this.ws.send(message), actual);
        } else {
            // Send the message
            this.ws.send(message);
        }
    }

    autoMount() {
        // Look for container with view path
        let container = document.querySelector('[dj-view]');
        if (!container) {
            // Fallback: look for dj-root with dj-view attribute
            container = document.querySelector('[dj-root][dj-view]');
        }

        if (container) {
            const viewPath = container.getAttribute('dj-view');
            if (viewPath) {
                // OPTIMIZATION: Check if content was already rendered by HTTP GET
                // We still send mount message (server needs to initialize session),
                // but we'll skip applying the HTML response
                const hasContent = container.innerHTML && container.innerHTML.trim().length > 0;

                if (hasContent) {
                    if (globalThis.djustDebug) console.log('[LiveView] Content pre-rendered via HTTP - will skip HTML in mount response');
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

        // Event sequencing (#560): assign monotonic ref so we can match
        // the server's response to this specific event and distinguish
        // it from server-initiated patches. Uses Set to track multiple
        // concurrent pending events.
        const ref = ++_eventRefCounter;
        _pendingEventRefs.add(ref);
        _pendingEventNames.set(ref, eventName);
        _pendingTriggerEls.set(ref, triggerElement);

        this.sendMessage({
            type: 'event',
            event: eventName,
            params: params,
            ref: ref
        });
        return true;
    }

    // Removed duplicate applyPatches and patch helper methods
    // Now using centralized handleServerResponse() -> applyPatches()

    /**
     * Handle scoped HTML update for an embedded child LiveView.
     * Replaces only the innerHTML of the embedded view's container div.
     */
    handleEmbeddedUpdate(data) {
        const viewId = data.view_id;
        const html = data.html;
        if (!viewId || html === undefined) {
            // codeql[js/log-injection] -- data is a server WebSocket message, not user input
            console.warn('[LiveView] Invalid embedded_update message:', data);
            return;
        }

        const container = document.querySelector(`[data-djust-embedded="${CSS.escape(viewId)}"]`);
        if (!container) {
            console.warn('[LiveView] Embedded view container not found: %s', String(viewId));
            return;
        }

        const _morphTemp = document.createElement('div');
        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
        _morphTemp.innerHTML = html;
        morphChildren(container, _morphTemp);
        if (globalThis.djustDebug) console.log('[LiveView] Updated embedded view: %s', String(viewId));

        // Re-bind events within the updated container
        reinitAfterDOMUpdate();
    }

    _showReconnectBanner(attempt, maxAttempts) {
        let banner = document.getElementById('dj-reconnecting-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'dj-reconnecting-banner';
            banner.className = 'dj-reconnecting-banner';
            banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99998;background:#f59e0b;color:#1c1917;text-align:center;padding:6px 16px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;font-weight:600;';
            document.body.appendChild(banner);
        }
        banner.textContent = 'Reconnecting\u2026 (attempt ' + attempt + ' of ' + maxAttempts + ')';
    }

    _removeReconnectBanner() {
        const banner = document.getElementById('dj-reconnecting-banner');
        if (banner) banner.remove();
    }

    _showConnectionErrorOverlay() {
        // Only show in DEBUG mode
        if (!window.DEBUG_MODE) return;

        // Don't duplicate
        if (document.getElementById('djust-connection-error-overlay')) return;

        const overlay = document.createElement('div');
        overlay.id = 'djust-connection-error-overlay';
        overlay.style.cssText = `
            position: fixed; bottom: 0; left: 0; right: 0; z-index: 99999;
            background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%);
            border-top: 3px solid #ef4444; padding: 20px 24px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            color: #e2e8f0; font-size: 14px; box-shadow: 0 -4px 20px rgba(0,0,0,0.4);
        `;
        overlay.innerHTML = `
            <div style="display:flex; align-items:flex-start; gap:16px; max-width:900px; margin:0 auto;">
                <span style="font-size:24px; line-height:1;">❌</span>
                <div style="flex:1;">
                    <div style="font-weight:700; font-size:16px; margin-bottom:8px; color:#fca5a5;">
                        WebSocket Connection Failed
                    </div>
                    <div style="margin-bottom:10px; color:#cbd5e1; line-height:1.6;">
                        djust could not establish a WebSocket connection. Common causes:
                    </div>
                    <ul style="margin:0 0 12px 18px; padding:0; color:#94a3b8; line-height:1.8;">
                        <li>ASGI server not running (need <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">daphne</code> or <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">uvicorn</code>, not <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">manage.py runserver</code>)</li>
                        <li>If using daphne, wrap HTTP handler with <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">ASGIStaticFilesHandler</code> or use <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">djust.asgi.get_application()</code></li>
                        <li>WebSocket route not configured (need <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">/ws/live/</code> path)</li>
                    </ul>
                    <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                        <code style="background:#1e293b; padding:6px 12px; border-radius:4px; color:#86efac; font-size:13px;">
                            python manage.py djust_check
                        </code>
                        <span style="color:#64748b; font-size:12px;">← Run this to diagnose configuration issues</span>
                    </div>
                </div>
                <button onclick="this.parentElement.parentElement.remove()" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:20px;padding:4px;">✕</button>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    _showRateLimitWarning() {
        // Show a brief non-intrusive toast; debounce so rapid limits don't spam
        if (this._rateLimitToast) return;
        const toast = document.createElement('div');
        toast.textContent = 'Slow down — some actions were dropped';
        toast.style.cssText = `
            position: fixed; bottom: 20px; right: 20px; z-index: 99999;
            background: #f59e0b; color: #1c1917; padding: 10px 18px;
            border-radius: 8px; font-size: 13px; font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2); opacity: 0;
            transition: opacity 0.3s; pointer-events: none;
        `;
        document.body.appendChild(toast);
        this._rateLimitToast = toast;
        requestAnimationFrame(() => { toast.style.opacity = '1'; });
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => { toast.remove(); this._rateLimitToast = null; }, 300);
        }, 2500);
    }

    startHeartbeat(interval = 30000) {
        // Guard: prevent multiple heartbeat intervals
        if (this.heartbeatInterval) {
            if (globalThis.djustDebug) console.log('[LiveView] Heartbeat already running, skipping duplicate');
            return;
        }

        this.heartbeatInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.sendMessage({ type: 'ping' });
            }
        }, interval);
        if (globalThis.djustDebug) console.log('[LiveView] Heartbeat started (interval:', interval, 'ms)');
    }
}

// Expose LiveViewWebSocket to window for client-dev.js to wrap
window.djust.LiveViewWebSocket = LiveViewWebSocket;
// Backward compatibility
window.LiveViewWebSocket = LiveViewWebSocket;

// Global WebSocket instance
let liveViewWS = null;

// ============================================================================
// SSE (Server-Sent Events) Transport for djust LiveView
//
// Fallback transport used when WebSocket is unavailable (e.g. corporate proxies).
// Architecture:
//   Server → Client : EventSource (text/event-stream at /djust/sse/<session_id>/)
//   Client → Server : HTTP POST  to /djust/sse/<session_id>/event/
//
// Feature limitations vs WebSocket transport (documented in docs/sse-transport.md):
//   - No binary file uploads
//   - No presence tracking
//   - No actor-based state management
//   - No MessagePack binary encoding
// ============================================================================

class LiveViewSSE {
    constructor() {
        this.sessionId = null;
        this.eventSource = null;
        this.sseBaseUrl = null;
        this.enabled = true;
        this.viewMounted = false;
        this._hasConnectedBefore = false;
        this.lastEventName = null;
        this.lastTriggerElement = null;
        // Mirror LiveViewWebSocket interface for interoperability
        this.stats = { sent: 0, received: 0, sentBytes: 0, receivedBytes: 0 };
    }

    /**
     * Open an SSE stream and mount the view.
     *
     * @param {string} viewPath  Dotted Python path to the LiveView class
     * @param {Object} params    URL query params forwarded to mount()
     */
    connect(viewPath, params = {}) {
        if (!this.enabled) return;
        if (globalThis.djustDebug) console.log('[SSE] Connecting, view:', viewPath);

        // Session ID is generated client-side; the server stores it as the
        // lookup key so event POSTs can reach the right session.
        this.sessionId = this._generateSessionId();
        this.sseBaseUrl = `/djust/sse/${this.sessionId}/`;

        const urlParams = new URLSearchParams(params);
        urlParams.set('view', viewPath);
        const streamUrl = `${this.sseBaseUrl}?${urlParams.toString()}`;

        this.eventSource = new EventSource(streamUrl);

        this.eventSource.onopen = () => {
            // Connection state CSS classes
            document.body.classList.add('dj-connected');
            document.body.classList.remove('dj-disconnected');

            // Track reconnections for form recovery
            if (this._hasConnectedBefore) {
                if (window.djust) window.djust._isReconnect = true;
            }
            this._hasConnectedBefore = true;
        };

        this.eventSource.onmessage = (event) => {
            try {
                this.stats.received++;
                this.stats.receivedBytes += event.data.length;
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (err) {
                console.error('[SSE] Failed to parse message:', err);
            }
        };

        this.eventSource.onerror = (_err) => {
            // EventSource auto-reconnects; we only disable on persistent failure.
            // onerror fires on every connection hiccup, so guard against noise.
            if (this.eventSource && this.eventSource.readyState === EventSource.CLOSED) {
                console.warn('[SSE] EventSource closed unexpectedly.');
                this.enabled = false;
                // Connection state CSS classes
                document.body.classList.add('dj-disconnected');
                document.body.classList.remove('dj-connected');
            }
        };
    }

    /**
     * Cleanly close the SSE stream (e.g. during TurboNav page transitions).
     */
    disconnect() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        this.viewMounted = false;
        this.sessionId = null;

        // Remove connection state CSS classes on intentional disconnect
        document.body.classList.remove('dj-connected');
        document.body.classList.remove('dj-disconnected');

        if (globalThis.djustDebug) console.log('[SSE] Disconnected');
    }

    /**
     * Handle a server-pushed message.
     * The message format is identical to the WebSocket protocol so that
     * 02-response-handler.js and all other message-handling modules work
     * without modification.
     */
    handleMessage(data) {
        if (globalThis.djustDebug) console.log('[SSE] Received:', data.type, data);

        switch (data.type) {

            case 'sse_connect':
                // Session ID confirmation from server (informational only — we
                // already know our session ID since we generated it).
                if (globalThis.djustDebug) console.log('[SSE] Connection acknowledged, session:', data.session_id);
                break;

            case 'mount':
                this.viewMounted = true;
                if (globalThis.djustDebug) console.log('[SSE] View mounted:', data.view);

                // Remove dj-cloak from all elements (FOUC prevention)
                document.querySelectorAll('[dj-cloak]').forEach(el => el.removeAttribute('dj-cloak'));

                // Finish page loading bar on mount
                if (window.djust.pageLoading) window.djust.pageLoading.finish();

                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                }
                if (data.cache_config) {
                    setCacheConfig(data.cache_config);
                }

                if (data.html) {
                    let container = document.querySelector('[dj-view]');
                    if (!container) container = document.querySelector('[dj-root]');
                    if (container) {
                        const hasDataDjAttrs = data.has_ids === true;
                        if (hasDataDjAttrs) {
                            _stampDjIds(data.html);
                        } else {
                            // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                            container.innerHTML = data.html;
                        }
                        reinitAfterDOMUpdate();
                        // Set mount ready flag so dj-mounted handlers only fire
                        // for elements added by subsequent VDOM patches, not on initial load
                        window.djust._mountReady = true;
                    }
                }
                // Trigger form recovery and dj-auto-recover after reconnect mount
                if (window.djust._isReconnect) {
                    if (typeof window.djust._processFormRecovery === 'function') {
                        window.djust._processFormRecovery();
                    }
                    if (typeof window.djust._processAutoRecover === 'function') {
                        window.djust._processAutoRecover();
                    }
                }
                break;

            case 'patch':
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_update':
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'error':
                console.error('[SSE] Server error:', data.error);
                window.dispatchEvent(new CustomEvent('djust:error', {
                    detail: { error: data.error, traceback: data.traceback || null }
                }));
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'noop':
                if (this.lastEventName) {
                    if (!data.async_pending) {
                        globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    }
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'push_event':
                window.dispatchEvent(new CustomEvent('djust:push_event', {
                    detail: { event: data.event, payload: data.payload }
                }));
                dispatchPushEventToHooks(data.event, data.payload);
                globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                break;

            case 'navigation':
                if (window.djust.navigation) {
                    window.djust.navigation.handleNavigation(data);
                }
                break;

            case 'navigate':
                // Auth redirect (sent by server when auth check fails)
                window.location.href = data.to;
                break;

            case 'stream':
                if (window.djust.handleStreamMessage) {
                    window.djust.handleStreamMessage(data);
                }
                break;

            case 'accessibility':
                if (window.djust.accessibility) {
                    window.djust.accessibility.processAnnouncements(data.announcements);
                }
                break;

            case 'focus':
                if (window.djust.accessibility) {
                    window.djust.accessibility.processFocus([data.selector, data.options]);
                }
                break;

            case 'flash':
                // Flash message from server (put_flash / clear_flash)
                if (window.djust.flash) {
                    window.djust.flash.handleFlash(data);
                }
                break;

            case 'page_metadata':
                // Page metadata from server (page_title / page_meta)
                if (window.djust.pageMetadata) {
                    window.djust.pageMetadata.handlePageMetadata(data);
                }
                break;

            case 'reload':
                window.location.reload();
                break;

            default:
                if (globalThis.djustDebug) console.log('[SSE] Unknown message type:', data.type);
        }
    }

    /**
     * Send an event to the server via HTTP POST.
     *
     * Returns true if the event was dispatched, false if not (mirrors
     * LiveViewWebSocket.sendEvent so 11-event-handler.js works unchanged).
     *
     * @param {string}      eventName      Handler name on the LiveView
     * @param {Object}      params         Event parameters
     * @param {Element|null} triggerElement DOM element that triggered the event
     */
    sendEvent(eventName, params = {}, triggerElement = null) {
        if (!this.enabled || !this.viewMounted) {
            return false;
        }

        this.lastEventName = eventName;
        this.lastTriggerElement = triggerElement;

        const body = JSON.stringify({ event: eventName, params });
        this.stats.sent++;
        this.stats.sentBytes += body.length;

        fetch(`${this.sseBaseUrl}event/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
        })
            .then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            })
            .catch(err => {
                console.error('[SSE] Event POST failed:', err);
                globalLoadingManager.stopLoading(eventName, triggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
            });

        return true;
    }

    /**
     * No-op: SSE keepalives are handled server-side via comment lines.
     * This method exists so callers that call liveViewWS.startHeartbeat() work
     * without branching.
     */
    startHeartbeat() {
        // SSE transport uses server-side keepalive comments — no client heartbeat needed.
    }

    // ------------------------------------------------------------------ //
    // Private helpers
    // ------------------------------------------------------------------ //

    _generateSessionId() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        // Fallback using crypto.getRandomValues() — cryptographically secure,
        // available in all modern environments (IE 11+, Node 15+).
        if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
            const buf = new Uint8Array(16);
            crypto.getRandomValues(buf);
            buf[6] = (buf[6] & 0x0f) | 0x40; // UUID version 4
            buf[8] = (buf[8] & 0x3f) | 0x80; // UUID variant bits
            const hex = Array.from(buf, b => b.toString(16).padStart(2, '0')).join('');
            return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
        }
        throw new Error('djust: Web Crypto API is required but not available in this environment.');
    }
}

// Expose to window for 14-init.js transport negotiation
window.djust.LiveViewSSE = LiveViewSSE;

// === HTTP Fallback LiveView Client ===

// Track VDOM version for synchronization
let clientVdomVersion = null;

// Event sequencing (#560): monotonic ref counter for matching event
// responses to requests, and buffering server-initiated pushes during
// pending events. Uses a Set to track multiple concurrent pending refs.
let _eventRefCounter = 0;
let _pendingEventRefs = new Set();     // refs of events awaiting server response
let _pendingEventNames = new Map();    // ref -> event name for pending events
let _pendingTriggerEls = new Map();    // ref -> trigger element for loading state
let _tickBuffer = [];                  // buffered server-initiated patches during pending events

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
window.djust._pushTickBuffer = function(data) {
    _tickBuffer.push(data);
};

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

const _globalStateBus = new StateBus(); // eslint: prefixed _ (used in decorators.js, not in client.js IIFE)

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

    if (globalThis.djustDebug) console.log(`[DraftMode] Initializing draft mode with key: ${draftKey}`);

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
        if (globalThis.djustDebug) console.log('[DraftMode] Draft clear flag detected, clearing draft...');
        globalDraftManager.clearDraft(draftKey);
        draftRoot.removeAttribute('data-draft-clear');
    }
}

function _collectFormData(container) {
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

function _restoreFormData(container, data) {
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

// Track which Counter containers have been initialized to prevent duplicate listeners
// on each server response. WeakSet entries are GC'd when the container is removed.
const _initializedCounters = new WeakSet();

// Client-side React Counter component (vanilla JS implementation)
function initReactCounters() {
    document.querySelectorAll('[data-react-component="Counter"]').forEach(container => {
        // Skip containers already initialized — prevents N listeners after N server responses
        if (_initializedCounters.has(container)) return;
        _initializedCounters.add(container);

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
 * Backward compatibility: Also reads dj-params='{"key": value}' JSON blob
 * for 0.3.2 → 0.3.6+ migration. The dj-params attribute is deprecated;
 * use individual data-* attributes instead. data-* attributes take
 * precedence over dj-params keys with the same name.
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
            attr.name === 'dj-id' ||
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

        // Convert kebab-case to snake_case, then strip dj_ namespace prefix
        // so data-dj-preset="x" becomes {preset: "x"}, not {dj_preset: "x"}
        let key = rawKey.replace(/-/g, '_');
        if (key.startsWith('dj_')) {
            key = key.slice(3);
        }

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

    // dj-params backward compatibility: merge JSON blob into params.
    // data-* attributes take precedence over dj-params keys.
    const djParamsAttr = element.getAttribute('dj-params');
    if (djParamsAttr !== null) {
        if (globalThis.djustDebug) {
            console.warn(
                '[LiveView] dj-params is deprecated and will be removed in a future release. ' +
                'Replace with individual data-* attributes, e.g. data-todo-id:int="{{ todo.id }}". ' +
                'See the 0.3.2 → 0.3.6 migration guide in CHANGELOG.md.'
            );
        }
        if (djParamsAttr !== '') {
            try {
                const parsed = JSON.parse(djParamsAttr);
                if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    for (const [k, v] of Object.entries(parsed)) {
                        // Prevent prototype pollution
                        if (UNSAFE_KEYS.includes(k)) continue;
                        // data-* attributes win; only fill in missing keys
                        if (!(k in params)) {
                            params[k] = v;
                        }
                    }
                }
            } catch {
                if (globalThis.djustDebug) console.warn('[LiveView] Failed to parse dj-params JSON: "' + djParamsAttr + '"');
            }
        }
    }

    // Merge dj-value-* attributes. dj-value-* takes precedence over data-*
    // and dj-params, matching Phoenix's phx-value-* semantics.
    const djValues = collectDjValues(element);
    for (const [k, v] of Object.entries(djValues)) {
        params[k] = v;
    }

    return params;
}

/**
 * Collect dj-value-* attributes from an element and return as a params object.
 *
 * dj-value-* is the standard way to pass static context alongside events
 * (Phoenix LiveView's phx-value-* equivalent). Supports the same type-hint
 * suffixes as data-* attributes.
 *
 * Examples:
 *   dj-value-id="42"             -> { id: "42" }
 *   dj-value-id:int="42"        -> { id: 42 }
 *   dj-value-item-type="soft"   -> { item_type: "soft" }
 *   dj-value-active:bool="true" -> { active: true }
 *   dj-value-tags:list="a,b,c"  -> { tags: ["a", "b", "c"] }
 *
 * @param {HTMLElement} element - Element to extract dj-value-* from
 * @returns {Object} - Collected params with coerced types
 */
function collectDjValues(element) {
    const values = Object.create(null);

    for (const attr of element.attributes) {
        if (!attr.name.startsWith('dj-value-')) continue;

        // Parse: dj-value-item-id:int -> key="item_id", type="int"
        const nameWithoutPrefix = attr.name.slice(9); // Remove "dj-value-"
        const colonIndex = nameWithoutPrefix.lastIndexOf(':');
        let rawKey, typeHint;

        if (colonIndex !== -1) {
            rawKey = nameWithoutPrefix.slice(0, colonIndex);
            typeHint = nameWithoutPrefix.slice(colonIndex + 1);
        } else {
            rawKey = nameWithoutPrefix;
            typeHint = null;
        }

        // Convert kebab-case to snake_case
        const key = rawKey.replace(/-/g, '_');

        // Prevent prototype pollution
        if (UNSAFE_KEYS.includes(key)) continue;

        let value = attr.value;

        // Apply type coercion (same logic as extractTypedParams)
        if (typeHint) {
            switch (typeHint) {
                case 'int':
                case 'integer': {
                    if (value === '') { value = 0; }
                    else {
                        const parsed = parseInt(value, 10);
                        value = isNaN(parsed) ? null : parsed;
                    }
                    break;
                }
                case 'float':
                case 'number': {
                    if (value === '') { value = 0.0; }
                    else {
                        const parsed = parseFloat(value);
                        value = isNaN(parsed) ? null : parsed;
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
                    try { value = JSON.parse(value); }
                    catch { /* keep as string */ }
                    break;
                case 'list':
                    value = value ? value.split(',').map(v => v.trim()).filter(v => v) : [];
                    break;
                default:
                    break;
            }
        }

        values[key] = value;
    }

    return values;
}

// Export for global access
window.djust = window.djust || {};
window.djust.extractTypedParams = extractTypedParams;
window.djust.collectDjValues = collectDjValues;
/**
 * Check if element has dj-confirm and show confirmation dialog.
 * @param {HTMLElement} element - Element with potential dj-confirm attribute
 * @returns {boolean} - true if confirmed or no dialog needed, false if cancelled
 */
function checkDjConfirm(element) {
    const confirmMsg = element.getAttribute('dj-confirm');
    if (confirmMsg && !window.confirm(confirmMsg)) {
        return false; // User cancelled
    }
    return true; // Proceed
}

// Track which DOM nodes have actually had event handlers attached.
// WeakMap<Element, Set<string>> — keys are live DOM nodes, values are
// sets of handler types already bound (e.g. 'click', 'submit').
// Unlike data attributes, WeakMap entries are automatically invalidated
// when a DOM node is replaced (cloned/morphed) because the new node
// is a different object.  This prevents stale binding flags from
// blocking re-binding after VDOM patches.
const _boundHandlers = new WeakMap();

function _isHandlerBound(element, type) {
    const set = _boundHandlers.get(element);
    return set ? set.has(type) : false;
}

function _markHandlerBound(element, type) {
    let set = _boundHandlers.get(element);
    if (!set) {
        set = new Set();
        _boundHandlers.set(element, set);
    }
    set.add(type);
}

// ============================================================================
// Scoped Listener Helpers (window/document event binding)
// ============================================================================

// Track all elements that have scoped (window/document) listeners so we can
// sweep and clean up listeners for elements removed from the DOM.
const _scopedListenerElements = new Set();

/**
 * Clean up all scoped (window/document) listeners stored on an element.
 * Each listener is stored as { target, eventType, handler, capture } in
 * element._djustScopedListeners.
 * @param {HTMLElement} element
 */
function _cleanupScopedListeners(element) {
    if (!element._djustScopedListeners) return;
    for (const entry of element._djustScopedListeners) {
        entry.target.removeEventListener(entry.eventType, entry.handler, entry.capture || false);
    }
    element._djustScopedListeners = [];
    _scopedListenerElements.delete(element);
}

/**
 * Register a scoped listener on an element. Stores the reference for cleanup.
 * @param {HTMLElement} element - Declaring element (anchor)
 * @param {EventTarget} target - window or document
 * @param {string} eventType - DOM event type (e.g. 'keydown')
 * @param {Function} handler - Event handler function
 * @param {boolean} [capture=false] - Use capture phase
 */
function _addScopedListener(element, target, eventType, handler, capture) {
    if (!element._djustScopedListeners) element._djustScopedListeners = [];
    const useCapture = capture || false;
    element._djustScopedListeners.push({ target, eventType, handler, capture: useCapture });
    target.addEventListener(eventType, handler, useCapture);
    _scopedListenerElements.add(element);
}

/**
 * Sweep all tracked scoped-listener elements and remove listeners for any
 * elements that are no longer in the DOM. Called before binding new scoped
 * listeners to prevent accumulation from conditional rendering.
 */
function _sweepOrphanedScopedListeners() {
    for (const element of _scopedListenerElements) {
        if (!document.contains(element)) {
            _cleanupScopedListeners(element);
        }
    }
}

/**
 * Normalize a key name to match KeyboardEvent.key values.
 * @param {string} name - Key name from attribute (e.g. 'escape', 'enter', 'k')
 * @returns {string} - Normalized key name
 */
function _normalizeKeyName(name) {
    const lower = name.toLowerCase();
    const keyMap = {
        'escape': 'Escape',
        'enter': 'Enter',
        'tab': 'Tab',
        'space': ' ',
        'backspace': 'Backspace',
        'delete': 'Delete',
        'arrowup': 'ArrowUp',
        'arrowdown': 'ArrowDown',
        'arrowleft': 'ArrowLeft',
        'arrowright': 'ArrowRight',
    };
    return keyMap[lower] || name;
}

/**
 * Add component and embedded view context to event params.
 * Extracts component_id and view_id from the element's ancestry.
 * @param {Object} params - Event params object to augment
 * @param {HTMLElement} element - Element that triggered the event
 */
function addEventContext(params, element) {
    const componentId = getComponentId(element);
    if (componentId) params.component_id = componentId;
    const embeddedViewId = getEmbeddedViewId(element);
    if (embeddedViewId) params.view_id = embeddedViewId;
}

// WeakSet to track elements whose dj-mounted handler has already fired.
// Using WeakSet means entries are GC'd when the DOM node is replaced,
// allowing the handler to fire again for genuinely new elements.
const _mountedElements = new WeakSet();

/**
 * Check if element is a form element that supports the disabled property.
 * @param {HTMLElement} element
 * @returns {boolean}
 */
function _isFormElement(element) {
    const tag = element.tagName;
    return tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
}

/**
 * Lock an element: set data-djust-locked marker and disable/style it.
 * For form elements (button, input, select, textarea): sets disabled = true.
 * For non-form elements: adds CSS class 'djust-locked'.
 * @param {HTMLElement} element
 */
function _lockElement(element) {
    element.setAttribute('data-djust-locked', '');
    if (_isFormElement(element)) {
        element.disabled = true;
    } else {
        element.classList.add('djust-locked');
    }
}

/**
 * Check if element has dj-lock and is already locked. If locked, return true
 * to signal the caller to skip the event. If not locked but has dj-lock, lock it.
 * @param {HTMLElement} element
 * @returns {boolean} true if event should be skipped (already locked)
 */
function _checkAndLock(element) {
    if (!element.hasAttribute('dj-lock')) return false;
    if (element.hasAttribute('data-djust-locked')) return true; // Already locked, skip
    _lockElement(element);
    return false;
}

/**
 * Apply dj-disable-with: save original text and replace with loading text.
 * Only saves original text if not already saved (prevents overwrite on double-submit).
 * @param {HTMLElement} element - Element with dj-disable-with attribute
 */
function _applyDisableWith(element) {
    const disableText = element.getAttribute('dj-disable-with');
    if (!disableText) return;
    if (!element.hasAttribute('data-djust-original-text')) {
        element.setAttribute('data-djust-original-text', element.textContent);
    }
    element.textContent = disableText;
    element.disabled = true;
}

// ============================================================================
// Delegated Event Handlers
// ============================================================================

// WeakMaps to store per-element rate limit state for delegated events.
// Since delegation means we don't have a closure per element, we use
// WeakMaps to associate rate-limited wrappers with their elements.
const _inputRateLimitState = new WeakMap();
const _changeRateLimitState = new WeakMap();
const _clickRateLimitState = new WeakMap();
const _keyboardRateLimitState = new WeakMap();

// Helper: Extract field name from element attributes
// Priority: data-field (explicit) > name (standard) > id (fallback)
function getFieldName(element) {
    if (element.dataset && element.dataset.field) {
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
    // Merge dj-value-* attributes from the triggering element
    Object.assign(params, collectDjValues(element));
    addEventContext(params, element);
    return params;
}

/**
 * Get or create a rate-limited handler wrapper for an element.
 * @param {WeakMap} stateMap - WeakMap storing per-element rate limit state
 * @param {HTMLElement} element - Element to get/create wrapper for
 * @param {string} eventType - Event type (for server rate limit lookup)
 * @param {Function} rawHandler - The raw (unwrapped) handler function
 * @returns {Function} - Rate-limited wrapper or raw handler
 */
function _getOrCreateRateLimitedHandler(stateMap, element, eventType, rawHandler) {
    let state = stateMap.get(element);
    if (state) return state.wrapped;

    // Create rate-limited wrapper for this element
    let wrapped = _applyRateLimitAttrs(element, rawHandler);
    if (wrapped === rawHandler && window.djust.rateLimit) {
        wrapped = window.djust.rateLimit.wrapWithRateLimit(element, eventType, rawHandler);
    }
    stateMap.set(element, { wrapped });
    return wrapped;
}

/**
 * Handle dj-click events via delegation.
 * @param {HTMLElement} element - Element with dj-click attribute
 * @param {Event} e - The original click event
 */
async function _handleDjClick(element, e) {
    e.preventDefault();

    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // Read attribute at fire time so morphElement attribute updates take effect
    const rawClickValue = element.getAttribute('dj-click') || '';

    // dj-confirm: show confirmation dialog before executing commands/events
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // JS Commands: synchronously check whether the attribute is a
    // JSON command chain. If so, fire-and-forget the chain (push
    // ops still round-trip, but we don't block the rest of this
    // handler on them). A plain event name falls through to the
    // normal dj-click path without adding an `await` boundary,
    // so synchronous expectations on dj-disable-with and friends
    // continue to hold.
    if (window.djust.js) {
        const _ops = window.djust.js._parseCommandValue(rawClickValue);
        if (_ops) {
            window.djust.js._executeOps(_ops, element);
            return;
        }
    }

    const parsed = parseEventHandler(rawClickValue);

    // dj-disable-with: disable and show loading text
    _applyDisableWith(element);

    // Apply optimistic update if specified
    let optimisticUpdateId = null;
    if (window.djust.optimistic) {
        optimisticUpdateId = window.djust.optimistic.applyOptimisticUpdate(element, parsed.name);
    }

    // Extract all data-* attributes with type coercion support
    const params = extractTypedParams(element);

    // Add positional arguments from handler syntax if present
    // e.g., dj-click="set_period('month')" -> params._args = ['month']
    if (parsed.args.length > 0) {
        params._args = parsed.args;
    }

    addEventContext(params, element);

    // Pass target element and optimistic update ID
    params._targetElement = element;
    params._optimisticUpdateId = optimisticUpdateId;

    // Handle dj-target for scoped updates
    const targetSelector = element.getAttribute('dj-target');
    if (targetSelector) {
        params._djTargetSelector = targetSelector;
    }

    await handleEvent(parsed.name, params);
}

/**
 * Handle dj-copy — client-side clipboard copy (no server round-trip).
 * @param {HTMLElement} element - Element with dj-copy attribute
 * @param {Event} e - The original click event
 */
function _handleDjCopy(element, e) {
    e.preventDefault();
    // Read attribute at click time (not bind time) so morph updates take effect
    var currentValue = element.getAttribute('dj-copy');
    if (!currentValue) return;

    // Selector-based copy: if value starts with #, . or [, try querySelector
    var textToCopy = currentValue;
    if (currentValue.charAt(0) === '#' || currentValue.charAt(0) === '.' || currentValue.charAt(0) === '[') {
        try {
            var target = document.querySelector(currentValue);
            if (target) {
                textToCopy = target.textContent;
            }
        } catch (err) {
            // Invalid selector — fall back to literal copy
        }
    }

    navigator.clipboard.writeText(textToCopy).then(function() {
        // CSS class feedback: add class and remove after 2s
        var cssClass = element.getAttribute('dj-copy-class') || 'dj-copied';
        element.classList.add(cssClass);
        setTimeout(function() { element.classList.remove(cssClass); }, 2000);

        // Text feedback: custom or default "Copied!"
        var feedbackText = element.getAttribute('dj-copy-feedback') || 'Copied!';
        var original = element.textContent;
        element.textContent = feedbackText;
        setTimeout(function() { element.textContent = original; }, 1500);

        // Optional server event for analytics
        var copyEvent = element.getAttribute('dj-copy-event');
        if (copyEvent) {
            handleEvent(copyEvent, { text: textToCopy });
        }
    });
}

/**
 * Handle dj-submit events on forms via delegation.
 * @param {HTMLElement} element - Form element with dj-submit attribute
 * @param {Event} e - The original submit event
 */
async function _handleDjSubmit(element, e) {
    e.preventDefault();

    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read attribute at fire time so morphElement attribute updates take effect
    const submitHandler = element.getAttribute('dj-submit');

    // dj-disable-with: disable submit buttons within the form
    const submitBtns = element.querySelectorAll('button[type="submit"][dj-disable-with]');
    submitBtns.forEach(btn => _applyDisableWith(btn));
    // Also check the submitter if it has dj-disable-with
    if (e.submitter && e.submitter.hasAttribute('dj-disable-with')) {
        _applyDisableWith(e.submitter);
    }

    const formData = new FormData(element);
    const params = Object.fromEntries(formData.entries());

    // Merge dj-value-* attributes from the form element
    Object.assign(params, collectDjValues(element));

    addEventContext(params, element);

    // _target: include submitter name if available
    params._target = (e.submitter && (e.submitter.name || e.submitter.id)) || null;

    // Pass target element for optimistic updates (Phase 3)
    params._targetElement = element;

    await handleEvent(submitHandler, params);
}

/**
 * Handle dj-change events via delegation.
 * @param {HTMLElement} element - Element with dj-change attribute
 * @param {Event} e - The original change event
 */
async function _handleDjChange(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const changeHandler = element.getAttribute('dj-change');
    const parsedChange = parseEventHandler(changeHandler);

    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    const params = buildFormEventParams(e.target, value);

    // Add positional arguments from handler syntax if present
    // e.g., dj-change="toggle_todo(3)" -> params._args = [3]
    if (parsedChange.args.length > 0) {
        params._args = parsedChange.args;
    }

    // _target: include triggering field's name (or id, or null)
    params._target = e.target.name || e.target.id || null;

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
    await handleEvent(parsedChange.name, params);
}

/**
 * Handle dj-input events via delegation.
 * @param {HTMLElement} element - Element with dj-input attribute
 * @param {Event} e - The original input event
 */
async function _handleDjInput(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const inputHandler = element.getAttribute('dj-input');
    const parsedInput = parseEventHandler(inputHandler);

    const params = buildFormEventParams(e.target, e.target.value);
    if (parsedInput.args.length > 0) {
        params._args = parsedInput.args;
    }

    // _target: include triggering field's name (or id, or null)
    params._target = e.target.name || e.target.id || null;

    await handleEvent(parsedInput.name, params);
}

/**
 * Handle dj-blur events via delegation (using focusout which bubbles).
 * @param {HTMLElement} element - Element with dj-blur attribute
 * @param {Event} e - The original focusout event
 */
async function _handleDjBlur(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const blurHandler = element.getAttribute('dj-blur');
    const parsedBlur = parseEventHandler(blurHandler);

    const params = buildFormEventParams(e.target, e.target.value);
    if (parsedBlur.args.length > 0) {
        params._args = parsedBlur.args;
    }
    await handleEvent(parsedBlur.name, params);
}

/**
 * Handle dj-focus events via delegation (using focusin which bubbles).
 * @param {HTMLElement} element - Element with dj-focus attribute
 * @param {Event} e - The original focusin event
 */
async function _handleDjFocus(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const focusHandler = element.getAttribute('dj-focus');
    const parsedFocus = parseEventHandler(focusHandler);

    const params = buildFormEventParams(e.target, e.target.value);
    if (parsedFocus.args.length > 0) {
        params._args = parsedFocus.args;
    }
    await handleEvent(parsedFocus.name, params);
}

/**
 * Handle dj-paste events via delegation.
 * Extracts structured clipboard payload (plain text, rich HTML, files)
 * and sends it to the server as a single event call.
 * @param {HTMLElement} element - Element with dj-paste attribute
 * @param {Event} e - The original paste event
 */
async function _handleDjPaste(element, e) {
    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    // Read and parse attribute at fire time
    const pasteHandler = element.getAttribute('dj-paste');
    const parsedPaste = parseEventHandler(pasteHandler);

    const clipboardData = e.clipboardData || window.clipboardData;
    if (!clipboardData) {
        // No clipboard data available — let the default paste happen
        return;
    }

    // Build structured payload: text, html, and file metadata.
    // The actual file bytes are NOT sent in this event — that would
    // blow the WS frame budget. Instead, set a dj-upload slot on
    // the element and UploadMixin will pick up any files from the
    // clipboard files list via the existing upload pipeline.
    let text = '';
    let html = '';
    const files = [];
    try {
        text = clipboardData.getData('text/plain') || '';
    } catch (err) { /* older browsers */ }
    try {
        html = clipboardData.getData('text/html') || '';
    } catch (err) { /* older browsers */ }
    if (clipboardData.files) {
        for (let i = 0; i < clipboardData.files.length; i++) {
            const f = clipboardData.files[i];
            files.push({
                name: f.name || 'clipboard-paste',
                type: f.type || '',
                size: f.size || 0,
            });
        }
    }

    // If the element has an upload slot configured, route pasted
    // files through the upload pipeline (image paste → chat, etc).
    // We route BEFORE sending the server event so the handler can
    // react to both the metadata and the pending upload in one tick.
    if (files.length > 0 && window.djust && window.djust.uploads && element.getAttribute('dj-upload')) {
        try {
            await window.djust.uploads.queueClipboardFiles(element, clipboardData.files);
        } catch (err) {
            if (globalThis.djustDebug) console.log('[LiveView] dj-paste: upload route failed', err);
        }
    }

    const params = {
        text: text,
        html: html,
        has_files: files.length > 0,
        files: files,
    };
    if (parsedPaste.args.length > 0) {
        params._args = parsedPaste.args;
    }

    // Suppress the default paste only when the element opts in
    // with dj-paste-suppress. Otherwise let the browser also
    // insert into the input so hybrid UIs still feel natural.
    if (element.hasAttribute('dj-paste-suppress')) {
        e.preventDefault();
    }

    await handleEvent(parsedPaste.name, params);
}

/**
 * Handle dj-keydown / dj-keyup events via delegation.
 * @param {HTMLElement} element - Element with dj-keydown or dj-keyup attribute
 * @param {Event} e - The original keyboard event
 * @param {string} eventType - 'keydown' or 'keyup'
 */
async function _handleDjKeyboard(element, e, eventType) {
    // Read attribute at fire time
    const keyHandler = element.getAttribute('dj-' + eventType);
    if (!keyHandler) return;

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

    // dj-lock: skip if already locked
    if (_checkAndLock(element)) return;

    // dj-confirm: show confirmation dialog before sending event
    if (!checkDjConfirm(element)) {
        return; // User cancelled
    }

    const fieldName = getFieldName(e.target);
    const params = {
        key: e.key,
        code: e.code,
        value: e.target.value,
        field: fieldName
    };

    // Merge dj-value-* attributes from the element
    Object.assign(params, collectDjValues(element));

    addEventContext(params, e.target);

    // Add target element and handle dj-target
    params._targetElement = e.target;
    const targetSelector = element.getAttribute('dj-target');
    if (targetSelector) {
        params._djTargetSelector = targetSelector;
    }

    await handleEvent(handlerName, params);
}

// ============================================================================
// Event Delegation
// ============================================================================

/**
 * Install ONE delegated listener per DOM event type on the given root element.
 * Idempotent — checks root._djustDelegated to avoid double-installing.
 * @param {HTMLElement} root - The LiveView root element to delegate from
 */
function installDelegatedListeners(root) {
    if (root._djustDelegated) return;
    root._djustDelegated = true;

    // click → dj-copy (client-only) first, then dj-click
    root.addEventListener('click', function(e) {
        var copyEl = e.target.closest('[dj-copy]');
        if (copyEl) {
            _handleDjCopy(copyEl, e);
            return;
        }
        var clickEl = e.target.closest('[dj-click]');
        if (clickEl) {
            // Rate-limit per element using WeakMap
            var rawHandler = function(ev) { return _handleDjClick(clickEl, ev); };
            var wrapped = _getOrCreateRateLimitedHandler(_clickRateLimitState, clickEl, 'click', rawHandler);
            wrapped(e);
        }
    });

    // submit → dj-submit
    root.addEventListener('submit', function(e) {
        var submitEl = e.target.closest('[dj-submit]');
        if (submitEl) {
            _handleDjSubmit(submitEl, e);
        }
    });

    // change → dj-change
    root.addEventListener('change', function(e) {
        var changeEl = e.target.closest('[dj-change]');
        if (changeEl) {
            // Rate-limit per element using WeakMap
            var rawHandler = function(ev) { return _handleDjChange(changeEl, ev); };
            var wrapped = _getOrCreateRateLimitedHandler(_changeRateLimitState, changeEl, 'change', rawHandler);
            wrapped(e);
        }
    });

    // input → dj-input (with smart rate limiting)
    root.addEventListener('input', function(e) {
        var inputEl = e.target.closest('[dj-input]');
        if (inputEl) {
            // Get or create rate-limited wrapper for this element
            var state = _inputRateLimitState.get(inputEl);
            if (!state) {
                // Build the raw handler
                var rawHandler = function(ev) { return _handleDjInput(inputEl, ev); };

                // Determine rate limit strategy
                var inputType = inputEl.type || inputEl.tagName.toLowerCase();
                var rateLimit = Object.prototype.hasOwnProperty.call(DEFAULT_RATE_LIMITS, inputType) ? DEFAULT_RATE_LIMITS[inputType] : { type: 'debounce', ms: 300 };

                // Check for explicit overrides: dj-* attributes take precedence
                if (inputEl.hasAttribute('dj-debounce')) {
                    var djVal = inputEl.getAttribute('dj-debounce');
                    if (djVal === 'blur') {
                        rateLimit.type = 'blur';
                        rateLimit.ms = 0;
                    } else {
                        rateLimit.type = 'debounce';
                        rateLimit.ms = parseInt(djVal, 10);
                    }
                } else if (inputEl.hasAttribute('dj-throttle')) {
                    rateLimit.type = 'throttle';
                    rateLimit.ms = parseInt(inputEl.getAttribute('dj-throttle'), 10);
                } else if (inputEl.hasAttribute('data-debounce')) {
                    rateLimit.type = 'debounce';
                    rateLimit.ms = parseInt(inputEl.getAttribute('data-debounce'));
                } else if (inputEl.hasAttribute('data-throttle')) {
                    rateLimit.type = 'throttle';
                    rateLimit.ms = parseInt(inputEl.getAttribute('data-throttle'));
                }

                // Apply rate limiting wrapper
                var wrapped;
                if (rateLimit.type === 'blur') {
                    // dj-debounce="blur": defer until element loses focus
                    var latestArgs = null;
                    wrapped = function() {
                        latestArgs = arguments;
                    };
                    inputEl.addEventListener('blur', function() {
                        if (latestArgs !== null) {
                            rawHandler.apply(null, latestArgs);
                            latestArgs = null;
                        }
                    });
                } else if (rateLimit.type === 'throttle') {
                    wrapped = throttle(rawHandler, rateLimit.ms);
                } else {
                    wrapped = debounce(rawHandler, rateLimit.ms);
                }

                state = { wrapped: wrapped };
                _inputRateLimitState.set(inputEl, state);
            }
            state.wrapped(e);
        }
    });

    // keydown → dj-keydown
    root.addEventListener('keydown', function(e) {
        var keyEl = e.target.closest('[dj-keydown]');
        if (keyEl) {
            var rawHandler = function(ev) { return _handleDjKeyboard(keyEl, ev, 'keydown'); };
            var wrapped = _getOrCreateRateLimitedHandler(_keyboardRateLimitState, keyEl, 'keydown', rawHandler);
            wrapped(e);
        }
    });

    // keyup → dj-keyup
    root.addEventListener('keyup', function(e) {
        var keyEl = e.target.closest('[dj-keyup]');
        if (keyEl) {
            var rawHandler = function(ev) { return _handleDjKeyboard(keyEl, ev, 'keyup'); };
            // keyup shares the keyboard state map but different element keys won't collide
            // since WeakMap is keyed by element object
            var state = _keyboardRateLimitState.get(keyEl);
            if (!state) {
                var wrappedKu = _applyRateLimitAttrs(keyEl, rawHandler);
                if (wrappedKu === rawHandler && window.djust.rateLimit) {
                    wrappedKu = window.djust.rateLimit.wrapWithRateLimit(keyEl, 'keyup', rawHandler);
                }
                _keyboardRateLimitState.set(keyEl, { wrapped: wrappedKu });
                state = _keyboardRateLimitState.get(keyEl);
            }
            state.wrapped(e);
        }
    });

    // paste → dj-paste
    root.addEventListener('paste', function(e) {
        var pasteEl = e.target.closest('[dj-paste]');
        if (pasteEl) {
            _handleDjPaste(pasteEl, e);
        }
    });

    // focusin → dj-focus (focusin bubbles, focus doesn't)
    root.addEventListener('focusin', function(e) {
        var focusEl = e.target.closest('[dj-focus]');
        if (focusEl) {
            _handleDjFocus(focusEl, e);
        }
    });

    // focusout → dj-blur (focusout bubbles, blur doesn't)
    root.addEventListener('focusout', function(e) {
        var blurEl = e.target.closest('[dj-blur]');
        if (blurEl) {
            _handleDjBlur(blurEl, e);
        }
    });
}

function bindLiveViewEvents(scope) {
    const root = scope || getLiveViewRoot() || document;

    // Install delegated listeners ONCE on the LiveView root
    const liveRoot = getLiveViewRoot();
    if (liveRoot) installDelegatedListeners(liveRoot);

    // Bind upload handlers (dj-upload, dj-upload-drop, dj-upload-preview)
    if (window.djust.uploads) {
        window.djust.uploads.bindHandlers(scope);
    }

    // Bind navigation directives (dj-patch, dj-navigate)
    if (window.djust.navigation) {
        window.djust.navigation.bindDirectives(scope);
    }

    // === Per-element scanning section (only for non-delegable events) ===

    // dj-poll needs per-element interval setup
    const pollSelector = '[dj-poll]';
    const pollElements = root.querySelectorAll(pollSelector);
    pollElements.forEach(element => {
        const pollHandler = element.getAttribute('dj-poll');
        if (pollHandler && !_isHandlerBound(element, 'poll')) {
            _markHandlerBound(element, 'poll');
            const parsed = parseEventHandler(pollHandler);
            const interval = parseInt(element.getAttribute('dj-poll-interval'), 10) || 5000;
            const pollParams = extractTypedParams(element);

            const intervalId = setInterval(() => {
                if (document.hidden) return;
                handleEvent(parsed.name, Object.assign({}, pollParams, { _skipLoading: true }));
            }, interval);

            element._djustPollIntervalId = intervalId;

            // Pause/resume on visibility change
            const visHandler = () => {
                if (!document.hidden) {
                    handleEvent(parsed.name, Object.assign({}, pollParams, { _skipLoading: true }));
                }
            };
            document.addEventListener('visibilitychange', visHandler);
            element._djustPollVisibilityHandler = visHandler;
        }
    });

    // ================================================================
    // Scoped listeners: dj-window-*, dj-document-*, dj-click-away, dj-shortcut
    // ================================================================

    // Sweep orphaned scoped listeners (elements removed from DOM by conditional rendering)
    _sweepOrphanedScopedListeners();

    // --- Feature 1: dj-window-* and dj-document-* event scoping ---
    const scopedPrefixes = [
        { prefix: 'dj-window-', target: window },
        { prefix: 'dj-document-', target: document },
    ];
    const scopedEventTypes = ['keydown', 'keyup', 'click', 'scroll', 'resize'];

    // Collect elements with dj-window-*/dj-document-* attributes.
    // These have dynamic attribute names (e.g. dj-window-keydown.escape)
    // that CSS selectors can't match, so we scan all elements within root.
    // This is a small scan since most pages have 0-5 scoped listener elements.
    const scopedElements = root.querySelectorAll('*');
    for (const { prefix, target } of scopedPrefixes) {
        for (const evtType of scopedEventTypes) {
            // scroll and resize only make sense on window
            if (target === document && (evtType === 'scroll' || evtType === 'resize')) continue;

            const attrBase = prefix + evtType;
            // Scan elements for attributes starting with this prefix+eventType
            // (covers both exact matches and key-modifier variants like dj-window-keydown.escape)
            scopedElements.forEach(element => {
                for (const attr of element.attributes) {
                    if (!attr.name.startsWith(attrBase)) continue;
                    const bindType = attr.name; // e.g. 'dj-window-keydown' or 'dj-window-keydown.escape'
                    if (_isHandlerBound(element, bindType)) continue;
                    _markHandlerBound(element, bindType);

                    // Parse handler name and optional key modifier
                    const attrValue = attr.value;
                    const suffix = attr.name.slice(attrBase.length); // '' or '.escape'
                    const requiredKey = suffix.startsWith('.') ? _normalizeKeyName(suffix.slice(1)) : null;

                    // Parse handler name (supports handler(args) syntax)
                    const parsed = parseEventHandler(attrValue);

                    const scopedHandler = async (e) => {
                        // Key filtering for keydown/keyup
                        if (requiredKey && e.key !== requiredKey) return;

                        // Build params from the declaring element
                        const params = extractTypedParams(element);
                        addEventContext(params, element);

                        // Add event-specific params
                        if (evtType === 'keydown' || evtType === 'keyup') {
                            params.key = e.key;
                            params.code = e.code;
                        } else if (evtType === 'click') {
                            params.clientX = e.clientX;
                            params.clientY = e.clientY;
                        } else if (evtType === 'scroll') {
                            params.scrollY = window.scrollY;
                            params.scrollX = window.scrollX;
                        } else if (evtType === 'resize') {
                            params.innerWidth = window.innerWidth;
                            params.innerHeight = window.innerHeight;
                        }

                        if (parsed.args.length > 0) {
                            params._args = parsed.args;
                        }

                        await handleEvent(parsed.name, params);
                    };

                    // Apply default throttle for scroll/resize
                    let finalHandler = scopedHandler;
                    if (evtType === 'scroll' || evtType === 'resize') {
                        const throttleMs = parseInt(element.getAttribute('data-throttle'), 10) || 150;
                        finalHandler = throttle(scopedHandler, throttleMs);
                    } else if (element.hasAttribute('data-debounce')) {
                        finalHandler = debounce(scopedHandler, parseInt(element.getAttribute('data-debounce'), 10));
                    } else if (element.hasAttribute('data-throttle')) {
                        finalHandler = throttle(scopedHandler, parseInt(element.getAttribute('data-throttle'), 10));
                    }

                    _addScopedListener(element, target, evtType, finalHandler, false);
                }
            });
        }
    }

    // --- Feature 2: dj-click-away ---
    document.querySelectorAll('[dj-click-away]').forEach(element => {
        if (_isHandlerBound(element, 'click-away')) return;
        _markHandlerBound(element, 'click-away');

        const handlerName = element.getAttribute('dj-click-away');

        const clickAwayHandler = async (e) => {
            // Only fire if click is outside the element
            if (element.contains(e.target)) return;

            // dj-confirm support
            if (!checkDjConfirm(element)) return;

            const params = extractTypedParams(element);
            addEventContext(params, element);

            await handleEvent(handlerName, params);
        };

        // Use capture phase so stopPropagation inside doesn't prevent detection
        _addScopedListener(element, document, 'click', clickAwayHandler, true);
    });

    // --- Feature 3: dj-shortcut ---
    document.querySelectorAll('[dj-shortcut]').forEach(element => {
        if (_isHandlerBound(element, 'shortcut')) return;
        _markHandlerBound(element, 'shortcut');

        const attrValue = element.getAttribute('dj-shortcut');
        const allowInInput = element.hasAttribute('dj-shortcut-in-input');

        // Parse comma-separated bindings
        // Each binding: [modifier+...]key:handler[:prevent]
        const bindings = attrValue.split(',').map(b => b.trim()).filter(b => b).map(binding => {
            const parts = binding.split(':');
            const keyCombo = parts[0].trim(); // e.g. 'ctrl+k' or 'escape'
            const handler = parts[1] ? parts[1].trim() : '';
            const preventDefault = parts[2] ? parts[2].trim() === 'prevent' : false;

            // Parse key combo into modifiers + key
            const comboParts = keyCombo.split('+');
            const key = _normalizeKeyName(comboParts[comboParts.length - 1]);
            const modifiers = new Set();
            for (let i = 0; i < comboParts.length - 1; i++) {
                modifiers.add(comboParts[i].toLowerCase());
            }

            return { key, modifiers, handler, preventDefault, comboString: keyCombo };
        });

        const shortcutHandler = async (e) => {
            // Skip if active element is a form input (unless opt-out)
            if (!allowInInput) {
                const active = document.activeElement;
                if (active) {
                    const tag = active.tagName;
                    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || active.isContentEditable) {
                        return;
                    }
                }
            }

            // Skip if element is not visible (hidden modals etc.)
            // Check hidden attribute and display:none; offsetParent is used as
            // a secondary check when available (not in JSDOM/SSR environments).
            if (element.hidden || element.style.display === 'none') return;
            if (!document.contains(element)) return;

            for (const binding of bindings) {
                if (e.key !== binding.key) continue;

                // Check modifiers
                const ctrlMatch = binding.modifiers.has('ctrl') === e.ctrlKey;
                const altMatch = binding.modifiers.has('alt') === e.altKey;
                const shiftMatch = binding.modifiers.has('shift') === e.shiftKey;
                const metaMatch = binding.modifiers.has('meta') === e.metaKey;

                if (!ctrlMatch || !altMatch || !shiftMatch || !metaMatch) continue;

                // Match found
                if (binding.preventDefault) {
                    e.preventDefault();
                }

                const params = extractTypedParams(element);
                addEventContext(params, element);
                params.key = e.key;
                params.code = e.code;
                params.shortcut = binding.comboString;

                await handleEvent(binding.handler, params);
                return; // Fire first match only
            }
        };

        _addScopedListener(element, document, 'keydown', shortcutHandler, false);
    });

    // Re-scan dj-loading attributes after DOM updates so dynamically
    // added elements (e.g. inside modals) get registered.
    globalLoadingManager.scanAndRegister();

    // dj-mounted: fire event for elements that have entered the DOM after
    // initial mount. Uses WeakSet to track already-fired elements so each
    // DOM node only triggers once (replaced nodes are new objects and will fire again).
    if (window.djust._mountReady) {
        document.querySelectorAll('[dj-mounted]').forEach(el => {
            if (_mountedElements.has(el)) return;
            _mountedElements.add(el);

            const handlerName = el.getAttribute('dj-mounted');
            if (!handlerName) return;

            // Collect dj-value-* and data-* params from the mounted element
            const params = extractTypedParams(el);
            addEventContext(params, el);

            handleEvent(handlerName, params);
        });
    }
}

/**
 * Apply dj-debounce / dj-throttle HTML attributes to an event handler.
 * If the element has dj-debounce or dj-throttle, wraps the handler accordingly.
 * dj-debounce="blur" is a special value that defers until the element loses focus.
 * Returns the (potentially wrapped) handler.
 * @param {HTMLElement} element - Element with potential dj-debounce/dj-throttle
 * @param {Function} handler - Original event handler
 * @returns {Function} - Wrapped or original handler
 */
function _applyRateLimitAttrs(element, handler) {
    if (element.hasAttribute('dj-debounce')) {
        const val = element.getAttribute('dj-debounce');
        if (val === 'blur') {
            // Special: defer event until element loses focus
            let latestArgs = null;
            const blurWrapper = function (...args) {
                latestArgs = args;
            };
            element.addEventListener('blur', function () {
                if (latestArgs !== null) {
                    handler(...latestArgs);
                    latestArgs = null;
                }
            });
            return blurWrapper;
        }
        const ms = parseInt(val, 10);
        if (ms === 0) {
            return handler; // dj-debounce="0" means no debounce
        }
        return debounce(handler, ms);
    }
    if (element.hasAttribute('dj-throttle')) {
        const ms = parseInt(element.getAttribute('dj-throttle'), 10);
        return throttle(handler, ms);
    }
    return handler;
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
    return document.querySelector('[dj-view]') || document.querySelector('[dj-root]') || document.body;
}

// Helper: Clear optimistic state
function clearOptimisticState(eventName) {
    if (eventName && optimisticUpdates.has(eventName)) {
        const { element: _element, originalState: _originalState } = optimisticUpdates.get(eventName);
        // TODO: Restore original state on error (e.g. _element, _originalState)
        optimisticUpdates.delete(eventName);
    }
}

/**
 * Reinitialize all dynamic content after a DOM replacement.
 *
 * Call this after any operation that replaces or morphs DOM content
 * (html_update, html_recovery, TurboNav, embedded view update, etc.)
 * instead of manually calling initReactCounters + initTodoItems +
 * bindLiveViewEvents + updateHooks individually.
 */
// WeakSet to track elements that have already been scrolled into view.
// Fresh DOM nodes (from VDOM replacement) won't be in the set, so they
// will scroll again — correct behavior for newly inserted content.
const _scrolledElements = new WeakSet();

function reinitAfterDOMUpdate(scope) {
    initReactCounters();
    initTodoItems();
    bindLiveViewEvents(scope);
    if (window.djust.mountHooks) {
        // updateHooks scans for [dj-hook] — scope it too
        const hookRoot = scope || getLiveViewRoot();
        hookRoot.querySelectorAll('[dj-hook]').forEach(el => {
            // Delegate to the hook system's per-element logic
            if (window.djust.mountHooks) window.djust.mountHooks(el);
        });
    }
    updateHooks();

    // dj-scroll-into-view: auto-scroll elements into view after DOM updates
    const scrollRoot = scope || document;
    scrollRoot.querySelectorAll('[dj-scroll-into-view]').forEach(el => {
        if (_scrolledElements.has(el)) return;
        _scrolledElements.add(el);

        const value = el.getAttribute('dj-scroll-into-view') || '';
        let options;
        switch (value) {
            case 'instant':
                options = { behavior: 'instant', block: 'nearest' };
                break;
            case 'center':
                options = { behavior: 'smooth', block: 'center' };
                break;
            case 'start':
                options = { behavior: 'smooth', block: 'start' };
                break;
            case 'end':
                options = { behavior: 'smooth', block: 'end' };
                break;
            default:
                options = { behavior: 'smooth', block: 'nearest' };
                break;
        }
        el.scrollIntoView(options);
    });
}

/**
 * Process dj-auto-recover elements after a WebSocket reconnect.
 * Scans for [dj-auto-recover] elements, serializes their DOM state
 * (form values + data-* attributes), and fires the named event.
 * Only fires when _isReconnect flag is set; clears the flag after processing.
 */
function _processAutoRecover() {
    if (!window.djust._isReconnect) return;
    window.djust._isReconnect = false;

    document.querySelectorAll('[dj-auto-recover]').forEach(function(container) {
        var handlerName = container.getAttribute('dj-auto-recover');
        if (!handlerName) return;

        // Serialize form field values within the container
        var formValues = {};
        container.querySelectorAll('input, textarea, select').forEach(function(field) {
            var name = field.name;
            if (!name) return;
            if (field.type === 'checkbox') {
                formValues[name] = field.checked;
            } else if (field.type === 'radio') {
                if (field.checked) formValues[name] = field.value;
            } else {
                formValues[name] = field.value;
            }
        });

        // Collect data-* attributes from the container element
        var dataAttrs = {};
        for (var i = 0; i < container.attributes.length; i++) {
            var attr = container.attributes[i];
            if (attr.name.startsWith('data-')) {
                var key = attr.name.slice(5); // Strip 'data-' prefix
                dataAttrs[key] = attr.value;
            }
        }

        var params = {
            _form_values: formValues,
            _data_attrs: dataAttrs
        };

        handleEvent(handlerName, params);
    });
}

/**
 * Process automatic form recovery after a WebSocket reconnect.
 * Scans all form fields with dj-change or dj-input inside [dj-view] and
 * fires synthetic change events when the DOM value differs from the
 * server-rendered default, restoring server state transparently.
 *
 * Skips fields with dj-no-recover or fields inside dj-auto-recover
 * containers (custom handlers take precedence).
 *
 * Fires events sequentially (batched) via handleEvent() to avoid
 * race conditions on the server.
 */
function _processFormRecovery() {
    if (!window.djust._isReconnect) return;

    var root = document.querySelector('[dj-view]');
    if (!root) root = document.querySelector('[dj-root]');
    if (!root) return;

    // Collect fields to recover
    var fields = root.querySelectorAll('input[dj-change], textarea[dj-change], select[dj-change], input[dj-input], textarea[dj-input], select[dj-input]');
    var pendingEvents = [];

    for (var i = 0; i < fields.length; i++) {
        var field = fields[i];

        // Skip fields with dj-no-recover
        if (field.hasAttribute('dj-no-recover')) continue;

        // Skip fields inside dj-auto-recover containers (custom handler takes precedence)
        if (field.closest('[dj-auto-recover]')) continue;

        // Determine handler name — prefer dj-change, fall back to dj-input
        var handlerAttr = field.hasAttribute('dj-change') ? 'dj-change' : 'dj-input';
        var handlerString = field.getAttribute(handlerAttr);
        if (!handlerString) continue;

        // Parse handler string to extract function name
        var parsed = parseEventHandler(handlerString);
        var handlerName = parsed.name;

        // Determine current DOM value and server default
        var tagName = field.tagName.toLowerCase();
        var fieldType = (field.type || '').toLowerCase();
        var domValue;
        var serverDefault;

        if (fieldType === 'checkbox') {
            domValue = field.checked;
            serverDefault = field.hasAttribute('checked');
        } else if (fieldType === 'radio') {
            domValue = field.checked;
            serverDefault = field.hasAttribute('checked');
        } else if (tagName === 'select') {
            domValue = field.value;
            // Server default: the option with 'selected' attribute, or the first option
            var selectedOption = field.querySelector('option[selected]');
            serverDefault = selectedOption ? selectedOption.value : (field.options.length > 0 ? field.options[0].value : '');
        } else {
            // text, textarea, number, email, etc.
            domValue = field.value;
            serverDefault = field.getAttribute('value') || (tagName === 'textarea' ? field.defaultValue : '');
        }

        // Skip if DOM value matches server default (avoid unnecessary server work)
        if (domValue === serverDefault) continue;

        // Build event params matching dj-change param structure
        var value = (fieldType === 'checkbox' || fieldType === 'radio') ? domValue : domValue;
        var fieldName = field.name || field.id || null;
        var params = { value: value, field: fieldName };

        // Add positional arguments from handler syntax if present
        if (parsed.args.length > 0) {
            params._args = parsed.args;
        }

        // _target: include triggering field's name
        params._target = fieldName;

        pendingEvents.push({ handlerName: handlerName, params: params });
    }

    // Fire events sequentially to avoid server race conditions
    if (pendingEvents.length > 0) {
        if (globalThis.djustDebug) console.log('[LiveView] Form recovery: restoring ' + pendingEvents.length + ' field(s)');
        var fireSequentially = function(index) {
            if (index >= pendingEvents.length) return;
            var evt = pendingEvents[index];
            void handleEvent(evt.handlerName, evt.params).then(function() { fireSequentially(index + 1); });
        };
        fireSequentially(0);
    }
}

// Export for testing and for createNodeFromVNode to mark VDOM-created elements as bound
window.djust.bindLiveViewEvents = bindLiveViewEvents;
window.djust.reinitAfterDOMUpdate = reinitAfterDOMUpdate;
window.djust.installDelegatedListeners = installDelegatedListeners;
window.djust._isHandlerBound = _isHandlerBound;
window.djust._markHandlerBound = _markHandlerBound;
window.djust._processAutoRecover = _processAutoRecover;
window.djust._processFormRecovery = _processFormRecovery;
window.djust._isReconnect = false;

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
                } else if (modifier === 'hide') {
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
        // Clean up entries for elements no longer in the DOM (e.g. after morphdom/patches)
        this.registeredElements.forEach((_config, element) => {
            if (!element.isConnected) {
                this.registeredElements.delete(element);
            }
        });

        // Use targeted attribute selectors for better performance on large pages
        const selectors = [
            '[dj-loading\\.disable]',
            '[dj-loading\\.show]',
            '[dj-loading\\.hide]',
            '[dj-loading\\.class]',
            '[dj-loading\\.for]'
        ].join(',');

        const loadingElements = document.querySelectorAll(selectors);
        loadingElements.forEach(element => {
            // Skip elements already registered — preserves originalState captured
            // before loading started (client-side loading modifies DOM styles,
            // and re-registering would capture the loading state as "original")
            if (this.registeredElements.has(element)) return;
            // Determine associated event name:
            // 1. Explicit: dj-loading.for="event_name" (works on any element)
            // 2. Implicit: from the element's own dj-click, dj-submit, etc.
            let eventName = element.getAttribute('dj-loading.for');
            if (!eventName) {
                const eventAttr = Array.from(element.attributes).find(
                    attr => attr.name.startsWith('dj-') && !attr.name.startsWith('dj-loading')
                );
                eventName = eventAttr ? eventAttr.value : null;
            }
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

    // Extract client-only properties before sending to server.
    // These are DOM references or internal flags that cannot be JSON-serialized
    // and would corrupt the params payload (e.g., HTMLElement objects serialize
    // as objects with numeric-indexed children that clobber form field data).
    const triggerElement = params._targetElement;
    const skipLoading = params._skipLoading;

    // Build clean server params (strip underscore-prefixed internal properties)
    const serverParams = {};
    for (const key of Object.keys(params)) {
        if (key === '_targetElement' || key === '_optimisticUpdateId' || key === '_skipLoading' || key === '_djTargetSelector') {
            continue;
        }
        serverParams[key] = params[key];
    }

    // DEP-002: Apply optimistic UI rule if one exists for this event
    const optimisticRules = window.djust._optimisticRules || {};
    const optimisticRule = optimisticRules[eventName];
    if (optimisticRule && triggerElement) {
        try {
            // Interpolate {component_id} and {value} into the target selector
            let selector = optimisticRule.target || '';
            selector = selector.replace('{component_id}', serverParams.component_id || '');
            selector = selector.replace('{value}', serverParams.value || '');

            // Find the target element relative to the component container
            const container = triggerElement.closest('[data-component-id]') || document;
            const target = selector ? container.querySelector(selector) || triggerElement.closest(selector) : triggerElement;

            if (target) {
                const action = optimisticRule.action;
                if (action === 'toggle_class' && optimisticRule['class']) {
                    target.classList.toggle(optimisticRule['class']);
                } else if (action === 'toggle_attr' && optimisticRule.attr) {
                    if (target.hasAttribute(optimisticRule.attr)) {
                        target.removeAttribute(optimisticRule.attr);
                    } else {
                        target.setAttribute(optimisticRule.attr, '');
                    }
                } else if (action === 'set_attr' && optimisticRule.attr) {
                    target.setAttribute(optimisticRule.attr, optimisticRule.value || '');
                }
                if (globalThis.djustDebug) console.log('[LiveView:optimistic] Applied rule:', eventName, action);
            }
        } catch (e) {
            if (globalThis.djustDebug) console.warn('[LiveView:optimistic] Rule failed:', e);
        }
    }

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
        if (!skipLoading) globalLoadingManager.startLoading(eventName, triggerElement);

        // Apply cached patches
        if (cached.patches && cached.patches.length > 0) {
            applyPatches(cached.patches);
            reinitAfterDOMUpdate();
        }

        if (!skipLoading) globalLoadingManager.stopLoading(eventName, triggerElement);
        return;
    }

    // Cache miss - need to fetch from server
    if (globalThis.djustDebug && cacheConfig.has(eventName)) {
        console.log(`[LiveView:cache] Cache miss: ${cacheKey}`);
    }

    if (!skipLoading) globalLoadingManager.startLoading(eventName, triggerElement);

    // Prepare server-bound params (already stripped of client-only properties)
    let paramsToSend = serverParams;

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
        paramsToSend = { ...serverParams, _cacheRequestId: cacheRequestId };
    }

    // Try WebSocket first
    if (liveViewWS && liveViewWS.sendEvent(eventName, paramsToSend, triggerElement)) {
        return;
    }

    // Fallback to HTTP
    if (globalThis.djustDebug) console.log('[LiveView] WebSocket unavailable, falling back to HTTP');

    try {
        // Read CSRF token from hidden input first, fall back to cookie.
        // Skip the hidden input if its value is empty — the Rust engine
        // renders "" when no csrf_token is in the template context (#696).
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
            || document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1]
            || '';
        const response = await fetch(window.location.href, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Djust-Event': eventName
            },
            body: JSON.stringify(paramsToSend)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        handleServerResponse(data, eventName, triggerElement);

    } catch (error) {
        console.error('[LiveView] HTTP fallback failed:', error);
        globalLoadingManager.stopLoading(eventName, triggerElement);
    }
}
window.djust.handleEvent = handleEvent;

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
 * Save the current focus state (active element, selection, scroll position).
 * Call before DOM mutations that may destroy focus. Pairs with restoreFocusState().
 *
 * @returns {Object|null} Saved focus state, or null if no form element is focused.
 */
function saveFocusState() {
    const active = document.activeElement;
    if (!active || active === document.body || active === document.documentElement) {
        return null;
    }

    // Only save state for form elements and contenteditable
    const isFormEl = (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT');
    const isEditable = active.isContentEditable;
    if (!isFormEl && !isEditable) {
        return null;
    }

    // Skip saving during broadcast updates — remote content should take effect.
    if (_isBroadcastUpdate) {
        return null;
    }

    const state = { tag: active.tagName };

    // Build a matching key: prefer id, then name, then dj-id, then positional index
    if (active.id) {
        state.findBy = 'id';
        state.key = active.id;
    } else if (active.name) {
        state.findBy = 'name';
        state.key = active.name;
    } else if (active.getAttribute && active.getAttribute('dj-id')) {
        state.findBy = 'dj-id';
        state.key = active.getAttribute('dj-id');
    } else {
        // Positional: index among same-tag siblings in the nearest dj-view
        state.findBy = 'index';
        const root = active.closest('[dj-view]') || document.body;
        const siblings = root.querySelectorAll(active.tagName.toLowerCase());
        state.key = Array.from(siblings).indexOf(active);
    }

    // Save value and selection state
    if (active.tagName === 'TEXTAREA' || (active.tagName === 'INPUT' && !['checkbox', 'radio'].includes(active.type))) {
        state.selStart = active.selectionStart;
        state.selEnd = active.selectionEnd;
        state.scrollTop = active.scrollTop;
        state.scrollLeft = active.scrollLeft;
    }

    return state;
}

/**
 * Restore focus state saved by saveFocusState().
 * Re-finds the element in the DOM (it may have been replaced) and restores
 * focus, selection range, and scroll position.
 *
 * @param {Object|null} state - Saved state from saveFocusState()
 */
function restoreFocusState(state) {
    if (!state) return;

    let el = null;
    if (state.findBy === 'id') {
        el = document.getElementById(state.key);
    } else if (state.findBy === 'name') {
        el = document.querySelector(`[name="${CSS.escape(state.key)}"]`);
    } else if (state.findBy === 'dj-id') {
        el = document.querySelector(`[dj-id="${CSS.escape(state.key)}"]`);
    } else {
        // Positional fallback
        const root = document.querySelector('[dj-view]') || document.body;
        const candidates = root.querySelectorAll(state.tag.toLowerCase());
        el = candidates[state.key] || null;
    }

    if (!el) return;

    // Re-focus the element (won't re-trigger focus event if already focused)
    if (document.activeElement !== el) {
        el.focus({ preventScroll: true });
    }

    // Restore selection range for text inputs/textareas
    if (state.selStart !== undefined && typeof el.setSelectionRange === 'function') {
        try {
            el.setSelectionRange(state.selStart, state.selEnd);
        } catch (e) {
            // setSelectionRange throws on some input types (email, number)
        }
    }

    // Restore scroll position within the element
    if (state.scrollTop !== undefined) {
        el.scrollTop = state.scrollTop;
        el.scrollLeft = state.scrollLeft;
    }
}

/**
 * Resolve a DOM node using ID-based lookup (primary) or path traversal (fallback).
 *
 * Resolution strategy:
 * 1. If djustId is provided, try querySelector('[dj-id="..."]') - O(1), reliable
 * 2. Fall back to index-based path traversal
 *
 * @param {Array<number>} path - Index-based path (fallback)
 * @param {string|null} djustId - Compact djust ID for direct lookup (e.g., "1a")
 * @returns {Node|null} - Found node or null
 */
function getNodeByPath(path, djustId = null) {
    // Strategy 1: ID-based resolution (fast, reliable)
    if (djustId) {
        const byId = document.querySelector(`[dj-id="${CSS.escape(djustId)}"]`);
        if (byId) {
            return byId;
        }
        // ID not found - fall through to path-based
        if (globalThis.djustDebug || window.DEBUG_MODE) {
            // Log without user data to avoid log injection
            if (globalThis.djustDebug) console.log('[LiveView] ID lookup failed, trying path fallback');
        }
    }

    // Strategy 2: Index-based path traversal (fallback)
    let node = getLiveViewRoot();

    if (path.length === 0) {
        return node;
    }

    for (let i = 0; i < path.length; i++) {
        const index = path[i]; // eslint-disable-line security/detect-object-injection -- path is a server-provided integer array
        const children = Array.from(node.childNodes).filter(child => {
            if (child.nodeType === Node.ELEMENT_NODE) return true;
            if (child.nodeType === Node.TEXT_NODE) {
                // Preserve non-breaking spaces (\u00A0) as significant, matching Rust VDOM parser.
                // Only filter out ASCII whitespace-only text nodes (space, tab, newline, CR).
                // JS \s includes \u00A0, so we use an explicit ASCII whitespace pattern instead.
                return (/[^ \t\n\r\f]/.test(child.textContent));
            }
            // Only include <!--dj-if--> placeholder comments — the Rust VDOM
            // parser preserves these for diffing stability (#559) but drops
            // all other HTML comments. Regular comments (<!-- Hero Section -->
            // etc.) must be excluded to keep path indices aligned.
            if (child.nodeType === Node.COMMENT_NODE) {
                return child.textContent.trim() === 'dj-if';
            }
            return false;
        });

        if (index >= children.length) {
            if (globalThis.djustDebug || window.DEBUG_MODE) {
                // Explicit number coercion for safe logging
                const safeIndex = Number(index) || 0;
                const safeLen = Number(children.length) || 0;
                const parentTag = node.tagName || '#text';
                const parentId = node.getAttribute ? (node.getAttribute('dj-id') || node.id || '') : '';
                const parentDesc = parentId ? `${parentTag}#${parentId}` : parentTag;
                console.warn(`[LiveView] Path traversal failed at index ${safeIndex}, only ${safeLen} children (parent: ${parentDesc}). The DOM may have been modified by third-party JS, or a {% if %} block changed the node count.`);
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
    // Handle comment nodes — Rust emits <!--dj-if--> placeholders for
    // {% if %} blocks that evaluate to False (#559).
    if (vnode.tag === '#comment') {
        return document.createComment(vnode.text || '');
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
            // Set all attributes on the element (including dj-* attributes).
            // Event listeners for dj-* attributes are attached by bindLiveViewEvents()
            // after patches are applied, which already uses _markHandlerBound to
            // prevent double-binding on subsequent calls.
            if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                elem.value = value;
            } else if (key === 'checked' && elem.tagName === 'INPUT') {
                elem.checked = true;
            } else if (key === 'selected' && elem.tagName === 'OPTION') {
                elem.selected = true;
            }
            elem.setAttribute(key, value);

            // Note: dj-* event listeners are attached by bindLiveViewEvents() after
            // patch application. Do NOT pre-mark elements here — that would prevent
            // bindLiveViewEvents() from ever attaching the listener.
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
/**
 * Flag set by handleServerResponse when applying broadcast patches.
 * When true, preserveFormValues skips saving/restoring the focused
 * element so remote content (from other users) takes effect.
 */
let _isBroadcastUpdate = false;

/**
 * Preserve form values across innerHTML replacement.
 *
 * innerHTML destroys the DOM, creating new elements. For the focused
 * element we save and restore the user's in-progress value + cursor.
 * For all textareas, we sync .value from textContent after replacement
 * (innerHTML only sets the DOM attribute, not the JS property).
 *
 * Matching strategy: id → name → positional index within container.
 */
function preserveFormValues(container, updateFn) {
    const active = document.activeElement;
    let saved = null;

    // Skip saving focused element for broadcast (remote) updates —
    // the server content from another user should take effect.
    if (_isBroadcastUpdate) {
        updateFn();
        container.querySelectorAll('textarea').forEach(el => {
            el.value = el.textContent || '';
        });
        return;
    }

    // Only save the focused form element (user is actively editing)
    if (active && container.contains(active) &&
        (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT' || active.tagName === 'SELECT')) {
        saved = { tag: active.tagName.toLowerCase() };
        // Build a matching key: prefer id, then name, then positional index
        if (active.id) {
            saved.findBy = 'id';
            saved.key = active.id;
        } else if (active.name) {
            saved.findBy = 'name';
            saved.key = active.name;
        } else {
            // Positional: find index among same-tag siblings in container
            saved.findBy = 'index';
            const siblings = container.querySelectorAll(active.tagName.toLowerCase());
            saved.key = Array.from(siblings).indexOf(active);
        }
        if (active.tagName === 'TEXTAREA') {
            saved.value = active.value;
            saved.selStart = active.selectionStart;
            saved.selEnd = active.selectionEnd;
        } else if (active.type === 'checkbox' || active.type === 'radio') {
            saved.checked = active.checked;
        } else {
            saved.value = active.value;
        }
    }

    updateFn();

    // Sync all textarea .value from textContent (innerHTML doesn't set .value)
    container.querySelectorAll('textarea').forEach(el => {
        el.value = el.textContent || '';
    });

    // Restore the focused element's value
    if (saved) {
        let el = null;
        if (saved.findBy === 'id') {
            el = container.querySelector(`#${CSS.escape(saved.key)}`);
        } else if (saved.findBy === 'name') {
            el = container.querySelector(`[name="${CSS.escape(saved.key)}"]`);
        } else {
            // Positional fallback
            const candidates = container.querySelectorAll(saved.tag);
            el = candidates[saved.key] || null;
        }
        if (el) {
            if (saved.tag === 'textarea') {
                el.value = saved.value;
                try { el.setSelectionRange(saved.selStart, saved.selEnd); } catch (e) { /* */ }
                el.focus();
            } else if (el.type === 'checkbox' || el.type === 'radio') {
                el.checked = saved.checked;
            } else if (saved.value !== undefined) {
                el.value = saved.value;
            }
        }
    }
}

/**
 * Morph existing DOM children to match desired DOM children.
 * Preserves existing elements (and their event listeners) where possible.
 *
 * Matching per child:
 *   1. If desired child has an id → find existing child with same id (keyed)
 *   2. If current existing child has same tag and neither has an id → reuse
 *   3. Otherwise → clone desired child and insert
 *
 * Unmatched existing children are removed after the walk.
 *
 * @param {Element} existing - Current live DOM parent
 * @param {Element} desired  - Target DOM parent (parsed from server HTML)
 */
function morphChildren(existing, desired) {
    const existingNodes = Array.from(existing.childNodes);
    const desiredNodes = Array.from(desired.childNodes);

    // Index existing elements by id for O(1) keyed lookup
    const existingById = new Map();
    for (const node of existingNodes) {
        if (node.nodeType === Node.ELEMENT_NODE && node.id) {
            existingById.set(node.id, node);
        }
    }

    const matched = new Set();
    let eIdx = 0;

    for (const dNode of desiredNodes) {
        // Advance past already-matched existing nodes
        while (eIdx < existingNodes.length && matched.has(existingNodes[eIdx])) {
            eIdx++;
        }
        const eNode = eIdx < existingNodes.length ? existingNodes[eIdx] : null;

        // --- Text node ---
        if (dNode.nodeType === Node.TEXT_NODE) {
            if (eNode && eNode.nodeType === Node.TEXT_NODE && !matched.has(eNode)) {
                if (eNode.textContent !== dNode.textContent) {
                    eNode.textContent = dNode.textContent;
                }
                matched.add(eNode);
                eIdx++;
            } else {
                existing.insertBefore(document.createTextNode(dNode.textContent), eNode);
            }
            continue;
        }

        // --- Comment node ---
        if (dNode.nodeType === Node.COMMENT_NODE) {
            if (eNode && eNode.nodeType === Node.COMMENT_NODE && !matched.has(eNode)) {
                if (eNode.textContent !== dNode.textContent) {
                    eNode.textContent = dNode.textContent;
                }
                matched.add(eNode);
                eIdx++;
            } else {
                existing.insertBefore(document.createComment(dNode.textContent), eNode);
            }
            continue;
        }

        // --- Element node ---
        if (dNode.nodeType !== Node.ELEMENT_NODE) {
            continue;
        }

        const dId = dNode.id || null;

        // Strategy 1: Match by id (keyed element)
        if (dId && existingById.has(dId)) {
            const match = existingById.get(dId);
            existingById.delete(dId);
            matched.add(match);
            if (match !== eNode) {
                // Move keyed element into correct position
                existing.insertBefore(match, eNode);
            } else {
                eIdx++;
            }
            morphElement(match, dNode);
            continue;
        }

        // Strategy 2: Same tag, no ids on either side — reuse in place
        if (eNode && eNode.nodeType === Node.ELEMENT_NODE &&
            eNode.tagName === dNode.tagName &&
            !dId && !eNode.id && !matched.has(eNode)) {
            matched.add(eNode);
            morphElement(eNode, dNode);
            eIdx++;
            continue;
        }

        // Strategy 3: No match — clone desired child and insert
        existing.insertBefore(dNode.cloneNode(true), eNode);
    }

    // Remove unmatched existing children
    for (const node of existingNodes) {
        if (!matched.has(node) && node.parentNode === existing) {
            existing.removeChild(node);
        }
    }
}

/**
 * Morph a single element to match a desired element.
 * Updates attributes and recurses into children.
 * Preserves event listeners on the existing element.
 *
 * @param {Element} existing - Current live DOM element
 * @param {Element} desired  - Target element to match
 */
function morphElement(existing, desired) {
    // Tag mismatch — replace entirely
    if (existing.tagName !== desired.tagName) {
        // Clean up poll timers before replacing (prevents orphaned intervals)
        if (existing._djustPollIntervalId) {
            clearInterval(existing._djustPollIntervalId);
            if (existing._djustPollVisibilityHandler) {
                document.removeEventListener('visibilitychange', existing._djustPollVisibilityHandler);
            }
        }
        // Clean up scoped (window/document) listeners before replacing
        _cleanupScopedListeners(existing);
        existing.parentNode.replaceChild(desired.cloneNode(true), existing);
        return;
    }

    // dj-update="ignore" — skip entirely
    if (existing.getAttribute('dj-update') === 'ignore') {
        return;
    }

    // --- Sync attributes ---
    // Remove attributes not present in desired.
    // Exception: canvas width/height are set by scripts (e.g. Chart.js) and must
    // not be removed — doing so resets the canvas context and clears drawn content.
    const isCanvas = existing.tagName === 'CANVAS';
    for (let i = existing.attributes.length - 1; i >= 0; i--) {
        const name = existing.attributes[i].name;
        if (!desired.hasAttribute(name)) {
            if (isCanvas && (name === 'width' || name === 'height')) continue;
            existing.removeAttribute(name);
        }
    }
    // Set/update attributes from desired
    for (const attr of desired.attributes) {
        if (existing.getAttribute(attr.name) !== attr.value) {
            existing.setAttribute(attr.name, attr.value);
        }
    }

    // --- Form element value sync ---
    const isFocused = document.activeElement === existing;
    const skipValue = isFocused && !_isBroadcastUpdate;

    if (existing.tagName === 'INPUT' && !skipValue) {
        if (existing.type === 'checkbox' || existing.type === 'radio') {
            existing.checked = desired.checked;
        } else {
            const newVal = desired.value || desired.getAttribute('value') || '';
            if (existing.value !== newVal) {
                existing.value = newVal;
            }
        }
    } else if (existing.tagName === 'SELECT' && !skipValue) {
        const newVal = desired.value;
        if (existing.value !== newVal) {
            existing.value = newVal;
        }
    }

    // --- Recurse into children ---
    // dj-update="append"/"prepend" accumulate children server-side;
    // morphing would remove them, so skip child recursion
    const updateMode = existing.getAttribute('dj-update');
    if (updateMode === 'append' || updateMode === 'prepend') {
        return;
    }

    morphChildren(existing, desired);

    // Sync textarea .value from textContent after children are morphed
    // (.value and .textContent diverge after initial render)
    if (existing.tagName === 'TEXTAREA' && !skipValue) {
        existing.value = existing.textContent || '';
    }
}

function applyDjUpdateElements(existingRoot, newRoot) {
    // Find all elements with dj-update attribute in the new content
    const djUpdateElements = newRoot.querySelectorAll('[dj-update]');

    if (djUpdateElements.length === 0) {
        // No dj-update elements — morph to preserve event listeners
        morphChildren(existingRoot, newRoot);
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
                        if (globalThis.djustDebug) {
                            console.log(`[LiveView:dj-update] Appended #${newChild.id} to #${elementId}`);
                        }
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
                        if (globalThis.djustDebug) {
                            console.log(`[LiveView:dj-update] Prepended #${newChild.id} to #${elementId}`);
                        }
                    }
                }
                break;
            }

            case 'ignore':
                // Don't update this element at all
                if (globalThis.djustDebug) {
                    console.log(`[LiveView:dj-update] Ignoring #${elementId}`);
                }
                break;

            case 'replace':
            default:
                // Morph to preserve event listeners
                morphElement(existingElement, newElement);
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
                    // Morph to preserve event listeners
                    morphElement(existing, newChild);
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
 * Stamp dj-id attributes from server HTML onto existing pre-rendered DOM.
 * This avoids replacing innerHTML (which destroys whitespace in code blocks).
 * Walks both trees in parallel and copies dj-id from server elements to DOM elements.
 * Note: serverHtml is trusted (comes from our own WebSocket mount response).
 */
function _stampDjIds(serverHtml, container) {
    if (!container) {
        container = document.querySelector('[dj-view]') ||
                    document.querySelector('[dj-root]');
    }
    if (!container) return;

    const parser = new DOMParser();
    // codeql[js/xss] -- serverHtml is rendered by the trusted Django/Rust template engine
    const doc = parser.parseFromString('<div>' + serverHtml + '</div>', 'text/html');
    const serverRoot = doc.body.firstChild;

    function stampRecursive(domNode, serverNode) {
        if (!domNode || !serverNode) return;
        if (serverNode.nodeType !== Node.ELEMENT_NODE || domNode.nodeType !== Node.ELEMENT_NODE) return;

        // Bail out if structure diverges (e.g. browser extension injected elements)
        if (domNode.tagName !== serverNode.tagName) return;

        const djId = serverNode.getAttribute('dj-id');
        if (djId) {
            domNode.setAttribute('dj-id', djId);
        }
        // Also stamp data-dj-src (template source mapping) if present
        const djSrc = serverNode.getAttribute('data-dj-src');
        if (djSrc) {
            domNode.setAttribute('data-dj-src', djSrc);
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
            // Preserve non-breaking spaces (\u00A0) as significant, matching Rust VDOM parser.
            // Only filter out ASCII whitespace-only text nodes.
            return (/[^ \t\n\r\f]/.test(child.textContent));
        }
        // Include comment nodes — the Rust VDOM parser preserves <!--dj-if-->
        // placeholders and counts them in child indices (#559).
        if (child.nodeType === Node.COMMENT_NODE) return true;
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
window.djust._getNodeByPath = getNodeByPath;
window.djust.createNodeFromVNode = createNodeFromVNode;
window.djust.preserveFormValues = preserveFormValues;
window.djust.saveFocusState = saveFocusState;
window.djust.restoreFocusState = restoreFocusState;
window.djust.morphChildren = morphChildren;
window.djust.morphElement = morphElement;

/**
 * Group patches by their parent path for batching.
 *
 * Child operations (InsertChild, RemoveChild, MoveChild) use the full path
 * as the parent key because the path points to the parent container.
 * Node-targeting operations (SetAttribute, SetText, etc.) use slice(0,-1)
 * because the path points to the node itself, and the parent is one level up.
 */
const CHILD_OPS = new Set(['InsertChild', 'RemoveChild', 'MoveChild']);
function groupPatchesByParent(patches) {
    const groups = new Map(); // Use Map to avoid prototype pollution
    for (const patch of patches) {
        const parentPath = CHILD_OPS.has(patch.type)
            ? patch.path.join('/')
            : patch.path.slice(0, -1).join('/');
        if (!groups.has(parentPath)) {
            groups.set(parentPath, []);
        }
        groups.get(parentPath).push(patch);
    }
    return groups;
}
window.djust._groupPatchesByParent = groupPatchesByParent;

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
        // Check if this insert is consecutive with the previous one AND targets same parent
        if (inserts[i].index === inserts[i - 1].index + 1 && inserts[i].d === inserts[i - 1].d) {
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
window.djust._groupConsecutiveInserts = groupConsecutiveInserts;

/**
 * Sort patches in 4-phase order for correct DOM mutation sequencing:
 * Phase 0: RemoveChild (descending index within same parent)
 * Phase 1: MoveChild
 * Phase 2: InsertChild
 * Phase 3: SetText, SetAttribute, and other node-targeting patches
 */
function _sortPatches(patches) {
    function patchPhase(p) {
        switch (p.type) {
            case 'RemoveChild': return 0;
            case 'MoveChild':   return 1;
            case 'InsertChild': return 2;
            default:            return 3;
        }
    }
    patches.sort(function(a, b) {
        const phaseA = patchPhase(a);
        const phaseB = patchPhase(b);
        if (phaseA !== phaseB) return phaseA - phaseB;
        // Within RemoveChild phase, sort by descending index per parent
        if (phaseA === 0) {
            const pA = JSON.stringify(a.path);
            const pB = JSON.stringify(b.path);
            if (pA === pB) return b.index - a.index;
        }
        return 0;
    });
    return patches;
}
window.djust._sortPatches = _sortPatches;

/**
 * Apply a single patch operation.
 *
 * Patches include:
 * - `path`: Index-based path (fallback)
 * - `d`: Compact djust ID for O(1) querySelector lookup
 */
function applySinglePatch(patch) {
    // Use ID-based resolution (d field) with path as fallback
    const node = getNodeByPath(patch.path, patch.d);
    if (!node) {
        // Sanitize for logging (patches come from trusted server, but log defensively)
        const safePath = Array.isArray(patch.path) ? patch.path.map(Number).join('/') : 'invalid';
        const patchType = String(patch.type || 'Unknown');
        console.warn('[LiveView] Patch failed (%s): node not found at path=%s, dj-id=%s', patchType, safePath, sanitizeIdForLog(patch.d));
        if (window.DEBUG_MODE) {
            console.groupCollapsed('[LiveView] Patch detail (%s)', patchType);
            if (globalThis.djustDebug) console.log('[LiveView] Full patch object:', JSON.stringify(patch));
            if (globalThis.djustDebug) console.log('[LiveView] Suggested causes:\n  - The DOM may have been modified by third-party JS\n  - A template {% if %} block may have changed the node count\n  - A conditional rendering path produced a different DOM structure');
            console.groupEnd();
        }
        return false;
    }

    try {
        switch (patch.type) {
            case 'Replace':
                // Clean up poll timers before replacing (prevents orphaned intervals)
                if (node._djustPollIntervalId) {
                    clearInterval(node._djustPollIntervalId);
                    if (node._djustPollVisibilityHandler) {
                        document.removeEventListener('visibilitychange', node._djustPollVisibilityHandler);
                    }
                }
                const newNode = createNodeFromVNode(patch.node, isInSvgContext(node.parentNode));
                node.parentNode.replaceChild(newNode, node);
                break;

            case 'SetText': {
                const safeText = String(patch.text);
                node.textContent = safeText;
                // If this is a text node inside a textarea, also update the textarea's .value
                // (textContent alone doesn't update what's displayed in the textarea)
                if (node.parentNode && node.parentNode.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node.parentNode) {
                        node.parentNode.value = safeText;
                    }
                }
                break;
            }

            case 'SetAttr': {
                // Guard: element-only methods (setAttribute) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                // Sanitize key to prevent prototype pollution
                const attrKey = String(patch.key);
                if (UNSAFE_KEYS.includes(attrKey)) break;
                const attrVal = String(patch.value != null ? patch.value : '');
                if (attrKey === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                    if (document.activeElement !== node) {
                        node.value = attrVal;
                    }
                    node.setAttribute(attrKey, attrVal);
                } else if (attrKey === 'checked' && node.tagName === 'INPUT') {
                    node.checked = true;
                    node.setAttribute('checked', '');
                } else if (attrKey === 'selected' && node.tagName === 'OPTION') {
                    node.selected = true;
                    node.setAttribute('selected', '');
                } else {
                    node.setAttribute(attrKey, attrVal);
                }
                break;
            }

            case 'RemoveAttr': {
                // Guard: element-only methods (removeAttribute) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                const removeKey = String(patch.key);
                // Never remove dj-* event handler attributes — defense in depth
                // against VDOM path mismatches from conditional rendering.
                // Also preserve data-dj-src (template source mapping).
                if (removeKey.startsWith('dj-') || removeKey === 'data-dj-src') {
                    break;
                }
                if (UNSAFE_KEYS.includes(removeKey)) break;
                if (removeKey === 'checked' && node.tagName === 'INPUT') {
                    node.checked = false;
                } else if (removeKey === 'selected' && node.tagName === 'OPTION') {
                    node.selected = false;
                }
                node.removeAttribute(removeKey);
                break;
            }

            case 'InsertChild': {
                // Guard: element-only methods (querySelector, tagName) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                const newChild = createNodeFromVNode(patch.node, isInSvgContext(node));
                // Guard: <select> only accepts <option>/<optgroup> as direct children.
                // When an adjacent {% if %} block expands, the server may resolve the
                // parent path to the <select> element rather than the surrounding
                // container, causing new sibling nodes to be inserted inside the
                // <select>.  Detect this mismatch and redirect the insert to the
                // correct parent so the new node becomes a sibling of <select>.
                let insertTarget = node;
                let insertRefChild = null;
                const isSelectNode = node.nodeType === Node.ELEMENT_NODE && node.tagName === 'SELECT';
                const newChildIsOption = newChild.nodeType === Node.ELEMENT_NODE &&
                    (newChild.tagName === 'OPTION' || newChild.tagName === 'OPTGROUP');
                if (isSelectNode && !newChildIsOption) {
                    // Redirect: insert as sibling of <select> instead of inside it
                    insertTarget = node.parentNode;
                    insertRefChild = node.nextSibling;
                    if (globalThis.djustDebug) {
                        console.log('[LiveView] InsertChild redirected: non-option child into SELECT parent');
                    }
                } else {
                    if (patch.ref_d) {
                        // ID-based resolution: find sibling by dj-id (resilient to index shifts)
                        const escaped = CSS.escape(patch.ref_d);
                        insertRefChild = node.querySelector(`:scope > [dj-id="${escaped}"]`);
                    }
                    if (!insertRefChild) {
                        // Fallback: index-based
                        const children = getSignificantChildren(node);
                        insertRefChild = children[patch.index] || null;
                    }
                }
                if (insertRefChild) {
                    insertTarget.insertBefore(newChild, insertRefChild);
                } else {
                    insertTarget.appendChild(newChild);
                }
                // If inserting a text node into a textarea, also update its .value
                if (newChild.nodeType === Node.TEXT_NODE && node.tagName === 'TEXTAREA') {
                    if (document.activeElement !== node) {
                        node.value = String(newChild.textContent || '');
                    }
                }
                break;
            }

            case 'RemoveChild': {
                // Guard: element-only methods (querySelector, tagName) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                let child = null;
                if (patch.child_d) {
                    // ID-based resolution: find child by dj-id (resilient to index shifts)
                    const escaped = CSS.escape(patch.child_d);
                    child = node.querySelector(`:scope > [dj-id="${escaped}"]`);
                }
                if (!child) {
                    // Fallback: index-based
                    const children = getSignificantChildren(node);
                    child = children[patch.index] || null;
                }
                if (child) {
                    const wasTextNode = child.nodeType === Node.TEXT_NODE;
                    const parentTag = node.tagName;
                    node.removeChild(child);
                    // If removing a text node from a textarea, also clear its .value
                    // (removing textContent alone doesn't update what's displayed)
                    if (wasTextNode && parentTag === 'TEXTAREA' && document.activeElement !== node) {
                        node.value = '';
                    }
                }
                break;
            }

            case 'MoveChild': {
                // Guard: element-only methods (querySelector) don't exist on text/comment nodes (#622)
                if (node.nodeType !== 1) {
                    if (globalThis.djustDebug) console.log('[LiveView] Patch %s targets non-element (nodeType=%d), skipping', patch.type, node.nodeType);
                    return false;
                }
                let child;
                if (patch.child_d) {
                    // ID-based resolution: find direct child by dj-id (resilient to index shifts)
                    const escaped = CSS.escape(patch.child_d);
                    child = node.querySelector(`:scope > [dj-id="${escaped}"]`);
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
function applyPatches(patches) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Save focus state before any DOM mutations (#559 follow-up: focus preservation)
    const focusState = saveFocusState();

    // Sort patches in 4-phase order for correct DOM mutation sequencing
    _sortPatches(patches);

    // For small patch sets, apply directly without batching overhead
    if (patches.length <= 10) {
        let failedCount = 0;
        const failedIndices = [];
        for (let _pi = 0; _pi < patches.length; _pi++) {
            if (!applySinglePatch(patches[_pi])) {
                failedCount++;
                failedIndices.push(_pi);
            }
        }
        if (failedCount > 0) {
            console.error(`[LiveView] ${failedCount}/${patches.length} patches failed (indices: ${failedIndices.join(', ')})`);
            // Still handle autofocus even when some patches failed (#617)
            if (!focusState || !focusState.id) {
                const autoFocusEl = document.querySelector('[dj-view] [autofocus]');
                if (autoFocusEl && document.activeElement !== autoFocusEl) {
                    autoFocusEl.focus();
                }
            }
            restoreFocusState(focusState);
            return false;
        }
        // Update hooks and model bindings after DOM patches
        updateHooks();
        bindModelElements();
        // Handle autofocus on dynamically inserted elements (#617)
        // Browser only honors autofocus on initial page load, so we
        // manually focus the first element with autofocus after a patch.
        if (!focusState || !focusState.id) {
            const autoFocusEl = document.querySelector('[dj-view] [autofocus]');
            if (autoFocusEl && document.activeElement !== autoFocusEl) {
                autoFocusEl.focus();
            }
        }
        restoreFocusState(focusState);
        return true;
    }

    // For larger patch sets, use batching
    let failedCount = 0;
    let successCount = 0;

    // Group patches by parent for potential batching
    const patchGroups = groupPatchesByParent(patches);

    for (const [, group] of patchGroups) {
        // Phase order within a group MUST match the top-level phase order:
        // RemoveChild → MoveChild → InsertChild → other.
        //
        // Previously the batching code below ran InsertChild patches (via
        // DocumentFragment) BEFORE iterating `group` for the RemoveChild
        // patches — violating phase order. That breaks when a comment/text
        // child without a dj-id needs removal: the index-based fallback
        // resolves to the just-inserted content instead of the old child,
        // and the wrong node gets deleted.  See regression fixtures for
        // NYC Claims tab switches (#641).
        //
        // Fix: apply all non-Insert patches individually FIRST, then batch
        // the consecutive inserts, then apply any remaining inserts that
        // were too small to batch.  _sortPatches has already sorted the
        // removes within the group by descending index.
        const nonInsertPatches = [];
        const insertPatches = [];
        for (const patch of group) {
            if (patch.type === 'InsertChild') insertPatches.push(patch);
            else nonInsertPatches.push(patch);
        }

        // 1. Apply non-insert patches (RemoveChild, MoveChild, SetAttr, etc.)
        //    in their existing sorted order.  RemoveChild patches are
        //    descending-index-sorted by _sortPatches, so they're safe to
        //    apply sequentially without index drift.
        for (const patch of nonInsertPatches) {
            if (applySinglePatch(patch)) {
                successCount++;
            } else {
                failedCount++;
            }
        }

        // 2. Batch consecutive inserts via DocumentFragment where possible.
        //    At this point the DOM is in the "post-remove" state, so index
        //    fallback for ref_d=None inserts lines up with what the server
        //    computed against the new VDOM.
        const batchedInserts = new Set();
        if (insertPatches.length >= 3) {
            const consecutiveGroups = groupConsecutiveInserts(insertPatches);

            for (const consecutiveGroup of consecutiveGroups) {
                if (consecutiveGroup.length < 3) continue;

                const firstPatch = consecutiveGroup[0];
                const parentNode = getNodeByPath(firstPatch.path, firstPatch.d);

                if (parentNode) {
                    try {
                        const fragment = document.createDocumentFragment();
                        const svgContext = isInSvgContext(parentNode);
                        for (const patch of consecutiveGroup) {
                            const newChild = createNodeFromVNode(patch.node, svgContext);
                            fragment.appendChild(newChild);
                            successCount++;
                            batchedInserts.add(patch);
                        }

                        const children = getSignificantChildren(parentNode);
                        const firstIndex = consecutiveGroup[0].index;
                        const refChild = children[firstIndex];

                        if (refChild) {
                            parentNode.insertBefore(fragment, refChild);
                        } else {
                            parentNode.appendChild(fragment);
                        }
                    } catch (error) {
                        console.error('[LiveView] Batch insert failed, falling back to individual patches:', error.message);
                        successCount -= consecutiveGroup.length;  // undo count
                        for (const patch of consecutiveGroup) batchedInserts.delete(patch);
                    }
                }
            }
        }

        // 3. Apply any insert patches that weren't batched (non-consecutive
        //    groups or group size < 3) individually.
        for (const patch of insertPatches) {
            if (batchedInserts.has(patch)) continue;
            if (applySinglePatch(patch)) {
                successCount++;
            } else {
                failedCount++;
            }
        }
    }

    if (failedCount > 0) {
        console.error(`[LiveView] ${failedCount}/${patches.length} patches failed (${successCount} succeeded)`);
        // Still handle autofocus even when some patches failed (#617)
        if (!focusState || !focusState.id) {
            const autoFocusEl = document.querySelector('[dj-view] [autofocus]');
            if (autoFocusEl && document.activeElement !== autoFocusEl) {
                autoFocusEl.focus();
            }
        }
        restoreFocusState(focusState);
        return false;
    }

    // Update hooks and model bindings after DOM patches
    updateHooks();
    bindModelElements();

    // Handle autofocus on dynamically inserted elements (#617)
    // Browser only honors autofocus on initial page load, so we
    // manually focus the first element with autofocus after a patch.
    if (!focusState || !focusState.id) {
        const autoFocusEl = document.querySelector('[dj-view] [autofocus]');
        if (autoFocusEl && document.activeElement !== autoFocusEl) {
            autoFocusEl.focus();
        }
    }

    restoreFocusState(focusState);
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
 *   <div dj-view="my_view" dj-lazy>
 *     <!-- Content loads when element scrolls into view -->
 *   </div>
 *
 *   <div dj-view="my_view" dj-lazy="click">
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
        const lazyMode = element.getAttribute('dj-lazy') || 'viewport';

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
        const elementId = element.id || element.getAttribute('dj-view');

        // Prevent double hydration
        if (this.hydratedElements.has(elementId)) {
            return;
        }
        this.hydratedElements.add(elementId);

        const viewPath = element.getAttribute('dj-view');
        if (!viewPath) {
            console.warn('[LiveView:lazy] Element missing dj-view attribute', element);
            return;
        }

        if (globalThis.djustDebug) console.log(`[LiveView:lazy] Hydrating: ${viewPath}`);

        // Ensure WebSocket is connected (skip in HTTP-only mode)
        if (window.DJUST_USE_WEBSOCKET === false) {
            if (globalThis.djustDebug) console.log('[LiveView:lazy] HTTP-only mode — skipping WebSocket for lazy element');
            return;
        }
        if (!liveViewWS || !liveViewWS.enabled) {
            liveViewWS = new LiveViewWebSocket();
            window.djust.liveViewInstance = liveViewWS;
            liveViewWS.connect();
        }

        // Wait for WebSocket connection then mount
        if (isWSConnected()) {
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
        if (globalThis.djustDebug) console.log(`[LiveView:lazy] Processing ${this.pendingMounts.length} pending mounts`);
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
            if (globalThis.djustDebug) console.log('[LiveView:lazy] Using pre-rendered content');
            liveViewWS.skipMountHtml = true;
        }

        // Pass URL query params
        const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
        liveViewWS.mount(viewPath, urlParams);

        // Remove lazy attribute to indicate hydration complete
        element.removeAttribute('dj-lazy');
        element.setAttribute('data-live-hydrated', 'true');

        // Bind events and hooks to the newly hydrated content
        reinitAfterDOMUpdate();
    },

    // Check if an element is lazily loaded
    isLazy(element) {
        return element.hasAttribute('dj-lazy');
    },

    // Force hydrate all lazy elements (useful for testing or SPA navigation)
    hydrateAll() {
        document.querySelectorAll('[dj-lazy]').forEach(el => {
            this.hydrateElement(el);
        });
    }
};

// Expose lazy hydration API
window.djust.lazyHydration = lazyHydrationManager;

// ============================================================================
// SSE Transport Fallback
// ============================================================================

/**
 * Switch the active transport to SSE.
 *
 * Called either when WebSocket exhausts all reconnect attempts (automatic
 * fallback) or directly when WebSocket is disabled and SSE is available.
 * Replaces the global liveViewWS instance with a LiveViewSSE instance and
 * mounts the view via the SSE stream endpoint.
 */
function _switchToSSETransport() {
    const container = document.querySelector('[dj-view]');
    if (!container) {
        console.warn('[SSE] No [dj-view] container found, cannot switch to SSE transport');
        return;
    }
    const viewPath = container.getAttribute('dj-view');
    if (!viewPath) {
        console.warn('[SSE] [dj-view] has no view path attribute, cannot switch to SSE transport');
        return;
    }

    if (globalThis.djustDebug) console.log('[SSE] Switching to SSE transport for view:', viewPath);

    const sseInstance = new window.djust.LiveViewSSE();
    liveViewWS = sseInstance;
    window.djust.liveViewInstance = sseInstance;

    const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
    sseInstance.connect(viewPath, urlParams);
}

// Expose for tests and manual override
window.djust._switchToSSETransport = _switchToSSETransport;

// ============================================================================
// Auto-stamp dj-root and dj-liveview-root on [dj-view]
// elements so developers only need to write dj-view (#258).
// Extracted as a helper so both djustInit() and reinitLiveViewForTurboNav() can call it.
function autoStampRootAttributes() {
    const allContainers = document.querySelectorAll('[dj-view]');
    allContainers.forEach(container => {
        if (!container.hasAttribute('dj-root')) {
            container.setAttribute('dj-root', '');
        }
        if (!container.hasAttribute('dj-liveview-root')) {
            container.setAttribute('dj-liveview-root', '');
        }
    });
    return allContainers;
}

// Initialize on load (support both normal page load and dynamic script injection via TurboNav)
function djustInit() {
    if (globalThis.djustDebug) console.log('[LiveView] Initializing...');

    // Initialize lazy hydration manager
    lazyHydrationManager.init();

    // Auto-stamp root attributes on all [dj-view] elements
    const allContainers = autoStampRootAttributes();

    if (allContainers.length === 0) {
        if (globalThis.djustDebug) console.error(
            '[LiveView] No containers found! Your template root element needs:\n' +
            '  dj-view="app.views.MyView"\n' +
            'Example: <div dj-view="myapp.views.DashboardView">'
        );
    } else {
        if (globalThis.djustDebug) console.log(`[LiveView] Found ${allContainers.length} containers`);
    }

    const lazyContainers = document.querySelectorAll('[dj-view][dj-lazy]');
    const eagerContainers = document.querySelectorAll('[dj-view]:not([dj-lazy])');

    // Register lazy containers with the lazy hydration manager
    lazyContainers.forEach(container => {
        lazyHydrationManager.register(container);
    });

    // Only initialize WebSocket if there are eager containers AND WebSocket is enabled
    const wsEnabled = window.DJUST_USE_WEBSOCKET !== false;
    const sseAvailable = typeof window.djust.LiveViewSSE !== 'undefined' && typeof EventSource !== 'undefined';

    if (eagerContainers.length > 0 && wsEnabled) {
        // Initialize WebSocket
        liveViewWS = new LiveViewWebSocket();
        window.djust.liveViewInstance = liveViewWS;

        // Wire SSE fallback: if WebSocket exhausts all reconnect attempts AND
        // the SSE transport is available, switch over automatically.
        if (sseAvailable) {
            liveViewWS.onTransportFailed = () => _switchToSSETransport();
        }

        liveViewWS.connect();

        // Start heartbeat
        liveViewWS.startHeartbeat();
    } else if (eagerContainers.length > 0 && !wsEnabled && sseAvailable) {
        // use_websocket: false but SSE is available — skip WebSocket entirely
        if (globalThis.djustDebug) console.log('[LiveView] WebSocket disabled, using SSE transport directly');
        _switchToSSETransport();
    } else if (eagerContainers.length > 0 && !wsEnabled) {
        // HTTP-only mode: create WS instance but disable it so sendEvent() falls through to HTTP
        liveViewWS = new LiveViewWebSocket();
        liveViewWS.enabled = false;
        window.djust.liveViewInstance = liveViewWS;
        if (globalThis.djustDebug) console.log('[LiveView] HTTP-only mode (use_websocket: false)');
    } else if (lazyContainers.length > 0) {
        if (globalThis.djustDebug) console.log('[LiveView] Deferring WebSocket connection until lazy elements are needed');
    }

    // Initialize React counters (if any)
    initReactCounters();

    // Bind initial events
    bindLiveViewEvents();

    // Mount dj-hook elements
    mountHooks();

    // Bind dj-model elements
    bindModelElements();

    // Initialize Draft Mode
    initDraftMode();

    // Scan and register dj-loading attributes
    globalLoadingManager.scanAndRegister();

    // Mark as initialized so turbo:load handler knows we're ready
    window.djustInitialized = true;
    if (globalThis.djustDebug) console.log('[LiveView] Initialization complete, window.djustInitialized = true');

    // Check if we have a pending turbo reinit (turbo:load fired before we finished init)
    if (pendingTurboReinit) {
        if (globalThis.djustDebug) console.log('[LiveView] Processing pending turbo:load reinit');
        pendingTurboReinit = false;
        reinitLiveViewForTurboNav();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', djustInit);
} else {
    djustInit();
}
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
        if (globalThis.djustDebug) console.log('[Upload] Configs loaded:', Object.keys(uploadConfigs));
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

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
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

        // Auto-upload if configured (default)
        if (!config || config.auto_upload !== false) {
            if (!isWSConnected()) {
                console.error('[Upload] WebSocket not connected');
                return;
            }

            for (const file of files) {
                // Validate client-side
                if (config) {
                    if (file.size > config.max_file_size) {
                        console.warn('[Upload] File too large: %s (%d > %d)', String(file.name), file.size, config.max_file_size);
                        window.dispatchEvent(new CustomEvent('djust:upload:error', {
                            detail: { file: file.name, error: 'File too large' }
                        }));
                        continue;
                    }
                }

                try {
                    const result = await uploadFile(liveViewWS, uploadName, file, config);
                    if (globalThis.djustDebug) console.log('[Upload] Complete: %s %o', String(file.name), result);
                } catch (err) {
                    console.error('[Upload] Failed: %s %o', String(file.name), err);
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

                if (!isWSConnected()) {
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
                        console.error('[Upload] Drop upload failed: %s %o', String(file.name), err);
                    }
                }
            });
        });
    }

    // ========================================================================
    // Exports
    // ========================================================================

    /**
     * Route a FileList obtained from `ClipboardEvent.clipboardData.files`
     * (the `dj-paste` flow) through the same upload pipeline used by
     * file-input and drag-drop uploads. Expects the target element to
     * have a `dj-upload="<slot_name>"` attribute whose slot has been
     * configured via `setUploadConfigs`.
     *
     * Returns a promise that resolves once every pasted file has been
     * uploaded (or rejected client-side). Errors for individual files
     * are dispatched as `djust:upload:error` events; they do not throw.
     */
    async function queueClipboardFiles(element, fileList) {
        if (!fileList || fileList.length === 0) return;
        const uploadName = element && element.getAttribute && element.getAttribute('dj-upload');
        if (!uploadName) return;

        const config = uploadConfigs[uploadName];
        const files = Array.from(fileList);

        await showPreviews(uploadName, files);

        if (!isWSConnected()) {
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
                console.error('[Upload] Paste upload failed: %s %o', String(file.name), err);
            }
        }
    }

    window.djust.uploads = {
        setConfigs: setUploadConfigs,
        handleProgress: handleUploadProgress,
        bindHandlers: bindUploadHandlers,
        cancelUpload: (ref) => cancelUpload(liveViewWS, ref),
        activeUploads: activeUploads,
        queueClipboardFiles: queueClipboardFiles,
    };

})();
// ============================================================================
// CursorOverlay — Built-in Hook for Collaborative Cursor Display
// ============================================================================
//
// Renders colored carets showing where remote users' cursors are positioned
// in a textarea. Uses the mirror-div technique to map character index to
// pixel coordinates.
//
// Usage:
//   <div dj-hook="CursorOverlay">
//     <textarea dj-input="update_content">{{ content }}</textarea>
//   </div>
//
// The hook auto-discovers the first <textarea> child and dynamically creates
// an overlay div (for rendering carets) and a hidden mirror div (for
// measuring cursor positions). No extra markup is needed in the template.
//
// Server contract:
//   - Hook sends: pushEvent("update_cursor", {position: <int>})
//   - Hook listens: handleEvent("cursor_positions", {cursors: {uid: {position, color, name, emoji}, ...}})
//
// ============================================================================

window.djust.hooks = window.djust.hooks || {};

window.djust.hooks.CursorOverlay = {
    mounted: function() {
        var self = this;

        // Discover textarea
        this.textarea = this.el.querySelector('textarea');
        if (!this.textarea) {
            console.warn('[CursorOverlay] No <textarea> found inside hook element');
            return;
        }

        // Create overlay div (visible, pointer-events:none)
        this.overlay = document.createElement('div');
        this.overlay.setAttribute('dj-update', 'ignore');
        this.overlay.style.cssText = 'position:absolute; inset:0; pointer-events:none; overflow:hidden;';
        // Copy textarea padding so carets align with text
        var cs = window.getComputedStyle(this.textarea);
        this.overlay.style.padding = cs.paddingTop + ' ' + cs.paddingRight + ' ' + cs.paddingBottom + ' ' + cs.paddingLeft;
        this.overlay.style.fontFamily = cs.fontFamily;
        this.overlay.style.fontSize = cs.fontSize;
        this.overlay.style.lineHeight = cs.lineHeight;
        this.el.appendChild(this.overlay);

        // Create hidden mirror div (for measurement)
        this.mirror = document.createElement('div');
        this.mirror.setAttribute('aria-hidden', 'true');
        this.mirror.style.cssText = 'position:absolute; visibility:hidden; white-space:pre-wrap; word-wrap:break-word; overflow-wrap:break-word;';
        this.el.appendChild(this.mirror);

        this._carets = {};       // {userId: caretElement}
        this._lastCursors = {};  // cached cursor data for repositioning on updated()
        this._debounceTimer = null;

        // Copy computed styles from textarea to mirror for accurate measurement
        this._syncMirrorStyles();

        // Debounced cursor position reporter
        this._sendCursorPosition = function() {
            clearTimeout(self._debounceTimer);
            self._debounceTimer = setTimeout(function() {
                var pos = self.textarea.selectionStart;
                self.pushEvent('update_cursor', { position: pos });
            }, 100);
        };

        // Bind cursor movement listeners
        this.textarea.addEventListener('keyup', this._sendCursorPosition);
        this.textarea.addEventListener('click', this._sendCursorPosition);
        this.textarea.addEventListener('select', this._sendCursorPosition);

        // Scroll sync: reposition carets when textarea scrolls
        this._onScroll = function() {
            self._repositionAll();
        };
        this.textarea.addEventListener('scroll', this._onScroll);

        // Listen for server push events with cursor positions
        this.handleEvent('cursor_positions', function(payload) {
            self._lastCursors = payload.cursors || {};
            self._renderCursors(self._lastCursors);
        });
    },

    updated: function() {
        // Text content may have changed — reposition all active carets
        if (this.textarea) {
            this._repositionAll();
        }
    },

    destroyed: function() {
        clearTimeout(this._debounceTimer);
        if (this.textarea) {
            this.textarea.removeEventListener('keyup', this._sendCursorPosition);
            this.textarea.removeEventListener('click', this._sendCursorPosition);
            this.textarea.removeEventListener('select', this._sendCursorPosition);
            this.textarea.removeEventListener('scroll', this._onScroll);
        }
        if (this.overlay && this.overlay.parentNode) {
            this.overlay.parentNode.removeChild(this.overlay);
        }
        if (this.mirror && this.mirror.parentNode) {
            this.mirror.parentNode.removeChild(this.mirror);
        }
    },

    _syncMirrorStyles: function() {
        var cs = window.getComputedStyle(this.textarea);
        var props = [
            'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
            'wordSpacing', 'textIndent', 'wordWrap', 'overflowWrap', 'whiteSpace',
            'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
            'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth'
        ];
        for (var i = 0; i < props.length; i++) {
            this.mirror.style[props[i]] = cs[props[i]];
        }
        // Use content-box width matching the textarea's actual text area
        // (clientWidth excludes scrollbar and border; subtract padding for content)
        var padL = parseFloat(cs.paddingLeft) || 0;
        var padR = parseFloat(cs.paddingRight) || 0;
        this.mirror.style.boxSizing = 'content-box';
        this.mirror.style.width = (this.textarea.clientWidth - padL - padR) + 'px';
    },

    _measureCursorPosition: function(charIndex) {
        // Mirror-div technique: fill mirror with text up to cursor, measure marker offset
        var text = this.textarea.value.substring(0, charIndex);
        this.mirror.textContent = '';
        var textNode = document.createTextNode(text);
        var marker = document.createElement('span');
        marker.textContent = '\u200b';  // zero-width space
        this.mirror.appendChild(textNode);
        this.mirror.appendChild(marker);

        var mirrorRect = this.mirror.getBoundingClientRect();
        var markerRect = marker.getBoundingClientRect();

        return {
            left: markerRect.left - mirrorRect.left,
            top: markerRect.top - mirrorRect.top - this.textarea.scrollTop
        };
    },

    _renderCursors: function(cursors) {
        var activeIds = {};

        for (var uid in cursors) {
            activeIds[uid] = true;
            var data = cursors[uid];
            var pos = this._measureCursorPosition(data.position);

            var caret = this._carets[uid];
            if (!caret) {
                // Create new caret element
                caret = document.createElement('div');
                caret.className = 'remote-cursor';
                caret.style.cssText = 'position:absolute; transition:left 0.15s ease, top 0.15s ease; pointer-events:none;';

                var line = document.createElement('div');
                line.style.cssText = 'width:2px; height:1.2em; border-radius:1px;';
                line.style.backgroundColor = data.color;
                caret.appendChild(line);

                var label = document.createElement('div');
                label.style.cssText = 'position:absolute; bottom:100%; left:0; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; white-space:nowrap; font-family:system-ui,sans-serif;';
                label.style.backgroundColor = data.color;
                label.textContent = (data.emoji || '') + ' ' + (data.name || '');
                caret.appendChild(label);

                this.overlay.appendChild(caret);
                this._carets[uid] = caret;
            }

            caret.style.left = pos.left + 'px';
            caret.style.top = pos.top + 'px';
        }

        // Remove carets for users who are no longer present
        for (var id in this._carets) {
            if (!activeIds[id]) {
                this._carets[id].parentNode.removeChild(this._carets[id]);
                delete this._carets[id];
            }
        }
    },

    _repositionAll: function() {
        // Re-sync mirror width in case textarea resized or scrollbar appeared
        var cs = window.getComputedStyle(this.textarea);
        var padL = parseFloat(cs.paddingLeft) || 0;
        var padR = parseFloat(cs.paddingRight) || 0;
        this.mirror.style.width = (this.textarea.clientWidth - padL - padR) + 'px';

        // Reposition using cached cursor data
        if (Object.keys(this._lastCursors).length > 0) {
            this._renderCursors(this._lastCursors);
        }
    }
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
 *       { op: "text",    target: "#output",       text: "partial token" },
 *       { op: "error",   target: "#output",       error: "Something failed" },
 *   ]}
 *
 * DOM attributes:
 *   dj-stream="stream_name"           — marks an element as a stream target
 *   dj-stream-mode="append|replace|prepend" — default insertion mode for text ops
 */

// Track active streams for error recovery and state
const _activeStreams = new Map();

function handleStreamMessage(data) {
    const ops = data.ops;
    if (!ops || !Array.isArray(ops)) return;

    const streamName = data.stream || '__default__';

    // Track stream as active
    if (!_activeStreams.has(streamName)) {
        _activeStreams.set(streamName, { started: Date.now(), errorCount: 0 });
    }

    for (const op of ops) {
        try {
            _applyStreamOp(op, streamName);
        } catch (err) {
            console.error('[LiveView:stream] Error applying op:', op, err);
            const info = _activeStreams.get(streamName);
            if (info) info.errorCount++;
        }
    }

    // Re-bind events and hooks on new DOM content
    reinitAfterDOMUpdate();
}

/**
 * Apply a single stream operation to the DOM.
 */
function _applyStreamOp(op, streamName) {
    const target = op.target;
    if (!target) return;

    // Resolve target element(s)
    const el = document.querySelector(target);
    if (!el) {
        if (globalThis.djustDebug) {
            console.warn('[LiveView:stream] Target not found:', target);
        }
        return;
    }

    switch (op.op) {
        case 'replace':
            // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
            el.innerHTML = op.html || '';
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:update', { op: 'replace', stream: streamName });
            break;

        case 'append': {
            const frag = _htmlToFragment(op.html);
            el.appendChild(frag);
            _autoScroll(el);
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:update', { op: 'append', stream: streamName });
            break;
        }

        case 'prepend': {
            const frag = _htmlToFragment(op.html);
            el.insertBefore(frag, el.firstChild);
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:update', { op: 'prepend', stream: streamName });
            break;
        }

        case 'delete':
            _dispatchStreamEvent(el, 'stream:remove', { stream: streamName });
            el.remove();
            break;

        case 'text': {
            // Streaming text content — respects dj-stream-mode attribute
            const text = op.text || '';
            const mode = op.mode || el.getAttribute('dj-stream-mode') || 'append';
            _applyTextOp(el, text, mode);
            _autoScroll(el);
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:text', { text, mode, stream: streamName });
            break;
        }

        case 'error': {
            // Show error state while preserving partial content
            const errorMsg = op.error || 'Stream error';
            _showStreamError(el, errorMsg);
            _dispatchStreamEvent(el, 'stream:error', { error: errorMsg, stream: streamName });
            break;
        }

        case 'done': {
            // Stream completed
            _activeStreams.delete(streamName);
            el.removeAttribute('data-stream-active');
            _dispatchStreamEvent(el, 'stream:done', { stream: streamName });
            break;
        }

        case 'start': {
            // Stream started — set active state
            el.setAttribute('data-stream-active', 'true');
            _removeStreamError(el);
            _dispatchStreamEvent(el, 'stream:start', { stream: streamName });
            break;
        }

        default:
            console.warn('[LiveView:stream] Unknown op:', op.op);
    }
}

/**
 * Apply text content with append/replace/prepend modes.
 */
function _applyTextOp(el, text, mode) {
    switch (mode) {
        case 'replace':
            el.textContent = text;
            break;
        case 'prepend':
            el.textContent = text + el.textContent;
            break;
        case 'append':
        default:
            el.textContent += text;
            break;
    }
    el.setAttribute('data-stream-active', 'true');
}

/**
 * Show an error indicator on a stream target, preserving existing content.
 */
function _showStreamError(el, message) {
    el.setAttribute('data-stream-error', 'true');
    el.removeAttribute('data-stream-active');

    // Add error element if not already present
    let errorEl = el.querySelector('.dj-stream-error');
    if (!errorEl) {
        errorEl = document.createElement('div');
        errorEl.className = 'dj-stream-error';
        errorEl.setAttribute('role', 'alert');
        el.appendChild(errorEl);
    }
    errorEl.textContent = message;
}

/**
 * Remove error state from a stream target.
 */
function _removeStreamError(el) {
    el.removeAttribute('data-stream-error');
    const errorEl = el.querySelector('.dj-stream-error');
    if (errorEl) errorEl.remove();
}

/**
 * Dispatch a custom event on a stream target element.
 */
function _dispatchStreamEvent(el, eventName, detail) {
    el.dispatchEvent(new CustomEvent(eventName, {
        bubbles: true,
        detail: detail,
    }));
}

/**
 * Parse an HTML string into a DocumentFragment.
 */
function _htmlToFragment(html) {
    const template = document.createElement('template');
    // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
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

/**
 * Get info about active streams (for debugging).
 */
function getActiveStreams() {
    const result = {};
    for (const [name, info] of _activeStreams) {
        result[name] = { ...info };
    }
    return result;
}

// Expose for WebSocket handler
window.djust = window.djust || {};
window.djust.handleStreamMessage = handleStreamMessage;
window.djust.getActiveStreams = getActiveStreams;

// ============================================================================
// Navigation — URL State Management (live_patch / live_redirect)
// ============================================================================

(function () {

    /**
     * Handle navigation commands from the server.
     *
     * Called when the server sends a { type: "navigation", ... } message
     * after a handler calls live_patch() or live_redirect().
     */
    function handleNavigation(data) {
        // Use data.action (set by server alongside type:"navigation") to distinguish
        // live_patch from live_redirect. Falls back to data.type for any legacy messages
        // that were sent without an action field.
        // TODO(deprecation): data.type fallback for pre-#307 clients — remove in next minor release
        const action = data.action || data.type;
        if (action === 'live_patch') {
            handleLivePatch(data);
        } else if (action === 'live_redirect') {
            handleLiveRedirect(data);
        }
    }

    /**
     * live_patch: Update URL without remounting the view.
     * Uses history.pushState/replaceState.
     */
    function handleLivePatch(data) {
        const currentUrl = new URL(window.location.href);
        let newUrl;

        if (data.path) {
            newUrl = new URL(data.path, window.location.origin);
        } else {
            newUrl = new URL(currentUrl);
        }

        // Set query params
        if (data.params !== undefined) {
            // Clear existing params and set new ones
            newUrl.search = '';
            for (const [key, value] of Object.entries(data.params || {})) {
                if (value !== null && value !== undefined && value !== '') {
                    newUrl.searchParams.set(key, String(value));
                }
            }
        }

        const method = data.replace ? 'replaceState' : 'pushState';
        window.history[method]({ djust: true }, '', newUrl.toString());

        if (globalThis.djustDebug) console.log(`[LiveView] live_patch: ${method} → ${newUrl.toString()}`);
    }

    /**
     * live_redirect: Navigate to a different view over the same WebSocket.
     * Updates URL, then sends a mount message for the new view.
     */
    function handleLiveRedirect(data) {
        // Start page loading bar for live_redirect navigation
        if (window.djust.pageLoading && window.djust.pageLoading.enabled) {
            window.djust.pageLoading.start();
        }

        const newUrl = new URL(data.path, window.location.origin);

        if (data.params) {
            for (const [key, value] of Object.entries(data.params)) {
                if (value !== null && value !== undefined && value !== '') {
                    newUrl.searchParams.set(key, String(value));
                }
            }
        }

        const method = data.replace ? 'replaceState' : 'pushState';
        window.history[method]({ djust: true, redirect: true }, '', newUrl.toString());

        // Clear the prefetch set so links on the new view are re-eligible for prefetching
        window.djust._prefetch?.clear();

        if (globalThis.djustDebug) console.log(`[LiveView] live_redirect: ${method} → ${newUrl.toString()}`);

        // Send a mount request for the new view path over the existing WebSocket
        // The server will unmount the old view and mount the new one
        if (isWSConnected()) {
            // Look up the view path for the new URL from the route map
            const viewPath = resolveViewPath(newUrl.pathname);
            if (viewPath) {
                const urlParams = Object.fromEntries(newUrl.searchParams);
                liveViewWS.sendMessage({
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: urlParams,
                    url: newUrl.pathname,
                });
            } else {
                // Fallback: full page navigation if we can't resolve the view
                console.warn('[LiveView] Cannot resolve view for', newUrl.pathname, '— doing full navigation');
                window.location.href = newUrl.toString();
            }
        }
    }

    /**
     * Resolve a URL path to a view path using the route map.
     *
     * The route map is populated by live_session() via a <script> tag
     * or by data attributes on the container.
     */
    function resolveViewPath(pathname) {
        // Check the route map (populated by live_session)
        const routeMap = window.djust._routeMap || {};

        // Try exact match first
        if (routeMap[pathname]) {
            return routeMap[pathname];
        }

        // Try pattern matching (for paths with parameters like /items/42/)
        for (const [pattern, viewPath] of Object.entries(routeMap)) {
            if (pattern.includes(':')) {
                // Convert Django-style pattern to regex
                // e.g., "/items/:id/" → /^\/items\/([^\/]+)\/$/
                const regexStr = pattern.replace(/:([^/]+)/g, '([^/]+)');
                const regex = new RegExp('^' + regexStr + '$');
                if (regex.test(pathname)) {
                    return viewPath;
                }
            }
        }

        // Fallback: check the current container's dj-view
        // (only works for live_patch, not cross-view navigation)
        const container = document.querySelector('[dj-view]');
        if (container) {
            return container.getAttribute('dj-view');
        }

        return null;
    }

    /**
     * Listen for browser back/forward (popstate) and send url_change to server.
     */
    window.addEventListener('popstate', function (event) {
        if (!liveViewWS || !liveViewWS.viewMounted) return;
        if (!isWSConnected()) return;

        const url = new URL(window.location.href);
        const params = Object.fromEntries(url.searchParams);

        // Check if this is a redirect (different path) vs patch (same path, different params)
        const isRedirect = event.state && event.state.redirect;

        if (isRedirect) {
            // Different view — need to remount
            const viewPath = resolveViewPath(url.pathname);
            if (viewPath) {
                liveViewWS.sendMessage({
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: params,
                    url: url.pathname,
                });
            } else {
                // Fallback
                window.location.reload();
            }
        } else {
            // Same view, different params — send url_change
            liveViewWS.sendMessage({
                type: 'url_change',
                params: params,
                uri: url.pathname + url.search,
            });
        }
    });

    /**
     * Bind dj-patch and dj-navigate directives.
     *
     * Called from bindLiveViewEvents() after DOM updates.
     */
    function _executePatch(el, patchValue, selectValue) {
        // Replace {value} placeholder with the actual value (for selects)
        if (selectValue !== undefined) {
            patchValue = patchValue.replace(/\{value\}/g, encodeURIComponent(selectValue));
        }

        const url = new URL(patchValue, window.location.href);

        // Build new URL by merging params into current URL
        const newUrl = new URL(window.location.href);
        for (const [k, v] of url.searchParams) {
            newUrl.searchParams.set(k, v);
        }
        if (patchValue.startsWith('/')) {
            newUrl.pathname = url.pathname;
        }

        // dj-patch-reload attribute forces full page navigation (opt-in escape hatch).
        if (el.hasAttribute('dj-patch-reload')) {
            window.location.href = newUrl.toString();
            return;
        }

        // WebSocket patch — pushState + url_change for selects, inputs, links, buttons
        if (!liveViewWS || !liveViewWS.viewMounted) return;
        window.history.pushState({ djust: true }, '', newUrl.toString());

        const allParams = Object.fromEntries(newUrl.searchParams);
        liveViewWS.sendMessage({
            type: 'url_change',
            params: allParams,
            uri: newUrl.pathname + newUrl.search,
        });
    }

    // Delegated change handler for dj-patch on select/input elements.
    // Bound once on document so it survives DOM replacement by morphdom.
    (function () {
        var _djPatchChangeHandlerInstalled = false;
        function installDjPatchChangeHandler() {
            if (_djPatchChangeHandlerInstalled) return;
            _djPatchChangeHandlerInstalled = true;
            document.addEventListener('change', function (e) {
                var el = e.target.closest('[dj-patch]');
                if (!el) return;
                if (el.tagName === 'SELECT' || el.tagName === 'INPUT') {
                    _executePatch(el, el.getAttribute('dj-patch'), el.value);
                }
            });
        }
        installDjPatchChangeHandler();
    })();

    function bindNavigationDirectives() {
        // dj-patch: Update URL params without remount
        // Select/input elements are handled by the delegated document listener above.
        document.querySelectorAll('[dj-patch]').forEach(function (el) {
            if (el.dataset.djustPatchBound) return;
            el.dataset.djustPatchBound = 'true';

            // Only bind click for non-select elements (links/buttons)
            if (el.tagName !== 'SELECT' && el.tagName !== 'INPUT') {
                el.addEventListener('click', function (e) {
                    e.preventDefault();
                    // When dj-patch is used as a boolean attribute on <a> tags
                    // (e.g. <a href="?tab=docs" dj-patch>), the attribute value
                    // is "" and the navigation target is the href.  Fall back to
                    // href so the link destination is respected.
                    var patchValue = el.getAttribute('dj-patch');
                    if (!patchValue && el.tagName === 'A') {
                        patchValue = el.getAttribute('href') || '';
                    }
                    _executePatch(el, patchValue);
                });
            }
        });

        // dj-navigate: Navigate to a different view
        document.querySelectorAll('[dj-navigate]').forEach(function (el) {
            if (el.dataset.djustNavigateBound) return;
            el.dataset.djustNavigateBound = 'true';

            el.addEventListener('click', function (e) {
                e.preventDefault();
                if (!liveViewWS || !liveViewWS.ws) return;

                const path = el.getAttribute('dj-navigate');
                handleLiveRedirect({ path: path, replace: false });
            });
        });
    }

    // Expose to djust namespace
    window.djust.navigation = {
        handleNavigation: handleNavigation,
        bindDirectives: bindNavigationDirectives,
        resolveViewPath: resolveViewPath,
    };

    // Initialize route map
    if (!window.djust._routeMap) {
        window.djust._routeMap = {};
    }
})();
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
 * Notify active hooks that a DOM update is about to happen.
 * Call this BEFORE applying patches.
 */
function beforeUpdateHooks(root) {
    _ensureHooksInit();
    root = root || document;
    for (const [, entry] of _activeHooks) {
        // Only call if element is still in the DOM
        if (root.contains(entry.el) && typeof entry.instance.beforeUpdate === 'function') {
            try {
                entry.instance.beforeUpdate();
            } catch (e) {
                console.error(`[dj-hook] Error in ${entry.hookName}.beforeUpdate():`, e);
            }
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
    _ensureHooksInit();
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
    _ensureHooksInit();
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
    _ensureHooksInit();
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
    _ensureHooksInit();
    for (const [, entry] of _activeHooks) {
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
// ============================================================================
// dj-model — Two-Way Data Binding
// ============================================================================
//
// Automatically syncs form input values with server-side view attributes.
//
// Usage in template:
//   <input type="text" dj-model="search_query" />
//   <textarea dj-model="description"></textarea>
//   <select dj-model="category">...</select>
//   <input type="checkbox" dj-model="is_active" />
//
// Options:
//   dj-model="field_name"              — sync on 'input' event (default)
//   dj-model.lazy="field_name"         — sync on 'change' event (blur)
//   dj-model.debounce-300="field_name" — debounce by 300ms
//
// The server-side ModelBindingMixin handles the 'update_model' event
// and sets the attribute on the view instance.
//
// ============================================================================

const _modelDebounceTimers = new Map();

/**
 * Parse dj-model attribute value and modifiers.
 * "field_name" → { field: "field_name", lazy: false, debounce: 0 }
 * With attribute dj-model.lazy="field_name" → { field: "field_name", lazy: true }
 */
function _parseModelAttr(el) {
    // Check for dj-model.lazy and dj-model.debounce-N
    const attrs = el.attributes;
    let field = null;
    let lazy = false;
    let debounce = 0;

    for (let i = 0; i < attrs.length; i++) {
        const name = attrs[i].name;
        if (name === 'dj-model') {
            field = attrs[i].value;
        } else if (name === 'dj-model.lazy') {
            field = attrs[i].value;
            lazy = true;
        } else if (name.startsWith('dj-model.debounce')) {
            field = attrs[i].value;
            const match = name.match(/debounce-?(\d+)/);
            debounce = match ? parseInt(match[1], 10) : 300;
        }
    }

    return { field, lazy, debounce };
}

/**
 * Get the current value from a form element.
 */
function _getElementValue(el) {
    if (el.type === 'checkbox') {
        return el.checked;
    }
    if (el.type === 'radio') {
        // For radio buttons, find the checked one in the same group
        const form = el.closest('form') || document;
        const checked = form.querySelector(`input[name="${el.name}"]:checked`);
        return checked ? checked.value : null;
    }
    if (el.tagName === 'SELECT' && el.multiple) {
        return Array.from(el.selectedOptions).map(o => o.value);
    }
    return el.value;
}

/**
 * Send update_model event to server.
 * Tries direct WebSocket first (synchronous, no loading states needed for model
 * binding), then falls back to handleEvent for HTTP-only scenarios.
 */
function _sendModelUpdate(field, value) {
    // Fast path: send directly via WebSocket (synchronous)
    const inst = window.djust.liveViewInstance;
    if (inst && inst.sendEvent && inst.sendEvent('update_model', { field, value })) {
        return;
    }
    // Fallback: handleEvent (includes HTTP fallback, loading states)
    handleEvent('update_model', { field, value });
}

/**
 * Bind dj-model to a single element.
 */
function _bindModel(el) {
    if (el._djustModelBound) return;
    el._djustModelBound = true;

    const { field, lazy, debounce } = _parseModelAttr(el);
    if (!field) return;

    const eventType = lazy ? 'change' : 'input';

    const handler = () => {
        const value = _getElementValue(el);

        if (debounce > 0) {
            const timerKey = `model:${field}`;
            if (_modelDebounceTimers.has(timerKey)) {
                clearTimeout(_modelDebounceTimers.get(timerKey));
            }
            _modelDebounceTimers.set(timerKey, setTimeout(() => {
                _sendModelUpdate(field, value);
                _modelDebounceTimers.delete(timerKey);
            }, debounce));
        } else {
            _sendModelUpdate(field, value);
        }
    };

    el.addEventListener(eventType, handler);

    // For checkboxes and radios, also listen on change
    if (el.type === 'checkbox' || el.type === 'radio') {
        el.addEventListener('change', handler);
    }
}

/**
 * Scan and bind all dj-model elements.
 */
function bindModelElements(root) {
    root = root || document;
    const elements = root.querySelectorAll('[dj-model], [dj-model\\.lazy], [dj-model\\.debounce]');
    elements.forEach(_bindModel);

    // Also check for dj-model with modifiers via attribute prefix
    root.querySelectorAll('input, textarea, select').forEach(el => {
        for (let i = 0; i < el.attributes.length; i++) {
            if (el.attributes[i].name.startsWith('dj-model')) {
                _bindModel(el);
                break;
            }
        }
    });
}

// Export
window.djust.bindModelElements = bindModelElements;
} // End of double-load guard
// ============================================================================
// Prefetch on Hover
// ============================================================================
// Posts PREFETCH messages to the service worker when users hover over links.
// Only same-origin links are prefetched; each URL is prefetched at most once.
// The set is cleared on SPA navigation so links on the new view are re-eligible.

(function () {
    var _prefetched = new Set();

    function _shouldPrefetch(link) {
        // No SW controller available
        if (!navigator.serviceWorker || !navigator.serviceWorker.controller) {
            return false;
        }
        // Respect save-data preference
        if (navigator.connection && navigator.connection.saveData) {
            return false;
        }
        // Element opted out
        if (link.hasAttribute('data-no-prefetch')) {
            return false;
        }
        // Must have href and be same-origin
        if (!link.href) {
            return false;
        }
        try {
            var url = new URL(link.href, location.origin);
            if (url.origin !== location.origin) {
                return false;
            }
        } catch (e) {
            return false;
        }
        // Already prefetched
        if (_prefetched.has(link.href)) {
            return false;
        }
        return true;
    }

    document.addEventListener('pointerenter', function (event) {
        if (!(event.target instanceof Element)) return;
        var link = event.target.closest('a');
        if (!link || !_shouldPrefetch(link)) {
            return;
        }
        _prefetched.add(link.href);
        if (globalThis.djustDebug) console.log('[djust] Prefetching:', link.href);
        navigator.serviceWorker.controller.postMessage({
            type: 'PREFETCH',
            url: link.href
        });
    }, true);

    // Expose for testing and for navigation to clear on SPA transition
    window.djust = window.djust || {};
    window.djust._prefetch = {
        _prefetched: _prefetched,
        _shouldPrefetch: _shouldPrefetch,
        clear: function () { _prefetched.clear(); }
    };
})();

// ============================================================================
// Flash Messages — Server-to-client transient notifications (put_flash)
// ============================================================================

(function () {

    var CONTAINER_ID = 'dj-flash-container';
    var DEFAULT_AUTO_DISMISS = 5000;
    var REMOVE_TRANSITION_MS = 300;

    /**
     * Get or create the flash container element.
     * Returns null if the container doesn't exist in the DOM (tag not used).
     */
    function getContainer() {
        return document.getElementById(CONTAINER_ID);
    }

    /**
     * Handle a flash message from the server.
     *
     * data.action === 'put':   render a new flash message
     * data.action === 'clear': remove existing messages (optionally by level)
     */
    function handleFlash(data) {
        if (globalThis.djustDebug) console.log('[LiveView] flash: %o', data);

        if (data.action === 'clear') {
            clearFlash(data.level);
            return;
        }

        if (data.action === 'put') {
            showFlash(data.level, data.message);
        }
    }

    /**
     * Render a flash message into the container.
     */
    function showFlash(level, message) {
        var container = getContainer();
        if (!container) {
            if (globalThis.djustDebug) console.log('[LiveView] flash: no #dj-flash-container found, skipping');
            return;
        }

        var el = document.createElement('div');
        el.className = 'dj-flash dj-flash-' + level;
        el.setAttribute('role', 'alert');
        el.setAttribute('data-dj-flash-level', level);
        el.textContent = message;

        container.appendChild(el);

        // Auto-dismiss
        var timeout = parseInt(container.getAttribute('data-dj-auto-dismiss'), 10);
        if (isNaN(timeout)) {
            timeout = DEFAULT_AUTO_DISMISS;
        }
        if (timeout > 0) {
            setTimeout(function () {
                dismissFlash(el);
            }, timeout);
        }
    }

    /**
     * Dismiss a single flash element with a removal animation.
     */
    function dismissFlash(el) {
        if (!el || !el.parentNode) return;
        el.classList.add('dj-flash-removing');
        setTimeout(function () {
            if (el.parentNode) {
                el.parentNode.removeChild(el);
            }
        }, REMOVE_TRANSITION_MS);
    }

    /**
     * Clear flash messages from the container.
     * If level is provided, only clear messages with that level.
     */
    function clearFlash(level) {
        var container = getContainer();
        if (!container) return;

        var selector = level
            ? '.dj-flash[data-dj-flash-level="' + level + '"]'
            : '.dj-flash';
        var elements = container.querySelectorAll(selector);
        for (var i = 0; i < elements.length; i++) {
            dismissFlash(elements[i]);
        }
    }

    // Expose to djust namespace
    window.djust.flash = {
        handleFlash: handleFlash,
        show: showFlash,
        clear: clearFlash,
        dismiss: dismissFlash,
    };

})();

// ============================================================================
// Page Loading Bar — NProgress-style loading indicator for navigation
//
// Lifecycle events:
//   djust:navigate-start  — dispatched when navigation begins
//   djust:navigate-end    — dispatched when navigation completes
//
// CSS class:
//   .djust-navigating     — added to [dj-root] during navigation
//
// Example (zero-JS page transition):
//   [dj-root].djust-navigating main {
//       opacity: 0.3;
//       transition: opacity 0.15s ease;
//       pointer-events: none;
//   }
// ============================================================================

(function () {
    // Inject CSS for the loading bar
    const style = document.createElement('style');
    style.textContent = `
        #djust-page-loading-bar {
            position: fixed;
            top: 0;
            left: 0;
            height: 3px;
            background: linear-gradient(90deg, #818cf8, #6366f1, #4f46e5);
            z-index: 99999;
            transition: width 2s ease-out, opacity 0.3s ease;
            pointer-events: none;
        }
    `;
    document.head.appendChild(style);

    let barElement = null;
    let finishTimeout = null;

    function start() {
        // Clean up any existing bar
        if (barElement) {
            barElement.remove();
            barElement = null;
        }
        if (finishTimeout) {
            clearTimeout(finishTimeout);
            finishTimeout = null;
        }

        barElement = document.createElement('div');
        barElement.id = 'djust-page-loading-bar';
        barElement.style.width = '0%';
        barElement.style.opacity = '1';
        document.body.appendChild(barElement);

        // Animate to 90% (never completes until finish() is called)
        requestAnimationFrame(() => {
            if (barElement) {
                barElement.style.width = '90%';
            }
        });

        // Add navigating class to dj-root for CSS-based transitions
        const root = document.querySelector('[dj-root]');
        if (root) root.classList.add('djust-navigating');

        // Dispatch lifecycle event
        document.dispatchEvent(new CustomEvent('djust:navigate-start'));
    }

    function finish() {
        if (!barElement) return;

        // Remove navigating class from dj-root
        const root = document.querySelector('[dj-root]');
        if (root) root.classList.remove('djust-navigating');

        // Dispatch lifecycle event
        document.dispatchEvent(new CustomEvent('djust:navigate-end'));

        // Snap to 100%
        barElement.style.transition = 'width 0.2s ease, opacity 0.3s ease 0.2s';
        barElement.style.width = '100%';
        barElement.style.opacity = '0';

        const bar = barElement;
        finishTimeout = setTimeout(() => {
            bar.remove();
            if (barElement === bar) {
                barElement = null;
            }
            finishTimeout = null;
        }, 500);
    }

    window.djust.pageLoading = {
        start: start,
        finish: finish,
        enabled: true,
    };

    // Hook into TurboNav: start bar before navigation
    window.addEventListener('turbo:before-visit', function () {
        if (window.djust.pageLoading.enabled) {
            start();
        }
    });

    // Hook into TurboNav: finish bar after load
    window.addEventListener('turbo:load', function () {
        if (window.djust.pageLoading.enabled) {
            finish();
        }
    });
})();

// ============================================================================
// Page Metadata — Dynamic document title and meta tag updates
// ============================================================================

(function () {

    // CSS.escape fallback for environments that don't support it (e.g., older browsers)
    var cssEscape = (typeof CSS !== 'undefined' && CSS.escape)
        ? CSS.escape
        : function (s) { return s.replace(/([^\w-])/g, '\\$1'); };

    /**
     * Handle a page metadata command from the server.
     *
     * data.action === 'title': update document.title
     * data.action === 'meta':  update or create a <meta> tag
     */
    function handlePageMetadata(data) {
        if (globalThis.djustDebug) console.log('[LiveView] page_metadata: %o', data);

        if (data.action === 'title') {
            document.title = data.value;
        } else if (data.action === 'meta') {
            var name = data.name;
            // Support both name= and property= attributes (og: and twitter: use property)
            var isOg = name.indexOf('og:') === 0 || name.indexOf('twitter:') === 0;
            var attr = isOg ? 'property' : 'name';
            var selector = 'meta[' + attr + '="' + cssEscape(name) + '"]';
            var el = document.querySelector(selector);
            if (el) {
                el.setAttribute('content', data.content);
            } else {
                el = document.createElement('meta');
                el.setAttribute(attr, name);
                el.setAttribute('content', data.content);
                document.head.appendChild(el);
            }
        }
    }

    // Expose to djust namespace
    window.djust.pageMetadata = {
        handlePageMetadata: handlePageMetadata,
    };

})();
// ============================================================================
// JS Commands — client-side interpreter + fluent chain API
// ============================================================================
//
// Exposes `window.djust.js` as both:
//   - a chain factory: djust.js.show('#modal').addClass('active', {to: '#overlay'}).exec()
//   - a dispatcher for JSON command lists built on the server via djust.js.JS
//
// Template binding integration: event handlers (dj-click, dj-change, etc.)
// detect when the attribute value starts with `[[` (a JSON command list) and
// execute the chain locally instead of sending a server event. The special
// `push` command in a chain DOES send a server event, so you can mix optimistic
// DOM updates with server round-trips in a single handler.
//
// All eleven commands from Phoenix LiveView 1.0 are supported: show, hide,
// toggle, add_class, remove_class, transition, dispatch, focus, set_attr,
// remove_attr, push. The public JS (camelCase) method names are addClass,
// removeClass, setAttr, removeAttr; the serialised ops use snake_case to match
// Phoenix and the Python djust.js.JS helper.
// ============================================================================

(function() {
    if (!window.djust) window.djust = {};

    // ------------------------------------------------------------------------
    // Target resolution
    // ------------------------------------------------------------------------

    /**
     * Resolve an operation's target into a NodeList-like array of elements.
     *
     * Targets (at most one):
     *   to=<selector>       absolute: document.querySelectorAll
     *   inner=<selector>    scoped to originEl's descendants
     *   closest=<selector>  walk up from originEl
     * (none of the above)   default to originEl itself
     */
    function resolveTargets(args, originEl) {
        args = args || {};
        if (args.to) {
            try {
                return Array.from(document.querySelectorAll(args.to));
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] bad to= selector', args.to, err);
                return [];
            }
        }
        if (args.inner && originEl) {
            try {
                return Array.from(originEl.querySelectorAll(args.inner));
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] bad inner= selector', args.inner, err);
                return [];
            }
        }
        if (args.closest && originEl) {
            try {
                const el = originEl.closest(args.closest);
                return el ? [el] : [];
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] bad closest= selector', args.closest, err);
                return [];
            }
        }
        return originEl ? [originEl] : [];
    }

    // ------------------------------------------------------------------------
    // Individual command executors
    // ------------------------------------------------------------------------

    function execShow(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const display = args.display || '';
        targets.forEach(el => {
            el.style.display = display;
            if (!display) el.style.removeProperty('display');
            el.removeAttribute('hidden');
            el.dispatchEvent(new CustomEvent('djust:show', { bubbles: true }));
        });
    }

    function execHide(args, originEl) {
        const targets = resolveTargets(args, originEl);
        targets.forEach(el => {
            el.style.display = 'none';
            el.dispatchEvent(new CustomEvent('djust:hide', { bubbles: true }));
        });
    }

    function execToggle(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const display = args.display || '';
        targets.forEach(el => {
            const cs = el.ownerDocument.defaultView.getComputedStyle(el);
            const hidden = cs.display === 'none' || el.hidden;
            if (hidden) {
                el.style.display = display;
                if (!display) el.style.removeProperty('display');
                el.removeAttribute('hidden');
            } else {
                el.style.display = 'none';
            }
        });
    }

    function execAddClass(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const classes = (args.names || '').split(/\s+/).filter(Boolean);
        targets.forEach(el => el.classList.add(...classes));
    }

    function execRemoveClass(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const classes = (args.names || '').split(/\s+/).filter(Boolean);
        targets.forEach(el => el.classList.remove(...classes));
    }

    function execTransition(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const classes = (args.names || '').split(/\s+/).filter(Boolean);
        const time = Number(args.time) || 200;
        targets.forEach(el => {
            el.classList.add(...classes);
            setTimeout(() => {
                el.classList.remove(...classes);
            }, time);
        });
    }

    function execSetAttr(args, originEl) {
        const targets = resolveTargets(args, originEl);
        // args.attr is a 2-tuple [name, value] (matches Phoenix/the Python helper)
        if (!Array.isArray(args.attr) || args.attr.length < 2) return;
        const [name, value] = args.attr;
        targets.forEach(el => el.setAttribute(name, value));
    }

    function execRemoveAttr(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const name = typeof args.attr === 'string' ? args.attr : (Array.isArray(args.attr) ? args.attr[0] : null);
        if (!name) return;
        targets.forEach(el => el.removeAttribute(name));
    }

    function execFocus(args, originEl) {
        const targets = resolveTargets(args, originEl);
        if (targets.length) {
            try {
                targets[0].focus();
            } catch (err) { /* focus can throw on non-focusable elements */ }
        }
    }

    function execDispatch(args, originEl) {
        const targets = resolveTargets(args, originEl);
        const name = args.event || 'djust:unnamed';
        const detail = args.detail || {};
        const bubbles = args.bubbles !== false;
        targets.forEach(el => {
            el.dispatchEvent(new CustomEvent(name, { bubbles, detail }));
        });
    }

    async function execPush(args, originEl) {
        // push op: bridge a chain to a server event. Uses the existing
        // handleEvent() pipeline so debouncing, rate limiting, and the
        // HTTP/WebSocket fallback path all work identically.
        const event = args.event;
        if (!event) return;
        const params = Object.assign({}, args.value || {});
        if (args.target) params._target = args.target;
        if (args.page_loading && window.djust.pageLoading && window.djust.pageLoading.start) {
            try { window.djust.pageLoading.start(); } catch (_) {}
        }
        try {
            if (typeof window.djust.handleEvent === 'function') {
                await window.djust.handleEvent(event, params);
            }
        } finally {
            if (args.page_loading && window.djust.pageLoading && window.djust.pageLoading.stop) {
                try { window.djust.pageLoading.stop(); } catch (_) {}
            }
        }
    }

    const COMMAND_TABLE = {
        show: execShow,
        hide: execHide,
        toggle: execToggle,
        add_class: execAddClass,
        addClass: execAddClass,          // alias for chains built client-side
        remove_class: execRemoveClass,
        removeClass: execRemoveClass,
        transition: execTransition,
        set_attr: execSetAttr,
        setAttr: execSetAttr,
        remove_attr: execRemoveAttr,
        removeAttr: execRemoveAttr,
        focus: execFocus,
        dispatch: execDispatch,
        push: execPush,
    };

    // ------------------------------------------------------------------------
    // Chain execution
    // ------------------------------------------------------------------------

    /**
     * Execute a parsed op list against the event origin element.
     * ops: array of [opName, args] tuples.
     */
    async function executeOps(ops, originEl) {
        if (!Array.isArray(ops)) return;
        for (const entry of ops) {
            if (!Array.isArray(entry) || entry.length < 1) continue;
            const [opName, args] = entry;
            const fn = COMMAND_TABLE[opName];
            if (!fn) {
                if (globalThis.djustDebug) console.log('[js-commands] unknown op', opName);
                continue;
            }
            try {
                // Some ops are async (push); others are sync. Await handles both.
                await fn(args || {}, originEl);
            } catch (err) {
                if (globalThis.djustDebug) console.log('[js-commands] op failed', opName, err);
            }
        }
    }

    /**
     * Parse an attribute value into an op list if it looks like a JSON array.
     * Returns null if the value is a plain event name (existing behavior).
     */
    function parseCommandValue(value) {
        if (typeof value !== 'string') return null;
        const trimmed = value.trim();
        if (!trimmed.startsWith('[')) return null;
        try {
            const parsed = JSON.parse(trimmed);
            if (Array.isArray(parsed) && parsed.length > 0 && Array.isArray(parsed[0])) {
                return parsed;
            }
        } catch (_) {
            return null;
        }
        return null;
    }

    /**
     * Public entry point for the event-binding layer: given an attribute
     * value and the element that fired the event, either execute a JS
     * Command chain (returning true) or bail out so the caller can fall
     * back to the legacy event-name behavior (returning false).
     */
    async function tryExecuteAttribute(value, originEl) {
        const ops = parseCommandValue(value);
        if (!ops) return false;
        await executeOps(ops, originEl);
        return true;
    }

    // ------------------------------------------------------------------------
    // Fluent chain API (mirrors the Python djust.js.JS helper)
    // ------------------------------------------------------------------------

    function JSChain(ops) {
        this.ops = ops ? ops.slice() : [];
    }

    function _chainAdd(chain, op, args) {
        const next = new JSChain(chain.ops);
        next.ops.push([op, args || {}]);
        return next;
    }

    JSChain.prototype.show = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'show', args);
    };

    JSChain.prototype.hide = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'hide', args);
    };

    JSChain.prototype.toggle = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'toggle', args);
    };

    JSChain.prototype.addClass = function(names, options) {
        const args = Object.assign({ names: names }, options || {});
        return _chainAdd(this, 'add_class', args);
    };

    JSChain.prototype.removeClass = function(names, options) {
        const args = Object.assign({ names: names }, options || {});
        return _chainAdd(this, 'remove_class', args);
    };

    JSChain.prototype.transition = function(names, options) {
        const args = Object.assign({ names: names, time: 200 }, options || {});
        return _chainAdd(this, 'transition', args);
    };

    JSChain.prototype.setAttr = function(name, value, options) {
        const args = Object.assign({ attr: [name, value] }, options || {});
        return _chainAdd(this, 'set_attr', args);
    };

    JSChain.prototype.removeAttr = function(name, options) {
        const args = Object.assign({ attr: name }, options || {});
        return _chainAdd(this, 'remove_attr', args);
    };

    JSChain.prototype.focus = function(selector, options) {
        const args = Object.assign({}, options || {});
        if (selector) args.to = selector;
        return _chainAdd(this, 'focus', args);
    };

    JSChain.prototype.dispatch = function(event, options) {
        const args = Object.assign({ event: event, bubbles: true }, options || {});
        return _chainAdd(this, 'dispatch', args);
    };

    JSChain.prototype.push = function(event, options) {
        const args = Object.assign({ event: event }, options || {});
        return _chainAdd(this, 'push', args);
    };

    /**
     * Run the chain against ``originEl`` (or the document body if omitted).
     * Returns a promise that resolves when every op is complete — relevant
     * because `push` round-trips to the server.
     */
    JSChain.prototype.exec = async function(originEl) {
        return executeOps(this.ops, originEl || document.body);
    };

    JSChain.prototype.toString = function() {
        return JSON.stringify(this.ops);
    };

    // ------------------------------------------------------------------------
    // Factory: djust.js.show(...) starts a new chain; djust.js.chain() exposes
    // an empty chain for hooks that want to build one up.
    // ------------------------------------------------------------------------

    const factory = {
        chain: function() { return new JSChain(); },
        show: function(selector, options) { return new JSChain().show(selector, options); },
        hide: function(selector, options) { return new JSChain().hide(selector, options); },
        toggle: function(selector, options) { return new JSChain().toggle(selector, options); },
        addClass: function(names, options) { return new JSChain().addClass(names, options); },
        removeClass: function(names, options) { return new JSChain().removeClass(names, options); },
        transition: function(names, options) { return new JSChain().transition(names, options); },
        setAttr: function(name, value, options) { return new JSChain().setAttr(name, value, options); },
        removeAttr: function(name, options) { return new JSChain().removeAttr(name, options); },
        focus: function(selector, options) { return new JSChain().focus(selector, options); },
        dispatch: function(event, options) { return new JSChain().dispatch(event, options); },
        push: function(event, options) { return new JSChain().push(event, options); },

        // Internal hooks used by the event-binding layer:
        _executeOps: executeOps,
        _tryExecuteAttribute: tryExecuteAttribute,
        _parseCommandValue: parseCommandValue,
        _JSChain: JSChain,
    };

    window.djust.js = factory;
})();
// ============================================================================
// djust:exec auto-executor (ADR-002 Phase 1a — server-initiated JS Commands)
// ============================================================================
//
// Listens for server-pushed `djust:exec` events and runs the JS Command chain
// they carry via `window.djust.js._executeOps(ops, null)`. Framework-provided;
// users never write or register a hook for this — it's bound once when
// client.js loads and handles every `push_commands()` call from the server.
//
// Transport: the server calls `self.push_commands(chain)` → the mixin calls
// `push_event("djust:exec", {"ops": chain.ops})` → the WebSocket consumer
// flushes the push-event queue → client.js dispatches a global
// `djust:push_event` CustomEvent on `window` with `detail: {event, payload}`
// (see 03-websocket.js case 'push_event'). We filter for `event === 'djust:exec'`
// and interpret the `payload.ops` array using the same `_executeOps` function
// used by inline `dj-click="[[...]]"` JSON chains and fluent-API `.exec()` calls
// from dj-hook code.
//
// There is no HTML markup, no hook registration, and no user setup required.
// Every djust page that loads client.js gets the auto-executor for free.
// ============================================================================

(function() {
    if (!window.djust) window.djust = {};

    function handleDjustExec(event) {
        const detail = event.detail || {};
        const eventName = detail.event;
        const payload = detail.payload;

        if (eventName !== 'djust:exec') return;

        if (!window.djust.js || !window.djust.js._executeOps) {
            // JS Commands module hasn't loaded yet (shouldn't happen since the
            // module ordering puts 26-js-commands.js before this file in the
            // build, but be defensive).
            if (globalThis.djustDebug) {
                console.log('[djust:exec] js commands module not loaded; skipping exec chain');
            }
            return;
        }

        if (!payload || !Array.isArray(payload.ops)) {
            if (globalThis.djustDebug) {
                console.log('[djust:exec] malformed payload (expected {ops: [...]}):', payload);
            }
            return;
        }

        try {
            // Execute the chain against the document body as the origin element.
            // Scoped targets (inner, closest) without an explicit to= selector
            // don't make sense for server-pushed chains, so we pass the body as
            // a stable default origin — chains that care about a specific
            // origin element should always use `to=` selectors.
            window.djust.js._executeOps(payload.ops, document.body);
        } catch (err) {
            // Don't let one bad op break the whole event pipeline. Log in debug
            // mode and swallow — downstream chains keep working.
            if (globalThis.djustDebug) {
                console.log('[djust:exec] op execution failed:', err);
            }
        }
    }

    // Register the listener once. The CustomEvent is fired on `window` by the
    // WebSocket consumer in 03-websocket.js — matches existing push_event
    // delivery semantics used by dj-hook's handleEvent() registrations.
    window.addEventListener('djust:push_event', handleDjustExec);

    // Expose the handler for tests and for debug-panel inspection.
    window.djust._execListener = { handleDjustExec: handleDjustExec };
})();
// ============================================================================
// Tutorial bubble listener (ADR-002 Phase 1c)
// ============================================================================
//
// Listens for tour:narrate CustomEvents (dispatched by TutorialMixin via
// push_commands(JS.dispatch("tour:narrate", ...))) and updates the
// #dj-tutorial-bubble container rendered by {% tutorial_bubble %}. Handles:
//
//   - Text content update from detail.text
//   - Step/total progress indicator from detail.step/detail.total
//   - Smart positioning next to detail.target (above, below, left, right)
//   - Auto-scroll to bring the target into view
//   - Arrow/pointer connecting bubble to target
//   - Backdrop overlay dimming everything except the target
//   - Show/hide via data-visible attribute (CSS-styled)
//
// The bubble is a "dumb" DOM surface — all tour logic lives server-side in
// TutorialMixin. This file is purely the presentation layer.
// ============================================================================

(function() {
    var BUBBLE_ID = 'dj-tutorial-bubble';
    var BACKDROP_ID = 'dj-tutorial-backdrop';
    var ARROW_CLASS = 'dj-tutorial-bubble__arrow';
    var GAP = 14; // px between target and bubble

    function getBubble() {
        return document.getElementById(BUBBLE_ID);
    }

    // --- Backdrop overlay (dims everything except the target) ---

    function ensureBackdrop() {
        var existing = document.getElementById(BACKDROP_ID);
        if (existing) return existing;
        var el = document.createElement('div');
        el.id = BACKDROP_ID;
        el.style.cssText = 'position:fixed;inset:0;z-index:9998;pointer-events:none;' +
            'background:rgba(0,0,0,0.4);opacity:0;transition:opacity 0.3s ease;';
        document.body.appendChild(el);
        return el;
    }

    function showBackdrop(targetEl) {
        var backdrop = ensureBackdrop();
        backdrop.style.opacity = '1';

        // Cut out the target element with a box-shadow trick:
        // The backdrop is transparent, and we use a massive box-shadow
        // on a highlight overlay to dim everything else.
        if (targetEl) {
            var rect = targetEl.getBoundingClientRect();
            var pad = 6;
            backdrop.style.clipPath = 'polygon(' +
                '0% 0%, 0% 100%, ' +
                (rect.left - pad) + 'px 100%, ' +
                (rect.left - pad) + 'px ' + (rect.top - pad) + 'px, ' +
                (rect.right + pad) + 'px ' + (rect.top - pad) + 'px, ' +
                (rect.right + pad) + 'px ' + (rect.bottom + pad) + 'px, ' +
                (rect.left - pad) + 'px ' + (rect.bottom + pad) + 'px, ' +
                (rect.left - pad) + 'px 100%, ' +
                '100% 100%, 100% 0%)';
        }
    }

    function hideBackdrop() {
        var backdrop = document.getElementById(BACKDROP_ID);
        if (backdrop) {
            backdrop.style.opacity = '0';
            backdrop.style.clipPath = '';
        }
    }

    // --- Arrow element ---

    function ensureArrow(bubble) {
        var existing = bubble.querySelector('.' + ARROW_CLASS);
        if (existing) return existing;
        var arrow = document.createElement('div');
        arrow.className = ARROW_CLASS;
        arrow.style.cssText = 'position:absolute;width:12px;height:12px;' +
            'background:inherit;transform:rotate(45deg);z-index:-1;';
        bubble.appendChild(arrow);
        return arrow;
    }

    function positionArrow(arrow, position) {
        // Reset
        arrow.style.top = '';
        arrow.style.bottom = '';
        arrow.style.left = '';
        arrow.style.right = '';

        switch (position) {
            case 'top':
                arrow.style.bottom = '-6px';
                arrow.style.left = '50%';
                arrow.style.marginLeft = '-6px';
                break;
            case 'bottom':
                arrow.style.top = '-6px';
                arrow.style.left = '50%';
                arrow.style.marginLeft = '-6px';
                break;
            case 'left':
                arrow.style.right = '-6px';
                arrow.style.top = '50%';
                arrow.style.marginTop = '-6px';
                break;
            case 'right':
                arrow.style.left = '-6px';
                arrow.style.top = '50%';
                arrow.style.marginTop = '-6px';
                break;
        }
    }

    // --- Content update ---

    function updateBubbleContent(bubble, detail) {
        var text = (detail && detail.text) || '';
        var textEl = bubble.querySelector('.dj-tutorial-bubble__text');
        if (textEl) {
            textEl.textContent = text;
        }

        var stepEl = bubble.querySelector('.dj-tutorial-bubble__step');
        if (stepEl) {
            var step = detail && detail.step;
            var total = detail && detail.total;
            if (typeof step === 'number' && typeof total === 'number' && total > 0) {
                stepEl.textContent = 'Step ' + String(step + 1) + ' of ' + String(total);
            } else {
                stepEl.textContent = '';
            }
        }
    }

    // --- Smart positioning ---

    function positionBubble(bubble, detail) {
        var targetSelector = detail && detail.target;
        var preferredPosition = (detail && detail.position) ||
            bubble.getAttribute('data-default-position') ||
            'bottom';

        if (!targetSelector) {
            // No target — show centered at top
            bubble.style.position = 'fixed';
            bubble.style.top = '80px';
            bubble.style.left = '50%';
            bubble.style.transform = 'translateX(-50%)';
            bubble.style.bottom = '';
            bubble.style.right = '';
            hideBackdrop();
            return;
        }

        var target = null;
        try {
            target = document.querySelector(targetSelector);
        } catch (err) {
            if (globalThis.djustDebug) console.log('[tutorial-bubble] bad selector', targetSelector);
        }

        if (!target) {
            bubble.style.position = 'fixed';
            bubble.style.top = '80px';
            bubble.style.left = '50%';
            bubble.style.transform = 'translateX(-50%)';
            bubble.style.bottom = '';
            bubble.style.right = '';
            hideBackdrop();
            return;
        }

        // Auto-scroll target into view
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Wait a tick for scroll to settle before positioning
        requestAnimationFrame(function() {
            var rect = target.getBoundingClientRect();
            var bubbleRect = bubble.getBoundingClientRect();
            var viewW = window.innerWidth;
            var viewH = window.innerHeight;
            var position = preferredPosition;

            // Auto-flip if bubble would go off-screen
            if (position === 'bottom' && rect.bottom + GAP + bubbleRect.height > viewH) {
                position = 'top';
            } else if (position === 'top' && rect.top - GAP - bubbleRect.height < 0) {
                position = 'bottom';
            }

            // Use fixed positioning relative to viewport
            bubble.style.position = 'fixed';
            bubble.style.transform = '';
            bubble.style.bottom = '';
            bubble.style.right = '';

            // Center bubble horizontally on the target, clamp to viewport
            var bubbleLeft = rect.left + rect.width / 2 - bubbleRect.width / 2;
            bubbleLeft = Math.max(12, Math.min(bubbleLeft, viewW - bubbleRect.width - 12));

            switch (position) {
                case 'top':
                    bubble.style.top = (rect.top - GAP - bubbleRect.height) + 'px';
                    bubble.style.left = bubbleLeft + 'px';
                    break;
                case 'left':
                    bubble.style.top = (rect.top + rect.height / 2 - bubbleRect.height / 2) + 'px';
                    bubble.style.left = (rect.left - GAP - bubbleRect.width) + 'px';
                    break;
                case 'right':
                    bubble.style.top = (rect.top + rect.height / 2 - bubbleRect.height / 2) + 'px';
                    bubble.style.left = (rect.right + GAP) + 'px';
                    break;
                case 'bottom':
                default:
                    bubble.style.top = (rect.bottom + GAP) + 'px';
                    bubble.style.left = bubbleLeft + 'px';
                    break;
            }

            bubble.setAttribute('data-position', position);

            // Position arrow
            var arrow = ensureArrow(bubble);
            positionArrow(arrow, position);

            // Show backdrop with cutout around target
            showBackdrop(target);
        });
    }

    // --- Show/hide ---

    function showBubble(bubble) {
        bubble.setAttribute('data-visible', 'true');
    }

    function hideBubble(bubble) {
        bubble.setAttribute('data-visible', 'false');
        hideBackdrop();
    }

    // --- Event handler ---

    function handleNarrate(event) {
        var bubble = getBubble();
        if (!bubble) return;

        var expectedEvent = bubble.getAttribute('data-event') || 'tour:narrate';
        if (event.type !== expectedEvent) return;

        var detail = event.detail || {};

        // Empty message is a signal to hide the bubble
        if (!detail.text) {
            hideBubble(bubble);
            return;
        }

        updateBubbleContent(bubble, detail);
        showBubble(bubble);
        positionBubble(bubble, detail);
    }

    // --- Listeners ---

    document.addEventListener('tour:narrate', handleNarrate);

    document.addEventListener('tour:hide', function() {
        var bubble = getBubble();
        if (bubble) hideBubble(bubble);
    });

    // Expose for tests
    if (!window.djust) window.djust = {};
    window.djust._tutorialBubble = {
        handleNarrate: handleNarrate,
        getBubble: getBubble,
        showBubble: showBubble,
        hideBubble: hideBubble,
    };
})();
