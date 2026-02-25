
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
        this.sseBaseUrl = `/djust/sse/${this.sessionId}/`;

        const urlParams = new URLSearchParams(params);
        urlParams.set('view', viewPath);
        const streamUrl = `${this.sseBaseUrl}?${urlParams.toString()}`;

        this.eventSource = new EventSource(streamUrl);

        this.eventSource.onmessage = (event) => {
            try {
                this.stats.received++;
                this.stats.receivedBytes += event.data.length;
                const data = JSON.parse(event.data);
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
        if (globalThis.djustDebug) console.log('[SSE] Disconnected');
    }

    /**
     * Handle a server-pushed message.
     * The message format is identical to the WebSocket protocol so that
     * 02-response-handler.js and all other message-handling modules work
     * without modification.
     */
    handleMessage(data) {
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
                            container.innerHTML = data.html; // codeql[js/xss-through-dom] -- html is server-rendered by the trusted Django/Rust template engine
                        }
                        bindLiveViewEvents();
                    }
                }
                break;

            case 'patch':
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_update':
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
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
                if (typeof dispatchPushEventToHooks === 'function') {
                    dispatchPushEventToHooks(data.event, data.payload);
                }
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

        const body = JSON.stringify({ event: eventName, params });
        this.stats.sent++;
        this.stats.sentBytes += body.length;

        fetch(`${this.sseBaseUrl}event/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
        })
            .then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            })
            .catch(err => {
                console.error('[SSE] Event POST failed:', err);
                globalLoadingManager.stopLoading(eventName, triggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
            });

        return true;
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
