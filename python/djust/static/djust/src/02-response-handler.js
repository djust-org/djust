
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
                if (globalThis.djustDebug) console.log(`[LiveView:cache] Cached patches: ${cacheKey} (TTL: ${ttl}s)`);
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
                if (liveViewWS && liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN) {
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
        document.querySelectorAll('.optimistic-pending').forEach(el => {
            el.classList.remove('optimistic-pending');
        });

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

                if (liveViewWS && liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN) {
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
            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            initReactCounters();
            initTodoItems();
            bindLiveViewEvents();
        }
        // Apply full HTML update (fallback)
        else if (data.html) {
            if (globalThis.djustDebug) console.log('[LiveView] Applying full HTML update');
            _isBroadcastUpdate = !!data.broadcast;
            const parser = new DOMParser();
            const doc = parser.parseFromString(data.html, 'text/html');
            const liveviewRoot = getLiveViewRoot();
            if (!liveviewRoot) {
                globalLoadingManager.stopLoading(eventName, triggerElement);
                window.location.reload();
                return false;
            }
            const newRoot = doc.querySelector('[dj-root]') || doc.body;

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
            if (globalThis.djustDebug) console.warn('[LiveView] Response has neither patches nor html!', data);
        }

        // Handle form reset
        if (data.reset_form) {
            if (globalThis.djustDebug) console.log('[LiveView] Resetting form');
            const form = document.querySelector('[dj-root] form');
            if (form) form.reset();
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
        } else if (globalThis.djustDebug) {
            if (globalThis.djustDebug) console.log('[LiveView] Keeping loading state — async work pending');
        }
        return true;

    } catch (error) {
        if (globalThis.djustDebug) console.error('[LiveView] Error in handleServerResponse:', error);
        globalLoadingManager.stopLoading(eventName, triggerElement);
        return false;
    }
}
