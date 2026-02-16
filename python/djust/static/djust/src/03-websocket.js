
// ============================================================================
// WebSocket LiveView Client
// ============================================================================

class LiveViewWebSocket {
    constructor() {
        this.ws = null;
        this.sessionId = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.viewMounted = false;
        this.enabled = true;  // Can be disabled to use HTTP fallback
        this._intentionalDisconnect = false;  // Set by disconnect() to suppress error overlay
        this.lastEventName = null;  // Phase 5: Track last event for loading state
        this.lastTriggerElement = null;  // Phase 5: Track trigger element for scoped loading

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
        console.log('[LiveView] Disconnecting for navigation...');

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

        // Close WebSocket if open or connecting
        if (this.ws) {
            if (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN) {
                this.ws.close();
                if (globalThis.djustDebug) console.log('[LiveView] WebSocket closed');
            }
        }

        this.ws = null;
        this.sessionId = null;
        this.viewMounted = false;
        this.vdomVersion = null;
        this.stats.connectedAt = null; // Reset connection timestamp
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

        console.log('[LiveView] Connecting to WebSocket:', url);
        this.ws = new WebSocket(url);

        this.ws.onopen = (_event) => {
            console.log('[LiveView] WebSocket connected');
            this.reconnectAttempts = 0;
            this._intentionalDisconnect = false;

            // Track reconnections (Phase 2.1: WebSocket Inspector)
            if (this.stats.connectedAt !== null) {
                this.stats.reconnections++;
                // Notify hooks of reconnection
                if (typeof notifyHooksReconnected === 'function') notifyHooksReconnected();
            }
            this.stats.connectedAt = Date.now();
        };

        this.ws.onclose = (_event) => {
            console.log('[LiveView] WebSocket disconnected');
            this.viewMounted = false;

            // Notify hooks of disconnection
            if (typeof notifyHooksDisconnected === 'function') notifyHooksDisconnected();

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

            // Remove loading indicators from DOM
            document.querySelectorAll('.optimistic-pending').forEach(el => {
                el.classList.remove('optimistic-pending');
            });

            // Skip reconnection logic if this was an intentional disconnect (TurboNav)
            if (this._intentionalDisconnect) {
                this._intentionalDisconnect = false;
                return;
            }

            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                console.log(`[LiveView] Reconnecting in ${delay}ms...`);
                setTimeout(() => this.connect(url), delay);
            } else {
                console.warn('[LiveView] Max reconnection attempts reached. Falling back to HTTP mode.');
                this.enabled = false;
                this._showConnectionErrorOverlay();
            }
        };

        this.ws.onerror = (error) => {
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

                this.handleMessage(data);
            } catch (error) {
                console.error('[LiveView] Failed to parse message:', error);
            }
        };
    }

    handleMessage(data) {
        console.log('[LiveView] Received:', data.type, data);

        switch (data.type) {
            case 'connect':
                this.sessionId = data.session_id;
                console.log('[LiveView] Session ID:', this.sessionId);
                this.autoMount();
                break;

            case 'mount':
                this.viewMounted = true;
                console.log('[LiveView] View mounted:', data.view);

                // Initialize VDOM version from mount response (critical for patch generation)
                if (data.version !== undefined) {
                    clientVdomVersion = data.version;
                    console.log('[LiveView] VDOM version initialized:', clientVdomVersion);
                }

                // Initialize cache configuration from mount response
                if (data.cache_config) {
                    setCacheConfig(data.cache_config);
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
                    // If server HTML has data-dj-id attributes, stamp them onto existing DOM
                    // This preserves whitespace (e.g. in code blocks) that innerHTML would destroy
                    if (hasDataDjAttrs && data.html) {
                        console.log('[LiveView] Stamping data-dj-id attributes onto pre-rendered DOM');
                        _stampDjIds(data.html);
                    } else {
                        console.log('[LiveView] Skipping mount HTML - using pre-rendered content');
                    }
                    this.skipMountHtml = false;
                    bindLiveViewEvents();
                } else if (data.html) {
                    // No pre-rendered content - use server HTML directly
                    if (hasDataDjAttrs) {
                        console.log('[LiveView] Hydrating DOM with data-dj-id attributes for reliable patching');
                    }
                    let container = document.querySelector('[dj-view]');
                    if (!container) {
                        container = document.querySelector('[dj-root]');
                    }
                    if (container) {
                        container.innerHTML = data.html;
                        bindLiveViewEvents();
                    }
                    this.skipMountHtml = false;
                }
                break;

            case 'patch':
                // Use centralized response handler
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_update':
                // Use centralized response handler
                handleServerResponse(data, this.lastEventName, this.lastTriggerElement);
                this.lastEventName = null;
                this.lastTriggerElement = null;
                break;

            case 'html_recovery': {
                // Server response to request_html — morph DOM with recovered HTML.
                // Bypasses normal version tracking to avoid mismatch loops.
                const parser = new DOMParser();
                const doc = parser.parseFromString(data.html, 'text/html');
                const liveviewRoot = getLiveViewRoot();
                if (!liveviewRoot) {
                    window.location.reload();
                    break;
                }
                const newRoot = doc.querySelector('[dj-root]') || doc.body;
                morphChildren(liveviewRoot, newRoot);
                clientVdomVersion = data.version;
                initReactCounters();
                initTodoItems();
                bindLiveViewEvents();
                if (globalThis.djustDebug) {
                    console.log('[LiveView] DOM recovered via morph, version:', data.version);
                }
                break;
            }

            case 'error':
                console.error('[LiveView] Server error:', data.error);
                if (data.traceback) {
                    console.error('Traceback:', data.traceback);
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
                if (globalThis.djustDebug) console.log('[Upload] Registered:', data.ref, 'for', data.upload_name);
                break;

            case 'stream':
                // Streaming partial DOM updates (LLM chat, live feeds)
                if (window.djust.handleStreamMessage) {
                    window.djust.handleStreamMessage(data);
                }
                break;

            case 'noop':
                // Server acknowledged event but no DOM changes needed (auto-detected
                // or explicit _skip_render). Clear loading state unless async pending.
                if (this.lastEventName) {
                    if (!data.async_pending) {
                        globalLoadingManager.stopLoading(this.lastEventName, this.lastTriggerElement);
                    } else if (globalThis.djustDebug) {
                        console.log('[LiveView] Keeping loading state — async work pending');
                    }
                    this.lastEventName = null;
                    this.lastTriggerElement = null;
                }
                break;

            case 'push_event':
                // Server-pushed event for JS hooks
                window.dispatchEvent(new CustomEvent('djust:push_event', {
                    detail: { event: data.event, payload: data.payload }
                }));
                // Dispatch to dj-hook instances that registered via handleEvent
                if (typeof dispatchPushEventToHooks === 'function') {
                    dispatchPushEventToHooks(data.event, data.payload);
                }
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

            case 'rate_limit_exceeded':
                // Server is dropping events due to rate limiting — show brief warning, do NOT retry
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

            case 'reload':
                // Hot reload: file changed, refresh the page
                window.location.reload();
                break;
        }
    }

    mount(viewPath, params = {}) {
        if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }

        console.log('[LiveView] Mounting view:', viewPath);
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

        // Send the message
        this.ws.send(message);
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
                    console.log('[LiveView] Content pre-rendered via HTTP - will skip HTML in mount response');
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

        this.sendMessage({
            type: 'event',
            event: eventName,
            params: params
        });
        return true;
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
            console.warn('[LiveView] Invalid embedded_update message:', data);
            return;
        }

        const container = document.querySelector(`[data-djust-embedded="${CSS.escape(viewId)}"]`);
        if (!container) {
            console.warn(`[LiveView] Embedded view container not found: ${viewId}`);
            return;
        }

        const _morphTemp = document.createElement('div');
        _morphTemp.innerHTML = html;
        morphChildren(container, _morphTemp);
        if (globalThis.djustDebug) console.log(`[LiveView] Updated embedded view: ${viewId}`);

        // Re-bind events within the updated container
        bindLiveViewEvents();
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
