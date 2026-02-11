
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
    const allContainers = document.querySelectorAll('[data-djust-view]');
    const lazyContainers = document.querySelectorAll('[data-djust-view][data-djust-lazy]');
    const eagerContainers = document.querySelectorAll('[data-djust-view]:not([data-djust-lazy])');

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

    // Re-bind events
    bindLiveViewEvents();

    // Re-scan dj-loading attributes
    globalLoadingManager.scanAndRegister();

    if (globalThis.djustDebug) console.log('[LiveView:TurboNav] Reinitialization complete');
}
