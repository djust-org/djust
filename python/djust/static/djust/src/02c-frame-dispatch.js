
// ============================================================================
// Code shared by the socket transports (WebSocket + SSE)
// ============================================================================
//
// The two socket transports speak the same wire frames. What they do the same
// way lives here, once, so a fix reaches both (#1646). Inbound: the
// receipt-time routing every frame passes through, the application of a frame
// addressed to a view mounted beside the page view (#3252), and the
// side-channel frames. Outbound: the mount and event frames, and the views
// mounted beside the page view. Each transport keeps only what is specific to
// its wire: connecting, sending, connection and session frames, mount replies,
// event replies (the WebSocket buffers server-initiated patches, #560) and
// errors. The `djust:error` detail of a server error is built here for the
// HTTP fallback too.

/**
 * Receipt-time routing shared by the socket transports. Returns true when the
 * frame was handled here and the transport must not dispatch it again.
 */
async function routeInboundFrame(transport, data) {
    // #3036: a live navigation to a page with a different page shell is a
    // full page load; the destination's root is not applied.
    if (window.djust.fallBackToFullLoadOnShellChange?.(data)) return true;
    // ADR-038 D-n: compared before anything from this mount is cached.
    if (window.djust._sw) window.djust._sw.applyMountMetadata(data);
    storeSignedSnapshot(data, transport.primaryViewPath);

    // A frame addressed to a view mounted beside the page view (#3252)
    // is applied to that view's container, in its own context.
    if (typeof data.target_id === 'string' && data.target_id &&
        _SLOT_FRAME_TYPES.has(data.type)) {
        await transport._handleSlotFrame(data);
        return true;
    }
    return false;
}

/**
 * Apply a frame the server addressed to a view mounted beside the page view
 * (``target_id``, #3252). A ``mount`` reply fills the view's container; any
 * other frame is handled by the code that handles the page view's frames, run
 * in the view's context (``withSlot``), so it patches the view's container
 * against the view's own VDOM version.
 *
 * ``markMounted``: the WebSocket counts a slot's mount reply as its own
 * ``viewMounted``; the SSE session is mounted by the page view alone.
 */
async function handleSlotFrame(transport, data, markMounted) {
    if (data.type === 'mount') {
        if (markMounted) transport.viewMounted = true;
        const container = slotContainer(data.target_id);
        const hadContent = !!container && container.innerHTML.trim().length > 0;
        applySlotMount(transport, data, { html: hadContent ? 'morph' : 'replace' });
        document.querySelectorAll('[dj-cloak]').forEach(el => el.removeAttribute('dj-cloak'));
        return;
    }
    const applied = await withSlot(data.target_id, () => transport._handleMessageImpl(data, true));
    if (applied === undefined) {
        // The view's container is gone (the page replaced it): its frames
        // have nowhere to go, but an event reply must still settle its request.
        const event = acknowledgeEventRequest(transport, data);
        if (event && event.eventName) globalLoadingManager.stopLoading(event.eventName, event.trigger);
    }
}

/**
 * Handle a frame both socket transports apply the same way. Returns false for
 * a frame type this does not know, so the caller can report it.
 *
 * @param {Object} transport  The receiving transport.
 * @param {Object} data       The inbound frame.
 * @param {string} logPrefix  The transport's console prefix.
 */
async function dispatchCommonFrame(transport, data, logPrefix) {
    switch (data.type) {
        case 'view_refused':
            // A view beside the page view is refused (login, permission, an
            // `on_mount` redirect): its container shows it, the page does
            // not navigate (#3252).
            applyViewRefusal(data);
            return true;

        case 'async_complete':
            completeAsyncBatch(transport, data.async_batch);
            return true;

        case 'stream':
            // Streaming partial DOM updates (LLM chat, live feeds)
            if (window.djust.handleStreamMessage) {
                window.djust.handleStreamMessage(data);
            }
            return true;

        case 'push_event':
            // Server-pushed event for JS hooks
            window.dispatchEvent(new CustomEvent('djust:push_event', {
                detail: { event: data.event, payload: data.payload }
            }));
            // Dispatch to dj-hook instances that registered via handleEvent
            dispatchPushEventToHooks(data.event, data.payload);
            // Clear loading state — when _skip_render is used, this is the
            // only response the client gets (no patch/html_update follows).
            globalLoadingManager.stopLoading(transport.lastEventName, transport.lastTriggerElement);
            return true;

        case 'embedded_update':
            // Scoped HTML update for an embedded child LiveView
            await handleEmbeddedResponse(data, transport);
            return true;

        case 'navigate': {
            // Auth redirect (sent by the server when an auth check fails).
            // Validate scheme/origin via the shared guard so this sink can't
            // pivot to open-redirect / javascript: DOM-XSS (#1646, finding #16).
            const navTarget = window.djust.safeNavigationTarget(data.to);
            if (navTarget) {
                window.location.href = navTarget; // codeql[js/xss] -- validated via safeNavigationTarget
            } else if (globalThis.djustDebug) {
                console.warn(logPrefix + ' navigate target rejected: %s', String(data.to));
            }
            return true;
        }

        case 'navigation':
            // Server-side live_patch or live_redirect
            if (window.djust.navigation) {
                window.djust.navigation.handleNavigation(data);
            }
            return true;

        case 'accessibility':
            // Screen reader announcements from server
            if (window.djust.accessibility) {
                window.djust.accessibility.processAnnouncements(data.announcements);
            }
            return true;

        case 'focus':
            // Focus command from server
            if (window.djust.accessibility) {
                window.djust.accessibility.processFocus([data.selector, data.options]);
            }
            return true;

        case 'flash':
            // Flash message from server (put_flash / clear_flash)
            if (window.djust.flash) {
                window.djust.flash.handleFlash(data);
            }
            return true;

        case 'page_metadata':
            // Page metadata from server (page_title / page_meta)
            if (window.djust.pageMetadata) {
                window.djust.pageMetadata.handlePageMetadata(data);
            }
            return true;

        case 'layout':
            // Runtime layout switch (set_layout). The server sends it from the
            // shared turn end, so it reaches both transports.
            if (window.djust.djLayout && typeof window.djust.djLayout.applyLayout === 'function') {
                window.djust.djLayout.applyLayout(data);
            }
            return true;

        case 'reload':
            // Hot reload: file changed, refresh the page
            window.location.reload();
            return true;

        default:
            return false;
    }
}

/**
 * The ``djust:error`` detail fields every transport reports for a server
 * error: the WebSocket and SSE ``error`` frames and the HTTP fallback's error
 * body. ``primaryViewPath`` names the view when the error does not.
 */
function serverErrorDetail(data, primaryViewPath) {
    return {
        error: data.error,
        // Stable machine-readable code (#3319), e.g. 'permission_denied'.
        code: typeof data.code === 'string' ? data.code : null,
        transient: data.transient === true,
        view: typeof data.view === 'string' ? data.view : (primaryViewPath || null),
        target_id: typeof data.target_id === 'string' ? data.target_id : null,
        traceback: data.traceback || null,
    };
}

// ----------------------------------------------------------------------------
// Outbound
// ----------------------------------------------------------------------------

/** The browser's IANA timezone, or null when it cannot be read. */
function detectClientTimezone(onError) {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (e) {
        if (onError) onError(e);
        return null;
    }
}

/**
 * A ``mount`` frame. ``url`` lets the server resolve URL kwargs (pk, slug, ...)
 * from the page URL (#1237); ``targetId`` names the container of a view mounted
 * beside the page view (#3252).
 */
function buildMountFrame(viewPath, params, hasPrerendered, clientTimezone, targetId = null) {
    const frame = {
        type: 'mount',
        view: viewPath,
        params: params,
        url: window.location.pathname,
        has_prerendered: hasPrerendered,
        client_timezone: clientTimezone,
    };
    if (targetId) frame.target_id = targetId;
    return frame;
}

/**
 * Send an event and return the promise its reply settles (#1315). The request
 * is registered before the send, so even an immediate reply finds it, and a
 * send that fails releases it.
 */
function sendEventRequest(transport, eventName, params, triggerElement, slotId) {
    const request = registerEventRequest(transport, eventName, triggerElement);
    try {
        if (transport.sendMessage({
            type: 'event',
            event: eventName,
            params: params,
            ref: request.ref,
            // An event from inside a view mounted beside the page view runs on
            // that view (#3252); the page view's carries no address.
            ...slotFrameFields(slotId || slotIdFor(triggerElement))
        }) === false) {
            cancelEventRequests(transport, request.ref);
        }
    } catch (error) {
        cancelEventRequests(transport, request.ref);
        throw error;
    }
    return request.promise;
}

/**
 * Mount again every view hydrated beside the page view on a new connection
 * (a reconnect): the server holds no view for a connection that closed.
 */
function remountSlotViews(transport) {
    const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
    for (const [targetId, slot] of Array.from(_mountedSlots.entries())) {
        if (!slotContainer(targetId)) {
            forgetSlot(targetId);
            continue;
        }
        _slotVersions.set(targetId, null);
        transport.mount(slot.viewPath, urlParams, { targetId: targetId, hasPrerendered: true });
    }
}

/**
 * Tell the server a view hydrated beside the page view is gone (its container
 * was removed). The page view and the other views stay live. ``canSend``: the
 * transport can send now; the slot is forgotten either way.
 */
function unmountSlotView(transport, targetId, canSend) {
    if (!_mountedSlots.has(targetId)) return false;
    forgetSlot(targetId);
    if (!canSend()) return false;
    transport.sendMessage({ type: 'unmount', target_id: targetId });
    return true;
}
