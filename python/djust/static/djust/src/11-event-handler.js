
// One-time guard so the actionable HTTP-fallback warning (#1674) fires once
// per session, not on every degraded event. Function-scoped within the bundle
// IIFE (#1635).
let _djustHttpFallbackWarned = false;

// Local operations share request bookkeeping with socket transports. Their
// completion is owned by the awaited operation, never by a server-supplied ref.
const _localEventTransport = {};
let _httpPageGeneration = 0;
const _pendingHttpControllers = new Set();
// HTTP fallback events run one at a time, in dispatch order, like frames on
// one socket. Each POST restores and saves the view's session state, so two
// in flight at once lose one's changes, and a stale response can overwrite
// input typed since. Null when nothing is in flight, so an event sent alone
// still goes out synchronously.
let _httpEventChain = null;
// #4108: the page-POST fallback answers with patches when the server can show
// that this page displays the render it would diff against. The proof is a
// token in two halves, "<digest>.<shape>". The digest is a keyed digest of
// that render: the page GET carries one (a <meta>, outside dj-root), every
// fallback answer carries the next, and each fallback POST echoes the latest.
// The shape is a checksum of that render's element tree (domShape,
// 12-vdom-patch.js). A patch answer is addressed by path, so it may only be
// applied to a DOM with the tree the server diffed against: the client sends
// the shape of its own DOM with the digest (the server answers with patches
// only if it is its render's shape), sends no token at all while its DOM has
// another shape (a script inserted or removed a node, a dj-remove exit is
// still playing), and checks the shape again before it applies the patches.
// The digest is null when unknown (a socket or SSE mount rendered this page,
// or something changed the DOM without naming a token); the server then
// answers with the whole page, as it does when a token does not match. There
// is no version counter on this path: a patch answer has no `version`, and a
// whole-page answer re-bases the cursor (see handleEvent).
let _httpRenderDigest = null;
let _httpRenderShape = null;
// Whole-page answers in a row to requests that sent a token. A page whose
// render differs on every request (a clock, a random id) never matches, and
// each token costs the server a second render: after two misses it is
// withheld for `_HTTP_DIGEST_HOLD` events, then offered again.
let _httpDigestMisses = 0;
let _httpDigestHold = 0;
const _HTTP_DIGEST_HOLD = 8;
const _RENDER_DIGEST_META = 'meta[name="djust-render-digest"]';

function _setRenderToken(token) {
    // A token that is not "<digest>.<shape>" leaves a shape no DOM has, so
    // nothing is sent for it.
    const parts = typeof token === 'string' ? token.split('.') : [];
    _httpRenderDigest = parts[0] || null;
    _httpRenderShape = parts[1] || null;
}

function _readPageRenderDigest() {
    const meta = document.querySelector(_RENDER_DIGEST_META);
    _setRenderToken(meta && meta.getAttribute('content'));
    _httpDigestMisses = _httpDigestHold = 0;
    _markDomAsRendered();
}

// The DOM is the server's render now (the page as loaded, or an answer just
// applied): number its elements, so a later move, swap or replacement shows.
function _markDomAsRendered() {
    if (_httpRenderDigest) domShape(getLiveViewRoot(), 'mark');
}

// Whether the DOM still is the render `shape` stands for: the same tree, made
// of the same elements in the same order.
function _domIsRender(shape) {
    return domShape(getLiveViewRoot(), 'verify') === shape;
}

// The token this fallback request sends, if any: the digest, and the shape of
// the DOM as it is now.
function _renderTokenToSend() {
    if (!_httpRenderDigest) return null;
    if (_httpDigestHold > 0) {
        _httpDigestHold -= 1;
        return null;
    }
    // The DOM is not the render the digest stands for (another layout, or
    // elements moved or replaced): patches computed from that render would
    // land on other nodes.
    if (!_domIsRender(_httpRenderShape)) return null;
    return _httpRenderDigest + '.' + _httpRenderShape;
}

// Record what a fallback answer says about the render now on the page.
function _noteRenderDigest(data, sent) {
    const whole = typeof data.html === 'string';
    const changed = whole || (Array.isArray(data.patches) && data.patches.length > 0)
        || data.type === 'embedded_update';
    if (typeof data.render_digest === 'string' && data.render_digest) {
        _setRenderToken(data.render_digest);
    } else if (changed) {
        _setRenderToken(null);
    }
    if (!sent || !changed) return;
    if (!whole) {
        _httpDigestMisses = 0;
    } else if (++_httpDigestMisses >= 2) {
        _httpDigestHold = _HTTP_DIGEST_HOLD;
    }
}

// Every answer of this transport enters here, an event's and a resync's: the
// same client-owned-flag strip as the WebSocket and SSE transports (#2829; the
// HTTP fallback dispatches straight into handleServerResponse, so it needs its
// own call), then the contract bookkeeping.
function _receiveHttpAnswer(data) {
    stripClientOwnedFrameFlags(data);
    _recordParameterContractFrame(_localEventTransport, data);
}

// The page no longer matches what the server last sent: a patch answer did not
// fit this DOM, or the DOM changed shape while the request was out. Ask for
// the page as the saved state renders it now (a POST with no event, which runs
// no handler and keeps the state) and morph it in. The page GET is no
// substitute: it is a new page load, which mounts the view again and saves
// that. Reload only when the request cannot be made or answered.
async function _httpFallbackResync() {
    const url = window.location.href;
    _setRenderToken(null);
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': window.djust.csrfToken(),
                'X-Djust-Resync': '1',
                ...(_localEventTransport._parameterContracts?.get(_localEventTransport.primaryViewPath) != null
                    ? {'X-Djust-Parameter-Contracts': '1'} : {})
            },
            body: '{}'
        });
        if (!response.ok) throw new Error(`status ${response.status}`);
        const data = await response.json();
        if (typeof data.html !== 'string' || url !== window.location.href) throw new Error('no page');
        _receiveHttpAnswer(data);
        _noteRenderDigest(data, null);
        clientVdomVersion = null;
        await handleServerResponse(data, null, null, _localEventTransport);
        _markDomAsRendered();
    } catch (_e) {
        window.location.reload();
    }
}

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
    _readPageRenderDigest();
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
    // The socket's mount render is on the page now, not the GET's (#4108).
    _setRenderToken(null);
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

// Declared types each wire hint may produce (ADR-036 D3); a conflicting
// explicit hint is rejected rather than converted twice. `json` fits any type.
const _WIRE_HINT_TYPES = {int: 'int float Decimal', integer: 'int float Decimal',
    float: 'float', number: 'float', bool: 'bool', boolean: 'bool', array: 'list', list: 'list'};

// ADR-036 binder entry point, called before any lock, confirmation,
// disable-with, optimistic or loading effect. Returns null for a legacy or
// unlisted binding (the caller keeps its unchanged legacy params). For a strict
// handler it returns the application payload, with routing context from
// `contextElement` when given: dj-value-* arguments (strict literals) plus only
// the generated values the handler declares, or all of them for a ** catch-all
// (Q1); _target is never generated (Q2). A rejected binding is reported
// value-free (N1) and returns false: the caller must stop.
function _strictBinding(element, eventName, generated = {}, positional = [], contextElement = null) {
    try {
        const resolved = _resolveParameterContract(element, eventName);
        if (resolved.policy !== 'strict') return null;
        const parameters = resolved.contract.parameters;
        const named = new Map();
        let open = null;
        for (const p of parameters) {
            if (p.kind === 'var_keyword') open = p;
            else if (p.kind === 'positional_or_keyword' || p.kind === 'keyword_only') named.set(p.name, p);
        }
        const sent = Object.create(null);
        for (const key of Object.keys(generated)) {
            // eslint-disable-next-line security/detect-object-injection
            if (named.has(key) || open) sent[key] = generated[key];
        }
        const values = _collectStrictEventParams(element, sent, positional, (key, hint) => {
            const type = (named.get(key) || open || {type: 'Any'}).type
                .replace(/^Optional\[(.*)\]$/, '$1').replace(/^list\[.*/, 'list');
            // A dj-value-* name may not reuse any generated name, sent or not.
            return !Object.hasOwn(generated, key) && (!hint || hint === 'json' || type === 'Any' ||
                // eslint-disable-next-line security/detect-object-injection
                (Object.hasOwn(_WIRE_HINT_TYPES, hint) && _WIRE_HINT_TYPES[hint].split(' ').includes(type)));
        });
        // A value supplied both positionally and by name is an error, not a choice.
        if (parameters.filter(p => p.kind.startsWith('positional')).slice(0, positional.length)
            .some(p => Object.hasOwn(values, p.name))) throw new Error();
        if (contextElement) addEventContext(values, contextElement);
        return values;
    } catch {
        console.error('[LiveView] Event arguments rejected by the handler contract:', eventName);
        window.dispatchEvent(new CustomEvent('djust:error', {detail: {
            error: 'Invalid event arguments for this handler.',
            traceback: null, event: eventName, validation_details: null,
        }}));
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
        if (key === '_targetElement' || key === '_slotId' || key === '_optimisticUpdateId' || key === '_skipLoading' || key === '_djTargetSelector') {
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
                // The DOM changes without an answer from the server (#4108).
                _setRenderToken(null);
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
        : liveViewWS.sendEvent(eventName, paramsToSend, triggerElement, params._slotId));
    if (wsPromise) {
        await wsPromise;
        return;
    }

    // A view mounted beside the page view (lazy hydration, mount_batch, #3252)
    // lives on a WebSocket or an SSE session: the HTTP fallback posts to the
    // page's own URL, where the page view would run the event. Refuse it rather
    // than run it on the wrong view.
    const slotId = teardown ? null : (params._slotId || slotIdFor(triggerElement));
    const httpLazyOwner = triggerElement?.closest('[data-djust-lazy-id]') ||
        (slotId && slotContainer(slotId)?.matches('[data-djust-lazy-id]'));
    if (slotId && !httpLazyOwner) {
        if (!skipLoading) globalLoadingManager.stopLoading(eventName, triggerElement);
        window.dispatchEvent(new CustomEvent('djust:error', {detail: {
            error: `Event "${eventName}" was not sent: its view needs the WebSocket or SSE transport.`,
            code: 'view_unavailable',
            traceback: null,
        }}));
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
    const httpOwner = document.querySelector('[dj-root]:not(html):not(head)') || document.body;
    // The fragment never reaches the server, so an in-page #anchor jump
    // does not make this a different page (PR #3122 review).
    const pageUrl = () => window.location.href.split('#')[0];
    const httpUrl = pageUrl();
    const httpGeneration = _httpPageGeneration;
    const ownsHttpResponse = () => httpOwner === (document.querySelector('[dj-root]:not(html):not(head)') || document.body)
        && httpUrl === pageUrl() && httpGeneration === _httpPageGeneration;
    // Keepalive teardown sends are not queued: they must leave with the page.
    const previousHttpEvent = teardown ? null : _httpEventChain;
    let releaseHttpEvent = null;
    if (!teardown) {
        const settled = new Promise(resolve => { releaseHttpEvent = resolve; });
        _httpEventChain = settled;
        settled.then(() => { if (_httpEventChain === settled) _httpEventChain = null; });
    }
    try {
        if (previousHttpEvent) {
            await previousHttpEvent;
            // Navigation while queued makes this event belong to a gone page.
            if (!ownsHttpResponse()) {
                // Real navigation is quiet (the page is gone); a URL or root
                // change without one drops an event the user sent, so say so.
                if (httpGeneration === _httpPageGeneration) {
                    window.dispatchEvent(new CustomEvent('djust:error', {detail: {
                        error: `Event "${eventName}" was not sent: the page changed while it was queued.`,
                        traceback: null,
                    }}));
                }
                return;
            }
        }
        // Input, configured-name cookie, then server meta tag (00-namespace.js).
        const csrfToken = window.djust.csrfToken();
        // Read here, after the wait above: the previous answer set it.
        const sentDigest = teardown ? null : _renderTokenToSend();
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
                    ? {'X-Djust-Parameter-Contracts': '1'} : {}),
                ...(sentDigest ? {'X-Djust-Render-Digest': sentDigest} : {})
            },
            body: JSON.stringify(paramsToSend)
        });

        if (!response.ok) {
            // Report the failure like a socket error frame does (#1646
            // parity): the server's error body when it sent one.
            if (!teardown && ownsHttpResponse()) {
                let detail = {error: `HTTP error! status: ${response.status}`, traceback: null};
                try {
                    const body = await response.json();
                    if (body && typeof body.error === 'string') {
                        detail = {
                            error: body.error,
                            // Stable refusal code (#3319), e.g. 'permission_denied'.
                            code: typeof body.code === 'string' ? body.code : null,
                            transient: body.transient === true,
                            view: typeof body.view === 'string' ? body.view : (_localEventTransport.primaryViewPath || null),
                            target_id: typeof body.target_id === 'string' ? body.target_id : null,
                            traceback: body.traceback || null,
                        };
                    }
                } catch (_e) { /* a non-JSON error body keeps the status message */ }
                window.dispatchEvent(new CustomEvent('djust:error', {detail}));
            }
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
        _receiveHttpAnswer(data);
        // A patch answer is addressed by path: it is applied only if, at the
        // moment of the first mutation, the DOM still is the render this
        // request named. Otherwise none of it is applied and the page is
        // resynced (handleServerResponse's failed-patch branch).
        if (sentDigest !== null && data.version === undefined &&
                Array.isArray(data.patches) && data.patches.length > 0) {
            const shape = sentDigest.split('.')[1];
            data._applyIf = () => _domIsRender(shape) || !(data._applyIfRefused = true);
        }
        _noteRenderDigest(data, sentDigest);
        if (data.type === 'embedded_update') {
            // An embedded child's event: the server answers with the child's own
            // HTML, as the socket transports do (#3104).
            const filled = await handleEmbeddedResponse(data, _localEventTransport);
            applyHttpSideChannels(data);
            return filled;
        } else {
            // Each fallback request renders on a fresh server renderer, so a
            // whole-page answer starts its own version count (v1) and is not
            // the successor of the previous answer. Like a socket mount or an
            // html_recovery frame, it re-bases the cursor instead of being
            // checked against it. A patch answer of this transport carries no
            // version at all (#4108): the server sends one only after matching
            // the render digest this request echoed, which checks the content
            // the patches apply to, not the order of answers. A patch answer
            // that does carry a version is still checked against the cursor.
            if (typeof data.html === 'string' && data.patches === undefined && data.version !== undefined) {
                clientVdomVersion = null;
            }
            await handleServerResponse(data, eventName, triggerElement, _localEventTransport);
            _markDomAsRendered();
        }

    } catch (error) {
        if (!httpController?.signal.aborted) console.error('[LiveView] HTTP fallback failed:', error);
    } finally {
        if (httpController) _pendingHttpControllers.delete(httpController);
        if (httpRequest) cancelEventRequests(_localEventTransport, httpRequest.ref);
        if (releaseHttpEvent) releaseHttpEvent();
    }
}
window.djust.handleEvent = handleEvent;
window.djust._installPageParameterContracts = _installPageParameterContracts;
window.djust._mirrorPageParameterContracts = _mirrorPageParameterContracts;
window.djust._strictBinding = _strictBinding;
window.djust._resolveParameterContract = _resolveParameterContract;
