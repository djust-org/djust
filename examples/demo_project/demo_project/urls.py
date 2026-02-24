"""
URL configuration for demo_project.
"""

from django.contrib import admin
from django.urls import path, include

from djust.pwa import service_worker_view, manifest_view
from djust.sse import sse_urlpatterns

urlpatterns = [
    path('admin/', admin.site.urls),

    # SSE fallback transport (server-sent events for corporate proxy environments)
    path('djust/', include(sse_urlpatterns)),

    # PWA — service worker must be at root scope for full-site coverage
    path('sw.js', service_worker_view, name='service-worker'),
    path('manifest.json', manifest_view, name='pwa-manifest'),

    # New organized apps
    path('', include('djust_homepage.urls')),       # Homepage and embedded demos
    path('demos/', include('djust_demos.urls')),     # Feature demonstrations
    path('forms/', include('djust_forms.urls')),     # Forms demonstrations
    path('tests/', include('djust_tests.urls')),     # Test views
    path('docs/', include('djust_docs.urls')),       # Documentation
    path('rentals/', include('djust_rentals.urls')), # Rental property management

    # Old app (for backwards compatibility - will be phased out)
    # path('', include('demo_app.urls')),
]
