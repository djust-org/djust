/**
 * djust PWA Client-Side Integration
 *
 * Provides offline support, service worker communication,
 * and PWA functionality for djust LiveView applications.
 */

(function() {
    'use strict';

    // Debug logging helper - uses djust debug system when available,
    // otherwise silently drops messages in production
    function _log(level, msg) {
        if (window.djust && window.djust.reportError && level === 'error') {
            window.djust.reportError(msg);
        } else if (window.djust && window.djust.debug) {
            window.djust.debug('[PWA] ' + msg);
        }
    }

    // PWA Manager Class
    class DjustPWA {
        constructor(config = {}) {
            this.config = {
                syncEndpoint: '/api/sync/',
                offlineStorage: 'indexeddb',
                connectionTimeout: 5000,
                retryInterval: 30000,
                maxOfflineActions: 100,
                ...config
            };

            this.isOnline = navigator.onLine;
            this.serviceWorker = null;
            this.offlineStorage = null;
            this.syncQueue = [];
            this.eventListeners = new Map();

            this.init();
        }

        async init() {
            _log('info', 'Initializing djust PWA support');

            // Initialize storage
            await this.initStorage();

            // Setup service worker
            await this.initServiceWorker();

            // Setup network detection
            this.initNetworkDetection();

            // Setup offline directive handling
            this.initOfflineDirectives();

            // Start sync monitoring
            this.startSyncMonitoring();

            _log('info', 'djust PWA initialization complete');
        }

        async initStorage() {
            try {
                if (this.config.offlineStorage === 'indexeddb' && 'indexedDB' in window) {
                    this.offlineStorage = new IndexedDBStorage();
                } else {
                    this.offlineStorage = new LocalStorageBackend();
                }

                await this.offlineStorage.init();
                _log('info', 'Storage initialized: ' + this.config.offlineStorage);
            } catch (error) {
                _log('error', 'Storage initialization failed');
                // Fall back to in-memory storage
                this.offlineStorage = new MemoryStorage();
            }
        }

        async initServiceWorker() {
            if ('serviceWorker' in navigator) {
                try {
                    const registration = await navigator.serviceWorker.getRegistration();
                    if (registration) {
                        this.serviceWorker = registration;

                        // Listen for service worker messages
                        navigator.serviceWorker.addEventListener('message', this.handleServiceWorkerMessage.bind(this));

                        // Check for updates
                        registration.addEventListener('updatefound', () => {
                            _log('info', 'Service worker update found');
                            this.dispatchEvent('sw-update-available', { registration });
                        });

                        _log('info', 'Service worker connected');
                    }
                } catch (error) {
                    _log('error', 'Service worker setup failed');
                }
            }
        }

        initNetworkDetection() {
            window.addEventListener('online', () => {
                _log('info', 'Connection restored');
                this.isOnline = true;
                this.handleConnectionChange(true);
            });

            window.addEventListener('offline', () => {
                _log('info', 'Connection lost');
                this.isOnline = false;
                this.handleConnectionChange(false);
            });

            // Initial status
            this.handleConnectionChange(this.isOnline);
        }

        initOfflineDirectives() {
            // Handle dj-offline attributes
            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    if (mutation.type === 'childList') {
                        mutation.addedNodes.forEach((node) => {
                            if (node.nodeType === Node.ELEMENT_NODE) {
                                this.processOfflineDirectives(node);
                            }
                        });
                    }
                });
            });

            observer.observe(document.body, {
                childList: true,
                subtree: true
            });

            // Process existing elements
            this.processOfflineDirectives(document.body);
        }

        processOfflineDirectives(element) {
            const offlineElements = element.querySelectorAll('[dj-offline]');

            offlineElements.forEach((el) => {
                const behavior = el.getAttribute('dj-offline');

                switch (behavior) {
                    case 'show':
                        el.style.display = this.isOnline ? 'none' : '';
                        break;
                    case 'hide':
                        el.style.display = this.isOnline ? '' : 'none';
                        break;
                    case 'disable':
                        el.disabled = !this.isOnline;
                        break;
                    case 'enable':
                        el.disabled = this.isOnline;
                        break;
                    case 'queue':
                        if (!this.isOnline) {
                            el.addEventListener('click', this.handleOfflineAction.bind(this));
                        }
                        break;
                }
            });
        }

        handleConnectionChange(online) {
            this.isOnline = online;

            // Update UI
            this.processOfflineDirectives(document.body);

            // Dispatch event for LiveView
            if (window.djust && window.djust.liveViewInstance) {
                window.djust.liveViewInstance.pushEvent('pwa:connection_change', {
                    online: online,
                    timestamp: Date.now()
                });
            }

            // Trigger sync if came back online
            if (online) {
                this.triggerSync();
            }

            // Dispatch custom event
            this.dispatchEvent('connection-change', { online });
        }

        handleOfflineAction(event) {
            if (this.isOnline) {
                return; // Let normal handling proceed
            }

            event.preventDefault();

            // Extract action data from element
            const element = event.target;
            const action = this.extractActionFromElement(element);

            if (action) {
                this.queueOfflineAction(action);

                // Show feedback
                this.showOfflineActionFeedback(element, 'Action queued for sync');
            }
        }

        extractActionFromElement(element) {
            // Extract djust event data from element
            const djClick = element.getAttribute('dj-click');
            const djSubmit = element.getAttribute('dj-submit');
            const djChange = element.getAttribute('dj-change');

            let eventName = null;
            let eventData = {};

            if (djClick) {
                eventName = djClick;
            } else if (djSubmit) {
                eventName = djSubmit;
                // Extract form data
                const form = element.closest('form');
                if (form) {
                    const formData = new FormData(form);
                    eventData = Object.fromEntries(formData);
                }
            } else if (djChange) {
                eventName = djChange;
                eventData = { value: element.value };
            }

            if (eventName) {
                return {
                    type: 'liveview_event',
                    event: eventName,
                    data: eventData,
                    element_id: element.id,
                    timestamp: Date.now()
                };
            }

            return null;
        }

        async queueOfflineAction(action) {
            try {
                // Add unique ID
                action.id = `offline_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
                action.status = 'pending';

                // Store in offline storage
                await this.offlineStorage.addAction(action);

                _log('info', 'Queued offline action: ' + action.id);

                // Notify LiveView if available
                if (window.djust && window.djust.liveViewInstance) {
                    window.djust.liveViewInstance.pushEvent('pwa:action_queued', {
                        action_id: action.id,
                        type: action.type
                    });
                }
            } catch (error) {
                _log('error', 'Failed to queue offline action');
            }
        }

        showOfflineActionFeedback(element, message) {
            // Create temporary feedback message
            const feedback = document.createElement('div');
            feedback.className = 'pwa-offline-feedback';
            feedback.textContent = message;
            feedback.style.cssText = `
                position: absolute;
                background: #333;
                color: white;
                padding: 8px 12px;
                border-radius: 4px;
                font-size: 12px;
                z-index: 10000;
                opacity: 0;
                transition: opacity 0.3s;
            `;

            // Position near element
            const rect = element.getBoundingClientRect();
            feedback.style.top = (rect.bottom + window.scrollY + 5) + 'px';
            feedback.style.left = (rect.left + window.scrollX) + 'px';

            document.body.appendChild(feedback);

            // Fade in
            setTimeout(() => feedback.style.opacity = '1', 10);

            // Remove after 3 seconds
            setTimeout(() => {
                feedback.style.opacity = '0';
                setTimeout(() => document.body.removeChild(feedback), 300);
            }, 3000);
        }

        async triggerSync() {
            if (!this.isOnline) {
                _log('info', 'Cannot sync while offline');
                return;
            }

            try {
                const pendingActions = await this.offlineStorage.getPendingActions();

                if (pendingActions.length === 0) {
                    _log('info', 'No actions to sync');
                    return;
                }

                _log('info', 'Starting sync of ' + pendingActions.length + ' actions');

                // Send sync request
                const response = await fetch(this.config.syncEndpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'djust-pwa'
                    },
                    body: JSON.stringify({
                        actions: pendingActions,
                        version: '1.0.0'
                    })
                });

                if (response.ok) {
                    const result = await response.json();
                    _log('info', 'Sync successful');

                    // Mark actions as synced
                    if (result.synced_ids) {
                        await this.offlineStorage.markActionsSynced(result.synced_ids);
                    }

                    // Notify LiveView
                    if (window.djust && window.djust.liveViewInstance) {
                        window.djust.liveViewInstance.pushEvent('pwa:sync_complete', result);
                    }

                    this.dispatchEvent('sync-complete', result);
                } else {
                    _log('error', 'Sync failed: ' + response.status + ' ' + response.statusText);
                    this.dispatchEvent('sync-error', { status: response.status });
                }
            } catch (error) {
                _log('error', 'Sync error: ' + error.message);
                this.dispatchEvent('sync-error', { error: error.message });
            }
        }

        startSyncMonitoring() {
            // Periodic sync when online
            setInterval(() => {
                if (this.isOnline) {
                    this.triggerSync();
                }
            }, this.config.retryInterval);
        }

        handleServiceWorkerMessage(event) {
            const { type, data } = event.data;

            switch (type) {
                case 'SYNC_COMPLETE':
                    _log('info', 'Service worker sync complete');
                    this.dispatchEvent('sw-sync-complete', data);
                    break;

                case 'CACHE_UPDATED':
                    _log('info', 'Cache updated');
                    this.dispatchEvent('cache-updated', data);
                    break;

                default:
                    _log('info', 'Unknown service worker message: ' + type);
            }
        }

        // Event system
        addEventListener(event, callback) {
            if (!this.eventListeners.has(event)) {
                this.eventListeners.set(event, []);
            }
            this.eventListeners.get(event).push(callback);
        }

        removeEventListener(event, callback) {
            if (this.eventListeners.has(event)) {
                const callbacks = this.eventListeners.get(event);
                const index = callbacks.indexOf(callback);
                if (index !== -1) {
                    callbacks.splice(index, 1);
                }
            }
        }

        dispatchEvent(event, data) {
            if (this.eventListeners.has(event)) {
                this.eventListeners.get(event).forEach(callback => {
                    try {
                        callback(data);
                    } catch (error) {
                        _log('error', 'Event listener error for ' + event);
                    }
                });
            }

            // Also dispatch as DOM event
            window.dispatchEvent(new CustomEvent(`pwa-${event}`, { detail: data }));
        }

        // Public API methods
        async clearOfflineData() {
            await this.offlineStorage.clear();
        }

        async getOfflineActionCount() {
            const actions = await this.offlineStorage.getPendingActions();
            return actions.length;
        }

        getConnectionInfo() {
            return {
                online: this.isOnline,
                type: navigator.connection?.type || 'unknown',
                effectiveType: navigator.connection?.effectiveType || 'unknown',
                downlink: navigator.connection?.downlink || null,
                rtt: navigator.connection?.rtt || null
            };
        }
    }

    // Storage Backends
    class IndexedDBStorage {
        constructor() {
            this.dbName = 'djust_pwa_storage';
            this.version = 1;
            this.db = null;
        }

        async init() {
            return new Promise((resolve, reject) => {
                const request = indexedDB.open(this.dbName, this.version);

                request.onerror = () => reject(request.error);
                request.onsuccess = () => {
                    this.db = request.result;
                    resolve();
                };

                request.onupgradeneeded = (event) => {
                    const db = event.target.result;

                    // Create object store for offline actions
                    if (!db.objectStoreNames.contains('actions')) {
                        const actionStore = db.createObjectStore('actions', { keyPath: 'id' });
                        actionStore.createIndex('status', 'status', { unique: false });
                        actionStore.createIndex('timestamp', 'timestamp', { unique: false });
                    }
                };
            });
        }

        async addAction(action) {
            return new Promise((resolve, reject) => {
                const transaction = this.db.transaction(['actions'], 'readwrite');
                const store = transaction.objectStore('actions');
                const request = store.add(action);

                request.onerror = () => reject(request.error);
                request.onsuccess = () => resolve();
            });
        }

        async getPendingActions() {
            return new Promise((resolve, reject) => {
                const transaction = this.db.transaction(['actions'], 'readonly');
                const store = transaction.objectStore('actions');
                const index = store.index('status');
                const request = index.getAll('pending');

                request.onerror = () => reject(request.error);
                request.onsuccess = () => resolve(request.result);
            });
        }

        async markActionsSynced(actionIds) {
            return new Promise((resolve, reject) => {
                const transaction = this.db.transaction(['actions'], 'readwrite');
                const store = transaction.objectStore('actions');

                const promises = actionIds.map(id => {
                    return new Promise((res, rej) => {
                        const getRequest = store.get(id);
                        getRequest.onsuccess = () => {
                            const action = getRequest.result;
                            if (action) {
                                action.status = 'synced';
                                action.synced_at = Date.now();

                                const putRequest = store.put(action);
                                putRequest.onerror = () => rej(putRequest.error);
                                putRequest.onsuccess = () => res();
                            } else {
                                res(); // Action not found, consider it synced
                            }
                        };
                        getRequest.onerror = () => rej(getRequest.error);
                    });
                });

                Promise.all(promises)
                    .then(() => resolve())
                    .catch(reject);
            });
        }

        async clear() {
            return new Promise((resolve, reject) => {
                const transaction = this.db.transaction(['actions'], 'readwrite');
                const store = transaction.objectStore('actions');
                const request = store.clear();

                request.onerror = () => reject(request.error);
                request.onsuccess = () => resolve();
            });
        }
    }

    class LocalStorageBackend {
        constructor() {
            this.storageKey = 'djust_pwa_actions';
        }

        async init() {
            // LocalStorage is synchronous, no initialization needed
        }

        async addAction(action) {
            const actions = this.getActions();
            actions.push(action);
            localStorage.setItem(this.storageKey, JSON.stringify(actions));
        }

        async getPendingActions() {
            const actions = this.getActions();
            return actions.filter(action => action.status === 'pending');
        }

        async markActionsSynced(actionIds) {
            const actions = this.getActions();
            const actionIdSet = new Set(actionIds);

            actions.forEach(action => {
                if (actionIdSet.has(action.id)) {
                    action.status = 'synced';
                    action.synced_at = Date.now();
                }
            });

            localStorage.setItem(this.storageKey, JSON.stringify(actions));
        }

        async clear() {
            localStorage.removeItem(this.storageKey);
        }

        getActions() {
            try {
                const data = localStorage.getItem(this.storageKey);
                return data ? JSON.parse(data) : [];
            } catch (error) {
                _log('error', 'LocalStorage parse error');
                return [];
            }
        }
    }

    class MemoryStorage {
        constructor() {
            this.actions = [];
        }

        async init() {
            // In-memory, no initialization needed
        }

        async addAction(action) {
            this.actions.push(action);
        }

        async getPendingActions() {
            return this.actions.filter(action => action.status === 'pending');
        }

        async markActionsSynced(actionIds) {
            const actionIdSet = new Set(actionIds);

            this.actions.forEach(action => {
                if (actionIdSet.has(action.id)) {
                    action.status = 'synced';
                    action.synced_at = Date.now();
                }
            });
        }

        async clear() {
            this.actions = [];
        }
    }

    // Initialize djust PWA when DOM is ready
    function initDjustPWA() {
        // Get config from global variable or data attribute
        let config = {};

        if (window.djustPWAConfig) {
            config = window.djustPWAConfig;
        } else {
            const configElement = document.querySelector('[data-djust-pwa-config]');
            if (configElement) {
                try {
                    config = JSON.parse(configElement.dataset.djustPwaConfig);
                } catch (error) {
                    _log('error', 'Invalid config JSON');
                }
            }
        }

        // Create global PWA instance
        window.djustPWA = new DjustPWA(config);

        // Integrate with djust LiveView if available
        if (window.djust) {
            window.djust.pwa = window.djustPWA;
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDjustPWA);
    } else {
        initDjustPWA();
    }

})();
