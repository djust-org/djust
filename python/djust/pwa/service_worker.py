"""
Service worker generation and management for djust PWA support.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from django.http import HttpResponse, HttpRequest
from django.conf import settings

logger = logging.getLogger(__name__)


class ServiceWorkerGenerator:
    """
    Generates service workers for djust PWA applications.
    
    Provides caching strategies, offline support, and background sync
    capabilities for progressive web apps.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default service worker configuration."""
        try:
            djust_config = getattr(settings, 'DJUST_CONFIG', {})
            return {
                'cache_name': djust_config.get('PWA_CACHE_NAME', 'djust-cache-v1'),
                'cache_strategy': djust_config.get('PWA_CACHE_STRATEGY', 'cache_first'),
                'cache_duration': djust_config.get('PWA_CACHE_DURATION', 86400),  # 24 hours
                'offline_page': djust_config.get('PWA_OFFLINE_PAGE', '/offline/'),
                'sync_endpoint': djust_config.get('PWA_SYNC_ENDPOINT', '/api/sync/'),
                'precache_urls': djust_config.get('PWA_PRECACHE_URLS', []),
                'exclude_patterns': djust_config.get('PWA_EXCLUDE_PATTERNS', ['/admin/', '/api/auth/']),
                'enable_background_sync': djust_config.get('PWA_ENABLE_BACKGROUND_SYNC', True),
                'enable_push_notifications': djust_config.get('PWA_ENABLE_PUSH_NOTIFICATIONS', False),
                'version': djust_config.get('PWA_VERSION', '1.0.0'),
            }
        except Exception as e:
            logger.error(f"Error loading service worker config: {e}")
            return self._get_minimal_config()
    
    def _get_minimal_config(self) -> Dict[str, Any]:
        """Get minimal service worker configuration."""
        return {
            'cache_name': 'djust-cache-v1',
            'cache_strategy': 'cache_first',
            'cache_duration': 86400,
            'offline_page': '/offline/',
            'sync_endpoint': '/api/sync/',
            'precache_urls': ['/'],
            'exclude_patterns': ['/admin/'],
            'enable_background_sync': True,
            'enable_push_notifications': False,
            'version': '1.0.0',
        }
    
    def generate_service_worker(self) -> str:
        """
        Generate complete service worker JavaScript code.
        
        Returns:
            Service worker JavaScript code as string
        """
        sw_code = self._generate_header()
        sw_code += self._generate_constants()
        sw_code += self._generate_install_handler()
        sw_code += self._generate_activate_handler()
        sw_code += self._generate_fetch_handler()
        
        if self.config['enable_background_sync']:
            sw_code += self._generate_sync_handler()
        
        if self.config['enable_push_notifications']:
            sw_code += self._generate_push_handler()
        
        sw_code += self._generate_message_handler()
        sw_code += self._generate_helper_functions()
        
        return sw_code
    
    def _generate_header(self) -> str:
        """Generate service worker header with metadata."""
        return f'''/* 
 * djust Service Worker
 * Version: {self.config['version']}
 * Generated automatically
 * Cache Strategy: {self.config['cache_strategy']}
 */

'''
    
    def _generate_constants(self) -> str:
        """Generate service worker constants."""
        precache_urls = json.dumps(self.config['precache_urls'])
        exclude_patterns = json.dumps(self.config['exclude_patterns'])
        
        return f'''
const CACHE_NAME = '{self.config['cache_name']}';
const CACHE_DURATION = {self.config['cache_duration']} * 1000; // Convert to milliseconds
const OFFLINE_PAGE = '{self.config['offline_page']}';
const SYNC_ENDPOINT = '{self.config['sync_endpoint']}';
const CACHE_STRATEGY = '{self.config['cache_strategy']}';
const PRECACHE_URLS = {precache_urls};
const EXCLUDE_PATTERNS = {exclude_patterns};
const SW_VERSION = '{self.config['version']}';

'''
    
    def _generate_install_handler(self) -> str:
        """Generate service worker install event handler."""
        return '''
// Install event - cache initial resources
self.addEventListener('install', event => {
    console.log('[SW] Installing service worker version:', SW_VERSION);
    
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('[SW] Caching precache URLs:', PRECACHE_URLS);
                return cache.addAll(PRECACHE_URLS);
            })
            .then(() => {
                console.log('[SW] Installation complete');
                // Skip waiting to activate immediately
                return self.skipWaiting();
            })
            .catch(error => {
                console.error('[SW] Installation failed:', error);
            })
    );
});

'''
    
    def _generate_activate_handler(self) -> str:
        """Generate service worker activate event handler."""
        return '''
// Activate event - clean up old caches
self.addEventListener('activate', event => {
    console.log('[SW] Activating service worker version:', SW_VERSION);
    
    event.waitUntil(
        caches.keys()
            .then(cacheNames => {
                return Promise.all(
                    cacheNames
                        .filter(cacheName => cacheName !== CACHE_NAME)
                        .map(cacheName => {
                            console.log('[SW] Deleting old cache:', cacheName);
                            return caches.delete(cacheName);
                        })
                );
            })
            .then(() => {
                console.log('[SW] Activation complete');
                // Claim all clients immediately
                return self.clients.claim();
            })
            .catch(error => {
                console.error('[SW] Activation failed:', error);
            })
    );
});

'''
    
    def _generate_fetch_handler(self) -> str:
        """Generate service worker fetch event handler."""
        if self.config['cache_strategy'] == 'cache_first':
            return self._generate_cache_first_fetch()
        elif self.config['cache_strategy'] == 'network_first':
            return self._generate_network_first_fetch()
        elif self.config['cache_strategy'] == 'stale_while_revalidate':
            return self._generate_stale_while_revalidate_fetch()
        else:
            return self._generate_cache_first_fetch()  # Default
    
    def _generate_cache_first_fetch(self) -> str:
        """Generate cache-first fetch strategy."""
        return '''
// Fetch event - cache first strategy
self.addEventListener('fetch', event => {
    const request = event.request;
    
    // Skip non-GET requests
    if (request.method !== 'GET') {
        return;
    }
    
    // Skip excluded patterns
    const url = new URL(request.url);
    if (shouldExcludeFromCache(url.pathname)) {
        return;
    }
    
    event.respondWith(
        caches.match(request)
            .then(cachedResponse => {
                if (cachedResponse) {
                    // Check if cached response is still fresh
                    const cachedTime = parseInt(cachedResponse.headers.get('sw-cached-time') || '0');
                    const now = Date.now();
                    
                    if (now - cachedTime < CACHE_DURATION) {
                        console.log('[SW] Cache hit (fresh):', request.url);
                        return cachedResponse;
                    } else {
                        console.log('[SW] Cache hit (stale):', request.url);
                        // Return cached but fetch new version in background
                        fetchAndCache(request);
                        return cachedResponse;
                    }
                }
                
                // Not in cache, fetch from network
                console.log('[SW] Cache miss, fetching:', request.url);
                return fetchAndCache(request);
            })
            .catch(error => {
                console.error('[SW] Fetch failed:', error);
                
                // Return offline page for navigation requests
                if (request.mode === 'navigate') {
                    return caches.match(OFFLINE_PAGE);
                }
                
                // Return generic offline response
                return new Response('Offline', { status: 503 });
            })
    );
});

'''
    
    def _generate_network_first_fetch(self) -> str:
        """Generate network-first fetch strategy."""
        return '''
// Fetch event - network first strategy
self.addEventListener('fetch', event => {
    const request = event.request;
    
    // Skip non-GET requests
    if (request.method !== 'GET') {
        return;
    }
    
    // Skip excluded patterns
    const url = new URL(request.url);
    if (shouldExcludeFromCache(url.pathname)) {
        return;
    }
    
    event.respondWith(
        fetch(request)
            .then(response => {
                console.log('[SW] Network success:', request.url);
                
                // Cache successful responses
                if (response && response.status === 200) {
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME)
                        .then(cache => {
                            // Add cache timestamp
                            const headers = new Headers(responseToCache.headers);
                            headers.set('sw-cached-time', Date.now().toString());
                            
                            const responseWithTimestamp = new Response(responseToCache.body, {
                                status: responseToCache.status,
                                statusText: responseToCache.statusText,
                                headers: headers
                            });
                            
                            cache.put(request, responseWithTimestamp);
                        });
                }
                
                return response;
            })
            .catch(error => {
                console.log('[SW] Network failed, trying cache:', request.url);
                
                // Network failed, try cache
                return caches.match(request)
                    .then(cachedResponse => {
                        if (cachedResponse) {
                            console.log('[SW] Cache fallback:', request.url);
                            return cachedResponse;
                        }
                        
                        // No cache, return offline page for navigation
                        if (request.mode === 'navigate') {
                            return caches.match(OFFLINE_PAGE);
                        }
                        
                        throw error;
                    });
            })
    );
});

'''
    
    def _generate_stale_while_revalidate_fetch(self) -> str:
        """Generate stale-while-revalidate fetch strategy."""
        return '''
// Fetch event - stale while revalidate strategy
self.addEventListener('fetch', event => {
    const request = event.request;
    
    // Skip non-GET requests
    if (request.method !== 'GET') {
        return;
    }
    
    // Skip excluded patterns
    const url = new URL(request.url);
    if (shouldExcludeFromCache(url.pathname)) {
        return;
    }
    
    event.respondWith(
        caches.open(CACHE_NAME)
            .then(cache => {
                return cache.match(request)
                    .then(cachedResponse => {
                        // Start network request regardless of cache hit
                        const fetchPromise = fetch(request)
                            .then(networkResponse => {
                                if (networkResponse && networkResponse.status === 200) {
                                    const responseToCache = networkResponse.clone();
                                    
                                    // Add cache timestamp
                                    const headers = new Headers(responseToCache.headers);
                                    headers.set('sw-cached-time', Date.now().toString());
                                    
                                    const responseWithTimestamp = new Response(responseToCache.body, {
                                        status: responseToCache.status,
                                        statusText: responseToCache.statusText,
                                        headers: headers
                                    });
                                    
                                    cache.put(request, responseWithTimestamp);
                                }
                                return networkResponse;
                            })
                            .catch(error => {
                                console.log('[SW] Network failed:', request.url, error);
                                return null;
                            });
                        
                        // Return cached version immediately if available
                        if (cachedResponse) {
                            console.log('[SW] Serving from cache (revalidating):', request.url);
                            return cachedResponse;
                        }
                        
                        // No cache, wait for network
                        console.log('[SW] No cache, waiting for network:', request.url);
                        return fetchPromise
                            .then(response => {
                                if (response) {
                                    return response;
                                }
                                
                                // Network failed and no cache
                                if (request.mode === 'navigate') {
                                    return cache.match(OFFLINE_PAGE);
                                }
                                
                                return new Response('Offline', { status: 503 });
                            });
                    });
            })
    );
});

'''
    
    def _generate_sync_handler(self) -> str:
        """Generate background sync event handler."""
        return '''
// Background sync event
self.addEventListener('sync', event => {
    console.log('[SW] Background sync triggered:', event.tag);
    
    if (event.tag === 'djust-offline-sync') {
        event.waitUntil(syncOfflineData());
    }
});

// Sync offline data with server
async function syncOfflineData() {
    try {
        console.log('[SW] Starting offline data sync');
        
        // Get offline actions from IndexedDB
        const db = await openIndexedDB();
        const actions = await getOfflineActions(db);
        
        if (actions.length === 0) {
            console.log('[SW] No offline actions to sync');
            return;
        }
        
        console.log(`[SW] Syncing ${actions.length} offline actions`);
        
        // Send batch sync request
        const response = await fetch(SYNC_ENDPOINT, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                actions: actions,
                version: SW_VERSION
            })
        });
        
        if (response.ok) {
            const result = await response.json();
            console.log('[SW] Sync successful:', result);
            
            // Clear synced actions
            await clearSyncedActions(db, result.synced_ids || []);
            
            // Notify all clients of sync completion
            const clients = await self.clients.matchAll();
            clients.forEach(client => {
                client.postMessage({
                    type: 'SYNC_COMPLETE',
                    data: result
                });
            });
        } else {
            console.error('[SW] Sync failed:', response.status, response.statusText);
        }
    } catch (error) {
        console.error('[SW] Sync error:', error);
    }
}

'''
    
    def _generate_push_handler(self) -> str:
        """Generate push notification event handler."""
        return '''
// Push notification event
self.addEventListener('push', event => {
    console.log('[SW] Push received:', event);
    
    let data = {};
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data = { title: 'Notification', body: event.data.text() };
        }
    }
    
    const options = {
        body: data.body || 'You have a new notification',
        icon: data.icon || '/static/icons/icon-192x192.png',
        badge: data.badge || '/static/icons/badge-72x72.png',
        image: data.image,
        data: data.data || {},
        tag: data.tag || 'djust-notification',
        requireInteraction: data.requireInteraction || false,
        actions: data.actions || []
    };
    
    event.waitUntil(
        self.registration.showNotification(
            data.title || 'djust App',
            options
        )
    );
});

// Notification click event
self.addEventListener('notificationclick', event => {
    console.log('[SW] Notification clicked:', event);
    
    event.notification.close();
    
    // Handle action clicks
    if (event.action) {
        console.log('[SW] Action clicked:', event.action);
        // Handle specific actions
    }
    
    // Open/focus app
    event.waitUntil(
        clients.matchAll({ type: 'window' })
            .then(clientList => {
                // Try to focus existing window
                for (let client of clientList) {
                    if (client.url === '/' && 'focus' in client) {
                        return client.focus();
                    }
                }
                
                // Open new window
                if (clients.openWindow) {
                    return clients.openWindow('/');
                }
            })
    );
});

'''
    
    def _generate_message_handler(self) -> str:
        """Generate message event handler for client communication."""
        return '''
// Message event - handle messages from clients
self.addEventListener('message', event => {
    console.log('[SW] Message received:', event.data);
    
    const { type, data } = event.data;
    
    switch (type) {
        case 'SKIP_WAITING':
            self.skipWaiting();
            break;
            
        case 'CACHE_URLS':
            if (data.urls && Array.isArray(data.urls)) {
                caches.open(CACHE_NAME)
                    .then(cache => cache.addAll(data.urls))
                    .then(() => {
                        event.ports[0].postMessage({ success: true });
                    })
                    .catch(error => {
                        event.ports[0].postMessage({ success: false, error: error.message });
                    });
            }
            break;
            
        case 'CLEAR_CACHE':
            caches.delete(CACHE_NAME)
                .then(() => {
                    event.ports[0].postMessage({ success: true });
                })
                .catch(error => {
                    event.ports[0].postMessage({ success: false, error: error.message });
                });
            break;
            
        case 'GET_CACHE_STATUS':
            getCacheStatus()
                .then(status => {
                    event.ports[0].postMessage({ success: true, data: status });
                })
                .catch(error => {
                    event.ports[0].postMessage({ success: false, error: error.message });
                });
            break;
            
        default:
            console.log('[SW] Unknown message type:', type);
    }
});

'''
    
    def _generate_helper_functions(self) -> str:
        """Generate helper functions."""
        return '''
// Helper functions

function shouldExcludeFromCache(pathname) {
    return EXCLUDE_PATTERNS.some(pattern => pathname.startsWith(pattern));
}

async function fetchAndCache(request) {
    try {
        const response = await fetch(request);
        
        if (response && response.status === 200) {
            const responseToCache = response.clone();
            const cache = await caches.open(CACHE_NAME);
            
            // Add cache timestamp
            const headers = new Headers(responseToCache.headers);
            headers.set('sw-cached-time', Date.now().toString());
            
            const responseWithTimestamp = new Response(responseToCache.body, {
                status: responseToCache.status,
                statusText: responseToCache.statusText,
                headers: headers
            });
            
            await cache.put(request, responseWithTimestamp);
        }
        
        return response;
    } catch (error) {
        console.error('[SW] Fetch and cache failed:', error);
        throw error;
    }
}

async function getCacheStatus() {
    const cache = await caches.open(CACHE_NAME);
    const keys = await cache.keys();
    
    return {
        cache_name: CACHE_NAME,
        cached_urls: keys.length,
        version: SW_VERSION,
        strategy: CACHE_STRATEGY
    };
}

async function openIndexedDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('djust_offline_sync', 1);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => resolve(request.result);
        
        request.onupgradeneeded = event => {
            const db = event.target.result;
            if (!db.objectStoreNames.contains('sync_queue')) {
                db.createObjectStore('sync_queue', { keyPath: 'id' });
            }
        };
    });
}

async function getOfflineActions(db) {
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(['sync_queue'], 'readonly');
        const store = transaction.objectStore('sync_queue');
        const request = store.getAll();
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const actions = request.result.filter(action => action.status === 'pending');
            resolve(actions);
        };
    });
}

async function clearSyncedActions(db, syncedIds) {
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(['sync_queue'], 'readwrite');
        const store = transaction.objectStore('sync_queue');
        
        const deletePromises = syncedIds.map(id => {
            return new Promise((res, rej) => {
                const deleteRequest = store.delete(id);
                deleteRequest.onerror = () => rej(deleteRequest.error);
                deleteRequest.onsuccess = () => res();
            });
        });
        
        Promise.all(deletePromises)
            .then(() => resolve())
            .catch(reject);
    });
}

console.log('[SW] Service worker loaded, version:', SW_VERSION);
'''


def service_worker_view(request: HttpRequest) -> HttpResponse:
    """
    Django view that serves the service worker JavaScript file.
    
    Args:
        request: Django HTTP request
        
    Returns:
        HttpResponse with service worker JavaScript
    """
    try:
        generator = ServiceWorkerGenerator()
        sw_code = generator.generate_service_worker()
        
        response = HttpResponse(sw_code, content_type='application/javascript')
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        
        return response
    except Exception as e:
        logger.error(f"Error generating service worker: {e}")
        
        # Return minimal service worker
        minimal_sw = '''
console.log('[SW] Minimal service worker loaded');

self.addEventListener('install', event => {
    console.log('[SW] Installing');
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    console.log('[SW] Activating');
    event.waitUntil(self.clients.claim());
});
'''
        
        response = HttpResponse(minimal_sw, content_type='application/javascript')
        response['Cache-Control'] = 'no-cache'
        return response