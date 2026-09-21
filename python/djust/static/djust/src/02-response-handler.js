
// ============================================================================
// Centralized Response Handler (WebSocket + HTTP)
// ============================================================================

/**
 * Drop CLIENT-OWNED frame flags from an INBOUND server frame.
 *
 * ``_deferred`` is set by the client on frames we deliberately deferred while a
 * user event was pending (see `_tickBuffer`), and the version check uses it to
 * tell our own deferral from a dropped patch (#2829). It must therefore never
 * be settable from the wire — otherwise a server (or anything that can write to
 * the transport) could suppress the dropped-patch detection for a frame.
 *
 * Every transport that hands a server frame to `handleServerResponse` calls
 * this first: the WebSocket (`03-websocket.js`), SSE (`03b-sse.js`) and the
 * HTTP fallback (`11-event-handler.js`). One helper rather than an inline
 * `delete` per transport, so a future transport cannot quietly omit it. The
 * deferred REPLAY calls `handleServerResponse` directly and so keeps our own
 * marker.
 *
 * @param {object} data - Parsed inbound frame (mutated in place)
 * @returns {object} the same frame
 */
function stripClientOwnedFrameFlags(data) {
    if (data && typeof data === 'object') {
        delete data._deferred;
        delete data._versionConsumed;
    }
    return data;
}

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

/** Morph a server-rendered child subtree without replacing its owner wrapper. */
function applyEmbeddedUpdate(data) {
    if (typeof data.view_id !== 'string' || !data.view_id || typeof data.html !== 'string') {
        if (globalThis.djustDebug) console.warn('[LiveView] Invalid embedded update');
        return false;
    }
    const container = document.querySelector(`[data-djust-embedded="${CSS.escape(data.view_id)}"]`);
    if (!container) return false;
    const incoming = document.createElement('div');
    // codeql[js/xss] -- html is rendered by the trusted Django/Rust server template engine
    incoming.innerHTML = data.html;
    morphChildren(container, incoming);
    _warnDeadScripts(container);
    reinitAfterDOMUpdate();
    return true;
}

/** Shared WS/SSE child response path; background frames cannot acknowledge an event. */
async function handleEmbeddedResponse(data, transport) {
    // Capture ownership before morphing: the response may remove its trigger.
    const tracked = data.ref != null && _pendingEventOwners.get(data.ref) === transport;
    const eventName = tracked ? _pendingEventNames.get(data.ref) : transport.lastEventName;
    const trigger = tracked ? _pendingTriggerEls.get(data.ref) : transport.lastTriggerElement;
    const owner = trigger && trigger.closest('[data-djust-embedded]');
    const ownerId = owner && owner.getAttribute('data-djust-embedded');
    const applied = applyEmbeddedUpdate(data);
    // A legitimate reply may arrive after its owner was removed. Settle its
    // own request rather than leaking the promise, but reject malformed frames.
    if (!applied && (!tracked || typeof data.view_id !== 'string' || !data.view_id ||
        typeof data.html !== 'string')) return false;
    if (data.source === 'async') {
        completeLegacyAsyncBatches(transport, data);
        return true;
    }
    // No-ref SSE replies must match the pending element's scope. A reply for
    // another child must not consume the most recently sent event's state.
    if (!tracked && (ownerId !== data.view_id ||
        (data.event_name && data.event_name !== eventName))) {
        return true;
    }
    const event = acknowledgeEventRequest(transport, data);
    if (event?.eventName && !data.async_pending) globalLoadingManager.stopLoading(event.eventName, event.trigger);
    if (!hasPendingEventRequests(transport) && _tickBuffer.length > 0) {
        await flushServerUpdates(transport);
    }
    return true;
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
async function handleServerResponse(data, eventName, triggerElement) {
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
            } else if (data._deferred && data._versionConsumed) {
                // A deferred frame whose version was ALREADY consumed at
                // receipt (see _tickBuffer, 03-websocket.js): the gap between it
                // and the cursor is our own deferral, not a dropped patch, so
                // applying it must not trigger a recovery (#2829). Forward only —
                // a flush runs after later frames may have advanced the cursor.
                //
                // `_versionConsumed` is load-bearing: a deferred frame whose
                // version was NOT consumed (the buffer site declined it because
                // it was non-contiguous) must FALL THROUGH to the strict check
                // below. Advancing for it here would vouch for the versions in
                // between and swallow the drop permanently — which is exactly
                // what an unconditional advance did on the noop-close path,
                // where no strict check runs on the closing frame.
                clientVdomVersion = Math.max(clientVdomVersion, data.version);
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
            } else {
                // Normal in-order frame: advance the cursor.
                clientVdomVersion = data.version;
            }
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
            const success = await applyPatches(data.patches);
            _isBroadcastUpdate = false;

            // For broadcast patches, sync textarea .value from .textContent.
            // VDOM patches update textContent directly (not via innerHTML),
            // so preserveFormValues never runs. Textarea .value is separate
            // from .textContent after initial render — must sync explicitly.
            // Routed through the shared sweep so the dj-update="ignore"
            // per-field opt-out (#1991) is honored here too (parallel-path
            // #1646 — the helper lives once, in 12-vdom-patch.js).
            if (data.broadcast) {
                syncBroadcastTextareas(getLiveViewRoot());
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
