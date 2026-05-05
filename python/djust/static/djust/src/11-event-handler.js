
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
        // eslint-disable-next-line security/detect-object-injection
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
    // eslint-disable-next-line security/detect-object-injection
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
            await applyPatches(cached.patches);
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
    // #1315: sendEvent now returns a Promise that resolves when the server
    // responds (patch, noop, or error with matching ref). Await it so callers
    // can run post-response logic (e.g. _setFormPending(false) in finally).
    const wsPromise = liveViewWS && liveViewWS.sendEvent(eventName, paramsToSend, triggerElement);
    if (wsPromise) {
        await wsPromise;
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
        await handleServerResponse(data, eventName, triggerElement);

    } catch (error) {
        console.error('[LiveView] HTTP fallback failed:', error);
        globalLoadingManager.stopLoading(eventName, triggerElement);
    }
}
window.djust.handleEvent = handleEvent;
