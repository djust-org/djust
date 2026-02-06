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
                    preserveFormValues(liveviewRoot, () => {
                        liveviewRoot.innerHTML = newRoot.innerHTML;
                    });
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

        // Clear optimistic state BEFORE applying changes
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
            _isBroadcastUpdate = !!data.broadcast;
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

            _isBroadcastUpdate = false;
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
            if (globalThis.djustDebug) console.log('[LiveView] Heartbeat stopped');
        }

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

        console.log('[LiveView] Connecting to WebSocket:', url);
        this.ws = new WebSocket(url);

        this.ws.onopen = (_event) => {
            console.log('[LiveView] WebSocket connected');
            this.reconnectAttempts = 0;

            // Track reconnections (Phase 2.1: WebSocket Inspector)
            if (this.stats.connectedAt !== null) {
                this.stats.reconnections++;
                // Notify hooks of reconnection
                if (typeof notifyHooksReconnected === 'function') notifyHooksReconnected();
            }
            this.stats.connectedAt = Date.now();
        };

        this.ws.onclose = (_event) => {
            console.log('[LiveView] WebSocket disconnected');
            this.viewMounted = false;

            // Notify hooks of disconnection
            if (typeof notifyHooksDisconnected === 'function') notifyHooksDisconnected();

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
                this._showConnectionErrorOverlay();
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
                if (globalThis.djustDebug) console.log('[Upload] Registered:', data.ref, 'for', data.upload_name);
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
                // Dispatch to dj-hook instances that registered via handleEvent
                if (typeof dispatchPushEventToHooks === 'function') {
                    dispatchPushEventToHooks(data.event, data.payload);
                }
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

    /**
     * Handle scoped HTML update for an embedded child LiveView.
     * Replaces only the innerHTML of the embedded view's container div.
     */
    handleEmbeddedUpdate(data) {
        const viewId = data.view_id;
        const html = data.html;
        if (!viewId || html === undefined) {
            console.warn('[LiveView] Invalid embedded_update message:', data);
            return;
        }

        const container = document.querySelector(`[data-djust-embedded="${CSS.escape(viewId)}"]`);
        if (!container) {
            console.warn(`[LiveView] Embedded view container not found: ${viewId}`);
            return;
        }

        container.innerHTML = html;
        if (globalThis.djustDebug) console.log(`[LiveView] Updated embedded view: ${viewId}`);

        // Re-bind events within the updated container
        bindLiveViewEvents();
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

    // Bind navigation directives (dj-patch, dj-navigate)
    if (window.djust.navigation) {
        window.djust.navigation.bindDirectives();
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

                // Embedded LiveView: route event to correct child view
                const embeddedViewId = getEmbeddedViewId(e.currentTarget);
                if (embeddedViewId) {
                    params.view_id = embeddedViewId;
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

                // Embedded LiveView: route event to correct child view
                const embeddedViewId = getEmbeddedViewId(e.target);
                if (embeddedViewId) {
                    params.view_id = embeddedViewId;
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
            // Embedded LiveView: route event to correct child view
            const embeddedViewId = getEmbeddedViewId(element);
            if (embeddedViewId) {
                params.view_id = embeddedViewId;
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

                    // Embedded LiveView: route event to correct child view
                    const embeddedViewId = getEmbeddedViewId(e.target);
                    if (embeddedViewId) {
                        params.view_id = embeddedViewId;
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

// Export for testing
window.djust.bindLiveViewEvents = bindLiveViewEvents;

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
        // Use targeted attribute selectors for better performance on large pages
        const selectors = [
            '[dj-loading\\.disable]',
            '[dj-loading\\.show]',
            '[dj-loading\\.hide]',
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

    // Start loading state
    const triggerElement = params._targetElement;

    // Check client-side cache first
    const config = cacheConfig.get(eventName);
    const keyParams = config?.key_params || null;
    const cacheKey = buildCacheKey(eventName, params, keyParams);
    const cached = getCachedResult(cacheKey);

    if (cached) {
        // Cache hit! Apply cached patches without server round-trip
        if (globalThis.djustDebug) {
            console.log(`[LiveView:cache] Cache hit: ${cacheKey}`);
        }

        // Still show brief loading state for UX consistency
        globalLoadingManager.startLoading(eventName, triggerElement);

        // Apply cached patches
        if (cached.patches && cached.patches.length > 0) {
            applyPatches(cached.patches);
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
    let paramsWithCache = params;

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
        paramsWithCache = { ...params, _cacheRequestId: cacheRequestId };
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
                'X-Djust-Event': eventName
            },
            body: JSON.stringify(paramsWithCache)
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
function getNodeByPath(path, djustId = null) {
    // Strategy 1: ID-based resolution (fast, reliable)
    if (djustId) {
        const byId = document.querySelector(`[data-dj-id="${CSS.escape(djustId)}"]`);
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

function applyDjUpdateElements(existingRoot, newRoot) {
    // Find all elements with dj-update attribute in the new content
    const djUpdateElements = newRoot.querySelectorAll('[dj-update]');

    if (djUpdateElements.length === 0) {
        // No dj-update elements, do a full replacement
        preserveFormValues(existingRoot, () => {
            existingRoot.innerHTML = newRoot.innerHTML;
        });
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
                preserveFormValues(existingElement, () => {
                    existingElement.innerHTML = newElement.innerHTML;
                });
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
                    preserveFormValues(existing, () => {
                        existing.innerHTML = newChild.innerHTML;
                    });
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
            // Preserve non-breaking spaces (\u00A0) as significant, matching Rust VDOM parser.
            // Only filter out ASCII whitespace-only text nodes.
            return (/[^ \t\n\r\f]/.test(child.textContent));
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
window.djust._getNodeByPath = getNodeByPath;
window.djust.createNodeFromVNode = createNodeFromVNode;
window.djust.preserveFormValues = preserveFormValues;

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
        console.warn(`[LiveView] Failed to find node: path=${safePath}, id=${sanitizeIdForLog(patch.d)}`);
        return false;
    }

    try {
        switch (patch.type) {
            case 'Replace':
                const newNode = createNodeFromVNode(patch.node, isInSvgContext(node.parentNode));
                node.parentNode.replaceChild(newNode, node);
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
function applyPatches(patches) {
    if (!patches || patches.length === 0) {
        return true;
    }

    // Sort patches in 4-phase order for correct DOM mutation sequencing
    _sortPatches(patches);

    // For small patch sets, apply directly without batching overhead
    if (patches.length <= 10) {
        let failedCount = 0;
        for (const patch of patches) {
            if (!applySinglePatch(patch)) {
                failedCount++;
            }
        }
        if (failedCount > 0) {
            console.error(`[LiveView] ${failedCount}/${patches.length} patches failed`);
            return false;
        }
        // Update hooks and model bindings after DOM patches
        if (typeof updateHooks === 'function') { updateHooks(); }
        if (typeof bindModelElements === 'function') { bindModelElements(); }
        return true;
    }

    // For larger patch sets, use batching
    let failedCount = 0;
    let successCount = 0;

    // Group patches by parent for potential batching
    const patchGroups = groupPatchesByParent(patches);

    for (const [, group] of patchGroups) {
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
            if (applySinglePatch(patch)) {
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

    // Update hooks and model bindings after DOM patches
    if (typeof updateHooks === 'function') { updateHooks(); }
    if (typeof bindModelElements === 'function') { bindModelElements(); }

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

    // Mount dj-hook elements
    if (typeof mountHooks === 'function') mountHooks();

    // Bind dj-model elements
    if (typeof bindModelElements === 'function') bindModelElements();

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
                    if (globalThis.djustDebug) console.log(`[Upload] Complete: ${file.name}`, result);
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

    // Re-bind events on new DOM content
    if (typeof bindLiveViewEvents === 'function') {
        bindLiveViewEvents();
    }
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
    'use strict';

    /**
     * Handle navigation commands from the server.
     *
     * Called when the server sends a { type: "navigation", ... } message
     * after a handler calls live_patch() or live_redirect().
     */
    function handleNavigation(data) {
        if (data.type === 'live_patch') {
            handleLivePatch(data);
        } else if (data.type === 'live_redirect') {
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

        if (globalThis.djustDebug) console.log(`[LiveView] live_redirect: ${method} → ${newUrl.toString()}`);

        // Send a mount request for the new view path over the existing WebSocket
        // The server will unmount the old view and mount the new one
        if (liveViewWS && liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN) {
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

        // Fallback: check the current container's data-djust-view
        // (only works for live_patch, not cross-view navigation)
        const container = document.querySelector('[data-djust-view]');
        if (container) {
            return container.dataset.djustView;
        }

        return null;
    }

    /**
     * Listen for browser back/forward (popstate) and send url_change to server.
     */
    window.addEventListener('popstate', function (event) {
        if (!liveViewWS || !liveViewWS.viewMounted) return;
        if (!liveViewWS.ws || liveViewWS.ws.readyState !== WebSocket.OPEN) return;

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
    function bindNavigationDirectives() {
        // dj-patch: Update URL params without remount
        document.querySelectorAll('[dj-patch]').forEach(function (el) {
            if (el.dataset.djustPatchBound) return;
            el.dataset.djustPatchBound = 'true';

            el.addEventListener('click', function (e) {
                e.preventDefault();
                if (!liveViewWS || !liveViewWS.viewMounted) return;

                const patchValue = el.getAttribute('dj-patch');
                const url = new URL(patchValue, window.location.href);
                const params = Object.fromEntries(url.searchParams);

                // Update browser URL
                const newUrl = new URL(window.location.href);
                newUrl.search = url.search;
                if (url.pathname !== '/' && patchValue.startsWith('/')) {
                    newUrl.pathname = url.pathname;
                }
                window.history.pushState({ djust: true }, '', newUrl.toString());

                // Send url_change to server
                liveViewWS.sendMessage({
                    type: 'url_change',
                    params: params,
                    uri: newUrl.pathname + newUrl.search,
                });
            });
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

    return instance;
}

/**
 * Scan the DOM for elements with dj-hook and mount their hooks.
 * Called on init and after DOM patches.
 */
function mountHooks(root) {
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
window.djust.beforeUpdateHooks = beforeUpdateHooks;
window.djust.updateHooks = updateHooks;
window.djust.notifyHooksDisconnected = notifyHooksDisconnected;
window.djust.notifyHooksReconnected = notifyHooksReconnected;
window.djust.dispatchPushEventToHooks = dispatchPushEventToHooks;
window.djust.destroyAllHooks = destroyAllHooks;
window.djust._activeHooks = _activeHooks;
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
    if (typeof handleEvent === 'function') {
        handleEvent('update_model', { field, value });
    }
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
