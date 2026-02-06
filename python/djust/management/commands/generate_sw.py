"""
Django management command to generate a service worker for PWA/offline support.

Usage:
    python manage.py generate_sw
    python manage.py generate_sw --cache-static --cache-templates
    python manage.py generate_sw --output static/sw.js
"""

import os
import time
from datetime import datetime
from typing import List, Set

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template import Template, Context


class Command(BaseCommand):
    help = "Generate a service worker for PWA/offline support"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            default=None,
            help="Output path for sw.js (default: STATIC_ROOT/sw.js or staticfiles/sw.js)",
        )
        parser.add_argument(
            "--cache-static",
            action="store_true",
            help="Include static files in the service worker cache",
        )
        parser.add_argument(
            "--cache-templates",
            action="store_true",
            help="Include offline templates in the service worker cache",
        )
        parser.add_argument(
            "--version",
            type=str,
            default=None,
            help="Cache version string (default: timestamp)",
        )
        parser.add_argument(
            "--static-extensions",
            type=str,
            default="js,css,png,jpg,jpeg,gif,svg,woff,woff2,ico",
            help="File extensions to cache from static files (comma-separated)",
        )
        parser.add_argument(
            "--exclude-patterns",
            type=str,
            default="admin,debug",
            help="Patterns to exclude from caching (comma-separated)",
        )

    def handle(self, *args, **options):
        # Determine output path
        output_path = options["output"]
        if not output_path:
            # Try STATIC_ROOT first, then fall back to staticfiles directory
            static_root = getattr(settings, "STATIC_ROOT", None)
            if static_root:
                output_path = os.path.join(static_root, "sw.js")
            else:
                # Use the first STATICFILES_DIRS or default
                staticfiles_dirs = getattr(settings, "STATICFILES_DIRS", [])
                if staticfiles_dirs:
                    output_path = os.path.join(staticfiles_dirs[0], "sw.js")
                else:
                    output_path = os.path.join(settings.BASE_DIR, "static", "sw.js")

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Generate version
        version = options["version"] or str(int(time.time()))

        # Collect static assets
        static_assets: List[str] = []
        if options["cache_static"]:
            static_assets = self._collect_static_assets(
                extensions=options["static_extensions"].split(","),
                exclude_patterns=options["exclude_patterns"].split(","),
            )

        # Collect template URLs
        template_urls: List[str] = []
        if options["cache_templates"]:
            template_urls = self._collect_template_urls()

        # Generate service worker content
        sw_content = self._generate_service_worker(
            version=version,
            static_assets=static_assets,
            template_urls=template_urls,
        )

        # Write to file
        with open(output_path, "w") as f:
            f.write(sw_content)

        self.stdout.write(self.style.SUCCESS(f"Generated service worker at: {output_path}"))
        self.stdout.write(f"  Version: {version}")
        self.stdout.write(f"  Static assets: {len(static_assets)}")
        self.stdout.write(f"  Template URLs: {len(template_urls)}")

        # Generate manifest.json if it doesn't exist
        manifest_path = os.path.join(output_dir, "manifest.json") if output_dir else "manifest.json"
        if not os.path.exists(manifest_path):
            self._generate_manifest(manifest_path)
            self.stdout.write(self.style.SUCCESS(f"Generated manifest at: {manifest_path}"))

    def _collect_static_assets(
        self, extensions: List[str], exclude_patterns: List[str]
    ) -> List[str]:
        """Collect static file URLs to cache."""
        assets: Set[str] = set()
        extensions = [ext.strip().lower() for ext in extensions]
        exclude_patterns = [p.strip().lower() for p in exclude_patterns if p.strip()]

        # Get static URL prefix
        static_url = getattr(settings, "STATIC_URL", "/static/")

        # Search in STATICFILES_DIRS
        staticfiles_dirs = getattr(settings, "STATICFILES_DIRS", [])
        for static_dir in staticfiles_dirs:
            if os.path.exists(static_dir):
                for root, dirs, files in os.walk(static_dir):
                    for file in files:
                        file_lower = file.lower()
                        ext = file_lower.split(".")[-1] if "." in file_lower else ""

                        if ext not in extensions:
                            continue

                        # Get relative path
                        rel_path = os.path.relpath(os.path.join(root, file), static_dir)

                        # Check exclude patterns
                        if any(p in rel_path.lower() for p in exclude_patterns):
                            continue

                        # Build URL
                        url = f"{static_url}{rel_path}".replace("\\", "/")
                        assets.add(url)

        # Search in STATIC_ROOT if it exists
        static_root = getattr(settings, "STATIC_ROOT", None)
        if static_root and os.path.exists(static_root):
            for root, dirs, files in os.walk(static_root):
                for file in files:
                    file_lower = file.lower()
                    ext = file_lower.split(".")[-1] if "." in file_lower else ""

                    if ext not in extensions:
                        continue

                    rel_path = os.path.relpath(os.path.join(root, file), static_root)

                    if any(p in rel_path.lower() for p in exclude_patterns):
                        continue

                    url = f"{static_url}{rel_path}".replace("\\", "/")
                    assets.add(url)

        # Always include djust client.js
        assets.add(f"{static_url}djust/client.js")

        return sorted(list(assets))

    def _collect_template_urls(self) -> List[str]:
        """Collect offline template URLs from registered views."""
        urls: List[str] = []

        # Get all URL patterns and look for LiveViews with offline_template
        try:
            from django.urls import get_resolver
            from djust.offline import OfflineMixin

            resolver = get_resolver()

            def extract_views(patterns, prefix=""):
                for pattern in patterns:
                    if hasattr(pattern, "url_patterns"):
                        # Nested URLResolver
                        new_prefix = prefix + str(pattern.pattern)
                        extract_views(pattern.url_patterns, new_prefix)
                    elif hasattr(pattern, "callback"):
                        # URLPattern
                        view = pattern.callback
                        if hasattr(view, "view_class"):
                            view_class = view.view_class
                            if (
                                isinstance(view_class, type)
                                and issubclass(view_class, OfflineMixin)
                                and getattr(view_class, "offline_template", None)
                            ):
                                url_path = prefix + str(pattern.pattern)
                                url_path = "/" + url_path.lstrip("^").rstrip("$")
                                urls.append(url_path)

            extract_views(resolver.url_patterns)

        except Exception as e:
            self.stderr.write(f"Warning: Could not collect template URLs: {e}")

        return urls

    def _generate_service_worker(
        self,
        version: str,
        static_assets: List[str],
        template_urls: List[str],
    ) -> str:
        """Generate the service worker JavaScript content."""
        template_str = """{% autoescape off %}// djust Service Worker - Generated by `python manage.py generate_sw`
// Version: {{ version }}
// Generated: {{ generated_at }}

const CACHE_NAME = 'djust-cache-v{{ version }}';
const OFFLINE_URL = '/offline/';

// Static assets to cache
const STATIC_ASSETS = [
{% for asset in static_assets %}    '{{ asset }}',
{% endfor %}];

// Template URLs to cache for offline
const TEMPLATE_URLS = [
{% for url in template_urls %}    '{{ url }}',
{% endfor %}];

// All URLs to precache
const PRECACHE_URLS = [...STATIC_ASSETS, ...TEMPLATE_URLS];

// Install event - cache assets
self.addEventListener('install', (event) => {
    console.log('[djust SW] Installing version {{ version }}...');
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[djust SW] Caching', PRECACHE_URLS.length, 'assets');
                // Cache assets individually to handle failures gracefully
                return Promise.allSettled(
                    PRECACHE_URLS.map(url =>
                        cache.add(url).catch(err => {
                            console.warn('[djust SW] Failed to cache:', url, err);
                        })
                    )
                );
            })
            .then(() => self.skipWaiting())
    );
});

// Activate event - clean old caches
self.addEventListener('activate', (event) => {
    console.log('[djust SW] Activating...');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName.startsWith('djust-cache-') && cacheName !== CACHE_NAME) {
                        console.log('[djust SW] Deleting old cache:', cacheName);
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

    // Skip chrome-extension and other non-http(s) requests
    if (!event.request.url.startsWith('http')) {
        return;
    }

    event.respondWith(
        caches.match(event.request)
            .then((cachedResponse) => {
                if (cachedResponse) {
                    // Return cached response and update cache in background
                    event.waitUntil(
                        fetch(event.request)
                            .then(response => {
                                if (response && response.status === 200) {
                                    const responseClone = response.clone();
                                    caches.open(CACHE_NAME).then(cache => {
                                        cache.put(event.request, responseClone);
                                    });
                                }
                            })
                            .catch(() => {})
                    );
                    return cachedResponse;
                }

                return fetch(event.request)
                    .then((response) => {
                        // Don't cache non-successful responses
                        if (!response || response.status !== 200 || response.type !== 'basic') {
                            return response;
                        }

                        // Cache the response
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
                        return new Response('Offline', { status: 503 });
                    });
            })
    );
});

// Background sync for queued events
self.addEventListener('sync', (event) => {
    if (event.tag === 'djust-event-sync') {
        console.log('[djust SW] Background sync triggered');
        event.waitUntil(notifyClientsToSync());
    }
});

async function notifyClientsToSync() {
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach(client => {
        client.postMessage({
            type: 'DJUST_SYNC_EVENTS'
        });
    });
}

// Listen for messages from the client
self.addEventListener('message', (event) => {
    if (event.data) {
        switch (event.data.type) {
            case 'SKIP_WAITING':
                self.skipWaiting();
                break;
            case 'GET_VERSION':
                event.ports[0].postMessage({ version: '{{ version }}' });
                break;
            case 'CLEAR_CACHE':
                caches.delete(CACHE_NAME).then(() => {
                    event.ports[0].postMessage({ cleared: true });
                });
                break;
        }
    }
});

// Push notification support (optional)
self.addEventListener('push', (event) => {
    if (event.data) {
        const data = event.data.json();
        const options = {
            body: data.body || '',
            icon: data.icon || '/static/icons/icon-192.png',
            badge: data.badge || '/static/icons/badge.png',
            data: data.data || {},
        };
        event.waitUntil(
            self.registration.showNotification(data.title || 'djust', options)
        );
    }
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    if (event.notification.data && event.notification.data.url) {
        event.waitUntil(
            clients.openWindow(event.notification.data.url)
        );
    }
});

console.log('[djust SW] Service worker loaded, version {{ version }}');
{% endautoescape %}"""
        template = Template(template_str)
        context = Context(
            {
                "version": version,
                "generated_at": datetime.now().isoformat(),
                "static_assets": static_assets,
                "template_urls": template_urls,
            }
        )
        return template.render(context)

    def _generate_manifest(self, output_path: str) -> None:
        """Generate a basic PWA manifest.json file."""
        import json

        manifest = {
            "name": getattr(settings, "DJUST_PWA_NAME", "djust App"),
            "short_name": getattr(settings, "DJUST_PWA_SHORT_NAME", "djust"),
            "description": getattr(
                settings, "DJUST_PWA_DESCRIPTION", "A djust-powered progressive web app"
            ),
            "start_url": "/",
            "display": "standalone",
            "background_color": getattr(settings, "DJUST_PWA_BACKGROUND_COLOR", "#ffffff"),
            "theme_color": getattr(settings, "DJUST_PWA_THEME_COLOR", "#007bff"),
            "icons": [
                {
                    "src": "/static/icons/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                },
                {
                    "src": "/static/icons/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                },
            ],
        }

        with open(output_path, "w") as f:
            json.dump(manifest, f, indent=2)
