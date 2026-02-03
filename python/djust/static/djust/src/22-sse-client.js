// ============================================================================
// SSE (Server-Sent Events) Client for djust
// ============================================================================
//
// Provides a simpler alternative to WebSocket for server-to-client streaming.
// Ideal for dashboards, notifications, progress indicators.
//
// Usage:
//   <div dj-sse="/api/notifications/stream/">
//     <!-- Content updated by SSE events -->
//   </div>
//
//   Or programmatically:
//   const sse = new DjustSSE('/api/stream/');
//   sse.on('update', (data) => console.log(data));
//   sse.connect();
//
// ============================================================================

/**
 * SSE Client class for managing Server-Sent Events connections.
 */
class DjustSSE {
    constructor(url, options = {}) {
        this.url = url;
        this.options = {
            autoReconnect: true,
            maxRetries: 5,
            retryDelay: 3000,      // Initial retry delay (server can override via retry field)
            withCredentials: false,
            ...options
        };

        this.eventSource = null;
        this.listeners = new Map();
        this.retryCount = 0;
        this.lastEventId = null;
        this.connected = false;
        this.destroyed = false;

        // Statistics for debugging
        this.stats = {
            messagesReceived: 0,
            bytesReceived: 0,
            reconnections: 0,
            errors: 0,
            connectedAt: null,
        };
    }

    /**
     * Connect to the SSE endpoint.
     */
    connect() {
        if (this.destroyed) {
            console.warn('[SSE] Cannot connect - instance destroyed');
            return;
        }

        if (this.eventSource) {
            this.disconnect();
        }

        // Build URL with lastEventId if resuming
        let url = this.url;
        if (this.lastEventId) {
            const separator = url.includes('?') ? '&' : '?';
            url = `${url}${separator}lastEventId=${encodeURIComponent(this.lastEventId)}`;
        }

        console.log('[SSE] Connecting to:', url);

        this.eventSource = new EventSource(url, {
            withCredentials: this.options.withCredentials,
        });

        this.eventSource.onopen = () => {
            console.log('[SSE] Connected');
            this.connected = true;
            this.retryCount = 0;
            this.stats.connectedAt = Date.now();

            if (this.stats.reconnections > 0) {
                this._emit('reconnected', { reconnections: this.stats.reconnections });
            }

            this._emit('connected', { url: this.url });
        };

        this.eventSource.onerror = (event) => {
            console.error('[SSE] Connection error:', event);
            this.connected = false;
            this.stats.errors++;

            this._emit('error', { event });

            // EventSource will auto-reconnect, but we track it
            if (this.eventSource.readyState === EventSource.CLOSED) {
                this.stats.reconnections++;

                if (this.options.autoReconnect && this.retryCount < this.options.maxRetries) {
                    this.retryCount++;
                    console.log(`[SSE] Will reconnect (attempt ${this.retryCount}/${this.options.maxRetries})`);
                    this._emit('reconnecting', { attempt: this.retryCount });
                } else if (this.retryCount >= this.options.maxRetries) {
                    console.warn('[SSE] Max retries reached, giving up');
                    this._emit('disconnected', { reason: 'max_retries' });
                }
            }
        };

        // Handle all messages (no event type)
        this.eventSource.onmessage = (event) => {
            this._handleMessage('message', event);
        };

        // Register handlers for known event types
        this._registerEventType('connect');
        this._registerEventType('patch');
        this._registerEventType('html');
        this._registerEventType('text');
        this._registerEventType('redirect');
        this._registerEventType('reload');
        this._registerEventType('error');
        this._registerEventType('ping');
    }

    /**
     * Register a handler for a specific event type on the EventSource.
     */
    _registerEventType(eventType) {
        if (!this.eventSource) return;

        this.eventSource.addEventListener(eventType, (event) => {
            this._handleMessage(eventType, event);
        });
    }

    /**
     * Handle an incoming SSE message.
     */
    _handleMessage(eventType, event) {
        this.stats.messagesReceived++;
        this.stats.bytesReceived += (event.data || '').length;

        // Track last event ID for resumption
        if (event.lastEventId) {
            this.lastEventId = event.lastEventId;
        }

        // Parse JSON data
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            // Not JSON, use raw data
            data = event.data;
        }

        console.log(`[SSE] Event: ${eventType}`, data);

        // Handle built-in event types
        switch (eventType) {
            case 'connect':
                // Initial connection event from server
                break;

            case 'patch':
                // DOM patches - delegate to existing patch system
                if (data.patches && typeof applyPatches === 'function') {
                    applyPatches(data.patches);
                    if (typeof bindLiveViewEvents === 'function') {
                        bindLiveViewEvents();
                    }
                }
                break;

            case 'html':
                // HTML update for a selector
                this._applyHtmlUpdate(data);
                break;

            case 'text':
                // Text update for a selector
                this._applyTextUpdate(data);
                break;

            case 'redirect':
                // Navigate to URL
                if (data.url) {
                    if (window.djust?.navigation?.navigate) {
                        window.djust.navigation.navigate(data.url);
                    } else {
                        window.location.href = data.url;
                    }
                }
                break;

            case 'reload':
                // Reload page
                window.location.reload();
                break;

            case 'error':
                // Server error
                console.error('[SSE] Server error:', data.error || data);
                break;
        }

        // Emit to listeners
        this._emit(eventType, data, event);
    }

    /**
     * Apply HTML update to a selector.
     */
    _applyHtmlUpdate(data) {
        const { selector, html, mode = 'replace' } = data;
        if (!selector || html === undefined) return;

        const el = document.querySelector(selector);
        if (!el) {
            console.warn('[SSE] Selector not found:', selector);
            return;
        }

        switch (mode) {
            case 'replace':
                el.innerHTML = html;
                break;
            case 'append':
                el.insertAdjacentHTML('beforeend', html);
                break;
            case 'prepend':
                el.insertAdjacentHTML('afterbegin', html);
                break;
            case 'outer':
                el.outerHTML = html;
                break;
        }

        // Re-bind events
        if (typeof bindLiveViewEvents === 'function') {
            bindLiveViewEvents();
        }

        // Dispatch event for hooks
        el.dispatchEvent(new CustomEvent('sse:update', {
            bubbles: true,
            detail: { selector, mode, html }
        }));
    }

    /**
     * Apply text update to a selector.
     */
    _applyTextUpdate(data) {
        const { selector, text, mode = 'replace' } = data;
        if (!selector || text === undefined) return;

        const el = document.querySelector(selector);
        if (!el) {
            console.warn('[SSE] Selector not found:', selector);
            return;
        }

        switch (mode) {
            case 'replace':
                el.textContent = text;
                break;
            case 'append':
                el.textContent += text;
                break;
            case 'prepend':
                el.textContent = text + el.textContent;
                break;
        }

        // Dispatch event for hooks
        el.dispatchEvent(new CustomEvent('sse:text', {
            bubbles: true,
            detail: { selector, mode, text }
        }));
    }

    /**
     * Register an event listener.
     */
    on(eventType, callback) {
        if (!this.listeners.has(eventType)) {
            this.listeners.set(eventType, new Set());
        }
        this.listeners.get(eventType).add(callback);

        // If already connected and this is a new event type, register it
        if (this.eventSource && !['message', 'connected', 'disconnected', 'error', 'reconnecting', 'reconnected'].includes(eventType)) {
            this._registerEventType(eventType);
        }

        return this; // Allow chaining
    }

    /**
     * Remove an event listener.
     */
    off(eventType, callback) {
        if (this.listeners.has(eventType)) {
            this.listeners.get(eventType).delete(callback);
        }
        return this;
    }

    /**
     * Emit an event to listeners.
     */
    _emit(eventType, data, originalEvent = null) {
        if (this.listeners.has(eventType)) {
            for (const callback of this.listeners.get(eventType)) {
                try {
                    callback(data, originalEvent);
                } catch (e) {
                    console.error(`[SSE] Error in ${eventType} listener:`, e);
                }
            }
        }

        // Also emit to wildcard listeners
        if (this.listeners.has('*')) {
            for (const callback of this.listeners.get('*')) {
                try {
                    callback(eventType, data, originalEvent);
                } catch (e) {
                    console.error('[SSE] Error in wildcard listener:', e);
                }
            }
        }
    }

    /**
     * Disconnect from the SSE endpoint.
     */
    disconnect() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        this.connected = false;
        this._emit('disconnected', { reason: 'manual' });
    }

    /**
     * Destroy the SSE instance (disconnect and prevent reconnection).
     */
    destroy() {
        this.destroyed = true;
        this.disconnect();
        this.listeners.clear();
    }

    /**
     * Get connection statistics.
     */
    getStats() {
        return {
            ...this.stats,
            connected: this.connected,
            lastEventId: this.lastEventId,
            retryCount: this.retryCount,
            url: this.url,
        };
    }
}

// ============================================================================
// Declarative SSE via dj-sse attribute
// ============================================================================

/**
 * Initialize SSE connections for elements with dj-sse attribute.
 *
 * Usage:
 *   <div dj-sse="/api/notifications/stream/"
 *        dj-sse-event="notification"
 *        dj-sse-target="#notification-list"
 *        dj-sse-mode="prepend">
 *     <!-- Updates will be prepended to #notification-list -->
 *   </div>
 */
const _sseConnections = new Map();

function initSSEElements() {
    document.querySelectorAll('[dj-sse]').forEach(el => {
        // Skip if already initialized
        if (el._djustSSE) return;

        const url = el.getAttribute('dj-sse');
        if (!url) return;

        const options = {
            autoReconnect: el.getAttribute('dj-sse-reconnect') !== 'false',
            withCredentials: el.getAttribute('dj-sse-credentials') === 'true',
        };

        const sse = new DjustSSE(url, options);

        // Store reference on element
        el._djustSSE = sse;
        _sseConnections.set(el, sse);

        // Default event handling - update the element itself
        const targetSelector = el.getAttribute('dj-sse-target') || null;
        const defaultMode = el.getAttribute('dj-sse-mode') || 'replace';
        const eventFilter = el.getAttribute('dj-sse-event') || null;

        // Handle all events
        sse.on('*', (eventType, data, event) => {
            // Skip internal events
            if (['connected', 'disconnected', 'error', 'reconnecting', 'reconnected'].includes(eventType)) {
                return;
            }

            // Filter by event type if specified
            if (eventFilter && eventType !== eventFilter) {
                return;
            }

            // Get target element
            const target = targetSelector ? document.querySelector(targetSelector) : el;
            if (!target) return;

            // If data has html, use it; otherwise stringify data
            let html;
            if (data && typeof data === 'object' && data.html) {
                html = data.html;
            } else if (data && typeof data === 'object') {
                // For generic data, dispatch event and let app handle it
                target.dispatchEvent(new CustomEvent('sse:data', {
                    bubbles: true,
                    detail: { eventType, data }
                }));
                return;
            } else {
                html = String(data);
            }

            // Apply update
            switch (defaultMode) {
                case 'replace':
                    target.innerHTML = html;
                    break;
                case 'append':
                    target.insertAdjacentHTML('beforeend', html);
                    break;
                case 'prepend':
                    target.insertAdjacentHTML('afterbegin', html);
                    break;
            }

            // Re-bind events
            if (typeof bindLiveViewEvents === 'function') {
                bindLiveViewEvents();
            }
        });

        // Handle connection state classes
        sse.on('connected', () => {
            el.classList.add('sse-connected');
            el.classList.remove('sse-disconnected', 'sse-connecting');
        });

        sse.on('disconnected', () => {
            el.classList.add('sse-disconnected');
            el.classList.remove('sse-connected', 'sse-connecting');
        });

        sse.on('reconnecting', () => {
            el.classList.add('sse-connecting');
            el.classList.remove('sse-connected');
        });

        // Connect
        el.classList.add('sse-connecting');
        sse.connect();
    });
}

/**
 * Disconnect SSE for a specific element or all elements.
 */
function disconnectSSE(el = null) {
    if (el) {
        const sse = _sseConnections.get(el);
        if (sse) {
            sse.destroy();
            _sseConnections.delete(el);
            delete el._djustSSE;
        }
    } else {
        // Disconnect all
        for (const [element, sse] of _sseConnections) {
            sse.destroy();
            delete element._djustSSE;
        }
        _sseConnections.clear();
    }
}

/**
 * Get SSE connection for an element.
 */
function getSSE(el) {
    return _sseConnections.get(el);
}

// ============================================================================
// Auto-initialization
// ============================================================================

// Initialize on DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSSEElements);
} else {
    // DOM already loaded, init now
    setTimeout(initSSEElements, 0);
}

// Re-initialize when djust updates the DOM
document.addEventListener('djust:mounted', initSSEElements);
document.addEventListener('djust:patched', initSSEElements);

// Clean up on navigation
window.addEventListener('beforeunload', () => disconnectSSE());

// ============================================================================
// Exports
// ============================================================================

window.djust = window.djust || {};
window.djust.SSE = DjustSSE;
window.djust.initSSE = initSSEElements;
window.djust.disconnectSSE = disconnectSSE;
window.djust.getSSE = getSSE;

// Also expose globally for convenience
window.DjustSSE = DjustSSE;
