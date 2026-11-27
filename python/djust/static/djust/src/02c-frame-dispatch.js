
// ============================================================================
// Shared inbound frame handling (WebSocket + SSE)
// ============================================================================
//
// The two socket transports receive the same wire frames. What they handle the
// same way lives here, once, so a fix reaches both (#1646): the receipt-time
// routing every frame passes through, the application of a frame addressed to
// a view mounted beside the page view (#3252), and the `render` frame (#3658).
// Each transport keeps only what is specific to its wire: connection and
// session frames, mount, the WebSocket's buffering of server-initiated frames
// (#560) and errors. The HTTP fallback applies its answers, which are `render`
// frames too, through `applyRenderFrame`.

// ----------------------------------------------------------------------------
// Protocol version (#3658)
// ----------------------------------------------------------------------------
// The server names its protocol on `connect`, `sse_connect`, `mount` and as the
// X-Djust-Protocol header of an HTTP answer. A different one means this bundle
// and the server disagree about the frames (a rolling deploy, or a cached
// bundle): the page reloads, once. A second mismatch within 30 s (a reload that
// fetched the same bundle, or a node that is still old) does not reload again:
// the live transport stops and the server-rendered page stays usable.
const _PROTOCOL_RELOAD_KEY = 'djust:protocol-reload';
// The frames that carry the client's protocol (the server checks it there).
const _PROTOCOL_VERBS = new Set(['mount', 'mount_batch', 'live_redirect_mount']);
let _protocolReloaded = false;

/** True when `value` (a frame field or a header) is not this bundle's protocol. */
function protocolDiffers(value) {
    return Number(value) !== window.djust.PROTOCOL;
}

function protocolMismatch(transport) {
    const now = Date.now();
    let recent = _protocolReloaded;
    try {
        const last = Number(window.sessionStorage.getItem(_PROTOCOL_RELOAD_KEY));
        recent = last > 0 && now - last < 30000;
        window.sessionStorage.setItem(_PROTOCOL_RELOAD_KEY, String(now));
    } catch (_e) {
        // No storage: one reload per page load.
    }
    if (!recent) {
        _protocolReloaded = true;
        try {
            window.djust._sw?.clearCaches();
        } catch (_e) {
            if (globalThis.djustDebug) console.warn('[LiveView] clearing service-worker caches failed', _e);
        }
        window.location.reload();
        return;
    }
    if (transport && typeof transport.disconnect === 'function') transport.disconnect();
    if (transport) transport.enabled = false;
    window.dispatchEvent(new CustomEvent('djust:error', {detail: {
        error: 'This page is out of date. Reload to continue.',
        code: 'protocol_mismatch',
        transient: false,
        traceback: null,
    }}));
}

/**
 * Receipt-time routing shared by the socket transports. Returns true when the
 * frame was handled here and the transport must not dispatch it again.
 */
async function routeInboundFrame(transport, data) {
    // #3658: a server that names no protocol on its handshake, or another one
    // anywhere, speaks other frames than this bundle. A mount that names none
    // follows a handshake that already did.
    if (((data.type === 'connect' || data.type === 'sse_connect') && protocolDiffers(data.protocol)) ||
        (data.type === 'mount' && data.protocol !== undefined && protocolDiffers(data.protocol))) {
        protocolMismatch(transport);
        return true;
    }
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
    // A `render` frame's DOM part is the view's; its effects are the page's
    // (a slot's flash, title or navigation behaves as the page view's does), so
    // they apply after the slot's context is left.
    const render = data.type === 'render';
    let held = false;
    const applied = await withSlot(data.target_id, async () => {
        if (!render) return transport._handleMessageImpl(data, true);
        // The WebSocket holds a server-initiated frame while events are pending
        // (#560); the version it may consume is the slot's.
        held = !!transport._holdServerFrame?.(data);
        if (!held) await applyRenderDom(transport, data);
        return true;
    });
    if (held) return;
    if (applied === undefined) {
        // The view's container is gone (the page replaced it): its frames
        // have nowhere to go, but an event reply must still settle its request.
        const event = acknowledgeEventRequest(transport, data);
        if (event && event.eventName) globalLoadingManager.stopLoading(event.eventName, event.trigger);
    }
    if (render) await finishRenderFrame(transport, data);
}

// ----------------------------------------------------------------------------
// The `render` frame (#3658)
// ----------------------------------------------------------------------------
// One frame per turn result, on every wire: a DOM part (at most one of
// `patches`, `html`, or `view_id` + `html` for an embedded child; none is an
// acknowledgement) and `effects`, the turn's side effects in the order the
// server ranked them. The client applies the DOM part, then the effects in
// array order, then the server-initiated frames it held back while this one's
// event was pending.

/** Server-initiated frames the WebSocket holds while its own events are pending (#560). */
const _SERVER_TURN_SOURCES = ['tick', 'broadcast', 'async', 'push', 'notify'];

/**
 * Apply a frame's DOM part, with its acknowledgement and loading-state rules.
 * `event`: the request an HTTP answer settles ({eventName, trigger}); a socket
 * frame settles its own by `ref`.
 */
async function applyRenderDom(transport, data, event) {
    // An embedded child's HTML (#3104): its wrapper keeps its owner. Resolves
    // to whether it was applied (an HTTP lazy fill reads it).
    if (data.view_id !== undefined) return handleEmbeddedResponse(data, transport);
    if (event === undefined) event = acknowledgeEventRequest(transport, data);
    // A frame with no DOM part that answers no request (a deferred or pushed
    // effect) leaves the page's pending-event state alone.
    if (event || data.reset_form || Array.isArray(data.patches) || typeof data.html === 'string') {
        await handleServerResponse(data, event?.eventName, event?.trigger, transport);
    }
}

/** After the DOM part: the effects, then what the WebSocket held back. */
async function finishRenderFrame(transport, data) {
    applyEffects(data.effects);
    if (!hasPendingEventRequests(transport) && _tickBuffer.length > 0) {
        if (globalThis.djustDebug) djLog('[LiveView] Flushing %d buffered frames', _tickBuffer.length);
        await flushServerUpdates(transport);
    }
}

/** Apply one `render` frame: the DOM part, then the effects. */
async function applyRenderFrame(transport, data, event) {
    const applied = await applyRenderDom(transport, data, event);
    await finishRenderFrame(transport, data);
    return applied;
}

/**
 * Apply a frame's effects in order. A malformed list or entry, or a kind this
 * client does not know (a newer server), is skipped; one never stops the rest.
 * Each effect is the object its handler takes (the 1.x side-channel frame,
 * with `type` renamed `kind`). A `switch`, never a lookup keyed on `kind`.
 */
function applyEffects(effects) {
    if (effects === undefined || effects === null) return;
    if (!Array.isArray(effects)) {
        if (globalThis.djustDebug) console.warn('[LiveView] render frame effects is not a list; ignored');
        return;
    }
    for (const effect of effects) {
        if (!effect || typeof effect !== 'object') {
            if (globalThis.djustDebug) console.warn('[LiveView] malformed effect skipped');
            continue;
        }
        try {
            applyEffect(effect);
        } catch (error) {
            console.error('[LiveView] effect %s failed:', String(effect.kind), error);
        }
    }
}

function applyEffect(effect) {
    const djust = window.djust;
    switch (effect.kind) {
        case 'push_event':
            // To window listeners and dj-hook handleEvent registrations. An
            // upload event is held while the uploads chunk loads (#4007).
            _djustDispatchPushEvent(effect.event, effect.payload);
            break;
        case 'flash':
            if (djust.flash) djust.flash.handleFlash(effect);
            break;
        case 'page_metadata':
            if (djust.pageMetadata) djust.pageMetadata.handlePageMetadata(effect);
            break;
        case 'layout':
            if (djust.djLayout && typeof djust.djLayout.applyLayout === 'function') djust.djLayout.applyLayout(effect);
            break;
        case 'navigation':
            // live_patch / live_redirect.
            if (djust.navigation) djust.navigation.handleNavigation(effect);
            break;
        case 'navigate': {
            // A full-page navigation (an auth or refusal redirect). The shared
            // guard keeps this sink from an open redirect or a javascript: URL
            // (#1646, finding #16).
            const target = window.djust.safeNavigationTarget(effect.to);
            if (target) {
                window.location.href = target; // codeql[js/xss] -- validated via safeNavigationTarget
            } else if (globalThis.djustDebug) {
                console.warn('[LiveView] navigate target rejected: %s', String(effect.to));
            }
            break;
        }
        default:
            if (globalThis.djustDebug) console.warn('[LiveView] unknown effect kind skipped: %s', String(effect.kind));
    }
}

/**
 * Settle the event a ``rate_limit_exceeded`` frame names (#4309, #4322) as
 * ``rate_limited``, end its loading state and apply what it held back.
 * Returns false when the frame names no request of this transport.
 */
async function settleRateLimitedEvent(transport, data) {
    if (data.ref == null || _pendingEventOwners.get(data.ref) !== transport) return false;
    cancelEventRequests(transport, data.ref, eventErrorOutcome({error: data.message, code: 'rate_limited'}));
    if (!hasPendingEventRequests(transport)) await flushServerUpdates(transport);
    return true;
}

/**
 * Handle a frame both socket transports apply the same way. Returns false for
 * a frame type this does not know, so the caller can report it.
 *
 * @param {Object} transport  The receiving transport.
 * @param {Object} data       The inbound frame.
 */
async function dispatchCommonFrame(transport, data) {
    switch (data.type) {
        case 'rate_limit_exceeded':
            // SSE (and any transport without its own case, #4322): a
            // turn-policy refusal names the dropped event's ref.
            await settleRateLimitedEvent(transport, data);
            return true;

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

        case 'reload':
            // Hot reload: file changed, refresh the page. A protocol reload
            // (#3658) goes through the loop guard.
            if (data.reason === 'protocol') {
                // The socket's 4409 close that follows needs no second verdict.
                if (transport) transport._protocolReloadSocket = transport.ws || true;
                protocolMismatch(transport);
            } else window.location.reload();
            return true;

        default:
            return false;
    }
}
