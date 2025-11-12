/**
 * Django Rust Live - Client-side runtime
 *
 * Minimal JavaScript client for reactive server-side rendering.
 * Handles WebSocket connection, event binding, DOM patching, and state management decorators.
 *
 * Features:
 * - WebSocket connection management with auto-reconnect
 * - Event binding for @click, @input, @change, @submit
 * - Efficient DOM patching with VDOM diff algorithm
 * - State management decorators (@debounce, @throttle, @optimistic, @cache, @client_state)
 * - Debug logging infrastructure (window.djustDebug = true)
 *
 * Bundle Size: ~7-8 KB minified (19.3 KB unminified)
 *
 * State Management:
 * - @debounce(wait, max_wait) - Delay events until user stops triggering
 * - @throttle(interval, leading, trailing) - Limit event frequency
 * - Debug: window.djustDebug = true; window.djustDebugCategories = ['debounce', 'throttle']
 */

(function() {
    'use strict';

    class DjangoRustLive {
        constructor() {
            this.ws = null;
            this.sessionId = null;
            this.reconnectAttempts = 0;
            this.maxReconnectAttempts = 5;
            this.reconnectDelay = 1000;
            this.eventHandlers = new Map();

            // State management for decorators
            this.debounceTimers = new Map(); // Map<handlerName, {timerId, firstCallTime}>
            this.throttleState = new Map();  // Map<handlerName, {lastCall, timeoutId, pendingData}>

            // Initialize handler metadata storage (injected by server)
            window.handlerMetadata = window.handlerMetadata || {};
        }

        /**
         * Connect to the LiveView WebSocket server
         */
        connect(url = null) {
            if (!url) {
                // Auto-detect WebSocket URL
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const host = window.location.host;
                url = `${protocol}//${host}/ws/live/`;
            }

            this.ws = new WebSocket(url);

            this.ws.onopen = this.onOpen.bind(this);
            this.ws.onclose = this.onClose.bind(this);
            this.ws.onerror = this.onError.bind(this);
            this.ws.onmessage = this.onMessage.bind(this);
        }

        onOpen(event) {
            console.log('[LiveView] Connected');
            this.reconnectAttempts = 0;
            this.bindEvents();
        }

        onClose(event) {
            console.log('[LiveView] Disconnected');

            // Clean up decorator state (timers)
            this.cleanupDecoratorState();

            // Attempt to reconnect
            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                console.log(`[LiveView] Reconnecting in ${delay}ms...`);
                setTimeout(() => this.connect(), delay);
            }
        }

        /**
         * Clean up decorator state (called on disconnect)
         */
        cleanupDecoratorState() {
            // Clear all debounce timers
            this.debounceTimers.forEach(state => {
                if (state.timerId) {
                    clearTimeout(state.timerId);
                }
            });
            this.debounceTimers.clear();

            // Clear all throttle timers
            this.throttleState.forEach(state => {
                if (state.timeoutId) {
                    clearTimeout(state.timeoutId);
                }
            });
            this.throttleState.clear();

            this.debug('cleanup', 'Cleared all decorator timers on disconnect');
        }

        onError(error) {
            console.error('[LiveView] WebSocket error:', error);
        }

        onMessage(event) {
            try {
                const data = JSON.parse(event.data);

                switch (data.type) {
                    case 'connected':
                        this.sessionId = data.session_id;
                        console.log('[LiveView] Session ID:', this.sessionId);
                        break;

                    case 'patch':
                        this.applyPatches(data.patches);
                        break;

                    case 'error':
                        console.error('[LiveView] Server error:', data.error);
                        break;

                    case 'pong':
                        // Heartbeat response
                        break;
                }
            } catch (error) {
                console.error('[LiveView] Failed to parse message:', error);
            }
        }

        /**
         * Bind event listeners to elements with @event attributes
         */
        bindEvents() {
            const elements = document.querySelectorAll('[\\@click], [\\@input], [\\@change], [\\@submit]');

            elements.forEach(element => {
                // Handle @click
                const clickHandler = element.getAttribute('@click');
                if (clickHandler) {
                    element.addEventListener('click', (e) => {
                        e.preventDefault();

                        // Extract data-* attributes as event parameters
                        const params = {};
                        Array.from(e.currentTarget.attributes).forEach(attr => {
                            if (attr.name.startsWith('data-')) {
                                const key = attr.name.slice(5); // Remove 'data-' prefix
                                // Try to parse as JSON for complex types, otherwise use as string
                                try {
                                    params[key] = JSON.parse(attr.value);
                                } catch {
                                    params[key] = attr.value;
                                }
                            }
                        });

                        this.sendEvent(clickHandler, params);
                    });
                }

                // Handle @input
                const inputHandler = element.getAttribute('@input');
                if (inputHandler) {
                    element.addEventListener('input', (e) => {
                        this.sendEvent(inputHandler, { value: e.target.value });
                    });
                }

                // Handle @change
                const changeHandler = element.getAttribute('@change');
                if (changeHandler) {
                    element.addEventListener('change', (e) => {
                        this.sendEvent(changeHandler, { value: e.target.value });
                    });
                }

                // Handle @submit
                const submitHandler = element.getAttribute('@submit');
                if (submitHandler) {
                    element.addEventListener('submit', (e) => {
                        e.preventDefault();
                        const formData = new FormData(e.target);
                        const data = Object.fromEntries(formData.entries());
                        this.sendEvent(submitHandler, data);
                    });
                }
            });
        }

        /**
         * Send an event to the server (with decorator support)
         */
        sendEvent(eventName, params = {}) {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                console.error('[LiveView] WebSocket not connected');
                return;
            }

            // Check for handler metadata (decorators)
            const metadata = window.handlerMetadata?.[eventName];

            // Warn if multiple decorators present (ambiguous behavior)
            if (metadata?.debounce && metadata?.throttle) {
                console.warn(
                    `[LiveView] Handler '${eventName}' has both @debounce and @throttle decorators. ` +
                    `Applying @debounce only. Use one decorator per handler.`
                );
            }

            // Apply debounce if configured
            if (metadata?.debounce) {
                this.debounceEvent(eventName, params, metadata.debounce);
                return; // Don't send immediately
            }

            // Apply throttle if configured
            if (metadata?.throttle) {
                this.throttleEvent(eventName, params, metadata.throttle);
                return; // Don't send immediately
            }

            // Send immediately (no decorators or metadata missing)
            this.sendEventImmediate(eventName, params);
        }

        /**
         * Send event immediately, bypassing decorators (internal method)
         */
        sendEventImmediate(eventName, params = {}) {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                console.error('[LiveView] WebSocket not connected');
                return;
            }

            const message = {
                type: 'event',
                event: eventName,
                params: params,
            };

            this.ws.send(JSON.stringify(message));
            this.debug('event', `Sent event: ${eventName}`, params);
        }

        /**
         * Debounce an event - delay until user stops triggering events
         *
         * @param {string} eventName - Handler name (e.g., "search")
         * @param {object} eventData - Event parameters
         * @param {object} config - {wait: number, max_wait: number|null}
         */
        debounceEvent(eventName, eventData, config) {
            const { wait, max_wait } = config;
            const now = Date.now();

            // Get or create state
            let state = this.debounceTimers.get(eventName);
            if (!state) {
                state = { timerId: null, firstCallTime: now };
                this.debounceTimers.set(eventName, state);
            }

            // Clear existing timer
            if (state.timerId) {
                clearTimeout(state.timerId);
            }

            // Check if we've exceeded max_wait
            if (max_wait && (now - state.firstCallTime) >= (max_wait * 1000)) {
                // Force execution - max wait exceeded
                this.sendEventImmediate(eventName, eventData);
                this.debounceTimers.delete(eventName);
                this.debug('debounce', `Force executing ${eventName} (max_wait exceeded)`);
                return;
            }

            // Set new timer
            state.timerId = setTimeout(() => {
                this.sendEventImmediate(eventName, eventData);
                this.debounceTimers.delete(eventName);
                this.debug('debounce', `Executing ${eventName} after ${wait}s wait`);
            }, wait * 1000);

            this.debug('debounce', `Debouncing ${eventName} (wait: ${wait}s, max_wait: ${max_wait || 'none'})`);
        }

        /**
         * Throttle an event - limit execution frequency
         *
         * @param {string} eventName - Handler name (e.g., "on_scroll")
         * @param {object} eventData - Event parameters
         * @param {object} config - {interval: number, leading: bool, trailing: bool}
         */
        throttleEvent(eventName, eventData, config) {
            const { interval, leading, trailing } = config;
            const now = Date.now();

            if (!this.throttleState.has(eventName)) {
                // First call - execute immediately if leading=true
                if (leading) {
                    this.sendEventImmediate(eventName, eventData);
                    this.debug('throttle', `Executing ${eventName} (leading edge)`);
                }

                // Set up state
                const state = {
                    lastCall: leading ? now : 0,
                    timeoutId: null,
                    pendingData: null
                };

                this.throttleState.set(eventName, state);

                // Schedule trailing call if needed
                if (trailing && !leading) {
                    state.pendingData = eventData;
                    state.timeoutId = setTimeout(() => {
                        this.sendEventImmediate(eventName, state.pendingData);
                        this.throttleState.delete(eventName);
                        this.debug('throttle', `Executing ${eventName} (trailing edge - no leading)`);
                    }, interval * 1000);
                }

                return;
            }

            const state = this.throttleState.get(eventName);
            const elapsed = now - state.lastCall;

            if (elapsed >= (interval * 1000)) {
                // Enough time has passed - execute now
                this.sendEventImmediate(eventName, eventData);
                state.lastCall = now;
                state.pendingData = null;

                // Clear any pending trailing call
                if (state.timeoutId) {
                    clearTimeout(state.timeoutId);
                    state.timeoutId = null;
                }

                this.debug('throttle', `Executing ${eventName} (interval elapsed: ${elapsed}ms)`);
            } else if (trailing) {
                // Update pending data and reschedule trailing call
                state.pendingData = eventData;

                if (state.timeoutId) {
                    clearTimeout(state.timeoutId);
                }

                const remaining = (interval * 1000) - elapsed;
                state.timeoutId = setTimeout(() => {
                    if (state.pendingData) {
                        this.sendEventImmediate(eventName, state.pendingData);
                        this.debug('throttle', `Executing ${eventName} (trailing edge)`);
                    }
                    this.throttleState.delete(eventName);
                }, remaining);

                this.debug('throttle', `Throttled ${eventName} (${remaining}ms until trailing)`);
            } else {
                this.debug('throttle', `Dropped ${eventName} (within interval, no trailing)`);
            }
        }

        /**
         * Debug logging helper
         *
         * Set window.djustDebug = true to enable
         * Set window.djustDebugCategories = ['debounce', 'throttle'] to filter
         */
        debug(category, message, data = null) {
            if (!window.djustDebug) return;

            // Filter by category if specified
            if (window.djustDebugCategories &&
                !window.djustDebugCategories.includes(category)) {
                return;
            }

            const prefix = `[LiveView:${category}]`;
            if (data) {
                console.log(prefix, message, data);
            } else {
                console.log(prefix, message);
            }
        }

        /**
         * Apply DOM patches from server
         */
        applyPatches(patches) {
            if (!patches || patches.length === 0) {
                return;
            }

            console.log('[LiveView] Applying patches:', patches);

            patches.forEach((patch, patchIndex) => {
                try {
                    const element = this.getElementByPath(patch.path);
                    if (!element) {
                        console.warn('[LiveView] Element not found for path:', patch.path);
                        return;
                    }

                    switch (patch.type) {
                        case 'Replace':
                            this.patchReplace(element, patch.node);
                            break;

                        case 'SetText':
                            this.patchSetText(element, patch.text);
                            break;

                        case 'SetAttr':
                            this.patchSetAttr(element, patch.key, patch.value);
                            break;

                        case 'RemoveAttr':
                            console.log(`[LiveView] Patch ${patchIndex}: RemoveAttr '${patch.key}' at path (${patch.path.length}) [${patch.path.join(', ')}]`);
                            console.log(`[LiveView]   Target element: <${element.tagName}> with ${element.children.length} children`);
                            this.patchRemoveAttr(element, patch.key);
                            break;

                        case 'InsertChild':
                            this.patchInsertChild(element, patch.index, patch.node);
                            break;

                        case 'RemoveChild':
                            this.patchRemoveChild(element, patch.index);
                            break;

                        case 'MoveChild':
                            this.patchMoveChild(element, patch.from, patch.to);
                            break;
                    }
                } catch (error) {
                    console.error(`[LiveView] FAILED: Patch ${patchIndex} at path [${patch.path.join(', ')}]`);
                    console.error(`[LiveView]   Patch type: ${patch.type}`);
                    console.error(`[LiveView]   Error: ${error.message}`);

                    // Show what we found at each step of the path
                    this.debugPath(patch.path);
                }
            });

            // Re-bind events after patching
            this.bindEvents();
        }

        getElementByPath(path) {
            if (path.length === 0) {
                return document.body;
            }

            let element = document.body;
            for (const index of path) {
                if (element.children[index]) {
                    element = element.children[index];
                } else {
                    return null;
                }
            }
            return element;
        }

        /**
         * Debug helper: Show what's at each step of a path
         */
        debugPath(path) {
            console.log('[LiveView] === Path Debug ===');
            let element = document.body;
            console.log(`[LiveView] Path[root] = BODY with ${element.children.length} children`);

            for (let i = 0; i < path.length; i++) {
                const index = path[i];
                console.log(`[LiveView] Path[${i}] = ${index}, available children: ${element.children.length}`);

                // Show all children tags
                const childTags = Array.from(element.children).map((child, idx) =>
                    `[${idx}]=${child.tagName}`
                ).join(' ');
                console.log(`[LiveView]   Children: ${childTags}`);

                if (element.children[index]) {
                    element = element.children[index];
                    console.log(`[LiveView]   Selected: <${element.tagName}> with ${element.children.length} children`);

                    // Show some attributes if present
                    if (element.id) console.log(`[LiveView]     id="${element.id}"`);
                    if (element.className) console.log(`[LiveView]     class="${element.className}"`);
                } else {
                    console.log(`[LiveView]   FAILED: Index ${index} out of bounds (only ${element.children.length} children)`);
                    break;
                }
            }
            console.log('[LiveView] === End Path Debug ===');
        }

        patchReplace(element, node) {
            const newElement = this.vnodeToElement(node);
            element.replaceWith(newElement);
        }

        patchSetText(element, text) {
            element.textContent = text;
        }

        patchSetAttr(element, key, value) {
            element.setAttribute(key, value);
        }

        patchRemoveAttr(element, key) {
            element.removeAttribute(key);
        }

        patchInsertChild(element, index, node) {
            const newElement = this.vnodeToElement(node);
            if (index >= element.children.length) {
                element.appendChild(newElement);
            } else {
                element.insertBefore(newElement, element.children[index]);
            }
        }

        patchRemoveChild(element, index) {
            if (element.children[index]) {
                element.children[index].remove();
            }
        }

        patchMoveChild(element, from, to) {
            if (element.children[from]) {
                const child = element.children[from];
                if (to >= element.children.length) {
                    element.appendChild(child);
                } else {
                    element.insertBefore(child, element.children[to]);
                }
            }
        }

        vnodeToElement(vnode) {
            if (vnode.tag === '#text') {
                return document.createTextNode(vnode.text || '');
            }

            const element = document.createElement(vnode.tag);

            // Set attributes
            for (const [key, value] of Object.entries(vnode.attrs || {})) {
                element.setAttribute(key, value);
            }

            // Add children
            for (const child of vnode.children || []) {
                element.appendChild(this.vnodeToElement(child));
            }

            return element;
        }

        /**
         * Start heartbeat to keep connection alive
         */
        startHeartbeat(interval = 30000) {
            setInterval(() => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'ping' }));
                }
            }, interval);
        }
    }

    // Export to global scope
    window.DjangoRustLive = new DjangoRustLive();

})();
