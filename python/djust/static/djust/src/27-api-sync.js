/**
 * djust API Sync - Real-time API synchronization directive
 * 
 * Provides `dj-api-sync` directive for automatic REST API polling and updates.
 * Works with RESTMixin on the backend for seamless API integration.
 * 
 * Usage:
 *   <div dj-api-sync="/api/products/" dj-api-interval="5000" dj-api-target="products">
 *     Products will auto-refresh every 5 seconds
 *   </div>
 * 
 * Attributes:
 *   dj-api-sync      - API endpoint URL
 *   dj-api-interval  - Polling interval in ms (default: 10000)
 *   dj-api-target    - Server-side attribute to update
 *   dj-api-method    - HTTP method (default: GET)
 *   dj-api-headers   - JSON object of custom headers
 *   dj-api-on-error  - Event handler name for errors
 *   dj-api-on-data   - Event handler name for data received
 */

(function(DJ) {
    'use strict';
    
    // API Sync state
    const apiSyncInstances = new Map();
    let instanceCounter = 0;
    
    /**
     * API Sync instance class
     */
    class APISyncInstance {
        constructor(element, options) {
            this.id = ++instanceCounter;
            this.element = element;
            this.options = {
                endpoint: options.endpoint,
                interval: options.interval || 10000,
                target: options.target,
                method: options.method || 'GET',
                headers: options.headers || {},
                onError: options.onError,
                onData: options.onData,
            };
            
            this.timer = null;
            this.abortController = null;
            this.lastData = null;
            this.isActive = false;
            this.retryCount = 0;
            this.maxRetries = 3;
        }
        
        /**
         * Start polling
         */
        start() {
            if (this.isActive) return;
            
            this.isActive = true;
            this.retryCount = 0;
            
            // Fetch immediately, then poll
            this.fetch();
            this.scheduleNext();
            
            DJ.log(`[APIsync] Started polling ${this.options.endpoint} (interval: ${this.options.interval}ms)`);
        }
        
        /**
         * Stop polling
         */
        stop() {
            this.isActive = false;
            
            if (this.timer) {
                clearTimeout(this.timer);
                this.timer = null;
            }
            
            if (this.abortController) {
                this.abortController.abort();
                this.abortController = null;
            }
            
            DJ.log(`[APIsync] Stopped polling ${this.options.endpoint}`);
        }
        
        /**
         * Schedule next poll
         */
        scheduleNext() {
            if (!this.isActive) return;
            
            this.timer = setTimeout(() => {
                this.fetch();
                this.scheduleNext();
            }, this.options.interval);
        }
        
        /**
         * Fetch data from API
         */
        async fetch() {
            if (!this.isActive) return;
            
            // Cancel any pending request
            if (this.abortController) {
                this.abortController.abort();
            }
            this.abortController = new AbortController();
            
            try {
                const headers = {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    ...this.options.headers,
                };
                
                // Add CSRF token if available
                const csrfToken = this.getCSRFToken();
                if (csrfToken && this.options.method !== 'GET') {
                    headers['X-CSRFToken'] = csrfToken;
                }
                
                const response = await fetch(this.options.endpoint, {
                    method: this.options.method,
                    headers,
                    signal: this.abortController.signal,
                    credentials: 'same-origin',
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                
                // Reset retry count on success
                this.retryCount = 0;
                
                // Check if data changed
                const dataStr = JSON.stringify(data);
                if (dataStr !== this.lastData) {
                    this.lastData = dataStr;
                    this.onDataReceived(data);
                }
                
            } catch (error) {
                if (error.name === 'AbortError') {
                    // Request was cancelled, ignore
                    return;
                }
                
                this.onError(error);
            }
        }
        
        /**
         * Get CSRF token from cookie or meta tag
         */
        getCSRFToken() {
            // Try meta tag first
            const metaTag = document.querySelector('meta[name="csrf-token"]');
            if (metaTag) {
                return metaTag.content;
            }
            
            // Try cookie
            const cookies = document.cookie.split(';');
            for (const cookie of cookies) {
                const [name, value] = cookie.trim().split('=');
                if (name === 'csrftoken') {
                    return value;
                }
            }
            
            return null;
        }
        
        /**
         * Handle received data
         */
        onDataReceived(data) {
            DJ.log(`[APIsync] Data received from ${this.options.endpoint}`, data);
            
            // Update server-side via WebSocket if target specified
            if (this.options.target && DJ.ws && DJ.ws.connected) {
                DJ.ws.send({
                    type: 'api_sync_update',
                    target: this.options.target,
                    data: data,
                });
            }
            
            // Call custom handler if specified
            if (this.options.onData) {
                this.element.dispatchEvent(new CustomEvent('dj:api-data', {
                    detail: { data, endpoint: this.options.endpoint },
                    bubbles: true,
                }));
                
                // Also trigger server-side event handler if dj-click style name
                if (DJ.ws && DJ.ws.connected) {
                    DJ.ws.send({
                        type: 'event',
                        handler: this.options.onData,
                        payload: { data, endpoint: this.options.endpoint },
                    });
                }
            }
            
            // Update element with loading state
            this.element.classList.remove('dj-api-loading');
            this.element.classList.add('dj-api-synced');
            this.element.dataset.djApiLastSync = new Date().toISOString();
        }
        
        /**
         * Handle errors
         */
        onError(error) {
            this.retryCount++;
            
            DJ.log(`[APIsync] Error fetching ${this.options.endpoint} (retry ${this.retryCount}/${this.maxRetries}):`, error);
            
            this.element.classList.remove('dj-api-loading', 'dj-api-synced');
            this.element.classList.add('dj-api-error');
            
            // Call error handler
            if (this.options.onError) {
                this.element.dispatchEvent(new CustomEvent('dj:api-error', {
                    detail: { error, endpoint: this.options.endpoint },
                    bubbles: true,
                }));
                
                if (DJ.ws && DJ.ws.connected) {
                    DJ.ws.send({
                        type: 'event',
                        handler: this.options.onError,
                        payload: { 
                            error: error.message, 
                            endpoint: this.options.endpoint,
                            retryCount: this.retryCount,
                        },
                    });
                }
            }
            
            // Stop polling after max retries
            if (this.retryCount >= this.maxRetries) {
                DJ.log(`[APIsync] Max retries reached for ${this.options.endpoint}, stopping`);
                this.stop();
            }
        }
        
        /**
         * Force immediate refresh
         */
        refresh() {
            this.element.classList.add('dj-api-loading');
            this.fetch();
        }
    }
    
    /**
     * Initialize API sync for an element
     */
    function initAPISyncElement(element) {
        // Skip if already initialized
        if (element._djApiSyncId) {
            return apiSyncInstances.get(element._djApiSyncId);
        }
        
        const endpoint = element.getAttribute('dj-api-sync');
        if (!endpoint) return null;
        
        // Parse options
        let headers = {};
        const headersAttr = element.getAttribute('dj-api-headers');
        if (headersAttr) {
            try {
                headers = JSON.parse(headersAttr);
            } catch (e) {
                DJ.log('[APIsync] Invalid JSON in dj-api-headers:', headersAttr);
            }
        }
        
        const options = {
            endpoint,
            interval: parseInt(element.getAttribute('dj-api-interval') || '10000', 10),
            target: element.getAttribute('dj-api-target'),
            method: element.getAttribute('dj-api-method') || 'GET',
            headers,
            onError: element.getAttribute('dj-api-on-error'),
            onData: element.getAttribute('dj-api-on-data'),
        };
        
        const instance = new APISyncInstance(element, options);
        apiSyncInstances.set(instance.id, instance);
        element._djApiSyncId = instance.id;
        
        // Start if auto-start is not disabled
        if (element.getAttribute('dj-api-auto') !== 'false') {
            instance.start();
        }
        
        return instance;
    }
    
    /**
     * Cleanup API sync for an element
     */
    function cleanupAPISyncElement(element) {
        const instanceId = element._djApiSyncId;
        if (!instanceId) return;
        
        const instance = apiSyncInstances.get(instanceId);
        if (instance) {
            instance.stop();
            apiSyncInstances.delete(instanceId);
        }
        delete element._djApiSyncId;
    }
    
    /**
     * Initialize all API sync elements in a container
     */
    function initAPISync(container = document) {
        const elements = container.querySelectorAll('[dj-api-sync]');
        elements.forEach(initAPISyncElement);
    }
    
    /**
     * Refresh a specific API sync element
     */
    function refreshAPISync(selector) {
        const element = typeof selector === 'string' 
            ? document.querySelector(selector) 
            : selector;
        
        if (!element) return;
        
        const instance = apiSyncInstances.get(element._djApiSyncId);
        if (instance) {
            instance.refresh();
        }
    }
    
    /**
     * Stop all API sync instances
     */
    function stopAllAPISync() {
        apiSyncInstances.forEach(instance => instance.stop());
        apiSyncInstances.clear();
    }
    
    // ========================================================================
    // DOM Observation
    // ========================================================================
    
    // Observe DOM for new elements
    const observer = new MutationObserver(mutations => {
        mutations.forEach(mutation => {
            // Handle added nodes
            mutation.addedNodes.forEach(node => {
                if (node.nodeType !== Node.ELEMENT_NODE) return;
                
                if (node.hasAttribute && node.hasAttribute('dj-api-sync')) {
                    initAPISyncElement(node);
                }
                
                // Check children
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-api-sync]').forEach(initAPISyncElement);
                }
            });
            
            // Handle removed nodes
            mutation.removedNodes.forEach(node => {
                if (node.nodeType !== Node.ELEMENT_NODE) return;
                
                if (node._djApiSyncId) {
                    cleanupAPISyncElement(node);
                }
                
                // Check children
                if (node.querySelectorAll) {
                    node.querySelectorAll('[dj-api-sync]').forEach(cleanupAPISyncElement);
                }
            });
        });
    });
    
    // Start observing once DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initAPISync();
            observer.observe(document.body, {
                childList: true,
                subtree: true,
            });
        });
    } else {
        initAPISync();
        observer.observe(document.body, {
            childList: true,
            subtree: true,
        });
    }
    
    // Cleanup on page unload
    window.addEventListener('beforeunload', stopAllAPISync);
    
    // Handle visibility changes (pause when hidden, resume when visible)
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            // Pause polling when page is hidden
            apiSyncInstances.forEach(instance => {
                if (instance.timer) {
                    clearTimeout(instance.timer);
                    instance.timer = null;
                }
            });
        } else {
            // Resume polling when page is visible
            apiSyncInstances.forEach(instance => {
                if (instance.isActive && !instance.timer) {
                    instance.fetch();
                    instance.scheduleNext();
                }
            });
        }
    });
    
    // ========================================================================
    // Public API
    // ========================================================================
    
    DJ.apiSync = {
        init: initAPISync,
        initElement: initAPISyncElement,
        refresh: refreshAPISync,
        stop: stopAllAPISync,
        instances: apiSyncInstances,
    };
    
    DJ.log('[APIsync] API sync module loaded');
    
})(window.DJ = window.DJ || {});
