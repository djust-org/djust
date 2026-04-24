"""
URL configuration for demo_project.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path, include

from djust.pwa import service_worker_view, manifest_view
from djust.sse import sse_urlpatterns

# Import the demo djust admin site — registers DemoProgressViewWidget +
# MaintenanceRequestDjustAdmin. Demonstrates v0.7.0 admin widget slots +
# @admin_action_with_progress.
from demo_project.djust_admin import djust_admin_site

urlpatterns = [
    path("admin/", admin.site.urls),
    # djust admin demo — exercises change_form_widgets / change_list_widgets
    # / BulkActionProgressWidget end-to-end.
    path("djust-admin-demo/", djust_admin_site.urls),
    # SSE fallback transport (server-sent events for corporate proxy environments)
    path("djust/", include(sse_urlpatterns)),
    # djust HTTP API — mounts ADR-008 dispatch + v0.7.0 @server_function
    # RPC at /djust/api/<slug>/<handler>/ and /djust/api/call/<slug>/<fn>/.
    path("djust/api/", include("djust.api.urls")),
    # PWA — service worker must be at root scope for full-site coverage
    path("sw.js", service_worker_view, name="service-worker"),
    path("manifest.json", manifest_view, name="pwa-manifest"),
]

# AI observability endpoints — DEBUG-gated, localhost-only (enforced by
# LocalhostOnlyObservabilityMiddleware). djust Python MCP calls these to
# introspect live state without sharing process memory with the dev server.
if settings.DEBUG:
    urlpatterns += [
        path("_djust/observability/", include("djust.observability.urls")),
    ]

urlpatterns += [
    # New organized apps
    path("", include("djust_homepage.urls")),  # Homepage and embedded demos
    path("demos/", include("djust_demos.urls")),  # Feature demonstrations
    path("forms/", include("djust_forms.urls")),  # Forms demonstrations
    path("tests/", include("djust_tests.urls")),  # Test views
    path("docs/", include("djust_docs.urls")),  # Documentation
    path("rentals/", include("djust_rentals.urls")),  # Rental property management
    # Old app (for backwards compatibility - will be phased out)
    # path('', include('demo_app.urls')),
]
