
// ============================================================================
// WebSocket LiveView Client
// ============================================================================

class LiveViewWebSocket {
    constructor() {
        this.ws = null;
        this.sessionId = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;
        this.minReconnectDelay = 500;
        this.maxReconnectDelayMs = 30000;
        this.viewMounted = false;
        this.enabled = true;  // Can be disabled to use HTTP fallback
        this._intentionalDisconnect = false;  // Set by disconnect() to suppress error overlay
        this.lastEventName = null;  // Phase 5: Track last event for loading state
        this.lastTriggerElement = null;  // Phase 5: Track trigger element for scoped loading
        // Optional callback invoked when all reconnect attempts are exhausted.
        // If set, it is called instead of _showConnectionErrorOverlay(), allowing
        // 14-init.js to switch to the SSE fallback transport.
        this.onTransportFailed = null;

        // WebSocket statistics tracking (Phase 2.1: WebSocket Inspector)
        this.stats = {
            sent: 0,           // Total messages sent
            received: 0,       // Total messages received
            sentBytes: 0,      // Total bytes sent
            receivedBytes: 0,  // Total bytes received
            reconnections: 0,  // Number of reconnections
            messages: [],      // Recent message history (last 50)
            connectedAt: null, // Timestamp of current connection
        };
    }

    /**
     * Cleanly disconnect the WebSocket for TurboNav navigation
     */
    disconnect() {
        if (globalThis.djustDebug) console.log('[LiveView] Disconnecting for navigation...');

        // Stop heartbeat
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
            if (globalThis.djustDebug) console.log('[LiveView] Heartbeat stopped');
        }

        // Mark as intentional so onclose doesn't show error overlay
        this._intentionalDisconnect = true;
        // Clear reconnect attempts so we don't auto-reconnect
        this.reconnectAttempts = this.maxReconnectAttempts;

        // Close WebSocket if open. For CONNECTING state, just null the
        // reference — calling close() on a CONNECTING socket triggers a
        // noisy "closed before established" error in the console (#732).
        if (this.ws) {
            if (this.ws.readyState === WebSocket.OPEN) {
                this.ws.close();
                if (globalThis.djustDebug) console.log('[LiveView] WebSocket closed');
            } else if (this.ws.readyState === WebSocket.CONNECTING) {
                // Let the connection attempt complete and fail silently
                // (onerror handler checks _intentionalDisconnect)
                if (globalThis.djustDebug) console.log('[LiveView] WebSocket still connecting — will close on connect');
            }
        }

        this.ws = null;
        this.sessionId = null;
        this.viewMounted = false;
        this.vdomVersion = null;
        this.stats.connectedAt = null; // Reset connection timestamp

        // Remove connection state CSS classes on intentional disconnect
        document.body.classList.remove('dj-connected');
        document.body.classList.remove('dj-disconnected');

        // Clear reconnection UI state
        document.body.removeAttribute('data-dj-reconnect-attempt');
        document.body.style.removeProperty('--dj-reconnect-attempt');
        this._removeReconnectBanner();

        // Event sequencing (#560): clear pending event state
        _pendingEventResolvers.forEach(resolve => resolve(null));
        _pendingEventRefs.clear();
        _pendingEventNames.clear();
        _pendingTriggerEls.clear();
        _pendingEventResolvers.clear();
        _tickBuffer.length = 0;
    }

    connect(url = null) {
        if (!this.enabled) return;

        // Guard: prevent duplicate connections
        if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
            if (globalThis.djustDebug) console.log('[LiveView] WebSocket already connected or connecting, skipping');
            return;
        }

        if (!url) {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;
            url = `${protocol}//${host}/ws/live/`;
        }

        if (globalThis.djustDebug) console.log('[LiveView] Connecting to WebSocket:', url);
        this.ws = new WebSocket(url);

        this.ws.onopen = (_event) => {
            if (globalThis.djustDebug) console.log('[LiveView] WebSocket connected');
            this.reconnectAttempts = 0;
            this._intentionalDisconnect = false;

            // Connection state CSS classes
            document.body.classList.add('dj-connected');
            document.body.classList.remove('dj-disconnected');

            // Clear reconnection UI state
            document.body.removeAttribute('data-dj-reconnect-attempt');
            document.body.style.removeProperty('--dj-reconnect-attempt');
            this._removeReconnectBanner();

            // Track reconnections (Phase 2.1: WebSocket Inspector)
            if (this.stats.connectedAt !== null) {
                this.stats.reconnections++;
                // Set reconnect flag for dj-auto-recover
                if (window.djust) window.djust._isReconnect = true;
                // Notify hooks of reconnection
                notifyHooksReconnected();
                // Public CustomEvent consumed by dj-track-static (v0.6.0)
                // and available to application code that wants to react
                // to reconnect without patching internal WS state.
                document.dispatchEvent(new CustomEvent('djust:ws-reconnected'));
            }
            this.stats.connectedAt = Date.now();
        };

        this.ws.onclose = (_event) => {
            if (globalThis.djustDebug) console.log('[LiveView] WebSocket disconnected');
            this.viewMounted = false;

            // Connection state CSS classes
            document.body.classList.add('dj-disconnected');
            document.body.classList.remove('dj-connected');

            // Notify hooks of disconnection
            notifyHooksDisconnected();

            // Clear all decorator state on disconnect
            // Phase 2: Debounce timers
            debounceTimers.forEach(state => {
                if (state.timerId) {
                    clearTimeout(state.timerId);
                }
            });
            debounceTimers.clear();

            // Phase 2: Throttle timers
            throttleState.forEach(state => {
                if (state.timeoutId) {
                    clearTimeout(state.timeoutId);
                }
            });
            throttleState.clear();

            // Phase 3: Optimistic updates
            optimisticUpdates.clear();
            pendingEvents.clear();

            // Event sequencing (#560): clear pending event state
            _pendingEventResolvers.forEach(resolve => resolve(null));
            _pendingEventRefs.clear();
            _pendingEventNames.clear();
            _pendingTriggerEls.clear();
            _pendingEventResolvers.clear();
            _tickBuffer.length = 0;

            // Remove loading indicators from DOM
            clearOptimisticPending();

            // Skip reconnection logic if this was an intentional disconnect (TurboNav)
            if (this._intentionalDisconnect) {
                this._intentionalDisconnect = false;
                return;
            }

            // Sticky LiveViews (Phase B): abnormal close invalidates
            // any detached sticky subtrees. The server will re-mount
            // sticky views from scratch on reconnect (new session,
            // new instances), so stash entries from the dead
            // connection cannot reattach — they'd only surface as
            // ``no-slot`` unmount events on the next navigation, or
            // worse, shadow a freshly re-mounted sticky. Clear the
            // stash before the reconnect attempt runs.
            if (window.djust && window.djust.stickyPreserve && window.djust.stickyPreserve.clearStash) {
                window.djust.stickyPreserve.clearStash();
            }

            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const baseDelay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                const cappedBase = Math.min(baseDelay, this.maxReconnectDelayMs);
                const jitteredDelay = Math.max(this.minReconnectDelay, Math.random() * cappedBase);
                if (globalThis.djustDebug) console.log('[LiveView] Reconnecting in ' + Math.round(jitteredDelay) + 'ms (attempt ' + this.reconnectAttempts + '/' + this.maxReconnectAttempts + ')...');

                // Update reconnection UI state
                document.body.setAttribute('data-dj-reconnect-attempt', String(this.reconnectAttempts));
                document.body.style.setProperty('--dj-reconnect-attempt', String(this.reconnectAttempts));
                this._showReconnectBanner(this.reconnectAttempts, this.maxReconnectAttempts);

                setTimeout(() => this.connect(url), jitteredDelay);
            } else {
                console.warn('[LiveView] Max reconnection attempts reached.');
                this.enabled = false;
                // If an SSE fallback is configured, hand off to it instead of
                // showing the connection error overlay.
                if (typeof this.onTransportFailed === 'function') {
                    if (globalThis.djustDebug) console.log('[LiveView] Invoking onTransportFailed for SSE fallback');
                    this.onTransportFailed();
                } else {
                    this._showConnectionErrorOverlay();
                }
            }
        };

        this.ws.onerror = (error) => {
            // Suppress error when disconnect() was called intentionally
            // (e.g. TurboNav navigation while WS is still connecting)
            if (this._intentionalDisconnect) return;
            console.error('[LiveView] WebSocket error:', error);
        };

        this.ws.onmessage = (event) => {
            try {
                // Track received message (Phase 2.1: WebSocket Inspector)
                const messageBytes = event.data.length;
                this.stats.received++;
                this.stats.receivedBytes += messageBytes;

                const data = JSON.parse(event.data);

                // Add to message history
                this.trackMessage({
                    direction: 'received',
                    type: data.type,
                    size: messageBytes,
                    timestamp: Date.now(),
                    data: data
                });

                // Latency simulation on receive (DEBUG_MODE only)
                const simLatency = window.DEBUG_MODE && window.djust && window.djust._simulatedLatency;
                if (simLatency > 0) {
                    const jitter = (window.djust._simulatedJitter || 0);
                    const actual = Math.max(0, simLatency + (Math.random() * 2 - 1) * simLatency * jitter);
                    // ``handleMessage`` is the queue-wrapper (#1098); its
                    // returned promise is the chain-tail with an internal
                    // ``.catch`` that already logs and swallows. The
                    // returned promise never rejects, so we just ignore it.
                    setTimeout(() => {
                        this.handleMessage(data);
                    }, actual);
                } else {
                    this.handleMessage(data);
                }
            } catch (error) {
                console.error('[LiveView] Failed to parse message:', error);
            }
        };
    }

    /**
     * Public entry point — serializes rapid-fire messages.
     *
     * Each invocation chains onto the prior in-flight promise so that
     * adjacent inbound frames cannot interleave their internal awaits.
     * Without this, two frames arriving in quick succession could each
     * start `_handleMessageImpl`, then both `await handleServerResponse`,
     * and race on shared state like ``_pendingEventRefs`` /
     * ``_tickBuffer``. Closes #1098.
     *
     * The returned promise resolves AFTER this frame's drain completes;
     * callers may ignore it. Errors propagate through `.catch()` to
     * preserve unhandled-rejection visibility.
     */
    handleMessage(data) {
        const prev = this._inflight || Promise.resolve();
        const next = prev
            .then(() => this._handleMessageImpl(data))
            .catch((err) => {
                console.error('[LiveView] handleMessage threw:', err);
            });
        this._inflight = next;
        return next;
    }

    async _handleMessageImpl(data) {
        if (globalThis.djustDebug) console.log('[LiveView] Received: %s %o', String(data.type), data);

        switch (data.type) {
            case 'connect':
                this.sessionId = data.session_id;
                if (globalThis.djustDebug) console.log('[LiveView] Session ID:', this.sessionId);
                this.autoMount();
                break;

            case 'mount':
                this.viewMounted = true;
                if (globalThis.djustDebug) console.log('[LiveView] View mounted: %s', String(data.view));

                // Fix #1 — stash server-emitted public view state so the
                // state-snapshot capture on next before-navigate has
                // something to serialize. The server includes
                // ``public_state`` only when ``enable_state_snapshot``
                // is True on the view class; non-opt-in views never
                // have state cached by the SW.
                if (data.public_state && typeof data.public_state === 'object' && data.view) {
                    if (!window.djust._clientState) window.djust._clientState = {};
                    window.djust._clientState[data.view] = data.public_state;
                }

                // Remove dj-cloak from all elements (FOUC prevention)
                document.querySelectorAll('[dj-cloak]').forEach(el => el.removeAttribute('dj-cloak'));

                // Finish page loading bar on mount
                if (window.djust.pageLoading) window.djust.pageLoading.finish();

                // Initialize VDOM version from mount response (critical for patch generation)
                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                    if (globalThis.djustDebug) console.log('[LiveView] VDOM version initialized:', clientVdomVersion);
                }

                // Initialize cache configuration from mount response
                if (data.cache_config) {
                    setCacheConfig(data.cache_config);
                }

                // Initialize optimistic UI rules from descriptor components (DEP-002)
                if (data.optimistic_rules) {
                    window.djust._optimisticRules = data.optimistic_rules;
                    if (globalThis.djustDebug) console.log('[LiveView] Optimistic rules loaded:', Object.keys(data.optimistic_rules));
                }

                // Initialize upload configurations from mount response
                if (data.upload_configs && window.djust.uploads) {
                    window.djust.uploads.setConfigs(data.upload_configs);
                }

                // OPTIMIZATION: Skip HTML replacement if content was pre-rendered via HTTP GET
                // Server sends has_ids flag to avoid client-side string search
                const hasDataDjAttrs = data.has_ids === true;
                if (this.skipMountHtml) {
                    // Content already rendered by HTTP GET - don't replace innerHTML
                    // If server HTML has dj-id attributes, stamp them onto existing DOM
                    // This preserves whitespace (e.g. in code blocks) that innerHTML would destroy
                    if (hasDataDjAttrs && data.html) {
                        if (globalThis.djustDebug) console.log('[LiveView] Stamping dj-id attributes onto pre-rendered DOM');
                        _stampDjIds(data.html); // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                    } else {
                        if (globalThis.djustDebug) console.log('[LiveView] Skipping mount HTML - using pre-rendered content');
                    }
                    this.skipMountHtml = false;
                    // Sticky LiveViews (Phase C Fix F1): the skipMountHtml
                    // branch is used when turbo-nav has pre-rendered the
                    // HTML (HTTP GET first). It was previously missing the
                    // post-mount sticky reattach — unreachable from
                    // ``live_redirect_mount`` today, but a foot-gun the
                    // moment a future path sets skipMountHtml=true on a
                    // redirect. Same no-op guard as the normal branch
                    // when stickyStash is empty.
                    if (window.djust.stickyPreserve && window.djust.stickyPreserve.reattachStickyAfterMount) {
                        window.djust.stickyPreserve.reattachStickyAfterMount();
                    }
                    // FIX #619: Defer reinitAfterDOMUpdate() to the next animation
                    // frame on pre-rendered mounts. Calling it synchronously here
                    // forces the browser to recalc layout mid-paint, which causes
                    // a visible flash on pages where the pre-rendered content has
                    // large elements (e.g. big dashboard stats) — the elements
                    // briefly render at the wrong size before settling. Deferring
                    // to the next frame lets the browser finish its current paint
                    // first. Form recovery + _mountReady must run AFTER reinit so
                    // handlers are bound when recovery fires, so the whole block
                    // moves into the rAF callback. Falls back to a synchronous call
                    // when requestAnimationFrame isn't available (JSDOM tests).
                    const runPostMount = () => {
                        reinitAfterDOMUpdate();
                        // Set mount ready flag so dj-mounted handlers only fire
                        // for elements added by subsequent VDOM patches, not on initial load
                        window.djust._mountReady = true;
                        // Trigger form recovery and dj-auto-recover after reconnect mount
                        if (window.djust._isReconnect) {
                            if (typeof window.djust._processFormRecovery === 'function') {
                                window.djust._processFormRecovery();
                            }
                            if (typeof window.djust._processAutoRecover === 'function') {
                                window.djust._processAutoRecover();
                            }
                        }
                    };
                    if (typeof requestAnimationFrame === 'function') {
                        requestAnimationFrame(runPostMount);
                    } else {
                        runPostMount();
                    }
                } else if (data.html) {
                    // Fix #3 — cache the mount HTML for fast back-nav
                    // paint. Non-invasive: only runs when the SW bridge
                    // is present and we actually have HTML from the
                    // server (skipped when the client used pre-rendered
                    // HTTP content).
                    try {
                        if (window.djust && window.djust._sw && typeof window.djust._sw.cacheVdom === 'function') {
                            const cacheUrl = (typeof window !== 'undefined' && window.location)
                                ? window.location.pathname
                                : '/';
                            window.djust._sw.cacheVdom(cacheUrl, data.html, typeof data.version === 'number' ? data.version : 0);
                        }
                    } catch (_e) {
                        // #1030 — best-effort cache write; only log under
                        // djustDebug so production console stays quiet but
                        // developers can diagnose cache misses.
                        if (globalThis.djustDebug) console.log('[LiveView] cache-write failed:', _e);
                    }
                    // No pre-rendered content - use server HTML directly
                    if (hasDataDjAttrs) {
                        if (globalThis.djustDebug) console.log('[LiveView] Hydrating DOM with dj-id attributes for reliable patching');
                    }
                    // Sticky LiveViews (Phase B): select a container that
                    // is NOT a sticky subtree root. When the old view is
                    // still in the DOM just before its innerHTML is
                    // replaced, a naive ``[dj-view]`` pick could return
                    // the sticky itself. ``[dj-sticky-root]`` is set by
                    // the server template tag on sticky wrappers —
                    // exclude them from container selection.
                    let container = document.querySelector('[dj-view]:not([dj-sticky-root])');
                    if (!container) {
                        container = document.querySelector('[dj-root]');
                    }
                    if (container) {
                        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                        container.innerHTML = data.html;
                        // Post-mount sticky reattach (Phase B). No-op
                        // when stickyStash is empty.
                        if (window.djust.stickyPreserve && window.djust.stickyPreserve.reattachStickyAfterMount) {
                            window.djust.stickyPreserve.reattachStickyAfterMount();
                        }
                        reinitAfterDOMUpdate();
                    }
                    this.skipMountHtml = false;
                    // Set mount ready flag so dj-mounted handlers only fire
                    // for elements added by subsequent VDOM patches, not on initial load
                    window.djust._mountReady = true;
                    // Trigger form recovery and dj-auto-recover after reconnect mount
                    if (window.djust._isReconnect) {
                        if (typeof window.djust._processFormRecovery === 'function') {
                            window.djust._processFormRecovery();
                        }
                        if (typeof window.djust._processAutoRecover === 'function') {
                            window.djust._processAutoRecover();
                        }
                    }
                }
                break;

            case 'mount_batch': {
                // Mount-batch response (v0.6.0) — carries N per-view payloads.
                // Apply each to [data-djust-target="<target_id>"] within a
                // [dj-view] element, track per-target VDOM versions, and run
                // reinitAfterDOMUpdate() ONCE after the batch is applied.
                this.viewMounted = true;
                // #1031: clear the lazy-hydration in-flight batch so a late
                // arriving error frame doesn't trigger a phantom fallback.
                if (window.djust && window.djust.lazyHydration) {
                    window.djust.lazyHydration.inFlightBatch = null;
                }
                const views = Array.isArray(data.views) ? data.views : [];
                const failed = Array.isArray(data.failed) ? data.failed : [];
                if (!window.djust._clientVdomVersions) {
                    window.djust._clientVdomVersions = {};
                }
                for (const entry of views) {
                    const targetId = entry && entry.target_id;
                    if (!targetId) continue;
                    const container = document.querySelector(
                        '[dj-view][data-djust-target="' + CSS.escape(String(targetId)) + '"]'
                    );
                    if (!container) {
                        if (globalThis.djustDebug) {
                            console.warn('[LiveView] mount_batch: target not found: %s', String(targetId));
                        }
                        continue;
                    }
                    if (typeof entry.html === 'string') {
                        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                        container.innerHTML = entry.html;
                    }
                    if (typeof entry.version === 'number') {
                        window.djust._clientVdomVersions[targetId] = entry.version;
                    }
                }
                for (const f of failed) {
                    if (globalThis.djustDebug) {
                        console.warn('[LiveView] mount_batch failed: %s %o', String(f.view || ''), f);
                    }
                }
                // Fix #4 — forward any navigate entries emitted by
                // on_mount redirect hooks to the navigation dispatcher.
                const navigates = Array.isArray(data.navigate) ? data.navigate : [];
                for (const nav of navigates) {
                    if (!nav || typeof nav.to !== 'string') continue;
                    if (window.djust.navigation && typeof window.djust.navigation.handleNavigation === 'function') {
                        window.djust.navigation.handleNavigation({
                            type: 'navigation',
                            action: 'live_redirect',
                            path: nav.to,
                        });
                    } else if (typeof window !== 'undefined' && window.location) {
                        // Fallback — hard navigation if the dispatcher
                        // isn't wired (non-LiveView pages).
                        // Same-origin guard: server-controlled `nav.to`
                        // is generally trusted but defense-in-depth
                        // prevents an attacker who breaches the wire
                        // protocol from pivoting to open-redirect /
                        // javascript: scheme XSS. Only allow same-origin
                        // absolute paths; reject protocol-relative
                        // (`//evil.com`), absolute URLs to other origins,
                        // and `javascript:` / `data:` schemes.
                        // Closes CodeQL js/client-side-unvalidated-url-redirection.
                        const target = nav.to;
                        const isSameOriginPath = (
                            target.length > 0 &&
                            target.charAt(0) === '/' &&
                            target.charAt(1) !== '/'  // reject protocol-relative
                        );
                        if (isSameOriginPath) {
                            window.location.href = target;
                        } else if (globalThis.djustDebug) {
                            console.warn(
                                '[djust] live_redirect rejected non-same-origin target:',
                                target,
                            );
                        }
                    }
                }
                if (views.length > 0) {
                    reinitAfterDOMUpdate();
                    window.djust._mountReady = true;
                }
                break;
            }

            case 'patch':
            case 'html_update': {
                // Event sequencing (#560): if this is a server-initiated update
                // (tick, broadcast, async) and we have pending user events,
                // buffer it and apply after the event responses arrive. This
                // prevents server pushes from consuming event loading state
                // and ensures version numbers stay sequential.
                const isServerInitiated = (
                    data.source === 'tick' ||
                    data.source === 'broadcast' ||
                    data.source === 'async'
                );
                const isEventResponse = (
                    data.ref != null && _pendingEventRefs.has(data.ref)
                );

                if (!isEventResponse && isServerInitiated && _pendingEventRefs.size > 0) {
                    // Buffer server-initiated patch — will be applied after
                    // all pending event responses arrive.
                    _tickBuffer.push(data);
                    if (globalThis.djustDebug) {
                        djLog('[LiveView] Buffered %s patch (v%s) — waiting for %d pending event(s)', String(data.source), String(data.version), _pendingEventRefs.size);
                    }
                    break;
                }

                // Determine event name and trigger for loading state
                let evName = this.lastEventName;
                let evTrigger = this.lastTriggerElement;

                if (isEventResponse) {
                    // This response matches a pending event — use tracked
                    // event name/trigger and remove from pending set.
                    evName = _pendingEventNames.get(data.ref) || this.lastEventName;
                    evTrigger = _pendingTriggerEls.get(data.ref) || this.lastTriggerElement;
                    _pendingEventRefs.delete(data.ref);
                    _pendingEventNames.delete(data.ref);
                    _pendingTriggerEls.delete(data.ref);
                    // #1315: Resolve the sendEvent Promise so callers awaiting
                    // the server response (e.g. _handleDjSubmit) can proceed.
                    const resolver = _pendingEventResolvers.get(data.ref);
                    if (resolver) {
                        _pendingEventResolvers.delete(data.ref);
                        resolver(data);
                    }
                } else if (isServerInitiated) {
                    // Server-initiated patch with no pending events — apply
                    // without consuming event loading state.
                    evName = null;
                    evTrigger = null;
                }

                await handleServerResponse(data, evName, evTrigger);

                if (!isServerInitiated) {
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }

                // After processing the event response, flush buffered
                // patches only when ALL pending events have resolved.
                if (isEventResponse && _pendingEventRefs.size === 0 && _tickBuffer.length > 0) {
                    if (globalThis.djustDebug) {
                        djLog('[LiveView] Flushing ' + _tickBuffer.length + ' buffered patches');
                    }
                    const buffered = _tickBuffer.splice(0);
                    for (const tickData of buffered) {
                        await handleServerResponse(tickData, null, null);
                    }
                }
                break;
            }

            case 'html_recovery': {
                // Server response to request_html — morph DOM with recovered HTML.
                // Bypasses normal version tracking to avoid mismatch loops.
                const parser = new DOMParser();
                // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                const doc = parser.parseFromString(data.html, 'text/html');
                const liveviewRoot = getLiveViewRoot();
                if (!liveviewRoot) {
                    window.location.reload();
                    break;
                }
                const newRoot = doc.querySelector('[dj-root]') || doc.body;
                morphChildren(liveviewRoot, newRoot);
                clientVdomVersion = data.version;
                reinitAfterDOMUpdate();
                if (globalThis.djustDebug) {
                    // codeql[js/log-injection] -- data.version is a server-controlled integer
                    djLog('[LiveView] DOM recovered via morph, version: %s', String(data.version));
                }
                break;
            }

            case 'error':
                // codeql[js/log-injection] -- data.error and data.traceback are server-provided error messages, not user input
                console.error('[LiveView] Server error:', data.error);
                if (data.traceback) {
                    // codeql[js/log-injection] -- data.traceback is a server-provided stack trace, not user input
                    console.error('Traceback:', data.traceback);
                }

                // #1031: if the error is about mount_batch (older server
                // doesn't recognize the frame type), fall back to per-view
                // mounts so the lazy-hydrated views still come up.
                if (
                    typeof data.error === 'string'
                    && /mount_batch|unknown\s+message\s+type/i.test(data.error)
                    && window.djust && window.djust.lazyHydration
                    && typeof window.djust.lazyHydration.handleMountBatchFallback === 'function'
                ) {
                    window.djust.lazyHydration.handleMountBatchFallback();
                    break;
                }

                // Non-recoverable errors (e.g. server restart lost state) — auto-reload
                if (data.recoverable === false) {
                    console.warn('[LiveView] Non-recoverable error, reloading page...');
                    window.location.reload();
                    break;
                }

                // Dispatch event for dev tools (debug panel, toasts)
                window.dispatchEvent(new CustomEvent('djust:error', {
                    detail: {
                        error: data.error,
                        traceback: data.traceback || null,
                        event: data.event || this.lastEventName || null,
                        validation_details: data.validation_details || null
                    }
                }));

                // Clear pending event refs (#560)
                _pendingEventResolvers.forEach(resolve => resolve(null));
                _pendingEventRefs.clear();
                _pendingEventNames.clear();
                _pendingTriggerEls.clear();
                _pendingEventResolvers.clear();
                _tickBuffer.length = 0;

                // Phase 5: Stop loading state on error
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'pong':
                // Heartbeat response
                break;

            case 'upload_progress':
                // File upload progress update
                if (window.djust.uploads) {
                    window.djust.uploads.handleProgress(data);
                }
                break;

            case 'upload_registered':
                // Upload registration acknowledged
                // codeql[js/log-injection] -- data.ref and data.upload_name are server-assigned upload identifiers
                if (globalThis.djustDebug) console.log('[Upload] Registered: %s for %s', String(data.ref), String(data.upload_name));
                break;

            case 'upload_resumed':
                // Resumable upload — server replied to upload_resume
                // with the last accepted offset. Dispatched to the
                // pending resume promise inside 15-uploads.js. (#821)
                if (window.djust.uploads && window.djust.uploads.handleResumed) {
                    window.djust.uploads.handleResumed(data);
                }
                break;

            case 'stream':
                // Streaming partial DOM updates (LLM chat, live feeds)
                if (window.djust.handleStreamMessage) {
                    window.djust.handleStreamMessage(data);
                }
                break;

            case 'noop': {
                // Server acknowledged event but no DOM changes needed (auto-detected
                // or explicit _skip_render). Clear loading state unless async pending.
                const noopEvName = (data.ref != null ? _pendingEventNames.get(data.ref) : null)
                    || this.lastEventName;
                const noopTrigger = (data.ref != null ? _pendingTriggerEls.get(data.ref) : null)
                    || this.lastTriggerElement;

                // Clear pending event ref (#560)
                if (data.ref != null && _pendingEventRefs.has(data.ref)) {
                    _pendingEventRefs.delete(data.ref);
                    _pendingEventNames.delete(data.ref);
                    _pendingTriggerEls.delete(data.ref);
                    // #1315: Resolve the sendEvent Promise on noop too.
                    const resolver = _pendingEventResolvers.get(data.ref);
                    if (resolver) {
                        _pendingEventResolvers.delete(data.ref);
                        resolver(data);
                    }
                }

                if (noopEvName) {
                    if (!data.async_pending) {
                        globalLoadingManager.stopLoading(noopEvName, noopTrigger);
                    } else {
                        if (globalThis.djustDebug) console.log('[LiveView] Keeping loading state — async work pending');
                    }
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }

                // Flush buffered patches only when all pending events resolved
                if (_pendingEventRefs.size === 0 && _tickBuffer.length > 0) {
                    const buffered = _tickBuffer.splice(0);
                    for (const tickData of buffered) {
                        await handleServerResponse(tickData, null, null);
                    }
                }
                break;
            }

            case 'push_event':
                // Server-pushed event for JS hooks
                window.dispatchEvent(new CustomEvent('djust:push_event', {
                    detail: { event: data.event, payload: data.payload }
                }));
                // Dispatch to dj-hook instances that registered via handleEvent
                dispatchPushEventToHooks(data.event, data.payload);
                // Clear loading state — when _skip_render is used, this is the
                // only response the client gets (no patch/html_update follows).
                globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                break;

            case 'embedded_update':
                // Scoped HTML update for an embedded child LiveView
                this.handleEmbeddedUpdate(data);
                // Stop loading state
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'child_update':
                // Sticky LiveViews Phase A: VDOM patch frame targeted at
                // a specific child view via view_id. Routed to 45-child-view.js.
                if (window.djust.childView && window.djust.childView.handleChildUpdate) {
                    await window.djust.childView.handleChildUpdate(data);
                }
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'sticky_hold':
                // Sticky LiveViews Phase B: authoritative list of
                // preserved sticky view_ids. Emitted BEFORE the
                // companion ``mount`` frame so the client can drop
                // stash entries the server is NOT preserving
                // (permission revoked, no matching slot, etc.) before
                // the new HTML is applied.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.reconcileStickyHold) {
                    window.djust.stickyPreserve.reconcileStickyHold(data.views || []);
                }
                break;

            case 'sticky_update':
                // Sticky LiveViews Phase B: VDOM patch frame scoped to
                // a sticky subtree. Applies via the new
                // ``applyPatches(patches, rootEl)`` variant — the
                // sticky's dj-id namespace is independent of the
                // parent's, so doc-wide lookups would be incorrect.
                if (window.djust.stickyPreserve && window.djust.stickyPreserve.handleStickyUpdate) {
                    await window.djust.stickyPreserve.handleStickyUpdate(data);
                }
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'rate_limit_exceeded':
                // Server is dropping events due to rate limiting — show brief warning, do NOT retry
                // codeql[js/log-injection] -- data.message is a server-controlled rate limit message
                console.warn('[LiveView] Rate limited:', data.message || 'Too many events');
                this._showRateLimitWarning();
                // Stop loading state if applicable
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'navigation':
                // Server-side live_patch or live_redirect
                if (window.djust.navigation) {
                    window.djust.navigation.handleNavigation(data);
                }
                break;

            case 'accessibility':
                // Screen reader announcements from server
                if (window.djust.accessibility) {
                    window.djust.accessibility.processAnnouncements(data.announcements);
                }
                break;

            case 'focus':
                // Focus command from server
                if (window.djust.accessibility) {
                    window.djust.accessibility.processFocus([data.selector, data.options]);
                }
                break;

            case 'flash':
                // Flash message from server (put_flash / clear_flash)
                if (window.djust.flash) {
                    window.djust.flash.handleFlash(data);
                }
                break;

            case 'page_metadata':
                // Page metadata from server (page_title / page_meta)
                if (window.djust.pageMetadata) {
                    window.djust.pageMetadata.handlePageMetadata(data);
                }
                break;

            case 'layout':
                // Runtime layout switch (v0.6.0) — set_layout() on the server.
                if (window.djust.djLayout && typeof window.djust.djLayout.applyLayout === 'function') {
                    window.djust.djLayout.applyLayout(data);
                }
                break;

            case 'reload':
                // Hot reload: file changed, refresh the page
                window.location.reload();
                break;

            case 'hvr-applied':
                // Hot View Replacement (v0.6.1) — server swapped __class__
                // on the live view instance without losing state. Surface
                // the event so tests/app code can observe it, then show
                // the dev indicator toast.
                try {
                    document.dispatchEvent(new CustomEvent('djust:hvr-applied', {
                        detail: data,
                        bubbles: true,
                    }));
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] hvr-applied dispatch failed', e);
                    }
                }
                if (globalThis.djust && globalThis.djust.hvr) {
                    globalThis.djust.hvr.showIndicator(data);
                }
                break;
            case 'time_travel_state':
                // Time-travel debugging (v0.6.1) — server acknowledges
                // a completed jump with the new cursor/history_len.
                // The debug panel listens for this event to update its
                // timeline UI; the main client just fans out so non-
                // debug-panel consumers can observe.
                try {
                    document.dispatchEvent(new CustomEvent('djust:time-travel-state', {
                        detail: data,
                        bubbles: true,
                    }));
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel-state dispatch failed', e);
                    }
                }
                break;
            case 'time_travel_event':
                // Time-travel debugging (v0.6.1 Fix #3) — server pushes
                // each recorded snapshot so the debug panel's timeline
                // populates incrementally. Payload shape:
                //   { type: 'time_travel_event', entry: {...}, history_len: N }
                // Fanned out as a CustomEvent so the debug-panel (and
                // any other listener) can append the entry.
                try {
                    document.dispatchEvent(new CustomEvent('djust:time-travel-event', {
                        detail: data,
                        bubbles: true,
                    }));
                } catch (e) {
                    if (globalThis.djustDebug) {
                        console.warn('[djust] time-travel-event dispatch failed', e);
                    }
                }
                break;
        }
    }

    mount(viewPath, params = {}) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        if (globalThis.djustDebug) console.log('[LiveView] Mounting view:', viewPath);
        // Detect browser timezone for server-side local time rendering
        let clientTimezone = null;
        try {
            clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch (e) {
            console.warn('[LiveView] Could not detect browser timezone:', e);
        }

        this.sendMessage({
            type: 'mount',
            view: viewPath,
            params: params,
            url: window.location.pathname,
            has_prerendered: this.skipMountHtml || false,  // Tell server we have pre-rendered content
            client_timezone: clientTimezone  // IANA timezone string (e.g. "America/New_York")
        });
        return true;
    }

    /**
     * Track a WebSocket message in the history (Phase 2.1: WebSocket Inspector)
     * @param {Object} message - Message metadata
     */
    trackMessage(message) {
        this.stats.messages.unshift(message);
        // Keep only last 50 messages
        if (this.stats.messages.length > 50) {
            this.stats.messages = this.stats.messages.slice(0, 50);
        }
    }

    /**
     * Send a message via WebSocket with tracking (Phase 2.1: WebSocket Inspector)
     * @param {Object} data - Data to send (will be JSON stringified)
     */
    sendMessage(data) {
        const message = JSON.stringify(data);
        const messageBytes = message.length;

        // Track sent message
        this.stats.sent++;
        this.stats.sentBytes += messageBytes;

        // Add to message history
        this.trackMessage({
            direction: 'sent',
            type: data.type,
            size: messageBytes,
            timestamp: Date.now(),
            data: data
        });

        // Latency simulation (DEBUG_MODE only)
        const simLatency = window.DEBUG_MODE && window.djust && window.djust._simulatedLatency;
        if (simLatency > 0) {
            const jitter = (window.djust._simulatedJitter || 0);
            const actual = Math.max(0, simLatency + (Math.random() * 2 - 1) * simLatency * jitter);
            setTimeout(() => this.ws.send(message), actual);
        } else {
            // Send the message
            this.ws.send(message);
        }
    }

    autoMount() {
        // Look for container with view path
        let container = document.querySelector('[dj-view]');
        if (!container) {
            // Fallback: look for dj-root with dj-view attribute
            container = document.querySelector('[dj-root][dj-view]');
        }

        if (container) {
            const viewPath = container.getAttribute('dj-view');
            if (viewPath) {
                // OPTIMIZATION: Check if content was already rendered by HTTP GET
                // We still send mount message (server needs to initialize session),
                // but we'll skip applying the HTML response
                const hasContent = container.innerHTML && container.innerHTML.trim().length > 0;

                if (hasContent) {
                    if (globalThis.djustDebug) console.log('[LiveView] Content pre-rendered via HTTP - will skip HTML in mount response');
                    this.skipMountHtml = true;
                }

                // Always send mount message to initialize server-side session
                // Pass URL query params so server mount can read filters (e.g., ?sender=80)
                const urlParams = Object.fromEntries(new URLSearchParams(window.location.search));
                this.mount(viewPath, urlParams);
            } else {
                console.warn('[LiveView] Container found but no view path specified');
            }
        } else {
            console.warn('[LiveView] No LiveView container found for auto-mounting');
        }
    }

    sendEvent(eventName, params = {}, triggerElement = null) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        if (!this.viewMounted) {
            console.warn('[LiveView] View not mounted. Event ignored:', eventName);
            return false;
        }

        // Phase 5: Track event name and trigger element for loading state
        this.lastEventName = eventName;
        this.lastTriggerElement = triggerElement;

        // Event sequencing (#560): assign monotonic ref so we can match
        // the server's response to this specific event and distinguish
        // it from server-initiated patches. Uses Set to track multiple
        // concurrent pending events.
        const ref = ++_eventRefCounter;
        _pendingEventRefs.add(ref);
        _pendingEventNames.set(ref, eventName);
        _pendingTriggerEls.set(ref, triggerElement);

        // #1315: Return a Promise so callers can await the server response
        // before running post-response logic (e.g. _setFormPending(false)).
        // Without this, fire-and-forget WS dispatch causes handleEvent to
        // resolve synchronously, and form-pending toggles off before any
        // browser repaint.
        return new Promise((resolve) => {
            _pendingEventResolvers.set(ref, resolve);
            this.sendMessage({
                type: 'event',
                event: eventName,
                params: params,
                ref: ref
            });
        });
    }

    // Removed duplicate applyPatches and patch helper methods
    // Now using centralized handleServerResponse() -> applyPatches()

    /**
     * Handle scoped HTML update for an embedded child LiveView.
     * Replaces only the innerHTML of the embedded view's container div.
     */
    handleEmbeddedUpdate(data) {
        const viewId = data.view_id;
        const html = data.html;
        if (!viewId || html === undefined) {
            // codeql[js/log-injection] -- data is a server WebSocket message, not user input
            console.warn('[LiveView] Invalid embedded_update message:', data);
            return;
        }

        const container = document.querySelector(`[data-djust-embedded="${CSS.escape(viewId)}"]`);
        if (!container) {
            console.warn('[LiveView] Embedded view container not found: %s', String(viewId));
            return;
        }

        const _morphTemp = document.createElement('div');
        // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
        _morphTemp.innerHTML = html;
        morphChildren(container, _morphTemp);
        if (globalThis.djustDebug) console.log('[LiveView] Updated embedded view: %s', String(viewId));

        // Re-bind events within the updated container
        reinitAfterDOMUpdate();
    }

    _showReconnectBanner(attempt, maxAttempts) {
        let banner = document.getElementById('dj-reconnecting-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'dj-reconnecting-banner';
            banner.className = 'dj-reconnecting-banner';
            banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99998;background:#f59e0b;color:#1c1917;text-align:center;padding:6px 16px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;font-weight:600;';
            document.body.appendChild(banner);
        }
        banner.textContent = 'Reconnecting\u2026 (attempt ' + attempt + ' of ' + maxAttempts + ')';
    }

    _removeReconnectBanner() {
        const banner = document.getElementById('dj-reconnecting-banner');
        if (banner) banner.remove();
    }

    _showConnectionErrorOverlay() {
        // Only show in DEBUG mode
        if (!window.DEBUG_MODE) return;

        // Don't duplicate
        if (document.getElementById('djust-connection-error-overlay')) return;

        const overlay = document.createElement('div');
        overlay.id = 'djust-connection-error-overlay';
        overlay.style.cssText = `
            position: fixed; bottom: 0; left: 0; right: 0; z-index: 99999;
            background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%);
            border-top: 3px solid #ef4444; padding: 20px 24px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            color: #e2e8f0; font-size: 14px; box-shadow: 0 -4px 20px rgba(0,0,0,0.4);
        `;
        overlay.innerHTML = `
            <div style="display:flex; align-items:flex-start; gap:16px; max-width:900px; margin:0 auto;">
                <span style="font-size:24px; line-height:1;">❌</span>
                <div style="flex:1;">
                    <div style="font-weight:700; font-size:16px; margin-bottom:8px; color:#fca5a5;">
                        WebSocket Connection Failed
                    </div>
                    <div style="margin-bottom:10px; color:#cbd5e1; line-height:1.6;">
                        djust could not establish a WebSocket connection. Common causes:
                    </div>
                    <ul style="margin:0 0 12px 18px; padding:0; color:#94a3b8; line-height:1.8;">
                        <li>ASGI server not running (need <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">daphne</code> or <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">uvicorn</code>, not <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">manage.py runserver</code>)</li>
                        <li>If using daphne, wrap HTTP handler with <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">ASGIStaticFilesHandler</code> or use <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">djust.asgi.get_application()</code></li>
                        <li>WebSocket route not configured (need <code style="background:#1e293b;padding:2px 6px;border-radius:3px;color:#a5f3fc;">/ws/live/</code> path)</li>
                    </ul>
                    <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                        <code style="background:#1e293b; padding:6px 12px; border-radius:4px; color:#86efac; font-size:13px;">
                            python manage.py djust_check
                        </code>
                        <span style="color:#64748b; font-size:12px;">← Run this to diagnose configuration issues</span>
                    </div>
                </div>
                <button onclick="this.parentElement.parentElement.remove()" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:20px;padding:4px;">✕</button>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    _showRateLimitWarning() {
        // Show a brief non-intrusive toast; debounce so rapid limits don't spam
        if (this._rateLimitToast) return;
        const toast = document.createElement('div');
        toast.textContent = 'Slow down — some actions were dropped';
        toast.style.cssText = `
            position: fixed; bottom: 20px; right: 20px; z-index: 99999;
            background: #f59e0b; color: #1c1917; padding: 10px 18px;
            border-radius: 8px; font-size: 13px; font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2); opacity: 0;
            transition: opacity 0.3s; pointer-events: none;
        `;
        document.body.appendChild(toast);
        this._rateLimitToast = toast;
        requestAnimationFrame(() => { toast.style.opacity = '1'; });
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => { toast.remove(); this._rateLimitToast = null; }, 300);
        }, 2500);
    }

    startHeartbeat(interval = 30000) {
        // Guard: prevent multiple heartbeat intervals
        if (this.heartbeatInterval) {
            if (globalThis.djustDebug) console.log('[LiveView] Heartbeat already running, skipping duplicate');
            return;
        }

        this.heartbeatInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.sendMessage({ type: 'ping' });
            }
        }, interval);
        if (globalThis.djustDebug) console.log('[LiveView] Heartbeat started (interval:', interval, 'ms)');
    }
}

// Expose LiveViewWebSocket to window for client-dev.js to wrap
window.djust.LiveViewWebSocket = LiveViewWebSocket;
// Backward compatibility
window.LiveViewWebSocket = LiveViewWebSocket;

// Global WebSocket instance
let liveViewWS = null;
