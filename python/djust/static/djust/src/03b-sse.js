
// ============================================================================
// SSE (Server-Sent Events) Transport for djust LiveView
//
// Fallback transport used when WebSocket is unavailable (e.g. corporate proxies).
// Architecture:
//   Server → Client : EventSource (text/event-stream at /djust/sse/<session_id>/)
//   Client → Server : HTTP POST  to /djust/sse/<session_id>/event/
//
// Feature limitations vs WebSocket transport (documented in docs/sse-transport.md):
//   - No binary file uploads
//   - No presence tracking
//   - No actor-based state management
//   - No MessagePack binary encoding
// ============================================================================

class LiveViewSSE {
    constructor() {
        this.sessionId = null;
        this.eventSource = null;
        this.sseBaseUrl = null;
        this.enabled = true;
        this.viewMounted = false;
        this._hasConnectedBefore = false;
        this.lastEventName = null;
        this.lastTriggerElement = null;
        // Mirror LiveViewWebSocket interface for interoperability
        this.stats = { sent: 0, received: 0, sentBytes: 0, receivedBytes: 0 };
    }

    /**
     * Open an SSE stream and mount the view.
     *
     * @param {string} viewPath  Dotted Python path to the LiveView class
     * @param {Object} params    URL query params forwarded to mount()
     */
    connect(viewPath, params = {}) {
        if (!this.enabled) return;
        if (globalThis.djustDebug) console.log('[SSE] Connecting, view:', viewPath);

        // Session ID is generated client-side; the server stores it as the
        // lookup key so event POSTs can reach the right session.
        this.sessionId = this._generateSessionId();
        // Resolved via window.djust.sseUrl() — honors FORCE_SCRIPT_NAME and
        // custom mount prefixes (closes #992). Default '/djust/' preserved
        // for deployments that don't use {% djust_client_config %}.
        this.sseBaseUrl = window.djust.sseUrl
            ? window.djust.sseUrl(`sse/${this.sessionId}/`)
            : `/djust/sse/${this.sessionId}/`;

        // GET-time mount via ?view= is preserved for back-compat: clients
        // pinned to the pre-#1237 server still mount that way. New clients
        // ALSO post a mount frame on open (idempotent server-side via the
        // runtime's early-return when view_instance is already set), which
        // is the only path that includes ``url`` for URL-kwarg resolution
        // (fixes #1237 bug 1).
        const urlParams = new URLSearchParams(params);
        urlParams.set('view', viewPath);
        const streamUrl = `${this.sseBaseUrl}?${urlParams.toString()}`;

        this.eventSource = new EventSource(streamUrl);

        // Stash these so onopen can post the mount frame.
        this._pendingViewPath = viewPath;
        this._pendingMountParams = params;

        this.eventSource.onopen = () => {
            // Connection state CSS classes
            document.body.classList.add('dj-connected');
            document.body.classList.remove('dj-disconnected');

            // Track reconnections for form recovery
            if (this._hasConnectedBefore) {
                if (window.djust) window.djust._isReconnect = true;
            }
            this._hasConnectedBefore = true;

            // #1237: send a WS-shaped mount frame so the runtime can resolve
            // URL kwargs from window.location.pathname (the SSE endpoint
            // path is NOT the page URL). Idempotent server-side.
            if (this._pendingViewPath) {
                this._sendMountFrame(this._pendingViewPath, this._pendingMountParams || {});
                this._pendingViewPath = null;
                this._pendingMountParams = null;
            }
        };

        this.eventSource.onmessage = (event) => {
            try {
                this.stats.received++;
                this.stats.receivedBytes += event.data.length;
                const data = JSON.parse(event.data);
                // ``handleMessage`` is the queue-wrapper (#1098); its
                // returned promise is the chain-tail with an internal
                // ``.catch`` that already logs and swallows. The returned
                // promise never rejects, so we just ignore it.
                this.handleMessage(data);
            } catch (err) {
                console.error('[SSE] Failed to parse message:', err);
            }
        };

        this.eventSource.onerror = (_err) => {
            // EventSource auto-reconnects; we only disable on persistent failure.
            // onerror fires on every connection hiccup, so guard against noise.
            if (this.eventSource && this.eventSource.readyState === EventSource.CLOSED) {
                console.warn('[SSE] EventSource closed unexpectedly.');
                this.enabled = false;
                // Connection state CSS classes
                document.body.classList.add('dj-disconnected');
                document.body.classList.remove('dj-connected');
            }
        };
    }

    /**
     * Cleanly close the SSE stream (e.g. during TurboNav page transitions).
     */
    disconnect() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        this.viewMounted = false;
        this.sessionId = null;

        // Remove connection state CSS classes on intentional disconnect
        document.body.classList.remove('dj-connected');
        document.body.classList.remove('dj-disconnected');

        if (globalThis.djustDebug) console.log('[SSE] Disconnected');
    }

    /**
     * Public entry point — serializes rapid-fire messages.
     *
     * Each invocation chains onto the prior in-flight promise so adjacent
     * inbound SSE frames cannot interleave their internal awaits. Same
     * shape as `LiveViewWebSocket.handleMessage`. Closes #1098.
     */
    handleMessage(data) {
        const prev = this._inflight || Promise.resolve();
        const next = prev
            .then(() => this._handleMessageImpl(data))
            .catch((err) => {
                console.error('[SSE] handleMessage threw:', err);
            });
        this._inflight = next;
        return next;
    }

    /**
     * Handle a server-pushed message.
     * The message format is identical to the WebSocket protocol so that
     * 02-response-handler.js and all other message-handling modules work
     * without modification.
     */
    async _handleMessageImpl(data) {
        if (globalThis.djustDebug) console.log('[SSE] Received:', data.type, data);

        switch (data.type) {

            case 'sse_connect':
                // Session ID confirmation from server (informational only — we
                // already know our session ID since we generated it).
                if (globalThis.djustDebug) console.log('[SSE] Connection acknowledged, session:', data.session_id);
                break;

            case 'mount':
                this.viewMounted = true;
                if (globalThis.djustDebug) console.log('[SSE] View mounted:', data.view);

                // Remove dj-cloak from all elements (FOUC prevention)
                document.querySelectorAll('[dj-cloak]').forEach(el => el.removeAttribute('dj-cloak'));

                // Finish page loading bar on mount
                if (window.djust.pageLoading) window.djust.pageLoading.finish();

                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                }
                if (data.cache_config) {
                    setCacheConfig(data.cache_config);
                }

                if (data.html) {
                    let container = document.querySelector('[dj-view]');
                    if (!container) container = document.querySelector('[dj-root]');
                    if (container) {
                        const hasDataDjAttrs = data.has_ids === true;
                        if (hasDataDjAttrs) {
                            _stampDjIds(data.html);
                        } else {
                            // codeql[js/xss] -- html is server-rendered by the trusted Django/Rust template engine
                            container.innerHTML = data.html;
                        }
                        reinitAfterDOMUpdate();
                        // Set mount ready flag so dj-mounted handlers only fire
                        // for elements added by subsequent VDOM patches, not on initial load
                        window.djust._mountReady = true;
                    }
                }
                // Trigger form recovery and dj-auto-recover after reconnect mount
                if (window.djust._isReconnect) {
                    if (typeof window.djust._processFormRecovery === 'function') {
                        window.djust._processFormRecovery();
                    }
                    if (typeof window.djust._processAutoRecover === 'function') {
                        window.djust._processAutoRecover();
                    }
                }
                break;

            case 'patch':
                await handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_update':
                await handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'error':
                console.error('[SSE] Server error:', data.error);
                window.dispatchEvent(new CustomEvent('djust:error', {
                    detail: { error: data.error, traceback: data.traceback || null }
                }));
                if (this.lastEventName) {
                    globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'noop':
                if (this.lastEventName) {
                    if (!data.async_pending) {
                        globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    }
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'push_event':
                window.dispatchEvent(new CustomEvent('djust:push_event', {
                    detail: { event: data.event, payload: data.payload }
                }));
                dispatchPushEventToHooks(data.event, data.payload);
                globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                break;

            case 'navigation':
                if (window.djust.navigation) {
                    window.djust.navigation.handleNavigation(data);
                }
                break;

            case 'navigate':
                // Auth redirect (sent by server when auth check fails)
                window.location.href = data.to;
                break;

            case 'stream':
                if (window.djust.handleStreamMessage) {
                    window.djust.handleStreamMessage(data);
                }
                break;

            case 'accessibility':
                if (window.djust.accessibility) {
                    window.djust.accessibility.processAnnouncements(data.announcements);
                }
                break;

            case 'focus':
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

            case 'reload':
                window.location.reload();
                break;

            default:
                if (globalThis.djustDebug) console.log('[SSE] Unknown message type:', data.type);
        }
    }

    /**
     * Send an event to the server via HTTP POST.
     *
     * Returns true if the event was dispatched, false if not (mirrors
     * LiveViewWebSocket.sendEvent so 11-event-handler.js works unchanged).
     *
     * Since #1237, this delegates to ``sendMessage`` so every existing
     * ``liveViewWS.sendEvent(...)`` call site flows through the same
     * one POST per frame path used by mount / url_change / etc.
     *
     * @param {string}      eventName      Handler name on the LiveView
     * @param {Object}      params         Event parameters
     * @param {Element|null} triggerElement DOM element that triggered the event
     */
    sendEvent(eventName, params = {}, triggerElement = null) {
        if (!this.enabled || !this.viewMounted) {
            return false;
        }

        this.lastEventName = eventName;
        this.lastTriggerElement = triggerElement;

        return this.sendMessage({ type: 'event', event: eventName, params });
    }

    /**
     * Send a wire-shape message to the server via HTTP POST.
     *
     * #1237 parity with ``LiveViewWebSocket.sendMessage``. Every call site
     * in 18-navigation.js / 02-response-handler.js / 13-lazy-hydration.js
     * / 15-uploads.js that does ``liveViewWS.sendMessage({type, ...})``
     * works transparently when the SSE transport is active.
     *
     * Returns true on accepted dispatch, false if the transport is
     * disabled. Errors during the underlying ``fetch`` are caught and
     * reported via ``[SSE]`` console error + loading-manager teardown
     * (mirrors the legacy sendEvent error-handling).
     *
     * @param {Object} data  Wire message; must include ``type``.
     */
    sendMessage(data) {
        if (!this.enabled) return false;

        const body = JSON.stringify(data);
        this.stats.sent++;
        this.stats.sentBytes += body.length;

        const triggerElement = this.lastTriggerElement;
        const eventName = (data && data.type === 'event') ? data.event : null;

        fetch(`${this.sseBaseUrl}message/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
        })
            .then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            })
            .catch(err => {
                console.error('[SSE] Message POST failed:', err);
                if (eventName) {
                    globalLoadingManager.stopLoading(eventName, triggerElement);
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
            });

        return true;
    }

    /**
     * Send a mount frame to the server.
     *
     * #1237: introduces a WS-shaped mount frame containing
     * ``url: window.location.pathname`` so the runtime resolves URL
     * kwargs (pk, slug, ...) from the actual page URL — fixing the bug
     * where SSE-mounted views with path params received empty kwargs.
     */
    _sendMountFrame(viewPath, params = {}) {
        let tz = null;
        try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { /* noop */ }
        return this.sendMessage({
            type: 'mount',
            view: viewPath,
            params,
            url: window.location.pathname,
            has_prerendered: false,
            client_timezone: tz,
        });
    }

    /**
     * No-op: SSE keepalives are handled server-side via comment lines.
     * This method exists so callers that call liveViewWS.startHeartbeat() work
     * without branching.
     */
    startHeartbeat() {
        // SSE transport uses server-side keepalive comments — no client heartbeat needed.
    }

    // ------------------------------------------------------------------ //
    // Private helpers
    // ------------------------------------------------------------------ //

    _generateSessionId() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        // Fallback using crypto.getRandomValues() — cryptographically secure,
        // available in all modern environments (IE 11+, Node 15+).
        if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
            const buf = new Uint8Array(16);
            crypto.getRandomValues(buf);
            buf[6] = (buf[6] & 0x0f) | 0x40; // UUID version 4
            buf[8] = (buf[8] & 0x3f) | 0x80; // UUID variant bits
            const hex = Array.from(buf, b => b.toString(16).padStart(2, '0')).join('');
            return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
        }
        throw new Error('djust: Web Crypto API is required but not available in this environment.');
    }
}

// Expose to window for 14-init.js transport negotiation
window.djust.LiveViewSSE = LiveViewSSE;
