
// One-time guard so the actionable HTTP-fallback warning (#1674) fires once
// per session, not on every degraded event. Function-scoped within the bundle
// IIFE (#1635).
let _djustHttpFallbackWarned = false;

// Local operations share request bookkeeping with socket transports. Their
// completion is owned by the awaited operation, never by a server-supplied ref.
const _localEventTransport = {};
let _httpPageGeneration = 0;
const _pendingHttpControllers = new Set();
for (const event of ['djust:before-navigate', 'turbo:before-visit', 'pagehide']) {
    window.addEventListener(event, () => {
        _httpPageGeneration += 1;
        for (const controller of _pendingHttpControllers) controller.abort();
        _pendingHttpControllers.clear();
    });
}

// ADR-036: the page's own (HTTP) contract scope. get() renders the root
// mount's owner contracts into script[data-djust-parameter-contracts], outside dj-root;
// HTTP fallback responses refresh them like WS/SSE render frames. A page whose
// data block names another view (a socket live_redirect replaced the root)
// leaves the scope unknown rather than applying a stale mount's rules.
function _installPageParameterContracts() {
    const root = findPageViewContainer();
    const path = root ? root.getAttribute('dj-view') : null;
    _localEventTransport.primaryViewPath = path || null;
    _localEventTransport._parameterContracts = new Map();
    _localEventTransport._parameterContractApplied = new Map();
    if (!path) return;
    const block = document.querySelector('script[data-djust-parameter-contracts]');
    let manifest = null;
    if (block) {
        let payload = null;
        try { payload = JSON.parse(block.textContent); } catch { payload = null; }
        if (!payload || payload.view !== path) {
            if (payload === null) _localEventTransport._parameterContracts.set(path, false);
            return;
        }
        manifest = payload.contracts;
    }
    try {
        _installParameterContracts(_localEventTransport, manifest, path, true, 0);
    } catch {
        // The scope is recorded as invalid; strict lookups fail closed.
        if (globalThis.djustDebug) console.warn('[LiveView] Invalid page parameter contracts');
    }
}

// The transport handleEvent() would send through right now.
function _eventContractTransport() {
    const socket = liveViewWS;
    if (socket && socket.enabled && socket.viewMounted &&
        (!socket.ws || (typeof WebSocket !== 'undefined' && socket.ws.readyState === WebSocket.OPEN))) {
        return socket;
    }
    return _localEventTransport;
}

// A socket mount of the page's root also refreshes the page scope, so an HTTP
// fallback after a socket live_redirect uses the current mount's contracts.
function _mirrorPageParameterContracts(manifest, viewPath) {
    if (typeof viewPath !== 'string' || !viewPath) return;
    _localEventTransport.primaryViewPath = viewPath;
    try {
        _installParameterContracts(_localEventTransport, manifest, viewPath, true, 0);
    } catch {
        // Recorded as invalid; strict lookups fail closed.
    }
}

// Resolve the public contract a native binding would dispatch under. The mount
// is the element's nearest non-embedded dj-view root (normally the page root).
// The owner address matches server routing: an embedded child's view_id wins
// over a component inside it. Returns {policy: 'legacy'|'strict'|'unknown'}.
// 'unknown' covers a mount this transport holds no record for (never delivered
// a contract: an unmounted, lazy or bare root) and an owner or handler a known
// mount does not list; neither can be strict, since strict contracts are always
// delivered and every strict handler is listed. Binders keep legacy collection
// and the server stays authoritative. A mount whose strict snapshot is invalid
// or missing (recorded as invalid on receipt) throws: callers fail closed
// rather than guess (ADR-036 Q1).
function _resolveParameterContract(element, eventName) {
    const transport = _eventContractTransport();
    const container = (element && element.closest &&
        element.closest('[dj-view]:not([data-djust-embedded])')) || findPageViewContainer();
    const path = container ? container.getAttribute('dj-view') : null;
    const mounts = transport._parameterContracts;
    if (!path || !mounts || !mounts.has(path)) return {policy: 'unknown', transport};
    const owners = mounts.get(path);
    if (owners === null) return {policy: 'legacy', transport};
    if (owners === false) throw new Error('Invalid public parameter contracts');
    const viewId = (element && getEmbeddedViewId(element)) || null;
    const componentId = viewId ? null : ((element && getComponentId(element)) || null);
    const handlers = owners.get(JSON.stringify([viewId, componentId]));
    if (!handlers || !handlers.has(eventName)) return {policy: 'unknown', transport};
    const contract = handlers.get(eventName);
    return {policy: contract.policy, contract, transport, viewId, componentId};
}

// Wire hints a declared type accepts (ADR-036 D3): a conflicting explicit hint
// is rejected rather than converted twice. Unhinted text is always accepted.
const _WIRE_HINT_TYPES = {
    int: ['int', 'float', 'Decimal'], integer: ['int', 'float', 'Decimal'],
    float: ['float'], number: ['float'],
    bool: ['bool'], boolean: ['bool'],
    json: null, array: ['list'], list: ['list'], object: [],
};

function _hintAccepted(hint, label) {
    let type = label;
    const optional = /^Optional\[(.*)\]$/.exec(type);
    if (optional) type = optional[1];
    if (type === 'Any') return true;
    const base = type.startsWith('list[') ? 'list' : type;
    // eslint-disable-next-line security/detect-object-injection
    const accepted = _WIRE_HINT_TYPES[hint];
    return accepted === null || (accepted !== undefined && accepted.includes(base));
}

// ADR-036 strict collection for a native binding. Returns null when the
// binding is not strict: the caller keeps its unchanged legacy params. For a
// strict handler it returns the application payload: dj-value-* arguments
// (strict literals) plus only the generated values the handler declares, or
// all of them for a ** catch-all (Q1). _target is never generated (Q2).
// Throws, with a value-free message, when the arguments are rejected.
function _strictEventParams(element, eventName, generated = {}, positional = []) {
    const resolved = _resolveParameterContract(element, eventName);
    if (resolved.policy !== 'strict') return null;
    const parameters = resolved.contract.parameters;
    const named = new Map(parameters
        .filter(p => p.kind === 'positional_or_keyword' || p.kind === 'keyword_only')
        .map(p => [p.name, p]));
    const openPayload = parameters.find(p => p.kind === 'var_keyword');
    const reject = () => { throw new Error('Invalid strict event arguments'); };
    const explicit = new Map();
    for (const attr of element.attributes) {
        if (!attr.name.startsWith('dj-value-')) continue;
        const parts = attr.name.slice(9).split(':');
        explicit.set(parts[0].replace(/-/g, '_'), parts[1]);
    }
    const sent = Object.create(null);
    for (const key of Object.keys(generated)) {
        if (explicit.has(key)) reject();
        if (named.has(key) || openPayload) {
            // eslint-disable-next-line security/detect-object-injection
            sent[key] = generated[key];
        }
    }
    for (const [key, hint] of explicit) {
        if (!hint) continue;
        const parameter = named.get(key) || openPayload;
        if (parameter && !_hintAccepted(hint, parameter.type)) reject();
    }
    const values = _collectStrictEventParams(element, sent, positional);
    // A value supplied both positionally and by name is an error, not a choice.
    const leading = parameters
        .filter(p => p.kind === 'positional_only' || p.kind === 'positional_or_keyword')
        .slice(0, positional.length);
    if (leading.some(p => Object.hasOwn(values, p.name))) reject();
    return values;
}

// Value-free, before any disable/optimistic/loading effect (ADR-036 N1).
function _reportStrictRejection(eventName) {
    console.error('[LiveView] Event arguments rejected by the handler contract:', eventName);
    window.dispatchEvent(new CustomEvent('djust:error', {detail: {
        error: 'Invalid event arguments for this handler.',
        traceback: null, event: eventName, validation_details: null,
    }}));
}

// Binder entry point. Returns strict params, null for a legacy/unknown binding
// (keep the legacy params), or false when a strict binding was rejected and
// reported: the caller must return before any effect.
function _strictBinding(element, eventName, generated, positional) {
    try {
        return _strictEventParams(element, eventName, generated, positional);
    } catch {
        _reportStrictRejection(eventName);
        return false;
    }
}

// Main Event Handler
//
// `_rateBypass` (#2656) is the re-entry flag for the @debounce / @throttle
// gate: the deferred send calls back in with it set so the gate does not
// re-defer its own timer's dispatch. It is a THIRD ARGUMENT rather than a
// params key on purpose — anything written into `params` would have to be
// stripped again before the payload is serialized to the server.
async function handleEvent(eventName, params = {}, _rateBypass = false) {
    // Snapshot before any await: teardown's scope ends after synchronous dispatch.
    const teardown = teardownEventTransport;
    if (teardown && teardown.collecting) {
        teardown.pending.set(eventName, params);
        return;
    }
    if (globalThis.djustDebug) {
        djLog(`[LiveView] Handling event: ${eventName}`, params);
    }

    // @debounce / @throttle: collapse or cap the outbound send. Runs before
    // anything else so a deferred event costs no loading state, no cache
    // lookup and no DOM work.
    if (!teardown && !_rateBypass && applyHandlerRateLimit(eventName, params, handleEvent)) {
        return;
    }

    // Extract client-only properties before sending to server.
    // These are DOM references or internal flags that cannot be JSON-serialized
    // and would corrupt the params payload (e.g., HTMLElement objects serialize
    // as objects with numeric-indexed children that clobber form field data).
    const triggerElement = params._targetElement;
    const skipLoading = params._skipLoading || !!teardown;

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

    if (cached && !teardown) {
        // Cache hit! Apply cached patches without server round-trip
        if (globalThis.djustDebug) {
            djLog(`[LiveView:cache] Cache hit: ${cacheKey}`);
        }

        // Still show brief loading state for UX consistency
        if (!skipLoading) globalLoadingManager.startLoading(eventName, triggerElement);

        const cachedRequest = registerEventRequest(_localEventTransport, eventName, triggerElement);
        try {
            // Apply cached patches
            if (cached.patches && cached.patches.length > 0) {
                await applyPatches(cached.patches);
                reinitAfterDOMUpdate();
            }
        } finally {
            cancelEventRequests(_localEventTransport, cachedRequest.ref);
        }
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
    if (config && !teardown) {
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
    const wsPromise = liveViewWS && (teardown
        ? (liveViewWS.sendTeardownEvent && liveViewWS.sendTeardownEvent(eventName, paramsToSend, triggerElement))
        : liveViewWS.sendEvent(eventName, paramsToSend, triggerElement));
    if (wsPromise) {
        await wsPromise;
        return;
    }

    // Fallback to HTTP. Emit an actionable, NON-debug-gated warning ONCE per
    // session (#1674): a URL-routed LiveView missing from
    // LIVEVIEW_ALLOWED_MODULES has its WebSocket mount rejected and silently
    // degrades to full-page HTTP re-renders that *look* like the app works.
    // The server intentionally returns a generic "View not found" (no allowlist
    // detail leaked), so the client points the developer at the likely cause.
    //
    // #2721: a teardown flush reaches this path BY DESIGN — the WS branch
    // above deliberately falls through because `LiveViewWebSocket` has no
    // `sendTeardownEvent`. That says nothing about the socket's health, so
    // warning here would be wrong AND would burn the once-per-session flag,
    // silencing a later genuine degraded mount — inverting the point of #1674.
    if (!teardown && !_djustHttpFallbackWarned) {
        _djustHttpFallbackWarned = true;
        console.warn(
            '[LiveView] Events are falling back to full-page HTTP re-renders '
            + '(WebSocket unavailable, or the view\'s mount was rejected). '
            + 'If this is your own LiveView, check that its module is listed in '
            + 'LIVEVIEW_ALLOWED_MODULES in your Django settings.'
        );
    }
    if (globalThis.djustDebug) console.log('[LiveView] WebSocket unavailable, falling back to HTTP');

    const httpRequest = teardown ? null
        : registerEventRequest(_localEventTransport, eventName, triggerElement);
    // Keepalive teardown sends deliberately outlive the outgoing page.
    const httpController = teardown ? null : new AbortController();
    if (httpController) _pendingHttpControllers.add(httpController);
    const httpOwner = document.querySelector('[dj-root]') || document.body;
    const httpUrl = window.location.href;
    const httpGeneration = _httpPageGeneration;
    const ownsHttpResponse = () => httpOwner === (document.querySelector('[dj-root]') || document.body)
        && httpUrl === window.location.href && httpGeneration === _httpPageGeneration;
    try {
        // Input, configured-name cookie, then server meta tag (00-namespace.js).
        const csrfToken = window.djust.csrfToken();
        const response = await fetch(teardown ? teardown.url : window.location.href, {
            keepalive: !!teardown,
            ...(httpController ? {signal: httpController.signal} : {}),
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
                'X-Djust-Event': eventName,
                // A strict (or invalid) page scope asks for an explicit
                // contract clear when the rendered tree no longer has one.
                ...(_localEventTransport._parameterContracts?.get(_localEventTransport.primaryViewPath) != null
                    ? {'X-Djust-Parameter-Contracts': '1'} : {})
            },
            body: JSON.stringify(paramsToSend)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        // This response belongs to the outgoing view; never patch the new one.
        if (teardown || !ownsHttpResponse()) return;
        const data = await response.json();
        // Parsing can yield after headers arrived; navigation during either
        // await invalidates every response effect, including metadata/cache.
        if (!ownsHttpResponse()) return;
        // Same client-owned-flag strip as the WebSocket and SSE transports
        // (#2829) — the HTTP fallback dispatches straight into
        // handleServerResponse, so it needs its own call.
        stripClientOwnedFrameFlags(data);
        _recordParameterContractFrame(_localEventTransport, data);
        await handleServerResponse(data, eventName, triggerElement, _localEventTransport);

    } catch (error) {
        if (!httpController?.signal.aborted) console.error('[LiveView] HTTP fallback failed:', error);
    } finally {
        if (httpController) _pendingHttpControllers.delete(httpController);
        if (httpRequest) cancelEventRequests(_localEventTransport, httpRequest.ref);
    }
}
window.djust.handleEvent = handleEvent;
window.djust._installPageParameterContracts = _installPageParameterContracts;
window.djust._mirrorPageParameterContracts = _mirrorPageParameterContracts;
window.djust._strictBinding = _strictBinding;
window.djust._resolveParameterContract = _resolveParameterContract;
