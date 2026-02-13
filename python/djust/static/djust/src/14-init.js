
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
    if (eagerContainers.length > 0 && wsEnabled) {
        // Initialize WebSocket
        liveViewWS = new LiveViewWebSocket();
        window.djust.liveViewInstance = liveViewWS;
        liveViewWS.connect();

        // Start heartbeat
        liveViewWS.startHeartbeat();
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
    if (typeof mountHooks === 'function') mountHooks();

    // Bind dj-model elements
    if (typeof bindModelElements === 'function') bindModelElements();

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
