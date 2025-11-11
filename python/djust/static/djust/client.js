/**
 * Django Rust Live - Client-side runtime
 *
 * Minimal JavaScript client for reactive server-side rendering.
 * Handles WebSocket connection, event binding, and DOM patching.
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

            // Attempt to reconnect
            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                console.log(`[LiveView] Reconnecting in ${delay}ms...`);
                setTimeout(() => this.connect(), delay);
            }
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
         * Send an event to the server
         */
        sendEvent(eventName, params = {}) {
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
