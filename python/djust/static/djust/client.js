// djust - WebSocket + HTTP Fallback Client

// ============================================================================
// Global Namespace
// ============================================================================

// Create djust namespace at the top to ensure it's available for all exports
window.djust = window.djust || {};

// ============================================================================
// API prefix resolution (#987) — FORCE_SCRIPT_NAME / sub-path mount support
// ============================================================================
// Resolves window.djust.apiPrefix once at bootstrap. Priority:
//   1. Explicit global (set BEFORE client.js loads) — highest.
//   2. <meta name="djust-api-prefix" content="..."> emitted by the
//      {% djust_client_config %} template tag.
//   3. Compile-time default '/djust/api/'.
//
// Companion helper: djust.apiUrl(path) joins prefix + path with slash
// normalization. Used by 48-server-functions.js (djust.call) and will
// be the canonical API URL builder for future client modules.
(function initApiPrefix() {
    // 1. Explicit override wins — developer set it manually before the
    //    bundle loaded (rare, but it's how integrators inject custom
    //    behaviour without patching client.js).
    //
    //    Backward-compat note: an explicit empty string ('') is treated
    //    as "use default" because the meta-tag fallback below also uses
    //    `prefix || '/djust/api/'`. Integrators who want to genuinely
    //    disable the prefix should set a non-empty sentinel like '/'.
    if (typeof window.djust.apiPrefix !== 'undefined' && window.djust.apiPrefix !== null) {
        return;
    }
    // 2. Meta tag emitted by {% djust_client_config %}. reverse()-derived
    //    so it honors FORCE_SCRIPT_NAME and api_patterns(prefix=...).
    let prefix = '';
    try {
        const meta = document.querySelector('meta[name="djust-api-prefix"]');
        if (meta) {
            const raw = meta.getAttribute('content');
            if (raw) prefix = raw.trim();
        }
    } catch (_) { /* SSR / detached DOM — fall through to default */ }
    // 3. Compile-time default.
    window.djust.apiPrefix = prefix || '/djust/api/';
})();

// Helper: join the configured API prefix with a relative path, normalizing
// slashes so '/prefix/' + '/path' doesn't produce '/prefix//path'. Callers
// pass the portion AFTER the prefix; this helper guarantees exactly one
// slash at the junction regardless of whether either side carries one.
//
// Absolute-URL note: this helper does NOT special-case absolute URLs. A
// call like apiUrl('https://evil.com/') would return
// '/djust/api/https://evil.com/' — the concatenation is naive by design.
// All current callers pass relative paths built from encodeURIComponent()
// segments (see 48-server-functions.js), so an attacker-controlled
// absolute URL cannot reach this helper. If a future caller derives
// `path` from user input, it MUST validate or encode it first.
window.djust.apiUrl = function apiUrl(path) {
    const raw = window.djust.apiPrefix || '/djust/api/';
    const normalizedPrefix = raw.endsWith('/') ? raw : raw + '/';
    const p = path == null ? '' : String(path);
    const normalizedPath = p.startsWith('/') ? p.slice(1) : p;
    return normalizedPrefix + normalizedPath;
};

// ============================================================================
// SSE prefix resolution (#992) — same pattern as apiPrefix, different meta tag
// ============================================================================
// Resolves window.djust.ssePrefix once at bootstrap. Priority matches
// apiPrefix: explicit global > <meta name="djust-sse-prefix"> > '/djust/'.
// Used by 03b-sse.js for EventSource + event POST URL construction.
(function initSsePrefix() {
    if (typeof window.djust.ssePrefix !== 'undefined' && window.djust.ssePrefix !== null) {
        return;
    }
    let prefix = '';
    try {
        const meta = document.querySelector('meta[name="djust-sse-prefix"]');
        if (meta) {
            const raw = meta.getAttribute('content');
            if (raw) prefix = raw.trim();
        }
    } catch (_) { /* SSR / detached DOM — fall through to default */ }
    window.djust.ssePrefix = prefix || '/djust/';
})();

// Mirror of apiUrl() for the SSE prefix. Same slash-normalization rules,
// same absolute-URL caveat (callers must encode/validate user input).
window.djust.sseUrl = function sseUrl(path) {
    const raw = window.djust.ssePrefix || '/djust/';
    const normalizedPrefix = raw.endsWith('/') ? raw : raw + '/';
    const p = path == null ? '' : String(path);
    const normalizedPath = p.startsWith('/') ? p.slice(1) : p;
    return normalizedPrefix + normalizedPath;
};

// ============================================================================
// djLog: debug-gated console.log (#761)
// ============================================================================
// Per djust/CLAUDE.md: "No console.log in JS without if (globalThis.djustDebug)
// guard". Rather than sprinkle that conditional at every callsite, client.js
// uses djLog which checks the flag once per call. Tree-shakes nothing (we
// still evaluate the args in the call), but at least the console stays clean
// when djustDebug is false.
//
// console.warn / console.error are NOT guarded — those indicate real problems
// and should be visible in prod.
window.djLog = function djLog(...args) {
    if (globalThis.djustDebug) console.log(...args);
};

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

    // Reset scoped delegation so new page's scoped listeners get registered
    if (typeof _scopedDelegationInstalled !== 'undefined') {
        _scopedDelegationInstalled = false;
    }
    if (typeof _scopedRegistry !== 'undefined') {
        _scopedRegistry.clear();
    }

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
                djLog(`[LiveView:cache] Cached patches: ${cacheKey} (TTL: ${ttl}s)`);
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
            // Sticky LiveViews (Phase B): this path handles ROOT patches
            // only. Sticky-targeted patches arrive via the ``sticky_update``
            // frame and are routed through 45-child-view.js's
            // ``handleStickyUpdate`` with a scoped ``rootEl``. No ambiguity
            // here — calling applyPatches(data.patches) with the default
            // null rootEl is correct for the root view.
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
            djLog('[LiveView] Keeping loading state — async work pending');
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

        // Close WebSocket if open. For CONNECTING state, just null the
        // reference — calling close() on a CONNECTING socket triggers a
        // noisy "closed before established" error in the console (#732).
        if (this.ws) {
            if (this.ws.readyState === WebSocket.OPEN) {
                this.ws.close();
                if (globalThis.djustDebug) console.log('[LiveView] WebSocket closed');
            } else if (this.ws.readyState === WebSocket.CONNECTING) {
                // Let the connection attempt complete and fail silently
                // (onerror handler checks _intentionalDisconnect)
                if (globalThis.djustDebug) console.log('[LiveView] WebSocket still connecting — will close on connect');
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
                // Public CustomEvent consumed by dj-track-static (v0.6.0)
                // and available to application code that wants to react
                // to reconnect without patching internal WS state.
                document.dispatchEvent(new CustomEvent('djust:ws-reconnected'));
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

            // Sticky LiveViews (Phase B): abnormal close invalidates
            // any detached sticky subtrees. The server will re-mount
            // sticky views from scratch on reconnect (new session,
            // new instances), so stash entries from the dead
            // connection cannot reattach — they'd only surface as
            // ``no-slot`` unmount events on the next navigation, or
            // worse, shadow a freshly re-mounted sticky. Clear the
            // stash before the reconnect attempt runs.
            if (window.djust && window.djust.stickyPreserve && window.djust.stickyPreserve.clearStash) {
                window.djust.stickyPreserve.clearStash();
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
            // Suppress error when disconnect() was called intentionally
            // (e.g. TurboNav navigation while WS is still connecting)
            if (this._intentionalDisconnect) return;
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

                // Fix #1 — stash server-emitted public view state so the
                // state-snapshot capture on next before-navigate has
                // something to serialize. The server includes
                // ``public_state`` only when ``enable_state_snapshot``
                // is True on the view class; non-opt-in views never
                // have state cached by the SW.
                if (data.public_state && typeof data.public_state === 'object' && data.view) {
                    if (!window.djust._clientState) window.djust._clientState = {};
                    window.djust._clientState[data.view] = data.public_state;
                }

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
                    // Sticky LiveViews (Phase C Fix F1): the skipMountHtml
                    // branch is used when turbo-nav has pre-rendered the
                    // HTML (HTTP GET first). It was previously missing the
                    // post-mount sticky reattach — unreachable from
                    // ``live_redirect_mount`` today, but a foot-gun the
                    // moment a future path sets skipMountHtml=true on a
                    // redirect. Same no-op guard as the normal branch
                    // when stickyStash is empty.
                    if (window.djust.stickyPreserve && window.djust.stickyPreserve.reattachStickyAfterMount) {
                        window.djust.stickyPreserve.reattachStickyAfterMount();
                    }
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
                    // Fix #3 — cache the mount HTML for fast back-nav
                    // paint. Non-invasive: only runs when the SW bridge
                    // is present and we actually have HTML from the
                    // server (skipped when the client used pre-rendered
                    // HTTP content).
                    try {
                        if (window.djust && window.djust._sw && typeof window.djust._sw.cacheVdom === 'function') {
                            const cacheUrl = (typeof window !== 'undefined' && window.location)
                                ? window.location.pathname
                                : '/';
                            window.djust._sw.cacheVdom(cacheUrl, data.html, typeof data.version === 'number' ? data.version : 0);
                        }
                    } catch (_e) { /* best-effort cache put */ }
                    // No pre-rendered content - use server HTML directly
                    if (hasDataDjAttrs) {
                        if (globalThis.djustDebug) console.log('[LiveView] Hydrating DOM with dj-id attributes for reliable patching');
                    }
                    // Sticky LiveViews (Phase B): select a container that
                    // is NOT a sticky subtree root. When the old view is
                    // still in the DOM just before its innerHTML is
                    // replaced, a naive ``[dj-view]`` pick could return
                    // the sticky itself. ``[dj-sticky-root]`` is set by
                    // the server template tag on sticky wrappers —
                    // exclude them from container selection.
                    let container = document.querySelector('[dj-view]:not([dj-sticky-root])');
                    if (!container) {
                        container = document.querySelector('[dj-root]');
                    }
                    if (container) {
                        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                        container.innerHTML = data.html;
                        // Post-mount sticky reattach (Phase B). No-op
                        // when stickyStash is empty.
                        if (window.djust.stickyPreserve && window.djust.stickyPreserve.reattachStickyAfterMount) {
                            window.djust.stickyPreserve.reattachStickyAfterMount();
                        }
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

            case 'mount_batch': {
                // Mount-batch response (v0.6.0) — carries N per-view payloads.
                // Apply each to [data-djust-target="<target_id>"] within a
                // [dj-view] element, track per-target VDOM versions, and run
                // reinitAfterDOMUpdate() ONCE after the batch is applied.
                this.viewMounted = true;
                const views = Array.isArray(data.views) ? data.views : [];
                const failed = Array.isArray(data.failed) ? data.failed : [];
                if (!window.djust._clientVdomVersions) {
                    window.djust._clientVdomVersions = {};
                }
                for (const entry of views) {
                    const targetId = entry && entry.target_id;
                    if (!targetId) continue;
                    const container = document.querySelector(
                        '[dj-view][data-djust-target="' + CSS.escape(String(targetId)) + '"]'
                    );
                    if (!container) {
                        if (globalThis.djustDebug) {
                            console.warn('[LiveView] mount_batch: target not found: %s', String(targetId));
                        }
                        continue;
                    }
                    if (typeof entry.html === 'string') {
                        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                        container.innerHTML = entry.html;
                    }
                    if (typeof entry.version === 'number') {
                        window.djust._clientVdomVersions[targetId] = entry.version;
                    }
                }
                for (const f of failed) {
                    if (globalThis.djustDebug) {
                        console.warn('[LiveView] mount_batch failed: %s %o', String(f.view || ''), f);
                    }
                }
                // Fix #4 — forward any navigate entries emitted by
                // on_mount redirect hooks to the navigation dispatcher.
                const navigates = Array.isArray(data.navigate) ? data.navigate : [];
                for (const nav of navigates) {
                    if (!nav || typeof nav.to !== 'string') continue;
                    if (window.djust.navigation && typeof window.djust.navigation.handleNavigation === 'function') {
                        window.djust.navigation.handleNavigation({
                            type: 'navigation',
                            action: 'live_redirect',
                            path: nav.to,
                        });
                    } else if (typeof window !== 'undefined' && window.location) {
                        // Fallback — hard navigation if the dispatcher
                        // isn't wired (non-LiveView pages).
                        window.location.href = nav.to;
                    }
                }
                if (views.length > 0) {
                    reinitAfterDOMUpdate();
                    window.djust._mountReady = true;
                }
                break;
            }

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
                        djLog('[LiveView] Buffered %s patch (v%s) — waiting for %d pending event(s)', String(data.source), String(data.version), _pendingEventRefs.size);
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
                        djLog('[LiveView] Flushing ' + _tickBuffer.length + ' buffered patches');
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
                    djLog('[LiveView] DOM recovered via morph, version: %s', String(data.version));
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

            case 'upload_resumed':
                // Resumable upload — server replied to upload_resume
                // with the last accepted offset. Dispatched to the
                // pending resume promise inside 15-uploads.js. (#821)
                if (window.djust.uploads && window.djust.uploads.handleResumed) {
                    window.djust.uploads.handleResumed(data);
                }
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

            case 'child_update':
                // Sticky LiveViews Phase A: VDOM patch frame targeted at
                // a specific child view via view_id. Routed to 45-child-view.js.
                if (window.djust.childView && window.djust.childView.handleChildUpdate) {
                    window.djust.childView.handleChildUpdate(data);
                }
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'sticky_hold':
                // Sticky LiveViews Phase B: authoritative list of
                // preserved sticky view_ids. Emitted BEFORE the
                // companion ``mount`` frame so the client can drop
                // stash entries the server is NOT preserving
                // (permission revoked, no matching slot, etc.) before
                // the new HTML is applied.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.reconcileStickyHold) {
                    window.djust.stickyPreserve.reconcileStickyHold(data.views || []);
                }
                break;

            case 'sticky_update':
                // Sticky LiveViews Phase B: VDOM patch frame scoped to
                // a sticky subtree. Applies via the new
                // ``applyPatches(patches, rootEl)`` variant — the
                // sticky's dj-id namespace is independent of the
                // parent's, so doc-wide lookups would be incorrect.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.handleStickyUpdate) {
                    window.djust.stickyPreserve.handleStickyUpdate(data);
                }
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

            case 'layout':
                // Runtime layout switch (v0.6.0) — set_layout() on the server.
                if (window.djust.djLayout && typeof window.djust.djLayout.applyLayout === 'function') {
                    window.djust.djLayout.applyLayout(data);
                }
                break;

            case 'reload':
                // Hot reload: file changed, refresh the page
                window.location.reload();
                break;

            case 'hvr-applied':
                // Hot View Replacement (v0.6.1) — server swapped __class__
                // on the live view instance without losing state. Surface
                // the event so tests/app code can observe it, then show
                // the dev indicator toast.
                try {
                    document.dispatchEvent(new CustomEvent('djust:hvr-applied', {
                        detail: data,
                        bubbles: true,
                    }));
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] hvr-applied dispatch failed', e);
                    }
                }
                if (globalThis.djust && globalThis.djust.hvr) {
                    globalThis.djust.hvr.showIndicator(data);
                }
                break;
            case 'time_travel_state':
                // Time-travel debugging (v0.6.1) — server acknowledges
                // a completed jump with the new cursor/history_len.
                // The debug panel listens for this event to update its
                // timeline UI; the main client just fans out so non-
                // debug-panel consumers can observe.
                try {
                    document.dispatchEvent(new CustomEvent('djust:time-travel-state', {
                        detail: data,
                        bubbles: true,
                    }));
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel-state dispatch failed', e);
                    }
                }
                break;
            case 'time_travel_event':
                // Time-travel debugging (v0.6.1 Fix #3) — server pushes
                // each recorded snapshot so the debug panel's timeline
                // populates incrementally. Payload shape:
                //   { type: 'time_travel_event', entry: {...}, history_len: N }
                // Fanned out as a CustomEvent so the debug-panel (and
                // any other listener) can append the entry.
                try {
                    document.dispatchEvent(new CustomEvent('djust:time-travel-event', {
                        detail: data,
                        bubbles: true,
                    }));
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel-event dispatch failed', e);
                    }
                }
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
        // Resolved via window.djust.sseUrl() — honors FORCE_SCRIPT_NAME and
        // custom mount prefixes (closes #992). Default '/djust/' preserved
        // for deployments that don't use {% djust_client_config %}.
        this.sseBaseUrl = window.djust.sseUrl
            ? window.djust.sseUrl(`sse/${this.sessionId}/`)
            : `/djust/sse/${this.sessionId}/`;

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
            djLog(`[StateBus] Set: ${key} =`, value, `(was:`, oldValue, `)`);
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
            djLog(`[StateBus] Subscribed to: ${key} (${this.subscribers.get(key).size} subscribers)`);
        }
        return () => {
            const subs = this.subscribers.get(key);
            if (subs) {
                subs.delete(callback);
                if (globalThis.djustDebug) {
                    djLog(`[StateBus] Unsubscribed from: ${key} (${subs.size} remaining)`);
                }
            }
        };
    }

    notify(key, newValue, oldValue) {
        const callbacks = this.subscribers.get(key) || new Set();
        if (callbacks.size > 0 && globalThis.djustDebug) {
            djLog(`[StateBus] Notifying ${callbacks.size} subscribers of: ${key}`);
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
            djLog('[StateBus] Cleared all state');
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
                    djLog(`[DraftMode] Saved draft: ${draftKey}`, data);
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
                djLog(`[DraftMode] Loaded draft: ${draftKey} (${age}s old)`, draftData.data);
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
                djLog(`[DraftMode] Cleared draft: ${draftKey}`);
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
            djLog(`[DraftMode] Cleared all ${keys.length} drafts`);
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
        djLog('[DraftMode] Restored form data:', data);
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

// ============================================================================
// Scoped Event Delegation (dj-window-*, dj-document-*)
// ============================================================================
// Install ONE listener per event type on window/document. When the event fires,
// dispatch to registered declaring elements. Elements are registered by scanning
// the DOM once at install time. Re-scanning happens on TurboNav navigation or
// when bindLiveViewEvents() is called after DOM changes.

// Registry: Map<"prefix:evtType", Set<{element, attrName, handler, requiredKey}>>
var _scopedRegistry = new Map();
var _scopedDelegationInstalled = false;

/**
 * Scan the DOM for elements with dj-window-[event] / dj-document-[event] attributes
 * and register them in the scoped registry for delegated dispatch.
 */
function _scanScopedElements() {
    var scopedPrefixes = ['dj-window-', 'dj-document-'];
    var scopedEventTypes = ['keydown', 'keyup', 'click', 'scroll', 'resize'];
    var root = document.querySelector('[dj-view]') || document.querySelector('[dj-root]') || document;

    // Clear stale entries (elements removed from DOM)
    _scopedRegistry.forEach(function(entries, key) {
        entries.forEach(function(entry) {
            if (!document.contains(entry.element)) {
                entries.delete(entry);
            }
        });
    });

    // Scan ALL elements in the LiveView root (only way to find dotted attr names).
    // This runs once at mount and on TurboNav — NOT after every patch.
    var allElements = root.querySelectorAll('*');
    allElements.forEach(function(element) {
        for (var ai = 0; ai < element.attributes.length; ai++) {
            var attrName = element.attributes[ai].name;
            for (var pi = 0; pi < scopedPrefixes.length; pi++) {
                var prefix = scopedPrefixes[pi];
                if (!attrName.startsWith(prefix)) continue;

                // Find which event type this is (e.g. 'keydown' from 'dj-window-keydown.escape')
                var rest = attrName.slice(prefix.length); // 'keydown' or 'keydown.escape'
                var evtType = null;
                for (var ei = 0; ei < scopedEventTypes.length; ei++) {
                    if (rest === scopedEventTypes[ei] || rest.startsWith(scopedEventTypes[ei] + '.')) {
                        evtType = scopedEventTypes[ei];
                        break;
                    }
                }
                if (!evtType) continue;

                var registryKey = prefix + evtType;
                if (!_scopedRegistry.has(registryKey)) {
                    _scopedRegistry.set(registryKey, new Set());
                }

                var suffix = rest.slice(evtType.length); // '' or '.escape'
                var requiredKey = suffix.startsWith('.') ? _normalizeKeyName(suffix.slice(1)) : null;
                var parsed = parseEventHandler(element.attributes[ai].value);

                // Check if already registered (prevent duplicates)
                var entries = _scopedRegistry.get(registryKey);
                var alreadyRegistered = false;
                entries.forEach(function(entry) {
                    if (entry.element === element && entry.attrName === attrName) {
                        alreadyRegistered = true;
                    }
                });
                if (alreadyRegistered) continue;

                entries.add({
                    element: element,
                    attrName: attrName,
                    parsed: parsed,
                    requiredKey: requiredKey,
                });
            }
        }
    });
}

/**
 * Install delegated listeners on window/document for scoped events.
 * Called once — listeners dispatch to the registry.
 */
function _installScopedDelegation() {
    if (_scopedDelegationInstalled) return;
    _scopedDelegationInstalled = true;

    // Scan DOM once at install time to populate the registry
    _scanScopedElements();

    var scopedTargets = [
        { prefix: 'dj-window-', target: window },
        { prefix: 'dj-document-', target: document },
    ];
    var scopedEventTypes = ['keydown', 'keyup', 'click', 'scroll', 'resize'];

    for (var ti = 0; ti < scopedTargets.length; ti++) {
        var prefix = scopedTargets[ti].prefix;
        var target = scopedTargets[ti].target;

        for (var ei = 0; ei < scopedEventTypes.length; ei++) {
            var evtType = scopedEventTypes[ei];
            if (target === document && (evtType === 'scroll' || evtType === 'resize')) continue;

            (function(prefix, target, evtType) {
                var registryKey = prefix + evtType;

                target.addEventListener(evtType, function(e) {
                    var entries = _scopedRegistry.get(registryKey);
                    if (!entries || entries.size === 0) return;

                    entries.forEach(function(entry) {
                        // Skip elements removed from DOM
                        if (!document.contains(entry.element)) return;

                        // Key filtering
                        if (entry.requiredKey && e.key !== entry.requiredKey) return;

                        var params = extractTypedParams(entry.element);
                        addEventContext(params, entry.element);

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

                        if (entry.parsed.args.length > 0) {
                            params._args = entry.parsed.args;
                        }

                        handleEvent(entry.parsed.name, params);
                    });
                }, evtType === 'scroll' || evtType === 'resize' ? { passive: true } : false);
            })(prefix, target, evtType);
        }
    }
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
const _keydownRateLimitState = new WeakMap();
const _keyupRateLimitState = new WeakMap();

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
        djLog(`[LiveView] dj-change handler: value="${value}", params=`, params);
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
 * Idempotent — uses AbortController to tear down old listeners before
 * installing new ones, preventing double-firing after TurboNav navigation.
 * @param {HTMLElement} root - The LiveView root element to delegate from
 */
function installDelegatedListeners(root) {
    // Tear down previous delegated listeners (if any) to prevent double-firing
    // after TurboNav morphs the page content while preserving the root element.
    if (root._djustDelegateAbort) {
        root._djustDelegateAbort.abort();
    }
    var controller = new AbortController();
    root._djustDelegateAbort = controller;
    var opts = { signal: controller.signal };

    // Helper: addEventListener with abort signal for clean teardown
    function on(event, handler) {
        root.addEventListener(event, handler, opts);
    }

    // click → dj-copy (client-only) first, then dj-click
    on('click', function(e) {
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
    on('submit', function(e) {
        var submitEl = e.target.closest('[dj-submit]');
        if (submitEl) {
            _handleDjSubmit(submitEl, e);
        }
    });

    // change → dj-change
    on('change', function(e) {
        var changeEl = e.target.closest('[dj-change]');
        if (changeEl) {
            // Rate-limit per element using WeakMap
            var rawHandler = function(ev) { return _handleDjChange(changeEl, ev); };
            var wrapped = _getOrCreateRateLimitedHandler(_changeRateLimitState, changeEl, 'change', rawHandler);
            wrapped(e);
        }
    });

    // input → dj-input (with smart rate limiting)
    on('input', function(e) {
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
    on('keydown', function(e) {
        var keyEl = e.target.closest('[dj-keydown]');
        if (keyEl) {
            var rawHandler = function(ev) { return _handleDjKeyboard(keyEl, ev, 'keydown'); };
            var wrapped = _getOrCreateRateLimitedHandler(_keydownRateLimitState, keyEl, 'keydown', rawHandler);
            wrapped(e);
        }
    });

    // keyup → dj-keyup (separate WeakMap from keydown to avoid handler collision)
    on('keyup', function(e) {
        var keyEl = e.target.closest('[dj-keyup]');
        if (keyEl) {
            var rawHandler = function(ev) { return _handleDjKeyboard(keyEl, ev, 'keyup'); };
            var wrapped = _getOrCreateRateLimitedHandler(_keyupRateLimitState, keyEl, 'keyup', rawHandler);
            wrapped(e);
        }
    });

    // paste → dj-paste
    on('paste', function(e) {
        var pasteEl = e.target.closest('[dj-paste]');
        if (pasteEl) {
            _handleDjPaste(pasteEl, e);
        }
    });

    // focusin → dj-focus (focusin bubbles, focus doesn't)
    on('focusin', function(e) {
        var focusEl = e.target.closest('[dj-focus]');
        if (focusEl) {
            _handleDjFocus(focusEl, e);
        }
    });

    // focusout → dj-blur (focusout bubbles, blur doesn't)
    on('focusout', function(e) {
        var blurEl = e.target.closest('[dj-blur]');
        if (blurEl) {
            _handleDjBlur(blurEl, e);
        }
    });
}

function bindLiveViewEvents(scope) {
    const root = scope || getLiveViewRoot() || document;

    // Install delegated listeners on the LiveView root element.
    // Only install on actual [dj-view]/[dj-root] elements, NOT on document.body
    // fallback — body persists across TurboNav page swaps, causing duplicate
    // events when navigating away from a LiveView page and back.
    const liveRoot = document.querySelector('[dj-view]') || document.querySelector('[dj-root]');
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

    // --- Feature 1: dj-window-* and dj-document-* event scoping ---
    // Delegated approach: install ONE listener per event type on window/document.
    // When the event fires, find declaring elements via querySelectorAll('[dj-window-keydown]')
    // and dispatch to matching handlers. This avoids querySelectorAll('*') entirely.
    _installScopedDelegation();

    // Sweep orphaned scoped listeners (click-away, shortcut) for elements
    // removed from DOM by conditional rendering
    _sweepOrphanedScopedListeners();

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
//
// Sticky LiveViews (Phase B) invariant: the parent view's [dj-view] is
// stamped in DOM order BEFORE its sticky child (the parent template tag
// runs before {% live_render %} resolves), so the first matching
// [dj-view] in document order is the parent. Calling code that needs to
// operate on the PARENT (mainline patch application, form helpers,
// etc.) relies on this ordering and MUST NOT be changed to select
// [dj-sticky-root] subtrees. Sticky targets are reached via the scoped
// applier in 45-child-view.js, NOT via getLiveViewRoot().
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
    // Extract any new colocated hook definitions (<script type="djust/hook">)
    // from the freshly-patched DOM BEFORE we mount/update hooks so definitions
    // are visible to mountHooks().
    if (window.djust.extractColocatedHooks) {
        window.djust.extractColocatedHooks(scope || document);
    }
    if (window.djust.mountHooks) {
        // updateHooks scans for [dj-hook] — scope it too
        const hookRoot = scope || getLiveViewRoot();
        hookRoot.querySelectorAll('[dj-hook]').forEach(el => {
            // Delegate to the hook system's per-element logic
            if (window.djust.mountHooks) window.djust.mountHooks(el);
        });
    }
    updateHooks();

    // dj-virtual / dj-viewport-*: re-scan after VDOM morph so new containers
    // get observers and existing ones pick up new first/last children. For
    // dj-virtual, existing containers must ALSO be refreshed so stream-
    // appended items render (initVirtualLists short-circuits on already-
    // tracked containers; refreshVirtualList is the re-window path).
    const reinitScope = scope || document;
    if (window.djust.initVirtualLists) window.djust.initVirtualLists(reinitScope);
    if (window.djust.refreshVirtualList) {
        reinitScope.querySelectorAll('[dj-virtual]').forEach((el) => {
            window.djust.refreshVirtualList(el);
        });
    }
    if (window.djust.initInfiniteScroll) window.djust.initInfiniteScroll(reinitScope);

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

        // Shorthand form: ``dj-loading="event_name"`` (v0.5.1). Treat as an
        // implicit ``.show`` modifier — the element is hidden by default and
        // becomes visible (display:block) while the named event is in-flight.
        // Auto-hide on register so authors don't need inline ``style="display:none"``.
        if (element.hasAttribute('dj-loading')) {
            modifiers.push({ type: 'show' });
            // Record the ORIGINAL display (if the author set one explicitly,
            // use it for the visible phase); then force display:none so the
            // element starts hidden.
            const priorDisplay = element.style.display;
            originalState.display = 'none';
            originalState.visibleDisplay = (priorDisplay && priorDisplay !== 'none') ? priorDisplay : 'block';
            element.style.display = 'none';
        }

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
                djLog(`[Loading] Registered element for "${eventName}":`, modifiers);
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
            '[dj-loading]',
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
            // 1. Shorthand: dj-loading="event_name" (v0.5.1)
            // 2. Explicit: dj-loading.for="event_name" (works on any element)
            // 3. Implicit: from the element's own dj-click, dj-submit, etc.
            let eventName = element.getAttribute('dj-loading') || element.getAttribute('dj-loading.for');
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
            djLog(`[Loading] Scanned ${this.registeredElements.size} elements with dj-loading attributes`);
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
                djLog(`[Loading] triggerElement:`, triggerElement);
                djLog(`[Loading] hasAttribute('dj-loading.disable'):`, hasDisable);
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
            djLog(`[Loading] Started: ${eventName}`);
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
            djLog(`[Loading] Stopped: ${eventName}`);
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
        djLog(`[LiveView] Handling event: ${eventName}`, params);
    }

    // Extract client-only properties before sending to server.
    // These are DOM references or internal flags that cannot be JSON-serialized
    // and would corrupt the params payload (e.g., HTMLElement objects serialize
    // as objects with numeric-indexed children that clobber form field data).
    const triggerElement = params._targetElement;
    const skipLoading = params._skipLoading;

    // v0.7.0 — Activity gate. Drop the event client-side when ANY
    // ancestor activity wrapper is hidden and not eager. The nested
    // case matters: ``closest('[data-djust-activity]')`` alone returns
    // the NEAREST wrapper, which for ``<outer hidden><inner visible>``
    // would be the INNER one — and the inner being visible would
    // incorrectly dispatch the event even though the user can't see it
    // (the outer hides the whole subtree).
    //
    // Selector discipline is the same as 12-vdom-patch.js so the patch
    // gate and the event gate stay in lock-step. We also stamp the
    // resolved ``_activity`` name (nearest wrapper) on the outbound
    // payload so the server can route / defer per-activity on the rare
    // race where the client gate is stale (mid-morph hide).
    let _activityName = null;
    if (triggerElement && triggerElement.closest) {
        // Drop if any hidden non-eager ancestor exists — correct under nesting.
        const hiddenAncestor = triggerElement.closest(
            '[data-djust-activity][hidden]:not([data-djust-eager="true"])'
        );
        if (hiddenAncestor) {
            if (globalThis.djustDebug) {
                console.log(
                    '[LiveView:activity] drop event in hidden activity:',
                    eventName,
                    hiddenAncestor.getAttribute('data-djust-activity')
                );
            }
            // Match the contract of an early-return in cached path:
            // stop loading state if we started any, then bail.
            if (!skipLoading) globalLoadingManager.stopLoading(eventName, triggerElement);
            return;
        }
        // For routing: stamp _activity with the nearest activity wrapper
        // the trigger lives inside (regardless of its own visibility — by
        // this point we've already confirmed no hidden ancestor exists).
        const activityAncestor = triggerElement.closest('[data-djust-activity]');
        if (activityAncestor) {
            _activityName = activityAncestor.getAttribute('data-djust-activity') || null;
        }
    }

    // Build clean server params (strip underscore-prefixed internal properties)
    const serverParams = {};
    for (const key of Object.keys(params)) {
        if (key === '_targetElement' || key === '_optimisticUpdateId' || key === '_skipLoading' || key === '_djTargetSelector') {
            continue;
        }
        serverParams[key] = params[key];
    }
    // Preserve the resolved activity name so the server can route / defer
    // per-activity. Only attached when we actually found a wrapper, so
    // the payload stays compact for non-activity events.
    if (_activityName) {
        serverParams._activity = _activityName;
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
            djLog(`[LiveView:cache] Cache hit: ${cacheKey}`);
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
        djLog(`[LiveView:cache] Cache miss: ${cacheKey}`);
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
                    djLog(`[LiveView:cache] Cleaned up stale pending request: ${cacheRequestId}`);
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
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root. When
 *   provided, the positional-fallback index is computed relative to this
 *   root (used by scoped sticky patch application so the focus index for
 *   a child view doesn't collide with the parent view's positional
 *   indices).
 * @returns {Object|null} Saved focus state, or null if no form element is focused.
 */
function saveFocusState(rootEl = null) {
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
        // Positional: index among same-tag siblings in the nearest dj-view.
        // Sticky patch applier passes rootEl so the index is scoped to the
        // sticky subtree, not the whole document.
        state.findBy = 'index';
        const root = rootEl || active.closest('[dj-view]') || document.body;
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
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root. When
 *   provided, the lookup queries against ``rootEl`` instead of
 *   ``document``, so sticky/child patches don't resurrect a matching
 *   id-carrying element outside their own subtree.
 */
function restoreFocusState(state, rootEl = null) {
    if (!state) return;

    const scope = rootEl || document;
    let el = null;
    if (state.findBy === 'id') {
        el = (rootEl && rootEl.querySelector)
            ? rootEl.querySelector('#' + CSS.escape(state.key))
            : document.getElementById(state.key);
    } else if (state.findBy === 'name') {
        el = scope.querySelector(`[name="${CSS.escape(state.key)}"]`);
    } else if (state.findBy === 'dj-id') {
        el = scope.querySelector(`[dj-id="${CSS.escape(state.key)}"]`);
    } else {
        // Positional fallback — scoped to rootEl when provided.
        const root = rootEl || document.querySelector('[dj-view]') || document.body;
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
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root. When
 *   provided, both the dj-id lookup and the path traversal are scoped to
 *   ``rootEl``. Sticky LiveViews (Phase B) pass the sticky subtree here
 *   so a child's dj-id doesn't match the parent's.
 * @returns {Node|null} - Found node or null
 */
function getNodeByPath(path, djustId = null, rootEl = null) {
    // Strategy 1: ID-based resolution (fast, reliable)
    if (djustId) {
        const scope = rootEl || document;
        const byId = scope.querySelector(`[dj-id="${CSS.escape(djustId)}"]`);
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
    let node = rootEl || getLiveViewRoot();

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
        saved = { tag: active.tagName.toLowerCase(), originalName: active.name };
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
            if (node.nodeType === Node.ELEMENT_NODE
                && globalThis.djust
                && typeof globalThis.djust.maybeDeferRemoval === 'function'
                && globalThis.djust.maybeDeferRemoval(node)) {
                continue;
            }
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
        if (globalThis.djust && typeof globalThis.djust.maybeDeferRemoval === 'function'
            && existing.nodeType === Node.ELEMENT_NODE
            && existing.hasAttribute('dj-remove')) {
            // If a removal is already pending for this element, the replacement
            // node was inserted by the prior patch — skip to avoid duplicates.
            const alreadyPending = globalThis.djust.djRemove
                && globalThis.djust.djRemove._pendingRemovals
                && globalThis.djust.djRemove._pendingRemovals.has(existing);
            if (alreadyPending) {
                return;
            }
            const newNode = desired.cloneNode(true);
            existing.parentNode.insertBefore(newNode, existing);
            if (globalThis.djust.maybeDeferRemoval(existing)) {
                return;
            }
            // Declined — fall through to the normal replace (newNode
            // already inserted, drop duplicate first).
            existing.parentNode.removeChild(newNode);
        }
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
    // Also: attributes listed in dj-ignore-attrs are client-owned — the morph
    // path must not remove or overwrite them (mirrors the SetAttr guard at
    // line ~1199, which only covered individual attribute patches).
    const isCanvas = existing.tagName === 'CANVAS';
    const _isIgnored = (globalThis.djust && typeof globalThis.djust.isIgnoredAttr === 'function')
        ? globalThis.djust.isIgnoredAttr
        : null;
    for (let i = existing.attributes.length - 1; i >= 0; i--) {
        const name = existing.attributes[i].name;
        if (!desired.hasAttribute(name)) {
            if (isCanvas && (name === 'width' || name === 'height')) continue;
            if (_isIgnored && _isIgnored(existing, name)) continue;
            existing.removeAttribute(name);
        }
    }
    // Set/update attributes from desired
    for (const attr of desired.attributes) {
        if (_isIgnored && _isIgnored(existing, attr.name)) continue;
        if (existing.getAttribute(attr.name) !== attr.value) {
            existing.setAttribute(attr.name, attr.value);
        }
    }

    // --- Form element value sync ---
    const isFocused = document.activeElement === existing;
    // Skip value sync for focused inputs to preserve what the user is typing,
    // UNLESS the input's identity changed (different name = different field).
    const nameChanged = existing.getAttribute('name') !== desired.getAttribute('name');
    const skipValue = isFocused && !_isBroadcastUpdate && !nameChanged;

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
                            djLog(`[LiveView:dj-update] Appended #${newChild.id} to #${elementId}`);
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
                            djLog(`[LiveView:dj-update] Prepended #${newChild.id} to #${elementId}`);
                        }
                    }
                }
                break;
            }

            case 'ignore':
                // Don't update this element at all
                if (globalThis.djustDebug) {
                    djLog(`[LiveView:dj-update] Ignoring #${elementId}`);
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
                if (globalThis.djust
                    && typeof globalThis.djust.maybeDeferRemoval === 'function'
                    && globalThis.djust.maybeDeferRemoval(existing)) {
                    continue;
                }
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
function applySinglePatch(patch, rootEl = null) {
    // Use ID-based resolution (d field) with path as fallback.
    // rootEl is threaded in by the scoped applier (Sticky LiveViews
    // Phase B) so child / sticky patches don't resolve against the
    // parent view's dj-id namespace.
    const node = getNodeByPath(patch.path, patch.d, rootEl);
    // v0.7.0 — {% dj_activity %} gate. If the target node lives inside
    // a HIDDEN activity wrapper that is NOT eager, we intentionally
    // skip the subtree patch so local DOM state (form values, scroll
    // offsets, transient JS state) is preserved across show/hide
    // cycles. The server is the canonical source of visibility, so the
    // next render after the activity is shown will re-sync state.
    if (node && node.nodeType === 1 && node.closest) {
        const hiddenActivity = node.closest('[data-djust-activity][hidden]:not([data-djust-eager="true"])');
        if (hiddenActivity) {
            if (globalThis.djustDebug) {
                console.log('[LiveView:activity] skipping patch inside hidden activity:', hiddenActivity.getAttribute('data-djust-activity'), patch.type);
            }
            return true;
        }
    }
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
                if (node.nodeType === Node.ELEMENT_NODE
                    && globalThis.djust
                    && typeof globalThis.djust.maybeDeferRemoval === 'function'
                    && node.hasAttribute('dj-remove')) {
                    // If a removal is already pending for this element, the
                    // replacement node was inserted by the prior patch — skip
                    // to avoid duplicates.
                    const alreadyPending = globalThis.djust.djRemove
                        && globalThis.djust.djRemove._pendingRemovals
                        && globalThis.djust.djRemove._pendingRemovals.has(node);
                    if (alreadyPending) {
                        break;
                    }
                    node.parentNode.insertBefore(newNode, node);
                    if (globalThis.djust.maybeDeferRemoval(node)) {
                        break;
                    }
                    // Declined — drop the pre-inserted duplicate and fall
                    // through to the normal replace path.
                    node.parentNode.removeChild(newNode);
                }
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
                // dj-ignore-attrs: element opts out of server updates for this key
                // (e.g. <dialog dj-ignore-attrs="open">).
                if (globalThis.djust && typeof globalThis.djust.isIgnoredAttr === 'function' &&
                    globalThis.djust.isIgnoredAttr(node, attrKey)) {
                    if (globalThis.djustDebug) {
                        console.debug('[LiveView] Skipped SetAttr on ignored attr %s', attrKey);
                    }
                    break;
                }
                const attrVal = String(patch.value != null ? patch.value : '');
                if (attrKey === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                    if (document.activeElement !== node) {
                        node.value = attrVal;
                    }
                    node.setAttribute(attrKey, attrVal);
                } else if (attrKey === 'name' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA' || node.tagName === 'SELECT')) {
                    // Input name changed = different field. Clear the value
                    // so the old field's content doesn't leak into the new field.
                    node.setAttribute(attrKey, attrVal);
                    const serverValue = node.getAttribute('value') || '';
                    node.value = serverValue;
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
                        djLog('[LiveView] InsertChild redirected: non-option child into SELECT parent');
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
                    if (!wasTextNode
                        && child.nodeType === Node.ELEMENT_NODE
                        && globalThis.djust
                        && typeof globalThis.djust.maybeDeferRemoval === 'function'
                        && globalThis.djust.maybeDeferRemoval(child)) {
                        break;
                    }
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
 *
 * @param {Array} patches - VDOM patch list (server-authoritative).
 * @param {HTMLElement|null} [rootEl=null] — optional scoping root for
 *   the patch application. When null (the default path used by every
 *   pre-Phase-B caller), the live view root is resolved via
 *   ``getLiveViewRoot()`` exactly as before — zero regressions for
 *   top-level patches. When non-null (used by Sticky LiveViews Phase B
 *   and the now-wired Phase A child_update path), node lookups,
 *   focus save/restore, and the autofocus query are all scoped to
 *   ``rootEl`` so they cannot spill into or from another view's
 *   subtree.
 */
function applyPatches(patches, rootEl = null) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Save focus state before any DOM mutations (#559 follow-up: focus preservation)
    const focusState = saveFocusState(rootEl);
    const autofocusScope = rootEl || document;

    // Sort patches in 4-phase order for correct DOM mutation sequencing
    _sortPatches(patches);

    // For small patch sets, apply directly without batching overhead
    if (patches.length <= 10) {
        let failedCount = 0;
        const failedIndices = [];
        for (let _pi = 0; _pi < patches.length; _pi++) {
            if (!applySinglePatch(patches[_pi], rootEl)) {
                failedCount++;
                failedIndices.push(_pi);
            }
        }
        if (failedCount > 0) {
            console.error(`[LiveView] ${failedCount}/${patches.length} patches failed (indices: ${failedIndices.join(', ')})`);
            // Still handle autofocus even when some patches failed (#617)
            if (!focusState || !focusState.id) {
                const autoFocusEl = autofocusScope.querySelector('[autofocus]');
                if (autoFocusEl && document.activeElement !== autoFocusEl) {
                    autoFocusEl.focus();
                }
            }
            restoreFocusState(focusState, rootEl);
            return false;
        }
        // Note: updateHooks() and bindModelElements() are called by
        // reinitAfterDOMUpdate() in the response handler — not here,
        // to avoid double-scanning the DOM.
        // Handle autofocus on dynamically inserted elements (#617)
        // Browser only honors autofocus on initial page load, so we
        // manually focus the first element with autofocus after a patch.
        if (!focusState || !focusState.id) {
            const autoFocusEl = autofocusScope.querySelector('[autofocus]');
            if (autoFocusEl && document.activeElement !== autoFocusEl) {
                autoFocusEl.focus();
            }
        }
        restoreFocusState(focusState, rootEl);
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
            if (applySinglePatch(patch, rootEl)) {
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
                const parentNode = getNodeByPath(firstPatch.path, firstPatch.d, rootEl);

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
            if (applySinglePatch(patch, rootEl)) {
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
            const autoFocusEl = autofocusScope.querySelector('[autofocus]');
            if (autoFocusEl && document.activeElement !== autoFocusEl) {
                autoFocusEl.focus();
            }
        }
        restoreFocusState(focusState, rootEl);
        return false;
    }

    // Note: updateHooks() and bindModelElements() are called by
    // reinitAfterDOMUpdate() in the response handler — not here,
    // to avoid double-scanning the DOM.

    // Handle autofocus on dynamically inserted elements (#617)
    // Browser only honors autofocus on initial page load, so we
    // manually focus the first element with autofocus after a patch.
    if (!focusState || !focusState.id) {
        const autoFocusEl = autofocusScope.querySelector('[autofocus]');
        if (autoFocusEl && document.activeElement !== autoFocusEl) {
            autoFocusEl.focus();
        }
    }

    restoreFocusState(focusState, rootEl);
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
            djLog(`[LiveView:lazy] Registered element for lazy hydration (mode: ${lazyMode})`, element);
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

        // Mount-batch optimization (v0.6.0): when 2+ lazy views are
        // hydrating together, send one mount_batch WebSocket frame
        // instead of N separate mount frames. Opt out via
        // window.DJUST_USE_MOUNT_BATCH = false.
        const useBatch = (
            mounts.length >= 2
            && window.DJUST_USE_MOUNT_BATCH !== false
            && liveViewWS
            && typeof liveViewWS.sendMessage === 'function'
            && isWSConnected()
        );
        if (useBatch) {
            const viewEntries = [];
            const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
            mounts.forEach(({ element, viewPath }) => {
                const targetId = element.getAttribute('data-djust-target')
                    || element.id
                    || ('dj-target-' + Math.random().toString(36).slice(2, 10));
                if (!element.getAttribute('data-djust-target')) {
                    element.setAttribute('data-djust-target', targetId);
                }
                const hasContent = element.innerHTML && element.innerHTML.trim().length > 0;
                viewEntries.push({
                    view: viewPath,
                    params: urlParams,
                    url: window.location.pathname,
                    target_id: targetId,
                    has_prerendered: !!hasContent,
                });
                element.removeAttribute('dj-lazy');
                element.setAttribute('data-live-hydrated', 'true');
            });
            let clientTimezone = null;
            try {
                clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            } catch (_e) { /* tz detect failure — omit */ }
            liveViewWS.sendMessage({
                type: 'mount_batch',
                views: viewEntries,
                client_timezone: clientTimezone,
            });
            return;
        }

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

    // Extract colocated hook definitions from <script type="djust/hook"> tags
    // emitted by the {% colocated_hook %} template tag.  Must run BEFORE
    // mountHooks() so newly registered definitions are visible to the scan.
    if (window.djust.extractColocatedHooks) {
        window.djust.extractColocatedHooks(document);
    }

    // Mount dj-hook elements
    mountHooks();

    // Bind dj-model elements
    bindModelElements();

    // Initialize Draft Mode
    initDraftMode();

    // Scan and register dj-loading attributes
    globalLoadingManager.scanAndRegister();

    // Initialize virtual lists and infinite-scroll viewport observers (v0.5.0)
    if (window.djust.initVirtualLists) window.djust.initVirtualLists(document);
    if (window.djust.initInfiniteScroll) window.djust.initInfiniteScroll(document);

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

    // ========================================================================
    // Resumable upload persistence (ADR-010, #821)
    // ========================================================================
    //
    // When a slot is configured with resumable=True the client keeps a
    // small record in IndexedDB keyed by file "hint" (name + size +
    // lastModified — a poor-man's fingerprint that survives within a
    // single browser session without requiring full-file hashing on
    // upload start).
    //
    // On WS reconnect the client sends `upload_resume` with the saved
    // ref; server replies with bytes_received + chunks_received and
    // the client seeks past them. If IndexedDB is unavailable
    // (private mode, old browser) we fall back to in-memory only —
    // resume works within the same tab load but not across reloads.

    const RESUMABLE_DB_NAME = 'djust_uploads';
    const RESUMABLE_STORE_NAME = 'pending';
    const RESUMABLE_DB_VERSION = 1;
    let _resumableDbPromise = null;

    function _hasIndexedDB() {
        try {
            return typeof indexedDB !== 'undefined' && indexedDB !== null;
        } catch (_) {
            return false;
        }
    }

    /**
     * Open (or lazily create) the IndexedDB backing the resumable
     * upload cache. Returns a Promise<IDBDatabase|null> — null on
     * failure (private mode, SecurityError, etc.) so callers can
     * degrade gracefully to in-memory.
     */
    function openResumableDb() {
        if (!_hasIndexedDB()) return Promise.resolve(null);
        if (_resumableDbPromise) return _resumableDbPromise;
        _resumableDbPromise = new Promise((resolve) => {
            let req;
            try {
                req = indexedDB.open(RESUMABLE_DB_NAME, RESUMABLE_DB_VERSION);
            } catch (err) {
                resolve(null);
                return;
            }
            req.onupgradeneeded = (ev) => {
                const db = ev.target.result;
                if (!db.objectStoreNames.contains(RESUMABLE_STORE_NAME)) {
                    db.createObjectStore(RESUMABLE_STORE_NAME, { keyPath: 'fileHint' });
                }
            };
            req.onsuccess = (ev) => resolve(ev.target.result);
            req.onerror = () => resolve(null);
            req.onblocked = () => resolve(null);
        });
        return _resumableDbPromise;
    }

    /**
     * Build a file "hint" key — not a cryptographic hash, just a
     * best-effort fingerprint. Good enough to spot "is this the same
     * file the user selected last session?" without reading bytes.
     */
    function fileHintKey(file) {
        if (!file) return null;
        const name = file.name || '';
        const size = Number(file.size || 0);
        const lm = Number(file.lastModified || 0);
        return `${name}|${size}|${lm}`;
    }

    async function saveResumableRecord(record) {
        const db = await openResumableDb();
        if (!db) return false;
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readwrite');
                tx.objectStore(RESUMABLE_STORE_NAME).put(record);
                tx.oncomplete = () => resolve(true);
                tx.onerror = () => resolve(false);
                tx.onabort = () => resolve(false);
            } catch (_) {
                resolve(false);
            }
        });
    }

    async function loadResumableRecord(fileHint) {
        const db = await openResumableDb();
        if (!db) return null;
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readonly');
                const req = tx.objectStore(RESUMABLE_STORE_NAME).get(fileHint);
                req.onsuccess = () => resolve(req.result || null);
                req.onerror = () => resolve(null);
            } catch (_) {
                resolve(null);
            }
        });
    }

    async function deleteResumableRecord(fileHint) {
        const db = await openResumableDb();
        if (!db) return false;
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readwrite');
                tx.objectStore(RESUMABLE_STORE_NAME).delete(fileHint);
                tx.oncomplete = () => resolve(true);
                tx.onerror = () => resolve(false);
            } catch (_) {
                resolve(false);
            }
        });
    }

    async function listResumableRecords() {
        const db = await openResumableDb();
        if (!db) return [];
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(RESUMABLE_STORE_NAME, 'readonly');
                const req = tx.objectStore(RESUMABLE_STORE_NAME).getAll();
                req.onsuccess = () => resolve(req.result || []);
                req.onerror = () => resolve([]);
            } catch (_) {
                resolve([]);
            }
        });
    }

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
     * Parse a UUID string back into its 16-byte representation.
     * Used when resuming an upload — we have the ref string but need
     * the bytes for binary frames.
     */
    function uuidStringToBytes(s) {
        const hex = String(s || '').replace(/-/g, '');
        if (hex.length !== 32) return null;
        const bytes = new Uint8Array(16);
        for (let i = 0; i < 16; i++) {
            bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
        }
        return bytes;
    }

    /**
     * Upload a single file via chunked binary WebSocket frames.
     *
     * Options (all optional):
     *   - resumeRef / resumeRefBytes — previously saved upload_id to
     *     resume. When present, the client skips `upload_register` and
     *     sends `upload_resume` first; the server replies with
     *     bytes_received and the client seeks past the already-
     *     uploaded chunks.
     *   - resumable — when true, the client persists a record to
     *     IndexedDB so it can resume after a tab reload.
     */
    async function uploadFile(ws, uploadName, file, config, opts) {
        opts = opts || {};
        const chunkSize = (config && config.chunk_size) || DEFAULT_CHUNK_SIZE;
        const isResumable = !!(opts.resumable || (config && config.resumable));

        let refBytes;
        let ref;
        let startOffset = 0;
        let startChunkIndex = 0;

        if (opts.resumeRef) {
            ref = opts.resumeRef;
            refBytes = uuidStringToBytes(ref) || uuidBytes();
        } else {
            refBytes = uuidBytes();
            ref = uuidToString(refBytes);
        }

        const fileHint = fileHintKey(file);

        // Resumable path: try to resume first.
        if (opts.resumeRef) {
            const resumePayload = await sendResumeAndWait(ws, ref);
            if (resumePayload && resumePayload.status === 'resumed') {
                startOffset = Number(resumePayload.bytes_received || 0);
                if (startOffset > file.size) startOffset = file.size;
                startChunkIndex = Math.floor(startOffset / chunkSize);
                if (globalThis.djustDebug) {
                    console.log('[Upload] Resuming %s at %d bytes', String(file.name), startOffset);
                }
            } else {
                // not_found or locked — fall back to a fresh register.
                if (fileHint) deleteResumableRecord(fileHint);
                refBytes = uuidBytes();
                ref = uuidToString(refBytes);
            }
        }

        // Fresh (or fallback) registration.
        if (startOffset === 0) {
            ws.sendMessage({
                type: 'upload_register',
                upload_name: uploadName,
                ref: ref,
                client_name: file.name,
                client_type: file.type,
                client_size: file.size,
                resumable: isResumable,
            });
        }

        if (isResumable && fileHint) {
            saveResumableRecord({
                fileHint,
                ref,
                uploadName,
                clientName: file.name,
                clientSize: file.size,
                savedAt: Date.now(),
            });
        }

        return new Promise((resolve, reject) => {
            activeUploads.set(ref, {
                file, config, uploadName, refBytes, resolve, reject,
                chunkIndex: startChunkIndex, sent: startOffset,
                resumable: isResumable, fileHint,
            });

            // Read file and send chunks
            const reader = new FileReader();
            reader.onload = function() {
                const buffer = reader.result;
                let offset = startOffset;
                let chunkIndex = startChunkIndex;

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
     * Pending promises for in-flight `upload_resume` handshakes —
     * resolved when the server replies with `upload_resumed`.
     */
    const pendingResumes = new Map();

    /**
     * Send `upload_resume` and return a Promise that resolves with the
     * server's `upload_resumed` payload. Times out after 5 s so a
     * silent server doesn't stall the upload forever.
     */
    function sendResumeAndWait(ws, ref) {
        return new Promise((resolve) => {
            let done = false;
            const timer = setTimeout(() => {
                if (done) return;
                done = true;
                pendingResumes.delete(ref);
                resolve(null);
            }, 5000);
            pendingResumes.set(ref, (payload) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                pendingResumes.delete(ref);
                resolve(payload);
            });
            try {
                ws.sendMessage({ type: 'upload_resume', ref });
            } catch (err) {
                if (!done) {
                    done = true;
                    clearTimeout(timer);
                    pendingResumes.delete(ref);
                    resolve(null);
                }
            }
        });
    }

    /**
     * Handle `upload_resumed` messages delivered by the WS frame
     * handler. Exposed as `window.djust.uploads.handleResumed` so
     * 03-websocket.js can call it without creating a circular import.
     *
     * Security: `data` arrives from the WebSocket wire, so `data.ref`
     * is attacker-controlled. We only dispatch when:
     *   1. `ref` is a non-empty string (no lookup-by-object tricks);
     *   2. `pending` is a function we placed in the Map ourselves
     *      (validated via `typeof` — a Map.get() that misses returns
     *      undefined, never a prototype method from the Map class).
     * This prevents a malicious server from steering `pending(data)`
     * into an unintended dispatch.
     */
    function handleUploadResumed(data) {
        if (!data || typeof data.ref !== 'string' || data.ref.length === 0) {
            return;
        }
        const pending = pendingResumes.get(data.ref);
        if (typeof pending === 'function') {
            pending(data);
        }
    }

    /**
     * Upload a single file DIRECTLY to a pre-signed URL (e.g. S3 PUT).
     *
     * Used by the "mode: 'presigned'" flow (#820): the server signs a
     * PUT URL and returns it as an upload spec; the client streams
     * bytes straight to the object store, bypassing the djust
     * WebSocket entirely. Progress is reported via XHR upload events
     * (fetch streams still don't expose upload progress in 2026).
     *
     * @param {Object} spec - { url, key, fields } from server
     * @param {File}   file - the file to PUT
     * @param {Object} hooks - { onProgress(percent), onComplete(result), onError(err) }
     * @returns {Promise<{ key: string, status: number }>}
     */
    function uploadFilePresigned(spec, file, hooks) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('PUT', spec.url, true);

            // Apply server-specified fields as request headers. Content-Type
            // MUST match what was passed at presign time or S3 returns 403.
            const fields = (spec && spec.fields) || {};
            for (const name of Object.keys(fields)) {
                try { xhr.setRequestHeader(name, fields[name]); } catch (_) { /* ignore */ }
            }

            if (xhr.upload) {
                xhr.upload.onprogress = (ev) => {
                    if (!ev.lengthComputable) return;
                    const pct = Math.round((ev.loaded / ev.total) * 100);
                    if (hooks && typeof hooks.onProgress === 'function') {
                        hooks.onProgress(pct);
                    }
                    // Also dispatch the same public event as the WS path
                    // so app code doesn't care which transport we used.
                    window.dispatchEvent(new CustomEvent('djust:upload:progress', {
                        detail: { ref: spec.key, progress: pct, status: 'uploading', mode: 'presigned' }
                    }));
                };
            }

            xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    const result = { key: spec.key, status: xhr.status };
                    if (hooks && typeof hooks.onComplete === 'function') {
                        hooks.onComplete(result);
                    }
                    window.dispatchEvent(new CustomEvent('djust:upload:progress', {
                        detail: { ref: spec.key, progress: 100, status: 'complete', mode: 'presigned' }
                    }));
                    resolve(result);
                } else {
                    const err = new Error('Presigned PUT failed: HTTP ' + xhr.status);
                    if (hooks && typeof hooks.onError === 'function') hooks.onError(err);
                    window.dispatchEvent(new CustomEvent('djust:upload:error', {
                        detail: { file: file.name, error: err.message, mode: 'presigned' }
                    }));
                    reject(err);
                }
            };

            xhr.onerror = () => {
                const err = new Error('Presigned PUT network failure');
                if (hooks && typeof hooks.onError === 'function') hooks.onError(err);
                window.dispatchEvent(new CustomEvent('djust:upload:error', {
                    detail: { file: file.name, error: err.message, mode: 'presigned' }
                }));
                reject(err);
            };

            xhr.send(file);
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
            if (upload && upload.fileHint) {
                // Clear the IndexedDB resumable record — upload is done.
                deleteResumableRecord(upload.fileHint);
            }
            activeUploads.delete(ref);
        } else if (status === 'error') {
            if (upload && upload.reject) {
                upload.reject(new Error('Upload failed on server'));
            }
            if (upload && upload.fileHint) {
                deleteResumableRecord(upload.fileHint);
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
        // v0.5.7 (#820) — pre-signed S3 PUT: client-direct upload to
        // object storage. Server returns { mode: 'presigned', url, key,
        // fields }; app code calls uploadPresigned(spec, file, hooks).
        uploadPresigned: uploadFilePresigned,
        // v0.5.7 (#821) — resumable uploads across WS disconnects.
        handleResumed: handleUploadResumed,
        openResumableDb,
        saveResumableRecord,
        loadResumableRecord,
        deleteResumableRecord,
        listResumableRecords,
        fileHintKey,
        uuidStringToBytes,
        uploadFile: (file, uploadName, opts) => {
            const cfg = uploadConfigs[uploadName];
            return uploadFile(liveViewWS, uploadName, file, cfg, opts || {});
        },
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

        case 'prune': {
            // Stream `:limit` garbage-collection. Trim children from the
            // specified edge ('top' removes from the start, 'bottom' from
            // the end) until `limit` or fewer element children remain.
            const limit = typeof op.limit === 'number' ? Math.max(0, op.limit) : 0;
            const edge = op.edge === 'bottom' ? 'bottom' : 'top';
            // `.children` is an HTMLCollection — always element-only, no
            // nodeType filter needed (was redundant per review #801).
            let kids = Array.from(el.children);
            while (kids.length > limit) {
                const victim = edge === 'top' ? kids.shift() : kids.pop();
                if (!victim) break;
                victim.remove();
            }
            _dispatchStreamEvent(el, 'stream:prune', { stream: streamName, edge, limit });
            break;
        }

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

        // Scroll to top on navigation (or to anchor if present)
        var hash = newUrl.hash;
        if (hash) {
            try {
                var target = document.querySelector(hash);
                if (target) {
                    target.scrollIntoView({ behavior: 'instant' });
                }
            } catch (_e) {
                // Malformed hash (e.g. "#foo[bar]") — fall through to scroll top
                window.scrollTo({ top: 0, behavior: 'instant' });
            }
        } else {
            window.scrollTo({ top: 0, behavior: 'instant' });
        }

        // Clear the prefetch set so links on the new view are re-eligible for prefetching
        window.djust._prefetch?.clear();

        if (globalThis.djustDebug) console.log(`[LiveView] live_redirect: ${method} → ${newUrl.toString()}`);

        // Send a mount request for the new view path over the existing WebSocket
        // The server will unmount the old view and mount the new one
        if (isWSConnected()) {
            // Look up the view path for the new URL from the route map
            const viewPath = resolveViewPath(newUrl.pathname);
            if (viewPath) {
                // Sticky LiveViews (Phase B): BEFORE the outbound
                // live_redirect_mount message, detach any
                // [dj-sticky-view] subtrees into the module-local
                // stash so they survive the mount-frame innerHTML
                // replacement. If stashing happens AFTER the mount
                // frame arrives, the subtree has already been
                // destroyed.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.stashStickySubtrees) {
                    window.djust.stickyPreserve.stashStickySubtrees();
                }

                // State-snapshot capture (v0.6.0) — fire the public event
                // so 46-state-snapshot.js can post the current view's
                // public state to the SW cache BEFORE this URL leaves.
                try {
                    window.dispatchEvent(new CustomEvent('djust:before-navigate', {
                        detail: { fromUrl: window.location.pathname, toUrl: newUrl.pathname },
                    }));
                } catch (_e) { /* CustomEvent may fail in old environments */ }

                const urlParams = Object.fromEntries(newUrl.searchParams);
                const outgoing = {
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: urlParams,
                    url: newUrl.pathname,
                };
                // Attach pending state-snapshot (populated by popstate
                // handler in 46-state-snapshot.js when the user hits back).
                if (window.djust && window.djust._pendingStateSnapshot) {
                    outgoing.state_snapshot = window.djust._pendingStateSnapshot;
                    window.djust._pendingStateSnapshot = null;
                }
                liveViewWS.sendMessage(outgoing);
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
     *
     * Fix #2: the handler is async so the state-snapshot lookup can be
     * awaited BEFORE sending ``live_redirect_mount`` — the synchronous
     * version captured ``_pendingStateSnapshot`` before the async
     * lookup populated it, causing the first popstate to go out without
     * its cached snapshot.
     *
     * Fix #3: before the live_redirect_mount goes out we also fast-paint
     * cached HTML via ``djust._sw.lookupVdom`` so the user sees
     * something instantly on back-nav; the live WS reply reconciles
     * afterwards via the normal mount handler.
     */
    window.addEventListener('popstate', async function (event) {
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
                // Sticky LiveViews (Phase B): detach sticky subtrees
                // into the stash BEFORE the outbound
                // live_redirect_mount message.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.stashStickySubtrees) {
                    window.djust.stickyPreserve.stashStickySubtrees();
                }

                // Fix #3 — VDOM cache fast-paint. If the SW has a
                // recently-cached HTML snapshot for this URL, paint it
                // into the existing ``[dj-view]`` container NOW so the
                // user sees something instantly. The incoming
                // ``mount`` frame's innerHTML replacement reconciles
                // the DOM shortly after.
                try {
                    if (window.djust && window.djust._sw && typeof window.djust._sw.lookupVdom === 'function') {
                        const vdomReply = await window.djust._sw.lookupVdom(url.pathname);
                        if (vdomReply && vdomReply.hit && !vdomReply.stale && typeof vdomReply.html === 'string') {
                            let fastContainer = document.querySelector('[dj-view]:not([dj-sticky-root])');
                            if (!fastContainer) fastContainer = document.querySelector('[dj-root]');
                            if (fastContainer) {
                                // codeql[js/xss] -- html is server-rendered; only reads from SW cache keyed by same-origin url
                                fastContainer.innerHTML = vdomReply.html;
                                window.dispatchEvent(new CustomEvent('djust:vdom-cache-applied', {
                                    detail: { url: url.pathname, version: vdomReply.version },
                                }));
                            }
                        }
                    }
                } catch (_e) { /* fast-paint is best-effort */ }

                // Fix #2 — await the state-snapshot lookup before we
                // send the outbound ``live_redirect_mount`` frame.
                let stateSnapshot = null;
                try {
                    if (window.djust && window.djust._stateSnapshot && typeof window.djust._stateSnapshot.lookupStateForUrl === 'function') {
                        stateSnapshot = await window.djust._stateSnapshot.lookupStateForUrl(url.pathname);
                    } else if (window.djust && window.djust._pendingStateSnapshot) {
                        // Back-compat fallback — if the older async-race
                        // slot happens to be populated, honor it.
                        stateSnapshot = window.djust._pendingStateSnapshot;
                        window.djust._pendingStateSnapshot = null;
                    }
                } catch (_e) { stateSnapshot = null; }

                const outgoing = {
                    type: 'live_redirect_mount',
                    view: viewPath,
                    params: params,
                    url: url.pathname,
                };
                if (stateSnapshot) {
                    outgoing.state_snapshot = stateSnapshot;
                }
                liveViewWS.sendMessage(outgoing);
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
    const _prefetched = new Set();

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
            const url = new URL(link.href, location.origin);
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
        const link = event.target.closest('a');
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

    // Expose for testing and for navigation to clear on SPA transition.
    // clear() also clears the intent-prefetch set if the intent IIFE has
    // installed; otherwise the intent module sets its own clear bridge.
    window.djust = window.djust || {};
    window.djust._prefetch = {
        _prefetched: _prefetched,
        _shouldPrefetch: _shouldPrefetch,
        clear: function () {
            _prefetched.clear();
            if (window.djust && window.djust._intentPrefetch
                && typeof window.djust._intentPrefetch.clear === 'function') {
                window.djust._intentPrefetch.clear();
            }
        }
    };
})();

// ============================================================================
// Intent-based prefetch (dj-prefetch)
// ============================================================================
// Layer on top of the SW-mediated hover prefetch above. This IIFE:
//   - fires on `mouseenter` with a 65 ms debounce (cancelled by `mouseleave`),
//   - fires on `touchstart` immediately (no debounce — mobile users commit fast),
//   - injects <link rel="prefetch" as="document"> so the browser manages the
//     cache lifecycle (falls back to low-priority fetch when relList doesn't
//     advertise 'prefetch').
// Links opt in via the `dj-prefetch` attribute. `dj-prefetch="false"` opts out.
// Only same-origin URLs are prefetched; each URL fires at most once.

(function () {
    const HOVER_DEBOUNCE_MS = 65;
    const _intentPrefetched = new Set();
    const _pending = new WeakMap(); // link -> {timer, controller}

    function _supportsLinkPrefetch() {
        try {
            return document.createElement('link').relList.supports('prefetch');
        } catch (_) { return false; }
    }
    const _canUseLinkRel = _supportsLinkPrefetch();

    function _shouldIntentPrefetch(link) {
        if (!link || !link.hasAttribute || !link.hasAttribute('dj-prefetch')) return false;
        const v = link.getAttribute('dj-prefetch');
        if (v === 'false') return false;
        if (navigator.connection && navigator.connection.saveData) return false;
        if (!link.href) return false;
        try {
            const url = new URL(link.href, location.origin);
            if (url.origin !== location.origin) return false;
        } catch (_) { return false; }
        if (_intentPrefetched.has(link.href)) return false;
        return true;
    }

    function _doPrefetch(link) {
        _intentPrefetched.add(link.href);
        if (globalThis.djustDebug) console.log('[djust] Intent prefetch:', link.href);
        if (_canUseLinkRel) {
            const el = document.createElement('link');
            el.rel = 'prefetch';
            el.href = link.href;
            el.setAttribute('as', 'document');
            document.head.appendChild(el);
            return { cancel: function () { /* browser handles; no-op */ } };
        }
        const ctrl = new AbortController();
        try {
            fetch(link.href, {
                credentials: 'same-origin',
                priority: 'low',
                signal: ctrl.signal,
            }).catch(function () { /* aborts + prefetch failures are non-fatal */ });
        } catch (_) { /* fetch unsupported — silently no-op */ }
        return { cancel: function () { ctrl.abort(); } };
    }

    function _onEnter(event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest && event.target.closest('a[dj-prefetch]');
        if (!link || !_shouldIntentPrefetch(link)) return;
        if (_pending.has(link)) return;
        const timer = setTimeout(function () {
            _pending.delete(link);
            _doPrefetch(link);
        }, HOVER_DEBOUNCE_MS);
        _pending.set(link, { timer: timer, controller: null });
    }

    function _onLeave(event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest && event.target.closest('a[dj-prefetch]');
        if (!link) return;
        const p = _pending.get(link);
        if (!p) return;
        clearTimeout(p.timer);
        _pending.delete(link);
    }

    function _onTouch(event) {
        if (!(event.target instanceof Element)) return;
        const link = event.target.closest && event.target.closest('a[dj-prefetch]');
        if (!link || !_shouldIntentPrefetch(link)) return;
        _doPrefetch(link); // no debounce on explicit touch
    }

    document.addEventListener('mouseenter', _onEnter, true);
    document.addEventListener('mouseleave', _onLeave, true);
    document.addEventListener('touchstart', _onTouch, { capture: true, passive: true });

    window.djust = window.djust || {};
    window.djust._intentPrefetch = {
        _prefetched: _intentPrefetched,
        _shouldIntentPrefetch: _shouldIntentPrefetch,
        _onEnter: _onEnter,
        _onLeave: _onLeave,
        _onTouch: _onTouch,
        HOVER_DEBOUNCE_MS: HOVER_DEBOUNCE_MS,
        clear: function () { _intentPrefetched.clear(); },
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

        // Add navigating class to dj-root for CSS-based transitions.
        // Sticky LiveViews (Phase B): exclude [dj-sticky-root] wrappers
        // so a sticky child doesn't incorrectly receive the navigating
        // class — the class is a whole-page transition cue.
        const root = document.querySelector('[dj-root]:not([dj-sticky-root])') || document.querySelector('[dj-root]');
        if (root) root.classList.add('djust-navigating');

        // Dispatch lifecycle event
        document.dispatchEvent(new CustomEvent('djust:navigate-start'));
    }

    function finish() {
        if (!barElement) return;

        // Remove navigating class from dj-root (exclude sticky roots,
        // matching start()).
        const root = document.querySelector('[dj-root]:not([dj-sticky-root])') || document.querySelector('[dj-root]');
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
                djLog('[djust:exec] js commands module not loaded; skipping exec chain');
            }
            return;
        }

        if (!payload || !Array.isArray(payload.ops)) {
            if (globalThis.djustDebug) {
                djLog('[djust:exec] malformed payload (expected {ops: [...]}):', payload);
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
                djLog('[djust:exec] op execution failed:', err);
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

// ============================================================================
// dj-virtual — Virtual / windowed lists with DOM recycling (v0.5.0+)
// ============================================================================
//
// Render only the visible slice of a large list. All items outside the
// viewport (plus overscan) are pulled out of the DOM on scroll; the container
// keeps a spacer element so the native scrollbar reflects the virtual length.
//
// Required attributes on the container:
//   dj-virtual="items_var_name"       — context variable driving the list
//
// Height modes (pick one):
//   dj-virtual-item-height="48"       — FIXED pixel height per item
//   dj-virtual-variable-height        — VARIABLE heights (ResizeObserver)
//
// Optional:
//   dj-virtual-overscan="5"           — rows rendered above/below (default 3)
//   dj-virtual-estimated-height="60"  — default baseline for unmeasured
//                                       items in variable mode (default 50)
//   dj-virtual-key-attr="data-key"    — attribute on each item used as the
//                                       height-cache key in VARIABLE mode
//                                       (default "data-key"). Keeps cached
//                                       heights bound to their item when
//                                       the list is reordered; missing
//                                       attribute falls back to index.
//
// The container itself must have a fixed height (e.g. style="height: 600px")
// and `overflow: auto`. Direct children must be pre-rendered server-side for
// first paint; on hydration we move them under an inner shell that uses
// translateY() to position the visible slice, and recycle nodes on scroll.
//
// Integration:
//   - djust.initVirtualLists(root) — scan + set up new containers
//   - djust.refreshVirtualList(el) — forced recompute after VDOM morph
//   - djust.teardownVirtualList(el) — remove observers (test helper)
//
// Throttling: scroll handler wrapped in requestAnimationFrame (one compute
// per frame). No setTimeout debounce.

(function initVirtualListModule() {
    const STATE = new WeakMap();
    const DEFAULT_OVERSCAN = 3;
    const DEFAULT_ESTIMATED_HEIGHT = 50;
    const DEFAULT_KEY_ATTR = 'data-key';

    // Build the stable cache key for an item in variable-height mode.
    // Prefer the configured data-key attribute so heights survive
    // reorders; fall back to the index string for back-compat with lists
    // that don't mark items with a key.
    function itemKey(state, node, idx) {
        if (state.keyAttr && node && node.nodeType === 1) {
            const k = node.getAttribute(state.keyAttr);
            if (k != null && k !== '') return 'k:' + k;
        }
        return 'i:' + idx;
    }

    function parseIntAttr(el, name, fallback) {
        const raw = el.getAttribute(name);
        if (raw == null || raw === '') return fallback;
        const n = parseInt(raw, 10);
        return Number.isFinite(n) && n >= 0 ? n : fallback;
    }

    function hasBoolAttr(el, name) {
        return el.hasAttribute(name);
    }

    function setup(container) {
        const fixedItemHeight = parseIntAttr(container, 'dj-virtual-item-height', 0);
        const variableMode = hasBoolAttr(container, 'dj-virtual-variable-height');

        // Mode detection:
        //  - fixed: dj-virtual-item-height set (> 0). variableMode ignored
        //    even if attribute is also present (fixed wins; deterministic).
        //  - variable: no valid item-height AND dj-virtual-variable-height is
        //    present.
        //  - otherwise: no-op (mirrors original behaviour).
        if (!fixedItemHeight && !variableMode) {
            if (globalThis.djustDebug) {
                console.warn(
                    '[dj-virtual] Missing dj-virtual-item-height and ' +
                    'dj-virtual-variable-height on', container
                );
            }
            return;
        }

        const overscan = parseIntAttr(container, 'dj-virtual-overscan', DEFAULT_OVERSCAN);
        const estimatedHeight = parseIntAttr(
            container, 'dj-virtual-estimated-height', DEFAULT_ESTIMATED_HEIGHT
        ) || DEFAULT_ESTIMATED_HEIGHT;
        // Attribute name whose value becomes the height-cache key. Empty
        // string (explicit opt-out) falls back to index-based keying.
        const keyAttrRaw = container.getAttribute('dj-virtual-key-attr');
        const keyAttr = keyAttrRaw == null ? DEFAULT_KEY_ATTR : keyAttrRaw;

        // Snapshot the pre-rendered children as the full item pool.
        const originalChildren = Array.from(container.children).filter(
            el => el.nodeType === 1
        );

        // Inner shell that actually holds the visible slice. A spacer sibling
        // forces the container scroll height to the virtual length.
        const shell = document.createElement('div');
        shell.setAttribute('data-dj-virtual-shell', '');
        shell.style.position = 'relative';
        shell.style.willChange = 'transform';
        shell.style.transform = 'translateY(0px)';

        const spacer = document.createElement('div');
        spacer.setAttribute('data-dj-virtual-spacer', '');
        spacer.style.width = '1px';
        spacer.style.pointerEvents = 'none';
        spacer.style.visibility = 'hidden';

        container.innerHTML = '';
        container.appendChild(shell);
        container.appendChild(spacer);
        if (getComputedStyle(container).position === 'static') {
            container.style.position = 'relative';
        }

        const state = {
            container,
            shell,
            spacer,
            mode: fixedItemHeight ? 'fixed' : 'variable',
            itemHeight: fixedItemHeight,       // fixed mode only
            estimatedHeight,                   // variable mode fallback
            keyAttr,                           // variable mode: item key source
            heights: new Map(),                // variable mode: itemKey -> px
            offsets: null,                     // variable mode: prefix-sum cache (lazy)
            overscan,
            items: originalChildren,
            visibleStart: 0,
            visibleEnd: 0,
            rafPending: false,
            onScroll: null,
            onResize: null,
            resizeObserver: null,
            itemObserver: null,                // variable mode: per-item RO
            nodeToIndex: new WeakMap(),        // variable mode: reverse lookup
        };

        state.onScroll = () => requestFrame(state);
        state.onResize = () => requestFrame(state);

        container.addEventListener('scroll', state.onScroll, { passive: true });
        if (typeof ResizeObserver !== 'undefined') {
            state.resizeObserver = new ResizeObserver(state.onResize);
            state.resizeObserver.observe(container);

            if (state.mode === 'variable') {
                // Per-item observer: every item that scrolls into the window
                // gets measured; cache lookups drive the offset math.
                state.itemObserver = new ResizeObserver((entries) => {
                    let dirty = false;
                    for (const entry of entries) {
                        const node = entry.target;
                        const idx = state.nodeToIndex.get(node);
                        if (idx == null) continue;
                        // Prefer borderBox for layout-accurate measurement;
                        // fall back to getBoundingClientRect for older engines
                        // (jsdom's ResizeObserver stub often omits both).
                        let h = 0;
                        if (entry.borderBoxSize && entry.borderBoxSize.length) {
                            h = entry.borderBoxSize[0].blockSize;
                        } else if (entry.contentRect) {
                            h = entry.contentRect.height;
                        }
                        if (!h) h = node.getBoundingClientRect().height;
                        h = Math.round(h);
                        const cacheKey = itemKey(state, node, idx);
                        if (h > 0 && state.heights.get(cacheKey) !== h) {
                            state.heights.set(cacheKey, h);
                            dirty = true;
                        }
                    }
                    if (dirty) {
                        state.offsets = null; // invalidate prefix-sum cache
                        requestFrame(state);
                    }
                });
            }
        }

        STATE.set(container, state);
        render(state);
    }

    function requestFrame(state) {
        if (state.rafPending) return;
        state.rafPending = true;
        const raf = typeof requestAnimationFrame !== 'undefined'
            ? requestAnimationFrame
            : (cb) => setTimeout(cb, 16);
        raf(() => {
            state.rafPending = false;
            render(state);
        });
    }

    // --- variable-mode geometry helpers --------------------------------------

    function avgMeasuredHeight(state) {
        const n = state.heights.size;
        if (!n) return state.estimatedHeight;
        let total = 0;
        for (const h of state.heights.values()) total += h;
        return total / n;
    }

    function heightFor(state, idx) {
        const node = state.items[idx];
        const cacheKey = itemKey(state, node, idx);
        const cached = state.heights.get(cacheKey);
        if (cached != null) return cached;
        return state.estimatedHeight;
    }

    // Build the prefix-sum offset array lazily. `offsets[i]` = sum of heights
    // for items [0..i). `offsets[total]` = virtual total height.
    function ensureOffsets(state) {
        if (state.offsets) return state.offsets;
        const total = state.items.length;
        const offsets = new Float64Array(total + 1);
        let acc = 0;
        for (let i = 0; i < total; i++) {
            offsets[i] = acc;
            acc += heightFor(state, i);
        }
        offsets[total] = acc;
        state.offsets = offsets;
        return offsets;
    }

    // Binary search: first index whose offset + height > scrollTop.
    function firstVisibleIndex(offsets, scrollTop, total) {
        if (scrollTop <= 0 || total === 0) return 0;
        let lo = 0;
        let hi = total;
        while (lo < hi) {
            const mid = (lo + hi) >>> 1;
            if (offsets[mid + 1] <= scrollTop) lo = mid + 1;
            else hi = mid;
        }
        return Math.min(lo, Math.max(0, total - 1));
    }

    function render(state) {
        if (state.mode === 'variable') {
            renderVariable(state);
        } else {
            renderFixed(state);
        }
    }

    function renderFixed(state) {
        const { container, shell, spacer, itemHeight, overscan, items } = state;
        const total = items.length;

        spacer.style.height = (total * itemHeight) + 'px';

        if (total === 0) {
            shell.innerHTML = '';
            shell.style.transform = 'translateY(0px)';
            state.visibleStart = 0;
            state.visibleEnd = 0;
            return;
        }

        const viewportHeight = container.clientHeight || 0;
        const scrollTop = container.scrollTop || 0;

        const firstVisible = Math.floor(scrollTop / itemHeight);
        const visibleCount = Math.max(1, Math.ceil(viewportHeight / itemHeight));

        const start = Math.max(0, firstVisible - overscan);
        const end = Math.min(total, firstVisible + visibleCount + overscan);

        if (start === state.visibleStart && end === state.visibleEnd && shell.childElementCount === end - start) {
            // Window unchanged and DOM already populated — nothing to do.
            shell.style.transform = 'translateY(' + (start * itemHeight) + 'px)';
            return;
        }

        // Recycle by clearing the shell and re-attaching the slice nodes.
        // We reuse the real element references from `items` so frameworks /
        // tests can rely on identity across scrolls.
        shell.textContent = '';
        const frag = document.createDocumentFragment();
        for (let i = start; i < end; i++) {
            const node = items[i];
            node.style.height = itemHeight + 'px';
            node.style.boxSizing = 'border-box';
            frag.appendChild(node);
        }
        shell.appendChild(frag);
        shell.style.transform = 'translateY(' + (start * itemHeight) + 'px)';

        state.visibleStart = start;
        state.visibleEnd = end;
    }

    function renderVariable(state) {
        const { container, shell, spacer, overscan, items } = state;
        const total = items.length;

        if (total === 0) {
            spacer.style.height = '0px';
            shell.innerHTML = '';
            shell.style.transform = 'translateY(0px)';
            state.visibleStart = 0;
            state.visibleEnd = 0;
            return;
        }

        const offsets = ensureOffsets(state);
        spacer.style.height = offsets[total] + 'px';

        const viewportHeight = container.clientHeight || 0;
        const scrollTop = container.scrollTop || 0;

        const firstVisible = firstVisibleIndex(offsets, scrollTop, total);

        // Walk forward from firstVisible until we've covered viewportHeight.
        const avg = avgMeasuredHeight(state) || state.estimatedHeight;
        let end = firstVisible;
        let covered = 0;
        while (end < total && covered < viewportHeight) {
            covered += heightFor(state, end);
            end++;
        }
        // If we never reached viewportHeight (e.g. list shorter), end === total.
        end = Math.min(total, end + overscan);
        const start = Math.max(0, firstVisible - overscan);

        // Attach slice nodes, register with the per-item observer.
        shell.textContent = '';
        const frag = document.createDocumentFragment();
        // Disconnect + re-observe only items currently on-screen. Using a
        // single WeakMap (nodeToIndex) lets us update index bindings cheaply
        // when the slice shifts.
        if (state.itemObserver) {
            // We don't disconnect wholesale — ResizeObserver keeps per-target
            // entries, and re-calling observe() on the same node is a no-op.
            // We DO need to seed the nodeToIndex map so resize callbacks can
            // resolve node -> current index.
        }
        for (let i = start; i < end; i++) {
            const node = items[i];
            node.style.boxSizing = 'border-box';
            // DO NOT set a fixed height — variable mode lets content size
            // itself, and ResizeObserver reports back.
            state.nodeToIndex.set(node, i);
            frag.appendChild(node);
            if (state.itemObserver) {
                try {
                    state.itemObserver.observe(node);
                } catch (e) {
                    // Some jsdom versions throw on re-observe of same node.
                    if (globalThis.djustDebug) {
                        console.warn('[dj-virtual] itemObserver.observe failed', e);
                    }
                }
            } else {
                // No RO available (very old environment): read the height
                // synchronously so at least the prefix-sum converges after
                // the first render pass.
                const rect = typeof node.getBoundingClientRect === 'function'
                    ? node.getBoundingClientRect()
                    : null;
                if (rect && rect.height > 0) {
                    const h = Math.round(rect.height);
                    const cacheKey = itemKey(state, node, i);
                    if (state.heights.get(cacheKey) !== h) {
                        state.heights.set(cacheKey, h);
                        state.offsets = null; // recompute next frame
                    }
                }
            }
        }
        shell.appendChild(frag);
        shell.style.transform = 'translateY(' + offsets[start] + 'px)';

        state.visibleStart = start;
        state.visibleEnd = end;
    }

    function initVirtualLists(root) {
        const scope = root || document;
        const containers = scope.querySelectorAll
            ? scope.querySelectorAll('[dj-virtual]')
            : [];
        containers.forEach(container => {
            if (!STATE.has(container)) setup(container);
        });
    }

    function refreshVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;
        // Re-snapshot: after VDOM morph, the shell holds only the visible
        // slice. The full data source is whatever is currently in the shell
        // plus any new children added outside of virtualization. We support
        // an external update path: callers may set `container.__djVirtualItems`
        // to an array of HTMLElements to replace the item pool.
        const replacement = container.__djVirtualItems;
        if (Array.isArray(replacement)) {
            state.items = replacement.slice();
            delete container.__djVirtualItems;
            state.visibleStart = -1;
            state.visibleEnd = -1;
            // In variable mode, replacing the pool invalidates cached
            // heights keyed by index (item i may be a different item
            // now). Heights keyed by `data-key` survive reorders, so we
            // only drop index-keyed entries and keep `k:*` entries.
            if (state.mode === 'variable') {
                if (state.keyAttr) {
                    const preserved = new Map();
                    for (const [k, v] of state.heights) {
                        if (typeof k === 'string' && k.charCodeAt(0) === 107) {
                            // starts with 'k:' — data-key entry
                            preserved.set(k, v);
                        }
                    }
                    state.heights = preserved;
                } else {
                    state.heights = new Map();
                }
                state.offsets = null;
                state.nodeToIndex = new WeakMap();
            }
        }
        render(state);
    }

    function teardownVirtualList(container) {
        const state = STATE.get(container);
        if (!state) return;
        container.removeEventListener('scroll', state.onScroll);
        if (state.resizeObserver) state.resizeObserver.disconnect();
        if (state.itemObserver) state.itemObserver.disconnect();
        // Restore the pre-virtualization children and remove the shell/spacer.
        // Without this, removing `dj-virtual` from a live container leaves
        // the injected wrapper elements in place and shows only the
        // currently-visible slice — confusing for downstream consumers.
        try {
            container.textContent = '';
            const frag = document.createDocumentFragment();
            for (const node of state.items) frag.appendChild(node);
            container.appendChild(frag);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn('[dj-virtual] teardown restore failed', e);
            }
        }
        STATE.delete(container);
    }

    window.djust = window.djust || {};
    window.djust.initVirtualLists = initVirtualLists;
    window.djust.refreshVirtualList = refreshVirtualList;
    window.djust.teardownVirtualList = teardownVirtualList;
})();

// ============================================================================
// dj-viewport-top / dj-viewport-bottom — Bidirectional infinite scroll (v0.5.0)
// ============================================================================
//
// Fire server events when the first or last child of a stream container
// enters the viewport. Phoenix 1.0 parity with phx-viewport-top /
// phx-viewport-bottom.
//
// Attributes on the stream/list container:
//   dj-viewport-top="event_name"       — fire when first child enters viewport
//   dj-viewport-bottom="event_name"    — fire when last child enters viewport
//   dj-viewport-threshold="0.1"        — IntersectionObserver threshold (default 0.1)
//
// Firing semantics: once-per-entry. A sentinel attribute
// `data-dj-viewport-fired` is set on the sentinel child (first or last) so
// the same element doesn't re-fire on scroll oscillation. To re-arm after
// firing, either (a) replace the sentinel child via a stream op (the new
// child has no sentinel attribute), or (b) call `djust.resetViewport(container)`
// from a client-side hook. There is no corresponding HTML attribute —
// re-arming is programmatic.
//
// Integration:
//   - djust.initInfiniteScroll(root)
//   - djust.teardownInfiniteScroll(container)
//   - djust.resetViewport(container) — clear fired sentinels (re-arm)

(function initInfiniteScrollModule() {
    const STATE = new WeakMap();
    const DEFAULT_THRESHOLD = 0.1;

    function parseFloatAttr(el, name, fallback) {
        const raw = el.getAttribute(name);
        if (raw == null || raw === '') return fallback;
        const n = parseFloat(raw);
        return Number.isFinite(n) && n >= 0 && n <= 1 ? n : fallback;
    }

    function dispatch(container, eventName, edge) {
        // Dispatch a CustomEvent for tests and hook-based handlers to
        // observe, then send to the server via the same public entry
        // point that dj-click / dj-change / dj-submit use
        // (11-event-handler.js exposes this as window.djust.handleEvent).
        const detail = { event: eventName, edge, target: container };
        container.dispatchEvent(new CustomEvent('dj-viewport', {
            bubbles: true,
            detail,
        }));
        if (window.djust && typeof window.djust.handleEvent === 'function') {
            try {
                window.djust.handleEvent(eventName, { edge });
            } catch (err) {
                if (globalThis.djustDebug) {
                    console.warn(
                        '[dj-viewport] handleEvent failed for %s: %s',
                        eventName,
                        err,
                    );
                }
            }
        } else if (globalThis.djustDebug) {
            console.warn(
                '[dj-viewport] window.djust.handleEvent not available — ' +
                    'event %s not sent to server',
                eventName,
            );
        }
    }

    function markFired(el) {
        if (el && el.setAttribute) el.setAttribute('data-dj-viewport-fired', 'true');
    }
    function hasFired(el) {
        return !!(el && el.getAttribute && el.getAttribute('data-dj-viewport-fired') === 'true');
    }

    function setup(container) {
        if (typeof IntersectionObserver === 'undefined') {
            if (globalThis.djustDebug) {
                console.warn('[dj-viewport] IntersectionObserver not available');
            }
            return;
        }

        const topEvent = container.getAttribute('dj-viewport-top');
        const bottomEvent = container.getAttribute('dj-viewport-bottom');
        if (!topEvent && !bottomEvent) return;

        const threshold = parseFloatAttr(container, 'dj-viewport-threshold', DEFAULT_THRESHOLD);

        const state = {
            container,
            topEvent,
            bottomEvent,
            threshold,
            observer: null,
            observedTop: null,
            observedBottom: null,
        };

        state.observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                const target = entry.target;
                if (hasFired(target)) continue;
                markFired(target);

                if (target === state.observedTop && state.topEvent) {
                    dispatch(container, state.topEvent, 'top');
                } else if (target === state.observedBottom && state.bottomEvent) {
                    dispatch(container, state.bottomEvent, 'bottom');
                }
            }
        }, {
            root: null,
            threshold,
        });

        STATE.set(container, state);
        observeSentinels(state);
    }

    function observeSentinels(state) {
        const { container, observer } = state;
        const kids = Array.from(container.children).filter(el => el.nodeType === 1);
        const first = kids[0] || null;
        const last = kids[kids.length - 1] || null;

        if (state.observedTop && state.observedTop !== first) {
            observer.unobserve(state.observedTop);
            state.observedTop = null;
        }
        if (state.observedBottom && state.observedBottom !== last) {
            observer.unobserve(state.observedBottom);
            state.observedBottom = null;
        }

        if (state.topEvent && first && first !== state.observedTop) {
            observer.observe(first);
            state.observedTop = first;
        }
        if (state.bottomEvent && last && last !== state.observedBottom && last !== first) {
            observer.observe(last);
            state.observedBottom = last;
        }
    }

    function initInfiniteScroll(root) {
        const scope = root || document;
        const containers = scope.querySelectorAll
            ? scope.querySelectorAll('[dj-viewport-top], [dj-viewport-bottom]')
            : [];
        containers.forEach(container => {
            if (!STATE.has(container)) {
                setup(container);
            } else {
                // Re-scan sentinels: children may have changed after VDOM morph.
                observeSentinels(STATE.get(container));
            }
        });
    }

    function resetViewport(container) {
        const state = STATE.get(container);
        if (!state) return;
        if (state.observedTop) state.observedTop.removeAttribute('data-dj-viewport-fired');
        if (state.observedBottom) state.observedBottom.removeAttribute('data-dj-viewport-fired');
    }

    function teardownInfiniteScroll(container) {
        const state = STATE.get(container);
        if (!state) return;
        if (state.observer) state.observer.disconnect();
        STATE.delete(container);
    }

    window.djust = window.djust || {};
    window.djust.initInfiniteScroll = initInfiniteScroll;
    window.djust.resetViewport = resetViewport;
    window.djust.teardownInfiniteScroll = teardownInfiniteScroll;
})();
// ============================================================================
// dj-ignore-attrs — Client-owned attributes skipped by VDOM SetAttr patches
// ============================================================================
//
// Mark HTML attributes as client-owned so VDOM SetAttr patches skip them.
// Essential for browser-native elements (<dialog open>, <details open>) and
// third-party JS libraries that manage attributes the server doesn't know
// about.
//
// Phoenix 1.1 parity: JS.ignore_attributes/1.
//
// Usage (template):
//   <dialog dj-ignore-attrs="open">
//   <div dj-ignore-attrs="data-lib-state, aria-expanded">
//
// Format: comma-separated attribute names, whitespace-tolerant.
// ============================================================================

(function initIgnoreAttrs() {
    globalThis.djust = globalThis.djust || {};

    /**
     * Return true when the element opts out of SetAttr updates for attrName.
     *
     * @param {Element|null} el - DOM element
     * @param {string} attrName - attribute key to check
     * @returns {boolean}
     */
    globalThis.djust.isIgnoredAttr = function(el, attrName) {
        if (!el || typeof el.getAttribute !== 'function') return false;
        const raw = el.getAttribute('dj-ignore-attrs');
        if (!raw) return false;
        // Empty attribute name never matches a listed key.
        if (!attrName) return false;
        // CSV with whitespace tolerance. Exact match on attribute name.
        // Empty tokens (from "open,,close" or trailing "open,") are skipped
        // so they don't accidentally match an empty attribute name.
        for (const item of raw.split(',')) {
            const trimmed = item.trim();
            if (!trimmed) continue;
            if (trimmed === attrName) return true;
        }
        return false;
    };
})();
// ============================================================================
// Colocated JS Hooks — Phoenix 1.1 parity
// ============================================================================
//
// Extract <script type="djust/hook"> tags emitted by the {% colocated_hook %}
// template tag and register them into window.djust.hooks for the dj-hook
// runtime to mount.
//
// Usage (template):
//   {% load live_tags %}
//   {% colocated_hook "Chart" %}
//       hook.mounted = function() { renderChart(this.el); };
//       hook.updated = function() { renderChart(this.el); };
//   {% endcolocated_hook %}
//   <canvas dj-hook="Chart"></canvas>
//
// With namespacing (DJUST_CONFIG = {"hook_namespacing": "strict"}) the server
// emits the tag's data-hook as e.g. "myapp.views.DashboardView.Chart"; the
// client registers that exact key, and dj-hook="myapp.views.DashboardView.Chart"
// in the template resolves correctly.
//
// SECURITY BOUNDARY
// -----------------
// The script body is evaluated via `new Function(...)`. This is safe at the
// same trust level as any other JS inside a Django template: the body is
// template-author-controlled, not user-supplied. The {% colocated_hook %} tag
// escapes `</script>` in the body to prevent premature tag close. Users on
// strict CSP without 'unsafe-eval' should not use this feature — register
// hooks via a nonce-bearing <script>window.djust.hooks.X = {...}</script>
// instead.
// ============================================================================

(function initColocatedHooks() {
    globalThis.djust = globalThis.djust || {};

    /**
     * Walk the given root and register every <script type="djust/hook">
     * definition it finds. Idempotent via the data-djust-hook-registered
     * sentinel — safe to call on every DOM morph.
     *
     * @param {ParentNode} [root=document] - where to search
     */
    function extractAndRegister(root) {
        root = root || document;
        const scripts = root.querySelectorAll('script[type="djust/hook"]');
        for (const scriptEl of scripts) {
            if (scriptEl.dataset.djustHookRegistered === '1') continue;
            const hookName = scriptEl.getAttribute('data-hook');
            if (!hookName) {
                // Mark as processed so we don't keep scanning it.
                scriptEl.dataset.djustHookRegistered = '1';
                continue;
            }
            try {
                // Convention: the body assigns to a local `hook` object.
                // We wrap in an IIFE-factory so users don't have to return
                // the hook themselves.
                //
                // `new Function` is intentional here: the template body is
                // inert text (we set type="djust/hook" on the emitter so
                // the browser doesn't auto-execute it). Running it through
                // `new Function` is the registration step. The body is
                // authored by the same people who write the template —
                // the same trust boundary as any other inline template
                // script. The Python emitter escapes `</script>` in all
                // casings to prevent tag-breakout.
                // eslint-disable-next-line no-new-func
                const factory = new Function(
                    'return (function() { const hook = {}; ' +
                    scriptEl.textContent +
                    '; return hook; })()'
                );
                const definition = factory();
                globalThis.djust.hooks = globalThis.djust.hooks || {};
                globalThis.djust.hooks[hookName] = definition;
                scriptEl.dataset.djustHookRegistered = '1';
                if (globalThis.djustDebug) {
                    console.debug('[colocated-hook] Registered %s', hookName);
                }
            } catch (err) {
                if (globalThis.djustDebug) {
                    console.warn(
                        '[colocated-hook] Failed to register %s: %s',
                        hookName,
                        err && err.message
                    );
                }
                scriptEl.dataset.djustHookRegistered = 'error';
            }
        }
    }

    globalThis.djust.extractColocatedHooks = extractAndRegister;
})();
// ============================================================================
// Service Worker Registration — opt-in instant shell + reconnection bridge
// ============================================================================
// Users explicitly opt in from their own init code:
//
//     djust.registerServiceWorker({
//         instantShell: true,
//         reconnectionBridge: true,
//     });
//
// The SW itself is served from /static/djust/service-worker.js and is NOT
// part of the client.js bundle (SW scripts must be separate files).
//
// Instant-shell swap — what works and what doesn't:
//
// - `dj-click`, `dj-submit`, `dj-change`, `dj-input`, and the rest of djust's
//   attribute-based event wiring all work post-swap. They use **document-level
//   event delegation** (not MutationObserver) — a single listener on `document`
//   dispatches based on `e.target.closest('[dj-click]')`, so newly inserted
//   nodes participate automatically without any per-element binding.
//
// - `dj-hook` elements need re-binding after a swap because each hook runs
//   per-element `mount`/`update` callbacks. After replacing the `<main>`
//   innerHTML, we call `djust.reinitAfterDOMUpdate(placeholder)` which scans
//   the swapped subtree for `[dj-hook]`, extracts colocated
//   `<script type="djust/hook">` definitions, and primes dj-virtual /
//   dj-viewport observers.
//
// - **Inline `<script>` tags inside `<main>` will NOT execute**. This is
//   standard browser behavior for `.innerHTML = …`: script nodes are inserted
//   into the tree but not evaluated. If you need JS to run after a shell
//   navigation, either (a) emit the `<script>` OUTSIDE `<main>` in the shell
//   layout so it runs on first load, (b) restructure as a `dj-hook` (which
//   will be re-bound automatically), or (c) use a page-level load listener
//   on the `djust:shell-swapped` CustomEvent dispatched after every swap.
//
// - `registerServiceWorker(options)` is **idempotent**: a second call returns
//   the cached registration promise without re-running `initInstantShell` or
//   `initReconnectionBridge`, so drain listeners and the WS sendMessage
//   patch are applied at most once. **Options from the second call are
//   ignored** — toggling `instantShell: false → true` across calls will NOT
//   start the shell client. Pass both flags on the first call, or reload.
//   If the first call failed (SW register() rejected), the cache is cleared
//   so a retry can succeed.

(function () {
    globalThis.djust = globalThis.djust || {};

    function _swAvailable() {
        return typeof navigator !== 'undefined' && 'serviceWorker' in navigator;
    }

    function _genConnectionId() {
        return 'dj-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    }

    // -----------------------------------------------------------------
    // Instant shell client half
    // -----------------------------------------------------------------

    function initInstantShell() {
        if (!_swAvailable()) return;
        // Only swap when the page we're viewing is the SW-served shell.
        var placeholder = document.querySelector(
            'main[data-djust-shell-placeholder="1"]'
        );
        if (!placeholder) {
            return;
        }
        var url = window.location.href;
        fetch(url, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'X-Djust-Main-Only': '1' },
        })
            .then(function (res) {
                if (!res.ok) {
                    // Server did not honor the header — fall back to full reload.
                    window.location.reload();
                    return null;
                }
                return res.text();
            })
            .then(function (html) {
                if (html === null || html === undefined) return;
                // Replace the placeholder <main> with a real <main> carrying the
                // fresh inner HTML. We use the existing placeholder element
                // so id/classes set by the shell template are preserved.
                placeholder.removeAttribute('data-djust-shell-placeholder');
                // codeql[js/xss] -- html is the server-rendered main content
                // for the current URL (same-origin, trusted). No user input
                // reaches this point.
                placeholder.innerHTML = html;
                // Re-run djust's DOM-update hook: binds dj-hook elements,
                // extracts colocated <script type="djust/hook"> definitions,
                // and primes dj-virtual / dj-viewport observers in the swapped
                // region. Without this, dj-hook content inside <main> stays
                // inert after a shell navigation (dj-click / dj-submit keep
                // working because those use document-level event delegation
                // and don't need per-element binding).
                if (window.djust && typeof window.djust.reinitAfterDOMUpdate === 'function') {
                    try {
                        window.djust.reinitAfterDOMUpdate(placeholder);
                    } catch (e) {
                        if (globalThis.djustDebug) {
                            console.warn('[sw] reinitAfterDOMUpdate failed after shell swap', e);
                        }
                    }
                }
                // Notify the rest of the client that a shell-swap completed so
                // any listeners that care about navigation can react.
                window.dispatchEvent(new CustomEvent('djust:shell-swapped', {
                    detail: { url: url },
                }));
            })
            .catch(function (err) {
                if (globalThis.djustDebug) {
                    console.warn('[sw] instant shell swap failed; reloading', err);
                }
                window.location.reload();
            });
    }

    // -----------------------------------------------------------------
    // Reconnection bridge client half
    // -----------------------------------------------------------------

    function _waitForWs(callback, attempts) {
        attempts = attempts == null ? 50 : attempts;
        var ws = globalThis.djust && globalThis.djust.ws;
        if (ws) {
            callback(ws);
            return;
        }
        if (attempts <= 0) return;
        setTimeout(function () {
            _waitForWs(callback, attempts - 1);
        }, 100);
    }

    function initReconnectionBridge() {
        if (!_swAvailable()) return;
        var connectionId = _genConnectionId();
        var bridge = {
            connectionId: connectionId,
            bufferedCount: 0,
        };
        globalThis.djust._reconnectBridge = bridge;

        _waitForWs(function (ws) {
            // Monkey-patch sendMessage so that when the WS is not OPEN the
            // serialized payload is handed to the SW instead of dropped.
            if (ws._djustBridgePatched) return;
            ws._djustBridgePatched = true;

            var originalSend = ws.sendMessage.bind(ws);
            ws.sendMessage = function (data) {
                var OPEN = (typeof WebSocket !== 'undefined') ? WebSocket.OPEN : 1;
                var state = ws.ws && ws.ws.readyState;
                if (state !== OPEN) {
                    var payload;
                    try {
                        payload = JSON.stringify(data);
                    } catch (e) {
                        if (globalThis.djustDebug) {
                            console.warn('[sw] cannot serialize WS payload', e);
                        }
                        return;
                    }
                    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
                        navigator.serviceWorker.controller.postMessage({
                            type: 'DJUST_BUFFER',
                            connectionId: connectionId,
                            payload: payload,
                        });
                        bridge.bufferedCount++;
                        if (globalThis.djustDebug) {
                            console.log('[sw] buffered WS payload (#' + bridge.bufferedCount + ')');
                        }
                    }
                    return;
                }
                return originalSend(data);
            };
        });

        // Listen for drain replies from the SW.
        if (navigator.serviceWorker) {
            navigator.serviceWorker.addEventListener('message', function (event) {
                var msg = event.data;
                if (!msg || msg.type !== 'DJUST_DRAIN_REPLY') return;
                if (msg.connectionId !== connectionId) return;
                var ws = globalThis.djust && globalThis.djust.ws;
                if (!ws || !ws.ws) return;
                var OPEN = (typeof WebSocket !== 'undefined') ? WebSocket.OPEN : 1;
                if (ws.ws.readyState !== OPEN) return;
                var messages = msg.messages || [];
                for (var i = 0; i < messages.length; i++) {
                    try {
                        ws.ws.send(messages[i]);
                    } catch (e) {
                        if (globalThis.djustDebug) {
                            console.warn('[sw] replay send failed', e);
                        }
                        break;
                    }
                }
                bridge.bufferedCount = 0;
                if (globalThis.djustDebug) {
                    console.log('[sw] drained ' + messages.length + ' buffered WS payloads');
                }
            });
        }

        // When the WS (re)connects, request a drain from the SW.
        window.addEventListener('djust:ws-open', function () {
            if (navigator.serviceWorker && navigator.serviceWorker.controller) {
                navigator.serviceWorker.controller.postMessage({
                    type: 'DJUST_DRAIN',
                    connectionId: connectionId,
                });
            }
        });
    }

    // -----------------------------------------------------------------
    // v0.6.0 — VDOM cache + state snapshot
    // -----------------------------------------------------------------

    // Map requestId -> resolve() function. Populated by lookupVdom /
    // lookupState; drained by the SW-message listener below.
    var _pendingLookups = {};
    var _requestIdCounter = 0;

    function _nextRequestId(prefix) {
        _requestIdCounter += 1;
        return (prefix || 'rq') + '-' + _requestIdCounter + '-' + Date.now().toString(36);
    }

    function _swController() {
        if (!navigator.serviceWorker) return null;
        return navigator.serviceWorker.controller;
    }

    function initVdomCache() {
        if (!_swAvailable()) return;
        if (!navigator.serviceWorker) return;
        navigator.serviceWorker.addEventListener('message', function (event) {
            var data = event.data;
            if (!data || data.type !== 'VDOM_CACHE_REPLY') return;
            var rid = data.requestId;
            if (rid && _pendingLookups[rid]) {
                try {
                    _pendingLookups[rid](data);
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[sw] VDOM_CACHE_REPLY handler threw', e);
                    }
                }
                delete _pendingLookups[rid];
            }
        });
    }

    function initStateSnapshot() {
        if (!_swAvailable()) return;
        if (!navigator.serviceWorker) return;
        navigator.serviceWorker.addEventListener('message', function (event) {
            var data = event.data;
            if (!data || data.type !== 'STATE_SNAPSHOT_REPLY') return;
            var rid = data.requestId;
            if (rid && _pendingLookups[rid]) {
                try {
                    _pendingLookups[rid](data);
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[sw] STATE_SNAPSHOT_REPLY handler threw', e);
                    }
                }
                delete _pendingLookups[rid];
            }
        });
    }

    function cacheVdom(url, html, version) {
        var ctrl = _swController();
        if (!ctrl) return;
        ctrl.postMessage({
            type: 'VDOM_CACHE',
            url: url,
            html: html,
            version: typeof version === 'number' ? version : 0,
            ts: Date.now(),
        });
    }

    function lookupVdom(url) {
        return new Promise(function (resolve) {
            var ctrl = _swController();
            if (!ctrl) {
                resolve({ hit: false, stale: false, html: null });
                return;
            }
            var rid = _nextRequestId('vdom');
            _pendingLookups[rid] = function (reply) { resolve(reply); };
            ctrl.postMessage({
                type: 'VDOM_CACHE_LOOKUP',
                requestId: rid,
                url: url,
            });
            // Safety timeout so callers are never stuck if the SW goes away.
            setTimeout(function () {
                if (_pendingLookups[rid]) {
                    delete _pendingLookups[rid];
                    resolve({ hit: false, stale: false, html: null });
                }
            }, 500);
        });
    }

    function captureState(url, viewSlug, stateJson) {
        var ctrl = _swController();
        if (!ctrl) return;
        // Clamp payload at 64 KB — defense-in-depth against accidental
        // dumps of large collections. SW also enforces 256 KB.
        if (typeof stateJson !== 'string') return;
        if (stateJson.length > 64 * 1024) {
            if (globalThis.djustDebug) {
                console.warn('[sw] STATE_SNAPSHOT payload > 64KB; dropping');
            }
            return;
        }
        ctrl.postMessage({
            type: 'STATE_SNAPSHOT',
            url: url,
            view_slug: viewSlug,
            state_json: stateJson,
            ts: Date.now(),
        });
    }

    function lookupState(url) {
        return new Promise(function (resolve) {
            var ctrl = _swController();
            if (!ctrl) {
                resolve({ hit: false, view_slug: null, state_json: null });
                return;
            }
            var rid = _nextRequestId('state');
            _pendingLookups[rid] = function (reply) { resolve(reply); };
            ctrl.postMessage({
                type: 'STATE_SNAPSHOT_LOOKUP',
                requestId: rid,
                url: url,
            });
            setTimeout(function () {
                if (_pendingLookups[rid]) {
                    delete _pendingLookups[rid];
                    resolve({ hit: false, view_slug: null, state_json: null });
                }
            }, 500);
        });
    }

    // -----------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------

    // Idempotency guard for registerServiceWorker. Calling it twice is a
    // common init pattern (theme switch, settings toggle, dev-mode reload)
    // and without this guard each call adds another drain listener and
    // another sendMessage wrapper, causing buffered replays to double.
    var _registerPromise = null;
    var _bridgeInitialized = false;
    var _shellInitialized = false;
    var _vdomCacheInitialized = false;
    var _stateSnapshotInitialized = false;

    globalThis.djust.registerServiceWorker = function (options) {
        options = options || {};
        if (_registerPromise) {
            if (globalThis.djustDebug) {
                console.log('[sw] registerServiceWorker called again; returning cached registration');
            }
            return _registerPromise;
        }
        if (!_swAvailable()) {
            if (globalThis.djustDebug) {
                console.warn('[sw] navigator.serviceWorker unavailable; opt-in features disabled');
            }
            // Don't cache a null — allow a later call after the env gains SW
            // support (e.g. polyfill) to still try.
            return Promise.resolve(null);
        }
        var swUrl = options.swUrl || '/static/djust/service-worker.js';
        var scope = options.scope || '/';
        _registerPromise = (async function () {
            var registration = null;
            try {
                registration = await navigator.serviceWorker.register(swUrl, { scope: scope });
            } catch (err) {
                if (globalThis.djustDebug) {
                    console.warn('[sw] registration failed', err);
                }
                // Reset so a later call after fixing the cause can retry.
                _registerPromise = null;
                return null;
            }
            if (options.instantShell && !_shellInitialized) {
                _shellInitialized = true;
                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', initInstantShell);
                } else {
                    initInstantShell();
                }
            }
            if (options.reconnectionBridge && !_bridgeInitialized) {
                _bridgeInitialized = true;
                initReconnectionBridge();
            }
            if (options.vdomCache && !_vdomCacheInitialized) {
                _vdomCacheInitialized = true;
                initVdomCache();
            }
            if (options.stateSnapshot && !_stateSnapshotInitialized) {
                _stateSnapshotInitialized = true;
                initStateSnapshot();
            }
            return registration;
        })();
        return _registerPromise;
    };

    // Exposed for tests and for the navigation / popstate code paths.
    globalThis.djust._sw = {
        initInstantShell: initInstantShell,
        initReconnectionBridge: initReconnectionBridge,
        initVdomCache: initVdomCache,
        initStateSnapshot: initStateSnapshot,
        cacheVdom: cacheVdom,
        lookupVdom: lookupVdom,
        captureState: captureState,
        lookupState: lookupState,
    };
})();

// Form polish — v0.5.1 batch (dj-no-submit, dj-trigger-action)
//
// dj-no-submit="enter"
//   Prevent form submission when the user presses Enter in a text-ish input.
//   Stops the #1 form UX annoyance: users press Enter to confirm a field and
//   accidentally submit the entire form. Multi-line inputs (textarea) and
//   submit buttons are exempt — pressing Enter in a textarea still inserts a
//   newline, and clicking a submit button still submits.
//
// dj-trigger-action
//   After a successful djust validation round-trip (no server error), bridge
//   to a standard HTML form POST. Essential for OAuth redirects, payment
//   gateway handoffs, and anywhere the final step needs a real browser POST.
//   Usage: the server calls ``self.trigger_submit(selector)`` (or pushes a
//   ``djust:trigger-submit`` event with ``{"selector": "#form-id"}``); the
//   client finds the matching form and calls its native ``.submit()``.

const _TEXT_INPUT_TYPES = new Set([
    'text', 'search', 'email', 'url', 'tel', 'password', 'number'
]);

function _isEnterKey(event) {
    // IME composition: CJK users press Enter to confirm an IME candidate —
    // that keypress MUST NOT be treated as a form-submit trigger. Browsers
    // signal this via ``event.isComposing`` (keydown fired during composition)
    // and the legacy ``event.keyCode === 229`` on some platforms.
    if (event.isComposing || event.keyCode === 229) return false;
    return event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
}

function _isEligibleEnterTarget(target) {
    if (!target || target.tagName !== 'INPUT') return false;
    const type = (target.type || 'text').toLowerCase();
    return _TEXT_INPUT_TYPES.has(type);
}

function _installFormPolishListeners() {
    // dj-no-submit="enter" — swallow Enter-key submits from text inputs.
    document.addEventListener('keydown', function (e) {
        if (!_isEnterKey(e)) return;
        const target = e.target;
        if (!_isEligibleEnterTarget(target)) return;
        const form = target.closest('form[dj-no-submit]');
        if (!form) return;
        const modes = (form.getAttribute('dj-no-submit') || '')
            .split(/[,\s]+/)
            .map(s => s.trim())
            .filter(Boolean);
        if (modes.length === 0 || modes.includes('enter')) {
            e.preventDefault();
            if (globalThis.djustDebug) {
                djLog('[form-polish] suppressed Enter-key submit on', form);
            }
        }
    }, { capture: true });

    // dj-trigger-action — native POST bridge. Listen for a push event from
    // the server carrying ``{"selector": "..."}`` (or "form_id"). Find the
    // form, ensure it carries ``dj-trigger-action``, and submit it natively.
    const handleTriggerAction = function (detail) {
        if (!detail) return;
        const selector = detail.selector || (detail.form_id ? '#' + detail.form_id : null);
        if (!selector) return;
        let form;
        try {
            form = document.querySelector(selector);
        } catch (err) {
            if (globalThis.djustDebug) {
                djLog('[form-polish] invalid djust:trigger-submit selector:', selector, err);
            }
            return;
        }
        if (!form || form.tagName !== 'FORM') {
            if (globalThis.djustDebug) {
                djLog('[form-polish] djust:trigger-submit: no matching <form> for', selector);
            }
            return;
        }
        // SECURITY: this attribute check is load-bearing, not defense-in-depth.
        // A server compromise that could push ``trigger_submit("#any-form")``
        // would otherwise be able to submit arbitrary forms on the page. The
        // ``dj-trigger-action`` attribute is the explicit author opt-in that
        // limits the blast radius. Do NOT simplify this check away.
        if (!form.hasAttribute('dj-trigger-action')) {
            if (globalThis.djustDebug) {
                djLog('[form-polish] refusing to submit form without dj-trigger-action:', form);
            }
            return;
        }
        // Native submit — triggers the browser's normal POST flow, bypassing
        // djust's WS handler for this final step.
        form.submit();
    };

    // Server-pushed events arrive as a ``djust:push_event`` CustomEvent on
    // ``window`` with ``detail = {event, payload}``. Filter to the
    // ``djust:trigger-submit`` subtype and forward the payload.
    window.addEventListener('djust:push_event', function (e) {
        if (!e || !e.detail || e.detail.event !== 'djust:trigger-submit') return;
        handleTriggerAction(e.detail.payload);
    });
}

// Install on initial load and keep it simple — the keydown listener attaches
// to ``document`` so DOM morphs don't need to re-register.
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installFormPolishListeners);
    } else {
        _installFormPolishListeners();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.formPolish = {
    _isEnterKey,
    _isEligibleEnterTarget,
};

// dj-dialog — native <dialog> modal integration (v0.5.1 P2)
//
// Usage:
//   <dialog id="settings" dj-dialog="open">...</dialog>
//
// When the attribute value changes from close → open, showModal() is called
// (which adds backdrop, focus-trap, and Escape handling — all browser-native).
// When it changes from open → close, close() is called.
//
// Leverages the HTML <dialog> element's own modal behavior so djust doesn't
// re-implement focus management. A MutationObserver watches every <dialog>
// on the page; VDOM morphs that swap the dj-dialog value fire the right
// showModal/close call automatically.

function _syncDialogState(el) {
    if (!(el instanceof HTMLDialogElement)) return;
    const state = (el.getAttribute('dj-dialog') || '').trim().toLowerCase();
    if (state === 'open') {
        if (!el.open) {
            try { el.showModal(); }
            catch (_e) {
                // Some browsers throw if the element is already modal or
                // in an inconsistent DOM state — fall back to the boolean
                // open attribute so the dialog is at least visible.
                el.setAttribute('open', '');
            }
        }
    } else if (state === 'close' || state === 'closed') {
        if (el.open) el.close();
    }
}

function _syncAllDialogs(root) {
    const scope = root || document;
    const dialogs = scope.querySelectorAll('dialog[dj-dialog]');
    dialogs.forEach(_syncDialogState);
}

function _installDjDialogObserver() {
    // Initial pass — handle dialogs rendered at page load.
    _syncAllDialogs();

    // Watch for attribute changes on any <dialog> in the tree. Single
    // document-level observer rather than per-element listeners so VDOM
    // morphs that swap dj-dialog pick it up without re-registration.
    const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'attributes' && m.attributeName === 'dj-dialog') {
                if (m.target instanceof HTMLDialogElement) {
                    _syncDialogState(m.target);
                }
            } else if (m.type === 'childList') {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType === 1) {
                        if (node instanceof HTMLDialogElement && node.hasAttribute('dj-dialog')) {
                            _syncDialogState(node);
                        } else if (node.querySelectorAll) {
                            _syncAllDialogs(node);
                        }
                    }
                });
            }
        });
    });
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['dj-dialog'],
        subtree: true,
        childList: true,
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjDialogObserver);
    } else {
        _installDjDialogObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djDialog = {
    _syncDialogState,
    _syncAllDialogs,
};
/**
 * Dev-mode error overlay — Next.js/Vite-style full-screen error display.
 *
 * Listens for the `djust:error` CustomEvent (dispatched by 03-websocket.js
 * and 03b-sse.js when the server sends `type: "error"` frames) and renders
 * an in-browser panel with the error message, triggering event, Python
 * traceback, hint, and validation details when present.
 *
 * Only active when `window.DEBUG_MODE === true` (set by the `djust_tags`
 * template tag when Django DEBUG=True). Production builds receive no
 * overlay at all — the server strips `traceback` / `debug_detail` / `hint`
 * in non-DEBUG mode, so the overlay would have nothing interesting to show.
 *
 * Dismissal: Escape key, the close button, or clicking the backdrop.
 * Idempotent — a second error while the overlay is open replaces the
 * content rather than stacking panels.
 */

(function initErrorOverlay() {
    const OVERLAY_ID = 'djust-error-overlay';
    const STYLE_ID = 'djust-error-overlay-style';

    function _escape(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = `
#${OVERLAY_ID} {
    position: fixed; inset: 0; z-index: 2147483646;
    background: rgba(0,0,0,0.72);
    display: flex; align-items: flex-start; justify-content: center;
    padding: 40px 20px; overflow-y: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
#${OVERLAY_ID} .djust-eo-panel {
    background: #1a1a1a; color: #e8e8e8;
    border: 1px solid #5a2626; border-radius: 6px;
    width: 100%; max-width: 960px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.6);
    overflow: hidden;
}
#${OVERLAY_ID} .djust-eo-header {
    background: #3a1515; color: #ffb4b4;
    padding: 14px 20px;
    display: flex; justify-content: space-between; align-items: center;
    font-weight: 600; font-size: 14px;
    border-bottom: 1px solid #5a2626;
}
#${OVERLAY_ID} .djust-eo-close {
    background: transparent; border: none; color: #ffb4b4;
    font-size: 22px; line-height: 1; cursor: pointer;
    padding: 0 4px;
}
#${OVERLAY_ID} .djust-eo-close:hover { color: #fff; }
#${OVERLAY_ID} .djust-eo-body { padding: 18px 22px; }
#${OVERLAY_ID} .djust-eo-error {
    font-size: 15px; color: #ff8a8a;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    margin: 0 0 14px 0; word-break: break-word; white-space: pre-wrap;
}
#${OVERLAY_ID} .djust-eo-meta {
    color: #8a8a8a; font-size: 12px; margin-bottom: 12px;
}
#${OVERLAY_ID} .djust-eo-meta code {
    background: #2a2a2a; color: #c8c8c8; padding: 2px 6px; border-radius: 3px;
}
#${OVERLAY_ID} .djust-eo-section-title {
    color: #bbb; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.05em;
    margin: 16px 0 6px 0;
}
#${OVERLAY_ID} pre.djust-eo-trace {
    background: #0e0e0e; border: 1px solid #2a2a2a; border-radius: 4px;
    padding: 12px 14px; margin: 0;
    color: #d0d0d0; font-size: 12px; line-height: 1.5;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    overflow-x: auto; white-space: pre; max-height: 360px;
}
#${OVERLAY_ID} .djust-eo-hint {
    background: #1f2a1f; border-left: 3px solid #6fbf73;
    color: #d2ebd4; padding: 10px 14px; border-radius: 3px;
    font-size: 13px; line-height: 1.5;
}
#${OVERLAY_ID} .djust-eo-footer {
    color: #666; font-size: 11px; padding: 10px 22px;
    border-top: 1px solid #2a2a2a; background: #151515;
}
`;
        document.head.appendChild(style);
    }

    function _render(detail) {
        _injectStyles();
        const errorText = detail.error || 'Server error';
        const eventName = detail.event || null;
        const traceback = detail.traceback || null;
        const hint = detail.hint || null;
        const debugDetail = detail.debug_detail || null;
        const validation = detail.validation_details || null;

        let existing = document.getElementById(OVERLAY_ID);
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.setAttribute('role', 'alertdialog');
        overlay.setAttribute('aria-label', 'djust dev error');

        const panel = document.createElement('div');
        panel.className = 'djust-eo-panel';
        panel.addEventListener('click', (e) => e.stopPropagation());

        let html = '';
        html += '<div class="djust-eo-header">';
        html += '<span>djust · server error</span>';
        html += '<button type="button" class="djust-eo-close" aria-label="Dismiss">&times;</button>';
        html += '</div>';
        html += '<div class="djust-eo-body">';
        html += `<p class="djust-eo-error">${_escape(errorText)}</p>`;
        if (eventName) {
            html += `<div class="djust-eo-meta">Triggered by event <code>${_escape(eventName)}</code></div>`;
        }
        if (debugDetail && debugDetail !== errorText) {
            html += '<div class="djust-eo-section-title">Detail</div>';
            html += `<pre class="djust-eo-trace">${_escape(debugDetail)}</pre>`;
        }
        if (traceback) {
            html += '<div class="djust-eo-section-title">Traceback</div>';
            html += `<pre class="djust-eo-trace">${_escape(traceback)}</pre>`;
        }
        if (hint) {
            html += '<div class="djust-eo-section-title">Hint</div>';
            html += `<div class="djust-eo-hint">${_escape(hint)}</div>`;
        }
        if (validation) {
            html += '<div class="djust-eo-section-title">Validation</div>';
            html += `<pre class="djust-eo-trace">${_escape(JSON.stringify(validation, null, 2))}</pre>`;
        }
        html += '</div>';
        html += '<div class="djust-eo-footer">Press <kbd>Esc</kbd> to dismiss · shown only when Django DEBUG=True</div>';
        panel.innerHTML = html;
        overlay.appendChild(panel);

        const dismiss = () => overlay.remove();
        overlay.addEventListener('click', dismiss);
        panel.querySelector('.djust-eo-close').addEventListener('click', dismiss);

        document.body.appendChild(overlay);
        return overlay;
    }

    function _onKeyDown(e) {
        if (e.key !== 'Escape') return;
        const overlay = document.getElementById(OVERLAY_ID);
        if (overlay) overlay.remove();
    }

    function _onError(e) {
        if (!window.DEBUG_MODE) return;
        const detail = (e && e.detail) || {};
        _render(detail);
    }

    if (typeof window !== 'undefined') {
        window.addEventListener('djust:error', _onError);
        window.addEventListener('keydown', _onKeyDown);
        // Expose for tests and for manual triggering from devtools.
        window.djustErrorOverlay = {
            show: (detail) => _render(detail || {}),
            dismiss: () => {
                const o = document.getElementById(OVERLAY_ID);
                if (o) o.remove();
            },
        };
    }
})();

// dj-mutation — declarative DOM mutation → server event (v0.6.0)
//
// Fires a server event when attributes or children of the marked element
// change. Replaces the custom dj-hook authors had to write to bridge
// third-party widgets (charts, maps, rich-text editors) that mutate the
// DOM outside djust's control.
//
// Usage:
//   <div dj-mutation="handle_change" dj-mutation-attr="class,style">
//   <div dj-mutation="on_children_update">
//   <div dj-mutation="on_change" dj-mutation-debounce="300">
//
// Semantics:
//   - If dj-mutation-attr="a,b,c" is set, observe attribute changes on
//     those attrs and dispatch {mutation: "attributes", attrs: [...]}.
//   - Otherwise observe childList changes and dispatch
//     {mutation: "childList", added: N, removed: N}.
//   - dj-mutation-debounce (ms, default 150) coalesces bursts so a chart
//     library re-rendering 50 times in 10ms produces one server event.
//
// Dispatch path:
//   1. A local cancelable `dj-mutation-fire` CustomEvent bubbles from
//      the element, carrying detail={handler, payload}. Application
//      code can preventDefault() to short-circuit the server call.
//   2. If not cancelled, the payload is forwarded to the server via
//      the standard djust event pipeline (window.djust.handleEvent),
//      invoking the @event_handler method named in dj-mutation=.
//
// Don't list sensitive attributes (e.g. password field `value`) in
// dj-mutation-attr: the attribute name is included in the server
// payload, which is noisy for audit logs.

const _djMutationObservers = new WeakMap();

function _parseAttrList(raw) {
    return (raw || '')
        .split(',')
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; });
}

function _installDjMutationFor(el) {
    if (_djMutationObservers.has(el)) return;
    const handlerName = el.getAttribute('dj-mutation');
    if (!handlerName) return;

    const attrList = _parseAttrList(el.getAttribute('dj-mutation-attr'));
    const rawDebounce = parseInt(el.getAttribute('dj-mutation-debounce') || '150', 10);
    const debounceMs = Number.isFinite(rawDebounce) && rawDebounce >= 0 ? rawDebounce : 150;

    let timer = null;
    let pending = null;

    function _dispatch() {
        const payload = pending;
        pending = null;
        timer = null;
        if (!payload) return;
        // Local CustomEvent first — lets application code intercept or
        // short-circuit via preventDefault before the server roundtrip.
        const ev = new CustomEvent('dj-mutation-fire', {
            bubbles: true,
            cancelable: true,
            detail: { handler: handlerName, payload: payload },
        });
        const proceed = el.dispatchEvent(ev);
        if (!proceed) return;
        // Route to the standard djust event pipeline so the server-side
        // handler named in dj-mutation="..." actually runs.
        if (globalThis.djust && typeof globalThis.djust.handleEvent === 'function') {
            globalThis.djust.handleEvent(handlerName, payload);
        }
    }

    const observer = new MutationObserver(function (mutations) {
        let attrsChanged = null;
        let added = 0, removed = 0;
        mutations.forEach(function (m) {
            if (m.type === 'attributes') {
                attrsChanged = attrsChanged || new Set();
                attrsChanged.add(m.attributeName);
            } else if (m.type === 'childList') {
                added += m.addedNodes.length;
                removed += m.removedNodes.length;
            }
        });
        if (attrsChanged) {
            pending = { mutation: 'attributes', attrs: Array.from(attrsChanged) };
        } else if (added || removed) {
            pending = { mutation: 'childList', added: added, removed: removed };
        }
        if (timer) clearTimeout(timer);
        timer = setTimeout(_dispatch, debounceMs);
    });

    const opts = attrList.length > 0
        ? { attributes: true, attributeFilter: attrList }
        : { childList: true };
    observer.observe(el, opts);
    _djMutationObservers.set(el, { observer: observer, clear: function () {
        if (timer) { clearTimeout(timer); timer = null; pending = null; }
    }});
}

function _tearDownDjMutation(el) {
    const entry = _djMutationObservers.get(el);
    if (entry) {
        entry.observer.disconnect();
        // Cancel any in-flight debounced dispatch so a setTimeout
        // doesn't fire on a detached element after removal.
        entry.clear();
        _djMutationObservers.delete(el);
    }
}

function _installDjMutationObserver() {
    document.querySelectorAll('[dj-mutation]').forEach(_installDjMutationFor);

    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            // #879: if the dj-mutation attribute itself is removed from an
            // already-observed element, tear down the observer so we don't
            // leave a stale MutationObserver attached.
            if (m.type === 'attributes' && m.attributeName === 'dj-mutation') {
                const target = m.target;
                if (target && target.nodeType === 1 && !target.hasAttribute('dj-mutation')) {
                    if (_djMutationObservers.has(target)) _tearDownDjMutation(target);
                }
                return;
            }
            m.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (node.hasAttribute && node.hasAttribute('dj-mutation')) {
                    _installDjMutationFor(node);
                }
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-mutation]').forEach(_installDjMutationFor);
                }
            });
            m.removedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (_djMutationObservers.has(node)) _tearDownDjMutation(node);
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-mutation]').forEach(_tearDownDjMutation);
                }
            });
        });
    });
    rootObserver.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['dj-mutation'],
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjMutationObserver);
    } else {
        _installDjMutationObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djMutation = {
    _installDjMutationFor,
    _tearDownDjMutation,
};

// dj-sticky-scroll — auto-scroll preservation (v0.6.0)
//
// Keeps a scrollable container pinned to the bottom when new content is
// appended, but backs off when the user scrolls up to read history.
// Resumes auto-scroll when the user scrolls back to the bottom.
//
// Usage:
//   <div dj-sticky-scroll style="overflow-y: auto; height: 400px">
//       {% for msg in messages %}
//       <div>{{ msg.text }}</div>
//       {% endfor %}
//   </div>
//
// Use cases: chat messages, log viewers, terminal output, live feeds.
// Replaces the custom dj-hook authors otherwise wrote with ~30 lines of
// scroll-position math.

const _djStickyObservers = new WeakMap();
// 1px tolerance for sub-pixel scroll math (clientHeight and scrollHeight
// can round differently in some layouts).
const _STICKY_TOLERANCE = 1;

function _isAtBottom(el) {
    return el.scrollTop + el.clientHeight >= el.scrollHeight - _STICKY_TOLERANCE;
}

function _scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
}

function _installDjStickyScrollFor(el) {
    if (_djStickyObservers.has(el)) return;

    // Seed: assume we start at bottom so the first append scrolls.
    el._djStickyAtBottom = true;
    // #881: Deliberately scroll-to-bottom on install regardless of current
    // position. Matches Phoenix's phx-auto-scroll / Ember's scroll-into-view
    // behavior — sticky-scroll is an "opt into bottom-pinning" attribute,
    // and authors typically want the initial view to show the most recent
    // content (chat, log output).
    _scrollToBottom(el);

    function onScroll() {
        el._djStickyAtBottom = _isAtBottom(el);
    }
    el.addEventListener('scroll', onScroll, { passive: true });

    const observer = new MutationObserver(function () {
        if (el._djStickyAtBottom) {
            _scrollToBottom(el);
        }
    });
    observer.observe(el, { childList: true, subtree: true });

    _djStickyObservers.set(el, { observer: observer, onScroll: onScroll });
}

function _tearDownDjStickyScroll(el) {
    const entry = _djStickyObservers.get(el);
    if (!entry) return;
    entry.observer.disconnect();
    el.removeEventListener('scroll', entry.onScroll);
    _djStickyObservers.delete(el);
    delete el._djStickyAtBottom;
}

function _installDjStickyScrollObserver() {
    document.querySelectorAll('[dj-sticky-scroll]').forEach(_installDjStickyScrollFor);

    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            // #879: if the dj-sticky-scroll attribute itself is removed from
            // an already-observed element, tear down the observer so we
            // don't leave a stale MutationObserver + scroll listener attached.
            if (m.type === 'attributes' && m.attributeName === 'dj-sticky-scroll') {
                const target = m.target;
                if (target && target.nodeType === 1 && !target.hasAttribute('dj-sticky-scroll')) {
                    if (_djStickyObservers.has(target)) _tearDownDjStickyScroll(target);
                }
                return;
            }
            m.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (node.hasAttribute && node.hasAttribute('dj-sticky-scroll')) {
                    _installDjStickyScrollFor(node);
                }
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-sticky-scroll]').forEach(_installDjStickyScrollFor);
                }
            });
            m.removedNodes.forEach(function (node) {
                if (node.nodeType !== 1) return;
                if (_djStickyObservers.has(node)) _tearDownDjStickyScroll(node);
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-sticky-scroll]').forEach(_tearDownDjStickyScroll);
                }
            });
        });
    });
    rootObserver.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['dj-sticky-scroll'],
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjStickyScrollObserver);
    } else {
        _installDjStickyScrollObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djStickyScroll = {
    _installDjStickyScrollFor,
    _tearDownDjStickyScroll,
    _isAtBottom,
};

// dj-track-static — stale asset detection on WS reconnect (v0.6.0)
//
// Phoenix phx-track-static parity. Without this, clients on long-lived
// WebSocket connections silently run stale JavaScript after a server
// deploy — zero-downtime on the server, broken behavior on connected
// clients.
//
// Usage:
//   <script dj-track-static src="{% static 'js/app.abc123.js' %}"></script>
//   <link dj-track-static rel="stylesheet" href="...">
//   <script dj-track-static="reload" src="..."></script>
//
// Behavior:
//   On the FIRST djust:ws-reconnected event the snapshot is empty, so
//   the first connect seeds it. On every subsequent reconnect, each
//   [dj-track-static] element's src/href is compared against the
//   initial snapshot. If any differ, dispatch a dj:stale-assets
//   CustomEvent (detail = { changed: [...urls] }). If any of the
//   changed elements carried dj-track-static="reload", call
//   window.location.reload() instead.

// #880: Using `Map` (not `WeakMap`) deliberately: the reconnect-diff step
// iterates ALL tracked elements to compare snapshot URLs with current URLs.
// WeakMap does not support iteration, so we accept the weak-reference
// tradeoff. If an element is removed from the DOM, the `isConnected` check
// in `_checkStale` skips it — we don't leak observers, just map entries
// that are cleared on the next `_snapshotAssets()` seed.
let _djTrackStaticSnapshot = null;

function _urlOf(el) {
    return el.getAttribute('src') || el.getAttribute('href') || '';
}

function _snapshotAssets() {
    // See #880 comment above: Map chosen for iteration support.
    const snap = new Map();
    document.querySelectorAll('[dj-track-static]').forEach(function (el) {
        snap.set(el, _urlOf(el));
    });
    return snap;
}

function _checkStale() {
    // Normally the snapshot is seeded at DOMContentLoaded and
    // _checkStale is never called with a null snapshot. This branch
    // only triggers after _resetSnapshot() — it's a test hook for
    // exercising the seed path without reloading the document.
    if (_djTrackStaticSnapshot === null) {
        _djTrackStaticSnapshot = _snapshotAssets();
        return null;
    }
    const changed = [];
    let shouldReload = false;
    _djTrackStaticSnapshot.forEach(function (oldUrl, el) {
        // If the tracked element is no longer in the document (VDOM
        // morphed it out entirely), we can't tell whether its
        // replacement carries a new URL or was simply removed. Treat
        // it as unchanged — avoids false-positive reloads on benign
        // morphs. A future enhancement could re-query live
        // [dj-track-static] elements and diff by URL identity.
        if (!el.isConnected) return;
        const currentUrl = _urlOf(el);
        if (currentUrl !== oldUrl) {
            changed.push(currentUrl);
            if ((el.getAttribute('dj-track-static') || '').trim() === 'reload') {
                shouldReload = true;
            }
        }
    });
    return { changed: changed, shouldReload: shouldReload };
}

function _onWsReconnected() {
    const result = _checkStale();
    if (!result) return;  // First connect — snapshot was just seeded.
    if (result.changed.length === 0) return;
    if (result.shouldReload) {
        window.location.reload();
        return;
    }
    document.dispatchEvent(new CustomEvent('dj:stale-assets', {
        detail: { changed: result.changed },
    }));
}

function _installDjTrackStatic() {
    // Seed snapshot on page load (the \"first connect\" for SSR / full page
    // load case). The subsequent djust:ws-reconnected events compare
    // against this baseline.
    _djTrackStaticSnapshot = _snapshotAssets();
    document.addEventListener('djust:ws-reconnected', _onWsReconnected);
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjTrackStatic);
    } else {
        _installDjTrackStatic();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djTrackStatic = {
    _snapshotAssets,
    _checkStale,
    _onWsReconnected,
    _resetSnapshot: function () { _djTrackStaticSnapshot = null; },
};

// dj-layout — runtime layout switching (v0.6.0)
//
// Handles the {"type": "layout", "path": ..., "html": ...} WebSocket
// frame emitted by LayoutMixin.set_layout(). Swaps the document body
// with the new layout while physically moving the live
// [dj-root] / [data-djust-root] element into the new layout's
// root-shaped slot — preserving all inner LiveView state (form values,
// scroll position, focused element, dj-hook bookkeeping, third-party
// widget instances hanging off live nodes).
//
// Use case: toggling between admin / public layouts, fullscreen mode
// for editors, minimal layout during onboarding.
//
// Known limitations (v1):
//   - <head> tags are NOT merged. If the new layout needs different
//     stylesheets or scripts, add them to the original layout's <head>.
//   - Dispatches djust:layout-changed on document for app-level hooks.

function _findRoot(scope) {
    // Sticky LiveViews (Phase B): exclude [dj-sticky-root] wrappers so
    // a sticky child embedded via {% live_render ... sticky=True %}
    // does not masquerade as the layout root. Sticky subtrees carry
    // [dj-sticky-root] on their outermost [dj-view]; the parent view's
    // [dj-root] is always outside of them.
    return (
        scope.querySelector('[data-djust-root]:not([dj-sticky-root])')
        || scope.querySelector('[dj-root]:not([dj-sticky-root])')
        || scope.querySelector('[data-djust-root]')
        || scope.querySelector('[dj-root]')
    );
}

function _applyLayout(payload) {
    const html = payload && payload.html;
    if (!html || typeof html !== 'string') {
        if (globalThis.djustDebug) console.warn('[djust:layout] empty html payload; ignoring');
        return;
    }
    let newDoc;
    try {
        newDoc = new DOMParser().parseFromString(html, 'text/html');
    } catch (_e) {
        console.warn('[djust:layout] failed to parse incoming layout HTML');
        return;
    }
    const newRoot = _findRoot(newDoc);
    const currentRoot = _findRoot(document);
    if (!newRoot || !currentRoot) {
        console.warn(
            '[djust:layout] could not locate dj-root / data-djust-root in ' +
            (newRoot ? 'current document' : 'incoming layout') + '; ignoring swap'
        );
        return;
    }
    // Physically move the current (live) root into the new layout's body
    // in place of the new layout's root-shaped placeholder. This
    // preserves every inner DOM node, including focused elements and
    // third-party-widget references.
    newRoot.parentNode.replaceChild(currentRoot, newRoot);
    // Swap the live body with the reconstructed body. document.body
    // adoption is automatic across DOMParser documents in modern
    // browsers.
    const newBody = newDoc.body;
    if (newBody) {
        document.body.replaceWith(newBody);
    }
    document.dispatchEvent(new CustomEvent('djust:layout-changed', {
        detail: { path: payload.path || null },
    }));
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djLayout = {
    applyLayout: _applyLayout,
    _findRoot,
};

// dj-transition — declarative CSS enter/leave transitions (v0.6.0)
//
// Phoenix JS.transition parity. Orchestrates a three-phase CSS class
// application (start → active → end) so template authors can drive
// CSS transitions without writing a dj-hook.
//
// Usage:
//   <div dj-transition="opacity-0 transition-opacity-300 opacity-100">
//     Fades in from 0 to 100 opacity over 300 ms.
//   </div>
//
// The attribute value is three space-separated tokens — start, active,
// end — each a single class name. Commas, parens, or other separators
// are NOT supported: `classList.add` would throw InvalidCharacterError
// on the resulting tokens. (A future enhancement could accept
// parenthesised multi-class groups; one-class-per-phase is the common
// case and keeps the parsing trivial.)
//
// Re-trigger from JS: calling `el.setAttribute('dj-transition', spec)`
// re-runs the sequence, even when `spec` is identical to the current
// value — MutationObserver fires on any attribute set, not only value
// changes.
//
// Lifecycle:
//   Phase 1 (start):  applied synchronously when the attribute appears
//                     or changes. Sets the pre-transition state.
//   Phase 2 (active): applied on the next animation frame — the
//                     transition begins. Adding the `transition-*`
//                     classes here ensures the browser sees the start
//                     state first.
//   Phase 3 (end):    applied on the same frame as phase 2 — the
//                     final target state. Kept on the element after
//                     the transition completes.
//
// On `transitionend`, phase-2 classes are removed (they typically
// carry the `transition-*` helper and are not needed once the animation
// has completed). A 600 ms fallback timeout cleans up phase-2 classes if
// `transitionend` never fires (e.g. zero-duration transitions or
// display: none).

const _djTransitionState = new WeakMap();
const _FALLBACK_MS = 600;

function _parseSpec(raw) {
    const input = (raw || '').trim();
    // Reject comma, paren, or bracket separators. `classList.add` throws
    // InvalidCharacterError on tokens containing these characters — catch
    // malformed specs up front and log in debug mode instead of letting
    // the error surface at runtime.
    if (/[,()[\]]/.test(input)) {
        if (globalThis.djustDebug) {
            console.warn('[djust] dj-transition: commas, parens, and brackets are not supported in spec:', raw);
        }
        return null;
    }
    const parts = input.split(/\s+/).filter(Boolean);
    if (parts.length < 3) return null;
    return { start: parts[0], active: parts[1], end: parts[2] };
}

function _runTransition(el, spec) {
    // Cancel any previous sequence on the same element.
    const prev = _djTransitionState.get(el);
    if (prev && prev.fallback) clearTimeout(prev.fallback);
    if (prev && prev.onEnd) el.removeEventListener('transitionend', prev.onEnd);

    // Phase 1 — start state.
    el.classList.add(spec.start);

    const state = {};
    _djTransitionState.set(el, state);

    // Phase 2 + 3 — schedule on the next frame so the browser commits
    // the phase-1 layout before the transition classes land.
    const _raf = globalThis.requestAnimationFrame || function (cb) { return setTimeout(cb, 16); };
    _raf(function () {
        el.classList.remove(spec.start);
        el.classList.add(spec.active);
        el.classList.add(spec.end);

        function cleanup() {
            // Guard against detached elements — if the node has been
            // removed from the DOM before this fires (typically the 600 ms
            // fallback path), skip classList/listener work. classList on
            // a detached node is technically safe but any parentNode
            // access downstream would NPE.
            if (!el.isConnected) {
                _djTransitionState.delete(el);
                return;
            }
            el.classList.remove(spec.active);
            if (state.fallback) clearTimeout(state.fallback);
            el.removeEventListener('transitionend', onEnd);
            _djTransitionState.delete(el);
        }

        function onEnd(ev) {
            // Only react to transitions on THIS element, not bubbled
            // transitions from children.
            if (ev.target !== el) return;
            cleanup();
        }
        state.onEnd = onEnd;
        el.addEventListener('transitionend', onEnd);

        // Fallback in case transitionend never fires.
        state.fallback = setTimeout(cleanup, _FALLBACK_MS);
    });
}

function _installDjTransitionFor(el) {
    const raw = el.getAttribute('dj-transition');
    const spec = _parseSpec(raw);
    if (!spec) return;
    _runTransition(el, spec);
}

function _installDjTransitionObserver() {
    document.querySelectorAll('[dj-transition]').forEach(_installDjTransitionFor);

    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'attributes' && m.attributeName === 'dj-transition') {
                // Attribute changed (including re-asserted with same value) —
                // re-run the sequence so authors can retrigger from JS.
                _installDjTransitionFor(m.target);
            } else if (m.type === 'childList') {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.hasAttribute && node.hasAttribute('dj-transition')) {
                        _installDjTransitionFor(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[dj-transition]').forEach(_installDjTransitionFor);
                    }
                });
            }
        });
    });
    rootObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['dj-transition'],
        subtree: true,
        childList: true,
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installDjTransitionObserver);
    } else {
        _installDjTransitionObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djTransition = {
    _parseSpec,
    _runTransition,
    _installDjTransitionFor,
};

// dj-remove — declarative CSS exit transitions (v0.6.0, phase 2a)
//
// Phoenix JS.hide / phx-remove parity. When a VDOM patch, morph loop,
// or dj-update prune would physically remove an element that carries
// dj-remove="...", djust delays the removal until the CSS transition
// the attribute describes has completed (or a 600 ms fallback fires).
//
// Usage — three-token form (matches dj-transition):
//   <li dj-remove="opacity-100 transition-opacity-300 opacity-0">Toast</li>
//
// Usage — single-token short form:
//   <li dj-remove="fade-out">Toast</li>
//
// The three-token form is identical to dj-transition: phase 1 applies
// the start class, phase 2 (next animation frame) swaps in the active
// class, and phase 3 applies the end class that drives the transition.
// The single-token form applies one class and waits for transitionend.
//
// Lifecycle — the caller in 12-vdom-patch.js invokes
// window.djust.maybeDeferRemoval(node) instead of its usual removeChild
// / replaceChild / el.remove() call. If the element carries
// [dj-remove], we apply the exit classes but do NOT detach the node —
// the element stays connected during the animation so the transition
// actually plays. We physically remove it when `transitionend` fires
// (or when the fallback timer expires).
//
// Override the 600 ms fallback via dj-remove-duration="N" (ms).
//
// Descendants: if a [dj-remove] element has [dj-remove] descendants,
// ONLY the outer element is deferred — descendants travel with it.
// This matches Phoenix.
//
// Cancellation: if a subsequent patch strips the dj-remove attribute
// from a pending element, the pending removal cancels — the applied
// exit classes are stripped, the fallback timer clears, and the
// element stays mounted. Implemented via a per-element MutationObserver
// watching `dj-remove`.
//
// Unlike 41-dj-transition.js, this module does NOT install a
// document-level MutationObserver. dj-remove is a pull API — the patch
// applier reaches out to us via maybeDeferRemoval() — so we don't need
// to watch the DOM for new dj-remove attributes.

const _pendingRemovals = new WeakMap();   // Element -> { fallback, onEnd, observer, spec }
const _REMOVE_FALLBACK_MS = 600;

function _parseRemoveSpec(raw) {
    if (raw === null || raw === undefined) return null;
    const parts = String(raw).trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return null;
    if (parts.length === 1) return { single: parts[0] };
    if (parts.length >= 3) return { start: parts[0], active: parts[1], end: parts[2] };
    // Two tokens — ambiguous, treat as invalid (matches dj-transition).
    if (parts.length === 2) {
        if (globalThis.djustDebug) {
            console.warn('dj-remove: 2-token spec is invalid, use 1 or 3 tokens:', raw);
        }
        return null;
    }
    return null;
}

function _durationFor(el) {
    const raw = el.getAttribute && el.getAttribute('dj-remove-duration');
    if (raw === null || raw === undefined || raw === '') return _REMOVE_FALLBACK_MS;
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n)) return _REMOVE_FALLBACK_MS;
    // Clamp to a sane range so a malformed attribute can't leak a pending
    // removal indefinitely or fire before the frame has had a chance to paint.
    if (n < 0) return 0;
    if (n > 30000) return 30000;
    return n;
}

function _teardownState(el, state) {
    // Shared cleanup for both _finalizeRemoval and _cancelRemoval: clear
    // the fallback timer, remove the transitionend listener, disconnect
    // the MutationObserver, and drop the WeakMap entry. Safe to call
    // with a state object whose fields are partially populated (e.g.
    // before the raf callback has run).
    if (state.fallback) clearTimeout(state.fallback);
    if (state.onEnd) el.removeEventListener('transitionend', state.onEnd);
    if (state.observer) state.observer.disconnect();
    _pendingRemovals.delete(el);
}

function _finalizeRemoval(el) {
    // Called when the animation completes or the fallback fires. Clean up
    // all pending state and physically detach the element. Guard against
    // the element having been moved or already detached by another patch.
    const state = _pendingRemovals.get(el);
    if (!state) return;
    _teardownState(el, state);
    if (el.parentNode) {
        el.parentNode.removeChild(el);
    }
}

function _cancelRemoval(el) {
    // Called when the dj-remove attribute is stripped from a pending
    // element. Revert applied classes, clear the timer, leave the
    // element where it was.
    const state = _pendingRemovals.get(el);
    if (!state) return;
    _teardownState(el, state);
    const spec = state.spec;
    if (spec) {
        if (spec.single) {
            el.classList.remove(spec.single);
        } else {
            el.classList.remove(spec.active);
            el.classList.remove(spec.end);
            el.classList.remove(spec.start);
        }
    }
}

function _runRemove(el, spec) {
    // Phase 1 — start state (three-token form only).
    if (!spec.single) {
        el.classList.add(spec.start);
    }

    const state = { spec: spec };
    _pendingRemovals.set(el, state);

    function onEnd(ev) {
        if (ev.target !== el) return;
        _finalizeRemoval(el);
    }
    state.onEnd = onEnd;
    el.addEventListener('transitionend', onEnd);

    // Fallback in case transitionend never fires.
    state.fallback = setTimeout(function () {
        _finalizeRemoval(el);
    }, _durationFor(el));

    // Watch for cancellation: if the dj-remove attribute is stripped
    // while the removal is pending, cancel and keep the element mounted.
    if (typeof MutationObserver !== 'undefined') {
        const observer = new MutationObserver(function (mutations) {
            for (const m of mutations) {
                if (m.type === 'attributes' && m.attributeName === 'dj-remove') {
                    if (!el.hasAttribute('dj-remove')) {
                        _cancelRemoval(el);
                        return;
                    }
                }
            }
        });
        observer.observe(el, { attributes: true, attributeFilter: ['dj-remove'] });
        state.observer = observer;
    }

    // Phase 2 + 3 — schedule on the next frame so the browser commits
    // the phase-1 layout before the transition classes land.
    const _raf = globalThis.requestAnimationFrame || function (cb) { return setTimeout(cb, 16); };
    _raf(function () {
        if (!_pendingRemovals.has(el)) return;  // Cancelled already.
        if (spec.single) {
            el.classList.add(spec.single);
        } else {
            el.classList.remove(spec.start);
            el.classList.add(spec.active);
            el.classList.add(spec.end);
        }
    });
}

function maybeDeferRemoval(el) {
    // Entry point for 12-vdom-patch.js. Returns TRUE if removal was
    // deferred (caller should SKIP its normal removeChild/remove call);
    // FALSE otherwise (caller continues its normal removal path).
    if (!el || el.nodeType !== 1) return false;
    if (!el.hasAttribute || !el.hasAttribute('dj-remove')) return false;
    // An element with no parent cannot be "deferred from removal" — it's
    // already removed. Let the caller handle its own state.
    if (!el.parentNode) return false;
    if (_pendingRemovals.has(el)) return true;  // Already deferring.
    const spec = _parseRemoveSpec(el.getAttribute('dj-remove'));
    if (!spec) return false;
    _runRemove(el, spec);
    return true;
}

// Export on the global namespace for the patch-applier hook to use,
// and for tests to reach into the internals.
globalThis.djust = globalThis.djust || {};
globalThis.djust.maybeDeferRemoval = maybeDeferRemoval;
globalThis.djust.djRemove = {
    _parseRemoveSpec,
    _durationFor,
    _runRemove,
    _finalizeRemoval,
    _cancelRemoval,
    _teardownState,
    _pendingRemovals,
    _REMOVE_FALLBACK_MS,
    maybeDeferRemoval,
};

// dj-transition-group — declarative enter/leave animation orchestration for
// lists of children (v0.6.0, phase 2c).
//
// React `<TransitionGroup>` / Vue `<transition-group>` parity. Authors mark
// a parent container and specify an enter spec and a leave spec once; djust
// wires those specs onto each child automatically so that new children
// animate in (via the existing `dj-transition` runner) and removed children
// animate out (via the existing `dj-remove` runner). This module does NOT
// re-implement the phase-1/2/3 class-cycling machinery or the pending-removal
// deferral — it orchestrates the two primitives by setting their attributes
// on children.
//
// Usage — long form (preferred):
//   <ul dj-transition-group
//       dj-group-enter="opacity-0 transition-opacity-300 opacity-100"
//       dj-group-leave="opacity-100 transition-opacity-300 opacity-0">
//     {% for t in toasts %}<li>{{ t.text }}</li>{% endfor %}
//   </ul>
//
// Usage — short form (one attribute, pipe-separated):
//   <ul dj-transition-group="fade-in | fade-out">...</ul>
//   <ul dj-transition-group="opacity-0 t-opacity-300 opacity-100 | opacity-100 t-opacity-300 opacity-0">...</ul>
//
// Each half accepts the same shapes as dj-transition / dj-remove:
//   three-token: "start active end" (phase-cycling CSS transition)
//   one-token:   "fade-out" (single-class, transitionend-driven)
//
// Initial children — by default, only the leave spec is copied onto each
// existing child at mount (so an exit animation plays if they're later
// removed, but nothing animates in on first paint). Opt in to initial
// enter animation with the `dj-group-appear` attribute:
//   <ul dj-transition-group dj-group-appear ...>...</ul>
//
// Never overwrites author-specified `dj-transition` or `dj-remove` on a
// child — an escape hatch for per-item overrides.
//
// Implementation note — MutationObserver fires AFTER the DOM mutation, so
// we cannot defer a removal from the parent observer (the child is already
// detached by the time we hear about it). Removal deferral relies entirely
// on `dj-remove` being present on the child BEFORE the removal patch runs.
// That is why this module sets `dj-remove` at ADD time, not at REMOVE time.

const _ENTER_ATTR = 'dj-transition';
const _LEAVE_ATTR = 'dj-remove';
const _GROUP_ATTR = 'dj-transition-group';
const _GROUP_ENTER_ATTR = 'dj-group-enter';
const _GROUP_LEAVE_ATTR = 'dj-group-leave';
const _APPEAR_ATTR = 'dj-group-appear';

const _installedParents = new WeakMap();
let _rootObserverInstalled = false;

function _parseGroupAttr(raw) {
    if (raw === null || raw === undefined) return null;
    const s = String(raw).trim();
    if (s === '') return null;
    if (s.indexOf('|') === -1) return null;
    const parts = s.split('|').map(function (p) { return p.trim(); });
    if (parts.length !== 2) return null;
    if (!parts[0] || !parts[1]) return null;
    return { enter: parts[0], leave: parts[1] };
}

function _resolveSpecs(parent) {
    const longEnter = parent.getAttribute(_GROUP_ENTER_ATTR);
    const longLeave = parent.getAttribute(_GROUP_LEAVE_ATTR);
    const short = _parseGroupAttr(parent.getAttribute(_GROUP_ATTR));
    return {
        enter: longEnter || (short && short.enter) || null,
        leave: longLeave || (short && short.leave) || null,
    };
}

function _handleChildAdded(child, parent, opts) {
    if (!child || child.nodeType !== 1) return;
    const specs = _resolveSpecs(parent);
    if (specs.leave && !child.hasAttribute(_LEAVE_ATTR)) {
        child.setAttribute(_LEAVE_ATTR, specs.leave);
    }
    if (opts && opts.applyEnter && specs.enter && !child.hasAttribute(_ENTER_ATTR)) {
        child.setAttribute(_ENTER_ATTR, specs.enter);
        // dj-transition's document-level observer picks up the attribute
        // mutation and runs the phase sequence.
    }
}

function _install(parent) {
    if (!parent || parent.nodeType !== 1) return;
    if (_installedParents.has(parent)) return;
    const appear = parent.hasAttribute(_APPEAR_ATTR);
    const initialChildren = Array.prototype.slice.call(parent.children);
    initialChildren.forEach(function (child) {
        _handleChildAdded(child, parent, { applyEnter: appear });
    });
    if (typeof MutationObserver === 'undefined') {
        _installedParents.set(parent, { observer: null });
        return;
    }
    const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type !== 'childList') return;
            m.addedNodes.forEach(function (node) {
                _handleChildAdded(node, parent, { applyEnter: true });
            });
        });
    });
    observer.observe(parent, { childList: true, subtree: false });
    _installedParents.set(parent, { observer: observer });
}

function _uninstall(parent) {
    if (!parent || parent.nodeType !== 1) return;
    const entry = _installedParents.get(parent);
    if (!entry) return;
    if (entry.observer) {
        entry.observer.disconnect();
    }
    _installedParents.delete(parent);
}

function _rescan() {
    document.querySelectorAll('[' + _GROUP_ATTR + ']').forEach(_install);
}

function _installRootObserver() {
    if (_rootObserverInstalled) return;
    _rootObserverInstalled = true;
    _rescan();
    const rootObserver = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            if (m.type === 'attributes' && m.attributeName === _GROUP_ATTR) {
                if (m.target.hasAttribute(_GROUP_ATTR)) {
                    _install(m.target);
                } else {
                    // Attribute stripped: uninstall the per-parent observer so it
                    // stops wiring new children. Symmetric with dj-remove's
                    // cancel-on-strip behavior.
                    _uninstall(m.target);
                }
            } else if (m.type === 'childList') {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.hasAttribute && node.hasAttribute(_GROUP_ATTR)) {
                        _install(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[' + _GROUP_ATTR + ']').forEach(_install);
                    }
                });
                m.removedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.hasAttribute && node.hasAttribute(_GROUP_ATTR)) {
                        _uninstall(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[' + _GROUP_ATTR + ']').forEach(_uninstall);
                    }
                });
            }
        });
    });
    rootObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: [_GROUP_ATTR],
        subtree: true,
        childList: true,
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _installRootObserver);
    } else {
        _installRootObserver();
    }
}

globalThis.djust = globalThis.djust || {};
globalThis.djust.djTransitionGroup = {
    _install: _install,
    _uninstall: _uninstall,
    _rescan: _rescan,
    _handleChildAdded: _handleChildAdded,
    _parseGroupAttr: _parseGroupAttr,
    _resolveSpecs: _resolveSpecs,
    _installedParents: _installedParents,
};

// dj-flip — FLIP-technique list reorder animations
// (v0.6.0, phase 2d — animations milestone finale).
//
// FLIP = First, Last, Invert, Play (Paul Lewis, 2015). When a child
// element reorders within its [dj-flip] parent, we:
//   1. First — read rect BEFORE the reorder (cached on install + after
//      every settled animation).
//   2. Last — read rect AFTER the mutation.
//   3. Invert — apply `transform: translate(-dx, -dy)` with
//      `transition: none`, which visually pins the child at its old
//      position even though the DOM has already moved.
//   4. Play — on the next animation frame, clear the transform and
//      populate `transition: transform <duration>ms <easing>`. The
//      browser animates back to identity = the new position.
//
// This is a hook-free integration: we install a per-parent
// MutationObserver(childList, subtree:false), same architecture as
// 43-dj-transition-group.js. The VDOM patcher's `MoveChild` patch
// calls `insertBefore()` on the same DOM node, so the node's identity
// is preserved and our WeakMap rect cache survives reorders.
//
// Usage:
//   <ul dj-flip>
//     {% for item in items %}<li id="item-{{ item.id }}">{{ item.label }}</li>{% endfor %}
//   </ul>
//
// Tunables (on the parent):
//   dj-flip-duration="500"               — transition duration in ms (default 300).
//   dj-flip-easing="ease-in"             — CSS easing function
//                                          (default cubic-bezier(.2,.8,.2,1)).
//
// Accessibility: respects `prefers-reduced-motion: reduce` — when true,
// we skip all transforms and return silently. Matches dj-transition's
// philosophy of degrading to an instant change.
//
// Keyed lists: FLIP animates items whose DOM identity is preserved by
// the VDOM diff. Rust's `diff_keyed_children` emits `MoveChild` only for
// items with stable `id=` (or `dj-id`); without them the diff falls
// back to delete+insert, for which FLIP correctly no-ops (no cached
// First rect to invert against).
//
// Nested [dj-flip] parents are isolated: each installs its own
// MutationObserver with subtree:false so the outer parent does not see
// the inner parent's childList mutations.
//
// SVG / table children are out of v1 scope — `transform:translate()`
// on non-block/flex children has weird behavior. Document, don't
// enforce.

(function () {
    "use strict";

    const _FLIP_ATTR = "dj-flip";
    const _DURATION_ATTR = "dj-flip-duration";
    const _EASING_ATTR = "dj-flip-easing";
    const _DEFAULT_DURATION = 300;
    const _DEFAULT_EASING = "cubic-bezier(.2,.8,.2,1)";

    // Per-parent bookkeeping. We use a Map (not WeakMap) for the parent
    // registry so tests can probe `_observerCount()`; the MutationObserver
    // anchor itself keeps the parent element alive, so the WeakMap
    // idiom doesn't buy us anything here.
    const _installedParents = new Map(); // Element -> { observer, rectCache }
    let _rootObserverInstalled = false;

    // Rect cache — WeakMap so rects don't leak when a child is GC'd.
    // We keep ONE global cache rather than per-parent because children
    // can (theoretically) move between parents, and the WeakMap does not
    // grow unbounded.
    const _rectCache = new WeakMap(); // Element -> DOMRect

    function _reducedMotion() {
        if (typeof matchMedia !== "function") return false;
        try {
            return matchMedia("(prefers-reduced-motion: reduce)").matches;
        } catch (_e) {
            return false;
        }
    }

    function _parseDuration(parent) {
        const raw = parent.getAttribute && parent.getAttribute(_DURATION_ATTR);
        if (raw === null || raw === undefined || raw === "") return _DEFAULT_DURATION;
        // Use Number() rather than parseInt(raw, 10) — parseInt accepts
        // trailing garbage ("300abc" → 300) which is a silent footgun.
        // Number() requires the WHOLE string to be a valid number or it
        // returns NaN, which we then reject via Number.isFinite().
        const n = Number(raw);
        if (!Number.isFinite(n) || n < 0) return _DEFAULT_DURATION;
        // Clamp to a sane upper bound — a 60-second FLIP is almost
        // certainly a typo, and a runaway `transition` ties up the
        // element's inline style indefinitely.
        if (n > 30000) return 30000;
        return n;
    }

    function _parseEasing(parent) {
        const raw = parent.getAttribute && parent.getAttribute(_EASING_ATTR);
        if (!raw) return _DEFAULT_EASING;
        // Light validation: reject anything with a semicolon or quote —
        // those would break out of the transition shorthand. Accepted
        // chars match CSS timing-function syntax (letters, digits,
        // hyphens, dots, parens, commas, whitespace).
        if (/[;"'<>]/.test(raw)) return _DEFAULT_EASING;
        return raw;
    }

    function _snapshotRects(parent) {
        // Read rects for every direct child and cache them. Called once
        // on install and again after each animation cycle so the "First"
        // rects for the NEXT reorder are fresh.
        const children = Array.prototype.slice.call(parent.children);
        for (let i = 0; i < children.length; i++) {
            const c = children[i];
            if (c.nodeType !== 1) continue;
            try {
                _rectCache.set(c, c.getBoundingClientRect());
            } catch (_e) {
                // Detached node or exotic child — skip.
            }
        }
    }

    function _requestAnimationFrame(fn) {
        if (typeof globalThis.requestAnimationFrame === "function") {
            return globalThis.requestAnimationFrame(fn);
        }
        return setTimeout(fn, 16);
    }

    function _animateReorder(parent) {
        // Called when the MutationObserver on `parent` sees a childList
        // mutation. Read the new rects, diff against cache, inverse-
        // translate the movers, then kick off the transition on the
        // next frame.
        if (_reducedMotion()) {
            // Refresh cache so future animations (after the preference
            // flips back) have fresh "First" rects.
            _snapshotRects(parent);
            return;
        }

        const duration = _parseDuration(parent);
        const easing = _parseEasing(parent);
        const children = Array.prototype.slice.call(parent.children);
        const movers = [];

        // Pass 1 — READ. Collect new rects for every child with a cached
        // prior rect. Doing all reads before any writes avoids layout
        // thrashing (read-read-read, then write-write-write).
        for (let i = 0; i < children.length; i++) {
            const c = children[i];
            if (c.nodeType !== 1) continue;
            const prev = _rectCache.get(c);
            if (!prev) continue; // New child — no First rect to invert.
            let next;
            try {
                next = c.getBoundingClientRect();
            } catch (_e) {
                continue;
            }
            const dx = prev.left - next.left;
            const dy = prev.top - next.top;
            if (dx === 0 && dy === 0) continue; // Didn't move.
            movers.push({ el: c, dx: dx, dy: dy });
        }

        // Pass 2 — WRITE the inverse. Pin each mover at its old position
        // with transition:none so the layout change is invisible to the
        // user. We preserve any pre-existing transition style so we can
        // restore it at cleanup time.
        for (let i = 0; i < movers.length; i++) {
            const m = movers[i];
            m.prevTransition = m.el.style.transition;
            m.prevTransform = m.el.style.transform;
            m.el.style.transition = "none";
            m.el.style.transform = "translate(" + m.dx + "px, " + m.dy + "px)";
        }

        // Pass 3 — PLAY on the next frame. Browser commits the inverse
        // transform, then we clear it back to identity under a
        // transition. Setting `transform = ''` lets the browser animate
        // from the inverted position to identity, which visually
        // matches the DOM's actual new position.
        _requestAnimationFrame(function () {
            for (let i = 0; i < movers.length; i++) {
                const m = movers[i];
                m.el.style.transition = "transform " + duration + "ms " + easing;
                m.el.style.transform = "";
            }
        });

        // Cleanup — after the transition should have finished, strip
        // our inline styles so the element is clean for subsequent
        // patches. Use duration + 50 ms margin; jsdom never fires
        // transitionend so we cannot rely on that event.
        const cleanupDelay = duration + 50;
        setTimeout(function () {
            for (let i = 0; i < movers.length; i++) {
                const m = movers[i];
                // Only clear if our values are still there — a subsequent
                // reorder might already have overwritten them. We restore
                // BOTH transition and transform symmetrically: if the
                // author had `transform: rotate(5deg)` inline before
                // mount, we must put it back — otherwise FLIP would
                // permanently stomp author-specified transforms after
                // the first reorder.
                if (m.el.style.transform === "") {
                    m.el.style.transition = m.prevTransition || "";
                    m.el.style.transform = m.prevTransform || "";
                }
            }
            // Refresh cache for the next reorder — but ONLY if no child
            // is still mid-animation from a later reorder. If a second
            // reorder fired while this one was playing, reading rects now
            // would capture intermediate positions and corrupt the cache.
            // The LATER reorder's own cleanup timer will snapshot when
            // it's safe.
            const inFlight = Array.prototype.some.call(
                parent.children,
                function (el) {
                    return el.style && el.style.transition &&
                        el.style.transition.indexOf("transform") !== -1;
                }
            );
            if (!inFlight) _snapshotRects(parent);
        }, cleanupDelay);
    }

    function _install(parent) {
        if (!parent || parent.nodeType !== 1) return;
        if (_installedParents.has(parent)) return;
        _snapshotRects(parent);
        if (typeof MutationObserver === "undefined") {
            _installedParents.set(parent, { observer: null });
            return;
        }
        const observer = new MutationObserver(function (mutations) {
            let sawChild = false;
            for (let i = 0; i < mutations.length; i++) {
                if (mutations[i].type === "childList") {
                    sawChild = true;
                    break;
                }
            }
            if (!sawChild) return;
            _animateReorder(parent);
        });
        observer.observe(parent, { childList: true, subtree: false });
        _installedParents.set(parent, { observer: observer });
    }

    function _uninstall(parent) {
        if (!parent || parent.nodeType !== 1) return;
        const entry = _installedParents.get(parent);
        if (!entry) return;
        if (entry.observer) {
            entry.observer.disconnect();
        }
        _installedParents.delete(parent);
    }

    function _rescan() {
        if (typeof document === "undefined") return;
        document.querySelectorAll("[" + _FLIP_ATTR + "]").forEach(_install);
    }

    function _installRootObserver() {
        if (_rootObserverInstalled) return;
        _rootObserverInstalled = true;
        _rescan();
        if (typeof MutationObserver === "undefined") return;
        const rootObserver = new MutationObserver(function (mutations) {
            for (let i = 0; i < mutations.length; i++) {
                const m = mutations[i];
                if (m.type === "attributes" && m.attributeName === _FLIP_ATTR) {
                    if (m.target.hasAttribute(_FLIP_ATTR)) {
                        _install(m.target);
                    } else {
                        _uninstall(m.target);
                    }
                } else if (m.type === "childList") {
                    m.addedNodes.forEach(function (node) {
                        if (node.nodeType !== 1) return;
                        if (node.hasAttribute && node.hasAttribute(_FLIP_ATTR)) {
                            _install(node);
                        }
                        if (node.querySelectorAll) {
                            node.querySelectorAll("[" + _FLIP_ATTR + "]").forEach(_install);
                        }
                    });
                    m.removedNodes.forEach(function (node) {
                        if (node.nodeType !== 1) return;
                        if (node.hasAttribute && node.hasAttribute(_FLIP_ATTR)) {
                            _uninstall(node);
                        }
                        if (node.querySelectorAll) {
                            node.querySelectorAll("[" + _FLIP_ATTR + "]").forEach(_uninstall);
                        }
                    });
                }
            }
        });
        rootObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: [_FLIP_ATTR],
            subtree: true,
            childList: true,
        });
    }

    if (typeof document !== "undefined") {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", _installRootObserver);
        } else {
            _installRootObserver();
        }
    }

    globalThis.djust = globalThis.djust || {};
    globalThis.djust.flip = {
        _install: _install,
        _uninstall: _uninstall,
        _rescan: _rescan,
        _animateReorder: _animateReorder,
        _snapshotRects: _snapshotRects,
        _parseDuration: _parseDuration,
        _parseEasing: _parseEasing,
        _observerCount: function () { return _installedParents.size; },
        _installedParents: _installedParents,
        _rectCache: _rectCache,
    };
})();

// 45-child-view.js — Embedded child LiveView dispatch + Sticky LiveView
// preservation across live_redirect.
//
// Phase A (v0.5.7) shipped the child_update wire-frame receiver (WIRE-
// ONLY: validate + dispatch lifecycle event, DOM untouched). Phase B
// (v0.6.0) extends this module with:
//
//   * A scoped VDOM applier — reuses the main ``applyPatches`` via the
//     new ``applyPatches(patches, rootEl)`` variant so child / sticky
//     patches are scoped to their subtree and don't collide with the
//     parent's VDOM coordinates. Closes the Phase A "DOM untouched"
//     TODO: ``handleChildUpdate`` now really updates the child.
//   * Per-view VDOM version tracking — ``clientVdomVersions:
//     Map<view_id, number>`` keyed by ``__root`` for the top-level view
//     and ``view_id`` for each child / sticky.
//   * Sticky stash + reconcile + reattach flow — detach sticky subtrees
//     BEFORE live_redirect sends, reconcile against the server's
//     authoritative ``sticky_hold`` list on mount, then re-attach each
//     stashed subtree at the matching ``[dj-sticky-slot="<id>"]`` via
//     ``replaceWith()`` (DOM identity preserved — form values, scroll,
//     focus, and third-party widget references all survive).
//
// Each ``{% live_render "...Path" sticky=True %}`` on the server emits:
//     <div dj-view dj-sticky-view="<id>" dj-sticky-root
//          data-djust-embedded="<id>">…</div>
// Apps listen to ``djust:sticky-preserved`` / ``djust:sticky-unmounted``
// CustomEvents to react to lifecycle transitions.

(function () {
    "use strict";

    const djust = globalThis.djust = globalThis.djust || {};

    // Per-view VDOM version tracking (Phase B). Keyed by view_id, with
    // the sentinel "__root" reserved for the top-level view.
    //
    // The top-level ``clientVdomVersion`` module-local (04-cache.js)
    // remains authoritative for the root view for now — we mirror its
    // value into this map so any consumer that wants per-view versions
    // can use a single lookup.
    const clientVdomVersions = new Map();
    djust.clientVdomVersions = clientVdomVersions;

    // ---------------------------------------------------------------------
    // Sticky stash (Phase B).
    // ---------------------------------------------------------------------
    //
    // Map<sticky_id, HTMLElement> holding subtrees detached from the DOM
    // before a live_redirect is sent. Key is the ``dj-sticky-view``
    // attribute value; equal to the view_id the server uses for
    // ``sticky_update`` patches.
    const stickyStash = new Map();

    // Fallback for environments where CSS.escape is missing (jsdom < 22
    // ships a partial CSSOM). Escapes anything other than [A-Za-z0-9_-].
    function _escapeSelector(value) {
        if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
            return CSS.escape(value);
        }
        return String(value).replace(/([^A-Za-z0-9_-])/g, "\\$1");
    }

    /**
     * Look up the DOM root of an embedded child view by its view_id.
     * Returns null if no matching [dj-view] element is in the document.
     */
    function _getChildRoot(viewId) {
        if (!viewId) return null;
        const selector =
            '[dj-view][data-djust-embedded="' + _escapeSelector(viewId) + '"]';
        return document.querySelector(selector);
    }

    /**
     * Look up the DOM root of a sticky view by its sticky_id.
     * Returns null if no matching [dj-sticky-view] element is in the document.
     */
    function _getStickyRoot(stickyId) {
        if (!stickyId) return null;
        const selector = '[dj-sticky-view="' + _escapeSelector(stickyId) + '"]';
        return document.querySelector(selector);
    }

    /**
     * Dispatch a CustomEvent on a target element, swallowing errors in
     * ancient runtimes that lack CustomEvent.
     */
    function _dispatch(target, name, detail) {
        try {
            target.dispatchEvent(new CustomEvent(name, {
                bubbles: true,
                detail: detail || {},
            }));
        } catch (_err) {
            // CustomEvent constructor missing — ignore.
        }
    }

    /**
     * Apply a list of VDOM patches scoped to ``rootEl`` by calling the
     * top-level ``applyPatches`` function with the scoping root. When
     * the top-level function is missing (tests or malformed bundles)
     * this is a no-op so the receiver still dispatches its lifecycle
     * event without tripping an exception.
     */
    function _applyScopedPatches(patches, rootEl) {
        if (typeof applyPatches !== "function") {
            if (globalThis.djustDebug) {
                console.warn("[djust] applyPatches is not in scope; skipping");
            }
            return false;
        }
        return applyPatches(patches, rootEl);
    }

    /**
     * Handle an inbound ``child_update`` frame.
     *
     * Phase B: applies the frame's patches scoped to the child subtree
     * (``[dj-view][data-djust-embedded="<view_id>"]``). Updates the
     * per-view VDOM version in ``clientVdomVersions``. Dispatches
     * ``djust:child-updated`` before the patch pass so observers can
     * see pre-apply state; this matches the Phase A event shape with
     * ``phase: 'B'``.
     */
    function handleChildUpdate(message) {
        if (!message) return;
        const viewId = message.view_id;
        if (!viewId) {
            if (globalThis.djustDebug) {
                console.warn("[djust] child_update frame missing view_id", message);
            }
            return;
        }
        const root = _getChildRoot(viewId);
        if (!root) {
            if (globalThis.djustDebug) {
                console.warn("[djust] child_update for unknown view_id=" + viewId);
            }
            return;
        }

        // Dispatch pre-apply so observers see the pre-apply state.
        _dispatch(root, "djust:child-updated", {
            view_id: viewId,
            version: message.version,
            patches: message.patches || [],
            phase: "B",
        });

        // Apply scoped patches. Child patches carry their own dj-id
        // namespace; without rootEl the doc-wide lookup in
        // getNodeByPath would cross subtree boundaries.
        if (Array.isArray(message.patches) && message.patches.length > 0) {
            _applyScopedPatches(message.patches, root);
        }
        clientVdomVersions.set(viewId, message.version);
    }

    /**
     * Handle an inbound ``sticky_update`` frame. Same shape as
     * ``child_update`` but targets a sticky subtree via the
     * ``[dj-sticky-view]`` selector.
     */
    function handleStickyUpdate(message) {
        if (!message) return;
        const viewId = message.view_id;
        if (!viewId) return;
        const root = _getStickyRoot(viewId);
        if (!root) {
            if (globalThis.djustDebug) {
                console.warn("[djust] sticky_update for unknown view_id=" + viewId);
            }
            return;
        }
        _dispatch(root, "djust:sticky-updated", {
            view_id: viewId,
            version: message.version,
            patches: message.patches || [],
        });
        if (Array.isArray(message.patches) && message.patches.length > 0) {
            _applyScopedPatches(message.patches, root);
        }
        clientVdomVersions.set(viewId, message.version);
    }

    /**
     * Walk all ``[dj-sticky-view]`` elements and detach each into the
     * stash, keyed by its ``dj-sticky-view`` attribute value.
     *
     * Idempotent — if an id is already stashed (rapid re-entrant
     * navigation), the second call is a no-op for that id. Skips
     * ``document.contains`` checks so re-entrant calls during a
     * DOM replacement cycle stay safe.
     */
    function stashStickySubtrees() {
        const elements = document.querySelectorAll("[dj-sticky-view]");
        elements.forEach(function (el) {
            const stickyId = el.getAttribute("dj-sticky-view");
            if (!stickyId) return;
            if (stickyStash.has(stickyId)) return;  // idempotent
            // Detach from DOM — parentNode.removeChild preserves the
            // subtree including form values, focus, and third-party
            // widget references (e.g. <video> players).
            if (el.parentNode) {
                el.parentNode.removeChild(el);
            }
            stickyStash.set(stickyId, el);
        });
    }

    /**
     * Reconcile the stash against the server's authoritative list of
     * surviving sticky ids. Entries in the stash but NOT in ``views``
     * are dropped + a ``djust:sticky-unmounted`` event is dispatched
     * with reason ``server-unmount``.
     */
    function reconcileStickyHold(views) {
        const authoritative = new Set(Array.isArray(views) ? views : []);
        stickyStash.forEach(function (subtree, stickyId) {
            if (!authoritative.has(stickyId)) {
                stickyStash.delete(stickyId);
                _dispatch(subtree, "djust:sticky-unmounted", {
                    sticky_id: stickyId,
                    reason: "server-unmount",
                });
            }
        });
    }

    /**
     * After a mount-frame HTML replacement has been applied, walk
     * ``[dj-sticky-slot]`` elements in the new DOM. For each match,
     * look up the corresponding stash entry; if present, replace the
     * slot with the stashed subtree via ``replaceWith()`` (preserving
     * DOM identity). If absent, dispatch ``djust:sticky-unmounted``
     * with reason ``no-slot`` and drop the entry.
     */
    function reattachStickyAfterMount() {
        if (stickyStash.size === 0) return;
        const slots = document.querySelectorAll("[dj-sticky-slot]");
        const reattached = new Set();
        slots.forEach(function (slotEl) {
            const stickyId = slotEl.getAttribute("dj-sticky-slot");
            if (!stickyId) return;
            const subtree = stickyStash.get(stickyId);
            if (!subtree) return;
            // Use replaceWith so DOM identity of the sticky subtree is
            // preserved — form values, focus, scroll, and any
            // third-party widget references hanging off nodes inside
            // the subtree all survive.
            if (slotEl.replaceWith) {
                slotEl.replaceWith(subtree);
            } else if (slotEl.parentNode) {
                slotEl.parentNode.replaceChild(subtree, slotEl);
            }
            stickyStash.delete(stickyId);
            reattached.add(stickyId);
            _dispatch(subtree, "djust:sticky-preserved", {
                sticky_id: stickyId,
            });
        });

        // Any leftover stash entry that didn't find a matching slot is
        // unmounted with reason no-slot.
        stickyStash.forEach(function (subtree, stickyId) {
            stickyStash.delete(stickyId);
            _dispatch(subtree, "djust:sticky-unmounted", {
                sticky_id: stickyId,
                reason: "no-slot",
            });
        });
    }

    /**
     * Emit ``djust:child-mounted`` CustomEvent for every ``[dj-view]``
     * that carries a ``data-djust-embedded`` in the initial DOM on
     * DOMContentLoaded. Apps can listen to react to child view lifecycle
     * (e.g. third-party embeds, analytics).
     */
    function emitChildMountedEvents() {
        const children = document.querySelectorAll(
            "[dj-view][data-djust-embedded]"
        );
        children.forEach(function (el) {
            const viewId = el.getAttribute("data-djust-embedded");
            _dispatch(el, "djust:child-mounted", { view_id: viewId });
        });
    }

    djust.childView = {
        handleChildUpdate: handleChildUpdate,
        _getChildRoot: _getChildRoot,
    };

    /**
     * Drop every entry from the sticky stash. Called from 03-websocket.js
     * when the WebSocket closes abnormally (server crash, network
     * partition) — the server will re-mount any sticky views from
     * scratch on reconnect, so keeping detached subtrees from a
     * dead session would only cause ``no-slot`` unmount events to
     * fire on the next navigation.
     */
    function clearStash() {
        if (stickyStash.size > 0 && globalThis.djustDebug) {
            console.log("[sticky] clearing stash of " + stickyStash.size + " subtree(s) on abnormal close");
        }
        stickyStash.clear();
    }

    djust.stickyPreserve = {
        handleStickyUpdate: handleStickyUpdate,
        stashStickySubtrees: stashStickySubtrees,
        reconcileStickyHold: reconcileStickyHold,
        reattachStickyAfterMount: reattachStickyAfterMount,
        clearStash: clearStash,
        _getStickyRoot: _getStickyRoot,
        _stash: stickyStash,
    };

    // Also expose the top-level applyPatches under a stable name so
    // other modules (and tests) can invoke the scoped variant without
    // reaching into 12-vdom-patch.js's internals.
    if (typeof applyPatches === "function" && !djust._applyPatches) {
        djust._applyPatches = applyPatches;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", emitChildMountedEvents);
    } else {
        emitChildMountedEvents();
    }
})();

// ============================================================================
// State Snapshot — client half (v0.6.0)
// ============================================================================
//
// Captures the current view's public state on before-navigate and posts it to
// the Service Worker's state cache. On popstate (back-button), looks up a
// cached state and stashes it on window.djust._pendingStateSnapshot so the
// next outbound live_redirect_mount can include it.
//
// Per-view opt-in: the server-side LiveView must declare
// `enable_state_snapshot = True`. The client sends the snapshot regardless
// (belt-and-braces); the server ignores snapshots for non-opt-in views.
//
// Non-invasive: this module only wires listeners and reads/writes one
// globalThis slot. It never mutates the DOM or WebSocket directly.

(function () {
    globalThis.djust = globalThis.djust || {};

    function _swBridge() {
        return globalThis.djust && globalThis.djust._sw;
    }

    function _currentViewSlug(fromUrl) {
        // The route map has pathname -> view-slug entries. When a caller
        // passes ``fromUrl`` (the source URL captured at dispatch time —
        // see Fix #9), prefer that over ``location.pathname`` since
        // pushState() in 18-navigation.js runs BEFORE the
        // ``djust:before-navigate`` dispatch, leaving
        // ``location.pathname`` already pointing at the DESTINATION.
        var pathname = fromUrl
            || ((typeof window !== 'undefined' && window.location)
                ? window.location.pathname
                : '/');
        var routeMap = (globalThis.djust && globalThis.djust._routeMap) || {};
        if (routeMap[pathname]) return routeMap[pathname];
        if (typeof document !== 'undefined') {
            var container = document.querySelector('[dj-view]');
            if (container) return container.getAttribute('dj-view') || '';
        }
        return '';
    }

    function _serializeCurrentState(slug) {
        // The server-side view state is mirrored client-side through the
        // state bus (`05-state-bus.js`) for hydration hints — but the
        // canonical record lives on `window.djust._clientState`, a
        // per-view-slug snapshot dict populated by the mount handler
        // when the server includes ``public_state`` in the mount frame
        // (emitted only when ``enable_state_snapshot = True`` on the
        // view class — see Fix #1).
        if (!slug) return null;
        var bag = (globalThis.djust && globalThis.djust._clientState) || {};
        var state = bag[slug];
        if (!state) return null;
        try {
            return JSON.stringify(state);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn('[state-snapshot] JSON.stringify failed', e);
            }
            return null;
        }
    }

    function _captureBeforeNavigate(event) {
        var bridge = _swBridge();
        if (!bridge || typeof bridge.captureState !== 'function') return;
        // Fix #9: prefer the explicit ``fromUrl`` in the CustomEvent
        // detail so we capture under the SOURCE URL, not the post-
        // pushState destination.
        var fromUrl = (event && event.detail && event.detail.fromUrl)
            || ((typeof window !== 'undefined' && window.location)
                ? window.location.pathname
                : '/');
        var slug = _currentViewSlug(fromUrl);
        if (!slug) return;
        var json = _serializeCurrentState(slug);
        if (!json) return;
        try {
            bridge.captureState(fromUrl, slug, json);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn('[state-snapshot] captureState threw', e);
            }
        }
    }

    // Fix #2: kept for backwards-compat — prewarm the pending slot via
    // an async lookup. Does NOT gate popstate send (the popstate handler
    // in 18-navigation.js now awaits ``lookupStateForUrl`` directly).
    function _popstateLookup(_popstateEvent) {
        var bridge = _swBridge();
        if (!bridge || typeof bridge.lookupState !== 'function') return;
        var url = (typeof window !== 'undefined' && window.location)
            ? window.location.pathname
            : '/';
        bridge.lookupState(url).then(function (reply) {
            if (!reply || !reply.hit) {
                globalThis.djust._pendingStateSnapshot = null;
                return;
            }
            globalThis.djust._pendingStateSnapshot = {
                view_slug: reply.view_slug,
                state_json: reply.state_json,
                ts: reply.ts,
            };
            if (globalThis.djustDebug) {
                console.log('[state-snapshot] restored pending snapshot for', url);
            }
        }).catch(function () {
            globalThis.djust._pendingStateSnapshot = null;
        });
    }

    // Public awaitable used by 18-navigation.js popstate handler (Fix
    // #2). Resolves to ``{view_slug, state_json, ts}`` on hit, or
    // ``null`` on miss / unavailable bridge. Does NOT mutate
    // ``_pendingStateSnapshot`` — the caller owns that slot.
    function lookupStateForUrl(url) {
        var bridge = _swBridge();
        if (!bridge || typeof bridge.lookupState !== 'function') {
            return Promise.resolve(null);
        }
        return bridge.lookupState(url).then(function (reply) {
            if (!reply || !reply.hit) return null;
            return {
                view_slug: reply.view_slug,
                state_json: reply.state_json,
                ts: reply.ts,
            };
        }).catch(function () { return null; });
    }

    if (typeof window !== 'undefined') {
        // before-navigate — fired by 18-navigation.js on live_redirect /
        // dj-navigate / popstate-cross-view. Capture BEFORE the WS frame
        // goes out so the cache entry for the CURRENT URL is fresh.
        window.addEventListener('djust:before-navigate', _captureBeforeNavigate);
        // popstate — lookup the cached snapshot for the destination URL
        // so 18-navigation.js can attach it to the outbound
        // live_redirect_mount frame.
        window.addEventListener('popstate', _popstateLookup);
    }

    globalThis.djust._stateSnapshot = {
        _capture: _captureBeforeNavigate,
        _lookupOnPopstate: _popstateLookup,
        _serialize: _serializeCurrentState,
        lookupStateForUrl: lookupStateForUrl,
    };
})();
/**
 * Hot View Replacement (HVR) — v0.6.1
 *
 * Client-side dev indicator for state-preserving Python reloads. The server
 * broadcasts a ``{type:'hvr-applied'}`` frame after successfully swapping
 * ``__class__`` on the live view instance; the websocket module (03) routes
 * it here to:
 *   1. Dispatch a ``djust:hvr-applied`` CustomEvent on ``document`` so
 *      application code / tests can observe HVR bursts.
 *   2. In DEBUG mode (``globalThis.djustDebug === true``) paint a small
 *      corner toast confirming the reload. Toast auto-dismisses after 3s.
 *
 * Module is development-only in intent — nothing here runs unless the
 * server actually sends an hvr-applied frame, which only happens when
 * ``DEBUG=True`` + ``LIVEVIEW_CONFIG["hvr_enabled"]``.
 */
(function () {
    "use strict";
    const djust = (globalThis.djust = globalThis.djust || {});

    /**
     * Render a short-lived toast confirming HVR applied successfully.
     * No-op outside debug mode (``globalThis.djustDebug`` falsy).
     *
     * @param {object} data - The ``hvr-applied`` message body. Expected
     *     fields: ``view`` (string), ``version`` (number), ``file`` (string).
     */
    function showIndicator(data) {
        if (!globalThis.djustDebug) return;
        try {
            if (!document || !document.body) return;
            const el = document.createElement("div");
            el.className = "djust-hvr-toast";
            el.setAttribute("role", "status");
            el.setAttribute("aria-live", "polite");
            const view = (data && data.view) || "(unknown)";
            const version = (data && data.version) || 0;
            el.textContent = "HVR applied: " + view + " (v" + version + ")";
            el.style.cssText =
                "position:fixed;bottom:12px;right:12px;" +
                "background:#22c55e;color:#fff;padding:8px 12px;" +
                "border-radius:4px;font:14px system-ui,sans-serif;" +
                "box-shadow:0 2px 8px rgba(0,0,0,.15);z-index:999999;" +
                "pointer-events:none;";
            document.body.appendChild(el);
            setTimeout(function () {
                try {
                    el.remove();
                } catch (err) {
                    if (globalThis.djustDebug) {
                        console.warn("[djust.hvr] toast cleanup failed", err);
                    }
                }
            }, 3000);
        } catch (e) {
            if (globalThis.djustDebug) {
                console.warn("[djust.hvr] showIndicator failed", e);
            }
        }
    }

    djust.hvr = { showIndicator: showIndicator };
})();
// ============================================================================
// Server Functions — djust.call(viewSlug, funcName, params)
// ============================================================================
// Same-origin RPC from the browser to an @server_function method on a
// LiveView. No re-render, no assigns diff — pure request/response. Rejects
// with an Error carrying {code, status, details} on non-2xx responses.
//
// CSRF: reads the hidden input (preferred) then falls back to the cookie.
// Mirrors the resolver in src/11-event-handler.js for consistency.

(function () {
    function _csrf() {
        try {
            const input = document.querySelector('[name=csrfmiddlewaretoken]');
            if (input && input.value) return input.value;
        } catch (_) { /* SSR / detached DOM */ }
        const m = (document.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    async function call(viewSlug, funcName, params) {
        if (!viewSlug || !funcName) {
            throw new Error('djust.call requires (viewSlug, funcName)');
        }
        // #987: resolve the URL prefix through djust.apiUrl() so
        // FORCE_SCRIPT_NAME / api_patterns(prefix=...) sub-path deploys
        // work. djust.apiPrefix is set at bootstrap from the
        // {% djust_client_config %} meta tag (see 00-namespace.js).
        const url = window.djust.apiUrl(
            'call/' + encodeURIComponent(viewSlug) + '/' + encodeURIComponent(funcName) + '/'
        );
        const resp = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': _csrf(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({ params: params || {} }),
        });
        let data = {};
        try { data = await resp.json(); } catch (_) { /* empty / non-json */ }
        if (!resp.ok) {
            const err = new Error(data.message || ('djust.call failed: ' + resp.status));
            err.code = data.error || 'http_error';
            err.status = resp.status;
            err.details = data.details;
            throw err;
        }
        return data.result;
    }

    window.djust = window.djust || {};
    window.djust.call = call;
})();
// ============================================================================
// {% dj_activity %} — activity visibility tracker (v0.7.0)
// ============================================================================
// React 19.2 <Activity> parity. The server is the canonical source of
// visibility; this module observes the DOM for ``[data-djust-activity]``
// wrappers, tracks their current ``hidden`` state, and dispatches a
// bubbling ``djust:activity-shown`` CustomEvent when an activity flips
// from hidden → visible. Consumers (user code, the event-dispatch gate in
// 11-event-handler.js, and the VDOM-patch gate in 12-vdom-patch.js) read
// this state via ``window.djust.activityVisible(name)``.
//
// No autocomplete-style polling loop: one MutationObserver filtered to
// the ``hidden`` attribute on activity roots keeps overhead low. A
// re-scan runs on ``djust:morph-complete`` so activities added by VDOM
// patches get tracked.

(function () {
    // name → { node, visible, eager } — authoritative in-memory mirror of
    // every activity wrapper currently in the document. Keyed by the
    // ``data-djust-activity`` attribute; when the same name appears more
    // than once (a case the A071 system check flags at build time), the
    // LAST-scanned node wins so the map size matches DOM reality.
    const _activities = new Map();

    function _isHidden(node) {
        // Treat explicit ``hidden`` attribute OR computed display:none as
        // hidden. The attribute is the primary signal (that's what the
        // template tag emits); the computed check is defensive for apps
        // that override with CSS.
        if (!node || node.nodeType !== 1) return false;
        if (node.hasAttribute('hidden')) return true;
        try {
            const cs = node.ownerDocument.defaultView.getComputedStyle(node);
            if (cs && cs.display === 'none') return true;
        } catch (_) {
            // getComputedStyle can throw in exotic environments — fall
            // through to the attribute check result.
        }
        return false;
    }

    function _scan(root) {
        const scope = root || document;
        const nodes = scope.querySelectorAll
            ? scope.querySelectorAll('[data-djust-activity]')
            : [];
        // Build a fresh view from what's actually in the DOM so stale
        // entries don't linger after a VDOM patch removes a wrapper.
        const fresh = new Map();
        for (const node of nodes) {
            const name = node.getAttribute('data-djust-activity') || '';
            if (!name) continue;
            fresh.set(name, {
                node: node,
                visible: !_isHidden(node),
                eager: node.getAttribute('data-djust-eager') === 'true',
            });
        }
        // Replace contents of the live map so external references still
        // resolve the latest state.
        _activities.clear();
        for (const [k, v] of fresh) _activities.set(k, v);
        return _activities;
    }

    function activityVisible(name) {
        // Accept a falsy ``name`` as "no activity context" — behave
        // identically to an unknown name (defaults to visible so callers
        // don't suppress legitimate events against a typo).
        if (!name) return true;
        const entry = _activities.get(name);
        if (!entry) return true;
        return entry.visible !== false;
    }

    function _dispatchShown(name, node) {
        try {
            const ev = new CustomEvent('djust:activity-shown', {
                bubbles: true,
                detail: { name: name, node: node },
            });
            node.dispatchEvent(ev);
            if (globalThis.djustDebug) {
                console.log('[djust:activity] shown:', name);
            }
        } catch (_) {
            // CustomEvent may not be available in some legacy envs; safe
            // to swallow — the state map stays in sync regardless.
        }
    }

    // One observer watches the whole document for ``hidden`` attribute
    // flips on nodes carrying ``data-djust-activity``. attributeOldValue
    // lets us distinguish a hidden→visible flip (dispatch) from a
    // visible→hidden flip (state update only, no event).
    let _observer = null;

    function _installObserver() {
        if (_observer || typeof MutationObserver === 'undefined') return;
        _observer = new MutationObserver(function (mutations) {
            for (const m of mutations) {
                if (m.type !== 'attributes' || m.attributeName !== 'hidden') continue;
                const node = m.target;
                if (!node || !node.getAttribute) continue;
                const name = node.getAttribute('data-djust-activity');
                if (!name) continue;
                const wasHidden = m.oldValue !== null; // oldValue === null iff attr was absent
                const isHidden = node.hasAttribute('hidden');
                const entry = _activities.get(name) || {
                    node: node,
                    visible: true,
                    eager: node.getAttribute('data-djust-eager') === 'true',
                };
                entry.node = node;
                entry.visible = !isHidden;
                _activities.set(name, entry);
                if (wasHidden && !isHidden) {
                    _dispatchShown(name, node);
                }
            }
        });
        _observer.observe(document.body || document.documentElement, {
            attributes: true,
            attributeOldValue: true,
            attributeFilter: ['hidden'],
            subtree: true,
        });
    }

    // Initial scan + observer install. On document-ready (or immediately
    // if we're already past DOMContentLoaded).
    function _boot() {
        _scan(document);
        _installObserver();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _boot, { once: true });
    } else {
        _boot();
    }

    // Re-scan when VDOM patches add / remove activity wrappers. The morph
    // pipeline emits ``djust:morph-complete`` after every successful
    // patch batch; a lightweight re-scan catches newly-introduced
    // wrappers and drops entries for removed ones.
    window.addEventListener('djust:morph-complete', function () {
        _scan(document);
    });

    // Expose to the namespace. The top-level ``activityVisible`` helper
    // is what user code, the event-handler gate, and the VDOM-patch gate
    // all call; ``_activity`` carries the implementation-detail handles
    // for tests.
    window.djust = window.djust || {};
    window.djust._activity = {
        _activities: _activities,
        _scan: _scan,
        _isHidden: _isHidden,
        activityVisible: activityVisible,
    };
    window.djust.activityVisible = activityVisible;
})();
