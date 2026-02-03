"""
OfflineMixin — Progressive Web App and offline support for LiveView.

Provides offline detection, event queueing, and state persistence:

    class FormView(OfflineMixin, LiveView):
        offline_template = 'offline_form.html'  # Shown when offline
        sync_events = ['submit']  # Events that sync when back online
        persist_state = True  # Save state to IndexedDB when offline

Features:
- offline_template: Fallback template for offline rendering
- sync_events: List of event names to queue and sync when reconnected
- persist_state: Whether to save view state to IndexedDB
- get_offline_context(): Customize offline template context
- get_persistable_state(): Customize what state is persisted

Client-side directives:
- dj-offline-hide: Hide element when offline
- dj-offline-show: Show element when offline
- dj-offline-disable: Disable element when offline
- CSS classes: .djust-online, .djust-offline (on body)
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class OfflineMixin:
    """
    Mixin that adds offline/PWA support to a LiveView.

    Offline Template:
        Set offline_template to render a fallback UI when the user is offline.
        This template is cached by the service worker for offline access.

    Event Queueing:
        When offline, events listed in sync_events are queued in IndexedDB
        and automatically synced when the connection is restored.

    State Persistence:
        When persist_state is True, the view's state is saved to IndexedDB
        when going offline, and restored on reconnect.

    Configuration:
        - offline_template: Template to render when offline (optional)
        - sync_events: List of event names to queue when offline (default: [])
        - persist_state: Whether to persist state to IndexedDB (default: False)
        - persist_fields: Specific fields to persist (default: all serializable)
        - cache_templates: Additional templates to cache for offline (default: [])
    """

    # Configuration defaults
    offline_template: Optional[str] = None  # Fallback template for offline mode
    sync_events: List[str] = []  # Events to queue and sync when back online
    persist_state: bool = False  # Save state to IndexedDB when offline
    persist_fields: Optional[List[str]] = None  # Specific fields to persist (None = all)
    cache_templates: List[str] = []  # Additional templates to pre-cache

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._offline_config_sent: bool = False
        self._queued_events: List[Dict[str, Any]] = []

    def get_offline_config(self) -> Dict[str, Any]:
        """
        Get the offline configuration for this view.

        Sent to the client during mount to configure offline behavior.
        """
        config = {
            "enabled": bool(self.offline_template or self.sync_events or self.persist_state),
            "syncEvents": list(self.sync_events),
            "persistState": self.persist_state,
            "persistFields": self.persist_fields,
        }

        # Include offline template path if specified
        if self.offline_template:
            config["offlineTemplate"] = self.offline_template

        return config

    def get_offline_context(self) -> Dict[str, Any]:
        """
        Get context for the offline template.

        Override to customize what data is available in the offline template.
        Default returns basic view information.

        Returns:
            Dict of context variables for the offline template
        """
        return {
            "view": self,
            "offline": True,
        }

    def get_persistable_state(self) -> Dict[str, Any]:
        """
        Get the state to persist to IndexedDB.

        Override to customize what state is saved when going offline.
        By default, returns all serializable public attributes.

        Returns:
            Dict of state to persist
        """
        state = {}

        # Get all public attributes
        for key in dir(self):
            if key.startswith("_"):
                continue
            if key in ("request", "kwargs", "args"):
                continue

            # Skip methods and class attributes
            value = getattr(self, key, None)
            if callable(value):
                continue

            # Check persist_fields filter
            if self.persist_fields is not None and key not in self.persist_fields:
                continue

            # Only include JSON-serializable values
            try:
                json.dumps(value)
                state[key] = value
            except (TypeError, ValueError):
                # Skip non-serializable values
                logger.debug(f"[OfflineMixin] Skipping non-serializable field: {key}")
                pass

        return state

    def restore_persisted_state(self, state: Dict[str, Any]) -> None:
        """
        Restore state from IndexedDB after reconnect.

        Called automatically when a persisted state is sent from the client.
        Override to customize restoration behavior.

        Args:
            state: Dict of persisted state from IndexedDB
        """
        for key, value in state.items():
            # Only restore if field exists and is in persist_fields (if specified)
            if self.persist_fields is not None and key not in self.persist_fields:
                continue

            # Skip internal/protected attributes
            if key.startswith("_"):
                continue

            setattr(self, key, value)
            logger.debug(f"[OfflineMixin] Restored field: {key}")

    def process_queued_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process events that were queued while offline.

        Called automatically when the client sends queued events after reconnect.
        Override to customize how queued events are processed.

        Args:
            events: List of queued events, each with 'name' and 'params' keys

        Returns:
            List of results for each event (success/error info)
        """
        results = []

        for event in events:
            event_name = event.get("name")
            params = event.get("params", {})
            timestamp = event.get("timestamp")

            if not event_name:
                results.append({"success": False, "error": "Missing event name"})
                continue

            # Check if this event is in sync_events
            if self.sync_events and event_name not in self.sync_events:
                results.append({
                    "success": False,
                    "error": f"Event '{event_name}' not in sync_events",
                    "skipped": True,
                })
                continue

            # Try to call the handler
            handler = getattr(self, f"handle_{event_name}", None)
            if handler is None:
                handler = getattr(self, event_name, None)

            if handler and callable(handler):
                try:
                    handler(**params) if params else handler()
                    results.append({
                        "success": True,
                        "event": event_name,
                        "timestamp": timestamp,
                    })
                except Exception as e:
                    logger.exception(f"[OfflineMixin] Error processing queued event: {event_name}")
                    results.append({
                        "success": False,
                        "event": event_name,
                        "error": str(e),
                        "timestamp": timestamp,
                    })
            else:
                results.append({
                    "success": False,
                    "event": event_name,
                    "error": f"Handler not found for '{event_name}'",
                })

        return results

    def get_cache_urls(self) -> List[str]:
        """
        Get additional URLs to cache for offline access.

        Override to specify custom URLs that should be cached by the service worker.

        Returns:
            List of URL paths to cache
        """
        urls = []

        # Add offline template if specified
        if self.offline_template:
            urls.append(f"/djust/offline-template/{self.offline_template}")

        return urls

    def get_context_data(self, **kwargs):
        """
        Add offline configuration to template context.
        """
        context = super().get_context_data(**kwargs) if hasattr(super(), "get_context_data") else {}

        # Add offline config for client-side initialization
        context["djust_offline_config"] = self.get_offline_config()

        return context


# Service worker template content for generate_sw command
SERVICE_WORKER_TEMPLATE = '''
// djust Service Worker - Generated by `python manage.py generate_sw`
// Version: {{ version }}
// Generated: {{ generated_at }}

const CACHE_NAME = 'djust-cache-v{{ version }}';
const OFFLINE_URL = '/offline/';

// Static assets to cache
const STATIC_ASSETS = [
{% for asset in static_assets %}
    '{{ asset }}',
{% endfor %}
];

// Template URLs to cache for offline
const TEMPLATE_URLS = [
{% for url in template_urls %}
    '{{ url }}',
{% endfor %}
];

// All URLs to precache
const PRECACHE_URLS = [...STATIC_ASSETS, ...TEMPLATE_URLS, OFFLINE_URL];

// Install event - cache assets
self.addEventListener('install', (event) => {
    console.log('[ServiceWorker] Installing...');
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[ServiceWorker] Caching assets');
                return cache.addAll(PRECACHE_URLS);
            })
            .then(() => self.skipWaiting())
    );
});

// Activate event - clean old caches
self.addEventListener('activate', (event) => {
    console.log('[ServiceWorker] Activating...');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('[ServiceWorker] Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch event - serve from cache, fall back to network
self.addEventListener('fetch', (event) => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    // Skip WebSocket requests
    if (event.request.url.includes('/ws/')) {
        return;
    }

    event.respondWith(
        caches.match(event.request)
            .then((response) => {
                if (response) {
                    return response;
                }

                return fetch(event.request)
                    .then((response) => {
                        // Don't cache non-successful responses
                        if (!response || response.status !== 200 || response.type !== 'basic') {
                            return response;
                        }

                        // Clone the response
                        const responseToCache = response.clone();

                        caches.open(CACHE_NAME)
                            .then((cache) => {
                                cache.put(event.request, responseToCache);
                            });

                        return response;
                    })
                    .catch(() => {
                        // Return offline page for navigation requests
                        if (event.request.mode === 'navigate') {
                            return caches.match(OFFLINE_URL);
                        }
                    });
            })
    );
});

// Background sync for queued events
self.addEventListener('sync', (event) => {
    if (event.tag === 'djust-event-sync') {
        console.log('[ServiceWorker] Syncing queued events...');
        event.waitUntil(syncQueuedEvents());
    }
});

async function syncQueuedEvents() {
    // Get all clients and notify them to sync
    const clients = await self.clients.matchAll();
    clients.forEach(client => {
        client.postMessage({
            type: 'SYNC_QUEUED_EVENTS'
        });
    });
}

// Listen for messages from the client
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
'''

# PWA Manifest template
PWA_MANIFEST_TEMPLATE = {
    "name": "djust App",
    "short_name": "djust",
    "description": "A djust-powered progressive web app",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#ffffff",
    "theme_color": "#007bff",
    "icons": [
        {
            "src": "/static/icons/icon-192.png",
            "sizes": "192x192",
            "type": "image/png"
        },
        {
            "src": "/static/icons/icon-512.png",
            "sizes": "512x512",
            "type": "image/png"
        }
    ]
}
