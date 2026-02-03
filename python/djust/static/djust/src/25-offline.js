// ============================================================================
// Offline/PWA Support for djust LiveViews
// ============================================================================
// Provides:
// - Offline detection and status tracking
// - Event queueing when offline with IndexedDB persistence
// - State persistence to IndexedDB
// - Offline directive handling (dj-offline-show/hide/disable)
// - Service worker communication
// - Background sync support

(function() {
    'use strict';

    // IndexedDB database name and version
    const DB_NAME = 'djust_offline';
    const DB_VERSION = 1;
    const STORE_EVENTS = 'queued_events';
    const STORE_STATE = 'view_state';

    // ========================================================================
    // IndexedDB Helpers
    // ========================================================================

    let db = null;

    /**
     * Open the IndexedDB database
     * @returns {Promise<IDBDatabase>}
     */
    async function openDatabase() {
        if (db) return db;

        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);

            request.onerror = () => {
                console.error('[djust Offline] IndexedDB error:', request.error);
                reject(request.error);
            };

            request.onsuccess = () => {
                db = request.result;
                console.log('[djust Offline] IndexedDB opened');
                resolve(db);
            };

            request.onupgradeneeded = (event) => {
                const database = event.target.result;

                // Create object store for queued events
                if (!database.objectStoreNames.contains(STORE_EVENTS)) {
                    const eventsStore = database.createObjectStore(STORE_EVENTS, {
                        keyPath: 'id',
                        autoIncrement: true
                    });
                    eventsStore.createIndex('timestamp', 'timestamp', { unique: false });
                    eventsStore.createIndex('viewId', 'viewId', { unique: false });
                }

                // Create object store for view state
                if (!database.objectStoreNames.contains(STORE_STATE)) {
                    database.createObjectStore(STORE_STATE, { keyPath: 'viewId' });
                }

                console.log('[djust Offline] IndexedDB schema created');
            };
        });
    }

    /**
     * Save an event to the queue
     * @param {string} viewId - The LiveView ID
     * @param {string} eventName - Event name
     * @param {object} params - Event parameters
     * @returns {Promise<number>} The event ID
     */
    async function queueEvent(viewId, eventName, params) {
        const database = await openDatabase();
        
        return new Promise((resolve, reject) => {
            const transaction = database.transaction([STORE_EVENTS], 'readwrite');
            const store = transaction.objectStore(STORE_EVENTS);

            const event = {
                viewId,
                name: eventName,
                params: params || {},
                timestamp: Date.now(),
                synced: false
            };

            const request = store.add(event);

            request.onsuccess = () => {
                console.log('[djust Offline] Event queued:', eventName);
                updateQueuedCount();
                resolve(request.result);
            };

            request.onerror = () => {
                console.error('[djust Offline] Failed to queue event:', request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Get all queued events for a view
     * @param {string} viewId - The LiveView ID (optional, gets all if not provided)
     * @returns {Promise<Array>}
     */
    async function getQueuedEvents(viewId = null) {
        const database = await openDatabase();

        return new Promise((resolve, reject) => {
            const transaction = database.transaction([STORE_EVENTS], 'readonly');
            const store = transaction.objectStore(STORE_EVENTS);

            let request;
            if (viewId) {
                const index = store.index('viewId');
                request = index.getAll(viewId);
            } else {
                request = store.getAll();
            }

            request.onsuccess = () => {
                // Sort by timestamp
                const events = request.result.sort((a, b) => a.timestamp - b.timestamp);
                resolve(events);
            };

            request.onerror = () => {
                reject(request.error);
            };
        });
    }

    /**
     * Clear queued events
     * @param {Array<number>} ids - Event IDs to clear (clears all if not provided)
     * @returns {Promise<void>}
     */
    async function clearQueuedEvents(ids = null) {
        const database = await openDatabase();

        return new Promise((resolve, reject) => {
            const transaction = database.transaction([STORE_EVENTS], 'readwrite');
            const store = transaction.objectStore(STORE_EVENTS);

            if (ids && ids.length > 0) {
                // Delete specific events
                ids.forEach(id => store.delete(id));
            } else {
                // Clear all
                store.clear();
            }

            transaction.oncomplete = () => {
                console.log('[djust Offline] Cleared queued events');
                updateQueuedCount();
                resolve();
            };

            transaction.onerror = () => {
                reject(transaction.error);
            };
        });
    }

    /**
     * Save view state to IndexedDB
     * @param {string} viewId - The LiveView ID
     * @param {object} state - State to persist
     * @returns {Promise<void>}
     */
    async function saveState(viewId, state) {
        const database = await openDatabase();

        return new Promise((resolve, reject) => {
            const transaction = database.transaction([STORE_STATE], 'readwrite');
            const store = transaction.objectStore(STORE_STATE);

            const record = {
                viewId,
                state,
                savedAt: Date.now()
            };

            const request = store.put(record);

            request.onsuccess = () => {
                console.log('[djust Offline] State saved for view:', viewId);
                resolve();
            };

            request.onerror = () => {
                console.error('[djust Offline] Failed to save state:', request.error);
                reject(request.error);
            };
        });
    }

    /**
     * Load view state from IndexedDB
     * @param {string} viewId - The LiveView ID
     * @returns {Promise<object|null>}
     */
    async function loadState(viewId) {
        const database = await openDatabase();

        return new Promise((resolve, reject) => {
            const transaction = database.transaction([STORE_STATE], 'readonly');
            const store = transaction.objectStore(STORE_STATE);
            const request = store.get(viewId);

            request.onsuccess = () => {
                if (request.result) {
                    console.log('[djust Offline] State loaded for view:', viewId);
                    resolve(request.result.state);
                } else {
                    resolve(null);
                }
            };

            request.onerror = () => {
                reject(request.error);
            };
        });
    }

    /**
     * Clear saved state for a view
     * @param {string} viewId - The LiveView ID (clears all if not provided)
     * @returns {Promise<void>}
     */
    async function clearState(viewId = null) {
        const database = await openDatabase();

        return new Promise((resolve, reject) => {
            const transaction = database.transaction([STORE_STATE], 'readwrite');
            const store = transaction.objectStore(STORE_STATE);

            if (viewId) {
                store.delete(viewId);
            } else {
                store.clear();
            }

            transaction.oncomplete = () => {
                console.log('[djust Offline] State cleared');
                resolve();
            };

            transaction.onerror = () => {
                reject(transaction.error);
            };
        });
    }

    // ========================================================================
    // Offline Detection
    // ========================================================================

    let isOnline = navigator.onLine;
    let wsConnected = false;
    let offlineConfig = null;

    /**
     * Update online/offline status
     * @param {boolean} online - Whether we're online
     * @param {boolean} wsStatus - WebSocket connection status
     */
    function updateOnlineStatus(online, wsStatus = wsConnected) {
        const wasOnline = isOnline && wsConnected;
        isOnline = online;
        wsConnected = wsStatus;
        const nowOnline = isOnline && wsConnected;

        // Update body classes
        document.body.classList.toggle('djust-online', nowOnline);
        document.body.classList.toggle('djust-offline', !nowOnline);

        // Update directives
        updateOfflineDirectives(!nowOnline);

        // Update indicators
        updateOfflineIndicators(!nowOnline);

        // Dispatch custom event
        window.dispatchEvent(new CustomEvent('djust:online-status', {
            detail: { online: nowOnline, networkOnline: isOnline, wsConnected }
        }));

        console.log(`[djust Offline] Status: ${nowOnline ? 'ONLINE' : 'OFFLINE'} (network: ${isOnline}, ws: ${wsConnected})`);

        // If we just came online, try to sync
        if (nowOnline && !wasOnline) {
            syncQueuedEvents();
        }
    }

    /**
     * Update elements with offline directives
     * @param {boolean} offline - Whether we're offline
     */
    function updateOfflineDirectives(offline) {
        // dj-offline-show: show when offline
        document.querySelectorAll('[dj-offline-show]').forEach(el => {
            el.style.display = offline ? '' : 'none';
        });

        // dj-offline-hide: hide when offline
        document.querySelectorAll('[dj-offline-hide]').forEach(el => {
            el.style.display = offline ? 'none' : '';
        });

        // dj-offline-disable: disable when offline
        document.querySelectorAll('[dj-offline-disable]').forEach(el => {
            if (offline) {
                el.setAttribute('disabled', 'disabled');
                el.classList.add('djust-disabled-offline');
            } else {
                // Only remove disabled if we added it
                if (el.classList.contains('djust-disabled-offline')) {
                    el.removeAttribute('disabled');
                    el.classList.remove('djust-disabled-offline');
                }
            }
        });
    }

    /**
     * Update offline indicator elements
     * @param {boolean} offline - Whether we're offline
     */
    function updateOfflineIndicators(offline) {
        document.querySelectorAll('.djust-offline-indicator').forEach(indicator => {
            const onlineText = indicator.dataset.onlineText || 'Online';
            const offlineText = indicator.dataset.offlineText || 'Offline';
            const onlineClass = indicator.dataset.onlineClass || 'djust-status-online';
            const offlineClass = indicator.dataset.offlineClass || 'djust-status-offline';

            const textEl = indicator.querySelector('.djust-indicator-text');
            if (textEl) {
                textEl.textContent = offline ? offlineText : onlineText;
            }

            indicator.classList.toggle(onlineClass, !offline);
            indicator.classList.toggle(offlineClass, offline);
        });
    }

    /**
     * Update queued event count display
     */
    async function updateQueuedCount() {
        try {
            const events = await getQueuedEvents();
            const count = events.length;

            // Update any queued count indicators
            document.querySelectorAll('.djust-queued-count').forEach(el => {
                el.textContent = count;
            });

            // Show/hide queued indicator
            document.querySelectorAll('.djust-queued-indicator').forEach(el => {
                el.classList.toggle('has-queued', count > 0);
            });
        } catch (error) {
            console.error('[djust Offline] Failed to update queued count:', error);
        }
    }

    // ========================================================================
    // Event Queueing
    // ========================================================================

    /**
     * Queue an event for later sync (when offline)
     * @param {string} eventName - Event name
     * @param {object} params - Event parameters
     * @returns {Promise<boolean>} Whether the event was queued
     */
    async function handleOfflineEvent(eventName, params) {
        // Check if this event should be queued
        if (!offlineConfig || !offlineConfig.syncEvents) {
            console.log('[djust Offline] No sync events configured, dropping event:', eventName);
            return false;
        }

        if (!offlineConfig.syncEvents.includes(eventName)) {
            console.log('[djust Offline] Event not in syncEvents, dropping:', eventName);
            return false;
        }

        // Get view ID from container
        const container = document.querySelector('[data-djust-view]');
        const viewId = container ? container.dataset.djustView : 'default';

        try {
            await queueEvent(viewId, eventName, params);
            showQueuedToast(eventName);
            return true;
        } catch (error) {
            console.error('[djust Offline] Failed to queue event:', error);
            return false;
        }
    }

    /**
     * Show a toast notification that an event was queued
     * @param {string} eventName - Event name
     */
    function showQueuedToast(eventName) {
        // Check if toast already exists
        let toast = document.getElementById('djust-queued-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'djust-queued-toast';
            toast.style.cssText = `
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: #fef3c7;
                color: #92400e;
                padding: 12px 16px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                font-size: 14px;
                z-index: 99999;
                transition: opacity 0.3s ease;
            `;
            document.body.appendChild(toast);
        }

        toast.textContent = `Action queued: ${eventName}. Will sync when back online.`;
        toast.style.opacity = '1';

        // Hide after 3 seconds
        clearTimeout(toast._hideTimeout);
        toast._hideTimeout = setTimeout(() => {
            toast.style.opacity = '0';
        }, 3000);
    }

    // ========================================================================
    // Sync Queued Events
    // ========================================================================

    let isSyncing = false;

    /**
     * Sync all queued events to the server
     * @returns {Promise<void>}
     */
    async function syncQueuedEvents() {
        if (isSyncing) {
            console.log('[djust Offline] Already syncing, skipping');
            return;
        }

        if (!isOnline || !wsConnected) {
            console.log('[djust Offline] Cannot sync - offline');
            return;
        }

        isSyncing = true;
        console.log('[djust Offline] Syncing queued events...');

        try {
            const events = await getQueuedEvents();
            if (events.length === 0) {
                console.log('[djust Offline] No events to sync');
                isSyncing = false;
                return;
            }

            console.log(`[djust Offline] Syncing ${events.length} events`);

            // Send events to server via WebSocket
            if (window.liveViewWS && window.liveViewWS.ws && window.liveViewWS.ws.readyState === WebSocket.OPEN) {
                window.liveViewWS.sendMessage({
                    type: 'sync_queued_events',
                    events: events.map(e => ({
                        name: e.name,
                        params: e.params,
                        timestamp: e.timestamp
                    }))
                });

                // Clear synced events
                await clearQueuedEvents(events.map(e => e.id));
                console.log('[djust Offline] Events synced and cleared');
            } else {
                console.warn('[djust Offline] WebSocket not connected, cannot sync');
            }
        } catch (error) {
            console.error('[djust Offline] Sync failed:', error);
        } finally {
            isSyncing = false;
        }
    }

    // ========================================================================
    // State Persistence
    // ========================================================================

    /**
     * Save current view state to IndexedDB
     * @param {object} state - State to save (optional, will be gathered from view)
     */
    async function persistViewState(state = null) {
        if (!offlineConfig || !offlineConfig.persistState) {
            return;
        }

        const container = document.querySelector('[data-djust-view]');
        const viewId = container ? container.dataset.djustView : 'default';

        // If no state provided, try to get it from the server response
        if (!state) {
            console.log('[djust Offline] No state provided for persistence');
            return;
        }

        try {
            await saveState(viewId, state);
        } catch (error) {
            console.error('[djust Offline] Failed to persist state:', error);
        }
    }

    /**
     * Restore view state from IndexedDB
     * @returns {Promise<object|null>}
     */
    async function restoreViewState() {
        if (!offlineConfig || !offlineConfig.persistState) {
            return null;
        }

        const container = document.querySelector('[data-djust-view]');
        const viewId = container ? container.dataset.djustView : 'default';

        try {
            const state = await loadState(viewId);
            if (state) {
                // Send restored state to server
                if (window.liveViewWS && window.liveViewWS.ws && window.liveViewWS.ws.readyState === WebSocket.OPEN) {
                    window.liveViewWS.sendMessage({
                        type: 'restore_state',
                        state: state
                    });
                }
            }
            return state;
        } catch (error) {
            console.error('[djust Offline] Failed to restore state:', error);
            return null;
        }
    }

    // ========================================================================
    // Service Worker Communication
    // ========================================================================

    /**
     * Handle service worker update available
     * @param {ServiceWorkerRegistration} registration
     */
    function onUpdateAvailable(registration) {
        console.log('[djust Offline] New service worker version available');

        // Dispatch event for app to handle
        window.dispatchEvent(new CustomEvent('djust:sw-update-available', {
            detail: { registration }
        }));

        // Show update notification (can be customized by app)
        if (!document.getElementById('djust-sw-update-toast')) {
            const toast = document.createElement('div');
            toast.id = 'djust-sw-update-toast';
            toast.innerHTML = `
                <div style="
                    position: fixed;
                    bottom: 20px;
                    left: 20px;
                    background: #1e40af;
                    color: white;
                    padding: 12px 16px;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                    font-size: 14px;
                    z-index: 99999;
                    display: flex;
                    align-items: center;
                    gap: 12px;
                ">
                    <span>A new version is available</span>
                    <button onclick="window.djust.offline.updateServiceWorker()" style="
                        background: white;
                        color: #1e40af;
                        border: none;
                        padding: 6px 12px;
                        border-radius: 4px;
                        cursor: pointer;
                        font-weight: 500;
                    ">Update</button>
                    <button onclick="this.parentElement.parentElement.remove()" style="
                        background: transparent;
                        color: white;
                        border: none;
                        padding: 6px;
                        cursor: pointer;
                        opacity: 0.7;
                    ">✕</button>
                </div>
            `;
            document.body.appendChild(toast);
        }
    }

    /**
     * Trigger service worker update
     */
    function updateServiceWorker() {
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.getRegistration().then(registration => {
                if (registration && registration.waiting) {
                    registration.waiting.postMessage({ type: 'SKIP_WAITING' });
                }
            });
        }

        // Remove update toast
        const toast = document.getElementById('djust-sw-update-toast');
        if (toast) toast.remove();

        // Reload page after a short delay
        setTimeout(() => window.location.reload(), 500);
    }

    /**
     * Get service worker version
     * @returns {Promise<string>}
     */
    async function getServiceWorkerVersion() {
        if (!('serviceWorker' in navigator)) {
            return null;
        }

        const registration = await navigator.serviceWorker.ready;
        if (!registration.active) {
            return null;
        }

        return new Promise((resolve) => {
            const channel = new MessageChannel();
            channel.port1.onmessage = (event) => {
                resolve(event.data.version);
            };
            registration.active.postMessage({ type: 'GET_VERSION' }, [channel.port2]);
        });
    }

    // ========================================================================
    // Initialization
    // ========================================================================

    /**
     * Initialize offline support
     * @param {object} config - Offline configuration from server
     */
    function init(config) {
        offlineConfig = config;
        console.log('[djust Offline] Initialized with config:', config);

        // Set initial online status
        updateOnlineStatus(navigator.onLine, false);

        // Open database
        openDatabase().catch(err => {
            console.error('[djust Offline] Failed to open database:', err);
        });

        // Update queued count
        updateQueuedCount();
    }

    // Listen for network status changes
    window.addEventListener('online', () => {
        console.log('[djust Offline] Network: online');
        updateOnlineStatus(true);
    });

    window.addEventListener('offline', () => {
        console.log('[djust Offline] Network: offline');
        updateOnlineStatus(false);
    });

    // Listen for WebSocket connection status
    window.addEventListener('djust:ws-connected', () => {
        updateOnlineStatus(isOnline, true);
    });

    window.addEventListener('djust:ws-disconnected', () => {
        updateOnlineStatus(isOnline, false);
    });

    // Listen for service worker messages
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.addEventListener('message', (event) => {
            if (event.data && event.data.type === 'DJUST_SYNC_EVENTS') {
                syncQueuedEvents();
            }
        });

        // Handle controller change (new SW activated)
        navigator.serviceWorker.addEventListener('controllerchange', () => {
            console.log('[djust Offline] Service worker controller changed');
        });
    }

    // Listen for page visibility to sync on return
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && isOnline && wsConnected) {
            syncQueuedEvents();
        }
    });

    // ========================================================================
    // Export to window.djust
    // ========================================================================

    window.djust = window.djust || {};
    window.djust.offline = {
        // Status
        get isOnline() { return isOnline && wsConnected; },
        get isNetworkOnline() { return isOnline; },
        get isWsConnected() { return wsConnected; },

        // Configuration
        init,
        getConfig: () => offlineConfig,

        // Event queueing
        queueEvent: handleOfflineEvent,
        getQueuedEvents,
        syncQueuedEvents,
        clearQueuedEvents,

        // State persistence
        saveState: persistViewState,
        loadState: restoreViewState,
        clearState,

        // Service worker
        onUpdateAvailable,
        updateServiceWorker,
        getServiceWorkerVersion,

        // Internal (for WebSocket integration)
        _updateOnlineStatus: updateOnlineStatus,
    };

    // Set initial body class
    document.body.classList.add(navigator.onLine ? 'djust-online' : 'djust-offline');

    console.log('[djust Offline] Module loaded');

})();
