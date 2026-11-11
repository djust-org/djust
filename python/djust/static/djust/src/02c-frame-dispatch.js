
// ============================================================================
// Shared inbound frame handling (WebSocket + SSE)
// ============================================================================
//
// The two socket transports receive the same wire frames. What they handle the
// same way lives here, once, so a fix reaches both (#1646): the receipt-time
// routing every frame passes through, the application of a frame addressed to
// a view mounted beside the page view (#3252), and the side-channel frames.
// Each transport keeps only what is specific to its wire: connection and
// session frames, mount, event replies (the WebSocket buffers server-initiated
// patches, #560) and errors.

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
