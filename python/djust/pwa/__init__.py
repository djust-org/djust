"""
djust.pwa — Progressive Web App support for djust LiveViews.

Enables offline-first djust applications with:
- Service worker integration for caching rendered HTML
- Optimistic state persistence to IndexedDB  
- Auto-sync when connection restored
- `dj-offline` directive for offline-aware UI states

Quick Start::

    from djust import LiveView
    from djust.pwa import PWAMixin, OfflineMixin

    class TaskListView(PWAMixin, OfflineMixin, LiveView):
        template_name = 'task_list.html'
        offline_storage = 'tasks'  # IndexedDB store name
        
        def mount(self, request, **kwargs):
            self.tasks = self.get_cached_or_fetch('tasks', Task.objects.all())
        
        def add_task(self, title):
            # Works offline - queued for sync when online
            task = self.create_offline(Task, title=title)
            self.tasks.append(task)

Configuration in settings.py::

    DJUST_CONFIG = {
        # PWA configuration
        'PWA_ENABLED': True,
        'PWA_NAME': 'My App',
        'PWA_SHORT_NAME': 'MyApp',
        'PWA_THEME_COLOR': '#000000',
        'PWA_BACKGROUND_COLOR': '#ffffff',
        'PWA_DISPLAY': 'standalone',  # 'fullscreen', 'minimal-ui', 'browser'
        
        # Service worker
        'PWA_SERVICE_WORKER_PATH': '/sw.js',
        'PWA_CACHE_STRATEGY': 'cache_first',  # 'network_first', 'stale_while_revalidate'
        'PWA_CACHE_DURATION': 86400,  # 24 hours
        
        # Offline storage
        'PWA_OFFLINE_STORAGE': 'indexeddb',  # 'localstorage', 'sessionstorage'
        'PWA_SYNC_ENDPOINT': '/api/sync/',
        'PWA_MAX_OFFLINE_ACTIONS': 100,
        
        # Network detection
        'PWA_CONNECTION_TIMEOUT': 5000,  # ms
        'PWA_RETRY_INTERVAL': 30000,  # 30 seconds
    }

Template usage::

    <!-- Offline indicator -->
    <div dj-offline="show" class="offline-banner">
        You're offline. Changes will sync when connected.
    </div>
    
    <!-- Offline-aware actions -->
    <button dj-offline="disable" dj-click="delete_item">Delete</button>
    <button dj-offline="queue" dj-click="save_draft">Save Draft</button>

Service Worker registration::

    <!-- In your base template -->
    {% load pwa_tags %}
    {% pwa_manifest %}
    {% pwa_service_worker %}
"""

from .mixins import PWAMixin, OfflineMixin, SyncMixin
from .manifest import PWAManifestGenerator, manifest_view
from .service_worker import ServiceWorkerGenerator, service_worker_view
from .storage import (
    OfflineStorage,
    IndexedDBStorage,
    LocalStorage,
    SyncQueue,
    OfflineAction,
    get_storage_backend,
)
from .sync import (
    SyncManager,
    ConflictResolver,
    MergeStrategy,
    register_sync_handler,
    sync_endpoint_view,
)
from .utils import (
    is_online,
    get_connection_info,
    estimate_sync_time,
    compress_state,
    decompress_state,
)

__all__ = [
    # Mixins
    'PWAMixin',
    'OfflineMixin', 
    'SyncMixin',
    
    # Manifest
    'PWAManifestGenerator',
    'manifest_view',
    
    # Service Worker
    'ServiceWorkerGenerator',
    'service_worker_view',
    
    # Storage
    'OfflineStorage',
    'IndexedDBStorage',
    'LocalStorage',
    'SyncQueue',
    'OfflineAction',
    'get_storage_backend',
    
    # Sync
    'SyncManager',
    'ConflictResolver',
    'MergeStrategy',
    'register_sync_handler',
    'sync_endpoint_view',
    
    # Utils
    'is_online',
    'get_connection_info',
    'estimate_sync_time',
    'compress_state',
    'decompress_state',
]