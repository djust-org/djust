
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
